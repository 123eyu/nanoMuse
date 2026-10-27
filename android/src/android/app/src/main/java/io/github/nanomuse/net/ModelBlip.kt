package io.github.nanomuse.net

import com.openminis.app.data.model.LLMError
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import java.io.IOException

/**
 * A model call that a transport blip should not end (#640).
 *
 * The chat retries a network error, a transient error and a 5xx three times (1 s, 2 s, 4 s)
 * before it falls back or asks the person. The sandbox's `model_use`, which a script or a
 * scheduled task's tool calls, went to the provider once: a connection the peer closed or an
 * empty answer ended the task, and the person had to notice and press retry. iOS's bridge
 * has had this retry since before the fork; this is the Android half.
 */
object ModelBlip {
    /** The waits between attempts; `size` more attempts after the first. */
    val DELAYS_MS = longArrayOf(1_000L, 2_000L)

    /** The errors the chat retries on the same model: the transport, a transient, a 5xx. */
    fun isBlip(e: Throwable): Boolean = when (e) {
        is LLMError.NetworkError, is LLMError.TransientError -> true
        is LLMError.ProviderError -> FIVE_HUNDRED.containsMatchIn(e.detail)
        is LLMError -> false
        else -> e is IOException
    }

    /** Runs [call]; a blip is tried again after each wait in [delays]; anything else is thrown at once. */
    suspend fun <T> retrying(delays: LongArray = DELAYS_MS, call: suspend (attempt: Int) -> T): T {
        var attempt = 0
        while (true) {
            try {
                return call(attempt)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Throwable) {
                if (!isBlip(e) || attempt >= delays.size) throw e
                delay(delays[attempt])
                attempt++
            }
        }
    }

    /** A gateway's status, as the provider error carries it: `[503] …`. */
    private val FIVE_HUNDRED = Regex("""\[5\d\d]""")

    /**
     * A 4xx that is the gateway's own report of getting no answer from its upstream ("HTTP 0",
     * an empty status): a blip to retry, not a request to change. Gateways in front of several
     * providers answer this way for a dropped upstream connection.
     */
    fun saysUpstreamGaveNothing(message: String): Boolean = UPSTREAM_NOTHING.containsMatchIn(message)

    private val UPSTREAM_NOTHING = Regex("""(?i)\bHTTP\s*0\b""")
}
