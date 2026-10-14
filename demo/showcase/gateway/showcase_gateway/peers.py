"""Who may speak for the visitor: ``X-Forwarded-For`` is believed from the edge proxy only.

Two kinds of peer open connections to this process. Caddy, on the edge network, forwards the
visitors' requests and puts the visitor's address in ``X-Forwarded-For``. The session
containers, on the sessions network, and the kept Muses, on the web network, reach the same
port for their model calls; a container can also reach ``/api/demo/*`` and ``/api/web/*``,
and the agent inside it can be talked into any request by the visitor driving it. A header
from such a peer is not the visitor's word, so the address limits (``PER_IP_*``, the relay's
own per-address limits on sign-in codes) would count whatever the container said. So the
header is read only when the connection itself comes from a trusted proxy: an address or a
network given in ``TRUSTED_PROXIES``, or a name (``caddy``, the compose service) resolved
through Docker's DNS and looked up again when a peer is not in the last answer, since a
recreated container gets a new address.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections.abc import Awaitable, Callable

log = logging.getLogger("showcase.peers")

Resolver = Callable[[str], Awaitable[list[str]]]

# how long a name's answer is believed before a peer outside it makes us ask again, and how
# often a peer outside it may make us ask at all (a container knocking on the API must not
# turn into a lookup per request)
TTL_S = 60.0
RELOOK_S = 2.0


async def _resolve(name: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    return [str(info[4][0]) for info in infos]


class TrustedProxies:
    """The peers whose ``X-Forwarded-For`` is the visitor's address."""

    def __init__(
        self,
        entries: tuple[str, ...],
        resolve: Resolver | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.any = "*" in entries
        self.networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        self.names: list[str] = []
        for entry in entries:
            entry = entry.strip()
            if not entry or entry == "*":
                continue
            try:
                self.networks.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                self.names.append(entry)
        self._resolve = resolve or _resolve
        self._clock = clock
        self._known: set[str] = set()
        self._asked_at = float("-inf")
        self._lock = asyncio.Lock()

    def describe(self) -> str:
        if self.any:
            return "any peer"
        parts = [str(n) for n in self.networks] + self.names
        return ", ".join(parts) if parts else "no peer"

    async def trusts(self, peer: str) -> bool:
        if self.any:
            return True
        if peer in self.names:
            return True
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        if any(address in network for network in self.networks):
            return True
        if not self.names:
            return False
        now = self._clock()
        if peer in self._known and now - self._asked_at < TTL_S:
            return True
        if now - self._asked_at >= RELOOK_S:
            await self._refresh()
        return peer in self._known

    async def _refresh(self) -> None:
        async with self._lock:
            if self._clock() - self._asked_at < RELOOK_S:
                return
            known: set[str] = set()
            for name in self.names:
                try:
                    known.update(await self._resolve(name))
                except OSError as exc:
                    log.debug("trusted proxy %s does not resolve: %s", name, exc)
            self._known = known
            self._asked_at = self._clock()
