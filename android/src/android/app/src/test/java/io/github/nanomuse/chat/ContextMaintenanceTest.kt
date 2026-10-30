package io.github.nanomuse.chat

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ContextMaintenanceTest {
    @Test
    fun `a failed early summary does not prevent answering the current question`() = runTest {
        val guard = ContextMaintenance()
        var requests = 0
        var tokens = 32_038
        suspend fun check() = guard.check(128_000, { tokens }) { before, attempt ->
            assertEquals(32_038, before)
            assertEquals(1, attempt)
            requests += 1
            false
        }

        assertEquals(ContextMaintenance.Action.PROCEED, check())
        assertTrue(guard.softDeferred)
        // More tool output below the true boundary must not launch another summary.
        tokens = 36_000
        assertEquals(ContextMaintenance.Action.PROCEED, check())
        assertEquals(1, requests)
    }

    @Test
    fun `a summary that adds text moves the divider but does not trap a small request`() = runTest {
        val guard = ContextMaintenance()
        var tokens = 32_038
        val action = guard.check(128_000, { tokens }) { _, _ ->
            tokens = 33_000
            true
        }
        assertEquals(ContextMaintenance.Action.COMPACTED, action)
        assertTrue(guard.softDeferred)
        assertEquals(ContextMaintenance.Action.PROCEED, guard.check(128_000, { tokens }) { _, _ ->
            error("the same retained history must not be compacted again")
        })
    }

    @Test
    fun `a partial reduction above the soft target resumes instead of summarizing again`() = runTest {
        val guard = ContextMaintenance()
        var tokens = 45_000
        assertEquals(ContextMaintenance.Action.COMPACTED, guard.check(128_000, { tokens }) { _, _ ->
            tokens = 35_000
            true
        })
        assertEquals(ContextMaintenance.Action.PROCEED, guard.check(128_000, { tokens }) { _, _ ->
            error("partial reduction already made room for the answer")
        })
    }

    @Test
    fun `hard pressure retries a deferred summary and stops on failure`() = runTest {
        val guard = ContextMaintenance()
        var tokens = 32_038
        assertEquals(ContextMaintenance.Action.PROCEED, guard.check(128_000, { tokens }) { _, _ -> false })
        tokens = 108_000
        assertEquals(ContextMaintenance.Action.STOP, guard.check(128_000, { tokens }) { _, attempt ->
            assertEquals(2, attempt)
            false
        })
        assertEquals(2, guard.attempts)
    }

    @Test
    fun `hard pressure stops when a successful summary recovers no space`() = runTest {
        val guard = ContextMaintenance()
        assertEquals(ContextMaintenance.Action.STOP, guard.check(128_000, { 108_000 }) { _, _ -> true })
    }

    @Test
    fun `recovery below the soft target permits maintenance after later growth`() = runTest {
        val guard = ContextMaintenance()
        var tokens = 32_038
        guard.check(128_000, { tokens }) { _, _ -> false }
        tokens = 108_000
        assertEquals(ContextMaintenance.Action.COMPACTED, guard.check(128_000, { tokens }) { _, _ ->
            tokens = 20_000
            true
        })
        assertFalse(guard.softDeferred)
        tokens = 33_000
        guard.check(128_000, { tokens }) { _, attempt ->
            assertEquals(3, attempt)
            false
        }
        assertEquals(3, guard.attempts)
    }

    @Test
    fun `hard pressure can recover through a bounded sequence of reductions`() = runTest {
        val guard = ContextMaintenance()
        var tokens = 120_000
        repeat(2) {
            assertEquals(ContextMaintenance.Action.COMPACTED, guard.check(128_000, { tokens }) { _, _ ->
                tokens -= 5_000
                true
            })
        }
        assertEquals(ContextMaintenance.Action.COMPACTED, guard.check(128_000, { tokens }) { _, _ ->
            tokens = 100_000
            true
        })
        assertEquals(ContextMaintenance.Action.PROCEED, guard.check(128_000, { tokens }) { _, _ ->
            error("recovered below the hard reserve")
        })
        tokens = 108_000
        assertEquals(ContextMaintenance.Action.STOP, guard.check(128_000, { tokens }) { _, _ ->
            error("three attempts already consumed")
        })
        assertEquals(3, guard.attempts)
    }

    @Test
    fun `small window exhaustion and idle requests do not call the summarizer`() = runTest {
        val cases = listOf(
            Triple(16_000, 14_399, ContextMaintenance.Action.PROCEED),
            Triple(16_000, 14_400, ContextMaintenance.Action.STOP),
            Triple(40_000, 29_999, ContextMaintenance.Action.PROCEED),
            Triple(40_000, 30_000, ContextMaintenance.Action.STOP),
            Triple(128_000, 31_999, ContextMaintenance.Action.PROCEED),
            Triple(0, Int.MAX_VALUE, ContextMaintenance.Action.PROCEED),
        )
        for ((window, tokens, expected) in cases) {
            assertEquals(expected, ContextMaintenance().check(window, { tokens }) { _, _ ->
                error("no summary permitted for $tokens / $window")
            })
        }
        assertEquals(54_000, ContextMaintenance.hardThreshold(64_000))
        assertEquals(108_000, ContextMaintenance.hardThreshold(128_000))
        assertEquals(Int.MAX_VALUE - 20_000, ContextMaintenance.hardThreshold(Int.MAX_VALUE))
    }

    @Test
    fun `cancelling a summary never resumes a model request`() = runTest {
        val cancellation = CancellationException("user stopped")
        var caught: CancellationException? = null
        try {
            ContextMaintenance().check(128_000, { 32_038 }) { _, _ -> throw cancellation }
        } catch (error: CancellationException) {
            caught = error
        }
        assertTrue(caught === cancellation)
    }

    @Test
    fun `a changed model can start a fresh maintenance budget`() = runTest {
        val guard = ContextMaintenance()
        guard.check(128_000, { 32_038 }) { _, _ -> false }
        assertTrue(guard.softDeferred)
        guard.reset()
        assertFalse(guard.softDeferred)
        assertEquals(0, guard.attempts)
        guard.check(128_000, { 32_038 }) { _, attempt ->
            assertEquals(1, attempt)
            false
        }
        assertEquals(1, guard.attempts)
    }
}
