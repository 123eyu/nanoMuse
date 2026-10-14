"""0.22: the GitHub collector — stars, forks, watchers and release downloads, per UTC day.

Two requests to the public API (`GET /repos/{owner}/{repo}`, `GET
/repos/{owner}/{repo}/releases`), once a day from an asyncio task and on demand from the
console (*Refresh now*). Each run writes today's snapshot into the `daily` table under
the `gh.*` metrics — `gh.stars`, `gh.forks`, `gh.watchers`, `gh.issues`, `gh.downloads`
(key `total`, one key per platform, `asset:<name>` per release asset and `release:<tag>` per
release) — replacing the day's earlier figure, so the last run of a day is the day's
value. "New per day" is today's figure minus the previous day's. The relay keeps working
when GitHub is unreachable: the error is logged and shown, the last snapshot stands.

Unauthenticated, GitHub allows 60 requests an hour; GITHUB_TOKEN raises that and is
never needed for once a day.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from .db import Database, utc_day

log = logging.getLogger("nanomuse_cloud.github")

METRICS = ("gh.stars", "gh.forks", "gh.watchers", "gh.issues", "gh.downloads")
# how often the task re-reads GitHub; the day's row is overwritten each time
INTERVAL_S = 6 * 3600
RELEASES_PAGES = 5  # 100 releases a page


def platform_of_asset(name: str) -> str:
    """Which app a release asset is: android, ios, mac, windows, linux, docker, other."""
    n = name.lower()
    if n.endswith((".apk", ".aab")):
        return "android"
    if n.endswith(".ipa"):
        return "ios"
    if n.endswith((".dmg", ".pkg")) or "mac" in n or "darwin" in n:
        return "mac"
    if n.endswith((".exe", ".msi")) or "win" in n:
        return "windows"
    if n.endswith((".appimage", ".deb", ".rpm", ".snap", ".flatpak")) or "linux" in n:
        return "linux"
    if "docker" in n or n.endswith(".tar"):
        return "docker"
    return "other"


class GitHubCollector:
    def __init__(
        self,
        db: Database,
        repo: str,
        token: str = "",
        api: str = "https://api.github.com",
        transport: httpx.AsyncBaseTransport | None = None,
        clock=time.time,
    ):
        self.db = db
        self.repo = repo.strip().strip("/")
        self.token = token
        self.api = api.rstrip("/")
        self.transport = transport
        self.clock = clock
        self.last_run: int | None = None
        self.last_ok: int | None = None
        self.error: str = ""
        self.running = False
        self._task: asyncio.Task | None = None
        state = db.settings_get("github.collector")
        if state is not None and isinstance(state[0], dict):
            self.last_run = state[0].get("last_run")
            self.last_ok = state[0].get("last_ok")
            self.error = str(state[0].get("error") or "")

    @property
    def enabled(self) -> bool:
        return bool(self.repo)

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/vnd.github+json", "User-Agent": "nanomuse-cloud", "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    async def collect(self) -> dict[str, Any]:
        """One run: fetch, write today's snapshot, remember how it went. A GitHub error is
        an answer, not an exception; whatever happens, `running` is let go at the end."""
        if not self.enabled:
            return {"ok": False, "error": "no repository configured"}
        if self.running:
            return {"ok": False, "error": "already running"}
        # `running` is let go in `finally`: an error escaping the write below (a locked
        # database, an asset of an unexpected shape) used to leave it set, and every run
        # after that, the six-hourly one and *Refresh now*, answered "already running"
        # until a restart
        self.running = True
        t = int(self.clock())
        try:
            try:
                async with httpx.AsyncClient(timeout=20.0, transport=self.transport, headers=self._headers()) as c:
                    repo = await self._json(c, f"{self.api}/repos/{self.repo}")
                    releases: list[dict] = []
                    for page in range(1, RELEASES_PAGES + 1):
                        chunk = await self._json(c, f"{self.api}/repos/{self.repo}/releases", params={"per_page": 100, "page": page})
                        if not isinstance(chunk, list):
                            break
                        releases.extend(x for x in chunk if isinstance(x, dict))
                        if len(chunk) < 100:
                            break
            except (httpx.HTTPError, ValueError) as e:
                self.error = f"{type(e).__name__}: {str(e)[:160]}"
                self.last_run = t
                self._remember()
                log.warning("github: could not read %s: %s", self.repo, self.error)
                return {"ok": False, "error": self.error}
            snapshot = self.snapshot_from(repo, releases)
            day = utc_day(t)
            for metric, keyed in snapshot.items():
                for key, value in keyed.items():
                    self.db.daily_set(day, metric, key, value, ts=t)
            self.error = ""
            self.last_run = self.last_ok = t
            self._remember()
        finally:
            self.running = False
        log.info(
            "github: %s has %d stars, %d forks, %d downloads across %d releases",
            self.repo,
            snapshot["gh.stars"][""],
            snapshot["gh.forks"][""],
            snapshot["gh.downloads"]["total"],
            len(releases),
        )
        return {"ok": True, "day": day, "stars": snapshot["gh.stars"][""], "downloads": snapshot["gh.downloads"]["total"]}

    @staticmethod
    async def _json(c: httpx.AsyncClient, url: str, params: dict | None = None) -> Any:
        r = await c.get(url, params=params)
        if r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0":
            raise ValueError("GitHub rate limit reached; set GITHUB_TOKEN or wait an hour")
        r.raise_for_status()
        return r.json()

    @staticmethod
    def snapshot_from(repo: Any, releases: list[dict]) -> dict[str, dict[str, int]]:
        """The figures one run writes, from the two answers. `watchers` is GitHub's
        `subscribers_count` (its `watchers_count` is the star count under another name)."""
        repo = repo if isinstance(repo, dict) else {}
        out: dict[str, dict[str, int]] = {
            "gh.stars": {"": int(repo.get("stargazers_count") or 0)},
            "gh.forks": {"": int(repo.get("forks_count") or 0)},
            "gh.watchers": {"": int(repo.get("subscribers_count") or 0)},
            "gh.issues": {"": int(repo.get("open_issues_count") or 0)},
        }
        downloads: dict[str, int] = {"total": 0}
        for rel in releases:
            tag = str(rel.get("tag_name") or rel.get("name") or "")[:80]
            rel_total = 0
            for asset in rel.get("assets") or []:
                if not isinstance(asset, dict):
                    continue
                name = str(asset.get("name") or "")[:120]
                n = int(asset.get("download_count") or 0)
                rel_total += n
                downloads["total"] += n
                downloads[f"asset:{name}"] = downloads.get(f"asset:{name}", 0) + n
                p = platform_of_asset(name)
                downloads[p] = downloads.get(p, 0) + n
            if tag:
                downloads[f"release:{tag}"] = downloads.get(f"release:{tag}", 0) + rel_total
        out["gh.downloads"] = downloads
        return out

    def _remember(self) -> None:
        self.db.settings_put("github.collector", {"last_run": self.last_run, "last_ok": self.last_ok, "error": self.error})

    # -- the series the console draws ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "repo": self.repo,
            "token": bool(self.token),
            "last_run": self.last_run,
            "last_ok": self.last_ok,
            "error": self.error,
            "running": self.running,
            "interval_s": INTERVAL_S,
        }

    def series(self, days: int = 30) -> dict[str, Any]:
        """Per UTC day: the running totals and what each day added (today − the previous
        snapshot), for stars, forks, watchers and downloads; the latest figures per platform
        and per asset; which days have a snapshot at all."""
        t = int(self.clock())
        today = utc_day(t)
        since = today - 86400 * max(0, days - 1)
        # every snapshot of the running totals (one row a day per metric: small), so the
        # first shown day's "new" compares to the last snapshot before the window, gaps included
        rows: dict[str, dict[int, int]] = {m: {} for m in METRICS}
        for m in METRICS:
            for r in self.db.daily_rows(m, 0, key="total" if m == "gh.downloads" else ""):
                rows[m][int(r["day"])] = int(r["value"])
        series = []
        prev: dict[str, int | None] = {}
        for m in METRICS:
            before = [d for d in rows[m] if d < since]
            prev[m] = rows[m][max(before)] if before else None
        for i in range(days):
            d = since + i * 86400
            row: dict[str, Any] = {"day": d, "snapshot": any(d in rows[m] for m in METRICS)}
            for m in METRICS:
                name = m.split(".", 1)[1]
                v = rows[m].get(d)
                row[name] = v
                row[f"new_{name}"] = (v - prev[m]) if (v is not None and prev[m] is not None) else (0 if v is not None else None)
                if v is not None:
                    prev[m] = v
            series.append(row)
        latest = {str(r["key"]): int(r["value"]) for r in self.db.daily_latest("gh.downloads")}
        latest_day = self.db.daily_latest("gh.stars")
        assets = sorted(
            ({"name": k[6:], "downloads": v, "platform": platform_of_asset(k[6:])} for k, v in latest.items() if k.startswith("asset:")),
            key=lambda x: -x["downloads"],
        )
        releases = sorted(
            ({"tag": k[8:], "downloads": v} for k, v in latest.items() if k.startswith("release:")), key=lambda x: -x["downloads"]
        )
        platforms = {k: v for k, v in latest.items() if not k.startswith(("asset:", "release:")) and k != "total"}
        return {
            "status": self.status(),
            "days": series,
            "latest": {
                "day": int(latest_day[0]["day"]) if latest_day else None,
                "stars": int(latest_day[0]["value"]) if latest_day else None,
                "forks": _one(self.db.daily_latest("gh.forks")),
                "watchers": _one(self.db.daily_latest("gh.watchers")),
                "issues": _one(self.db.daily_latest("gh.issues")),
                "downloads": latest.get("total"),
                "platforms": platforms,
                "assets": assets,
                "releases": releases,
            },
        }

    # -- the task ----------------------------------------------------------------------------

    def start(self) -> None:
        """Begin the background loop: a run now, then every INTERVAL_S (the day's row is
        overwritten, so the last run of a UTC day is the day's figure)."""
        if not self.enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="github-collector")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — shutting down
                pass
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(3.0)  # let the server come up first
        while True:
            try:
                await self.collect()
            except Exception as e:  # noqa: BLE001 — the loop must not die
                log.warning("github: collector failed: %s", e)
            await asyncio.sleep(INTERVAL_S)


def _one(rows: list) -> int | None:
    return int(rows[0]["value"]) if rows else None
