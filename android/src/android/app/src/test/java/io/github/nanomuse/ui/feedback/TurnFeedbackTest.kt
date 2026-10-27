package io.github.nanomuse.ui.feedback

import io.github.nanomuse.cloud.ClientEvents
import io.github.nanomuse.cloud.Provenance
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

/** A Stop, the question after it, and the thumbs: what reaches the record and what the screen keeps. */
class TurnFeedbackTest {
    private val sent = mutableListOf<JSONObject>()

    @Before fun fresh() {
        ClientEvents.reset()
        Provenance.reset()
        TurnFeedback.reset()
        sent.clear()
        ClientEvents.enabled = { true }
        ClientEvents.sender = { batch: JSONArray -> for (i in 0 until batch.length()) sent += batch.getJSONObject(i) }
    }

    @After fun clean() {
        ClientEvents.reset()
        ClientEvents.enabled = { false }
        ClientEvents.sender = null
        TurnFeedback.reset()
        Provenance.reset()
    }

    private fun events(): List<Pair<String, JSONObject>> {
        ClientEvents.drainNow()
        return sent.map { it.getString("kind") to it.getJSONObject("data") }
    }

    @Test fun `a Stop is reported at once and the person is asked why, once`() {
        Provenance.fields(Provenance.Session("s1").also { it.turns = { 2 } }, "minis-1", emptyList())
        TurnFeedback.stopped("s1")
        val ask = TurnFeedback.asking("s1")!!
        assertEquals(2, ask.turnNo)
        TurnFeedback.turnEnded("s1", errored = true) // the stream's end after a cancel: not an error on top of the Stop
        TurnFeedback.answerStop(ask, "changed_mind")
        assertNull(TurnFeedback.asking("s1"))
        val e = events()
        assertEquals(listOf("turn.ended", "turn.stopped"), e.map { it.first })
        assertEquals("aborted", e[0].second.getString("ended"))
        assertEquals(2, e[0].second.getInt("turn_no"))
        assertEquals("changed_mind", e[1].second.getString("reason"))
    }

    @Test fun `skipping the question says so, and a stale question is not shown`() {
        TurnFeedback.stopped("s2")
        val ask = TurnFeedback.asking("s2")!!
        assertNull(TurnFeedback.asking("s2", now = ask.at + TurnFeedback.STOP_ASK_MS + 1))
        assertNull(TurnFeedback.asking("other"))
        TurnFeedback.answerStop(ask, null)
        assertEquals("skipped", events().last().second.getString("reason"))
    }

    @Test fun `a routine's or another device's turn gets no question`() {
        Provenance.arm("s3", Provenance.RUN_SCHEDULE)
        TurnFeedback.stopped("s3")
        assertNull(TurnFeedback.asking("s3"))
        assertEquals("aborted", events().single().second.getString("ended"))
    }

    @Test fun `a turn that ends in an error is reported, a finished one is not`() {
        TurnFeedback.turnEnded("s4", errored = false)
        TurnFeedback.turnEnded("s4", errored = true)
        val e = events()
        assertEquals(1, e.size)
        assertEquals("error", e.single().second.getString("ended"))
    }

    @Test fun `the thumbs toggle, a reason rides with a thumb down, and the screen keeps the vote`() {
        TurnFeedback.vote("s5", "m1", "up")
        assertEquals("up", TurnFeedback.votes.value["m1"])
        TurnFeedback.vote("s5", "m1", "up") // the same thumb again takes it back
        assertNull(TurnFeedback.votes.value["m1"])
        TurnFeedback.vote("s5", "m1", "down")
        TurnFeedback.vote("s5", "m1", "down", reason = "unsafe", keep = true)
        assertEquals("down", TurnFeedback.votes.value["m1"])
        TurnFeedback.vote("s5", "m1", "up", reason = "unsafe") // a reason never rides with a thumb up
        val e = events().map { it.second }
        assertEquals(listOf("up", "none", "down", "down", "up"), e.map { it.getString("vote") })
        assertEquals(listOf(false, false, false, true, false), e.map { it.has("reason") })
        assertEquals("unsafe", e[3].getString("reason"))
    }
}
