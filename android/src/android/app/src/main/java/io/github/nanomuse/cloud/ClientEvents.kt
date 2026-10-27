package io.github.nanomuse.cloud

import android.content.Context
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ProcessLifecycleOwner
import com.openminis.app.logging.AppLogger
import org.json.JSONArray
import org.json.JSONObject
import java.util.ArrayDeque
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * What happened around a turn that the request itself does not show, told to the account's
 * record as codes and numbers (relay 0.31, docs/cloud.md "Client events"): what the risk gate
 * decided on a tool call and how the person answered its card, that a reply was stopped and
 * why, a thumb up or down, each step of a screen task. `POST /v1/events`, a hundred at most
 * per request, gathered for a few seconds first; nothing goes out without an account, and a
 * relay out of reach loses the batch rather than hoarding it.
 *
 * The shape is kept here before anything travels: `data` is a flat object of short codes
 * (`[A-Za-z0-9_.:/ -]`, 120 characters at most), numbers and booleans; a string that looks like
 * prose is left out, never cut — this is how the promise that no message travels here is kept
 * on the phone, not only at the relay. Each event may name the turn it is about by the id the
 * record knows it by ([Provenance.turnRef]) and the conversation. The desktop twin is
 * `src/events.ts`; the iOS one `NanoMuseClientEvents.swift`.
 */
object ClientEvents {
    private const val TAG = "ClientEvents"

    /** Events per request, the relay's ceiling. */
    const val BATCH = 100
    /** How long events gather before a batch goes. */
    const val FLUSH_MS = 4_000L
    /** Events waiting; past this the oldest is dropped. */
    const val QUEUE_MAX = 1_000
    const val DATA_MAX_KEYS = 16
    const val CODE_MAX = 120

    private val codeRx = Regex("[A-Za-z0-9_.:/ -]{0,120}")
    private val keyRx = Regex("[a-z][a-z0-9_]{0,31}")
    private val kindRx = Regex("[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*){0,3}")

    private val lock = Any()
    private val queue = ArrayDeque<JSONObject>()
    private val worker = Executors.newSingleThreadScheduledExecutor { r -> Thread(r, "nanomuse-events").apply { isDaemon = true } }
    private var timer: ScheduledFuture<*>? = null
    @Volatile private var app: Context? = null

    /** Where a batch goes; the app's is the relay, a test's is a list. */
    @Volatile var sender: ((JSONArray) -> Unit)? = null
    /** Whether anything goes at all: signed in, on the app. */
    @Volatile var enabled: () -> Boolean = { app?.let { NanoMuseCloud.apiKey(it) != null } == true }

    /** Batches sent and events dropped since the process started; the log reads them. */
    val sent = AtomicInteger()
    val dropped = AtomicInteger()

    /** App start: the relay as the sender, and what is waiting goes when the app leaves the screen. */
    fun init(context: Context) {
        val ctx = context.applicationContext
        app = ctx
        sender = { events -> NanoMuseCloud.postEvents(ctx, events) }
        ProcessLifecycleOwner.get().lifecycle.addObserver(
            LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) flush() },
        )
    }

    /** How many events are waiting. */
    val pending: Int get() = synchronized(lock) { queue.size }

    /**
     * One event: [kind] in dotted lower case, [data] cleaned to the shape above, the turn it is
     * about ([turn], when the record has named it) and its [conversation]. Nothing is queued
     * when nothing would be sent.
     */
    fun add(kind: String, data: Map<String, Any?>, turn: Provenance.TurnRef? = null, conversation: String? = null) {
        if (!enabled()) return
        val event = event(kind, data, turn, conversation) ?: return
        synchronized(lock) {
            if (queue.size >= QUEUE_MAX) {
                queue.pollFirst()
                dropped.incrementAndGet()
            }
            queue.addLast(event)
            if (timer == null) timer = worker.schedule({ flush() }, FLUSH_MS, TimeUnit.MILLISECONDS)
        }
    }

    /** An event about the session's latest turn: the record's id and the conversation looked up for it. */
    fun addFor(sessionId: String?, kind: String, data: Map<String, Any?>) {
        val sid = sessionId?.takeIf { it.isNotEmpty() }
        add(kind, data, turn = sid?.let { Provenance.turnRef(it) }, conversation = sid?.let { Provenance.conversationOf(it) })
    }

    /** The event as it travels, or null for one the relay would drop (so it is not sent at all). */
    fun event(kind: String, data: Map<String, Any?>, turn: Provenance.TurnRef? = null, conversation: String? = null): JSONObject? {
        val k = kind.trim()
        if (!kindRx.matches(k) || k.length > 48) return null
        val clean = JSONObject()
        var keys = 0
        for ((key, value) in data) {
            if (keys >= DATA_MAX_KEYS) break
            if (!keyRx.matches(key)) continue
            val v: Any = when (value) {
                null -> continue
                is Boolean -> value
                is Int, is Long, is Short, is Byte -> (value as Number).toLong().takeIf { kotlin.math.abs(it) < 1_000_000_000_000_000L } ?: continue
                is Float, is Double -> (value as Number).toDouble().takeIf { it.isFinite() && kotlin.math.abs(it) < 1e15 } ?: continue
                is Enum<*> -> value.name.lowercase()
                is String -> value.trim().takeIf { codeRx.matches(it) } ?: continue // prose never travels: left out, not cut
                else -> continue
            }
            clean.put(key, v)
            keys++
        }
        val e = JSONObject().put("kind", k).put("ts", System.currentTimeMillis() / 1000).put("data", clean)
        turn?.let {
            e.put("turn", it.id)
            if (it.own) e.put("source", "own")
        }
        conversation?.takeIf { it.isNotEmpty() }?.let { e.put("conversation", it.take(96)) }
        return e
    }

    /** Sends what is waiting, a batch at a time, on the worker. */
    fun flush() {
        synchronized(lock) { timer = null }
        worker.execute { drain() }
    }

    private fun drain() {
        while (true) {
            val batch = JSONArray()
            synchronized(lock) {
                while (batch.length() < BATCH) batch.put(queue.pollFirst() ?: break)
            }
            if (batch.length() == 0) return
            val send = sender ?: return
            if (!enabled()) { dropped.addAndGet(batch.length()); return }
            try {
                send(batch)
                sent.incrementAndGet()
            } catch (t: Throwable) {
                dropped.addAndGet(batch.length())
                AppLogger.info(TAG, "a batch of ${batch.length()} events was not sent: ${t.message}")
                return
            }
        }
    }

    /** For tests: what is waiting goes now, on this thread. */
    internal fun drainNow() = drain()

    /** For tests: nothing waiting, nothing scheduled. */
    internal fun reset() {
        synchronized(lock) {
            queue.clear()
            timer?.cancel(false)
            timer = null
        }
    }

    // ── the kinds, as docs/cloud.md lists them ───────────────────────────────

    /** The risk gate's word on a tool call: what the policy said, what covered it, what was decided. */
    fun sentinelDecision(sessionId: String?, data: Map<String, Any?>) = addFor(sessionId, "sentinel.decision", data)

    /** How the person answered a risk card, or that nobody did. */
    fun approvalResult(sessionId: String?, data: Map<String, Any?>) = addFor(sessionId, "approval.result", data)

    /** A turn that ended short: `aborted` (Stop), `error`, `abandoned`. */
    fun turnEnded(sessionId: String?, ended: String) =
        addFor(sessionId, "turn.ended", mapOf("ended" to ended, "turn_no" to turnNoOf(sessionId)))

    /** Why a reply was stopped, in the person's one word, or `skipped`. */
    fun turnStopped(sessionId: String?, reason: String) =
        addFor(sessionId, "turn.stopped", mapOf("reason" to reason, "turn_no" to turnNoOf(sessionId)))

    /** A thumb up or down on a reply (`none` takes it back), with a reason when one was given. */
    fun turnFeedback(sessionId: String?, vote: String, reason: String?, turnNo: Int? = null) =
        addFor(sessionId, "turn.feedback", mapOf("vote" to vote, "reason" to reason, "turn_no" to (turnNo ?: turnNoOf(sessionId))))

    /** One step of a screen task, or its end. */
    fun handsStep(sessionId: String?, trajectory: String, step: Int, action: String, outcome: String?, latencyMs: Long? = null) =
        addFor(sessionId, "hands.step", mapOf("trajectory" to trajectory, "step" to step, "action" to action, "outcome" to outcome, "latency_ms" to latencyMs))

    private fun turnNoOf(sessionId: String?): Int? = sessionId?.let { Provenance.turnNo(it) }?.takeIf { it > 0 }
}
