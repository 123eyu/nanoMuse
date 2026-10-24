"""The console's files as the relay serves them (after 0.22): revalidated on every load, so a
deploy reaches the next reload; the web console's error table knows every code the relay
can answer a signed-in person with, in both languages; and the admin console (0.28: built
from cloud/console with Vite, committed under nanomuse_cloud/console/admin) keeps its two
locales in step, its copy plain, and reads routes the relay serves."""

from __future__ import annotations

import re
from pathlib import Path

from test_accounts import make

CLOUD = Path(__file__).resolve().parents[1]
CONSOLE = CLOUD / "nanomuse_cloud" / "console"
ADMIN_SRC = CLOUD / "console" / "src"
LOCALES = ADMIN_SRC / "lib" / "locales.ts"

# what a signed-in person can get from the relay since 0.22 (service.py, api.py), by code
CODES_0_22 = [
    "signup_closed",
    "domain_limited",
    "channel_unsupported",
    "service_paused",
    "sync_paused",
    "hub_paused",
    "account_deleted",
    "too_many_in_flight",
    "rate_limited",
]


async def test_console_files_are_revalidated_on_every_load():
    app, client, *_ = make()
    for path in ("/app/", "/app/app.js", "/app/admin/", "/app/admin/admin.js"):
        r = await client.get(path)
        assert r.status_code == 200, (path, r.status_code)
        assert r.headers.get("cache-control") == "no-cache", path
        assert r.headers.get("etag"), path
    # the API's own answers keep their own caching
    r = await client.get("/v1/config")
    assert r.headers.get("cache-control") == "public, max-age=60"
    r = await client.get("/v1/nudges")
    assert r.headers.get("cache-control") == "public, max-age=3600"


def test_web_console_translates_every_refusal_in_both_languages():
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    tables = re.findall(r"errors: \{(.*?)\},\n", source, flags=re.S)
    assert len(tables) == 2, "one error table for 简体中文, one for English"
    zh, en = tables
    # account_disabled is the code the relay sends (service.sign_in); the table once said
    # "disabled", which nothing sends, so a disabled person read the server's English
    for code in CODES_0_22 + [
        "allowance_exhausted",
        "bad_key",
        "phone_region",
        "account_disabled",
        "bad_request",
        "too_large",
        "no_session",
        "device_online",
        "sync_off",
        "invite_code",
    ]:
        assert f"{code}:" in zh, f"{code} has no Chinese sentence"
        assert f"{code}:" in en, f"{code} has no English sentence"
    assert "disabled:" not in zh.replace("account_disabled:", "") and "disabled:" not in en.replace("account_disabled:", "")
    # the step marker for a failed step is a word in each language, not a punctuation mark
    assert len(re.findall(r"\bfailed: \"", source)) == 2 and 'k: "!"' not in source
    # a paused allowance (429 with paused: true) is said as paused, not as spent
    assert zh.count("e.paused") == 1 and en.count("e.paused") == 1
    # no exclamation marks in what a person reads
    assert "!" not in re.sub(r"[!=]==?|!\w", "", zh + en) and "！" not in zh


def test_web_console_is_one_hub_device_per_tab():
    """The id the console says hello with lives in sessionStorage, so two tabs of one
    browser are two devices on the hub. Shared through localStorage, as before, the two
    took turns replacing each other (close 4003) and reconnecting for as long as both
    stayed open."""
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    assert 'sessionStorage.getItem("nm.webid")' in source and 'sessionStorage.setItem("nm.webid"' in source
    assert 'LS.setItem("nm.webid"' not in source and 'LS.getItem("nm.webid")' not in source
    # the hello carries that id; the hub keys its connections on it (hub.py _attach)
    assert "device: { id: webId," in source


def test_web_console_copy_has_no_dashes_in_sentences():
    """The house rule: what a person reads carries no em dash. Checked on the two string
    tables (`const T = zh ? {...} : {...}`) of the web console; the one-character placeholder
    for an empty cell (`none: "—"`, `"—"`) is a glyph, not a sentence, and is let through."""
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    m = re.search(r"const T = zh \? \{(.*?)\n  \};", source, flags=re.S)
    assert m, "app.js: the T tables were not found"
    tables = m.group(1).replace('"—"', "")
    for line in tables.splitlines():
        assert "—" not in line, f"app.js: an em dash in a sentence: {line.strip()[:80]}"


# -- the admin console (0.28): cloud/console, built into nanomuse_cloud/console/admin ------------


def _locale_tables() -> tuple[str, str]:
    source = LOCALES.read_text(encoding="utf-8")
    zh_start = source.index("export const zh = {")
    en_start = source.index("export const en: Strings = {")
    return source[zh_start:en_start], source[en_start:]


def _keys(table: str) -> set[str]:
    """(section, key) pairs of one locale table: sections are the two-space keys, their
    strings the four-space ones; the inline maps (`{ a: "x", b: "y" }`) count as one key."""
    out: set[str] = set()
    section = ""
    for line in table.splitlines():
        m = re.match(r"^  ([a-zA-Z_]+): ", line)
        if m:
            section = m.group(1)
            out.add((section, ""))
            continue
        m = re.match(r"^    ([a-zA-Z_\"'.]+): ", line)
        if m and section:
            out.add((section, m.group(1).strip("\"'")))
    return out


def test_admin_console_has_every_string_in_both_languages():
    """locales.ts carries 简体中文 first and English second with the same sections and keys
    (locales.test.ts checks the nested maps and the function arities under vitest; this is
    the same promise kept where the Python checks run)."""
    zh, en = _locale_tables()
    zk, ek = _keys(zh), _keys(en)
    assert zk - ek == set(), f"keys only in 简体中文: {sorted(zk - ek)[:10]}"
    assert ek - zk == set(), f"keys only in English: {sorted(ek - zk)[:10]}"
    assert len(zk) > 400, "the locale table looks truncated"


def test_admin_console_copy_has_no_dashes_or_exclamation_marks():
    """No em dash and no exclamation mark in any string literal of the admin console's
    locales (the glyph placeholders are a middle dot, never a dash)."""
    source = LOCALES.read_text(encoding="utf-8")
    literals = re.findall(r'"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`', source)
    bad = [s for s in literals if re.search(r"[—!！]", s)]
    assert bad == [], bad[:5]


def test_admin_console_build_is_the_committed_one():
    """The build lands under nanomuse_cloud/console/admin as exactly index.html, admin.js and
    admin.css under fixed names, and index.html loads them from /app/admin/ (the relay's
    mount); CI rebuilds and fails on a difference, so the committed files are the source's."""
    out = CONSOLE / "admin"
    assert sorted(p.name for p in out.iterdir()) == ["admin.css", "admin.js", "index.html"]
    html = (out / "index.html").read_text(encoding="utf-8")
    assert 'src="/app/admin/admin.js"' in html and 'href="/app/admin/admin.css"' in html
    assert '<div id="app"></div>' in html and 'content="noindex' in html
    js = (out / "admin.js").read_text(encoding="utf-8")
    # the build is the console in this tree: a string only the new locales carry
    assert "模型接口此刻" in js and "The model endpoint now" in js


def test_admin_console_reads_routes_the_relay_serves():
    """Every /v1/admin route the console's sources name has a handler in api.py, and the
    0.28 analytics routes are read by a page each."""
    api = (CLOUD / "nanomuse_cloud" / "api.py").read_text(encoding="utf-8")
    served = set(re.findall(r'@app\.(?:get|post|put|delete)\("(/v1/admin[^"]*)"', api))
    served_prefixes = {s.split("{")[0].rstrip("/") for s in served}
    sources = "\n".join(p.read_text(encoding="utf-8") for p in ADMIN_SRC.rglob("*.ts*"))
    named = set(re.findall(r'"(/v1/admin/[a-z_/.-]+)', sources)) | set(re.findall(r"`(/v1/admin/[a-z_/.-]+)", sources))
    for route in sorted(named):
        base = route.rstrip("/")
        assert base in served_prefixes or any(base.startswith(p + "/") for p in served_prefixes), (
            f"the console reads {route}, which api.py does not serve"
        )
    for route in (
        "live",
        "analytics/usage",
        "analytics/day",
        "analytics/platforms",
        "analytics/cohorts",
        "analytics/hands",
        "analytics/quality",
        "analytics/abuse",
    ):
        assert f"/v1/admin/{route}" in served, route
        assert f"/v1/admin/{route}" in sources, f"no page reads /v1/admin/{route}"


def test_admin_console_names_every_event_kind_the_relay_writes():
    """The timeline and the feed show an event's kind by a sentence in both languages: each
    kind the relay writes (db.add_event and cloud.note in service.py, api.py, hub.py, sync.py)
    has one."""
    kinds: set[str] = set()
    for name in ("service.py", "api.py", "hub.py", "sync.py", "controls.py"):
        text = (CLOUD / "nanomuse_cloud" / name).read_text(encoding="utf-8")
        for call in re.findall(r"(?:add_event|\.note)\((.*?)\)", text, flags=re.S):
            kinds.update(re.findall(r'"([a-z_]+\.[a-z_.]+)"', call))
        kinds.update(re.findall(r'kind = "([a-z_]+\.[a-z_.]+)" if', text))
        kinds.update(re.findall(r'if [a-z_]+ else "([a-z_]+\.[a-z_.]+)"', text))
        for a, b in re.findall(r'return "([a-z_]+\.[a-z_.]+)" if .*? else "([a-z_]+\.[a-z_.]+)"', text):
            kinds.update((a, b))
    assert {"account.created", "budget.refused", "upstream.error", "content.rejected", "invite.clawback", "credit.granted"} <= kinds
    zh, en = _locale_tables()
    for kind in sorted(kinds):
        assert f'"{kind}": ' in zh, f"{kind} has no Chinese name"
        assert f'"{kind}": ' in en, f"{kind} has no English name"


def test_admin_console_edits_the_star_card_text_in_both_languages():
    """The nudges editor (relay 0.22's policy) has the two text inputs, English and 简体中文,
    named in both locales, and saves the whole policy with its version through PUT."""
    settings = (ADMIN_SRC / "pages" / "Settings.tsx").read_text(encoding="utf-8")
    assert "star({ text: e.target.value })" in settings and "star({ text_zh: e.target.value })" in settings
    assert 'put("/v1/admin/nudges", body)' in settings and "{ reset: true }" in settings
    zh, en = _locale_tables()
    assert 'nuText: "英文文案"' in zh and 'nuTextZh: "中文文案"' in zh
    assert 'nuText: "Text (English)"' in en and 'nuTextZh: "Text (中文)"' in en


def test_admin_console_invites_page_reclaims_through_the_relay():
    """The Invites page lists the inviters with the same-address warning and sends the
    operator to the drawer's reclaim, which posts to /v1/admin/invites/abuse with both flags
    after asking."""
    invites = (ADMIN_SRC / "pages" / "Invites.tsx").read_text(encoding="utf-8")
    assert '"/v1/admin/invites?' in invites and "invited_same_ip" in invites and "T.in.sameIpWarn" in invites
    drawer = (ADMIN_SRC / "pages" / "AccountDrawer.tsx").read_text(encoding="utf-8")
    assert '"/v1/admin/invites/abuse"' in drawer and "delete_unused" in drawer and "disable" in drawer
    assert "T.ac.abuseConfirm(" in drawer and "T.ac.abuseResult(" in drawer
    zh, en = _locale_tables()
    assert 'abuse: "收回邀请奖励"' in zh and 'abuse: "Reclaim invite credit"' in en


def test_web_console_prints_the_relays_code_lifetime_and_password_length():
    """The relay's ``CODE_TTL_S`` and ``PASSWORD_MIN_LEN`` are configurable; the console
    reads them from ``/v1/config`` since 0.25 instead of saying ten minutes and 8 characters."""
    source = (CONSOLE / "app.js").read_text(encoding="utf-8")
    assert '"/v1/config"' in source and "cfg.code_ttl_s" in source and "cfg.password_min_len" in source
    assert source.count("codeSent: (m) =>") == 2 and source.count("pwShort: (n) =>") == 2 and source.count("pwWhy: (n) =>") == 2
    assert "十分钟" not in source and "ten minutes" not in source
    assert "8 个字符" not in source and "8 位" not in source and "8 characters" not in source
    # the default stands for a relay that does not send the fields yet
    assert "code_ttl_s: 600" in source and "password_min_len: 8" in source


def test_admin_console_people_page_searches_and_filters_in_the_address_bar():
    """The People page: the search reaches the identifier, the addresses, the version, the
    devices and the inviter and takes several words; the filters and the sort live in the
    hash so a view can be shared and survives a reload; the drawer's statement and timeline
    page through the relay with `before`, filtered by kind and text."""
    people = (ADMIN_SRC / "pages" / "People.tsx").read_text(encoding="utf-8")
    assert "matches(haystack(a), ts)" in people
    hay = people[people.index("const haystack = ") : people.index("const base = useMemo(")]
    for field in (
        "a.identifier",
        "a.id",
        "a.first_ip",
        "a.last_ip",
        "a.last_version",
        "d.name",
        "d.llm_model",
        "a.invite_code",
        "a.invited_by",
    ):
        assert field in hay, f"the search does not reach {field}"
    for key in ("country", "province", "channel", "status", "joined", "activity", "spend", "source", "platform", "version", "sort"):
        assert f'p.get("{key}")' in people, f"{key} is not a hash parameter"
    drawer = (ADMIN_SRC / "pages" / "AccountDrawer.tsx").read_text(encoding="utf-8")
    assert "/ledger" in drawer and "/events" in drawer and "before" in drawer and "kind" in drawer
    activity = (ADMIN_SRC / "pages" / "Activity.tsx").read_text(encoding="utf-8")
    assert "/v1/admin/events" in activity and "before" in activity
    # the Stats page's jump list
    stats = (ADMIN_SRC / "pages" / "Stats.tsx").read_text(encoding="utf-8")
    assert "`m-${m.id}`" in stats and "scrollIntoView" in stats


def test_admin_console_live_page_judges_the_endpoint_on_the_last_ten_minutes():
    """The Live page's verdict: saturated when over a quarter of the last ten minutes'
    requests queued or the queue's P95 passes two seconds (or the gate is full with a
    queue), busy at a twentieth or half a second; the thresholds in the code are the ones
    the sentence under the verdict tells the operator."""
    live = (ADMIN_SRC / "pages" / "Live.tsx").read_text(encoding="utf-8")
    assert "waited_share > 0.25" in live and "p95_wait_ms > 2000" in live
    assert "waited_share > 0.05" in live and "p95_wait_ms > 500" in live
    zh, en = _locale_tables()
    assert "25%" in zh and "2 秒" in zh and "5%" in zh and "500 毫秒" in zh
    assert "25%" in en and "2 s" in en and "5%" in en and "500 ms" in en
