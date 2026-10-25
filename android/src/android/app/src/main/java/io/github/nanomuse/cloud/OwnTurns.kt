package io.github.nanomuse.cloud

import android.content.Context
import com.openminis.app.data.model.AgentContentPart
import com.openminis.app.data.model.AgentToolDefinition
import com.openminis.app.data.model.LLMMessage
import com.openminis.app.data.model.LLMModel
import com.openminis.app.data.model.LLMResponse
import com.openminis.app.data.model.LLMStreamChunk
import com.openminis.app.data.model.LLMUsage
import com.openminis.app.data.model.ProviderInstance
import com.openminis.app.data.model.ThinkingLevel
import com.openminis.app.logging.AppLogger
import com.openminis.app.provider.LLMProvider
import io.github.nanomuse.models.ModelSlots
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import org.json.JSONArray
import org.json.JSONObject
import java.security.MessageDigest
import java.util.ArrayDeque
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * The account's record for turns that did not pass through the relay (relay 0.29,
 * docs/cloud.md, "The record"): a chat answered by a key of the person's own is sent up
 * afterwards in the shape the relay keeps, so the record is whole wherever the model was.
 *
 * [wrap] puts a recording layer around the provider `ProviderFactory` built: the request as
 * the provider is about to send it and the answer as it came back are assembled into one turn
 * once the call has finished, and queued for one worker thread: the pictures first
 * (`PUT /v1/blobs/{sha256}`, a `HEAD` saving what another device already put up), then
 * `POST /v1/turns` with `blob:sha256:` references where the pictures were. Nothing here can
 * fail a chat: an upload that fails is logged and dropped, the queue is bounded, and the key
 * of the person's own never leaves the phone (the relay gets the provider's catalogue id and
 * the model's). The relay's own models are not recorded here: the relay keeps those turns
 * itself. The desktop twin is `src/record.ts`; the iOS one `NanoMuseOwnTurns.swift`.
 */
object OwnTurns {
    private const val TAG = "OwnTurns"

    /** `blob:sha256:<hex>`: how a turn names a file put up first. */
    const val BLOB_PREFIX = "blob:sha256:"

    /** Uploads waiting their turn; past this the oldest is dropped (a relay out of reach must not hoard memory). */
    const val QUEUE_MAX = 32

    /** The relay keeps a turn of at most this many characters of JSON (`Cloud.TURN_MAX_CHARS`). */
    const val TURN_MAX_CHARS = 2_000_000

    private val queue = ArrayDeque<() -> Unit>()
    private val worker = Executors.newSingleThreadExecutor { r -> Thread(r, "nanomuse-own-turns").apply { isDaemon = true } }

    /** Uploads sent and dropped since the process started; the log reads them. */
    val sent = AtomicInteger()
    val dropped = AtomicInteger()

    /**
     * Whether turns of [instance] go to the record: the phone is signed in, the relay keeps
     * a record, and the instance is not the relay's own (those turns the relay already has).
     */
    fun wants(context: Context, instance: ProviderInstance): Boolean {
        if (NanoMuseCloud.apiKey(context) == null) return false
        if (NanoMuseCloud.instance(context)?.id == instance.id) return false
        return NanoMuseCloud.account(context)?.record?.on == true
    }

    /** The provider with the recording layer when [wants] says so, else as it is. */
    fun wrap(context: Context?, instance: ProviderInstance, provider: LLMProvider): LLMProvider {
        if (context == null || !wants(context, instance)) return provider
        val app = context.applicationContext
        val name = ModelSlots.catalogueOf(app, instance)?.id ?: instance.providerType.name
        return Recording(app, name.take(60), provider)
    }

    /** How many uploads are waiting. */
    val pending: Int get() = synchronized(queue) { queue.size }

    // ── the layer ──────────────────────────────────────────────────────────

    private class Recording(
        private val context: Context,
        private val providerName: String,
        private val inner: LLMProvider,
    ) : LLMProvider by inner {
        // the public entries are overridden too: the interface's defaults would call the
        // inner provider's clamped methods and pass this layer by
        override suspend fun sendMessage(
            messages: List<LLMMessage>, systemPrompt: String?, maxTokens: Int, temperature: Double?,
            imageParts: List<LLMMessage.ImagePart>, tools: List<AgentToolDefinition>, thinkingLevel: ThinkingLevel,
        ): LLMResponse = sendMessageClamped(messages, systemPrompt, maxTokens, temperature, imageParts, tools, clampThinkingLevel(thinkingLevel))

        override fun streamMessage(
            messages: List<LLMMessage>, systemPrompt: String?, maxTokens: Int, temperature: Double?,
            imageParts: List<LLMMessage.ImagePart>, tools: List<AgentToolDefinition>, thinkingLevel: ThinkingLevel,
        ): Flow<LLMStreamChunk> = streamMessageClamped(messages, systemPrompt, maxTokens, temperature, imageParts, tools, clampThinkingLevel(thinkingLevel))

        override suspend fun sendMessageClamped(
            messages: List<LLMMessage>, systemPrompt: String?, maxTokens: Int, temperature: Double?,
            imageParts: List<LLMMessage.ImagePart>, tools: List<AgentToolDefinition>, thinkingLevel: ThinkingLevel,
        ): LLMResponse {
            val model = inner.model
            val g = Gathered()
            try {
                val response = inner.sendMessageClamped(messages, systemPrompt, maxTokens, temperature, imageParts, tools, thinkingLevel)
                g.content.append(response.text)
                g.finish = response.stopReason
                g.usage = response.usage
                g.finished = true
                return response
            } catch (e: CancellationException) {
                g.ended = "aborted"
                throw e
            } catch (e: Throwable) {
                g.ended = "error"
                throw e
            } finally {
                enqueue(context, providerName, model, messages, systemPrompt, imageParts, tools, g)
            }
        }

        override fun streamMessageClamped(
            messages: List<LLMMessage>, systemPrompt: String?, maxTokens: Int, temperature: Double?,
            imageParts: List<LLMMessage.ImagePart>, tools: List<AgentToolDefinition>, thinkingLevel: ThinkingLevel,
        ): Flow<LLMStreamChunk> = flow {
            val model = inner.model
            val g = Gathered()
            try {
                inner.streamMessageClamped(messages, systemPrompt, maxTokens, temperature, imageParts, tools, thinkingLevel).collect { chunk ->
                    g.take(chunk)
                    emit(chunk)
                }
            } catch (e: CancellationException) {
                g.ended = "aborted"
                throw e
            } catch (e: Throwable) {
                g.ended = "error"
                throw e
            } finally {
                enqueue(context, providerName, model, messages, systemPrompt, imageParts, tools, g)
            }
        }
    }

    /** The answer as it came, gathered from the chunks (or the one response). */
    private class Gathered {
        val content = StringBuilder()
        val reasoning = StringBuilder()
        val calls = LinkedHashMap<String, JSONObject>()
        var usage: LLMUsage? = null
        var finish: String? = null
        var finished = false
        /** Why it ended short: `aborted` (the person stopped it), `error` (the stream threw), else `abandoned`. */
        var ended: String? = null

        fun take(chunk: LLMStreamChunk) {
            when (chunk) {
                is LLMStreamChunk.Text -> content.append(chunk.text)
                is LLMStreamChunk.ThinkingDelta -> reasoning.append(chunk.text)
                is LLMStreamChunk.ReasoningContent -> if (chunk.content.length >= reasoning.length) {
                    // the whole blob so far, not an increment
                    reasoning.setLength(0)
                    reasoning.append(chunk.content)
                }
                is LLMStreamChunk.Usage -> usage = chunk.usage
                is LLMStreamChunk.ToolUseStart -> calls.getOrPut(chunk.id) { toolCall(chunk.id, chunk.name, "") }
                is LLMStreamChunk.ToolInputDelta -> calls[chunk.id]?.getJSONObject("function")?.put("arguments", chunk.accumulated)
                is LLMStreamChunk.ToolCallComplete -> calls[chunk.id] = toolCall(chunk.id, chunk.name, chunk.args.toString())
                is LLMStreamChunk.Finished -> { finish = chunk.stopReason; finished = true }
                else -> Unit
            }
        }
    }

    private fun toolCall(id: String, name: String, arguments: String): JSONObject =
        JSONObject().put("id", id).put("type", "function").put("function", JSONObject().put("name", name).put("arguments", arguments))

    // ── the queue ──────────────────────────────────────────────────────────

    private fun enqueue(
        context: Context, providerName: String, model: LLMModel, messages: List<LLMMessage>, systemPrompt: String?,
        imageParts: List<LLMMessage.ImagePart>, tools: List<AgentToolDefinition>, g: Gathered,
    ) {
        val at = System.currentTimeMillis()
        val job = { upload(context, providerName, model, messages, systemPrompt, imageParts, tools, g, at) }
        synchronized(queue) {
            if (queue.size >= QUEUE_MAX) {
                queue.pollFirst()
                dropped.incrementAndGet()
            }
            queue.addLast(job)
        }
        worker.execute { drainOne() }
    }

    private fun drainOne() {
        val job = synchronized(queue) { queue.pollFirst() } ?: return
        try {
            job()
        } catch (t: Throwable) {
            dropped.incrementAndGet()
            AppLogger.info(TAG, "an own-key turn was not sent: ${t.message}")
        }
    }

    private class TurnFile(val sha256: String, val mime: String, val data: ByteArray)

    private fun upload(
        context: Context, providerName: String, model: LLMModel, messages: List<LLMMessage>, systemPrompt: String?,
        imageParts: List<LLMMessage.ImagePart>, tools: List<AgentToolDefinition>, g: Gathered, at: Long,
    ) {
        if (NanoMuseCloud.apiKey(context) == null) return // signed out meanwhile: nothing to send it as
        val files = LinkedHashMap<String, TurnFile>()
        val out = JSONArray()
        if (!systemPrompt.isNullOrEmpty()) out.put(JSONObject().put("role", "system").put("content", systemPrompt))
        for ((i, m) in messages.withIndex()) {
            // pictures passed beside the messages belong to the last user message, as the providers send them
            val extra = if (i == messages.lastIndex && m.role == LLMMessage.Role.USER) imageParts else emptyList()
            for (row in rows(m, extra, files)) out.put(row)
        }
        val response = JSONObject().put("message", assistantMessage(g))
        g.usage?.let { u ->
            val cached = u.cacheReadInputTokens ?: 0
            response.put(
                "usage",
                JSONObject().put("prompt_tokens", u.inputTokens + cached + (u.cacheCreationInputTokens ?: 0))
                    .put("completion_tokens", u.outputTokens).put("cached_tokens", cached),
            )
        }
        val partial = !g.finished
        val turn = JSONObject()
            .put("id", UUID.randomUUID().toString())
            .put("ts", at / 1000)
            .put("provider", providerName)
            .put("model", model.id.take(120))
            .put("conversation", conversationKey(messages))
            .put("messages", out)
            .put("response", response)
            .put("status", if (partial) "partial" else "ok")
        if (tools.isNotEmpty()) turn.put("tools", JSONArray().also { arr -> tools.forEach { arr.put(it.toOpenAIJson()) } })
        if (partial) turn.put("ended", g.ended ?: "abandoned")
        if (out.toString().length + response.toString().length > TURN_MAX_CHARS) {
            dropped.incrementAndGet()
            AppLogger.info(TAG, "a turn of ${messages.size} messages is past the relay's size and was not sent")
            return
        }
        for (f in files.values) {
            if (!NanoMuseCloud.hasBlob(context, f.sha256)) NanoMuseCloud.putBlob(context, f.sha256, f.mime, f.data)
        }
        NanoMuseCloud.postTurn(context, turn)
        sent.incrementAndGet()
    }

    /**
     * One message as the relay keeps it: OpenAI-style roles and content parts, pictures as
     * references, a tool result as a `tool` message of its own. An assistant message with
     * tool calls carries them as `tool_calls`.
     */
    private fun rows(m: LLMMessage, extra: List<LLMMessage.ImagePart>, files: MutableMap<String, TurnFile>): List<JSONObject> {
        val rows = ArrayList<JSONObject>()
        val parts = JSONArray()
        val calls = JSONArray()
        if (m.content.isNotEmpty() && m.contentParts.none { it is AgentContentPart.Text && it.text == m.content }) {
            parts.put(JSONObject().put("type", "text").put("text", m.content))
        }
        for (part in m.contentParts) {
            when (part) {
                is AgentContentPart.Text -> parts.put(JSONObject().put("type", "text").put("text", part.text))
                is AgentContentPart.ToolUse -> calls.put(toolCall(part.id, part.name, part.input.toString()))
                is AgentContentPart.ToolResult -> {
                    val row = JSONObject().put("role", "tool").put("tool_call_id", part.id).put("name", part.name)
                    val img = part.imageData
                    if (img != null) {
                        val content = JSONArray().put(JSONObject().put("type", "text").put("text", part.content))
                        content.put(imagePart(file(img, part.imageMimeType ?: "image/png", files)))
                        row.put("content", content)
                    } else {
                        row.put("content", part.content)
                    }
                    if (part.isError) row.put("is_error", true)
                    rows.add(row)
                }
                // a picture in the message body (a user's attachment kept as a part): a file
                is AgentContentPart.ImageData -> parts.put(imagePart(file(part.data, part.mimeType, files)))
            }
        }
        for (p in m.imageParts + extra) parts.put(imagePart(file(p.data, p.mimeType, files)))
        for (a in m.audioParts) parts.put(JSONObject().put("type", "text").put("text", "[audio ${a.format}, ${a.base64Data.length * 3 / 4} bytes]"))
        if (parts.length() > 0 || calls.length() > 0 || rows.isEmpty()) {
            val row = JSONObject().put("role", m.role.value)
            val onlyText = parts.length() == 1 && parts.getJSONObject(0).optString("type") == "text"
            row.put("content", if (parts.length() == 0) "" else if (onlyText) parts.getJSONObject(0).getString("text") else parts)
            if (!m.reasoningContent.isNullOrEmpty()) row.put("reasoning_content", m.reasoningContent)
            if (calls.length() > 0) row.put("tool_calls", calls)
            rows.add(0, row)
        }
        return rows
    }

    private fun imagePart(ref: String): JSONObject = JSONObject().put("type", "image_url").put("image_url", JSONObject().put("url", ref))

    /** The file's reference, the bytes remembered for the upload. */
    private fun file(data: ByteArray, mime: String, files: MutableMap<String, TurnFile>): String {
        val sha = sha256(data)
        files.getOrPut(sha) { TurnFile(sha, mime.ifBlank { "image/png" }, data) }
        return BLOB_PREFIX + sha
    }

    private fun assistantMessage(g: Gathered): JSONObject {
        val msg = JSONObject().put("role", "assistant").put("content", g.content.toString())
        if (g.reasoning.isNotEmpty()) msg.put("reasoning_content", g.reasoning.toString())
        if (g.calls.isNotEmpty()) msg.put("tool_calls", JSONArray().also { arr -> g.calls.values.forEach { arr.put(it) } })
        g.finish?.let { msg.put("finish_reason", it) }
        return msg
    }

    /**
     * The conversation's id: a hash of its first user message, the same id the Cloud's chat
     * requests carry in `X-NanoMuse-Conversation` ([CloudHeaders]), so the two halves of one
     * conversation meet in the record. Names no content; empty when there is no user text yet.
     */
    fun conversationKey(messages: List<LLMMessage>): String {
        for (m in messages) {
            if (m.role != LLMMessage.Role.USER) continue
            val text = m.contentParts.filterIsInstance<AgentContentPart.Text>().joinToString("") { it.text }.ifEmpty { m.content }
            if (text.isNotEmpty()) return "minis-" + sha256(text.toByteArray(Charsets.UTF_8)).take(32)
        }
        return ""
    }

    private fun sha256(data: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(data).joinToString("") { "%02x".format(it) }
}
