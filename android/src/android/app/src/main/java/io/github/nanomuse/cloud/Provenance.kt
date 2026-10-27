package io.github.nanomuse.cloud

import com.openminis.app.data.model.AgentContentPart
import com.openminis.app.data.model.LLMMessage
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import org.json.JSONObject
import kotlin.coroutines.AbstractCoroutineContextElement
import kotlin.coroutines.CoroutineContext
import kotlin.coroutines.EmptyCoroutineContext
import kotlin.coroutines.coroutineContext

/**
 * Where a model call sits, said to the relay (relay 0.31, docs/cloud.md "Provenance"): who
 * started the turn (`person`, `schedule`, `feed`, `goal`, `hub`), the turn's number in the
 * conversation, the call's number within the turn, and for a screen task the trajectory's id
 * and the step. A Cloud request carries them as headers next to `X-NanoMuse-Conversation`
 * ([CloudHeaders]); a turn run on a key of the person's own carries the same as fields of the
 * record it is sent up in ([OwnTurns]). Codes and numbers, never a word of the conversation.
 *
 * The provider that builds the request does not know which chat it works for, so the chat
 * says it through the coroutine it runs the turn in: `ChatViewModel` is given a scope that
 * carries a [Session] (its session id and a way to count its turns), and every call made under
 * that scope — the chat's own, a routine's, a hub task's — reads it from the coroutine context.
 * The screen operator runs on a thread of its own and wraps each model call in a [Hands]
 * element. Who started the turn is told by whoever starts it ([arm]) and lasts until the turn
 * ends ([turnEnded]); a turn nobody announced is the person's.
 *
 * The same rows remember the id the relay gave the last Cloud call (`x-nanomuse-request`) and
 * the id of the last own-key turn sent up, so an event about the turn ([ClientEvents]) can name
 * it. The desktop twin is `src/provenance.ts`; the iOS one `NanoMuseProvenance.swift`.
 */
object Provenance {
    const val PLATFORM = "android"

    const val H_RUN = "X-NanoMuse-Run"
    const val H_TURN = "X-NanoMuse-Turn"
    const val H_STEP = "X-NanoMuse-Step"
    const val H_TRAJECTORY = "X-NanoMuse-Trajectory"
    const val H_TRAJECTORY_STEP = "X-NanoMuse-Trajectory-Step"
    const val H_PLATFORM = "X-NanoMuse-Platform"

    /** The relay names the turn it kept in this response header; events about the turn carry the same id. */
    const val RESPONSE_HEADER = "x-nanomuse-request"

    const val RUN_PERSON = "person"
    const val RUN_SCHEDULE = "schedule"
    const val RUN_FEED = "feed"
    const val RUN_GOAL = "goal"
    const val RUN_HUB = "hub"

    /** Sessions remembered; past this the one longest untouched goes. */
    const val SESSIONS_KEPT = 64

    private val runRx = Regex("[a-z][a-z0-9_]{0,23}")
    private val idRx = Regex("[A-Za-z0-9_.:-]{1,80}")

    /**
     * The chat a coroutine works for. On the chat's view-model scope, so every model call made
     * under it carries the session; [turns] counts the chat's turns (its user rows) when a call
     * goes out — the chat installs it once it has its messages, and without it the turn is
     * counted from the request itself.
     */
    class Session(val sessionId: String) : AbstractCoroutineContextElement(Session) {
        companion object Key : CoroutineContext.Key<Session>

        @Volatile var turns: (() -> Int)? = null
    }

    /** A screen task under way on this thread: its id and the step the model is asked about. */
    class Hands(trajectory: String, @Volatile var step: Int = 0) : AbstractCoroutineContextElement(Hands) {
        companion object Key : CoroutineContext.Key<Hands>

        val trajectory: String = trajectory.filter { it.isLetterOrDigit() || it in "_.:-" }.take(80).ifEmpty { "run" }
    }

    /** What one call said, as the headers carry it and as an own-key turn's fields repeat it. */
    data class Fields(
        val run: String,
        val turn: Int,
        val step: Int,
        val trajectory: String? = null,
        val trajectoryStep: Int? = null,
    ) {
        fun headers(): Map<String, String> = buildMap {
            put(H_RUN, run)
            if (turn > 0) put(H_TURN, turn.toString())
            if (step > 0) put(H_STEP, step.toString())
            trajectory?.let { put(H_TRAJECTORY, it) }
            trajectoryStep?.takeIf { it > 0 }?.let { put(H_TRAJECTORY_STEP, it.toString()) }
            put(H_PLATFORM, PLATFORM)
        }

        /** The same on a turn sent up: `run`, `turn`, `step`, `trajectory`, `trajectory_step`, `platform`. */
        fun into(turnJson: JSONObject): JSONObject {
            turnJson.put("run", run)
            if (turn > 0) turnJson.put("turn", turn)
            if (step > 0) turnJson.put("step", step)
            trajectory?.let { turnJson.put("trajectory", it) }
            trajectoryStep?.takeIf { it > 0 }?.let { turnJson.put("trajectory_step", it) }
            turnJson.put("platform", PLATFORM)
            return turnJson
        }
    }

    /** The id the record knows a turn by: the relay's request id of a Cloud turn, or an own-key turn's own id. */
    data class TurnRef(val id: String, val own: Boolean)

    private class Row {
        var run: String = RUN_PERSON
        var pinned = false
        var turn = 0
        var step = 0
        var conversation: String? = null
        var ref: TurnRef? = null
        var touched = 0L
    }

    private val lock = Any()
    private val rows = LinkedHashMap<String, Row>()
    private var tick = 0L

    // ── the chat's side ────────────────────────────────────────────────────

    /**
     * The scope a chat runs its turns in: the view model's usual one (a supervisor on the main
     * thread's immediate dispatcher) with the [Session] in it.
     */
    fun scope(sessionId: String): CoroutineScope {
        val main: CoroutineContext = try {
            Dispatchers.Main.immediate
        } catch (_: NotImplementedError) {
            EmptyCoroutineContext // a unit test without Android's main looper
        } catch (_: IllegalStateException) {
            EmptyCoroutineContext
        }
        return CoroutineScope(SupervisorJob() + main + Session(sessionId))
    }

    /** The next turn in [sessionId] is [run]'s (a routine's, the feed's, a goal's, another device's), until it ends. */
    fun arm(sessionId: String, run: String) {
        val word = run.lowercase().takeIf { runRx.matches(it) } ?: return
        synchronized(lock) {
            val row = row(sessionId)
            row.run = word
            row.pinned = true
        }
    }

    /** The announced run did not start after all. */
    fun disarm(sessionId: String) = turnEnded(sessionId)

    /** Who the turn under way in [sessionId] belongs to. */
    fun runOf(sessionId: String): String = synchronized(lock) { rows[sessionId]?.run ?: RUN_PERSON }

    /** The turn in [sessionId] is over: the next one is the person's unless someone says otherwise, and its calls count from one. */
    fun turnEnded(sessionId: String) {
        synchronized(lock) {
            val row = rows[sessionId] ?: return
            row.run = RUN_PERSON
            row.pinned = false
            row.step = 0
        }
    }

    /** The number of the turn under way (or last run) in [sessionId], 0 when none was counted yet. */
    fun turnNo(sessionId: String): Int = synchronized(lock) { rows[sessionId]?.turn ?: 0 }

    /** The conversation id the session's calls carried, once one went out. */
    fun conversationOf(sessionId: String): String? = synchronized(lock) { rows[sessionId]?.conversation }

    /** The record's id for the session's latest turn, once the relay or the uploader named it. */
    fun turnRef(sessionId: String): TurnRef? = synchronized(lock) { rows[sessionId]?.ref }

    /** The session a conversation id belongs to, when a call of it went out from here. */
    fun sessionOf(conversation: String): String? = synchronized(lock) {
        rows.entries.lastOrNull { it.value.conversation == conversation }?.key
    }

    // ── the call's side ────────────────────────────────────────────────────

    /** The headers for a Cloud request built in the current coroutine; see [fields]. */
    suspend fun headers(conversation: String?, messages: List<LLMMessage>): Map<String, String> =
        fields(coroutineContext, conversation, messages).headers()

    /**
     * What the call about to go out says of itself, read from the coroutine [context] it goes
     * out in: the session (its run and turn count) and the screen task, if any. Counts the
     * call as one more step of the session's turn, and remembers [conversation] as the
     * session's. Without a session in the context (a model call from somewhere else), the turn
     * and step are counted from the request alone and the run is the person's.
     */
    fun fields(context: CoroutineContext, conversation: String?, messages: List<LLMMessage>): Fields {
        val session = context[Session]
        val hands = context[Hands]
        val trajectory = hands?.trajectory
        val trajectoryStep = hands?.step?.takeIf { it > 0 }
        if (session == null) {
            return Fields(RUN_PERSON, turnOf(messages), stepOf(messages), trajectory, trajectoryStep)
        }
        val counted = session.turns?.let { count -> runCatching { count() }.getOrDefault(0) } ?: 0
        synchronized(lock) {
            val row = row(session.sessionId)
            val turn = when {
                counted > 0 -> counted
                row.turn > 0 -> row.turn // a screen task's call: the chat's turn, as the chat last said it
                else -> turnOf(messages)
            }
            if (turn != row.turn) {
                row.turn = turn
                row.step = 0
            }
            row.step++
            if (!conversation.isNullOrEmpty()) row.conversation = conversation
            return Fields(row.run, turn, row.step, trajectory, trajectoryStep)
        }
    }

    /** The relay answered a Cloud call of [conversation] and named the turn [requestId]. */
    fun noteResponse(conversation: String?, requestId: String?) {
        val id = requestId?.trim()?.takeIf { idRx.matches(it) } ?: return
        val key = conversation?.takeIf { it.isNotEmpty() } ?: return
        synchronized(lock) {
            val row = rows.entries.lastOrNull { it.value.conversation == key }?.value ?: return
            row.ref = TurnRef(id, own = false)
        }
    }

    /** An own-key turn of [sessionId] went up under [id]. */
    fun noteOwnTurn(sessionId: String?, id: String) {
        val sid = sessionId?.takeIf { it.isNotEmpty() } ?: return
        synchronized(lock) { row(sid).ref = TurnRef(id, own = true) }
    }

    /** The session the coroutine [context] works for, if a chat said so. */
    fun sessionIn(context: CoroutineContext): String? = context[Session]?.sessionId

    // ── counting from the request alone ─────────────────────────────────────

    /** A message the person (or a routine in their place) sent: a user row that is not a tool's result. */
    fun isPersonMessage(m: LLMMessage): Boolean =
        m.role == LLMMessage.Role.USER && m.contentParts.none { it is AgentContentPart.ToolResult }

    /** The turn's number as the request shows it: its user messages, tool results aside. */
    fun turnOf(messages: List<LLMMessage>): Int = messages.count(::isPersonMessage)

    /** The call's number within the turn as the request shows it: the model's answers since the last user message, plus one. */
    fun stepOf(messages: List<LLMMessage>): Int {
        var n = 0
        for (m in messages.asReversed()) {
            if (isPersonMessage(m)) break
            if (m.role == LLMMessage.Role.ASSISTANT) n++
        }
        return n + 1
    }

    /** For tests: nothing remembered. */
    internal fun reset() = synchronized(lock) { rows.clear() }

    private fun row(sessionId: String): Row {
        val row = rows.getOrPut(sessionId) { Row() }
        row.touched = ++tick
        if (rows.size > SESSIONS_KEPT) {
            rows.entries.minByOrNull { it.value.touched }?.let { rows.remove(it.key) }
        }
        return row
    }
}
