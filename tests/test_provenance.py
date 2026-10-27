"""Where a model call sits, told to the relay (relay 0.31): the run that set it going, the
turn and the call within it, the screen-operation trace and step, the platform; the id the
relay gives a turn, kept for the notes about it; turns on the person's own key sent up to
the account's record; client events for what happened around a turn."""

from __future__ import annotations

import importlib
import json
import re
import sys
from collections.abc import Iterator
from types import ModuleType
from typing import Any

import pytest
from openai import AsyncOpenAI

from nanomuse import telemetry as telemetry_mod
from nanomuse.cloud import CloudError
from nanomuse.config import LLMSettings, Settings
from nanomuse.llm import openai_chat as chat_mod
from nanomuse.llm import openai_responses as responses_mod
from nanomuse.llm import session
from nanomuse.llm.factory import create_llm
from nanomuse.schema import LLMResponse, Message
from nanomuse.telemetry import telemetry

RELAY = "https://cloud.nanomuse.cn/v1"
ELSEWHERE = "https://api.example-vendor.test/v1"


def _httpx_of(client: Any) -> ModuleType:
    """The httpx the SDK's client is built from (the SDK may ship its own copy)."""
    for cls in type(client).__mro__:
        root = cls.__module__.split(".")[0]
        if root.startswith("httpx"):
            return sys.modules.get(root) or importlib.import_module(root)
    raise AssertionError("no httpx in the client's bases")


class Wire:
    """Every request the clients send, answered with a short reply; the relay's answers
    carry the id of the turn it kept."""

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.httpx: ModuleType | None = None

    def handler(self, request: Any) -> Any:
        httpx = self.httpx
        assert httpx is not None
        self.requests.append(request)
        body = json.loads(request.content)
        headers = {}
        if request.url.host == "cloud.nanomuse.cn":
            headers["x-nanomuse-request"] = f"req-{len(self.requests)}"
        if request.url.path.endswith("/responses"):
            return httpx.Response(
                200,
                headers=headers,
                json={
                    "id": "r",
                    "object": "response",
                    "created_at": 0,
                    "model": body["model"],
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "id": "m",
                            "role": "assistant",
                            "status": "completed",
                            "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                        }
                    ],
                    "parallel_tool_calls": False,
                    "tool_choice": "auto",
                    "tools": [],
                },
            )
        return httpx.Response(
            200,
            headers=headers,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 1},
            },
        )

    def header(self, name: str) -> list[str | None]:
        return [r.headers.get(name) for r in self.requests]


@pytest.fixture()
def wire(monkeypatch: pytest.MonkeyPatch) -> Wire:
    """The clients' own HTTP client (with its response hook), answered by :class:`Wire`."""
    w = Wire()

    def make(**kw: Any) -> AsyncOpenAI:
        http = kw["http_client"]
        w.httpx = _httpx_of(http)
        http._transport = w.httpx.MockTransport(w.handler)
        return AsyncOpenAI(**kw)

    monkeypatch.setattr(chat_mod, "AsyncOpenAI", make)
    monkeypatch.setattr(responses_mod, "AsyncOpenAI", make)
    return w


class FakeCloud:
    """What the runtime would send the relay, kept in lists."""

    def __init__(self) -> None:
        self.api_key = ""
        self.turns: list[tuple[dict[str, Any], dict[str, str]]] = []
        self.events: list[tuple[list[dict[str, Any]], dict[str, str]]] = []
        self.blobs: dict[str, tuple[str, bytes]] = {}
        self.fail: CloudError | None = None

    async def post_turn(self, turn: dict[str, Any], headers: dict[str, str] | None = None) -> dict:
        if self.fail:
            raise self.fail
        self.turns.append((turn, dict(headers or {})))
        return {"id": turn.get("id")}

    async def post_events(
        self, events: list[dict[str, Any]], headers: dict[str, str] | None = None
    ) -> dict:
        if self.fail:
            raise self.fail
        self.events.append((events, dict(headers or {})))
        return {"kept": len(events), "dropped": 0}

    async def has_blob(self, sha256: str) -> bool:
        return sha256 in self.blobs

    async def put_blob(self, sha256: str, mime: str, data: bytes) -> dict[str, Any]:
        self.blobs[sha256] = (mime, data)
        return {"sha256": sha256}

    def all_events(self) -> list[dict[str, Any]]:
        return [e for batch, _ in self.events for e in batch]

    def noted(self) -> list[dict[str, Any]]:
        """Every event made so far: sent already, or still queued."""
        return [*self.all_events(), *telemetry._events]


@pytest.fixture()
def cloud() -> Iterator[FakeCloud]:
    """A signed-in runtime: the fake relay installed, the device named; undone after."""
    fake = FakeCloud()
    telemetry.install(fake, lambda: "key-1")  # type: ignore[arg-type]
    session.set_device("pc-test0001")
    session.set_cloud_base(RELAY)
    yield fake
    telemetry.install(None)
    session.set_device("")


def slot(**kw: Any) -> LLMSettings:
    base = {"api_key": "k", "model": "some-model", "stream": False, "tool_mode": "native"}
    return LLMSettings(**{**base, **kw})


# ------------------------------------------------------------------ the headers
def test_outside_a_turn_only_the_device_and_platform_are_said(cloud: FakeCloud):
    headers = session.relay_headers()
    assert headers["X-NanoMuse-Device"] == "pc-test0001"
    assert headers["X-NanoMuse-Platform"] in ("macos", "windows", "linux", "runtime")
    assert "X-NanoMuse-Run" not in headers and "X-NanoMuse-Turn" not in headers


def test_a_turn_names_its_run_number_and_each_call(cloud: FakeCloud):
    with session.turn("schedule", 4):
        first = session.relay_headers(session.next_call())
        with session.trajectory("pt-20261027-101010-ab12"):
            session.set_trajectory_step(3)
            second = session.relay_headers(session.next_call())
    after = session.relay_headers()
    assert first["X-NanoMuse-Run"] == "schedule"
    assert first["X-NanoMuse-Turn"] == "4" and first["X-NanoMuse-Step"] == "1"
    assert "X-NanoMuse-Trajectory" not in first
    assert second["X-NanoMuse-Step"] == "2"
    assert second["X-NanoMuse-Trajectory"] == "pt-20261027-101010-ab12"
    assert second["X-NanoMuse-Trajectory-Step"] == "3"
    assert "X-NanoMuse-Run" not in after


def test_a_run_without_a_turn_is_still_named(cloud: FakeCloud):
    with session.run("feed"):
        headers = session.relay_headers()
    assert headers["X-NanoMuse-Run"] == "feed" and "X-NanoMuse-Turn" not in headers


def test_the_run_words_follow_the_message_sources():
    assert session.run_of("user") == "person"
    assert session.run_of("device") == "hub"
    assert session.run_of("goal") == "goal"
    assert session.run_of("reminder") == session.run_of("trigger") == "schedule"
    # a source nobody typed that has no word of its own yet is a run of the schedule's kind
    assert session.run_of("something-new") == "schedule"
    assert session.run_of("") == "person"


def test_an_id_that_is_no_header_value_is_not_sent():
    session.set_device("pc with spaces\n")
    try:
        assert "X-NanoMuse-Device" not in session.relay_headers()
    finally:
        session.set_device("")


# ------------------------------------------------------------------ the clients
async def test_a_call_to_the_relay_carries_the_provenance_and_keeps_its_id(
    wire: Wire, cloud: FakeCloud
):
    llm = create_llm(slot(base_url=RELAY))
    token = session.enter("thread-9")
    try:
        with session.turn("person", 2):
            resp = await llm.ask([Message.user("hi")])
    finally:
        session.leave(token)
    assert wire.header("x-nanomuse-run") == ["person"]
    assert wire.header("x-nanomuse-turn") == ["2"]
    assert wire.header("x-nanomuse-step") == ["1"]
    assert wire.header("x-nanomuse-conversation") == ["thread-9"]
    assert wire.header("x-nanomuse-device") == ["pc-test0001"]
    assert wire.header("x-nanomuse-platform")[0] in ("macos", "windows", "linux", "runtime")
    # the relay's id for the turn it kept, on the response and for the notes after it
    assert resp.request_id == "req-1" and resp.turn_source == "cloud"
    # and nothing sent up as an own-key turn: the relay already has it
    assert cloud.turns == []
    await telemetry.flush()
    assert cloud.turns == []


async def test_the_responses_client_sends_the_same(wire: Wire, cloud: FakeCloud):
    llm = create_llm(slot(base_url=RELAY, protocol="responses"))
    with session.turn("goal", 1):
        resp = await llm.ask([Message.user("hi")])
    assert wire.header("x-nanomuse-run") == ["goal"]
    assert wire.header("x-nanomuse-step") == ["1"]
    assert resp.request_id == "req-1"


async def test_another_vendor_hears_nothing_of_it(wire: Wire, cloud: FakeCloud):
    llm = create_llm(slot(base_url=ELSEWHERE))
    with session.turn("person", 1):
        await llm.ask([Message.user("hi")])
    assert wire.header("x-nanomuse-run") == [None]
    assert wire.header("x-nanomuse-device") == [None]
    assert wire.header("x-nanomuse-platform") == [None]


async def test_a_turn_on_the_persons_own_key_goes_up_to_the_record(wire: Wire, cloud: FakeCloud):
    llm = create_llm(slot(base_url=ELSEWHERE, model="their-model"))
    token = session.enter("thread-3")
    try:
        with session.turn("schedule", 5):
            resp = await llm.ask([Message.system("be brief"), Message.user("hi")])
    finally:
        session.leave(token)
    assert re.fullmatch(r"[0-9a-f]{16}", resp.request_id) and resp.turn_source == "own"
    await telemetry.flush()
    assert len(cloud.turns) == 1
    turn, headers = cloud.turns[0]
    assert turn["id"] == resp.request_id
    assert turn["model"] == "their-model"
    assert turn["provider"]  # the catalogue's word or the protocol's, never the URL
    assert "example-vendor" not in turn["provider"]
    assert [m["role"] for m in turn["messages"]] == ["system", "user"]
    assert turn["response"]["message"] == {
        "role": "assistant",
        "content": "ok",
        "finish_reason": "stop",
    }
    assert turn["response"]["usage"] == {"prompt_tokens": 7, "completion_tokens": 1}
    assert turn["conversation"] == "thread-3"
    assert turn["run"] == "schedule" and turn["turn"] == 5 and turn["step"] == 1
    assert turn["platform"] in ("macos", "windows", "linux", "runtime")
    assert "api_key" not in json.dumps(turn) and "Authorization" not in json.dumps(turn)
    # the device goes with it (the user agent does not say which computer)
    assert headers["X-NanoMuse-Device"] == "pc-test0001"
    assert "X-NanoMuse-Run" not in headers


async def test_signed_out_nothing_is_sent_up(wire: Wire):
    telemetry.install(None)
    llm = create_llm(slot(base_url=ELSEWHERE))
    resp = await llm.ask([Message.user("hi")])
    assert resp.request_id == "" and resp.turn_source == ""
    telemetry.event("turn.feedback", {"vote": "up"})
    await telemetry.flush()  # nothing installed: a no-op


async def test_a_turn_is_sent_once_the_relay_is_back(cloud: FakeCloud):
    telemetry.own_turn(
        {
            "id": "a" * 16,
            "model": "m",
            "messages": [{"role": "user", "content": "x"}],
            "response": {"message": {"role": "assistant", "content": "y"}},
        }
    )
    cloud.fail = CloudError(0, "offline", "away")
    await telemetry.flush()
    assert cloud.turns == []
    cloud.fail = None
    telemetry._paused_until = 0.0
    await telemetry.flush()
    assert [t["id"] for t, _ in cloud.turns] == ["a" * 16]


async def test_a_relay_that_refuses_a_turn_does_not_get_it_again(cloud: FakeCloud):
    telemetry.own_turn(
        {
            "id": "b" * 16,
            "model": "m",
            "messages": [{"role": "user", "content": "x"}],
            "response": {"message": {"role": "assistant", "content": "y"}},
        }
    )
    cloud.fail = CloudError(400, "bad_request", "no")
    await telemetry.flush()
    cloud.fail = None
    await telemetry.flush()
    assert cloud.turns == []


async def test_pictures_go_up_as_files_first(cloud: FakeCloud, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(telemetry_mod, "INLINE_LIMIT", 10)
    data_url = "data:image/png;base64," + ("QUJD" * 20)
    telemetry.own_turn(
        {
            "id": "c" * 16,
            "model": "m",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "response": {"message": {"role": "assistant", "content": "a picture"}},
        }
    )
    await telemetry.flush()
    ((turn, _),) = cloud.turns
    part = turn["messages"][0]["content"][1]
    assert part["image_url"]["url"].startswith("blob:sha256:")
    sha = part["image_url"]["url"].split(":")[-1]
    assert cloud.blobs[sha][0] == "image/png"


# ------------------------------------------------------------------ client events
def test_an_event_keeps_codes_and_numbers_and_drops_prose(cloud: FakeCloud):
    session.note_own_turn("f" * 16)
    try:
        telemetry.event(
            "sentinel.decision",
            {
                "tool": "shell",
                "risk": "moderate",
                "decision": "allow",
                "waited_s": 2.5,
                "tainted": False,
                "note": "the user said: please run `rm -rf` on everything",
                "nested": {"a": 1},
            },
            conversation="thread-1",
        )
    finally:
        session.clear_last_turn()
    (e,) = telemetry._events
    assert e["kind"] == "sentinel.decision"
    assert e["turn"] == "f" * 16 and e["source"] == "own"
    assert e["conversation"] == "thread-1"
    assert e["data"] == {
        "tool": "shell",
        "risk": "moderate",
        "decision": "allow",
        "waited_s": 2.5,
        "tainted": False,
    }
    telemetry._events.clear()


async def test_events_go_up_in_batches_with_the_device(cloud: FakeCloud):
    for i in range(205):
        telemetry.event("hands.step", {"step": i})
    await telemetry.flush()
    assert [len(b) for b, _ in cloud.events] == [100, 100, 5]
    assert cloud.events[0][1]["X-NanoMuse-Device"] == "pc-test0001"


async def test_too_many_requests_pauses_the_notes(cloud: FakeCloud):
    telemetry.event("hands.step", {"step": 1})
    cloud.fail = CloudError(429, "too_many_requests", "slow down")
    await telemetry.flush()
    cloud.fail = None
    await telemetry.flush()  # still paused
    assert cloud.events == []
    telemetry._paused_until = 0.0
    await telemetry.flush()
    assert len(cloud.all_events()) == 1


def test_forgetting_the_key_drops_what_was_queued(cloud: FakeCloud):
    telemetry.event("hands.step", {"step": 1})
    telemetry.own_turn({"id": "d" * 16})
    telemetry.forget()
    assert not telemetry._events and not telemetry._turns


# ------------------------------------------------------------------ the agent loop
async def test_the_agent_loop_counts_its_turns_and_names_the_run(settings: Settings):
    from nanomuse.agent import MuseAgent
    from nanomuse.llm import MockLLM
    from nanomuse.sentinel import AuditLog, Sentinel
    from nanomuse.tools import Terminate, ToolCollection
    from nanomuse.ui import HeadlessUI

    seen: list[tuple[str, int, int]] = []

    def reply(_: list[Message]) -> LLMResponse:
        t = session.current_turn()
        assert t is not None
        seen.append((t.run, t.number, t.calls))
        return LLMResponse(content="done", finish_reason="stop")

    ui = HeadlessUI()
    audit = AuditLog(settings.audit_file)
    sentinel = Sentinel(settings.sentinel, audit, ui)
    agent = MuseAgent(
        settings, MockLLM([reply, reply, reply]), ToolCollection(Terminate()), sentinel, ui, audit
    )
    await agent.run("hi")
    await agent.run("again", run="goal")
    await agent.run("once more")
    # MockLLM does not go through the clients, so the call count stays at what the loop saw
    assert seen == [("person", 1, 0), ("goal", 2, 0), ("person", 3, 0)]
    assert session.current_turn() is None


async def test_the_sentinel_notes_its_decisions(settings: Settings, cloud: FakeCloud):
    from nanomuse.schema import Function, RiskLevel, ToolCall, ToolResult
    from nanomuse.sentinel import AuditLog, Sentinel
    from nanomuse.tools import ToolCollection
    from nanomuse.tools.base import BaseTool, CallAssessment
    from nanomuse.ui import HeadlessUI

    class Careful(BaseTool):
        name: str = "careful"
        description: str = "a tool that asks"
        parameters: dict[str, Any] = {"type": "object", "properties": {}}

        def assess(self, args: dict[str, Any]) -> CallAssessment:
            return CallAssessment(risk=RiskLevel.SENSITIVE, summary="do the careful thing")

        async def execute(self, **kwargs: Any) -> ToolResult:
            return ToolResult(output="done")

    ui = HeadlessUI(approve=True)
    audit = AuditLog(settings.audit_file)
    sentinel = Sentinel(settings.sentinel, audit, ui)
    tool = Careful()
    ToolCollection(tool)
    call = ToolCall(function=Function(name="careful", arguments="{}"))
    result = await sentinel.guard(call, tool)
    assert result.ok
    kinds = [e["kind"] for e in telemetry._events]
    assert "approval.result" in kinds and "sentinel.decision" in kinds
    approval = next(e for e in telemetry._events if e["kind"] == "approval.result")
    assert approval["data"]["tool"] == "careful" and approval["data"]["allow"] is True
    assert approval["data"]["by"] == "person" and approval["data"]["risk"] == "sensitive"
    decision = next(e for e in telemetry._events if e["kind"] == "sentinel.decision")
    assert decision["data"]["decision"] == "allow" and decision["data"]["policy"] == "ask"
    assert decision["data"]["by"] == "person" and decision["data"]["grant"] == "once"
    # nothing of the arguments or the summary travels
    assert "careful thing" not in json.dumps(list(telemetry._events))
    telemetry._events.clear()


# ------------------------------------------------------------------ the server
def test_a_thumb_and_a_stop_reason_reach_the_record(settings: Settings, cloud: FakeCloud):
    import time

    from fastapi.testclient import TestClient

    from nanomuse.llm import MockLLM
    from nanomuse.server import create_app
    from nanomuse.server.service import MuseService

    settings.server.token = "secret-token"
    llm = MockLLM([LLMResponse(content="here you are")])
    service = MuseService(settings, llm=llm)
    app = create_app(settings, service)
    with TestClient(app) as client:
        # the hub installed the real relay client at start; this runtime is signed in to
        # the fake one for the test
        telemetry.install(cloud, lambda: "key-1")  # type: ignore[arg-type]
        client.headers["Authorization"] = "Bearer secret-token"
        client.post("/api/threads/main/send", json={"text": "hi"})
        deadline = time.time() + 5
        assistant: list[dict[str, Any]] = []
        while time.time() < deadline and not assistant:
            assistant = [
                e
                for e in client.get("/api/threads/main/events").json()["events"]
                if e["type"] == "assistant"
            ]
            time.sleep(0.05)
        assert assistant
        r = client.post(
            "/api/threads/main/feedback", json={"event": assistant[0]["id"], "vote": "up"}
        )
        assert r.status_code == 200 and r.json()["event"]["feedback"] == "up"
        r = client.post(
            "/api/threads/main/feedback",
            json={"event": assistant[0]["id"], "vote": "down", "reason": "unhelpful"},
        )
        assert r.status_code == 200 and r.json()["event"]["feedback"] == "down"
        r = client.post("/api/threads/main/feedback", json={"stop_reason": "slow"})
        assert r.status_code == 200
        r = client.post("/api/threads/main/feedback", json={"stop_reason": "because"})
        assert r.status_code == 400
        r = client.post("/api/threads/main/feedback", json={"event": "nope", "vote": "up"})
        assert r.status_code == 404
        noted = cloud.noted()
        kinds = [(e["kind"], e["data"]) for e in noted]
        assert ("turn.feedback", {"vote": "up", "turn_no": 1}) in kinds
        assert ("turn.feedback", {"vote": "down", "turn_no": 1, "reason": "unhelpful"}) in kinds
        assert ("turn.stopped", {"reason": "slow", "turn_no": 1}) in kinds
        assert all(e["conversation"] == "main" for e in noted)
        telemetry._events.clear()
