package io.github.nanomuse.feed

import org.junit.Assert.assertEquals
import org.junit.Test

class FeedWriteCapacityTest {
    @Test
    fun `caps accepted drafts at remaining daily slots`() {
        assertEquals(
            // 10 of 12 written already: 2 of the 4 fit, 2 are dropped, and nothing is left for today
            FeedWriteCapacity.Plan(acceptedCount = 2, droppedByLimit = 2, remainingCapacity = 0),
            FeedWriteCapacity.plan(existingCount = 10, requestedCount = 4, dailyLimit = 12),
        )
    }

    @Test
    fun `drops all requested drafts when the daily cap is already full`() {
        assertEquals(
            FeedWriteCapacity.Plan(acceptedCount = 0, droppedByLimit = 3, remainingCapacity = 0),
            FeedWriteCapacity.plan(existingCount = 12, requestedCount = 3, dailyLimit = 12),
        )
    }

    @Test
    fun `accepts all drafts when they fit`() {
        assertEquals(
            // 4 written, 2 more fit, and 6 slots are left once they are written
            FeedWriteCapacity.Plan(acceptedCount = 2, droppedByLimit = 0, remainingCapacity = 6),
            FeedWriteCapacity.plan(existingCount = 4, requestedCount = 2, dailyLimit = 12),
        )
    }

    @Test
    fun `treats negative counts as zero`() {
        assertEquals(
            FeedWriteCapacity.Plan(acceptedCount = 0, droppedByLimit = 0, remainingCapacity = 0),
            FeedWriteCapacity.plan(existingCount = -2, requestedCount = -1, dailyLimit = 0),
        )
    }
}
