package io.github.nanomuse.avatar

import android.content.ContentValues
import android.content.Context
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.provider.MediaStore
import com.openminis.app.logging.AppLogger
import io.github.nanomuse.ui.avatar.AgentMood
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * *Save to gallery* in the avatar workbench (#664). The face lives in the app's own storage
 * (`filesDir/minis-global/nanomuse/avatar`), which no file manager can open; this copies
 * what is there to the phone's gallery through MediaStore, where the person can keep it,
 * reuse it or carry it to another phone: the base picture, the mood stills, the candidates
 * of the last generation, the animated moods and — most of all — a clip of their own, which
 * never leaves the phone otherwise. Pictures land in `Pictures/nanoMuse/avatar`, clips in
 * `Movies/nanoMuse/avatar`, each batch under one time stamp so nothing is overwritten.
 * Nothing is uploaded anywhere: it is a copy from a private folder to a visible one.
 */
object AvatarExport {
    private const val TAG = "AvatarExport"
    const val ALBUM = "nanoMuse/avatar"

    /** One file to copy: where it is, what to call it in the gallery, and whether it is a clip. */
    data class Item(val file: File, val name: String, val video: Boolean)

    /** What was copied. [pictures] and [clips] count the files that reached the gallery. */
    data class Result(val pictures: Int, val clips: Int) {
        val total: Int get() = pictures + clips
    }

    /**
     * The files worth saving under [root] (the avatar folder): the base picture and the mood
     * stills, the candidates of the last generation, the generated clips under `motion/` and
     * the person's own clips. Posters of the own clips (first frames the app keeps for
     * itself), the pending copy of a clip being chosen, and `avatar.json` are left out.
     * Empty files are skipped. [stamp] prefixes every name so two exports do not collide.
     */
    fun collect(root: File, stamp: String): List<Item> {
        val out = mutableListOf<Item>()
        fun add(file: File, name: String, video: Boolean) {
            if (file.isFile && file.length() > 0) out += Item(file, "$stamp-$name", video)
        }
        add(File(root, "base.png"), "base.png", video = false)
        for (mood in AgentMood.entries) {
            val m = mood.name.lowercase()
            add(File(root, "$m.png"), "$m.png", video = false)
        }
        File(root, "candidates").listFiles { f -> f.extension.equals("png", ignoreCase = true) }
            ?.sortedBy { it.name }
            ?.forEach { add(it, "candidate-${it.nameWithoutExtension}.png", video = false) }
        for (mood in AgentMood.entries) {
            val m = mood.name.lowercase()
            add(File(root, "motion/$m.mp4"), "$m.mp4", video = true)
        }
        add(File(root, "own-clip.mp4"), "own-clip.mp4", video = true)
        for (mood in AgentMood.entries) {
            val m = mood.name.lowercase()
            add(File(root, "own-clip-$m.mp4"), "own-clip-$m.mp4", video = true)
        }
        return out
    }

    /** The time stamp a batch is named by: `20261027-1945`, in local time. */
    fun stamp(now: Long = System.currentTimeMillis()): String =
        SimpleDateFormat("yyyyMMdd-HHmm", Locale.US).format(Date(now))

    /** Copies everything [collect] finds to the gallery; null when there was nothing to copy. */
    suspend fun export(context: Context): Result? = withContext(Dispatchers.IO) {
        val items = collect(AvatarStore.root(), stamp())
        if (items.isEmpty()) return@withContext null
        var pictures = 0
        var clips = 0
        for (item in items) {
            val ok = runCatching { save(context, item) }
                .onFailure { AppLogger.warning(TAG, "${item.name}: ${it.message}") }
                .getOrDefault(false)
            if (ok) { if (item.video) clips++ else pictures++ }
        }
        Result(pictures, clips)
    }

    private fun save(context: Context, item: Item): Boolean {
        val mime = if (item.video) "video/mp4" else "image/png"
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val collection: Uri = if (item.video) MediaStore.Video.Media.EXTERNAL_CONTENT_URI else MediaStore.Images.Media.EXTERNAL_CONTENT_URI
            val folder = (if (item.video) Environment.DIRECTORY_MOVIES else Environment.DIRECTORY_PICTURES) + "/" + ALBUM
            val values = ContentValues().apply {
                put(MediaStore.MediaColumns.DISPLAY_NAME, item.name)
                put(MediaStore.MediaColumns.MIME_TYPE, mime)
                put(MediaStore.MediaColumns.RELATIVE_PATH, folder)
                put(MediaStore.MediaColumns.IS_PENDING, 1)
            }
            val resolver = context.contentResolver
            val uri = resolver.insert(collection, values) ?: return false
            try {
                resolver.openOutputStream(uri)?.use { out -> item.file.inputStream().use { it.copyTo(out) } } ?: return false
            } catch (e: Exception) {
                resolver.delete(uri, null, null)
                throw e
            }
            values.clear()
            values.put(MediaStore.MediaColumns.IS_PENDING, 0)
            resolver.update(uri, values, null, null)
            return true
        }
        // Android 8 and 9: the public folder straight away, as the chat's "save picture" does.
        @Suppress("DEPRECATION")
        val base = Environment.getExternalStoragePublicDirectory(if (item.video) Environment.DIRECTORY_MOVIES else Environment.DIRECTORY_PICTURES)
        val dir = File(base, ALBUM).apply { mkdirs() }
        item.file.copyTo(File(dir, item.name), overwrite = true)
        return true
    }
}
