package io.github.nanomuse.chat

/**
 * Keeps cache-stable system instructions ahead of every session-dependent fragment.
 *
 * Providers cache a byte prefix, so callers must put memory, runtime data, model metadata,
 * and other mutable addenda in [volatileSections].
 */
object PromptCacheLayout {
    class VolatileSuffix internal constructor(internal val text: String)

    data class Parts(
        val stablePrefix: String,
        val volatileSuffix: String,
    ) {
        val prompt: String
            get() = stablePrefix + volatileSuffix
    }

    fun build(
        stableSections: List<String>,
        volatileSections: List<String?>,
        runtimeContext: String?,
    ): Parts = buildWithSuffix(
        stableSections = stableSections,
        leadingVolatileSections = emptyList(),
        suffix = makeVolatileSuffix(volatileSections, runtimeContext),
    )

    /**
     * Builds the shared mutable suffix. Runtime context is a separate argument so it cannot
     * accidentally drift ahead of another section.
     */
    fun makeVolatileSuffix(
        volatileSections: List<String?>,
        runtimeContext: String?,
    ): VolatileSuffix = VolatileSuffix(join((volatileSections + runtimeContext).filterNotNull()))

    /**
     * Prepends model-specific mutable sections without unpacking or duplicating a shared suffix.
     */
    fun buildWithSuffix(
        stableSections: List<String>,
        leadingVolatileSections: List<String?>,
        suffix: VolatileSuffix,
    ): Parts {
        val stablePrefix = join(stableSections)
        val volatileBody = join(leadingVolatileSections.filterNotNull() + suffix.text)
        return Parts(
            stablePrefix = stablePrefix,
            volatileSuffix = if (volatileBody.isEmpty()) "" else "\n\n$volatileBody",
        )
    }

    private fun join(sections: List<String>): String =
        sections
            .map(String::trim)
            .filter(String::isNotEmpty)
            .joinToString("\n\n")
}
