package io.github.nanomuse.cloud

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject

/**
 * Where a turn stands before the model has said anything, as nanoMuse Cloud tells it (relay
 * 0.31, docs/cloud.md). While the request waits for one of the relay's provider slots, the
 * stream carries a chunk with no `choices` and a `nanomuse` field every five seconds:
 * `phase: "queued"` and `ahead`, how many requests are in front of it; then one with
 * `phase: "working"` once the slot is its own. The model client hands every such field here
 * and the typing indicator reads the latest to say "3 ahead of you". A status is tied to the
 * moment it arrived, so the indicator of a later step ignores what an earlier one heard, and
 * a `queued` older than [STALE_MS] (the relay repeats it every five seconds) is not shown.
 */
object NmStreamStatus {
    /** One `nanomuse` field: the phase, how many are ahead (-1 when the relay did not say), when it came. */
    data class Status(
        val phase: String,
        val ahead: Int,
        val waitedMs: Long,
        val at: Long = System.currentTimeMillis(),
    ) {
        /** True for a "queued" that came after [since] and is not yet stale at [now]. */
        fun queuedFor(now: Long, since: Long): Boolean =
            phase == "queued" && at >= since && now - at <= STALE_MS
    }

    /** A queued status the relay has not refreshed for this long says nothing any more. */
    const val STALE_MS = 15_000L

    private val _latest = MutableStateFlow<Status?>(null)

    /** The last status any stream received, for the indicator. */
    val latest: StateFlow<Status?> = _latest.asStateFlow()

    /** The `nanomuse` object of a chat chunk; anything without a phase is ignored. */
    fun note(status: JSONObject) {
        val phase = status.optString("phase", "")
        if (phase.isEmpty()) return
        _latest.value = Status(phase, status.optInt("ahead", -1), status.optLong("waited_ms", 0L))
    }

    /** For tests. */
    internal fun reset() {
        _latest.value = null
    }
}
