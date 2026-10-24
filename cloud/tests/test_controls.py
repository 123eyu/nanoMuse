"""0.22: the operator's switches — what each endpoint answers while one is off — the
threshold rules (a rule fires once, re-arms, notifies) and the audit log behind them."""

from __future__ import annotations

import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.config import Settings
from nanomuse_cloud.controls import ControlError, Controls
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud

ADMIN = {"X-Admin-Token": "admin", "X-Admin-Actor": "tester"}
HELLO = {
    "type": "hello",
    "device": {"id": "phone-1", "name": "Pixel", "kind": "phone", "os": "TestOS", "version": "0.1", "actions": ["info"]},
}
CHAT = {"model": "qwen3.8-flash", "messages": [{"role": "user", "content": "hi"}]}


async def test_switches_start_on_and_flip_with_an_audit_line():
    app, client, sender, up, cloud, settings = make()
    r = await client.get("/v1/admin/controls", headers=ADMIN)
    assert r.status_code == 200, r.text
    view = r.json()
    assert set(view["switches"]) == {"free_allowance", "signups", "cloud_service", "sync", "hub", "apps_only"}
    assert all(s["enabled"] for s in view["switches"].values()) and view["audit"] == []

    r = await client.post("/v1/admin/controls/signups", json={"enabled": False, "note": "launch day"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json()["switches"]["signups"] == {
        "enabled": False,
        "actor": "tester",
        "note": "launch day",
        "updated_at": r.json()["switches"]["signups"]["updated_at"],
    }
    audit = (await client.get("/v1/admin/controls/audit", headers=ADMIN)).json()["audit"]
    assert len(audit) == 1 and audit[0]["actor"] == "tester" and audit[0]["action"] == "switch" and audit[0]["target"] == "signups"
    assert "off" in audit[0]["detail"] and "launch day" in audit[0]["detail"]
    # the dashboard, the health check and the public figures all say so
    assert (await client.get("/healthz")).json()["paused"] == ["signups"]
    assert (await client.get("/v1/config")).json()["paused"] == ["signups"]
    assert (await client.get("/v1/config")).json()["signup_open"] is False
    assert (await client.get("/v1/admin/accounts", headers=ADMIN)).json()["settings"]["paused"] == ["signups"]

    r = await client.post("/v1/admin/controls/nothing", json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"
    r = await client.post("/v1/admin/controls/signups", json={}, headers=ADMIN)
    assert r.status_code == 400
    assert (await client.get("/v1/admin/controls")).status_code == 401  # the token is required


def test_switches_survive_a_restart(tmp_path):
    path = str(tmp_path / "relay.sqlite")
    settings = Settings(database=path, secret="test-secret", admin_token="admin")
    cloud = Cloud(settings, Database(path), LogSender())
    cloud.controls.set("free_allowance", False, actor="ops", note="budget")
    cloud.controls.add_rule({"threshold": 100, "action": "notify"}, actor="ops")
    cloud.db.close()
    again = Cloud(settings, Database(path), LogSender())
    assert again.controls.on("free_allowance") is False and again.controls.on("signups") is True
    assert again.controls.state()["free_allowance"]["actor"] == "ops"
    assert [r["threshold"] for r in again.controls.rules()] == [100]
    assert len(again.controls.audit()) == 2
    again.db.close()


async def test_signups_closed_turns_new_identifiers_away_but_not_existing_accounts():
    app, client, sender, up, cloud, settings = make()
    existing = await sign_up(client, sender, "13800138000", "pixel")
    cloud.controls.set("signups", False)
    # a new number gets no code and no account
    r = await client.post("/v1/auth/code", json={"identifier": "13900139000"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "signup_closed"
    assert "paused" in r.json()["error"]["message"]
    # the account that exists signs in again, by code and by password, and uses the models
    again = await sign_up(client, sender, "13800138000", "mac")
    assert again["account"]["id"] == existing["account"]["id"]
    r = await client.post("/v1/chat/completions", json=CHAT, headers=auth(again["api_key"]))
    assert r.status_code == 200, r.text
    # the older sign-up gate (SIGNUP_OPEN) is untouched: this is a different, temporary no
    assert cloud.s.signup_open is True
    cloud.controls.set("signups", True)
    assert (await client.post("/v1/auth/code", json={"identifier": "13900139000"})).status_code == 204


async def test_free_allowance_off_refuses_limited_accounts_in_the_exhausted_shape():
    app, client, sender, up, cloud, settings = make(allowed_identifiers="13800138000")
    member = await sign_up(client, sender, "13800138000", "pixel")
    person = await sign_up(client, sender, "dev-a@example.com", "mac")
    cloud.controls.set("free_allowance", False, actor="ops")
    r = await client.post("/v1/chat/completions", json=CHAT, headers=auth(person["api_key"]))
    assert r.status_code == 429, r.text
    err = r.json()["error"]
    # the code every app of today knows, with the flags a newer one reads
    assert err["code"] == "allowance_exhausted" and err["paused"] is True and err["reason"] == "allowance_paused"
    assert "paused" in err["message"] and "ways" in err and "guidance" in err
    # the person's sign-in and account page keep working, and say what is off
    me = (await client.get("/v1/me", headers=auth(person["api_key"]))).json()
    assert me["paused"] == ["free_allowance"]
    # a member is not limited and is not refused
    r = await client.post("/v1/chat/completions", json=CHAT, headers=auth(member["api_key"]))
    assert r.status_code == 200, r.text
    cloud.controls.set("free_allowance", True, actor="ops")
    r = await client.post("/v1/chat/completions", json=CHAT, headers=auth(person["api_key"]))
    assert r.status_code == 200, r.text


async def test_apps_only_refuses_other_software_at_the_models_and_nothing_else():
    """0.28: with the switch on (the default) a pool worker, an SDK or the unbranded upstream
    app on a nanoMuse key is refused the models with a 403 that names the client and says
    where to go; the apps, the runtime, the browser console and members are answered; the
    sign-in, the account page and the allowance are untouched; the refusal is one `calls`
    row and one timeline event, never a budget refusal. Off, everything is answered."""
    app, client, sender, up, cloud, settings = make(allowed_identifiers="13800138000")
    member = await sign_up(client, sender, "13800138000", "pixel")
    person = await sign_up(client, sender, "dev-a@example.com", "mac")
    key = auth(person["api_key"])
    for ua in (
        "Go-http-client/1.1",
        "nanoMuse-Pool-Worker/1.0",
        "OpenAI/Python 1.5",
        "python-requests/2.32",
        "",
        "Minis/1.14 (iOS 26; iPhone)",
    ):
        r = await client.post("/v1/chat/completions", json=CHAT, headers={**key, "User-Agent": ua})
        assert r.status_code == 403, (ua, r.text)
        err = r.json()["error"]
        assert err["code"] == "client_not_supported" and "nanoMuse apps" in err["message"] and "key of your own" in err["message"]
        assert err["client"] == (ua.split("/")[0] or "(empty)")
    # the refusals cost nothing and the account page still answers the refused client
    me = (await client.get("/v1/me", headers={**key, "User-Agent": "Go-http-client/1.1"})).json()
    assert me["spend"]["total"] == 0 and me["paused"] == []
    # the apps, the runtime and the web console in a browser are answered
    for ua in (
        "nanoMuse/1.0.6 (Android 15; Pixel 9)",
        "nanoMuse/1.0.6 (iOS 26.1; iPhone)",
        "nanoMuse-Android/1.0.6",
        "deepseek-harness/1.2.0",
        "nanoMuse-Desktop/1.0.6",
        "nanoMuse/1.0.6",
        "AsyncOpenAI/Python 1.109.1",  # the runtime of 1.0.6 and earlier
        "Mozilla/5.0 (Macintosh) AppleWebKit/605 Safari/605",
    ):
        r = await client.post("/v1/chat/completions", json=CHAT, headers={**key, "User-Agent": ua})
        assert r.status_code == 200, (ua, r.text)
    # a member may call from whatever they like
    r = await client.post("/v1/chat/completions", json=CHAT, headers={**auth(member["api_key"]), "User-Agent": "Go-http-client/1.1"})
    assert r.status_code == 200, r.text
    # a refusal is its own event, never a budget refusal
    events = (await client.get("/v1/me/events", headers=key)).json()["events"]
    kinds = [e["kind"] for e in events]
    assert kinds.count("client.refused") == 6 and "budget.refused" not in kinds
    assert [e["detail"] for e in events if e["kind"] == "client.refused"][-1] == "Go-http-client"
    refused = [c for c in cloud.db.calls_since(0) if c["status"] == "refused"]
    assert len(refused) == 6 and {c["detail"] for c in refused} == {"client_not_supported"}
    assert all(c["prompt_tokens"] == 0 for c in refused)
    # the Non-app traffic page shows whom the switch turned away, by family
    abuse = (await client.get("/v1/admin/analytics/abuse?days=1", headers=ADMIN)).json()
    assert abuse["apps_only"] is True
    assert {(r["family"], r["requests"], r["accounts"]) for r in abuse["refused"]} == {
        ("Go-http-client", 1, 1),
        ("nanoMuse-Pool-Worker", 1, 1),
        ("OpenAI", 1, 1),
        ("python-requests", 1, 1),
        ("(empty)", 1, 1),
        ("Minis", 1, 1),
    }
    # the switch is a policy, not a pause: the health check does not list it
    assert (await client.get("/healthz")).json()["paused"] == []
    # off: any client with a valid key is answered
    r = await client.post("/v1/admin/controls/apps_only", json={"enabled": False, "note": "a developer's week"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    assert (await client.get("/healthz")).json()["paused"] == []
    r = await client.post("/v1/chat/completions", json=CHAT, headers={**key, "User-Agent": "Go-http-client/1.1"})
    assert r.status_code == 200, r.text
    assert (await client.get("/v1/admin/analytics/abuse?days=1", headers=ADMIN)).json()["apps_only"] is False


async def test_cloud_service_off_answers_503_everywhere_but_the_console():
    app, client, sender, up, cloud, settings = make()
    person = await sign_up(client, sender, "13800138000", "pixel")
    r = await client.post("/v1/admin/controls/cloud_service", json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["sockets_closed"] == 0
    for method, path, body in (
        ("GET", "/v1/me", None),
        ("POST", "/v1/chat/completions", CHAT),
        ("GET", "/v1/models", None),
        ("POST", "/v1/auth/code", {"identifier": "13900139000"}),
        ("GET", "/v1/sync/state", None),
        ("GET", "/v1/devices", None),
    ):
        r = await client.request(method, path, json=body, headers=auth(person["api_key"]))
        assert r.status_code == 503, (path, r.text)
        assert r.json()["error"]["code"] == "service_paused" and r.json()["error"]["paused"] is True
    # what still answers: the health check, the public figures, the console's API
    assert (await client.get("/healthz")).json() == {**(await client.get("/healthz")).json(), "paused": ["cloud_service"]}
    assert (await client.get("/v1/config")).json()["paused"] == ["cloud_service"]
    assert (await client.get("/v1/admin/overview", headers=ADMIN)).status_code == 200
    r = await client.post("/v1/admin/controls/cloud_service", json={"enabled": True}, headers=ADMIN)
    assert r.status_code == 200
    assert (await client.get("/v1/me", headers=auth(person["api_key"]))).status_code == 200


async def test_sync_off_keeps_the_state_readable_and_refuses_the_rest():
    app, client, sender, up, cloud, settings = make()
    person = await sign_up(client, sender, "13800138000", "pixel")
    key = auth(person["api_key"])
    assert (await client.get("/v1/sync/state", headers=key)).json()["paused"] is False
    cloud.controls.set("sync", False)
    assert (await client.get("/v1/sync/state", headers=key)).json()["paused"] is True
    r = await client.get("/v1/sync/changes", headers=key)
    assert r.status_code == 503 and r.json()["error"]["code"] == "sync_paused"
    r = await client.post("/v1/sync/changes", json={"device": "pixel", "conversations": [], "messages": []}, headers=key)
    assert r.status_code == 503 and r.json()["error"]["code"] == "sync_paused"
    # the models are not sync: they answer
    assert (await client.post("/v1/chat/completions", json=CHAT, headers=key)).status_code == 200


def test_hub_off_closes_sockets_with_4003_and_refuses_new_ones():
    settings = Settings(database=":memory:", secret="test-secret", admin_token="admin", public_base="http://cloud.test")
    sender = LogSender()
    cloud = Cloud(settings, Database(":memory:"), sender)
    app = create_app(settings, cloud)
    with TestClient(app) as c:
        assert c.post("/v1/auth/code", json={"identifier": "13800138000"}).status_code == 204
        _, code = sender.sent[-1]
        key = c.post("/v1/auth/verify", json={"identifier": "13800138000", "code": code, "device": "test"}).json()["api_key"]
        with c.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
            ws.send_json(HELLO)
            assert ws.receive_json()["type"] == "welcome"
            r = c.post("/v1/admin/controls/hub", json={"enabled": False}, headers=ADMIN)
            assert r.status_code == 200 and r.json()["sockets_closed"] == 1
            with pytest.raises(WebSocketDisconnect) as closed:
                for _ in range(5):  # a `devices` broadcast may still be queued ahead of the close
                    ws.receive_json()
            assert closed.value.code == 4003 and closed.value.reason == "hub_paused"
        with pytest.raises(WebSocketDisconnect) as refused:
            with c.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
                ws.receive_json()
        assert refused.value.code == 4003
        r = c.get("/v1/devices", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 503 and r.json()["error"]["code"] == "hub_paused"
        assert c.post("/v1/admin/controls/hub", json={"enabled": True}, headers=ADMIN).status_code == 200
        with c.websocket_connect("/v1/hub", headers={"Authorization": f"Bearer {key}"}) as ws:
            ws.send_json(HELLO)
            assert ws.receive_json()["type"] == "welcome"


def test_rules_fire_once_flip_their_switch_and_rearm():
    sent: list = []
    settings = Settings(database=":memory:", secret="test-secret", admin_email="ops@example.com", smtp_from="relay@example.com")
    db = Database(":memory:")
    ctl = Controls(db, settings, mailer=sent.append)
    r1 = ctl.add_rule({"threshold": 3, "action": "close_signups", "note": "beta"}, actor="ops")
    r2 = ctl.add_rule({"threshold": 5, "action": "notify"}, actor="ops")
    assert r1["enabled"] and r1["last_fired_at"] is None
    assert ctl.view(1)["next_threshold"] == 3
    # below the line: nothing
    assert ctl.evaluate(2) == [] and ctl.on("signups")
    # at the line: once
    fired = ctl.evaluate(3)
    assert [r["id"] for r in fired] == [r1["id"]] and ctl.on("signups") is False
    assert ctl.rules()[0]["last_fired_at"] and ctl.rules()[0]["fired_accounts"] == 3
    assert ctl.evaluate(4) == [], "a rule that has fired stays quiet"
    assert ctl.view(4)["next_threshold"] == 5
    # the switch was flipped by the rule, says the log
    log = ctl.audit()
    assert any(a["action"] == "rule.fired" and a["target"] == str(r1["id"]) for a in log)
    assert any(a["action"] == "switch" and a["target"] == "signups" and a["actor"] == f"rule:{r1['id']}" for a in log)
    # notify goes through the mailer, in both languages, and leaves a line
    fired = ctl.evaluate(5)
    assert [r["id"] for r in fired] == [r2["id"]] and len(sent) == 1
    msg = sent[0]
    assert msg["To"] == "ops@example.com" and "5" in msg.get_content() and "账号" in msg.get_content()
    assert any(a["action"] == "notify" and "sent" in a["detail"] for a in ctl.audit())
    # the operator turns sign-ups back on and re-arms the rule: it can fire again
    ctl.set("signups", True, actor="ops")
    ctl.update_rule(r1["id"], {"rearm": True}, actor="ops")
    assert ctl.rules()[0]["last_fired_at"] is None
    assert [r["id"] for r in ctl.evaluate(10)] == [r1["id"]] and ctl.on("signups") is False
    # a disabled rule never fires; a changed threshold re-arms by itself
    ctl.update_rule(r2["id"], {"enabled": False, "threshold": 6}, actor="ops")
    assert ctl.evaluate(100) == []
    ctl.update_rule(r2["id"], {"enabled": True}, actor="ops")
    assert [r["id"] for r in ctl.evaluate(100)] == [r2["id"]] and len(sent) == 2
    ctl.delete_rule(r2["id"], actor="ops")
    assert len(ctl.rules()) == 1
    for bad in ({"threshold": 0, "action": "notify"}, {"threshold": 5, "action": "explode"}, {"threshold": "x", "action": "notify"}):
        with pytest.raises(ControlError):
            ctl.add_rule(bad)
    db.close()


def test_notify_without_an_address_is_recorded_not_sent():
    settings = Settings(database=":memory:", secret="test-secret")
    db = Database(":memory:")
    ctl = Controls(db, settings)
    assert ctl.can_notify is False
    assert ctl.notify("test", ["line"]) is False
    assert ctl.audit()[0]["action"] == "notify" and "ADMIN_EMAIL" in ctl.audit()[0]["detail"]
    db.close()


async def test_rules_through_the_api_fire_on_sign_up():
    app, client, sender, up, cloud, settings = make()
    r = await client.post(
        "/v1/admin/controls/rules", json={"threshold": 2, "action": "close_signups", "note": "two is plenty"}, headers=ADMIN
    )
    assert r.status_code == 200, r.text
    rule_id = r.json()["id"]
    await sign_up(client, sender, "13800138000", "pixel")
    assert cloud.controls.on("signups") is True
    await sign_up(client, sender, "13800138001", "pixel")  # the second account reaches the line
    assert cloud.controls.on("signups") is False
    r = await client.post("/v1/auth/code", json={"identifier": "13800138002"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "signup_closed"
    view = (await client.get("/v1/admin/controls", headers=ADMIN)).json()
    rule = next(x for x in view["rules"] if x["id"] == rule_id)
    assert rule["last_fired_at"] and rule["fired_accounts"] == 2 and view["accounts_total"] == 2
    assert any(a["action"] == "rule.fired" for a in view["audit"])
    # the minute timer's check, by hand: nothing more to fire
    r = await client.post("/v1/admin/controls/evaluate", headers=ADMIN)
    assert r.status_code == 200 and r.json()["fired"] == []
    r = await client.put(f"/v1/admin/controls/rules/{rule_id}", json={"enabled": False}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["enabled"] is False
    assert (await client.delete(f"/v1/admin/controls/rules/{rule_id}", headers=ADMIN)).status_code == 204
    assert (await client.delete(f"/v1/admin/controls/rules/{rule_id}", headers=ADMIN)).status_code == 404
    r = await client.post("/v1/admin/controls/rules", json={"threshold": -1, "action": "notify"}, headers=ADMIN)
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_request"


async def test_a_notify_rule_crossed_by_a_sign_in_mails_off_the_request_path():
    """The sign-in that reaches the line gets its key back without waiting on SMTP: the
    switch work happens in the request, the e-mail on a thread of its own (or, failing
    that, on the minute timer's next tick)."""
    app, client, sender, up, cloud, settings = make(admin_email="ops@example.com", smtp_from="relay@example.com")
    sent: list = []
    gate = threading.Event()

    def slow_mailer(msg):
        gate.wait(5)  # the mail server is slow; the sign-in must not be
        sent.append(msg)

    cloud.controls._mailer = slow_mailer
    r = await client.post("/v1/admin/controls/rules", json={"threshold": 1, "action": "notify"}, headers=ADMIN)
    assert r.status_code == 200, r.text
    t0 = time.monotonic()
    await sign_up(client, sender, "13800138000", "pixel")
    assert time.monotonic() - t0 < 2.0, "the sign-in waited on the mail server"
    view = (await client.get("/v1/admin/controls", headers=ADMIN)).json()
    assert view["rules"][0]["last_fired_at"], "the rule fired inside the request"
    assert sent == []
    gate.set()
    for _ in range(50):
        if sent:
            break
        await asyncio.sleep(0.05)
    assert len(sent) == 1 and sent[0]["To"] == "ops@example.com"
    audit = (await client.get("/v1/admin/controls/audit", headers=ADMIN)).json()["audit"]
    assert any(a["action"] == "notify" and "sent" in a["detail"] for a in audit)
    # the minute timer's safety net: nothing left to send, and it says so
    assert cloud.controls.flush_notices() == 0 and cloud.rules_tick() == []


def test_deferred_notices_are_queued_then_flushed_in_order():
    sent: list = []
    settings = Settings(database=":memory:", secret="test-secret", admin_email="ops@example.com", smtp_from="relay@example.com")
    db = Database(":memory:")
    ctl = Controls(db, settings, mailer=sent.append)
    ctl.add_rule({"threshold": 2, "action": "notify"}, actor="ops")
    ctl.add_rule({"threshold": 3, "action": "notify"}, actor="ops")
    assert len(ctl.evaluate(3, defer_mail=True)) == 2 and sent == []
    assert ctl.flush_notices_later() is True
    for _ in range(100):
        if len(sent) == 2:
            break
        time.sleep(0.02)
    assert [m["Subject"].endswith(f"3 accounts: rule #{i} fired") for i, m in enumerate(sent, 1)] == [True, True]
    assert ctl.flush_notices() == 0 and ctl.flush_notices_later() is False
    db.close()


async def test_notify_test_answers_honestly_without_an_address():
    app, client, sender, up, cloud, settings = make()
    r = await client.post("/v1/admin/controls/notify-test", headers=ADMIN)
    assert r.status_code == 200, r.text
    assert r.json() == {"sent": False, "configured": False}
    audit = (await client.get("/v1/admin/controls/audit", headers=ADMIN)).json()["audit"]
    assert audit[0]["action"] == "notify" and audit[0]["actor"] == "tester"


def test_controls_events_land_on_the_timeline():
    settings = Settings(database=":memory:", secret="test-secret")
    db = Database(":memory:")
    ctl = Controls(db, settings)
    ctl.set("hub", False, actor="ops", note="maintenance")
    kinds = [r["kind"] for r in db.events_recent(10)]
    assert kinds == ["control.switch"]
    assert int(db.events_recent(10)[0]["ts"]) <= int(time.time())
    db.close()
