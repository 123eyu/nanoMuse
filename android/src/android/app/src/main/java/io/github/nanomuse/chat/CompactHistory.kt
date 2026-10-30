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
     */
    fun retainRecentTurns(
        messages: List<LLMMessage>,
        maxTokens: Int = 8_000,
        estimate: (List<LLMMessage>) -> Int = { ContextEstimate.request(it) },
    ): List<LLMMessage> {
        val starts = messages.indices.filter { index ->
            val message = messages[index]
            message.role == LLMMessage.Role.USER &&
                message.contentParts.none { it is AgentContentPart.ToolResult }
        }
        if (starts.size < 2) return messages

        var start = starts.last()
        for (candidate in starts.dropLast(1).asReversed()) {
            if (estimate(messages.subList(candidate, messages.size)) > maxTokens) break
            start = candidate
        }
        return if (start == 0) messages else messages.subList(start, messages.size).toList()
    }

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
