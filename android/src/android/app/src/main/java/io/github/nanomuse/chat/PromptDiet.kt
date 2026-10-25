package io.github.nanomuse.chat

import com.openminis.app.data.model.AgentToolDefinition

/**
 * The same instructions in fewer tokens. Every call carries the system prompt and the tool
 * schemas again; over the hosted model's last week the time to the first token grew with the
 * prompt (4.7 s at the median under 12k tokens, 25 s at 24 to 40k) and the baseline before a
 * single message was 12 to 14k tokens. The two fattest parts were the `browser_use` schema
 * (8k characters, two thirds of it on cookies, viewport and infinite scroll, which the model
 * reached for in a handful of the week's 840 browser calls) and the `minis-*` paragraphs of
 * the Android tools section (5.7k characters restating what each CLI's `--help` prints).
 *
 * This keeps upstream's text where it is and rewrites those parts densely on the way out:
 * the actions, parameters, enum values and required list of `browser_use` are upstream's,
 * only the wording is ours; the `android-*` lines and the section's intro stay as they are,
 * the five `minis-*` paragraphs and the terminal line are condensed, and a line this file
 * does not know is left alone. Nothing the model could do before is taken away.
 */
object PromptDiet {
    /** The hosted turns were sampled on 2026-10-25: 26.7k characters of system prompt, 14.2k of tools. */
    private const val BROWSER_USE =
        "Control a web browser with up to 3 tabs. Actions: navigate (web URLs, and minis:// resource URLs " +
            "such as minis://workspace/index.html to preview session files; their sub-resources resolve), " +
            "screenshot (returns an image; use it to see the page), click and type (by selector or " +
            "coordinates), get_text and get_readable to extract content, scroll, scroll_and_collect for " +
            "infinite-scroll pages (collects unique items across scroll positions in one call), " +
            "find_elements, get_page_info, get_backbone (a simplified DOM tree), hover, execute_js, " +
            "fetch (downloads with the page's session; returns metadata and a minis:// URL), new_tab, " +
            "close_tab, list_tabs (tab_id targets a tab; default the most recently used), set_user_agent, " +
            "set_viewport (viewport_width and viewport_height for this session, e.g. before screenshotting " +
            "a 1920×1080 composition; reset=true drops the override), wait_for_dom_stable (after navigation " +
            "or async loading; default 10 s), get_cookies and set_cookies for the current site, HttpOnly " +
            "included (get_cookies returns a summary and an env file, never raw values: " +
            "`. /var/minis/offloads/env_cookies_xxx.sh && command`). " +
            "Do NOT use this tool for minis:// action URLs (open_terminal, views, settings): those are app " +
            "deep links, write them as Markdown links in chat. " +
            // the hand-over sentence is nanoMuse's own and stays whole: the page needs the person
            "When a page needs the person themselves — a login, a password, a verification code, a " +
            "payment, a CAPTCHA — call hand_over with a short 'reason' in their language (e.g. '登录携程', " +
            "'Sign in to GitHub'): the page opens for them in this app, with their session, and the call " +
            "returns when they are done; then continue on the page as it is. Never type credentials and " +
            "never end your turn by telling them to log in themselves."

    /** Shorter descriptions for the parameters whose upstream text runs to a paragraph. */
    private val BROWSER_PARAMS = mapOf(
        "selector" to "CSS selector of the element (click, type, get_text, scroll, hover, find_elements). " +
            "For scroll: the scrollable container; omitted, the best one is detected.",
        "script" to "JavaScript for execute_js; runs inside an async function, so `await` and a top-level " +
            "`return` work.",
        "scroll_count" to "Scroll steps for scroll_and_collect (default 10, max 20), each by 'amount' pixels.",
        "item_selector" to "CSS selector of one content item for scroll_and_collect (e.g. 'article'); " +
            "omitted, repeated elements are detected.",
        "tab_id" to "Target tab (default the most recently used); list_tabs shows them.",
        "keywords" to "get_cookies: filter by name, space-separated; fuzzy=true (default) needs every " +
            "keyword in the name, fuzzy=false an exact match with one of them. Omit for all cookies.",
        "fuzzy" to "get_cookies: fuzzy (contains all) or exact (equals one) name matching; default true.",
        "cookies" to "set_cookies: a JSON array of {name, value, domain?, path?, secure?, http_only?, " +
            "expires? (Unix seconds; omit for a session cookie)}. Field names from common exports " +
            "(httpOnly, expirationDate, sameSite) are accepted, so cookies from a browser extension or " +
            "Playwright can be passed as they are.",
        "timeout" to "wait_for_dom_stable: seconds to wait (default 10).",
        "viewport_width" to "set_viewport: width in CSS pixels (e.g. 1920); with viewport_height unless reset=true.",
        "viewport_height" to "set_viewport: height in CSS pixels (e.g. 1080); with viewport_width unless reset=true.",
        "reset" to "set_viewport: true clears this session's override.",
    )

    /** The tools as the model sees them: `browser_use` worded densely, everything else untouched. */
    fun tools(tools: List<AgentToolDefinition>): List<AgentToolDefinition> = tools.map { tool ->
        if (tool.name != "browser_use") return@map tool
        tool.copy(
            description = BROWSER_USE,
            parameters = tool.parameters.mapValues { (key, param) ->
                BROWSER_PARAMS[key]?.let { param.copy(description = it) } ?: param
            },
        )
    }

    /** The section of the Android prompt this file condenses, and the one that follows it. */
    internal const val SECTION_HEAD = "\nAndroid-only tools (android-* CLIs):\n"
    internal const val SECTION_END = "\n\nEnvironment variables:"

    /** Each paragraph of the section that is rewritten, by the start of its line. */
    private val LINES = mapOf(
        "- minis-open " to "- minis-open <url-or-path>: open a web page or a file under /var/minis/ in a preview " +
            "inside the app (images, .md, .html, .pdf and office files, audio, video; anything else goes to the " +
            "share sheet), so the person keeps the conversation. Prefer it over android-open for whatever can be " +
            "previewed; android-open is for other schemes (tel:, mailto:, geo:, intent:) or when they want the " +
            "system handler.",
        "- minis-model-use" to "- minis-model-use: call another model the person has configured. `list` shows them " +
            "with their modalities (image_output, audio_output, ...), `search <query>` filters, " +
            "`run --model <id_or_name>` sends an OpenAI-shaped messages request (--input <json_file> or stdin; " +
            "stdout or --output <path>). The OpenAI shape is the input for every model and modality and is " +
            "converted for the provider; for provider-specific extras use `extra_body`, a custom `endpoint` or " +
            "a `passthrough` envelope (OpenAI-compatible providers only). For image_output models pass `prompt`, " +
            "`n`, `size`, `quality`, or Gemini's `generation_config`. The result may carry `warnings` and " +
            "`applied_extras`. `--help` has the full usage.",
        "- minis-config" to "- minis-config: read or change nanoMuse settings. `minis-config --help` lists the " +
            "subcommands, `topic-help <topic>` explains one area; for array fields (`models`, `groups`, " +
            "`envvars`, `defaults.agentLoopEntries`) `get` takes `--filter <keywords>` and " +
            "`--page <N> --page-size <N>` (default 20, max 100), so read a subset rather than the whole list. " +
            "Every write asks the person in-app and is logged (revertable under Settings → Logs → Config " +
            "Changes); after a change, relay the response's `user_message`. `permission_denied` means they " +
            "turned the tool off in [Settings → Permissions](minis://settings/permissions): say so, do not " +
            "retry. You can add providers and set their `apiKey` (a literal, or `\$\$ENV_VAR` to copy an env " +
            "var at write time), but `get` never returns API keys, OAuth tokens or env var values, and those " +
            "are not settable here; for an env var, point them at " +
            "[Set ENV_NAME](minis://settings/environments?create_key=ENV_NAME&create_value=).",
        "- minis-scheduled" to "- minis-scheduled: prompts that run on their own at a chosen time, for \"remind " +
            "me\", \"every morning\", \"run this later\". `create --time HH:MM --prompt \"...\" [--label L] " +
            "[--repeat once|daily|weekdays|custom --days mon,tue,...] [--target new|follow-up|rerun " +
            "--session <id> --message <id>] [--model <modelId>] [--start YYYY-MM-DD] [--end YYYY-MM-DD]` " +
            "(new: a fresh chat; follow-up: appended to a chat; rerun: a chat run again from a message); " +
            "`list` (with next trigger and history), `delete`, `enable`, `disable`, `run --id <taskId>`. " +
            "`--help` has the details.",
        "Interactive terminal:" to "Interactive terminal: [Open Terminal](minis://open_terminal), written as a " +
            "Markdown link, opens a terminal for what needs interactive stdin (passwords, ssh, TUI apps such " +
            "as htop or vi); `?init_command=` pre-fills (does not run) a fully percent-encoded command, e.g. " +
            "[Login to SSH](minis://open_terminal?init_command=ssh%20user%40host). Everything else is " +
            "shell_execute.",
    )

    /**
     * The stable prompt with its Android tools section condensed; the same string when the
     * section or its end is not where this file expects it.
     */
    fun prompt(base: String): String {
        val head = base.indexOf(SECTION_HEAD)
        if (head < 0) return base
        val start = head + SECTION_HEAD.length
        val end = base.indexOf(SECTION_END, start)
        if (end < 0) return base
        val section = base.substring(start, end).split("\n").joinToString("\n") { line ->
            LINES.entries.firstOrNull { line.startsWith(it.key) }?.value ?: line
        }
        return base.substring(0, start) + section + base.substring(end)
    }
}
