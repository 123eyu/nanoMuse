package io.github.nanomuse.chat

import com.openminis.app.data.ContextPolicy

/**
 * Automatic compaction is an attempt to keep requests quick, not a prerequisite for every
 * request above the 32k target. A failed attempt may continue while the model has room.
 * The uncapped policy's original reserve remains the stopping boundary.
 *
 * One instance belongs to an agent run. Once maintenance fails or cannot reach its target,
 * defer further attempts until the hard boundary, so each tool result does not start another
 * summary request. Every attempt, successful or not, consumes the same bounded budget.
 */
class ContextMaintenance(private val maxCompactions: Int = 3) {
    enum class Action { PROCEED, COMPACTED, STOP }

    var attempts: Int = 0
        private set

    var softDeferred: Boolean = false
        private set

    init {
        require(maxCompactions > 0)
    }

    /** Reset when the owner changes the active model or starts a new run. */
    fun reset() {
        attempts = 0
        softDeferred = false
    }

    /**
     * [measure] reads the current outgoing request, including any offloads or new summary.
     * [compact] returns whether a summary was installed. Cancellation deliberately propagates.
     * A successful summary returns [Action.COMPACTED] even when it did not shrink a request
     * below the soft target, so the caller can move its streaming bubble past the divider.
     */
    suspend fun check(
        contextWindow: Int,
        measure: () -> Int,
        compact: suspend (tokens: Int, attempt: Int) -> Boolean,
    ): Action {
        if (contextWindow <= 0) return Action.PROCEED
        val tokens = measure()
        if (tokens <= 0) return Action.PROCEED
        val policy = ContextPolicy.forContextWindow(contextWindow)
        when (policy.check(tokens, contextWindow)) {
            ContextPolicy.CheckResult.OK -> return Action.PROCEED
            ContextPolicy.CheckResult.EXHAUSTED -> return Action.STOP
            ContextPolicy.CheckResult.NEEDS_COMPACT -> Unit
        }

        val hardLimit = hardThreshold(contextWindow)
        val atHardLimit = tokens >= hardLimit
        if (!atHardLimit && softDeferred) return Action.PROCEED
        if (attempts >= maxCompactions) {
            softDeferred = true
            return if (atHardLimit) Action.STOP else Action.PROCEED
        }

        attempts += 1
        val compacted = compact(tokens, attempts)
        val afterTokens = measure()
        if (!compacted || afterTokens >= tokens) {
            softDeferred = true
            if (afterTokens >= hardLimit) return Action.STOP
            return if (compacted) Action.COMPACTED else Action.PROCEED
        }

        // A partial reduction should not immediately launch another slow summary when the
        // request still fits. At the hard boundary a further bounded attempt may be needed.
        softDeferred = afterTokens >= policy.compactThreshold
        return Action.COMPACTED
    }

    companion object {
        /** Original capacity reserve before the phone's latency-oriented 32k cap. */
        fun hardThreshold(contextWindow: Int): Int = when {
            contextWindow <= 0 -> 0
            contextWindow < 32_000 -> (contextWindow.toLong() * 9 / 10).toInt()
            contextWindow < 128_000 -> contextWindow - 10_000
            else -> contextWindow - 20_000
        }
    }
}
