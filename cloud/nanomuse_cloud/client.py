"""Who is at the other end of a request: the network address and the client software.

The relay keeps, next to what it already kept (sign-ins, requests, events, devices, kept
turns), the address a request came from and the user agent that made it — for the
operator's page, where every record of an account can be read, and for telling abuse
from use. Nothing else is derived from them here: no geolocation, no fingerprint.

The request layer (HTTP middleware, the hub's socket handler) sets the current
``ClientInfo`` in a context variable; the database writes read it when they add a row
that has a place for it. Code running outside a request (tests, maintenance) sees the
empty default and writes empty strings.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from contextvars import ContextVar, Token
from dataclasses import dataclass

UA_MAX = 160
LANG_MAX = 40


@dataclass(frozen=True)
class ClientInfo:
    ip: str = ""
    ua: str = ""  # the User-Agent header, cut to UA_MAX
    lang: str = ""  # Accept-Language, cut to LANG_MAX

    @property
    def platform(self) -> str:
        return platform_of(self.ua)


_NOBODY = ClientInfo()
_current: ContextVar[ClientInfo | None] = ContextVar("nanomuse_cloud_client", default=None)


def current() -> ClientInfo:
    return _current.get() or _NOBODY


def set_current(info: ClientInfo) -> Token:
    return _current.set(info)


def reset(token: Token) -> None:
    _current.reset(token)


Network = ipaddress.IPv4Network | ipaddress.IPv6Network
# The proxies whose X-Forwarded-For is believed: None is the built-in rule (every
# non-global peer), a tuple is the operator's TRUSTED_PROXIES and nothing else.
TrustedProxies = tuple[Network, ...] | None


def parse_trusted_proxies(spec: str) -> TrustedProxies:
    """TRUSTED_PROXIES as networks: comma-separated addresses or CIDR blocks; ``*`` trusts
    every peer. Empty keeps the built-in rule (None). An entry that is not an address is
    skipped with a line in the log rather than widening or narrowing the list by surprise."""
    out: list[Network] = []
    for item in (spec or "").split(","):
        item = item.strip()
        if not item:
            continue
        if item == "*":
            return (ipaddress.ip_network("0.0.0.0/0"), ipaddress.ip_network("::/0"))
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            logging.getLogger("nanomuse_cloud").warning("TRUSTED_PROXIES has an entry that is not an address or a network; ignored")
    return tuple(out) or None


def _is_proxy_peer(peer_host: str | None, trusted: TrustedProxies = None) -> bool:
    """A socket peer that may be the reverse proxy in front. With TRUSTED_PROXIES set, one
    of those addresses or networks and no other; else loopback, a private or otherwise
    non-global address (Caddy on the same box, the same Docker network, a tailnet). A peer
    on the open internet is the visitor themselves, and their ``X-Forwarded-For`` is just
    a header they typed."""
    if not peer_host:
        return False
    try:
        addr = ipaddress.ip_address(peer_host.split("%")[0])
    except ValueError:
        return False
    if trusted is not None:
        return any(addr.version == net.version and addr in net for net in trusted)
    return not addr.is_global


def visitor_ip(headers: Mapping[str, str], peer_host: str | None, trusted: TrustedProxies = None) -> str:
    """The visitor's address: the first hop of ``X-Forwarded-For`` when the request came
    through a proxy on this box or network (or one named in TRUSTED_PROXIES, when that is
    set), else the socket's peer. A relay exposed directly cannot be told a different
    address by the request itself; the per-address limits on codes and sign-ins key on
    this. The same list goes to uvicorn's proxy-headers middleware, so the two agree."""
    fwd = headers.get("x-forwarded-for") or ""
    if fwd and _is_proxy_peer(peer_host, trusted):
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return peer_host or ""


def from_headers(headers: Mapping[str, str], peer_host: str | None, trusted: TrustedProxies = None) -> ClientInfo:
    """What the proxy in front (Caddy) says the visitor's address is, the first hop of
    ``X-Forwarded-For``, else the socket's peer; the two headers that name the client."""
    ip = visitor_ip(headers, peer_host, trusted)
    return ClientInfo(
        ip=ip[:64],
        ua=(headers.get("user-agent") or "")[:UA_MAX],
        lang=(headers.get("accept-language") or "")[:LANG_MAX],
    )


def platform_of(ua: str) -> str:
    """The app behind a request, from the user agent the clients send: the Android app,
    the runtime on each system (which serves the web app, the desktop and the harness),
    a browser, or something else."""
    ua = str(ua or "")
    if ua.startswith("nanoMuse-Android"):
        return "android"
    if ua.startswith("nanoMuse/"):
        system = ua[ua.find("(") + 1 : ua.find(")")].lower() if "(" in ua and ")" in ua else ""
        return {"windows": "windows", "darwin": "macos", "linux": "linux"}.get(system, "runtime")
    if ua.startswith("Mozilla/"):
        return "browser"
    return "other"


def client_version(ua: str) -> str:
    """The version the client names in its user agent (``nanoMuse-Android/0.1.26 …``,
    ``nanoMuse/0.1.26 (linux)``), or an empty string."""
    ua = str(ua or "")
    head = ua.split(" ", 1)[0]
    if "/" in head and head.startswith("nanoMuse"):
        return head.split("/", 1)[1][:24]
    return ""
