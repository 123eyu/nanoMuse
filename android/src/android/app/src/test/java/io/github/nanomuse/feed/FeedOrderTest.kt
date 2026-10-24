package io.github.nanomuse.feed

import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.Locale

/**
 * One day's posts on the Feed page: the newest batch first, each batch in the model's order
 * (#555). Before, the day was sorted by file number, so the morning's batch stayed on top and
 * the evening's, the newest, sank to the bottom of the day.
 */
class FeedOrderTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun post(index: Int, createdAt: Long, dir: File): FeedPost = FeedPost(
        day = dir.name, index = index, title = "post $index", type = "note", emoji = "",
        body = "body $index", source = emptyList(), createdAt = createdAt, liked = false,
        file = File(dir, "%02d.md".format(Locale.ROOT, index)),
    )

    @Test fun `the newest batch comes first and keeps the model's order inside`() {
        val dir = tmp.newFolder("2026-10-23")
        val morning = 1_761_177_600_000L // 2026-10-23 08:00 (any fixed instant works)
        val evening = morning + 12 * 3_600_000L
        val posts = (1..5).map { post(it, morning, dir) } + (6..8).map { post(it, evening, dir) }

        val ordered = posts.shuffled().sortedWith(FeedStore.DAY_ORDER).map { it.index }

        assertEquals(listOf(6, 7, 8, 1, 2, 3, 4, 5), ordered)
    }

    @Test fun `posts read back from disk order the same way`() {
        val dir = tmp.newFolder("2026-10-23")
        val morning = 1_761_177_600_000L
        val evening = morning + 12 * 3_600_000L
        val batches = (1..5).map { post(it, morning, dir) } + (6..8).map { post(it, evening, dir) }
        batches.forEach { it.file.writeText(it.serialize()) }

        val readBack = dir.listFiles()!!.mapNotNull(FeedPost::parse).sortedWith(FeedStore.DAY_ORDER)

        assertEquals(listOf(6, 7, 8, 1, 2, 3, 4, 5), readBack.map { it.index })
        assertEquals("a batch shares its created time", 1, readBack.take(3).map { it.createdAt }.toSet().size)
    }

    @Test fun `a single batch stays in the model's order`() {
        val dir = tmp.newFolder("2026-10-24")
        val now = 1_761_264_000_000L
        val posts = (1..4).map { post(it, now, dir) }

        assertEquals(listOf(1, 2, 3, 4), posts.reversed().sortedWith(FeedStore.DAY_ORDER).map { it.index })
    }
}
