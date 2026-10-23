package io.github.nanomuse.reach

import org.json.JSONObject

/**
 * What a remembered answer to another device's `approval` event covers (the `target` of the
 * phone's RiskGate, under its `pc:` scope). A sender since 1.0.6 names it: `grant_key`, the
 * same key its own grants use (the runtime's tool and target, `shell:git`; the desktop's tool
 * and whole main argument). An older sender gives a preview only, and the first 60 characters
 * of it are all there is to remember by, as before.
 */
object RemoteApprovalKey {
    fun target(event: JSONObject): String {
        val key = event.optString("grant_key").trim()
        if (key.isNotEmpty()) return "task:$key"
        val preview = event.optString("preview").ifBlank { event.optString("summary") }
        return "task:" + preview.take(60)
    }
}
