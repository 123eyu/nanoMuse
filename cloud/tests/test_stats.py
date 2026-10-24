"""0.22: the console's statistics — the daily counters written as requests pass (and kept
across a restart), every metric's rows and CSV from one definition, the CLI's admin commands."""

from __future__ import annotations

import csv
import io
import time

import httpx
from fastapi.testclient import TestClient
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.__main__ import main as cli_main
from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database, utc_day
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud
from nanomuse_cloud.stats import METRICS, api_group

ADMIN = {"X-Admin-Token": "admin"}
CHAT = {"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]}


def test_api_groups():
    assert api_group("/v1/auth/code") == "auth" and api_group("/v1/me") == "account" and api_group("/v1/chat/completions") == "chat"
    assert api_group("/v1/sync/changes") == "sync" and api_group("/v1/admin/stats") == "admin" and api_group("/healthz") == "health"
    assert api_group("/api/v1/services/aigc/video-generation/video-synthesis") == "video" and api_group("/nothing") == "other"


def test_every_metric_is_defined_once_with_both_languages_and_a_how():
    ids = [m.id for m in METRICS]
    assert len(ids) == len(set(ids))
    for m in METRICS:
        assert m.title and m.title_zh and m.how and m.how_zh and m.shape in ("series", "table", "facts")
        assert m.columns and all(c.label and c.label_zh for c in m.columns)
        if m.shape == "series":
            assert m.columns[0].key == "day"


async def test_counters_rows_and_csv_through_the_api():
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "13800138000", "pixel")
    b = await sign_up(client, sender, "dev-a@example.com", "mac")
    assert (await client.post("/v1/chat/completions", json=CHAT, headers=auth(a["api_key"]))).status_code == 200
    assert (await client.post("/v1/chat/completions", json={**CHAT, "model": "boom"}, headers=auth(a["api_key"]))).status_code == 404
    assert (await client.get("/v1/me", headers=auth(b["api_key"]))).status_code == 200
    assert (await client.get("/v1/me", headers={"Authorization": "Bearer nm_nothing"})).status_code == 401
    assert (await client.get("/v1/sync/changes", headers=auth(b["api_key"]))).status_code == 200
    r = await client.post(
        "/v1/sync/changes", json={"device": "mac", "conversations": [], "messages": [{"mid": "bad"}]}, headers=auth(b["api_key"])
    )
    assert r.status_code == 200, r.text

    r = await client.get("/v1/admin/stats?days=3", headers=ADMIN)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["days"] == 3 and out["since"] == utc_day(int(time.time())) - 2 * 86400
    by_id = {m["id"]: m for m in out["metrics"]}
    assert set(by_id) == {m.id for m in METRICS}
    today = utc_day(int(time.time()))

    acc = {r["day"]: r for r in by_id["accounts_daily"]["rows"]}
    assert len(acc) == 3 and acc[today]["new"] == 2 and acc[today]["total"] == 2 and acc[today - 86400]["total"] == 0
    assert acc[today]["active_any"] == 2, "both accounts made an authenticated call"
    assert acc[today]["active_model"] == 1, "one of them asked a model"
    assert acc[today]["sign_ins"] == 2

    api = {r["day"]: r for r in by_id["api_daily"]["rows"]}[today]
    assert (
        api["auth"] == 4 and api["chat"] == 2 and api["account"] == 2 and api["sync"] == 2
    )  # the stats call itself is counted after it has answered
    assert api["total"] == sum(v for k, v in api.items() if k not in ("day", "total"))

    errs = {r["day"]: r for r in by_id["errors_daily"]["rows"]}[today]
    assert errs["bad_key"] == 1 and errs["model_not_offered"] == 1 and errs["total"] == 2
    assert [c["key"] for c in by_id["errors_daily"]["columns"]][:2] == ["day", "total"]

    sync = {r["day"]: r for r in by_id["sync_daily"]["rows"]}[today]
    assert sync["pulls"] == 1 and sync["pushes"] == 1 and sync["messages_in"] == 1 and sync["rejected"] == 1

    calls = {r["day"]: r for r in by_id["calls_daily"]["rows"]}[today]
    assert calls["requests"] == 1 and calls["chat"] == 1 and calls["tokens"] == calls["prompt_tokens"] + calls["completion_tokens"] > 0

    method = {r["method"]: r for r in by_id["accounts_by_method"]["rows"]}
    assert method["phone"]["accounts"] == 1 and method["email"]["accounts"] == 1
    origin = {(r["method"], r["origin"]): r["accounts"] for r in by_id["accounts_by_origin"]["rows"]}
    assert origin[("phone", "+86")] == 1 and origin[("email", "example.com")] == 1
    top = by_id["top_accounts"]["rows"]
    assert len(top) == 1 and top[0]["identifier"] == "+8613800138000" and top[0]["requests"] == 1
    dist = {r["used"]: r["accounts"] for r in by_id["allowance_distribution"]["rows"]}
    assert dist["0%"] == 1 and dist["1–49%"] == 1 and sum(dist.values()) == 2
    devices = {r["devices"]: r["accounts"] for r in by_id["devices_per_account"]["rows"]}
    assert devices["0"] == 2
    facts = {r["fact"]: r["value"] for r in by_id["relay"]["rows"]}
    assert facts["version"] and facts["db_writable"] == "yes" and facts["paused"] == "none" and "h" in facts["uptime"]
    # how it is computed rides with every metric, in both languages
    assert all(m["how"] and m["how_zh"] for m in out["metrics"])

    # the CSV: the same rows, days as dates, money with four decimals
    r = await client.get("/v1/admin/stats/accounts_daily.csv?days=3", headers=ADMIN)
    assert r.status_code == 200 and r.headers["content-disposition"].endswith('"accounts_daily.csv"')
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["day", "total", "new", "active_any", "active_model", "sign_ins", "sign_in_failures", "deleted"]
    assert len(rows) == 4 and rows[-1][0] == time.strftime("%Y-%m-%d", time.gmtime(today)) and rows[-1][1:3] == ["2", "2"]
    r = await client.get("/v1/admin/stats/calls_daily.csv?days=1", headers=ADMIN)
    assert r.text.splitlines()[1].split(",")[5].count(".") == 1
    r = await client.get("/v1/admin/stats/nothing.csv", headers=ADMIN)
    assert r.status_code == 404
    assert (await client.get("/v1/admin/stats")).status_code == 401


def test_counters_survive_a_restart(tmp_path):
    path = str(tmp_path / "relay.sqlite")
    db = Database(path)
    db.daily_add("api", "chat")
    db.daily_add("api", "chat", 2)
    db.daily_add("error", "bad_key")
    db.daily_add("active", "acc_1")
    db.daily_add("active", "acc_1")
    db.daily_add("active", "acc_2")
    db.close()
    db = Database(path)
    today = utc_day(int(time.time()))
    assert [(int(r["day"]), r["key"], int(r["value"])) for r in db.daily_rows("api")] == [(today, "chat", 3)]
    assert [(int(r["n"]), int(r["total"])) for r in db.daily_distinct("active")] == [(2, 3)]
    assert int(db.daily_rows("error")[0]["value"]) == 1
    db.close()


def test_cli_controls_talk_to_the_admin_api(monkeypatch, capsys):
    settings = Settings(database=":memory:", secret="test-secret", admin_token="admin", public_base="http://cloud.test")
    cloud = Cloud(settings, Database(":memory:"), LogSender())
    app = create_app(settings, cloud)
    with TestClient(app) as c:
        # the CLI's httpx client is pointed at the test app: a transport that replays each request through the TestClient
        def relay(request: httpx.Request) -> httpx.Response:
            r = c.request(request.method, request.url.raw_path.decode(), headers=dict(request.headers), content=request.content)
            return httpx.Response(r.status_code, headers=dict(r.headers), content=r.content)

        real_client = httpx.Client
        monkeypatch.setattr(httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(relay), **kw))
        cli_main(
            [
                "admin",
                "--base",
                "http://cloud.test",
                "--token",
                "admin",
                "--actor",
                "cli:tester",
                "controls",
                "set",
                "free_allowance",
                "off",
                "--note",
                "budget",
            ]
        )
        assert cloud.controls.on("free_allowance") is False
        cli_main(["admin", "--base", "http://cloud.test", "--token", "admin", "controls", "list"])
        out = capsys.readouterr().out
        assert "free_allowance  OFF" in out and "signups         on" in out
        cli_main(["admin", "--base", "http://cloud.test", "--token", "admin", "controls", "audit"])
        out = capsys.readouterr().out
        assert "cli:tester" in out and "budget" in out
        assert c.get("/healthz").json()["paused"] == ["free_allowance"]


async def test_the_historical_total_counts_the_accounts_deleted_since():
    """An account opened yesterday and deleted today was there at yesterday's end: the total
    for that day says so (the `account.deleted` line outlives the account)."""
    app, client, sender, up, cloud, settings = make()
    a = await sign_up(client, sender, "13800138000", "pixel")
    await sign_up(client, sender, "dev-a@example.com", "mac")
    today = utc_day(int(time.time()))
    with cloud.db.tx() as c:
        c.execute("UPDATE accounts SET created_at=? WHERE id=?", (today - 86400 + 3600, a["account"]["id"]))
    assert (await client.post("/v1/admin/delete", headers=ADMIN, json={"account_id": a["account"]["id"]})).status_code == 204
    r = await client.get("/v1/admin/stats?days=3", headers=ADMIN)
    acc = {r["day"]: r for r in next(m for m in r.json()["metrics"] if m["id"] == "accounts_daily")["rows"]}
    assert acc[today]["total"] == 1 and acc[today]["deleted"] == 1 and acc[today]["new"] == 1
    assert acc[today - 86400]["total"] == 1 and acc[today - 86400]["new"] == 1
    assert acc[today - 2 * 86400]["total"] == 0
