package io.github.nanomuse.feed

/** Keeps the feed's daily write limit and user-facing capacity calculation in one place. */
internal object FeedWriteCapacity {
    data class Plan(
        val acceptedCount: Int,
        val droppedByLimit: Int,
        val remainingCapacity: Int,
    )

    fun plan(existingCount: Int, requestedCount: Int, dailyLimit: Int): Plan {
        val remaining = (dailyLimit.coerceAtLeast(0) - existingCount.coerceAtLeast(0)).coerceAtLeast(0)
        val requested = requestedCount.coerceAtLeast(0)
        val accepted = minOf(remaining, requested)
        return Plan(
            acceptedCount = accepted,
            droppedByLimit = requested - accepted,
            remainingCapacity = remaining - accepted,
        )
    }
}
