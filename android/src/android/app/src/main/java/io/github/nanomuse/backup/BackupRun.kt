package io.github.nanomuse.backup

import android.content.Context
import com.openminis.app.R
import com.openminis.app.backup.BackupCategory
import com.openminis.app.backup.BackupExporter
import com.openminis.app.backup.BackupHistory
import com.openminis.app.backup.remote.RcloneChunkedUpload
import com.openminis.app.backup.remote.RcloneRemoteStore
import com.openminis.app.data.db.AppDatabase
import com.openminis.app.logging.AppLogger
import java.util.Locale

/**
 * A backup run without the page (#560): the choices saved on *Settings → Backup and restore*
 * (what to include, the per-file cap, whether chats carry their files) and every enabled
 * destination, exactly as the *Start backup* button would use them. `nanomuse-backup run` in the
 * sandbox calls this, so a scheduled task can keep a phone backed up.
 *
 * The run is recorded in [BackupHistory] like one started from the page, so the history list
 * and its detail screen show it. Two things only the page can give are refused up front rather
 * than failed half-way: a passphrase (an encrypted backup is typed for, every time, and a
 * shell must never see it) and a destination (a package that can only land in the app's own
 * sandbox dies with the app it exists to protect).
 */
object BackupRun {
    private const val TAG = "BackupRun"

    /** The run cannot start; [message] says what the person has to do on the page. */
    class Refused(message: String) : Exception(message)

    /** The saved export choices, read the way the page reads them. */
    data class Choices(
        val categories: Set<BackupCategory>,
        val encrypt: Boolean,
        val maxFileBytes: Long?,
        val chatFiles: Boolean,
        val destinations: List<RcloneRemoteStore.Remote>,
    )

    fun choices(context: Context): Choices {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val raw = prefs.getString(KEY_CATEGORIES, null)
        val categories = raw?.split(",")?.mapNotNull(BackupCategory::fromKey)
            ?.filter { it in BackupCategory.backupable }?.toSet()
            ?.ifEmpty { null } ?: BackupCategory.backupable.toSet()
        val maxFileBytes = when (val mb = prefs.getInt(KEY_MAX_FILE_MB, MAX_FILE_UNLIMITED)) {
            MAX_FILE_UNLIMITED -> null
            MAX_FILE_NO_FILES -> 0L
            else -> mb.toLong() * 1024L * 1024L
        }
        return Choices(
            categories = categories,
            encrypt = prefs.getBoolean(KEY_ENCRYPT, false),
            maxFileBytes = maxFileBytes,
            chatFiles = prefs.getBoolean(KEY_CHAT_FILES, true),
            destinations = RcloneRemoteStore(context).enabledRemotes,
        )
    }

    /**
     * Runs one backup to completion and returns its record. Throws [Refused] before any work
     * when the saved choices need the page; other failures end in a FAILED record, returned.
     */
    suspend fun run(context: Context, onProgress: ((String) -> Unit)? = null): BackupHistory.Record {
        val app = context.applicationContext
        val choices = choices(app)
        if (choices.encrypt) throw Refused(app.getString(R.string.nm_backup_run_needs_passphrase))
        if (choices.destinations.isEmpty()) throw Refused(app.getString(R.string.backup_needs_destination))

        val history = BackupHistory.get(app)
        val log = mutableListOf<BackupHistory.LogEntry>()
        var record = BackupHistory.Record(
            backupId = "",
            startedAt = System.currentTimeMillis(),
            status = BackupHistory.Status.RUNNING,
            categories = choices.categories.map { it.key }.sorted(),
            encrypted = false,
        )
        history.upsert(record)

        fun note(line: String, problem: Boolean = false) {
            log.add(BackupHistory.LogEntry(System.currentTimeMillis(), line, problem))
            history.upsert(record.copy(log = log.toList()))
            onProgress?.invoke(line)
        }

        try {
            note(app.getString(R.string.nm_backup_run_started))
            val summary = BackupExporter(app, AppDatabase.getInstance(app)).export(
                BackupExporter.Options(
                    categories = choices.categories,
                    maxFileBytes = choices.maxFileBytes,
                    chatFiles = choices.chatFiles,
                    includeCredentials = true,
                    passphrase = null,
                ),
            ) { line -> note(line) }

            val outcomes = deliver(app, summary.packageFile, summary.backupId, choices.destinations, ::note)
            outcomes.forEach {
                note(
                    if (it.succeeded) "Delivered to ${it.name}" else "Delivery to ${it.name} failed: ${it.detail}",
                    problem = !it.succeeded,
                )
            }
            // The local package is a fallback, not an archive: once every destination holds a
            // verified copy it only spends the phone's storage. Any failure keeps it.
            if (outcomes.all { it.succeeded }) {
                val freed = summary.packageFile.length()
                if (summary.packageFile.delete()) {
                    note(app.getString(R.string.backup_local_removed, humanBytes(freed)))
                }
            } else {
                note(app.getString(R.string.backup_local_kept))
            }

            val failed = outcomes.filterNot { it.succeeded }
            record = record.copy(
                backupId = summary.backupId,
                finishedAt = System.currentTimeMillis(),
                status = if (failed.isEmpty() && summary.skippedFiles == 0) {
                    BackupHistory.Status.SUCCEEDED
                } else {
                    BackupHistory.Status.COMPLETED_WITH_ISSUES
                },
                totalBytes = summary.totalBytes,
                skippedFiles = summary.skippedFiles,
                skippedEntries = summary.skippedPaths.map { BackupHistory.SkippedEntry(it.path, it.size) },
                packageName = summary.packageFile.name,
                destinations = outcomes,
                log = log.toList(),
            )
        } catch (e: Exception) {
            AppLogger.error(TAG, "[Backup] run failed: ${e.message}")
            log.add(BackupHistory.LogEntry(System.currentTimeMillis(), e.message ?: "Backup failed.", true))
            record = record.copy(
                finishedAt = System.currentTimeMillis(),
                status = BackupHistory.Status.FAILED,
                errorMessage = e.message,
                log = log.toList(),
            )
        } finally {
            history.upsert(record)
        }
        return record
    }

    private fun deliver(
        context: Context,
        packageFile: java.io.File,
        backupId: String,
        remotes: List<RcloneRemoteStore.Remote>,
        note: (String) -> Unit,
    ): List<BackupHistory.DestinationOutcome> {
        val store = RcloneRemoteStore(context)
        store.syncToRclone()
        val uploader = RcloneChunkedUpload(context)
        return remotes.map { remote ->
            try {
                note("Sending to ${remote.name}…")
                uploader.upload(packageFile, remote, backupId)
                BackupHistory.DestinationOutcome(remote.name, succeeded = true, kind = remote.backend, path = remote.path)
            } catch (e: Exception) {
                AppLogger.error(TAG, "[Backup] delivery to ${remote.name} failed: ${e.message}")
                BackupHistory.DestinationOutcome(
                    remote.name, succeeded = false,
                    detail = BackupHints.delivery(context, e.message),
                    kind = remote.backend, path = remote.path,
                )
            }
        }
    }

    private fun humanBytes(bytes: Long): String = when {
        bytes >= 1_000_000_000 -> String.format(Locale.US, "%.1f GB", bytes / 1e9)
        bytes >= 1_000_000 -> String.format(Locale.US, "%.1f MB", bytes / 1e6)
        bytes >= 1_000 -> String.format(Locale.US, "%.0f kB", bytes / 1e3)
        else -> "$bytes B"
    }

    // The page's preferences file and keys (BackupViewModel); a run reads them, never writes.
    private const val PREFS = "backup_ui"
    private const val KEY_CATEGORIES = "selectedCategories"
    private const val KEY_ENCRYPT = "encrypt"
    private const val KEY_MAX_FILE_MB = "maxFileSizeMB"
    private const val KEY_CHAT_FILES = "chatFiles"
    private const val MAX_FILE_NO_FILES = -1
    private const val MAX_FILE_UNLIMITED = 0
}
