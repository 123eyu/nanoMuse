package io.github.nanomuse.chat

import com.openminis.app.data.model.AgentContentPart
import com.openminis.app.data.model.AgentToolDefinition
import com.openminis.app.data.model.LLMMessage
import com.openminis.app.data.model.LLMUsage
import kotlin.math.ceil
import kotlin.math.max

/**
 * A conservative request-size estimate, not a tokenizer or a provider's bill.
 *
 * ASCII costs about one token per four characters. Non-ASCII BMP code points cost two,
 * and supplementary code points four, so Chinese and emoji do not inherit an English-only
 * character divisor. These are heuristics: [Calibration] raises them when a provider reports
 * a larger input count. Images use the caller's counter, independently of their byte size.
 */
object ContextEstimate {
    private const val MESSAGE_OVERHEAD = 3
    private const val REPLY_OVERHEAD = 3

    /** Cached tokens still occupy context, even if the provider reports fresh input separately. */
    fun inputTokens(usage: LLMUsage): Int = maxOf(
        usage.latestContextTokens.coerceAtLeast(0),
        add(usage.inputTokens, usage.cacheReadInputTokens ?: 0, usage.cacheCreationInputTokens ?: 0),
    )

    fun text(text: String): Int {
        var quarters = 0L
        var index = 0
        while (index < text.length) {
            val codePoint = Character.codePointAt(text, index)
            quarters += when {
                codePoint < 0x80 -> 1L
                codePoint <= 0xffff -> 8L
                else -> 16L
            }
            if (quarters >= Int.MAX_VALUE.toLong() * 4) return Int.MAX_VALUE
            index += Character.charCount(codePoint)
        }
        return ((quarters + 3) / 4).toInt()
    }

    fun part(part: AgentContentPart, imageTokens: (ByteArray) -> Int): Int = when (part) {
        is AgentContentPart.Text -> text(part.text)
        is AgentContentPart.ToolUse -> add(
            MESSAGE_OVERHEAD,
            text(part.id),
            text(part.name),
            text(part.input.toString()),
            text(part.thoughtSignature.orEmpty()),
        )
        is AgentContentPart.ToolResult -> add(
            MESSAGE_OVERHEAD,
            text(part.id),
            text(part.name),
            text(part.content),
            image(part.imageData, imageTokens),
        )
        is AgentContentPart.ImageData -> image(part.data, imageTokens)
    }

    fun request(
        messages: List<LLMMessage>,
        systemPrompt: String? = null,
        tools: List<AgentToolDefinition> = emptyList(),
        imageTokens: (ByteArray) -> Int = { 1000 },
    ): Int {
        var total = REPLY_OVERHEAD
        if (!systemPrompt.isNullOrEmpty()) total = add(total, MESSAGE_OVERHEAD, text(systemPrompt))
        for (message in messages) {
            total = add(total, MESSAGE_OVERHEAD, text(message.reasoningContent.orEmpty()))
            // Providers serialize structured parts instead of the aggregate content string.
            // Counting both would charge the assistant's visible text twice.
            if (message.contentParts.isEmpty()) {
                total = add(total, text(message.content))
            } else {
                for (contentPart in message.contentParts) total = add(total, part(contentPart, imageTokens))
            }
            for (imagePart in message.imageParts) total = add(total, image(imagePart.data, imageTokens))
            // Native audio is not tokenized as its base64 transport text. Keep the previous
            // audio boundary: its model-dependent cost is known only from provider usage.
        }
        for (tool in tools) total = add(total, text(tool.toOpenAIJson().toString()))
        return total
    }

    private fun image(bytes: ByteArray?, imageTokens: (ByteArray) -> Int): Int =
        if (bytes == null || bytes.isEmpty()) 0 else imageTokens(bytes).coerceAtLeast(0)

    private fun add(vararg counts: Int): Int {
        var total = 0L
        for (count in counts) {
            total += count.coerceAtLeast(0)
            if (total >= Int.MAX_VALUE) return Int.MAX_VALUE
        }
        return total.toInt()
    }

    /**
     * Provider usage calibrates a ratio, never a fixed floor left over from an older request.
     * Re-estimating a request after offload or compaction therefore reflects the smaller input.
     * Keep the highest observed ratio for this provider/model; a lower count cannot make the
     * local heuristic less cautious. The owner resets it when the provider or model changes.
     */
    class Calibration {
        private var scale = 1.0

        @Synchronized
        fun observe(actualTokens: Int, localEstimate: Int) {
            if (actualTokens <= 0 || localEstimate <= 0) return
            scale = max(scale, actualTokens.toDouble() / localEstimate)
        }

        @Synchronized
        fun estimate(localEstimate: Int): Int {
            val scaled = localEstimate.coerceAtLeast(0).toDouble() * scale
            return if (scaled >= Int.MAX_VALUE) Int.MAX_VALUE else ceil(scaled).toInt()
        }

        @Synchronized
        fun reset() {
            scale = 1.0
        }
    }
}
