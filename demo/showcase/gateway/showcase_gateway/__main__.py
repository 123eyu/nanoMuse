"""``python -m showcase_gateway`` — run the gateway with uvicorn, settings from the environment."""

from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "showcase_gateway.app:build",
        factory=True,
        host=settings.listen_host,
        port=settings.listen_port,
        # uvicorn is not told to rewrite the client from X-Forwarded-For: it would do so for
        # any peer, the session containers included, and the access log would carry the
        # visitors' addresses. The gateway reads the header itself, from the edge proxy only
        # (peers.py), so request.client stays the peer that connected.
        proxy_headers=False,
        log_level="info",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
