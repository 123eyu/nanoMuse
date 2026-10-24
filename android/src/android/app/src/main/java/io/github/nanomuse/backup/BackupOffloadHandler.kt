package io.github.nanomuse.backup

import android.content.Context
import com.openminis.app.backup.BackupHistory
import com.openminis.app.logging.AppLogger
import com.openminis.app.sandbox.NativeOffloadHandler
import com.openminis.app.sandbox.NativeOffloadRequest
import com.openminis.app.sandbox.NativeOffloadResult
import com.openminis.app.sandbox.offload.OffloadArgs
import kotlinx.coroutines.runBlocking
import org.json.JSONArray
import org.json.JSONObject

/**
 * `nanomuse-backup` in the sandbox (#560): `run` makes one backup with the choices and the
 * destinations saved on the Backup page and blocks until it is delivered, `status` prints the
 * latest run, `history` the recent ones, `choices` what a run would use. A scheduled task whose
 * prompt says to run it is how a phone gets backed up on a schedule; the page still shows every
 * run in its history.
 */
class BackupOffloadHandler(private val context: Context) : NativeOffloadHandler {

    override fun handle(request: NativeOffloadRequest): NativeOffloadResult {
        val args = OffloadArgs(request.argv.drop(1), booleanFlags = setOf("h", "help"))
        if (args.hasFlag("h", "help")) return NativeOffloadResult(0, HELP)
        return try {
            when (val sub = args.positional.firstOrNull() ?: "status") {
                "run" -> run()
                "status" -> status()
                "history" -> history(args.get("limit")?.toIntOrNull() ?: 10)
                "choices" -> choices()
                else -> NativeOffloadResult(2, "nanomuse-backup: unknown subcommand '$sub'\n$HELP")
            }
        } catch (e: BackupRun.Refused) {
            NativeOffloadResult(1, JSONObject().put("ran", false).put("refused", e.message).toString(2))
        } catch (e: Throwable) {
            AppLogger.warning(TAG, "handle failed: ${e.message}")
            NativeOffloadResult(1, "nanomuse-backup: ${e.message}")
        }
    }

    private fun run(): NativeOffloadResult {
        // Blocks until the package is built and delivered, as `minis-scheduled run` blocks for
        // its agent loop: the caller wants the outcome, not a ticket.
        val record = runBlocking { BackupRun.run(context) }
        val out = recordJson(record).put("ran", record.status != BackupHistory.Status.FAILED)
        return NativeOffloadResult(if (record.status == BackupHistory.Status.FAILED) 1 else 0, out.toString(2))
    }

    private fun status(): NativeOffloadResult {
        val latest = BackupHistory.get(context).records().firstOrNull()
            ?: return NativeOffloadResult(0, JSONObject().put("status", "none").toString(2))
        return NativeOffloadResult(0, recordJson(latest).toString(2))
    }

    private fun history(limit: Int): NativeOffloadResult {
        val arr = JSONArray()
        for (r in BackupHistory.get(context).records().take(limit.coerceIn(1, 100))) arr.put(recordJson(r))
        return NativeOffloadResult(0, JSONObject().put("runs", arr).put("count", arr.length()).toString(2))
    }

    private fun choices(): NativeOffloadResult {
        val c = BackupRun.choices(context)
        val out = JSONObject()
            .put("categories", JSONArray(c.categories.map { it.key }.sorted()))
            .put("chatFiles", c.chatFiles)
            .put("encrypt", c.encrypt)
            .put("maxFileBytes", c.maxFileBytes ?: JSONObject.NULL)
            .put("destinations", JSONArray(c.destinations.map { JSONObject().put("name", it.name).put("kind", it.backend).put("path", it.path) }))
            .put("canRun", !c.encrypt && c.destinations.isNotEmpty())
        return NativeOffloadResult(0, out.toString(2))
    }

    private fun recordJson(r: BackupHistory.Record): JSONObject = JSONObject()
        .put("id", r.id)
        .put("status", r.status.name.lowercase())
        .put("startedAt", r.startedAt)
        .put("finishedAt", r.finishedAt ?: JSONObject.NULL)
        .put("categories", JSONArray(r.categories))
        .put("totalBytes", r.totalBytes)
        .put("skippedFiles", r.skippedFiles)
        .put("package", r.packageName ?: JSONObject.NULL)
        .put("destinations", JSONArray(r.destinations.map {
            JSONObject().put("name", it.name).put("succeeded", it.succeeded)
                .put("detail", it.detail ?: JSONObject.NULL).put("kind", it.kind ?: JSONObject.NULL)
        }))
        .put("error", r.errorMessage ?: JSONObject.NULL)

    companion object {
        private const val TAG = "BackupOffload"
        private val HELP = """
            nanomuse-backup — back up this phone with the choices saved on Settings → Backup and restore.

            (no subcommand)  Same as `status`
            run              make one backup now and send it to every enabled destination; blocks until done
            status           the latest run
            history [--limit N]
            choices          what a run would include, and whether it can run from here

            A run uses the page's choices as they are: the categories, the per-file cap, whether chats
            carry their files. It refuses, without starting, when encryption is on (the passphrase is
            typed on the page and never reaches the shell) or when no destination is enabled.
            Schedule one with `minis-scheduled create --time 03:00 --repeat daily --prompt "Run nanomuse-backup run"`.
            Exit 0 when the run finished (see "status" for issues), 1 when it failed or was refused.
        """.trimIndent()
    }
}
