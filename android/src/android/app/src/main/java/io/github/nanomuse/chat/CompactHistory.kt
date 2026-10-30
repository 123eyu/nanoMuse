package io.github.nanomuse.chat

import com.openminis.app.data.model.AgentContentPart
import com.openminis.app.data.model.LLMMessage

/** Request-only views of compacted history. The saved conversation is never truncated. */
object CompactHistory {
    /**
     * Keep a contiguous suffix of whole user turns within [maxTokens]. A tool-result message
     * is not a turn boundary, even though it has the USER role. Always retain the newest turn:
     * a summary of earlier work must not replace the person's active request or its results.
     * If that turn alone exceeds the budget, the caller's context guard still decides what fits.
     * Set [keepNewestTurn] false only for optional history preceding a separately protected
     * current user turn. In that case every old turn may be dropped when no whole turn fits.
     * If a user message arrived between a tool call and its result, widen the retained prefix
     * to the call's whole user turn. Recheck until the boundary cuts no such pair.
     */
    fun retainRecentTurns(
        messages: List<LLMMessage>,
        maxTokens: Int,
        keepNewestTurn: Boolean = true,
        estimate: (List<LLMMessage>) -> Int = { ContextEstimate.request(it) },
    ): List<LLMMessage> {
        val starts = messages.indices.filter { index ->
            ContextTurns.isUserRequest(messages[index])
        }
        if (starts.size < 2) {
            return if (!keepNewestTurn && estimate(messages) > maxTokens) emptyList() else messages
        }

        val firstCalls = mutableMapOf<String, Int>()
        val results = mutableListOf<Pair<Int, String>>()
        messages.forEachIndexed { index, message ->
            for (part in message.contentParts) when (part) {
                is AgentContentPart.ToolUse -> firstCalls.putIfAbsent(part.id, index)
                is AgentContentPart.ToolResult -> results += index to part.id
                else -> Unit
            }
        }
        fun pairedStart(candidate: Int): Int {
            var boundary = candidate
            while (true) {
                val earlierCall = results.asSequence()
                    .filter { (index, _) -> index >= boundary }
                    .mapNotNull { (_, id) -> firstCalls[id]?.takeIf { it < boundary } }
                    .minOrNull() ?: return boundary
                boundary = starts.lastOrNull { it <= earlierCall } ?: 0
            }
        }

        var start = pairedStart(starts.last())
        if (!keepNewestTurn && estimate(messages.subList(start, messages.size)) > maxTokens) {
            return emptyList()
        }
        for (candidate in starts.asReversed()) {
            if (candidate >= start) continue
            val paired = pairedStart(candidate)
            if (estimate(messages.subList(paired, messages.size)) > maxTokens) break
            start = paired
        }
        return if (start == 0) messages else messages.subList(start, messages.size).toList()
    }

    /** Charge the summary and fixed request cost before replaying whole recent turns. */
    fun retainForBudget(
        messages: List<LLMMessage>,
        budget: ContextBudget.Plan,
        summaryTokens: Int = 0,
        keepNewestTurn: Boolean = true,
        estimate: (List<LLMMessage>) -> Int = { ContextEstimate.request(it) },
    ): List<LLMMessage> = retainRecentTurns(
        messages, budget.replayBudget(summaryTokens), keepNewestTurn, estimate,
    )

    /** Providers serialize parts instead of [LLMMessage.content] when parts are present. */
    fun prependSummary(message: LLMMessage, summary: String): LLMMessage {
        if (summary.isBlank()) return message
        return message.copy(
            content = summary + "\n\n" + message.content,
            contentParts = if (message.contentParts.isEmpty()) emptyList() else {
                listOf(AgentContentPart.Text(summary + "\n\n")) + message.contentParts
            },
        )
    }
}
