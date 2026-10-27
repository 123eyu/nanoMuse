package io.github.nanomuse.cloud

import org.json.JSONArray
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

/**
 * The record's events (relay 0.31): codes and numbers only, shaped on the phone before they
 * travel, gathered into batches, and nothing at all without an account.
 */
class ClientEventsTest {
    private val batches = mutableListOf<JSONArray>()

    @Before fun fresh() {
        ClientEvents.reset()
        Provenance.reset()
        batches.clear()
        ClientEvents.enabled = { true }
        ClientEvents.sender = { batches += it }
    }

    @After fun clean() {
        ClientEvents.reset()
        ClientEvents.enabled = { false }
        ClientEvents.sender = null
    }

    @Test fun `prose never travels, codes and numbers do`() {
        val e = ClientEvents.event(
            "sentinel.decision",
            mapOf(
                "tool" to "shell",
                "risk" to "sensitive",
                "allow" to true,
                "waited_s" to 12L,
                "latency_ms" to 850.5,
                "preview" to "rm -rf ~/Documents && echo \"gone\"", // not a code: quotes and more
                "note" to "a".repeat(121), // too long
                "Bad-Key" to "x",
                "nested" to mapOf("a" to 1),
                "none" to null,
                "kind" to Fake.ONE,
            ),
        )!!
        val data = e.getJSONObject("data")
        assertEquals(setOf("tool", "risk", "allow", "waited_s", "latency_ms", "kind"), data.keys().asSequence().toSet())
        assertEquals("shell", data.getString("tool"))
        assertTrue(data.getBoolean("allow"))
        assertEquals(12L, data.getLong("waited_s"))
        assertEquals("one", data.getString("kind"))
        assertEquals("sentinel.decision", e.getString("kind"))
        assertTrue(e.getLong("ts") > 1_700_000_000)
        assertFalse(e.has("turn"))
    }

    @Test fun `a kind of the wrong shape is not queued at all`() {
        assertNull(ClientEvents.event("Sentinel Decision", mapOf("a" to 1)))
        assertNull(ClientEvents.event("a.b.c.d.e", mapOf("a" to 1)))
        ClientEvents.add("Nope", mapOf("a" to 1))
        assertEquals(0, ClientEvents.pending)
    }

    @Test fun `the turn is named by the relay's id, or by the own-key turn's id with its source`() {
        val cloud = ClientEvents.event("turn.ended", mapOf("ended" to "aborted"), Provenance.TurnRef("req_1", own = false), "minis-c")!!
        assertEquals("req_1", cloud.getString("turn"))
        assertFalse(cloud.has("source"))
        assertEquals("minis-c", cloud.getString("conversation"))
        val own = ClientEvents.event("turn.ended", mapOf("ended" to "error"), Provenance.TurnRef("6f1a", own = true))!!
        assertEquals("6f1a", own.getString("turn"))
        assertEquals("own", own.getString("source"))
    }

    @Test fun `addFor looks the session's turn and conversation up`() {
        val session = Provenance.Session("s1").also { it.turns = { 3 } }
        Provenance.fields(session, "minis-s1", emptyList())
        Provenance.noteResponse("minis-s1", "req_9")
        ClientEvents.turnStopped("s1", "slow")
        ClientEvents.turnFeedback("s1", "down", "unsafe")
        ClientEvents.turnFeedback("s1", "up", "unsafe") // a reason goes with a thumb down only… the helper passes what it is given
        assertEquals(3, ClientEvents.pending)
        ClientEvents.drainNow()
        assertEquals(1, batches.size)
        val events = batches.single()
        assertEquals(3, events.length())
        val stopped = events.getJSONObject(0)
        assertEquals("turn.stopped", stopped.getString("kind"))
        assertEquals("req_9", stopped.getString("turn"))
        assertEquals("minis-s1", stopped.getString("conversation"))
        assertEquals("slow", stopped.getJSONObject("data").getString("reason"))
        assertEquals(3, stopped.getJSONObject("data").getInt("turn_no"))
        assertEquals("unsafe", events.getJSONObject(1).getJSONObject("data").getString("reason"))
    }

    @Test fun `events go in batches of a hundred`() {
        for (i in 1..230) ClientEvents.add("screen.viewed", mapOf("screen" to "chat", "n" to i))
        assertEquals(230, ClientEvents.pending)
        ClientEvents.drainNow()
        assertEquals(listOf(100, 100, 30), batches.map { it.length() })
        assertEquals(0, ClientEvents.pending)
        assertEquals(230, batches.sumOf { it.length() })
    }

    @Test fun `past the queue's size the oldest goes`() {
        for (i in 1..(ClientEvents.QUEUE_MAX + 10)) ClientEvents.add("screen.viewed", mapOf("n" to i))
        assertEquals(ClientEvents.QUEUE_MAX, ClientEvents.pending)
        ClientEvents.drainNow()
        assertEquals(11, batches.first().getJSONObject(0).getJSONObject("data").getInt("n"))
    }

    @Test fun `nothing is queued without an account`() {
        ClientEvents.enabled = { false }
        ClientEvents.add("screen.viewed", mapOf("screen" to "chat"))
        assertEquals(0, ClientEvents.pending)
    }

    @Test fun `a batch the relay refuses is dropped, not kept`() {
        val before = ClientEvents.dropped.get()
        ClientEvents.sender = { throw NanoMuseCloud.CloudException("rate_limited", "later") }
        ClientEvents.add("screen.viewed", mapOf("screen" to "chat"))
        ClientEvents.drainNow()
        assertEquals(0, ClientEvents.pending)
        assertEquals(before + 1, ClientEvents.dropped.get())
    }

    private enum class Fake { ONE }
}
