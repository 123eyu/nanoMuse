package com.openminis.app.provider

import com.openminis.app.data.model.AgentContentPart
import com.openminis.app.data.model.LLMMessage
import com.openminis.app.data.model.LLMModel
import com.openminis.app.provider.openai.OpenAIProvider
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ResponsesCallIdDeduplicationTest {
    private val provider = OpenAIProvider(
        apiKey = "test-key",
        model = LLMModel(
            id = "responses-model",
            displayName = "Responses Model",
            provider = "test",
        ),
        basePath = "https://example.invalid/v1",
        useResponsesAPI = true,
    )

    private fun toolUse(id: String) = AgentContentPart.ToolUse(
        id = id,
        name = "echo",
        input = JSONObject(),
    )

    private fun toolResult(id: String, output: String) = AgentContentPart.ToolResult(
        id = id,
        name = "echo",
        content = output,
    )

    @Test
    fun `responses api deduplicates call ids and keeps outputs paired`() {
        val longId = "x".repeat(64)
        val messages = listOf(
            LLMMessage(
                role = LLMMessage.Role.ASSISTANT,
                content = "",
                contentParts = listOf(
                    toolUse("call_same"),
                    toolUse("call_same"),
                    toolUse("call_same-2"),
                ),
            ),
            LLMMessage(
                role = LLMMessage.Role.USER,
                content = "",
                contentParts = listOf(
                    toolResult("call_same", "first"),
                    toolResult("call_same", "second"),
                    toolResult("call_same-2", "natural suffix"),
                ),
            ),
            LLMMessage(
                role = LLMMessage.Role.ASSISTANT,
                content = "",
                contentParts = listOf(
                    toolUse("call_same"),
                    toolUse(longId),
                    toolUse(longId),
                ),
            ),
            LLMMessage(
                role = LLMMessage.Role.USER,
                content = "",
                contentParts = listOf(
                    toolResult("call_same", "third"),
                    toolResult(longId, "long first"),
                    toolResult(longId, "long second"),
                ),
            ),
        )

        val input = provider.buildResponsesAPIBody(
            messages = messages,
            systemPrompt = null,
            maxTokens = 1024,
            stream = false,
        ).getJSONArray("input")

        val calls = mutableListOf<String>()
        val outputs = mutableListOf<String>()
        for (i in 0 until input.length()) {
            val item = input.getJSONObject(i)
            when (item.optString("type")) {
                "function_call" -> calls += item.getString("call_id")
                "function_call_output" -> outputs += item.getString("call_id")
            }
        }
        assertEquals(
            listOf(
                "call_same",
                "call_same-2",
                "call_same-2-2",
                "call_same-3",
                longId,
                "${"x".repeat(62)}-2",
            ),
            calls,
        )
        assertEquals(calls, outputs)
        assertEquals(calls.size, calls.toSet().size)
        assertTrue(calls.all { it.length <= 64 })
    }
}
