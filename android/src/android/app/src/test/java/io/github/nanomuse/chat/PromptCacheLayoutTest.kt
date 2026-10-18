package io.github.nanomuse.chat

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class PromptCacheLayoutTest {
    @Test
    fun `static instructions precede memory and runtime context`() {
        val parts = PromptCacheLayout.build(
            stableSections = listOf("BASE INSTRUCTIONS", "RISK POLICY", "HANDS POLICY"),
            volatileSections = listOf("SESSION ADDENDUM", "GLOBAL.md: remembered"),
            runtimeContext = "Runtime context: today",
        )

        assertTrue(parts.prompt.indexOf("RISK POLICY") < parts.prompt.indexOf("GLOBAL.md"))
        assertTrue(parts.prompt.indexOf("HANDS POLICY") < parts.prompt.indexOf("Runtime context"))
        assertTrue(parts.prompt.endsWith("Runtime context: today"))
    }

    @Test
    fun `changing only volatile content preserves the byte stable prefix`() {
        val first = PromptCacheLayout.build(
            stableSections = listOf("BASE INSTRUCTIONS", "RISK POLICY", "HANDS POLICY"),
            volatileSections = listOf("Recent memories: first"),
            runtimeContext = "Runtime context: day one",
        )
        val second = PromptCacheLayout.build(
            stableSections = listOf("BASE INSTRUCTIONS", "RISK POLICY", "HANDS POLICY"),
            volatileSections = listOf("Recent memories: second"),
            runtimeContext = "Runtime context: day two",
        )

        assertEquals(first.stablePrefix, second.stablePrefix)
        assertTrue(first.volatileSuffix != second.volatileSuffix)
        assertEquals(first.stablePrefix, first.prompt.removeSuffix(first.volatileSuffix))
        assertEquals(second.stablePrefix, second.prompt.removeSuffix(second.volatileSuffix))
    }

    @Test
    fun `fallback prepends model sections without losing or duplicating shared suffix`() {
        val suffix = PromptCacheLayout.makeVolatileSuffix(
            volatileSections = listOf("SKILLS", "GLOBAL.md", "USER.md"),
            runtimeContext = "Runtime context",
        )
        val parts = PromptCacheLayout.buildWithSuffix(
            stableSections = listOf("BASE"),
            leadingVolatileSections = listOf("MODEL CAPABILITIES", "MODEL BEHAVIOR"),
            suffix = suffix,
        )

        assertEquals(
            "BASE\n\nMODEL CAPABILITIES\n\nMODEL BEHAVIOR\n\nSKILLS\n\nGLOBAL.md\n\nUSER.md\n\nRuntime context",
            parts.prompt,
        )
        assertEquals(1, "Runtime context".toRegex().findAll(parts.prompt).count())
        assertTrue(parts.prompt.endsWith("Runtime context"))
    }
}
