package io.github.nanomuse.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The reply a turn produced is the last assistant row only when it follows the turn's user
 * row and carries no error (#637): a scheduled feed run whose model call failed used to be
 * handed yesterday's reply and wrote yesterday's posts again under today's date.
 */
class TurnReplyTest {
    private fun user(text: String) = TurnReply.Row("user", text, null)
    private fun assistant(text: String, error: String? = null) = TurnReply.Row("assistant", text, error)

    @Test fun `a finished turn's own reply is handed on`() {
        val rows = listOf(user("yesterday"), assistant("```feed …```"), user("today"), assistant("today's reply"))
        assertEquals("today's reply", TurnReply.ofThisTurn(rows))
    }

    @Test fun `a turn that failed before its own reply existed produces nothing`() {
        // the error is pinned on the last assistant row, which is yesterday's reply
        val rows = listOf(user("yesterday"), assistant("```feed …```", error = "connection closed"), user("today"))
        assertNull(TurnReply.ofThisTurn(rows))
        // and even unpinned, a reply before the turn's own user row is not this turn's
        assertNull(TurnReply.ofThisTurn(listOf(user("yesterday"), assistant("```feed …```"), user("today"))))
    }

    @Test fun `a reply that ended in an error is not acted on`() {
        val rows = listOf(user("today"), assistant("half a ```feed", error = "Provider error"))
        assertNull(TurnReply.ofThisTurn(rows))
    }

    @Test fun `rows after the reply do not hide it, and no rows at all is nothing`() {
        val rows = listOf(user("today"), assistant("reply"), TurnReply.Row("system", "", null))
        assertEquals("reply", TurnReply.ofThisTurn(rows))
        assertNull(TurnReply.ofThisTurn(emptyList()))
        assertEquals("intro", TurnReply.ofThisTurn(listOf(assistant("intro"))))
    }
}
