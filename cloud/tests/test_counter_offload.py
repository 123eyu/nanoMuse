"""Counter contention must not stall unrelated requests or split error/API accounting."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections import Counter

import anyio
import httpx
import pytest

from nanomuse_cloud import client as client_info
from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud


@pytest.fixture
async def relay(tmp_path):
    settings = Settings(database=str(tmp_path / "relay.db"), secret="fixture-secret", github_collect=False, geoip_enabled=False)
    db = Database(settings.database)
    app = create_app(settings, Cloud(settings, db, LogSender()))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://relay.test") as client:
        yield app, client, db
    await app.state.http.aclose()
    db.close()


def count(db, metric, key):
    return sum(row["value"] for row in db.daily_rows(metric, key=key))


async def wait_for(event):
    assert await asyncio.to_thread(event.wait, 3), "worker did not reach the expected point"


@pytest.mark.parametrize("lock_kind", ["connection", "sqlite_writer"])
async def test_a_counter_wait_does_not_block_static_requests(relay, monkeypatch, lock_kind):
    app, client, db = relay
    held, entered, release = threading.Event(), threading.Event(), threading.Event()
    guard_expired = []
    original = db.daily_add

    def hold():
        if lock_kind == "connection":
            with db._lock:
                held.set()
                if not release.wait(2):
                    guard_expired.append(True)
        else:
            conn = sqlite3.connect(app.state.settings.database, isolation_level=None)
            try:
                conn.execute("BEGIN IMMEDIATE")
                held.set()
                if not release.wait(2):
                    guard_expired.append(True)
                conn.rollback()
            finally:
                conn.close()

    def observed(metric, key, *args, **kwargs):
        entered.set()
        return original(metric, key, *args, **kwargs)

    monkeypatch.setattr(db, "daily_add", observed)
    holder = threading.Thread(target=hold)
    holder.start()
    await wait_for(held)
    request = asyncio.create_task(client.get("/healthz"))
    try:
        await wait_for(entered)
        response = await asyncio.wait_for(client.get("/app/"), 1)
        assert response.status_code == 200
        assert not guard_expired, "counter write blocked the event loop until the lock guard expired"
        assert not request.done(), "the health request must still await its counter"
    finally:
        release.set()
        holder.join(3)
        assert not holder.is_alive()
        response = await request
    assert response.status_code == 200
    assert count(db, "api", "health") == 1
    assert sum(row["value"] for row in db.daily_rows("api")) == 1


async def test_concurrent_success_and_errors_keep_counts_and_client_context(relay, monkeypatch):
    _, client, db = relay
    original = db.daily_add
    observed = []
    outer_context = client_info.current()

    def observe(metric, key, *args, **kwargs):
        observed.append((metric, key, client_info.current().ua))
        return original(metric, key, *args, **kwargs)

    monkeypatch.setattr(db, "daily_add", observe)
    requests = [("/healthz", 200), ("/missing", 404), ("/v1/me", 401)] * 4
    responses = await asyncio.gather(*(client.get(path, headers={"User-Agent": f"fixture-{i}"}) for i, (path, _) in enumerate(requests)))
    assert [r.status_code for r in responses] == [status for _, status in requests]
    assert count(db, "api", "health") == count(db, "api", "other") == count(db, "api", "account") == 4
    assert count(db, "error", "http_404") == count(db, "error", "bad_key") == 4
    assert Counter(ua for _, _, ua in observed) == Counter(
        {f"fixture-{i}": 1 if status == 200 else 2 for i, (_, status) in enumerate(requests)}
    )
    assert client_info.current() == outer_context


async def test_counter_failure_still_propagates(relay, monkeypatch):
    _, client, db = relay

    def failed(*args, **kwargs):
        raise sqlite3.OperationalError("fixture write failed")

    monkeypatch.setattr(db, "daily_add", failed)
    with pytest.raises(sqlite3.OperationalError, match="fixture write failed"):
        await client.get("/healthz")
    assert db.daily_rows("api") == []


@pytest.mark.parametrize("cancellation", ["task", "scope"])
async def test_started_error_accounting_finishes_both_counters_on_cancel(relay, monkeypatch, cancellation):
    _, client, db = relay
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    guard_expired = []
    original = db.daily_add

    def gated(metric, key, *args, **kwargs):
        if metric == "error":
            entered.set()
            if not release.wait(2):
                guard_expired.append(True)
        original(metric, key, *args, **kwargs)
        if metric == "api":
            finished.set()

    monkeypatch.setattr(db, "daily_add", gated)
    scope = anyio.CancelScope()

    async def fetch():
        with scope:
            await client.get("/missing")

    request = asyncio.create_task(fetch())
    try:
        await wait_for(entered)
        assert not guard_expired, "event loop could not cancel while the counter was waiting"
        if cancellation == "task":
            request.cancel()
        else:
            scope.cancel()
        await asyncio.sleep(0)
    finally:
        release.set()
        outcomes = await asyncio.gather(request, return_exceptions=True)
        await wait_for(finished)
    assert count(db, "error", "http_404") == 1
    assert count(db, "api", "other") == 1
    if cancellation == "task":
        assert isinstance(outcomes[0], asyncio.CancelledError)
    else:
        assert outcomes == [None]
