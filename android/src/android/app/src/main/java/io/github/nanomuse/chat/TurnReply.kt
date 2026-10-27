package io.github.nanomuse.chat

/**
 * Which assistant message a finished turn produced, read off the session's rows in order.
 *
 * The flows that act on a reply (goal blocks, feed drafts, the first conversation) used to
 * take the last assistant row of the session. A turn that fails before its own assistant
 * message exists leaves the previous reply as that row, with the terminal error pinned on it,
 * and a scheduled feed run that read it wrote yesterday's posts again under today's date
 * (#637). So the reply of a turn is the last assistant row only when it follows the last user
 * row and carries no error; otherwise the turn produced nothing to act on.
 */
object TurnReply {
    class Row(val role: String, val content: String, val error: String?)

    fun ofThisTurn(rows: List<Row>): String? {
        val lastUser = rows.indexOfLast { it.role == "user" }
        val lastAssistant = rows.indexOfLast { it.role == "assistant" }
        if (lastAssistant < 0 || lastAssistant < lastUser) return null
        val row = rows[lastAssistant]
        return if (row.error == null) row.content else null
    }
}
