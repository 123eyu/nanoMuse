package com.openminis.app.sandbox.offload

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * GH#557: a repeated option (minis-scheduled's --time) keeps every value
 * for [OffloadArgs.getAll], in argv order, across both the spaced and the
 * "=" forms. [OffloadArgs.get] is unchanged: it returns the last value.
 */
class OffloadArgsRepeatedValueTest {

    @Test
    fun `repeated --time keeps every value in order`() {
        val args = OffloadArgs(
            listOf(
                "create", "--time", "08:00", "--time", "14:00",
                "--time=20:00", "--prompt", "stretch",
            ),
        )
        assertEquals(listOf("08:00", "14:00", "20:00"), args.getAll("time", "t"))
    }

    @Test
    fun `get still returns the last value`() {
        val args = OffloadArgs(listOf("--time", "08:00", "--time", "14:00"))
        assertEquals("14:00", args.get("time", "t"))
    }

    @Test
    fun `getAll is empty when the option is absent`() {
        val args = OffloadArgs(listOf("list"))
        assertEquals(emptyList<String>(), args.getAll("time", "t"))
        assertNull(args.get("time", "t"))
    }
}
