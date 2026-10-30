package io.github.nanomuse.chat

import com.openminis.app.data.model.AgentContentPart
import com.openminis.app.data.model.AgentToolDefinition
import com.openminis.app.data.model.AgentToolParam
import com.openminis.app.data.model.LLMMessage
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ContextEstimateTest {
    private fun user(text: String) = LLMMessage(LLMMessage.Role.USER, text)

    @Test
    fun `a long Chinese canonical message counts without content parts`() {
        val chinese = "请继续读取中文报告，保留原文件。".repeat(300)
        val estimate = ContextEstimate.request(listOf(user(chinese)))
        assertTrue("plain content must not disappear from the estimate", estimate >= chinese.length)
        val english = "Read the report and keep the original file. ".repeat(300)
        assertTrue(ContextEstimate.text(english) < english.length / 2)
        assertTrue(ContextEstimate.text("😀".repeat(100)) >= 400)
    }

    @Test
    fun `a dozen Chinese tool results cross the compact threshold and offload reduces them`() {
        val output = "读取结果中文。".repeat(240)
        val history = buildList {
            add(user("只读取报告，不修改文件。"))
            repeat(12) { step ->
                add(LLMMessage(
                    role = LLMMessage.Role.ASSISTANT,
                    content = "",
                    contentParts = listOf(AgentContentPart.ToolUse(
                        "call-$step", "file_read", JSONObject().put("path", "report-$step.txt"),
                    )),
                ))
                add(LLMMessage(
                    role = LLMMessage.Role.USER,
                    content = "",
                    contentParts = listOf(AgentContentPart.ToolResult("call-$step", "file_read", output)),
                ))
            }
        }
        val before = ContextEstimate.request(history)
        assertTrue(before >= ContextBudget.COMPACT_TOKENS)
        val offloaded = history.map { message ->
            message.copy(contentParts = message.contentParts.map { part ->
                if (part is AgentContentPart.ToolResult) {
                    part.copy(content = "[CONTEXT OFFLOADED] Read the original from report.txt")
                } else part
            })
        }
        assertTrue(ContextEstimate.request(offloaded) < ContextBudget.OFFLOAD_TARGET_TOKENS)
        assertEquals(before, ContextEstimate.request(history))
        assertTrue(history.flatMap { it.contentParts }.filterIsInstance<AgentContentPart.ToolResult>()
            .all { it.content == output })
        assertEquals(12, offloaded.flatMap { it.contentParts }.filterIsInstance<AgentContentPart.ToolUse>().size)
        assertEquals(12, offloaded.flatMap { it.contentParts }.filterIsInstance<AgentContentPart.ToolResult>().size)
    }

    @Test
    fun `system instructions schemas reasoning and both image representations are counted`() {
        val message = user("看图")
        val plain = ContextEstimate.request(listOf(message))
        val tool = AgentToolDefinition(
            name = "file_read",
            description = "读取文件并返回内容。".repeat(200),
            parameters = mapOf("path" to AgentToolParam("string", "要读取的文件路径")),
            required = listOf("path"),
        )
        val withSchema = ContextEstimate.request(listOf(message), tools = listOf(tool))
        assertTrue(withSchema >= plain + ContextEstimate.text(tool.description))
        val withInstructions = ContextEstimate.request(listOf(message), systemPrompt = "只读取，不发送。")
        assertTrue(withInstructions > plain)
        val imageSizes = mutableListOf<Int>()
        val imageCounter: (ByteArray) -> Int = { imageSizes.add(it.size); 777 }
        val pictured = message.copy(imageParts = listOf(LLMMessage.ImagePart(ByteArray(5), "image/png")))
        assertEquals(plain + 777, ContextEstimate.request(listOf(pictured), imageTokens = imageCounter))
        val structured = message.copy(content = "ignored aggregate".repeat(1000), contentParts = listOf(
            AgentContentPart.Text(message.content),
            AgentContentPart.ImageData(ByteArray(7), "image/png"),
        ))
        assertEquals(plain + 777, ContextEstimate.request(listOf(structured), imageTokens = imageCounter))
        val toolImage = LLMMessage(LLMMessage.Role.USER, "", contentParts = listOf(
            AgentContentPart.ToolResult("call", "read_image", "", imageData = ByteArray(9)),
        ))
        assertTrue(ContextEstimate.request(listOf(toolImage), imageTokens = imageCounter) >= 777)
        assertEquals(listOf(5, 7, 9), imageSizes)
        val reasoned = message.copy(reasoningContent = "需要先查看报告来源。".repeat(200))
        assertTrue(ContextEstimate.request(listOf(reasoned)) > plain)
    }

    @Test
    fun `native audio bytes do not turn a short prompt into a full text context`() {
        val message = user("请转录这段录音。")
        val audio = LLMMessage.AudioPart(
            "wav", java.util.Base64.getEncoder().encodeToString(ByteArray(96 * 1024)),
        )
        assertTrue(ContextEstimate.text(audio.base64Data) >= ContextBudget.COMPACT_TOKENS)
        assertEquals(
            ContextEstimate.request(listOf(message)),
            ContextEstimate.request(listOf(message.copy(audioParts = listOf(audio)))),
        )
    }

    @Test
    fun `provider calibration follows smaller requests instead of retaining old usage`() {
        val calibration = ContextEstimate.Calibration()
        val large = ContextEstimate.request(listOf(user("中文报告".repeat(5000))))
        calibration.observe(actualTokens = large * 2, localEstimate = large)
        assertEquals(large * 2, calibration.estimate(large))
        val small = ContextEstimate.request(listOf(user("报告已移至文件，仅保留摘要。")))
        assertEquals(small * 2, calibration.estimate(small))
        assertTrue(calibration.estimate(small) < ContextBudget.OFFLOAD_TARGET_TOKENS)
        calibration.observe(actualTokens = 0, localEstimate = small)
        calibration.observe(actualTokens = small / 2, localEstimate = small)
        assertEquals(small * 2, calibration.estimate(small))
        calibration.reset()
        assertEquals(small, calibration.estimate(small))
    }

    @Test
    fun `opaque image and calibration counts saturate instead of wrapping below zero`() {
        val image = LLMMessage.ImagePart(byteArrayOf(1), "image/png")
        assertEquals(Int.MAX_VALUE, ContextEstimate.request(
            listOf(user("read").copy(imageParts = listOf(image, image))),
            imageTokens = { Int.MAX_VALUE },
        ))
        val calibration = ContextEstimate.Calibration()
        calibration.observe(actualTokens = Int.MAX_VALUE, localEstimate = 1)
        assertEquals(Int.MAX_VALUE, calibration.estimate(1000))
    }
}
