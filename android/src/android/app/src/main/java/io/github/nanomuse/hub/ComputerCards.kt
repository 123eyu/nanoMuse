package io.github.nanomuse.hub

import android.content.Context
import com.openminis.app.logging.AppLogger
import io.github.nanomuse.guard.GuardKind
import io.github.nanomuse.guard.RiskAssessment
import io.github.nanomuse.guard.RiskClass
import io.github.nanomuse.guard.RiskDecision
import io.github.nanomuse.guard.RiskGate
import io.github.nanomuse.guard.RiskRequest
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

/**
 * A computer's own approval cards on this phone (docs/hub.md, "A computer's own cards on the
 * phone"): a step of a scheduled task there, pushed as `event {stage:"approval", any_device}`
 * because nobody may be at the computer. The usual card shows (a notification in the
 * background); the answer goes back as `approve {approval_id, allow, scope}`. A card nobody
 * answers in time only leaves this phone: the one on the computer keeps waiting. The
 * computer's `approval_result` (answered there or on another phone) takes the card away.
 *
 * Nothing runs on this phone, so the remote-control switch does not apply. The grant, if any,
 * is kept on the computer.
 */
object ComputerCards {
    private const val TAG = "ComputerCards"

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    /** Cards shown here: computer id and approval id → the card's id. */
    private val shown = ConcurrentHashMap<String, String>()
    /** Cards the computer took back, so their answer is not sent. */
    private val withdrawn = ConcurrentHashMap.newKeySet<String>()

    fun onPush(context: Context, frame: JSONObject) {
        val from = frame.optJSONObject("from") ?: return
        val body = frame.optJSONObject("body") ?: return
        val computer = from.optString("id").ifBlank { return }
        val approvalId = body.optString("approval_id").ifBlank { return }
        val key = "$computer|$approvalId"
        when (body.optString("stage")) {
            "approval" -> if (body.optBoolean("any_device")) show(context, key, computer, from.optString("name").ifBlank { computer }, body)
            "approval_result" -> shown[key]?.let { id ->
                withdrawn.add(id)
                RiskGate.withdraw(id)
            }
        }
    }

    private fun show(context: Context, key: String, computer: String, computerName: String, body: JSONObject) {
        val chinese = context.resources.configuration.locales[0].language == "zh"
        val request = request(body, computerName, chinese)
        if (shown.putIfAbsent(key, request.id) != null) return
        val scopes = scopes(body)
        AppLogger.info(TAG, "$computerName asks about a step of “${request.elementText}”")
        scope.launch {
            val decision = try {
                RiskGate.ask(request)
            } finally {
                shown.remove(key)
            }
            if (withdrawn.remove(request.id)) return@launch
            val args = answer(decision, scopes) ?: return@launch
            args.put("approval_id", body.optString("approval_id"))
            runCatching { Hub.call(computer, "approve", args, timeoutMs = 30_000L) }
                .onFailure { AppLogger.warning(TAG, "answer to $computerName not delivered: ${it.message}") }
        }
    }

    /** The answers the computer's card offers besides *Deny*. */
    internal fun scopes(body: JSONObject): List<String> =
        body.optJSONArray("scopes")?.let { a -> (0 until a.length()).map { a.optString(it) } }.orEmpty()

    /** The card for a pushed question; [RiskRequest.pageUrl] names the computer, [RiskRequest.elementText] the task. */
    internal fun request(body: JSONObject, computerName: String, chinese: Boolean): RiskRequest {
        val title = body.optJSONObject("task")?.optString("title").orEmpty().trim()
        val reason = body.optString("reason").trim()
        val preview = (if (chinese) body.optString("preview_zh") else "").trim()
            .ifEmpty { body.optString("preview").trim() }
            .ifEmpty { reason }
        return RiskRequest(
            id = "hub-card-${UUID.randomUUID()}",
            sessionId = "hub:$computerName",
            kind = GuardKind.SCHEDULED,
            // "Always in this task" only when the computer offers it; it is kept there, not here.
            assessment = RiskAssessment(RiskClass.REMOTE, reason, target = if ("schedule" in scopes(body)) "task:$title" else null),
            preview = preview.take(1000),
            pageUrl = computerName,
            elementText = title,
            app = body.optString("app").trim().ifEmpty { null },
            timeoutMs = body.optInt("timeout", (RiskGate.TIMEOUT_MS / 1000).toInt()).coerceIn(30, 600) * 1000L,
        )
    }

    /** The `approve` args for a decision on the card; null when nobody answered (the computer keeps waiting). */
    internal fun answer(decision: RiskDecision, scopes: List<String>): JSONObject? = when (decision) {
        RiskDecision.ALLOW_ALWAYS -> JSONObject().put("allow", true).put("scope", if ("schedule" in scopes) "schedule" else "once")
        RiskDecision.ALLOW_ONCE, RiskDecision.ALLOW_SESSION -> JSONObject().put("allow", true).put("scope", "once")
        RiskDecision.DENY -> JSONObject().put("allow", false)
        RiskDecision.TIMEOUT -> null
    }
}
