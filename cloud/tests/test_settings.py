"""Relay 0.15: the allowance, the invite bonus and sign-up set from the operator's page while
the relay runs — in force at once, kept across restarts, applied to older accounts on request;
and the public figures at /v1/config that the apps print instead of numbers of their own."""

from __future__ import annotations

import httpx
from test_accounts import auth, make
from test_cloud import sign_up

from nanomuse_cloud.api import create_app
from nanomuse_cloud.db import Database
from nanomuse_cloud.senders import LogSender
from nanomuse_cloud.service import Cloud

# the tests call the models as one of the apps would; `apps_only` (0.28) refuses other software
APP_UA = {"User-Agent": "nanoMuse/0.0 (tests)"}

ADMIN = {"X-Admin-Token": "admin"}


async def test_public_config_needs_no_auth_and_follows_the_settings():
    app, client, sender, up, cloud, settings = make(allowance_cny=25, invite_bonus_cny=5)
    r = await client.get("/v1/config")
    assert r.status_code == 200 and "max-age" in r.headers.get("cache-control", "")
    cfg = r.json()
    assert cfg["allowance_cny"] == 25 and cfg["invite_bonus_cny"] == 5 and cfg["invitee_bonus_cny"] == 5
    assert cfg["signup_open"] is True and cfg["repo_url"].startswith("https://github.com/")
    assert cfg["privacy_url"] and cfg["own_key_docs"] and cfg["invite_url"] and cfg["version"]
    assert "upstream" not in r.text and "secret" not in r.text

    r = await client.post("/v1/admin/settings", headers=ADMIN, json={"allowance_cny": 8, "invite_bonus_cny": 3})
    assert r.status_code == 200, r.text
    cfg = (await client.get("/v1/config")).json()
    assert cfg["allowance_cny"] == 8 and cfg["invite_bonus_cny"] == 3


async def test_settings_change_at_once_and_reach_older_accounts_only_when_asked():
    app, client, sender, up, cloud, settings = make(allowance_cny=25)
    first = await sign_up(client, sender, "13800138000", "pixel")
    me1 = (await client.get("/v1/me", headers=auth(first["api_key"]))).json()
    assert me1["spend"]["grant"] == 25 and me1["spend"]["allowance_cny"] == 25

    # The page reads what is in force, what the environment says, and what it set itself.
    rt = (await client.get("/v1/admin/settings", headers=ADMIN)).json()
    assert rt["values"]["allowance_cny"] == 25 and rt["env"]["allowance_cny"] == 25
    assert rt["overridden"] == {"allowance_cny": False, "invite_bonus_cny": False, "signup_open": False}
    assert rt["below_allowance"] == 0

    # Lowered: the next sign-up gets the new figure; the first account keeps its pool.
    r = await client.post("/v1/admin/settings", headers=ADMIN, json={"allowance_cny": "8"})
    assert r.status_code == 200 and r.json()["values"]["allowance_cny"] == 8 and r.json()["overridden"]["allowance_cny"]
    second = await sign_up(client, sender, "13900139000", "pixel")
    me2 = (await client.get("/v1/me", headers=auth(second["api_key"]))).json()
    assert me2["spend"]["grant"] == 8 and me2["spend"]["allowance_cny"] == 8
    me1 = (await client.get("/v1/me", headers=auth(first["api_key"]))).json()
    assert me1["spend"]["grant"] == 25 and me1["spend"]["allowance_cny"] == 8
    assert (await client.get("/v1/admin/settings", headers=ADMIN)).json()["below_allowance"] == 0

    # Raised: both accounts are now below it; applying tops each up by its own difference, once.
    r = await client.post("/v1/admin/settings", headers=ADMIN, json={"allowance_cny": 30})
    assert r.status_code == 200 and r.json()["below_allowance"] == 2
    r = await client.post("/v1/admin/allowance/apply", headers=ADMIN)
    assert r.status_code == 200 and r.json() == {"accounts": 2, "allowance_cny": 30}
    me1 = (await client.get("/v1/me", headers=auth(first["api_key"]))).json()
    me2 = (await client.get("/v1/me", headers=auth(second["api_key"]))).json()
    assert me1["spend"]["grant"] == 30 and me2["spend"]["grant"] == 30
    r = await client.post("/v1/admin/allowance/apply", headers=ADMIN)
    assert r.json()["accounts"] == 0
    ledger = (await client.get(f"/v1/admin/accounts/{second['account']['id']}/ledger", headers=ADMIN)).json()
    raised = [row for row in ledger["rows"] if row["kind"] == "credit" and (row.get("detail") or {}).get("from") == "allowance"]
    assert len(raised) == 1 and raised[0]["detail"]["credit_uy"] == 22_000_000

    # Cleared: back to the environment's 25, and nobody's pool moves.
    r = await client.post("/v1/admin/settings", headers=ADMIN, json={"allowance_cny": None})
    assert r.status_code == 200 and r.json()["values"]["allowance_cny"] == 25 and not r.json()["overridden"]["allowance_cny"]
    me1 = (await client.get("/v1/me", headers=auth(first["api_key"]))).json()
    assert me1["spend"]["grant"] == 30

    # Bounds and unknown keys.
    for bad in ({"allowance_cny": 5000}, {"allowance_cny": "ten"}, {"invite_bonus_cny": -1}, {"signup_open": "maybe"}, {"usd_cny": 7}):
        r = await client.post("/v1/admin/settings", headers=ADMIN, json=bad)
        assert r.status_code == 400, bad
    assert (await client.post("/v1/admin/settings", json={"allowance_cny": 1})).status_code == 401


async def test_signup_can_be_closed_from_the_page():
    app, client, sender, up, cloud, settings = make(allowance_cny=25)
    await sign_up(client, sender, "13800138000", "pixel")
    r = await client.post("/v1/admin/settings", headers=ADMIN, json={"signup_open": False})
    assert r.status_code == 200 and r.json()["values"]["signup_open"] is False
    assert (await client.get("/v1/config")).json()["signup_open"] is False
    r = await client.post("/v1/auth/code", json={"identifier": "13900139000"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "not_invited"
    r = await client.post("/v1/admin/settings", headers=ADMIN, json={"signup_open": "on"})
    assert r.json()["values"]["signup_open"] is True
    assert (await client.post("/v1/auth/code", json={"identifier": "13900139000"})).status_code == 204


async def test_credit_everyone_leaves_members_out():
    app, client, sender, up, cloud, settings = make(allowance_cny=25)
    a = await sign_up(client, sender, "13800138000", "pixel")
    b = await sign_up(client, sender, "13900139000", "pixel")
    r = await client.post("/v1/admin/unlimited", headers=ADMIN, json={"account_id": b["account"]["id"], "unlimited": True})
    assert r.status_code == 204
    r = await client.post("/v1/admin/credit-all", headers=ADMIN, json={"cny": 1.5, "note": "holiday"})
    assert r.status_code == 200 and r.json() == {"accounts": 1, "cny": 1.5}
    me_a = (await client.get("/v1/me", headers=auth(a["api_key"]))).json()
    assert me_a["spend"]["grant"] == 26.5
    assert (await client.post("/v1/admin/credit-all", headers=ADMIN, json={"cny": 500})).status_code == 400
    assert (await client.post("/v1/admin/credit-all", headers=ADMIN, json={"cny": 0})).status_code == 400


async def test_the_operator_sets_a_pool_to_any_figure_up_or_down():
    """0.16: a pool is set — to what is left, to a total, or by a difference either way — for
    one account, for a chosen set, or for everyone limited; never below zero; the account's
    timeline says so."""
    app, client, sender, up, cloud, settings = make(allowance_cny=10)
    a = await sign_up(client, sender, "13800138000", "pixel")
    b = await sign_up(client, sender, "13900139000", "pixel")
    c = await sign_up(client, sender, "13700137000", "pixel")
    member = (
        await client.post("/v1/admin/unlimited", headers=ADMIN, json={"account_id": c["account"]["id"], "unlimited": True})
    ).status_code
    assert member == 204
    ida, idb = a["account"]["id"], b["account"]["id"]

    # What is left: the total becomes spent + 3 (nothing spent yet, so 3), and down is allowed.
    r = await client.post("/v1/admin/pool", headers=ADMIN, json={"account_id": ida, "left_cny": 3, "note": "trial"})
    assert r.status_code == 200, r.text
    assert r.json()["grant_cny"] == 3 and r.json()["left_cny"] == 3 and "grant_uy" not in r.json()
    me = (await client.get("/v1/me", headers=auth(a["api_key"]))).json()
    assert me["spend"]["grant"] == 3 and me["spend"]["left"] == 3
    events = (await client.get("/v1/me/events", headers=auth(a["api_key"]))).json()["events"]
    assert any(e["kind"] == "pool.set" and "¥3 left" in e["detail"] and "trial" in e["detail"] for e in events)

    # A total, and a difference down past zero stops at zero.
    r = await client.post("/v1/admin/pool", headers=ADMIN, json={"identifier": "13800138000", "grant_cny": 20})
    assert r.status_code == 200 and r.json()["grant_cny"] == 20
    r = await client.post("/v1/admin/pool", headers=ADMIN, json={"account_id": ida, "delta_cny": -25})
    assert r.status_code == 200 and r.json()["grant_cny"] == 0 and r.json()["left_cny"] == 0
    # The old credit route takes away too.
    r = await client.post("/v1/admin/credit", headers=ADMIN, json={"account_id": idb, "cny": -4, "note": "oops"})
    assert r.status_code == 200 and r.json()["grant_cny"] == 6
    ledger = (await client.get(f"/v1/admin/accounts/{idb}/ledger", headers=ADMIN)).json()
    down = [row for row in ledger["rows"] if row["kind"] == "credit" and (row.get("detail") or {}).get("credit_uy", 0) < 0]
    assert len(down) == 1 and down[0]["detail"]["credit_uy"] == -4_000_000 and down[0]["detail"]["set"] == 6_000_000

    # A set of accounts, then everyone limited (the member is left out, so two move).
    r = await client.post("/v1/admin/pool/batch", headers=ADMIN, json={"account_ids": [ida, idb, "nobody"], "left_cny": 1})
    assert r.status_code == 200 and r.json() == {"accounts": 2, "left_cny": 1.0}
    r = await client.post("/v1/admin/pool/batch", headers=ADMIN, json={"all": True, "delta_cny": 2.5, "note": "holiday"})
    assert r.status_code == 200 and r.json()["accounts"] == 2
    me_a = (await client.get("/v1/me", headers=auth(a["api_key"]))).json()
    me_b = (await client.get("/v1/me", headers=auth(b["api_key"]))).json()
    me_c = (await client.get("/v1/me", headers=auth(c["api_key"]))).json()
    assert me_a["spend"]["grant"] == 3.5 and me_b["spend"]["grant"] == 3.5 and me_c["spend"]["unlimited"] is True
    r = await client.post("/v1/admin/credit-all", headers=ADMIN, json={"cny": -1})
    assert r.status_code == 200 and r.json()["accounts"] == 2
    assert (await client.get("/v1/me", headers=auth(a["api_key"]))).json()["spend"]["grant"] == 2.5

    # One mode at a time, numbers only, within reason, somebody named.
    for bad in (
        {"account_id": ida},
        {"account_id": ida, "left_cny": 1, "grant_cny": 2},
        {"account_id": ida, "left_cny": "lots"},
        {"account_id": ida, "grant_cny": -1},
        {"account_id": ida, "delta_cny": 0},
        {"account_id": ida, "left_cny": 99_999},
    ):
        assert (await client.post("/v1/admin/pool", headers=ADMIN, json=bad)).status_code == 400, bad
    assert (await client.post("/v1/admin/pool", headers=ADMIN, json={"account_id": "nobody", "left_cny": 1})).status_code == 404
    assert (await client.post("/v1/admin/pool/batch", headers=ADMIN, json={"left_cny": 1})).status_code == 400
    assert (await client.post("/v1/admin/pool/batch", json={"all": True, "left_cny": 1})).status_code == 401


async def test_settings_survive_a_restart_and_a_bad_row_is_ignored():
    db = Database(":memory:")
    app, client, sender, up, cloud, settings = make(allowance_cny=25)
    cloud2 = Cloud(settings, db, LogSender())
    app2 = create_app(settings, cloud2)
    c2 = httpx.AsyncClient(transport=httpx.ASGITransport(app=app2), base_url="http://cloud.test", headers=APP_UA)
    r = await c2.post("/v1/admin/settings", headers=ADMIN, json={"allowance_cny": 12, "invite_bonus_cny": 2, "signup_open": False})
    assert r.status_code == 200
    db.settings_put("allowance_cny", "garbage")  # a hand-edited row: ignored, not fatal
    db.settings_put("not_a_setting", 1)
    cloud3 = Cloud(settings, db, LogSender())
    assert cloud3.s.allowance_cny == 25 and cloud3.s.invite_bonus_cny == 2 and cloud3.s.signup_open is False
    assert cloud3.env.allowance_cny == 25


async def test_older_accounts_are_marked_with_the_allowance_of_the_day():
    """A database from before the column: every account is taken to have been given the
    allowance in force when the relay first ran with 0.15, so a later raise reaches it."""
    db = Database(":memory:")
    db._conn.execute("UPDATE accounts SET allowance_uy=-1")  # nothing yet; the column exists
    cloud = Cloud(make(allowance_cny=25)[5], db, LogSender())
    row = db.create_account("h1", "phone", "138****0000", 0, grant_uy=cloud.s.allowance_uy)
    db._conn.execute("UPDATE accounts SET allowance_uy=-1 WHERE id=?", (row["id"],))
    cloud = Cloud(make(allowance_cny=25)[5], db, LogSender())
    assert db._conn.execute("SELECT allowance_uy FROM accounts WHERE id=?", (row["id"],)).fetchone()[0] == 25_000_000
    assert db.below_allowance(30_000_000) == 1 and db.below_allowance(25_000_000) == 0
    assert db.raise_allowance(30_000_000) == 1 and db.raise_allowance(30_000_000) == 0
    assert db._conn.execute("SELECT grant_uy FROM accounts WHERE id=?", (row["id"],)).fetchone()[0] == 30_000_000
