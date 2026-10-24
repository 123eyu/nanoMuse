package com.openminis.app.scheduled

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Calendar

/**
 * JVM unit tests for one scheduled task firing at several times a day
 * (GH#557): rows written before the "times" field read back as the single
 * time they always had, multi-time rows round-trip, and nextTriggerMs
 * picks the nearest of the task's times under the existing repeat rules.
 *
 * Instants are built with Calendar in the default zone, the same way
 * nextTriggerMs builds its candidates, so the expectations hold in
 * whatever zone the suite runs in. October 2026 dates: the 21st is a
 * Wednesday, the 23rd a Friday, the 26th a Monday, the 28th a Wednesday.
 */
class ScheduledTaskTimesTest {

    private fun at(dayOfMonth: Int, hour: Int, minute: Int): Long =
        Calendar.getInstance().apply {
            set(2026, Calendar.OCTOBER, dayOfMonth, hour, minute, 0)
            set(Calendar.MILLISECOND, 0)
        }.timeInMillis

    private fun task(
        repeatMode: ScheduledRepeatMode = ScheduledRepeatMode.DAILY,
        customDays: Set<Int> = emptySet(),
        primary: Pair<Int, Int> = 8 to 0,
        additional: List<Pair<Int, Int>> = emptyList(),
    ) = ScheduledTask(
        id = "task-1",
        label = "Water the plants",
        timeOfDayHour = primary.first,
        timeOfDayMinute = primary.second,
        repeatMode = repeatMode,
        customDays = customDays,
        prompt = "Remind me",
        createdAt = at(1, 0, 0),
        additionalTimes = additional,
    )

    // ─── stored rows ─────────────────────────────────────────────────────

    @Test
    fun `a row written before times reads back as its single time`() {
        val old = JSONObject(
            """{"id":"t1","label":"x","hour":9,"minute":30,"repeatMode":"DAILY",""" +
                """"customDays":"","prompt":"p","enabled":true,"createdAt":1}""",
        )
        val t = ScheduledTask.fromJson(old)
        assertEquals(listOf(9 to 30), t.timesOfDay)
        assertTrue(t.additionalTimes.isEmpty())
        // And it writes back without inventing a "times" array.
        assertFalse(t.toJson().has("times"))
    }

    @Test
    fun `a multi-time row round-trips with hour and minute as the first time`() {
        val t = task(primary = 8 to 0, additional = listOf(20 to 0, 14 to 0))
        val json = t.toJson()
        assertEquals(8, json.getInt("hour"))
        assertEquals(0, json.getInt("minute"))
        val arr = json.getJSONArray("times")
        assertEquals(
            listOf("08:00", "14:00", "20:00"),
            (0 until arr.length()).map { arr.getString(it) },
        )
        val back = ScheduledTask.fromJson(json)
        assertEquals(listOf(8 to 0, 14 to 0, 20 to 0), back.timesOfDay)
        assertEquals(listOf(14 to 0, 20 to 0), back.additionalTimes)
    }

    @Test
    fun `times label is the one time, or all of them joined`() {
        assertEquals("08:00", task().timesLabel())
        assertEquals(
            "08:00, 14:00, 20:00",
            task(additional = listOf(20 to 0, 14 to 0)).timesLabel(),
        )
    }

    @Test
    fun `a run records the time it was scheduled for, and old runs have none`() {
        val run = ScheduledRun(
            firedAt = at(21, 14, 0),
            sessionId = "s1",
            preview = "done",
            ok = true,
            scheduledForMs = at(21, 14, 0),
        )
        val back = ScheduledRun.fromJson(run.toJson())
        assertEquals(at(21, 14, 0), back.scheduledForMs)
        val old = ScheduledRun.fromJson(JSONObject("""{"firedAt":5,"ok":false}"""))
        assertNull(old.scheduledForMs)
        assertFalse(old.ok)
    }

    // ─── nextTriggerMs over several times ────────────────────────────────

    @Test
    fun `next trigger is the later time today`() {
        val t = task(additional = listOf(14 to 0, 20 to 0))
        assertEquals(at(21, 14, 0), t.nextTriggerMs(at(21, 9, 0)))
    }

    @Test
    fun `after a firing the next trigger is the following time today`() {
        // The receiver re-arms a moment after the 14:00 firing.
        val t = task(additional = listOf(14 to 0, 20 to 0))
        assertEquals(at(21, 20, 0), t.nextTriggerMs(at(21, 14, 0) + 1_000))
    }

    @Test
    fun `next trigger wraps to the earliest time tomorrow`() {
        val t = task(additional = listOf(14 to 0, 20 to 0))
        assertEquals(at(22, 8, 0), t.nextTriggerMs(at(21, 21, 0)))
    }

    @Test
    fun `custom days take the later time today, then next week's earliest`() {
        val t = task(
            repeatMode = ScheduledRepeatMode.CUSTOM,
            customDays = setOf(Calendar.WEDNESDAY),
            additional = listOf(20 to 0),
        )
        assertEquals(at(21, 20, 0), t.nextTriggerMs(at(21, 15, 0)))
        assertEquals(at(28, 8, 0), t.nextTriggerMs(at(21, 21, 0)))
    }

    @Test
    fun `weekdays skip the weekend to Monday's earliest time`() {
        val t = task(
            repeatMode = ScheduledRepeatMode.WEEKDAYS,
            additional = listOf(20 to 0),
        )
        assertEquals(at(26, 8, 0), t.nextTriggerMs(at(23, 21, 0)))
    }

    @Test
    fun `once fires at the nearest of its times`() {
        val t = task(
            repeatMode = ScheduledRepeatMode.ONCE,
            additional = listOf(14 to 0),
        )
        assertEquals(at(21, 14, 0), t.nextTriggerMs(at(21, 9, 0)))
    }

    @Test
    fun `a single time behaves exactly as before`() {
        val t = task(primary = 9 to 0)
        assertEquals(at(21, 9, 0), t.nextTriggerMs(at(21, 9, 0)))
        assertEquals(at(22, 9, 0), t.nextTriggerMs(at(21, 9, 30)))
    }
}
