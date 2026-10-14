"""The visitor's address: ``X-Forwarded-For`` counts only when a proxy on this box or
network sent it; a relay exposed directly keys its per-address limits on the socket."""

from nanomuse_cloud.client import from_headers, platform_of, visitor_ip


def test_forwarded_for_is_honoured_from_a_proxy_peer():
    h = {"x-forwarded-for": "203.0.113.7, 10.0.0.1"}
    assert visitor_ip(h, "127.0.0.1") == "203.0.113.7"  # Caddy on the same box
    assert visitor_ip(h, "172.18.0.3") == "203.0.113.7"  # Caddy on the Docker network
    assert visitor_ip(h, "::1") == "203.0.113.7"
    assert visitor_ip(h, "fe80::1%eth0") == "203.0.113.7"
    assert visitor_ip(h, "100.101.102.103") == "203.0.113.7"  # a tailnet address


def test_forwarded_for_is_ignored_from_the_open_internet():
    h = {"x-forwarded-for": "203.0.113.7"}
    # (the documentation ranges count as non-global, so real public addresses here)
    assert visitor_ip(h, "8.8.8.8") == "8.8.8.8"
    assert visitor_ip(h, "2606:4700:4700::1111") == "2606:4700:4700::1111"
    assert visitor_ip(h, None) == ""
    assert visitor_ip({}, "8.8.8.8") == "8.8.8.8"
    assert visitor_ip({"x-forwarded-for": " , "}, "127.0.0.1") == "127.0.0.1"


def test_client_info_carries_the_address_and_the_platform():
    info = from_headers(
        {"x-forwarded-for": "203.0.113.7", "user-agent": "nanoMuse-Android/0.1.40 (Pixel 8)"},
        "10.0.0.2",
    )
    assert info.ip == "203.0.113.7" and info.platform == "android"
    assert platform_of("nanoMuse/0.1.40 (darwin)") == "macos"
    assert platform_of("Mozilla/5.0") == "browser"


def test_trusted_proxies_default_to_the_non_global_networks(monkeypatch):
    from nanomuse_cloud.config import NON_GLOBAL_NETWORKS, Settings

    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    assert Settings().trusted_proxy_list == list(NON_GLOBAL_NETWORKS)
    assert "*" not in Settings().trusted_proxy_list
    monkeypatch.setenv("TRUSTED_PROXIES", "203.0.113.10, 2001:db8::/32")
    assert Settings().trusted_proxy_list == ["203.0.113.10", "2001:db8::/32"]


async def test_uvicorn_believes_the_header_from_a_proxy_peer_only(monkeypatch):
    """The relay runs under uvicorn's proxy-headers middleware with our list; a peer on the
    open internet keeps its own address whatever header it sends."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    from nanomuse_cloud.config import Settings

    monkeypatch.delenv("TRUSTED_PROXIES", raising=False)
    seen: list[str] = []

    async def app(scope, receive, send):  # type: ignore[no-untyped-def]
        seen.append(scope["client"][0])

    mw = ProxyHeadersMiddleware(app, Settings().trusted_proxy_list)
    headers = [(b"x-forwarded-for", b"203.0.113.7")]
    for peer in ("172.18.0.3", "127.0.0.1", "100.101.102.103", "8.8.8.8"):
        await mw({"type": "http", "client": (peer, 1), "headers": headers}, None, None)  # type: ignore[arg-type]
    assert seen == ["203.0.113.7", "203.0.113.7", "203.0.113.7", "8.8.8.8"]


def test_an_explicit_trusted_proxies_list_is_the_whole_list():
    from nanomuse_cloud.client import parse_trusted_proxies

    trusted = parse_trusted_proxies("10.0.0.1, 172.18.0.0/16, fd00::/8")
    h = {"x-forwarded-for": "203.0.113.7"}
    assert visitor_ip(h, "10.0.0.1", trusted) == "203.0.113.7"
    assert visitor_ip(h, "172.18.0.3", trusted) == "203.0.113.7"
    assert visitor_ip(h, "fd00::5", trusted) == "203.0.113.7"
    # a private peer not on the list is not believed once the list is set
    assert visitor_ip(h, "10.0.0.2", trusted) == "10.0.0.2"
    assert visitor_ip(h, "127.0.0.1", trusted) == "127.0.0.1"
    assert from_headers(h, "192.168.1.9", trusted).ip == "192.168.1.9"
    # the star believes every peer; an empty list means the built-in rule
    assert visitor_ip(h, "8.8.8.8", parse_trusted_proxies("*")) == "203.0.113.7"
    assert parse_trusted_proxies("") is None and parse_trusted_proxies(" , ") is None
    # a bad entry is skipped, the others stand
    partial = parse_trusted_proxies("not-an-address, 10.0.0.1")
    assert visitor_ip(h, "10.0.0.1", partial) == "203.0.113.7" and visitor_ip(h, "10.0.0.2", partial) == "10.0.0.2"


async def test_the_app_keys_its_limits_on_the_trusted_list(monkeypatch):
    from test_accounts import make

    # the test transport's peer is 127.0.0.1; a list without it means the header is ignored
    for trusted, seen in (("", "203.0.113.7"), ("10.0.0.1", "127.0.0.1"), ("127.0.0.1", "203.0.113.7")):
        app, client, sender, up, cloud, settings = make(trusted_proxies=trusted)
        r = await client.post("/v1/auth/code", json={"identifier": "13800138000"}, headers={"X-Forwarded-For": "203.0.113.7"})
        assert r.status_code == 204, (trusted, r.text)
        ident, code = sender.sent[-1]
        r = await client.post(
            "/v1/auth/verify",
            json={"identifier": "13800138000", "code": code, "device": "pixel"},
            headers={"X-Forwarded-For": "203.0.113.7"},
        )
        assert r.status_code == 200, (trusted, r.text)
        out = (await client.get(f"/v1/admin/address?ip={seen}", headers={"X-Admin-Token": "admin"})).json()
        assert len(out["accounts"]) == 1, (trusted, seen, out)
