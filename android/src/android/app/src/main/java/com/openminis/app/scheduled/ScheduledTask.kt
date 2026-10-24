package com.openminis.app.scheduled

import org.json.JSONArray
import org.json.JSONObject
import java.util.Calendar
import java.util.UUID

/**
 * [T-android-scheduled-tasks-run-records] One recorded execution of a
 * scheduled task. The run-records screen lists these newest-first; a run with
 * a non-null [sessionId] is tappable → opens that chat.
 */
data class ScheduledRun(
    val firedAt: Long,
    val sessionId: String?,
    val preview: String?,
    val ok: Boolean,
    // nanoMuse: [GH#557] the trigger instant this firing was armed for, so
    // the run history can show which of a task's times a run belongs to.
    // Null for runs started by hand (Run now, the CLI's run) and for rows
    // written before the field existed. Additive in the JSON, like the
    // optional fields above.
    val scheduledForMs: Long? = null,
) {
    fun toJson(): JSONObject = JSONObject().apply {
        put("firedAt", firedAt)
        if (sessionId != null) put("sessionId", sessionId)
        if (preview != null) put("preview", preview)
        put("ok", ok)
        // nanoMuse: [GH#557]
        if (scheduledForMs != null) put("scheduledForMs", scheduledForMs)
    }

    companion object {
        fun fromJson(o: JSONObject): ScheduledRun = ScheduledRun(
            firedAt = o.optLong("firedAt"),
            sessionId = if (o.has("sessionId")) o.optString("sessionId", null) else null,
            preview = if (o.has("preview")) o.optString("preview", null) else null,
            ok = o.optBoolean("ok", true),
            // nanoMuse: [GH#557]
            scheduledForMs = if (o.has("scheduledForMs")) o.optLong("scheduledForMs") else null,
        )
    }
}

/**
 * [T-android-scheduled-tasks-design / T-android-scheduled-tasks-full]
 * User-defined scheduled task that fires an AI action at a configured time.
 * Counterpart to the iOS Shortcuts surface (SendPrompt / FollowUpSession /
 * RetryRun App Intents) — on Android we own the scheduling layer ourselves
 * via AlarmManager, and the per-task [targetMode] mirrors the iOS intent set:
 *
 *   NEW_SESSION      ≈ SendPromptIntent with no session  → run prompt in a
 *                      fresh chat.
 *   APPEND_TO        ≈ FollowUpSessionIntent             → enqueue prompt into
 *                      an existing chat (follow-up).
 *   RERUN            ≈ RetryRunIntent                     → re-run an existing
 *                      chat from a chosen user message.
 *
 * Persistence: serialized to JSON via [toJson] / [fromJson] and stored in
 * [ScheduledTaskStore] (SharedPreferences). Schema lives here so the store
 * stays a thin layer over the JSON array.
 */
enum class ScheduledRepeatMode { ONCE, DAILY, WEEKDAYS, CUSTOM }

/**
 * What the task does when it fires. Mirrors the iOS App Intent set.
 *  - [NewSession]: run [ScheduledTask.prompt] in a brand-new chat.
 *  - [AppendToSession]: append the prompt to an existing chat (follow-up).
 *  - [RerunMessage]: re-run an existing chat from a specific user message
 *    (the prompt is ignored — the message itself is replayed).
 */
sealed class ScheduledTargetMode {
    object NewSession : ScheduledTargetMode()
    data class AppendToSession(val sessionId: String) : ScheduledTargetMode()
    data class RerunMessage(val sessionId: String, val messageId: String) : ScheduledTargetMode()

    fun encode(): String = when (this) {
        NewSession -> "NEW_SESSION"
        is AppendToSession -> "APPEND_TO:$sessionId"
        is RerunMessage -> "RERUN:$sessionId:$messageId"
    }

    /** Session this task targets, or null for NEW_SESSION. */
    val sessionIdOrNull: String?
        get() = when (this) {
            NewSession -> null
            is AppendToSession -> sessionId
            is RerunMessage -> sessionId
        }

    companion object {
        fun decode(raw: String?): ScheduledTargetMode {
            if (raw.isNullOrEmpty() || raw == "NEW_SESSION") return NewSession
            if (raw.startsWith("APPEND_TO:")) {
                return AppendToSession(raw.removePrefix("APPEND_TO:"))
            }
            if (raw.startsWith("RERUN:")) {
                // RERUN:<sessionId>:<messageId> — split on the FIRST colon only,
                // since ids are UUIDs (no embedded colons) but be defensive.
                val rest = raw.removePrefix("RERUN:")
                val idx = rest.indexOf(':')
                if (idx > 0) {
                    return RerunMessage(rest.substring(0, idx), rest.substring(idx + 1))
                }
            }
            return NewSession
        }
    }
}

data class ScheduledTask(
    val id: String = UUID.randomUUID().toString(),
    val label: String,
    val timeOfDayHour: Int,                  // 0-23
    val timeOfDayMinute: Int,                // 0-59
    val repeatMode: ScheduledRepeatMode,
    val customDays: Set<Int> = emptySet(),   // Calendar.DAY_OF_WEEK values when repeatMode=CUSTOM
    val prompt: String,
    val targetMode: ScheduledTargetMode = ScheduledTargetMode.NewSession,
    val modelId: String? = null,             // null → use app default (or fall back from modelBinding's resolved entry)
    // [T-android-scheduled-task-model-binding] Mirrors ChatSessionEntity.modelBinding.
    // JSON: `{"type":"group","groupId":"..."}` or `{"type":"entry","entryId":"..."}`
    // null → "use app default" (ScheduledAgentRunner resolves to defaultPrimaryGroupId).
    // When non-null, ScheduledAgentRunner writes it onto the new session row so the
    // chat picks up the same group/entry the user chose for the task.
    val modelBinding: String? = null,
    val enabled: Boolean = true,
    val createdAt: Long = System.currentTimeMillis(),
    // [T-android-scheduled-tasks-full] Optional active window. The task only
    // fires on/after startDateMs and on/before endDateMs (both = start-of-day
    // local epoch ms, inclusive). null = unbounded on that side. A ONCE task
    // with a startDate fires on the first matching day at/after the start;
    // repeating tasks skip days outside the window and auto-disable once the
    // end date has fully passed.
    val startDateMs: Long? = null,
    val endDateMs: Long? = null,
    val lastFiredAt: Long? = null,
    val lastResultPreview: String? = null,
    val lastResultSessionId: String? = null,
    // [T-android-scheduled-tasks-run-records] Newest-first execution log. The
    // list screen no longer shows a result preview; instead the long-press
    // "Run records" menu opens a screen backed by this. Capped at
    // MAX_RUN_HISTORY by the manager when appending.
    val runHistory: List<ScheduledRun> = emptyList(),
    // nanoMuse: a goal's periodic check is one of these, owned by the Goals
    // tab — hidden from the routines list, tagged with its goal, and fired
    // every [intervalMinutes] (first run a minute after creation) instead of
    // at a time of day. All three are additive in the JSON; old rows read
    // back unchanged.
    val hidden: Boolean = false,
    val goalId: String? = null,
    val intervalMinutes: Int? = null,
    // nanoMuse: [GH#557] further times of day the task fires at, on top of
    // the primary [timeOfDayHour] / [timeOfDayMinute]. Appended last with a
    // default so every existing construction compiles unchanged. Additive
    // in the JSON as a "times" array of "HH:MM" strings (see toJson); rows
    // written before the field existed read back with none.
    val additionalTimes: List<Pair<Int, Int>> = emptyList(),
) {

    /**
     * nanoMuse: [GH#557] Every time of day this task fires at, earliest
     * first: the primary time plus [additionalTimes], duplicates removed.
     * A task with no additional times has exactly one entry, as before.
     */
    val timesOfDay: List<Pair<Int, Int>>
        get() = (listOf(timeOfDayHour to timeOfDayMinute) + additionalTimes)
            .distinct()
            .sortedWith(compareBy<Pair<Int, Int>> { it.first }.thenBy { it.second })

    /** nanoMuse: [GH#557] "08:00" for one time, "08:00, 14:00, 20:00" for several. */
    fun timesLabel(): String =
        timesOfDay.joinToString(", ") { (h, m) -> "%02d:%02d".format(h, m) }

    /**
     * Wall-clock ms of the next firing time for this task, taking
     * [repeatMode] / [customDays] / [startDateMs] / [endDateMs] / current time
     * into account. Returns `null` when [enabled] is false, the task has no
     * valid next slot (CUSTOM with an empty day set), or the next slot would
     * fall after [endDateMs].
     *
     * Uses [Calendar.getInstance] so the local time zone is respected — users
     * schedule "09:00 every day" in their wall-clock sense, not UTC.
     */
    fun nextTriggerMs(now: Long = System.currentTimeMillis()): Long? {
        if (!enabled) return null

        // nanoMuse: interval tasks ignore the time of day. The receiver re-arms
        // BEFORE the run marks lastFiredAt, so "first run soon" is keyed on the
        // task's age, not on lastFiredAt.
        intervalMinutes?.takeIf { it > 0 }?.let { minutes ->
            val firstRun = lastFiredAt == null && now - createdAt < 30_000L
            return if (firstRun) now + 60_000L else now + minutes * 60_000L
        }

        // Earliest instant we may fire: max(now, start-of-startDate). This lets
        // a task created today with a future startDate wait until that day.
        val floor = maxOf(now, startDateMs ?: Long.MIN_VALUE)

        // nanoMuse: [GH#557] the next firing is the nearest of the task's
        // times. Scan forward from the floor's day: a day counts when it
        // matches the repeat rule, and the first of the day's times at or
        // after the floor wins. With one time this walks exactly as the old
        // single-candidate code did, including the week-long reach for a
        // CUSTOM day that only matches today. Interval tasks returned
        // above and never reach here.
        if (repeatMode == ScheduledRepeatMode.CUSTOM && customDays.isEmpty()) return null
        val day = Calendar.getInstance().apply {
            timeInMillis = floor
            set(Calendar.HOUR_OF_DAY, 0)
            set(Calendar.MINUTE, 0)
            set(Calendar.SECOND, 0)
            set(Calendar.MILLISECOND, 0)
        }
        var candidate: Long? = null
        for (offset in 0..8) {
            if (offset > 0) day.add(Calendar.DAY_OF_YEAR, 1)
            val dow = day.get(Calendar.DAY_OF_WEEK)
            val dayMatches = when (repeatMode) {
                ScheduledRepeatMode.ONCE, ScheduledRepeatMode.DAILY -> true
                ScheduledRepeatMode.WEEKDAYS ->
                    dow != Calendar.SATURDAY && dow != Calendar.SUNDAY
                ScheduledRepeatMode.CUSTOM -> dow in customDays
            }
            if (!dayMatches) continue
            for ((h, m) in timesOfDay) {
                val at = (day.clone() as Calendar).apply {
                    set(Calendar.HOUR_OF_DAY, h)
                    set(Calendar.MINUTE, m)
                    set(Calendar.SECOND, 0)
                    set(Calendar.MILLISECOND, 0)
                }.timeInMillis
                if (at >= floor) {
                    candidate = at
                    break
                }
            }
            if (candidate != null) break
        }
        if (candidate == null) return null

        // Respect the end-of-window: endDateMs is the start-of-day of the last
        // allowed day, so the task may fire any time up to end-of-that-day.
        val end = endDateMs
        if (end != null) {
            val endOfDay = Calendar.getInstance().apply {
                timeInMillis = end
                set(Calendar.HOUR_OF_DAY, 23)
                set(Calendar.MINUTE, 59)
                set(Calendar.SECOND, 59)
                set(Calendar.MILLISECOND, 999)
            }.timeInMillis
            if (candidate > endOfDay) return null
        }
        return candidate
    }

    fun toJson(): JSONObject = JSONObject().apply {
        put("id", id)
        put("label", label)
        put("hour", timeOfDayHour)
        put("minute", timeOfDayMinute)
        put("repeatMode", repeatMode.name)
        put("customDays", customDays.joinToString(","))
        put("prompt", prompt)
        put("targetMode", targetMode.encode())
        if (modelId != null) put("modelId", modelId)
        if (modelBinding != null) put("modelBinding", modelBinding)
        put("enabled", enabled)
        put("createdAt", createdAt)
        if (startDateMs != null) put("startDateMs", startDateMs)
        if (endDateMs != null) put("endDateMs", endDateMs)
        if (lastFiredAt != null) put("lastFiredAt", lastFiredAt)
        if (lastResultPreview != null) put("lastResultPreview", lastResultPreview)
        if (lastResultSessionId != null) put("lastResultSessionId", lastResultSessionId)
        if (runHistory.isNotEmpty()) {
            put("runHistory", JSONArray().apply { runHistory.forEach { put(it.toJson()) } })
        }
        // nanoMuse
        if (hidden) put("hidden", true)
        if (goalId != null) put("goalId", goalId)
        if (intervalMinutes != null) put("intervalMinutes", intervalMinutes)
        // nanoMuse: [GH#557] every time of day, "HH:MM", earliest first.
        // "hour"/"minute" above stay the first time, so a reader that
        // predates this field still fires the task, once a day at it.
        if (additionalTimes.isNotEmpty()) {
            put("times", JSONArray().apply {
                timesOfDay.forEach { (h, m) -> put("%02d:%02d".format(h, m)) }
            })
        }
    }

    companion object {
        /** Max recorded executions kept per task. */
        const val MAX_RUN_HISTORY = 50

        /** nanoMuse: [GH#557] "HH:MM" → (hour, minute); anything else → null. */
        fun parseTimeOfDay(raw: String): Pair<Int, Int>? {
            val parts = raw.trim().split(":")
            if (parts.size != 2) return null
            val h = parts[0].toIntOrNull()?.takeIf { it in 0..23 } ?: return null
            val m = parts[1].toIntOrNull()?.takeIf { it in 0..59 } ?: return null
            return h to m
        }

        /**
         * nanoMuse: [GH#557] The times beyond the first, from the "times"
         * array a multi-time row writes. Absent (rows from before the
         * field) → none. The "hour"/"minute" fields stay the first time;
         * entries in the array that repeat it, do not parse or fall out of
         * range are dropped, as customDays are below.
         */
        private fun additionalTimesFromJson(
            o: JSONObject,
            hour: Int,
            minute: Int,
        ): List<Pair<Int, Int>> {
            val arr = o.optJSONArray("times") ?: return emptyList()
            return buildList {
                for (i in 0 until arr.length()) {
                    parseTimeOfDay(arr.optString(i, ""))?.let { add(it) }
                }
            }.distinct()
                .filterNot { it.first == hour && it.second == minute }
                .sortedWith(compareBy<Pair<Int, Int>> { it.first }.thenBy { it.second })
        }

        fun fromJson(o: JSONObject): ScheduledTask {
            // nanoMuse: [GH#557] read the primary time first; the "times"
            // array is resolved against it by additionalTimesFromJson.
            val hour = o.optInt("hour", 9)
            val minute = o.optInt("minute", 0)
            return ScheduledTask(
                id = o.getString("id"),
                label = o.optString("label", ""),
                timeOfDayHour = hour,
                timeOfDayMinute = minute,
                repeatMode = runCatching {
                    ScheduledRepeatMode.valueOf(o.optString("repeatMode", "ONCE"))
                }.getOrDefault(ScheduledRepeatMode.ONCE),
                customDays = o.optString("customDays", "")
                    .split(',').mapNotNull { it.trim().toIntOrNull() }.toSet(),
                prompt = o.optString("prompt", ""),
                targetMode = ScheduledTargetMode.decode(o.optString("targetMode", null)),
                modelId = if (o.has("modelId")) o.optString("modelId", null) else null,
                modelBinding = if (o.has("modelBinding")) o.optString("modelBinding", null) else null,
                enabled = o.optBoolean("enabled", true),
                createdAt = o.optLong("createdAt", System.currentTimeMillis()),
                startDateMs = if (o.has("startDateMs")) o.optLong("startDateMs") else null,
                endDateMs = if (o.has("endDateMs")) o.optLong("endDateMs") else null,
                lastFiredAt = if (o.has("lastFiredAt")) o.optLong("lastFiredAt") else null,
                lastResultPreview = if (o.has("lastResultPreview"))
                    o.optString("lastResultPreview", null) else null,
                lastResultSessionId = if (o.has("lastResultSessionId"))
                    o.optString("lastResultSessionId", null) else null,
                runHistory = o.optJSONArray("runHistory")?.let { arr ->
                    buildList {
                        for (i in 0 until arr.length()) {
                            arr.optJSONObject(i)?.let { add(ScheduledRun.fromJson(it)) }
                        }
                    }
                } ?: emptyList(),
                // nanoMuse
                hidden = o.optBoolean("hidden", false),
                goalId = if (o.has("goalId")) o.optString("goalId", null) else null,
                intervalMinutes = if (o.has("intervalMinutes")) o.optInt("intervalMinutes") else null,
                // nanoMuse: [GH#557]
                additionalTimes = additionalTimesFromJson(o, hour, minute),
            )
        }
    }
}
