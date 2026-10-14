#!/usr/bin/env python3
"""nanomuse-traffic: how many people visit nanomuse.cn and download from it, by day.

Reads the JSON access log Caddy writes for the site (mirror/nanomuse.cn.caddy, kept for seven
days) from where the last run stopped, and adds what it finds to a small SQLite database of
daily counts: page views, visitors, bots, downloads per file, referring sites, the pages
themselves. Once a run it also asks GitHub for the repository's stars and the download counts
on its releases, so the two download numbers can sit side by side. The relay reads the database
(read-only, mounted into its container) for the operator's page; nothing here is public.

What is kept about a visitor: nothing that names one. A visitor is counted once a day through a
hash of (a random salt made for that day, the address, the browser string); the salt is thrown
away two days later, the hashes with it. Addresses are never written — the raw log Caddy keeps
for a week is the only place they exist, and it stays on the box.

    TRAFFIC_LOG_DIR      where Caddy's log lands (docker-compose.yml mounts logs/caddy there);
                         default: the showcase's logs/caddy
    TRAFFIC_LOG_NAME     the log's file name (default nanomuse.cn.log; rolled files sit beside it)
    TRAFFIC_DB           the database (default /var/lib/nanomuse-traffic/traffic.db)
    TRAFFIC_REPO         the repository whose stars and release downloads are recorded
    TRAFFIC_HOSTS        the site's own names, left out of the referrers (comma separated)
    TRAFFIC_DAY_OFFSET_H local day for the counts, hours east of UTC (default 8: Beijing days,
                         like the relay's DAY_OFFSET_H)
    GITHUB_TOKEN         optional; more API calls an hour

Idempotent and safe to run by hand: `nanomuse-traffic`, or `nanomuse-traffic --report` for the
last two weeks in the terminal. The systemd timer runs it every ten minutes.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
LOG_DIR = Path(os.environ.get("TRAFFIC_LOG_DIR") or HERE.parent / "logs" / "caddy")
LOG_NAME = os.environ.get("TRAFFIC_LOG_NAME") or "nanomuse.cn.log"
DB_PATH = Path(os.environ.get("TRAFFIC_DB") or "/var/lib/nanomuse-traffic/traffic.db")
REPO = os.environ.get("TRAFFIC_REPO") or "nano-muse/nanoMuse"
OWN_HOSTS = {
    h.strip().lower()
    for h in (os.environ.get("TRAFFIC_HOSTS") or "nanomuse.cn,www.nanomuse.cn").split(",")
    if h.strip()
}
DAY_OFFSET_S = int(os.environ.get("TRAFFIC_DAY_OFFSET_H") or "8") * 3600
TOKEN = os.environ.get("GITHUB_TOKEN", "")
API = "https://api.github.com"

# Crawlers and monitors: counted, but not as visitors. Download managers and the apps' own
# HTTP clients are not in here on purpose — a download is a download whoever fetches it.
BOT = re.compile(
    r"bot|spider|crawl|slurp|archive\.org|facebookexternalhit|preview|uptime|monitor|pingdom|statuscake|"
    r"curl/|wget/|python-requests|python-urllib|go-http-client|libwww|headlesschrome|phantomjs|"
    r"zgrab|masscan|nmap|censys|shodan|scaner|scanner",
    re.I,
)
CRAWLER = re.compile(r"bot|spider|crawl|zgrab|masscan|nmap|censys|shodan", re.I)
NOT_A_DOWNLOAD = (".sha256", ".json", ".txt", ".md", ".sig", ".asc")

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS days (
    day       TEXT PRIMARY KEY,                -- YYYY-MM-DD, local (TRAFFIC_DAY_OFFSET_H)
    requests  INTEGER NOT NULL DEFAULT 0,      -- everything the site answered
    pages     INTEGER NOT NULL DEFAULT 0,      -- HTML pages served to people
    visitors  INTEGER NOT NULL DEFAULT 0,      -- distinct (salt, address, browser) hashes that day
    bots      INTEGER NOT NULL DEFAULT 0,      -- requests by crawlers and monitors
    downloads INTEGER NOT NULL DEFAULT 0,      -- packages fetched from /dl/
    bytes     INTEGER NOT NULL DEFAULT 0       -- response bytes, all of it
);
CREATE TABLE IF NOT EXISTS visitors (day TEXT NOT NULL, vhash TEXT NOT NULL, PRIMARY KEY (day, vhash));
CREATE TABLE IF NOT EXISTS pages (day TEXT NOT NULL, path TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, path));
CREATE TABLE IF NOT EXISTS referrers (day TEXT NOT NULL, host TEXT NOT NULL, hits INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, host));
CREATE TABLE IF NOT EXISTS downloads (
    day   TEXT NOT NULL, file TEXT NOT NULL,
    hits  INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, file)
);
CREATE TABLE IF NOT EXISTS github (
    day       TEXT PRIMARY KEY,                -- the latest reading of that day
    ts        INTEGER NOT NULL,
    stars     INTEGER NOT NULL DEFAULT 0,
    forks     INTEGER NOT NULL DEFAULT 0,
    watchers  INTEGER NOT NULL DEFAULT 0,
    downloads INTEGER NOT NULL DEFAULT 0,      -- download_count summed over every release asset
    assets    TEXT NOT NULL DEFAULT '{}'       -- {tag: {asset: download_count}} for the newest releases
);
"""


def local_day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts + DAY_OFFSET_S))


def open_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # a rollback journal, not WAL: the relay opens this file read-only from another container,
    # and a WAL reader would need to write the -shm file beside it
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.executescript(SCHEMA)
    return conn


def get_state(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def salt_for(conn: sqlite3.Connection, day: str) -> str:
    key = f"salt:{day}"
    salt = get_state(conn, key)
    if not salt:
        salt = secrets.token_hex(16)
        set_state(conn, key, salt)
    return salt


# -- the log ------------------------------------------------------------------------------------


def log_files() -> list[Path]:
    """The current log and the ones Caddy rolled, oldest first."""
    if not LOG_DIR.is_dir():
        return []
    stem = LOG_NAME[: -len(".log")] if LOG_NAME.endswith(".log") else LOG_NAME
    rolled = [p for p in LOG_DIR.iterdir() if p.name.startswith(stem + "-") and p.name != LOG_NAME]
    rolled.sort(key=lambda p: p.stat().st_mtime)
    current = LOG_DIR / LOG_NAME
    return rolled + ([current] if current.exists() else [])


def rolled_since_last_run(conn: sqlite3.Connection, saved_inode: int, saved_offset: int):
    """The lines still unread in the files Caddy rolled since the last run, oldest first:
    the rest of the file that was current then (found by its inode; the site block says
    roll_uncompressed so that it keeps it), and the whole of every file rolled after it.
    Two rolls between two runs (a burst, a run that was skipped) used to lose the middle
    file; a saved file that is already gone (rolled past roll_keep or the week) used to lose
    every newer one, and now loses only itself: the files whose last write is later than the
    last run were rolled after it, so nothing of them was read."""
    rolled: list[tuple[Path, os.stat_result]] = []
    for p in log_files():
        if p.name == LOG_NAME or p.name.endswith(".gz"):
            continue
        try:
            rolled.append((p, p.stat()))
        except OSError:
            continue  # gone between the listing and now
    start = next((i for i, (_, ps) in enumerate(rolled) if ps.st_ino == saved_inode), None)
    if start is None:
        last_run = float(get_state(conn, "last_run", "0") or 0)
        unread = [p for p, ps in rolled if ps.st_mtime > last_run] if last_run else []
    else:
        with rolled[start][0].open("rb") as f:
            f.seek(saved_offset)
            yield from f
        unread = [p for p, _ in rolled[start + 1 :]]
    for p in unread:
        with p.open("rb") as f:
            yield from f


def read_new_lines(conn: sqlite3.Connection):
    """Yield the lines written since the last run. The position is remembered as (inode, offset)
    of the current file; when Caddy rolled it, the rolled files are finished first
    (``rolled_since_last_run``), then the new file is read from the top."""
    current = LOG_DIR / LOG_NAME
    if not current.exists():
        return
    st = current.stat()
    saved_inode = int(get_state(conn, "log:inode", "0") or 0)
    saved_offset = int(get_state(conn, "log:offset", "0") or 0)
    if saved_inode and saved_inode != st.st_ino:
        yield from rolled_since_last_run(conn, saved_inode, saved_offset)
        saved_offset = 0
    elif saved_inode == st.st_ino and saved_offset > st.st_size:
        saved_offset = 0  # truncated
    with current.open("rb") as f:
        f.seek(saved_offset)
        for line in f:
            if not line.endswith(b"\n"):
                break  # a half-written line: next run
            saved_offset += len(line)
            yield line
    set_state(conn, "log:inode", str(st.st_ino))
    set_state(conn, "log:offset", str(saved_offset))


def page_path(path: str) -> str:
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    if path.startswith("/dl/"):
        return "/dl/"
    return path[:120]


def referrer_host(ref: str) -> str:
    if not ref:
        return ""
    try:
        host = (urlsplit(ref).hostname or "").lower()
    except ValueError:
        return ""
    if not host or host in OWN_HOSTS:
        return ""
    return host[:100]


class Tally:
    """One run's additions, flushed to the database in a single transaction."""

    def __init__(self) -> None:
        self.days: dict[str, dict[str, int]] = {}
        self.pages: dict[tuple[str, str], int] = {}
        self.refs: dict[tuple[str, str], int] = {}
        self.dls: dict[tuple[str, str], list[int]] = {}
        self.visitors: set[tuple[str, str]] = set()
        self.lines = 0

    def day(self, d: str) -> dict[str, int]:
        return self.days.setdefault(
            d, {"requests": 0, "pages": 0, "bots": 0, "downloads": 0, "bytes": 0}
        )

    def add(self, rec: dict, conn: sqlite3.Connection) -> None:
        req = rec.get("request") or {}
        host = str(req.get("host") or "").lower().split(":")[0]
        if OWN_HOSTS and host not in OWN_HOSTS:
            return
        self.lines += 1
        ts = float(rec.get("ts") or time.time())
        d = local_day(ts)
        day = self.day(d)
        status = int(rec.get("status") or 0)
        size = int(rec.get("size") or 0)
        headers = req.get("headers") or {}
        ua = str((headers.get("User-Agent") or [""])[0])[:300]
        path = str(req.get("uri") or "/").split("?", 1)[0]
        ctype = str(((rec.get("resp_headers") or {}).get("Content-Type") or [""])[0])
        day["requests"] += 1
        day["bytes"] += max(0, size)
        if BOT.search(ua):
            day["bots"] += 1
            if not CRAWLER.search(ua) and path.startswith("/dl/"):
                self.download(d, path, status, size, headers, ctype)
            return
        if path.startswith("/dl/"):
            self.download(d, path, status, size, headers, ctype)
        if status in (200, 304) and ctype.startswith("text/html"):
            day["pages"] += 1
            p = page_path(path)
            self.pages[(d, p)] = self.pages.get((d, p), 0) + 1
            ref = referrer_host(str((headers.get("Referer") or [""])[0]))
            if ref:
                self.refs[(d, ref)] = self.refs.get((d, ref), 0) + 1
            ip = str(req.get("client_ip") or req.get("remote_ip") or "")
            vhash = hashlib.sha256(f"{salt_for(conn, d)}|{ip}|{ua}".encode()).hexdigest()[:16]
            self.visitors.add((d, vhash))

    def download(
        self, d: str, path: str, status: int, size: int, headers: dict, ctype: str
    ) -> None:
        name = path.rsplit("/", 1)[-1]
        if (
            not name
            or name.startswith(".")
            or name.lower().endswith(NOT_A_DOWNLOAD)
            or ctype.startswith("text/html")
        ):
            return
        rng = str((headers.get("Range") or [""])[0])
        whole = status == 200 or (status == 206 and rng.startswith("bytes=0-"))
        cur = self.dls.setdefault((d, name[:160]), [0, 0])
        if status in (200, 206):
            cur[1] += max(0, size)
        if whole:
            cur[0] += 1
            self.day(d)["downloads"] += 1

    def flush(self, conn: sqlite3.Connection) -> None:
        for d, v in self.days.items():
            conn.execute(
                """INSERT INTO days(day, requests, pages, bots, downloads, bytes) VALUES (?,?,?,?,?,?)
                   ON CONFLICT(day) DO UPDATE SET requests=requests+excluded.requests, pages=pages+excluded.pages,
                   bots=bots+excluded.bots, downloads=downloads+excluded.downloads, bytes=bytes+excluded.bytes""",
                (d, v["requests"], v["pages"], v["bots"], v["downloads"], v["bytes"]),
            )
        for (d, p), n in self.pages.items():
            conn.execute(
                "INSERT INTO pages(day, path, hits) VALUES (?,?,?) ON CONFLICT(day, path) DO UPDATE SET hits=hits+excluded.hits",
                (d, p, n),
            )
        for (d, r), n in self.refs.items():
            conn.execute(
                "INSERT INTO referrers(day, host, hits) VALUES (?,?,?) ON CONFLICT(day, host) DO UPDATE SET hits=hits+excluded.hits",
                (d, r, n),
            )
        for (d, f), (n, b) in self.dls.items():
            conn.execute(
                """INSERT INTO downloads(day, file, hits, bytes) VALUES (?,?,?,?)
                   ON CONFLICT(day, file) DO UPDATE SET hits=hits+excluded.hits, bytes=bytes+excluded.bytes""",
                (d, f, n, b),
            )
        for d, vh in self.visitors:
            conn.execute("INSERT OR IGNORE INTO visitors(day, vhash) VALUES (?,?)", (d, vh))
        for d in self.days:
            conn.execute(
                "UPDATE days SET visitors=(SELECT COUNT(*) FROM visitors WHERE day=?) WHERE day=?",
                (d, d),
            )


def ingest(conn: sqlite3.Connection) -> Tally:
    tally = Tally()
    conn.execute("BEGIN")
    try:
        for line in read_new_lines(conn):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or "request" not in rec:
                continue
            tally.add(rec, conn)
        tally.flush(conn)
        # the salts of the day before yesterday and earlier, and the hashes made with them:
        # the counts stay in `days`, the way back to a visitor does not
        cutoff = local_day(time.time() - 2 * 86400)
        conn.execute("DELETE FROM state WHERE key LIKE 'salt:%' AND key < ?", (f"salt:{cutoff}",))
        conn.execute("DELETE FROM visitors WHERE day < ?", (cutoff,))
        set_state(conn, "last_run", str(int(time.time())))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return tally


# -- GitHub -------------------------------------------------------------------------------------


def github(url: str) -> object:
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "nanomuse-traffic"}
    )
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def snapshot_github(conn: sqlite3.Connection) -> str:
    try:
        repo = github(f"{API}/repos/{REPO}")
        releases = github(f"{API}/repos/{REPO}/releases?per_page=30")
    except Exception as exc:  # noqa: BLE001 - the counts wait for the next run
        return f"github: {exc}"
    if not isinstance(repo, dict) or not isinstance(releases, list):
        return "github: unexpected answer"
    total = 0
    assets: dict[str, dict[str, int]] = {}
    for i, rel in enumerate(releases):
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        per = {
            str(a.get("name")): int(a.get("download_count") or 0)
            for a in rel.get("assets") or []
            if isinstance(a, dict)
        }
        total += sum(per.values())
        if len(assets) < 3:
            assets[str(rel.get("tag_name") or f"#{i}")] = per
    now = int(time.time())
    conn.execute(
        """INSERT INTO github(day, ts, stars, forks, watchers, downloads, assets) VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(day) DO UPDATE SET ts=excluded.ts, stars=excluded.stars, forks=excluded.forks,
           watchers=excluded.watchers, downloads=excluded.downloads, assets=excluded.assets""",
        (
            local_day(now),
            now,
            int(repo.get("stargazers_count") or 0),
            int(repo.get("forks_count") or 0),
            int(repo.get("subscribers_count") or 0),
            total,
            json.dumps(assets, ensure_ascii=False),
        ),
    )
    return f"github: {repo.get('stargazers_count', 0)} stars, {total} release downloads"


# -- the terminal -------------------------------------------------------------------------------


def report(conn: sqlite3.Connection, days: int = 14) -> None:
    since = local_day(time.time() - (days - 1) * 86400)
    rows = conn.execute("SELECT * FROM days WHERE day>=? ORDER BY day", (since,)).fetchall()
    print(f"{'day':10}  {'pages':>6} {'visitors':>8} {'bots':>6} {'downloads':>9} {'MB':>8}")
    for r in rows:
        print(
            f"{r['day']:10}  {r['pages']:>6} {r['visitors']:>8} {r['bots']:>6} {r['downloads']:>9} {r['bytes'] / 1e6:>8.1f}"
        )
    print()
    for title, sql in (
        (
            "downloads",
            "SELECT file AS k, SUM(hits) AS n FROM downloads WHERE day>=? GROUP BY file ORDER BY n DESC LIMIT 12",
        ),
        (
            "pages",
            "SELECT path AS k, SUM(hits) AS n FROM pages WHERE day>=? GROUP BY path ORDER BY n DESC LIMIT 12",
        ),
        (
            "referrers",
            "SELECT host AS k, SUM(hits) AS n FROM referrers WHERE day>=? GROUP BY host ORDER BY n DESC LIMIT 12",
        ),
    ):
        print(title)
        for r in conn.execute(sql, (since,)).fetchall():
            print(f"  {r['n']:>7}  {r['k']}")
    g = conn.execute("SELECT * FROM github ORDER BY day DESC LIMIT 1").fetchone()
    if g:
        print(
            f"\ngithub ({g['day']}): {g['stars']} stars, {g['forks']} forks, {g['downloads']} release downloads"
        )


def main(argv: list[str]) -> int:
    conn = open_db()
    if "--report" in argv:
        report(conn)
        return 0
    lock_path = DB_PATH.with_suffix(".lock")
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0  # the timer's run is still going
        tally = ingest(conn)
        note = snapshot_github(conn)
        try:
            os.chmod(DB_PATH, 0o644)  # the relay reads it from its container
        except OSError:
            pass
    touched = ", ".join(
        f"{d}: {v['pages']} pages, {v['downloads']} downloads"
        for d, v in sorted(tally.days.items())
    )
    print(f"traffic: {tally.lines} log lines" + (f" ({touched})" if touched else "") + f"; {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
