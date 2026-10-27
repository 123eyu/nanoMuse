"""Which conversation a model call belongs to, for the vendors that want to be told.

OpenCode Go asks every client to name itself with its own user agent and to send one stable id
per conversation in ``x-opencode-session`` (opencode.ai/docs/go, "Where can I use it?"). The
catalogue names such a header in an entry's ``session_header``; the chat clients add it to each
request with :func:`session_id`, and name nanoMuse with :data:`USER_AGENT`.

The agent loop says which conversation it is working on with :func:`conversation`, a context
variable, so every call made while the turn runs (the hands' model included) carries the same
id without threading it through each ``ask``. A call made outside a conversation (a connection
test, a background summary) carries the client's own id instead, stable for as long as the
client lives.

nanoMuse Cloud wants more than the conversation (relay 0.31, docs/cloud.md "Provenance"):
who started the turn (:func:`turn`: a person, a scheduled task, the feed's routine, a goal's
check, another device through the hub), which turn of the conversation it is and which model
call within it, and for a screen operation which task and step (:func:`trajectory`). The
same context variables carry them; :func:`relay_headers` writes them as the ``X-NanoMuse-*``
headers for a call to the relay, and :func:`provenance` as the fields of a turn sent up from
a key of the person's own. The relay's answer names the turn it kept
(``x-nanomuse-request``); :func:`note_response` keeps that id for the turn's later word on
it (a thumb, why it was stopped), see :mod:`nanomuse.telemetry`.
"""

from __future__ import annotations

import platform as _platform
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from nanomuse import __version__

_conversation: ContextVar[str | None] = ContextVar("nanomuse_conversation", default=None)

#: what a header value may be as it is: visible ASCII, short enough for any gateway
_HEADER_SAFE = re.compile(r"^[\x21-\x7e]{1,128}$")
_NAMESPACE = uuid.UUID("5b0f7c1e-3a52-4f4e-9d0e-6e616e6f4d75")

#: the user agent nanoMuse names itself with on every model call, as the phone apps do
USER_AGENT = f"nanoMuse/{__version__}"


@contextmanager
def conversation(conversation_id: str) -> Iterator[None]:
    """Model calls made inside the block belong to this conversation."""
    token = _conversation.set(conversation_id)
    try:
        yield
    finally:
        _conversation.reset(token)


def enter(conversation_id: str) -> Token[str | None]:
    """:func:`conversation` for a caller whose block is a ``try``: pass the token to
    :func:`leave` in its ``finally``."""
    return _conversation.set(conversation_id)


def leave(token: Token[str | None]) -> None:
    _conversation.reset(token)


def current_conversation() -> str | None:
    return _conversation.get()


def session_id(fallback: str) -> str:
    """The id to send for the current call: the conversation's, else ``fallback``. An id that
    is not a safe header value (spaces, non-ASCII, very long) is sent as a UUID derived from
    it, so it stays the same for the same conversation."""
    raw = _conversation.get() or fallback
    return raw if _HEADER_SAFE.match(raw) else str(uuid.uuid5(_NAMESPACE, raw))


def client_headers(extra: dict[str, str] | None, session_header: str = "") -> dict[str, str]:
    """The headers a client sends on every request: the slot's ``extra_headers``, plus
    nanoMuse's own user agent (one the person set in ``extra_headers`` stands). Every vendor
    is told the client's name, as the phone apps and the desktop tell it: nanoMuse Cloud
    answers the nanoMuse clients only and would take the SDK's own string for a script, and
    a vendor with a session header (OpenCode Go) asks for the name outright."""
    headers = dict(extra or {})
    if not any(k.lower() == "user-agent" for k in headers):
        headers["User-Agent"] = USER_AGENT
    return headers


def new_fallback() -> str:
    """A client's own id, for the calls made outside any conversation."""
    return str(uuid.uuid4())


# ---------------------------------------------------------------- where a turn comes from

#: who started the turn, in the relay's words (docs/cloud.md "Provenance")
RUNS = ("person", "schedule", "feed", "goal", "hub")
#: the runtime's `source` words for a queued message, as the relay wants them
RUN_OF_SOURCE = {
    "user": "person",
    "device": "hub",
    "goal": "goal",
    "reminder": "schedule",
    "trigger": "schedule",
    "routine": "schedule",
    "feed": "feed",
}


@dataclass
class Turn:
    """One turn of a conversation as the agent loop runs it: who started it, its number,
    and a counter of the model calls made within it (the hands' included)."""

    run: str = "person"
    number: int = 0
    calls: int = 0


@dataclass
class Trajectory:
    """One screen-operation task: its id (the operator's trace id) and the step under way."""

    id: str = ""
    step: int = 0


_turn: ContextVar[Turn | None] = ContextVar("nanomuse_turn", default=None)
_trajectory: ContextVar[Trajectory | None] = ContextVar("nanomuse_trajectory", default=None)
_run_only: ContextVar[str] = ContextVar("nanomuse_run", default="")
#: the id the relay gave the last call made in this context, and where it ran
#: ("cloud": the relay's `x-nanomuse-request`; "own": the id of the turn sent up from the
#: person's own key); ("", "") before any
_last_turn: ContextVar[tuple[str, str]] = ContextVar("nanomuse_last_turn", default=("", ""))

#: what this runtime says of itself to the relay, set once by the hub service
_device_id = ""
_cloud_host = "cloud.nanomuse.cn"


def run_of(source: str) -> str:
    """The relay's word for a message's ``source`` (``user``, ``goal``, ``reminder``, …)."""
    return RUN_OF_SOURCE.get(source, "schedule" if source else "person")


@contextmanager
def turn(run: str, number: int) -> Iterator[Turn]:
    """Model calls made inside the block belong to turn ``number`` of the conversation,
    started by ``run`` (one of :data:`RUNS`)."""
    t = Turn(run=run if run in RUNS else "person", number=max(0, int(number)))
    token = _turn.set(t)
    try:
        yield t
    finally:
        _turn.reset(token)


def begin_turn(run: str, number: int) -> Token[Turn | None]:
    """:func:`turn` for a caller whose block is a ``try``: pass the token to :func:`end_turn`."""
    return _turn.set(Turn(run=run if run in RUNS else "person", number=max(0, int(number))))


def end_turn(token: Token[Turn | None]) -> None:
    _turn.reset(token)


@contextmanager
def run(kind: str) -> Iterator[None]:
    """Calls made inside the block were started by ``kind`` (a routine's single call, made
    outside the agent loop: the feed)."""
    token = _run_only.set(kind if kind in RUNS else "schedule")
    try:
        yield
    finally:
        _run_only.reset(token)


@contextmanager
def trajectory(trajectory_id: str) -> Iterator[Trajectory]:
    """Calls made inside the block belong to one screen-operation task; the operator's loop
    says which step with :func:`set_trajectory_step`."""
    t = Trajectory(id=_ident(trajectory_id))
    token = _trajectory.set(t)
    try:
        yield t
    finally:
        _trajectory.reset(token)


def set_trajectory_step(step: int) -> None:
    t = _trajectory.get()
    if t is not None:
        t.step = max(0, int(step))


def current_turn() -> Turn | None:
    return _turn.get()


def next_call() -> int:
    """One more model call in the current turn; its number within the turn (0 outside one)."""
    t = _turn.get()
    if t is None:
        return 0
    t.calls += 1
    return t.calls


def set_device(device_id: str) -> None:
    """This runtime's device id on the hub, sent as ``X-NanoMuse-Device``."""
    global _device_id
    _device_id = _ident(device_id)


def set_cloud_base(base_url: str) -> None:
    """The relay's address, so a slot pointed at it is told the provenance (and no other
    vendor hears of it)."""
    global _cloud_host
    host = (urlparse(base_url or "").hostname or "").lower()
    if host:
        _cloud_host = host


def is_cloud(base_url: str | None) -> bool:
    """Whether a slot's endpoint is nanoMuse Cloud (its models, under the account's key)."""
    return (urlparse(base_url or "").hostname or "").lower() == _cloud_host


def runtime_platform() -> str:
    """The relay's word for the system this runtime is on (``X-NanoMuse-Platform``)."""
    system = _platform.system().lower()
    return {"darwin": "macos", "windows": "windows", "linux": "linux"}.get(system, "runtime")


def provenance(call_number: int | None = None) -> dict[str, Any]:
    """Where the next model call sits, as the relay's fields (``run``, ``turn``, ``step``,
    ``trajectory``, ``trajectory_step``, ``platform``); only the ones that are known.
    ``call_number`` is the call's number within the turn when the caller counted it
    (:func:`next_call`), else the turn's count so far."""
    out: dict[str, Any] = {"platform": runtime_platform()}
    t = _turn.get()
    if t is not None:
        out["run"] = t.run
        if t.number:
            out["turn"] = t.number
        step = t.calls if call_number is None else call_number
        if step:
            out["step"] = step
    elif _run_only.get():
        out["run"] = _run_only.get()
    tr = _trajectory.get()
    if tr is not None and tr.id:
        out["trajectory"] = tr.id
        if tr.step:
            out["trajectory_step"] = tr.step
    return out


_HEADER_OF = {
    "run": "X-NanoMuse-Run",
    "turn": "X-NanoMuse-Turn",
    "step": "X-NanoMuse-Step",
    "trajectory": "X-NanoMuse-Trajectory",
    "trajectory_step": "X-NanoMuse-Trajectory-Step",
    "platform": "X-NanoMuse-Platform",
}


def relay_headers(call_number: int | None = None) -> dict[str, str]:
    """The ``X-NanoMuse-*`` headers of a call to the relay: the device and the conversation
    (1.0.7) and the provenance (relay 0.31)."""
    headers: dict[str, str] = {}
    if _device_id:
        headers["X-NanoMuse-Device"] = _device_id
    conv = _conversation.get()
    if conv:
        headers["X-NanoMuse-Conversation"] = (
            conv if _HEADER_SAFE.match(conv) else str(uuid.uuid5(_NAMESPACE, conv))
        )
    for key, value in provenance(call_number).items():
        headers[_HEADER_OF[key]] = str(value)
    return headers


def note_response(response: Any) -> None:
    """An httpx response hook: the relay names the turn it kept in ``x-nanomuse-request``;
    the id is kept for this context, so the turn's later word on it finds it."""
    try:
        rid = response.headers.get("x-nanomuse-request")
    except Exception:  # noqa: BLE001 - a hook never fails a call
        rid = None
    if rid:
        _last_turn.set((str(rid)[:96], "cloud"))


def note_own_turn(turn_id: str) -> None:
    """A turn sent up from the person's own key: its id, as the relay will know it."""
    _last_turn.set((_ident(turn_id), "own"))


def clear_last_turn() -> None:
    _last_turn.set(("", ""))


def last_turn() -> tuple[str, str]:
    """The relay's id for the last model call made in this context and where it ran
    (``cloud`` or ``own``); ``("", "")`` when there was none."""
    return _last_turn.get()


_IDENT = re.compile(r"[A-Za-z0-9_.:-]{1,80}")


def _ident(value: str) -> str:
    v = str(value or "").strip()
    return v if _IDENT.fullmatch(v) else ""


__all__ = [
    "RUNS",
    "USER_AGENT",
    "Trajectory",
    "Turn",
    "begin_turn",
    "clear_last_turn",
    "client_headers",
    "conversation",
    "current_conversation",
    "current_turn",
    "end_turn",
    "enter",
    "is_cloud",
    "last_turn",
    "leave",
    "new_fallback",
    "next_call",
    "note_own_turn",
    "note_response",
    "provenance",
    "relay_headers",
    "run",
    "run_of",
    "runtime_platform",
    "session_id",
    "set_cloud_base",
    "set_device",
    "set_trajectory_step",
    "trajectory",
    "turn",
]
