package io.github.nanomuse.feed

import org.junit.Assert.assertEquals
import org.junit.Test
import java.io.File

/**
 * Body level duplicate checks for the feed (#637, #574). A reply replayed from an
 * earlier day used to land again because afterTurn only compared titles posted today;
 * a normalized body match against the kept posts stops that replay, and a duplicate
 * inside one batch is kept only once.
 */
class FeedDedupeTest {
    private fun draft(title: String, body: String) =
        FeedStore.Draft(title = title, type = "note", emoji = "", body = body, source = emptyList())

    private fun post(body: String) = FeedPost(
        day = "2026-10-25", index = 1, title = "old post", type = "note", emoji = "",
        body = body, source = emptyList(), createdAt = 0L, liked = false, file = File("01.md"),
    )

    @Test fun `whitespace differences do not hide a replayed body`() {
        assertEquals("hello world", FeedFlow.normalizeBody("  hello\n\n  world  "))
    }

    @Test fun `a body already posted is dropped`() {
        val kept = FeedFlow.dropDuplicateBodies(
            listOf(draft("new title", "hello world"), draft("other", "fresh body")),
            listOf(post("hello world")),
        )
        assertEquals(listOf("other"), kept.map { it.title })
    }

    @Test fun `a duplicate inside one batch is kept only once`() {
        val kept = FeedFlow.dropDuplicateBodies(
            listOf(draft("first", "same body"), draft("second", "same\nbody"), draft("third", "different")),
            emptyList(),
        )
        assertEquals(listOf("first", "third"), kept.map { it.title })
    }

    @Test fun `a body that only shares a title is kept`() {
        val kept = FeedFlow.dropDuplicateBodies(
            listOf(draft("old post", "a completely different body")),
            listOf(post("hello world")),
        )
        assertEquals(1, kept.size)
    }
}
