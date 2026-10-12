"""The trust boundary, the allowance after a failed start, and the gateway's own pages."""

from __future__ import annotations

import httpx

from showcase_gateway.app import create_app
from showcase_gateway.peers import TrustedProxies
from showcase_gateway.runner import RunnerError
from showcase_gateway.sessions import Refused, SessionManager
from showcase_gateway.trials import TrialManager, TrialStore

from .conftest import Clock, FakeRunner, Upstream, body, make_settings


async def client_for(app, peer: str = "127.0.0.1"):
    transport = httpx.ASGITransport(app=app, client=(peer, 50000))
    return httpx.AsyncClient(transport=transport, base_url="http://localhost:8000")


def make_world(**over):
    settings = make_settings(**over)
    runner = FakeRunner()
    upstream = Upstream()
    clock = Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    manager = SessionManager(settings, runner, http=client, clock=clock)
    manager.resolve = lambda host, port: ["93.184.216.34"]
    trials = TrialManager(settings, TrialStore(":memory:"), clock=clock)
    app = create_app(settings, manager, client=client, trials=trials)
    return settings, runner, upstream, clock, manager, app


# ------------------------------------------------------------------ who may speak for the visitor


async def test_the_header_is_believed_from_the_edge_only():
    """A session container that reaches the API with a forged X-Forwarded-For is limited
    under its own address, not the one it names."""
    settings, runner, upstream, clock, manager, app = make_world(
        trusted_proxies=("172.18.0.2",), per_ip_daily=1, per_ip_active=5, max_sessions=10
    )
    async with app.router.lifespan_context(app):
        edge = await client_for(app, "172.18.0.2")
        container = await client_for(app, "172.20.0.9")
        r = await edge.post("/api/demo/session", json={}, headers={"x-forwarded-for": "9.9.9.1"})
        assert r.status_code == 201 and manager.sessions[body(r)["id"]].ip == "9.9.9.1"
        # the container's three "visitors" are one address: the second start is refused
        r = await container.post(
            "/api/demo/session", json={}, headers={"x-forwarded-for": "9.9.9.2"}
        )
        assert r.status_code == 201 and manager.sessions[body(r)["id"]].ip == "172.20.0.9"
        r = await container.post(
            "/api/demo/session", json={}, headers={"x-forwarded-for": "9.9.9.3"}
        )
        assert r.status_code == 429 and body(r)["error"] == "daily_limit"


async def test_trust_proxy_off_ignores_the_header_from_anyone():
    settings, runner, upstream, clock, manager, app = make_world(trust_proxy=False)
    async with app.router.lifespan_context(app):
        c = await client_for(app, "10.1.1.1")
        r = await c.post("/api/demo/session", json={}, headers={"x-forwarded-for": "9.9.9.1"})
        assert r.status_code == 201 and manager.sessions[body(r)["id"]].ip == "10.1.1.1"


async def test_a_named_proxy_is_resolved_and_followed_when_recreated():
    """``caddy`` is a compose service: a recreated container gets a new address, and a peer
    that is not in the last answer makes the gateway ask again, at most every two seconds."""
    answers = {"caddy": ["172.18.0.2"]}
    asked = 0

    async def resolve(name: str) -> list[str]:
        nonlocal asked
        asked += 1
        return answers[name]

    clock = Clock()
    proxies = TrustedProxies(("caddy", "10.0.0.0/8"), resolve=resolve, clock=clock)
    assert await proxies.trusts("172.18.0.2") and asked == 1
    assert await proxies.trusts("10.2.3.4") and asked == 1  # the network: no lookup
    assert not await proxies.trusts("172.20.0.9") and asked == 1  # inside the two seconds
    clock.now += 3
    assert not await proxies.trusts("172.20.0.9") and asked == 2
    answers["caddy"] = ["172.18.0.7"]  # Caddy was recreated
    clock.now += 3
    assert await proxies.trusts("172.18.0.7") and asked == 3
    assert not await proxies.trusts("172.18.0.2")  # the old address is no longer Caddy's
    assert proxies.describe() == "10.0.0.0/8, caddy"
    assert TrustedProxies(("*",)).describe() == "any peer"
    assert await TrustedProxies(("*",)).trusts("anything")
    assert not await TrustedProxies(()).trusts("172.18.0.2")
    assert not await TrustedProxies(("caddy",), resolve=resolve).trusts("not-an-address")


# ------------------------------------------------------------------ a failed start is not a turn


class FlakyRunner(FakeRunner):
    def __init__(self) -> None:
        super().__init__()
        self.fail = 0

    async def start(self, name: str, env: dict[str, str]) -> str:
        if self.fail:
            self.fail -= 1
            raise RunnerError("docker run: no space left on device")
        return await super().start(name, env)


async def test_a_failed_start_does_not_count_against_the_allowance():
    settings = make_settings(per_ip_daily=1, per_account_daily=1, demo_signin_required=False)
    runner = FlakyRunner()
    upstream = Upstream()
    clock = Clock()
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    manager = SessionManager(settings, runner, http=client, clock=clock)
    app = create_app(settings, manager, client=client)
    async with app.router.lifespan_context(app):
        c = await client_for(app)
        runner.fail = 2
        for _ in range(2):
            r = await c.post("/api/demo/session", json={}, headers={"x-forwarded-for": "9.9.9.1"})
            assert r.status_code == 503 and body(r)["error"] == "start_failed"
        # the box failed twice; the visitor's one start of the day is still theirs
        r = await c.post("/api/demo/session", json={}, headers={"x-forwarded-for": "9.9.9.1"})
        assert r.status_code == 201
        await manager.end(body(r)["id"])
        r = await c.post("/api/demo/session", json={}, headers={"x-forwarded-for": "9.9.9.1"})
        assert r.status_code == 429 and body(r)["error"] == "daily_limit"
        # the same for an account's allowance (the account is counted alongside the address)
        sess = await manager.create("9.9.9.2", account="acct-1")
        await manager.end(sess.id)
        runner.fail = 1
        try:
            await manager.create("9.9.9.3", account="acct-2")
        except Refused as exc:
            assert exc.code == "start_failed"
        assert len(manager._account_starts["acct-2"]) == 0
        assert len(manager._account_starts["acct-1"]) == 1


# ------------------------------------------------------------------ the pages speak the language


async def test_the_ended_page_follows_the_browsers_language():
    settings, runner, upstream, clock, manager, app = make_world()
    async with app.router.lifespan_context(app):
        c = await client_for(app)
        host = {"host": "gone.s.localhost", "accept": "text/html"}
        r = await c.get("/", headers=host)
        assert r.status_code == 404 and '<html lang="en">' in r.text and "已经结束" in r.text
        r = await c.get("/", headers={**host, "accept-language": "zh-CN,zh;q=0.9,en;q=0.8"})
        assert '<html lang="zh">' in r.text
        r = await c.get("/?lang=en", headers={**host, "accept-language": "zh-CN"})
        assert '<html lang="en">' in r.text
        r = await c.get("/?lang=zh", headers=host)
        assert '<html lang="zh">' in r.text


def test_the_wake_page_carries_its_lines_in_one_language():
    from showcase_gateway.wake import wake_page

    en = wake_page("https://demo.example/web/")
    zh = wake_page("https://demo.example/web/", zh=True)
    assert "Waking your Muse" in en and "demo.example/web/" in en
    assert "正在唤醒你的 Muse" in zh and '"try_again"' in zh and "Try again" not in zh
