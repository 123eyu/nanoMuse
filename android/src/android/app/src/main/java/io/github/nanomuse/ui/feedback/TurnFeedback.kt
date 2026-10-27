package io.github.nanomuse.ui.feedback

import io.github.nanomuse.cloud.ClientEvents
import io.github.nanomuse.cloud.Provenance
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * The lightest feedback a chat can take, for the record (relay 0.31, docs/cloud.md "Client
 * events"): a thumb up or down beside a reply, and one question after a Stop — why — that the
 * person may skip. Codes only travel: `turn.feedback {vote, reason?}`, `turn.stopped {reason}`,
 * `turn.ended {ended}`; never the reply or a word typed.
 *
 * `ChatViewModel.cancelStream` calls [stopped] when the person presses Stop (the event goes at
 * once; the card to ask why is offered for a while, in the chat the stop happened in, and only
 * for a turn the person started — a routine's or another device's turn gets no card). The
 * chat's streaming flag calls [turnEnded] at every end, which reports a turn that ended in an
 * error. The thumbs keep what was voted per reply for the screen ([votes]). The desktop twin is
 * `src/client/Feedback.tsx`; the iOS one `NanoMuseFeedback.swift`.
 */
object TurnFeedback {
    /** The chat whose Stop is being asked about, and when it was pressed. */
    data class StopAsk(val sessionId: String, val turnNo: Int, val at: Long)

    /** How long after a Stop the question is still worth asking. */
    const val STOP_ASK_MS = 90_000L

    val STOP_REASONS = listOf("wrong", "slow", "changed_mind", "done", "other")
    val DOWN_REASONS = listOf("wrong", "unhelpful", "unsafe", "slow", "other")

    private val _stopAsk = MutableStateFlow<StopAsk?>(null)
    val stopAsk: StateFlow<StopAsk?> = _stopAsk.asStateFlow()

    /** Reply id → `up` or `down`, as voted on this screen since the app started. */
    private val _votes = MutableStateFlow<Map<String, String>>(emptyMap())
    val votes: StateFlow<Map<String, String>> = _votes.asStateFlow()

    private val stoppedSessions = HashSet<String>()
    private val lock = Any()

    /** The person pressed Stop in [sessionId] while a turn ran. */
    fun stopped(sessionId: String) {
        if (sessionId.isEmpty()) return
        synchronized(lock) { stoppedSessions += sessionId }
        ClientEvents.turnEnded(sessionId, "aborted")
        if (Provenance.runOf(sessionId) == Provenance.RUN_PERSON) {
            _stopAsk.value = StopAsk(sessionId, Provenance.turnNo(sessionId), System.currentTimeMillis())
        }
    }

    /**
     * The turn in [sessionId] is over. A turn that was stopped was reported at the Stop; one
     * that ended in an error is reported here; a turn that finished says nothing. Either way
     * the next turn is the person's again ([Provenance.turnEnded]).
     */
    fun turnEnded(sessionId: String, errored: Boolean) {
        val wasStopped = synchronized(lock) { stoppedSessions.remove(sessionId) }
        if (!wasStopped && errored) ClientEvents.turnEnded(sessionId, "error")
        Provenance.turnEnded(sessionId)
    }

    /** Whether the question after a Stop is still worth showing in [sessionId]. */
    fun asking(sessionId: String, now: Long = System.currentTimeMillis()): StopAsk? =
        _stopAsk.value?.takeIf { it.sessionId == sessionId && now - it.at <= STOP_ASK_MS }

    /** The person answered why they stopped (one of [STOP_REASONS]), or skipped the question. */
    fun answerStop(ask: StopAsk, reason: String?) {
        if (_stopAsk.value == ask) _stopAsk.value = null
        ClientEvents.turnStopped(ask.sessionId, reason?.takeIf { it in STOP_REASONS } ?: "skipped")
    }

    /** Drops the question without an answer (the chat was left; nothing is reported). */
    fun dropStopAsk(ask: StopAsk) {
        if (_stopAsk.value == ask) _stopAsk.value = null
    }

    /**
     * A thumb on reply [messageId] of [sessionId]: `up` or `down`, with a reason for a thumb
     * down when one was picked (one of [DOWN_REASONS]); the same thumb again takes it back
     * (`none`), unless [keep] says the vote stands (the reason picked after the thumb).
     */
    fun vote(sessionId: String, messageId: String, vote: String, reason: String? = null, turnNo: Int? = null, keep: Boolean = false) {
        val current = _votes.value[messageId]
        val next = if (current == vote && !keep) "none" else vote
        _votes.value = if (next == "none") _votes.value - messageId else _votes.value + (messageId to next)
        ClientEvents.turnFeedback(sessionId, next, reason?.takeIf { next == "down" && it in DOWN_REASONS }, turnNo)
    }

    /** For tests. */
    internal fun reset() {
        _stopAsk.value = null
        _votes.value = emptyMap()
        synchronized(lock) { stoppedSessions.clear() }
    }
}
