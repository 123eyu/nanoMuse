package io.github.nanomuse.cloud

import com.openminis.app.data.model.AgentContentPart
import com.openminis.app.data.model.LLMMessage
import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import kotlin.coroutines.EmptyCoroutineContext

/**
 * Where a call sits, as the relay hears it (relay 0.31): the turn and step counted from the
 * chat when it is there and from the request when it is not, the run pinned by whoever starts
 * the turn, the screen task's trajectory, and the ids the events name a turn by.
 */
class ProvenanceTest {
    @Before fun fresh() = Provenance.reset()
    @After fun clean() = Provenance.reset()

    private fun user(text: String) = LLMMessage(LLMMessage.Role.USER, text)
    private fun assistant(text: String) = LLMMessage(LLMMessage.Role.ASSISTANT, text)
    private fun toolResult() = LLMMessage(
        LLMMessage.Role.USER, "",
        contentParts = listOf(AgentContentPart.ToolResult(id = "c1", name = "shell", content = "ok")),
    )

    @Test fun `turn and step are counted from the request when no chat says otherwise`() {
        val one = listOf(user("hi"))
        assertEquals(1, Provenance.turnOf(one))
        assertEquals(1, Provenance.stepOf(one))
        val second = listOf(user("hi"), assistant("hello"), user("do it"), assistant("calling a tool"), toolResult())
        assertEquals(2, Provenance.turnOf(second)) // a tool's result is not the person's message
        assertEquals(2, Provenance.stepOf(second)) // one answer since the person spoke, so this is the second call
        val f = Provenance.fields(EmptyCoroutineContext, "minis-abc", second)
        assertEquals(Provenance.RUN_PERSON, f.run)
        assertEquals(2, f.turn)
        assertEquals(2, f.step)
        assertNull(f.trajectory)
    }

    @Test fun `the chat's own count wins and the steps count up within a turn`() {
        val session = Provenance.Session("s1")
        var turns = 7
        session.turns = { turns }
        val msgs = listOf(user("first")) // a compacted request shows one message; the chat knows better
        val a = Provenance.fields(session, "minis-c1", msgs)
        assertEquals(7, a.turn)
        assertEquals(1, a.step)
        val b = Provenance.fields(session, "minis-c1", msgs + assistant("…") + toolResult())
        assertEquals(7, b.turn)
        assertEquals(2, b.step)
        Provenance.turnEnded("s1")
        turns = 8
        val c = Provenance.fields(session, "minis-c1", msgs)
        assertEquals(8, c.turn)
        assertEquals(1, c.step)
        assertEquals(8, Provenance.turnNo("s1"))
        assertEquals("minis-c1", Provenance.conversationOf("s1"))
        assertEquals("s1", Provenance.sessionOf("minis-c1"))
    }

    @Test fun `who started the turn is pinned until it ends`() {
        val session = Provenance.Session("s2").also { it.turns = { 3 } }
        assertEquals(Provenance.RUN_PERSON, Provenance.runOf("s2"))
        Provenance.arm("s2", Provenance.RUN_FEED)
        assertEquals(Provenance.RUN_FEED, Provenance.fields(session, "minis-f", listOf(user("write the feed"))).run)
        assertEquals(Provenance.RUN_FEED, Provenance.fields(session, "minis-f", listOf(user("write the feed"), assistant("…"), toolResult())).run)
        Provenance.turnEnded("s2")
        assertEquals(Provenance.RUN_PERSON, Provenance.fields(session, "minis-f", listOf(user("thanks"))).run)
        Provenance.arm("s2", "Not A Word")
        assertEquals(Provenance.RUN_PERSON, Provenance.runOf("s2")) // a word the relay would not take is not pinned
        Provenance.arm("s2", Provenance.RUN_HUB)
        Provenance.disarm("s2")
        assertEquals(Provenance.RUN_PERSON, Provenance.runOf("s2"))
    }

    @Test fun `a screen task's calls carry the trajectory, the step and the chat's turn`() {
        val chat = Provenance.Session("s3").also { it.turns = { 4 } }
        Provenance.fields(chat, "minis-h", listOf(user("book a table"))) // the chat's call said turn 4
        val hands = Provenance.Hands("20260427-101500/odd chars", step = 3)
        val f = Provenance.fields(hands + Provenance.Session("s3"), "minis-hands", listOf(user("screen 3")))
        assertEquals("20260427-101500oddchars", f.trajectory)
        assertEquals(3, f.trajectoryStep)
        assertEquals(4, f.turn) // the chat's turn, as it last said it, not the hands' own one message
        assertEquals(2, f.step)
        val h = f.headers()
        assertEquals("20260427-101500oddchars", h[Provenance.H_TRAJECTORY])
        assertEquals("3", h[Provenance.H_TRAJECTORY_STEP])
        assertEquals("4", h[Provenance.H_TURN])
        assertEquals("android", h[Provenance.H_PLATFORM])
        assertEquals("person", h[Provenance.H_RUN])
    }

    @Test fun `the headers and the fields of a turn sent up say the same`() {
        val f = Provenance.Fields(Provenance.RUN_GOAL, 2, 5, "run-1", 9)
        val h = f.headers()
        assertEquals(setOf(Provenance.H_RUN, Provenance.H_TURN, Provenance.H_STEP, Provenance.H_TRAJECTORY, Provenance.H_TRAJECTORY_STEP, Provenance.H_PLATFORM), h.keys)
        val j = f.into(JSONObject())
        assertEquals("goal", j.getString("run"))
        assertEquals(2, j.getInt("turn"))
        assertEquals(5, j.getInt("step"))
        assertEquals("run-1", j.getString("trajectory"))
        assertEquals(9, j.getInt("trajectory_step"))
        assertEquals("android", j.getString("platform"))
        val bare = Provenance.Fields(Provenance.RUN_PERSON, 0, 0).into(JSONObject())
        assertFalse(bare.has("turn"))
        assertFalse(bare.has("trajectory"))
    }

    @Test fun `the events name the turn by the relay's id, or by the own-key turn's id`() {
        val session = Provenance.Session("s4").also { it.turns = { 1 } }
        Provenance.fields(session, "minis-r", listOf(user("hi")))
        Provenance.noteResponse("minis-r", "req_abc.123")
        assertEquals(Provenance.TurnRef("req_abc.123", own = false), Provenance.turnRef("s4"))
        Provenance.noteResponse("minis-r", "not an id!") // left as it was
        assertEquals("req_abc.123", Provenance.turnRef("s4")?.id)
        Provenance.noteResponse("minis-unknown", "req_other") // no session carried that conversation
        assertEquals("req_abc.123", Provenance.turnRef("s4")?.id)
        Provenance.noteOwnTurn("s4", "6f1a")
        assertEquals(Provenance.TurnRef("6f1a", own = true), Provenance.turnRef("s4"))
        assertNull(Provenance.turnRef("nobody"))
    }

    @Test fun `the suspending headers read the coroutine the call goes out in`() = runBlocking {
        val session = Provenance.Session("s5").also { it.turns = { 2 } }
        Provenance.arm("s5", Provenance.RUN_SCHEDULE)
        val h = kotlinx.coroutines.withContext(session) { Provenance.headers("minis-s", listOf(user("run the routine"))) }
        assertEquals("schedule", h[Provenance.H_RUN])
        assertEquals("2", h[Provenance.H_TURN])
        assertEquals("1", h[Provenance.H_STEP])
        assertEquals("s5", Provenance.sessionIn(session))
    }

    @Test fun `old sessions are let go`() {
        for (i in 1..(Provenance.SESSIONS_KEPT + 5)) {
            Provenance.fields(Provenance.Session("s$i").also { it.turns = { 1 } }, "minis-$i", listOf(user("x")))
        }
        assertNull(Provenance.conversationOf("s1"))
        assertTrue(Provenance.conversationOf("s${Provenance.SESSIONS_KEPT + 5}") != null)
    }
}
