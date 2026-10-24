package io.github.nanomuse.backup

import android.content.Context
import com.openminis.app.R

/**
 * Turns a server's refusal into a sentence a person can act on (#561). A WebDAV destination
 * behind nginx answers `413 Request Entity Too Large` when the package is bigger than its
 * `client_max_body_size`, and the raw text explained neither the cause nor the way around it.
 */
object BackupHints {
    private val TOO_LARGE = Regex("\\b413\\b|entity too large|request too large|too large", RegexOption.IGNORE_CASE)

    /** The delivery error as shown and recorded: the server's words, then the hint when one applies. */
    fun delivery(context: Context, message: String?): String {
        val text = message?.takeIf { it.isNotBlank() } ?: return context.getString(R.string.nm_backup_delivery_failed)
        if (!TOO_LARGE.containsMatchIn(text)) return text
        return text.trimEnd('.', ' ') + ". " + context.getString(R.string.nm_backup_hint_too_large)
    }
}
