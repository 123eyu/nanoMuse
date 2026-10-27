package io.github.nanomuse.net

import com.openminis.app.data.model.LLMError
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException

/**
 * The sandbox's `model_use` tries again after a transport blip (#640): the errors the chat
 * retries, as many times as there are waits, and nothing else.
 */
class ModelBlipTest {
    @Test fun `the chat's transient family is a blip, a refused request is not`() {
        assertTrue(ModelBlip.isBlip(LLMError.NetworkError(IOException("connection closed"))))
        assertTrue(ModelBlip.isBlip(LLMError.TransientError("Server returned an empty response")))
        assertTrue(ModelBlip.isBlip(LLMError.ProviderError("[502] bad gateway")))
        assertTrue(ModelBlip.isBlip(IOException("unexpected end of stream")))
        assertFalse(ModelBlip.isBlip(LLMError.ProviderError("[400] model not found")))
        assertFalse(ModelBlip.isBlip(LLMError.InvalidApiKey()))
        assertFalse(ModelBlip.isBlip(LLMError.RateLimited()))
        assertFalse(ModelBlip.isBlip(IllegalStateException("a bug")))
    }

    @Test fun `a blip is tried again after each wait, then thrown`() = runTest {
        var calls = 0
        val answer = ModelBlip.retrying(delays = longArrayOf(1L, 1L)) { attempt ->
            calls++
            if (attempt < 2) throw LLMError.NetworkError(IOException("connection closed"))
            "ok on attempt ${attempt + 1}"
        }
        assertEquals("ok on attempt 3", answer)
        assertEquals(3, calls)

        calls = 0
        try {
            ModelBlip.retrying<String>(delays = longArrayOf(1L, 1L)) { calls++; throw LLMError.TransientError("still down") }
            fail("a blip that never clears must be thrown")
        } catch (e: LLMError.TransientError) {
            assertEquals(3, calls)
        }
    }

    @Test fun `anything but a blip is thrown at once`() = runTest {
        var calls = 0
        try {
            ModelBlip.retrying<String>(delays = longArrayOf(1L, 1L)) { calls++; throw LLMError.ProviderError("[400] bad request") }
            fail("a 400 is not retried")
        } catch (e: LLMError.ProviderError) {
            assertEquals(1, calls)
        }
    }

    @Test fun `a gateway's HTTP 0 is the upstream giving nothing`() {
        assertTrue(ModelBlip.saysUpstreamGaveNothing("上游 HTTP 0"))
        assertTrue(ModelBlip.saysUpstreamGaveNothing("upstream http 0"))
        assertFalse(ModelBlip.saysUpstreamGaveNothing("HTTP 404 from upstream"))
        assertFalse(ModelBlip.saysUpstreamGaveNothing("model gpt-0 not found"))
    }
}
