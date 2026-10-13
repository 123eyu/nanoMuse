"""0.22: the GitHub collector against a mocked GitHub — snapshots per UTC day, the new-per-day
arithmetic across gaps, a failure that keeps the last snapshot, the series through the API."""

from __future__ import annotations

import json

import httpx
from test_accounts import make

from nanomuse_cloud.db import Database, utc_day
from nanomuse_cloud.github_stats import GitHubCollector, platform_of_asset

ADMIN = {"X-Admin-Token": "admin"}
DAY = 86400


class FakeGitHub:
    """The two endpoints the collector reads, with figures a test moves between runs."""

    def __init__(self):
        self.stars, self.forks, self.watchers, self.issues = 120, 7, 9, 3
        self.releases = [
            {
                "tag_name": "v0.21.0",
                "assets": [
                    {"name": "nanoMuse-0.21.0.apk", "download_count": 400},
                    {"name": "nanoMuse-0.21.0-arm64.dmg", "download_count": 50},
                    {"name": "nanoMuse-Setup-0.21.0.exe", "download_count": 30},
                ],
            },
            {"tag_name": "v0.20.0", "assets": [{"name": "nanoMuse-0.20.0.apk", "download_count": 1000}]},
        ]
        self.down = False
        self.rate_limited = False
        self.calls: list[str] = []

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.calls.append(str(request.url))
            assert request.headers.get("accept") == "application/vnd.github+json"
            if self.down:
                raise httpx.ConnectError("no route to GitHub")
            if self.rate_limited:
                return httpx.Response(403, headers={"x-ratelimit-remaining": "0"}, json={"message": "API rate limit exceeded"})
            path = request.url.path
            if path == "/repos/nano-muse/nanoMuse":
                return httpx.Response(
                    200,
                    json={
                        "stargazers_count": self.stars,
                        "forks_count": self.forks,
                        "subscribers_count": self.watchers,
                        "watchers_count": self.stars,  # GitHub's alias of the star count: must not be taken for watchers
                        "open_issues_count": self.issues,
                    },
                )
            if path == "/repos/nano-muse/nanoMuse/releases":
                page = int(request.url.params.get("page", "1"))
                return httpx.Response(200, json=self.releases if page == 1 else [])
            return httpx.Response(404, json={"message": "Not Found"})

        return httpx.MockTransport(handle)


def test_platform_of_asset():
    assert platform_of_asset("nanoMuse-0.21.0.apk") == "android"
    assert platform_of_asset("nanoMuse.ipa") == "ios"
    assert platform_of_asset("nanoMuse-0.21.0-arm64.dmg") == "mac"
    assert platform_of_asset("nanoMuse-Setup-0.21.0.exe") == "windows"
    assert platform_of_asset("nanoMuse-0.21.0.AppImage") == "linux"
    assert platform_of_asset("SHA256SUMS.txt") == "other"


async def test_snapshots_deltas_and_a_failure_that_keeps_the_last_one():
    gh = FakeGitHub()
    db = Database(":memory:")
    clock = {"t": 1_800_000_000}  # a fixed "now", moved by the test
    c = GitHubCollector(db, "nano-muse/nanoMuse", token="ghp-test", transport=gh.transport(), clock=lambda: clock["t"])
    assert c.enabled and c.status()["token"] is True and c.last_ok is None

    out = await c.collect()
    assert out["ok"] and out["stars"] == 120 and out["downloads"] == 1480
    day0 = utc_day(clock["t"])
    assert out["day"] == day0 and c.last_ok == clock["t"] and c.error == ""
    assert any("Bearer ghp-test" in str(h) for h in [c._headers()])
    # one row a day per metric; the download keys: the total, each platform, each asset, each release
    rows = {
        (m, r["key"]): int(r["value"])
        for m in ("gh.stars", "gh.forks", "gh.watchers", "gh.issues", "gh.downloads")
        for r in db.daily_rows(m)
    }
    assert rows[("gh.stars", "")] == 120 and rows[("gh.forks", "")] == 7 and rows[("gh.watchers", "")] == 9 and rows[("gh.issues", "")] == 3
    assert rows[("gh.downloads", "total")] == 1480 and rows[("gh.downloads", "android")] == 1400 and rows[("gh.downloads", "mac")] == 50
    assert rows[("gh.downloads", "asset:nanoMuse-0.21.0.apk")] == 400 and rows[("gh.downloads", "release:v0.20.0")] == 1000

    # a second run the same day overwrites the day's figure (the last run of a day stands)
    gh.stars = 125
    clock["t"] += 3600
    assert (await c.collect())["ok"]
    assert [int(r["value"]) for r in db.daily_rows("gh.stars")] == [125]

    # two days later (a day with no snapshot in between): new = today − the last snapshot
    gh.stars, gh.releases[0]["assets"][0]["download_count"] = 140, 520
    clock["t"] += 2 * DAY
    assert (await c.collect())["ok"]
    s = c.series(4)
    days = {r["day"]: r for r in s["days"]}
    assert days[day0]["stars"] == 125 and days[day0]["new_stars"] == 0 and days[day0]["snapshot"] is True
    assert days[day0 + DAY]["stars"] is None and days[day0 + DAY]["new_stars"] is None and days[day0 + DAY]["snapshot"] is False
    assert days[day0 + 2 * DAY]["stars"] == 140 and days[day0 + 2 * DAY]["new_stars"] == 15
    assert days[day0 + 2 * DAY]["downloads"] == 1600 and days[day0 + 2 * DAY]["new_downloads"] == 120
    assert s["latest"]["stars"] == 140 and s["latest"]["downloads"] == 1600 and s["latest"]["platforms"]["android"] == 1520
    assert s["latest"]["assets"][0] == {"name": "nanoMuse-0.20.0.apk", "downloads": 1000, "platform": "android"}
    assert [r["tag"] for r in s["latest"]["releases"]] == ["v0.20.0", "v0.21.0"]
    # the first shown day compares to the snapshot before the window
    s = c.series(1)
    assert s["days"][0]["new_stars"] == 15

    # GitHub unreachable: the error is kept and shown, the snapshot stands, nothing raises
    gh.down = True
    clock["t"] += DAY
    out = await c.collect()
    assert out["ok"] is False and "ConnectError" in out["error"]
    assert c.error and c.last_run == clock["t"] and c.last_ok == clock["t"] - DAY
    assert c.series(2)["days"][-1]["snapshot"] is False and c.series(2)["latest"]["stars"] == 140
    # ...and so is the rate limit, with a plain message
    gh.down, gh.rate_limited = False, True
    out = await c.collect()
    assert out["ok"] is False and "GITHUB_TOKEN" in out["error"]
    # the last run's state survives a new collector over the same database (a restart)
    again = GitHubCollector(db, "nano-muse/nanoMuse", transport=gh.transport(), clock=lambda: clock["t"])
    assert again.last_ok == c.last_ok and again.error == c.error
    db.close()


async def test_a_failed_write_does_not_leave_the_collector_marked_running():
    """An error past the fetch (here the database refusing the snapshot) used to leave
    `running` set, and every run after it, the six-hourly one and *Refresh now*, answered
    "already running" until a restart. The flag is let go whatever happens."""
    gh = FakeGitHub()
    db = Database(":memory:")
    c = GitHubCollector(db, "nano-muse/nanoMuse", transport=gh.transport(), clock=lambda: 1_800_000_000)
    kept = db.daily_set

    def refuse(*a, **k):
        raise RuntimeError("database is locked")

    db.daily_set = refuse  # type: ignore[method-assign]
    try:
        await c.collect()
    except RuntimeError:
        pass
    else:
        raise AssertionError("the write's error reaches the caller")
    assert c.running is False
    db.daily_set = kept  # type: ignore[method-assign]
    out = await c.collect()
    assert out["ok"] is True and out["stars"] == 120
    db.close()


async def test_no_repository_means_no_collector():
    db = Database(":memory:")
    c = GitHubCollector(db, "", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert c.enabled is False
    assert (await c.collect()) == {"ok": False, "error": "no repository configured"}
    assert c.series(3)["status"]["enabled"] is False
    db.close()


async def test_series_and_refresh_through_the_admin_api():
    gh = FakeGitHub()
    app, client, sender, up, cloud, settings = make(github_repo="nano-muse/nanoMuse")
    # the collector the app made reads the fake, not GitHub
    app.state.collector.transport = gh.transport()
    r = await client.get("/v1/admin/github", headers=ADMIN)
    assert r.status_code == 200 and r.json()["status"]["repo"] == "nano-muse/nanoMuse" and r.json()["latest"]["stars"] is None
    r = await client.post("/v1/admin/github/refresh", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["ok"] and r.json()["stars"] == 120 and r.json()["status"]["last_ok"]
    r = await client.get("/v1/admin/github?days=7", headers=ADMIN)
    s = r.json()
    assert len(s["days"]) == 7 and s["days"][-1]["stars"] == 120 and s["days"][-1]["new_stars"] == 0
    assert s["latest"]["downloads"] == 1480
    # the same figures under the statistics, with their CSV
    r = await client.get("/v1/admin/stats/github_daily?days=7", headers=ADMIN)
    assert r.status_code == 200 and r.json()["rows"][-1]["downloads"] == 1480
    r = await client.get("/v1/admin/stats/github_assets.csv", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    lines = r.text.splitlines()
    assert lines[0] == "asset,platform,downloads" and lines[1] == "nanoMuse-0.20.0.apk,android,1000"
    assert (await client.get("/v1/admin/github")).status_code == 401
    assert json.loads(json.dumps(s))  # plain JSON throughout
