package io.github.nanomuse.cloud

import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The relay's "where you stand" field (docs/cloud.md, relay 0.31) as the typing indicator
 * reads it: a queued status counts for the step whose dots were already showing when it came,
 * for fifteen seconds; a working status, or a chunk without a phase, is never "ahead of you".
 */
class NmStreamStatusTest {
    @After
    fun clear() = NmStreamStatus.reset()

    @Test
    fun aQueuedStatusIsReadForTheStepItArrivedIn() {
        val shownAt = System.currentTimeMillis() - 6_000
        NmStreamStatus.note(JSONObject("""{"phase": "queued", "ahead": 3, "waited_ms": 5004}"""))
        val s = NmStreamStatus.latest.value!!
        assertEquals("queued", s.phase)
        assertEquals(3, s.ahead)
        assertEquals(5004L, s.waitedMs)
        assertTrue(s.queuedFor(now = System.currentTimeMillis(), since = shownAt))
        // the dots of a later step appeared after it: an earlier step's news, not this one's
        assertFalse(s.queuedFor(now = s.at + 1_000, since = s.at + 500))
        // the relay repeats it every five seconds; one it stopped repeating is stale
        assertFalse(s.queuedFor(now = s.at + NmStreamStatus.STALE_MS + 1, since = shownAt))
    }

    @Test
    fun workingAndUnknownShapesSayNothingAboutAQueue() {
        NmStreamStatus.note(JSONObject("""{"phase": "working", "waited_ms": 12000}"""))
        val s = NmStreamStatus.latest.value!!
        assertEquals(-1, s.ahead)
        assertFalse(s.queuedFor(now = System.currentTimeMillis(), since = 0L))
        NmStreamStatus.reset()
        NmStreamStatus.note(JSONObject("""{"ahead": 2}"""))
        assertNull(NmStreamStatus.latest.value)
    }
}
