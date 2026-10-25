"""``nanomuse channels`` from a terminal, with no server running (the files are edited)
and with one (the API is called)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from nanomuse.channels.cli import channels_app
from nanomuse.channels.store import PairingStore

runner = CliRunner()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def config(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    path = tmp_path / "config.toml"
    # Forward slashes: a Windows path's backslashes are escapes inside a basic TOML string.
    path.write_text(
        f'data_dir = "{data.as_posix()}"\n[agent]\nworkspace = "{(tmp_path / "ws").as_posix()}"\n'
        f'[llm]\napi_key = "k"\n[server]\nport = {free_port()}\n',
        "utf-8",
    )
    return path


def run(*args: str) -> Any:
    result = runner.invoke(channels_app, list(args))
    return result


def test_offline_status_pending_approve_deny(config: Path, tmp_path: Path):
    r = run("status", "--config", str(config))
    assert r.exit_code == 0, r.output
    assert "Feishu" in r.output and "Telegram" in r.output and "not running" in r.output
    r = run("pending", "--config", str(config))
    assert r.exit_code == 0 and "No pairing codes" in r.output
    pairing = PairingStore(tmp_path / "data")
    code = pairing.request("telegram", "1001", sender_name="Ann")
    r = run("pending", "--config", str(config))
    assert code in r.output and "Ann" in r.output
    r = run("approve", code.lower(), "--config", str(config))
    assert r.exit_code == 0 and "Paired Ann on telegram" in r.output
    assert pairing.is_approved("telegram", "1001")
    r = run("approve", code, "--config", str(config))
    assert r.exit_code == 1 and "no pairing with that code" in r.output
    other = pairing.request("telegram", "1002")
    assert run("deny", other, "--config", str(config)).exit_code == 0
    assert run("deny", other, "--config", str(config)).exit_code == 1
    r = run("status", "--json", "--config", str(config))
    assert r.exit_code == 0
    view = json.loads(r.output)
    telegram = next(c for c in view["channels"] if c["name"] == "telegram")
    assert telegram["paired"][0]["sender_id"] == "1001" and view["offline"] is True
    # the test message needs the server
    r = run("test", "telegram", "--config", str(config))
    assert r.exit_code == 1 and "not running" in r.output
    r = run("login", "dingtalk", "--config", str(config))
    assert r.exit_code == 1 and "only `login feishu`" in r.output


def test_with_a_server_the_api_is_called(
    config: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "server_token").write_text("tok-123", "utf-8")
    seen: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, request.headers.get("authorization", "")))
        if request.url.path == "/api/channels":
            return httpx.Response(
                200,
                json={
                    "channels": [
                        {
                            "name": "telegram",
                            "label": "Telegram",
                            "enabled": True,
                            "status": {"state": "connected", "detail": "@muse_bot"},
                            "paired": [{"sender_id": "1", "deliver": True}],
                        }
                    ],
                    "pending": [
                        {
                            "code": "ABCDEF",
                            "channel": "telegram",
                            "sender_id": "9",
                            "sender_name": "Zed",
                        }
                    ],
                },
            )
        if request.url.path.endswith("/approve"):
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "paired": {"channel": "telegram", "sender_id": "9", "sender_name": "Zed"},
                },
            )
        if request.url.path.endswith("/test"):
            return httpx.Response(
                409, json={"detail": "Telegram is not running — switch it on first."}
            )
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    def fake_request(method: str, url: str, **kw: Any) -> httpx.Response:
        with httpx.Client(transport=transport) as client:
            return client.request(method, url, json=kw.get("json"), headers=kw.get("headers"))

    monkeypatch.setattr(httpx, "request", fake_request)
    r = run("status", "--config", str(config))
    assert r.exit_code == 0, r.output
    assert "connected" in r.output and "@muse_bot" in r.output and "1 pairing code" in r.output
    assert seen[0] == ("GET", "/api/channels", "Bearer tok-123")
    r = run("approve", "abcdef", "--config", str(config))
    assert r.exit_code == 0 and "Paired Zed on telegram" in r.output
    assert seen[-1][1] == "/api/channels/pairing/ABCDEF/approve"
    r = run("test", "telegram", "--config", str(config))
    assert r.exit_code == 1 and "switch it on" in r.output


def test_the_api_is_called_without_the_environment_s_proxy(tmp_path: Path, dead_proxy: str) -> None:
    """The command calls this machine's own server with its token in the header. ``httpx``
    used to read the environment's proxy for that call: the token went to the proxy, and
    with a proxy that answers nothing a running server looked like no server at all, so the
    offline files were read instead of the server's live answer."""
    import http.server
    import threading

    answer = {
        "channels": [
            {
                "name": "telegram",
                "label": "Telegram",
                "enabled": True,
                "status": {"state": "connected", "detail": "@muse_bot"},
                "paired": [],
            }
        ],
        "pending": [],
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the handler's name
            body = json.dumps(answer).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        path = tmp_path / "config.toml"
        path.write_text(
            f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
            f'[agent]\nworkspace = "{(tmp_path / "ws").as_posix()}"\n'
            f'[llm]\napi_key = "k"\n[server]\nport = {server.server_address[1]}\n',
            "utf-8",
        )
        r = run("status", "--config", str(path))
    finally:
        server.shutdown()
        server.server_close()
    assert r.exit_code == 0, r.output
    assert "@muse_bot" in r.output and "not running" not in r.output


def test_a_bad_setting_is_named(tmp_path: Path):
    """The same sentence as the main CLI's: each setting pydantic refused, with its path,
    not a count of problems the person has to go and find."""
    path = tmp_path / "config.toml"
    path.write_text('[sentinel]\nmode = "loose"\n[agent]\nmax_steps = "many"\n', "utf-8")
    result = run("status", "--config", str(path))
    assert result.exit_code == 1
    assert "2 setting(s)" in result.output
    assert "sentinel.mode" in result.output and "agent.max_steps" in result.output

    from nanomuse.cli import app

    result = CliRunner().invoke(app, ["chatgpt", "status", "--config", str(path)])
    assert result.exit_code == 1
    assert "sentinel.mode" in result.output and "agent.max_steps" in result.output
