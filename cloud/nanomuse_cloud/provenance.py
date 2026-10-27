"""Where a turn came from (relay 0.31, docs/cloud.md "Provenance").

A model call is one of many in a conversation, and the apps know things about it that the
request body does not say: who started it (a person typing, a scheduled task, the feed's
routine, a goal's check, another device through the hub), which turn of the conversation it
belongs to and which step of that turn it is, and, for a screen operation, which task and
which step of the task. The apps say so in a few request headers on a Cloud turn and in the
same fields on a turn they ran on a key of their own (POST /v1/turns). The relay keeps them
beside the turn, the training view and the call's telemetry row, so a reader can tell a
person's question from a routine's run and one model call from the turn it belongs to.

This module is the vocabulary and the parsing: no storage, no network. Everything it
accepts is a short id or a number; nothing here ever carries words from a conversation.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

#: who started the turn, as the apps say it in `X-NanoMuse-Run` / `run`. `person`: someone
#: typed; `schedule`: a scheduled task; `feed`: the feed's daily routine; `goal`: a goal's
#: check or evaluation; `hub`: this device ran it for another device's request. An app from
#: before the header sends nothing and the relay stores "" (not said), never a guess.
KNOWN_RUNS = ("person", "schedule", "feed", "goal", "hub")
_RUN_RE = re.compile(r"[a-z][a-z0-9_]{0,23}")
_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,80}")
_MAX_INDEX = 1_000_000_000

#: the header names (lower case, as Starlette hands them) and the body fields, in order
HEADERS = {
    "run": "x-nanomuse-run",
    "turn": "x-nanomuse-turn",
    "step": "x-nanomuse-step",
    "trajectory": "x-nanomuse-trajectory",
    "trajectory_step": "x-nanomuse-trajectory-step",
    "platform": "x-nanomuse-platform",
}
FIELDS = tuple(HEADERS)

#: the platforms an app may name outright (`X-NanoMuse-Platform`); "other" and the unbranded
#: upstream app are what the user agent says, never a claim
PLATFORM_CLAIMS = ("android", "ios", "desktop", "windows", "macos", "linux", "runtime", "browser")

EMPTY: dict[str, Any] = {"run": "", "turn": 0, "step": 0, "trajectory": "", "trajectory_step": 0, "platform": ""}


def _run(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if _RUN_RE.fullmatch(s) else ""


def _index(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 0
    return n if 0 < n <= _MAX_INDEX else 0


def _ident(value: Any) -> str:
    s = str(value or "").strip()
    return s if _ID_RE.fullmatch(s) else ""


def platform_claim(value: Any) -> str:
    s = str(value or "").strip().lower()
    return s if s in PLATFORM_CLAIMS else ""


def clean(values: Mapping[str, Any]) -> dict[str, Any]:
    """The six fields from any mapping of them, each validated, the rest of the mapping
    ignored. A value the relay cannot keep reads as not said."""
    return {
        "run": _run(values.get("run")),
        "turn": _index(values.get("turn")),
        "step": _index(values.get("step")),
        "trajectory": _ident(values.get("trajectory")),
        "trajectory_step": _index(values.get("trajectory_step")),
        "platform": platform_claim(values.get("platform")),
    }


def from_headers(headers: Mapping[str, str]) -> dict[str, Any]:
    """The fields as a request's headers say them (a Cloud turn, or the fallback for an
    uploaded one)."""
    return clean({field: headers.get(name) for field, name in HEADERS.items()})


def from_body(data: Mapping[str, Any], headers: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The fields of an uploaded turn (POST /v1/turns): the body's say, a header filling
    in what the body left out."""
    out = clean(data)
    if headers:
        fallback = from_headers(headers)
        for k, v in fallback.items():
            if not out[k]:
                out[k] = v
    return out


def said(values: Mapping[str, Any]) -> dict[str, Any]:
    """Only the fields that were said, for a meta JSON or an export line."""
    return {k: v for k, v in values.items() if v}


# -- the language of what the person wrote --------------------------------------------------
#
# The apps' Accept-Language header is empty on most turns, so the relay looks at the
# person's last message itself and keeps one word about its script: enough to tell the
# Chinese turns from the English ones and the Japanese from both. It never keeps the text.

_RANGES = (
    ("zh", (0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x20000, 0x2A6DF), (0xF900, 0xFAFF)),
    ("ja", (0x3040, 0x309F), (0x30A0, 0x30FF), (0x31F0, 0x31FF)),
    ("ko", (0xAC00, 0xD7AF), (0x1100, 0x11FF), (0x3130, 0x318F)),
    ("ru", (0x0400, 0x04FF)),
    ("ar", (0x0600, 0x06FF), (0x0750, 0x077F)),
    ("th", (0x0E00, 0x0E7F)),
    ("hi", (0x0900, 0x097F)),
    ("he", (0x0590, 0x05FF)),
    ("el", (0x0370, 0x03FF)),
    ("latin", (0x0041, 0x005A), (0x0061, 0x007A), (0x00C0, 0x024F)),
)


def script_of(text: str, limit: int = 2000) -> str:
    """One word for the script of ``text``: ``zh``, ``ja``, ``ko``, ``ru``, ``ar``, ``th``,
    ``hi``, ``he``, ``el``, ``latin``, or "" when there are no letters. Kana in a text that
    is otherwise Han is Japanese (Japanese prose carries both; Chinese carries no kana).
    Looks at the first ``limit`` characters."""
    counts: dict[str, int] = {}
    for ch in text[:limit]:
        o = ord(ch)
        for name, *ranges in _RANGES:
            if any(lo <= o <= hi for lo, hi in ranges):
                counts[name] = counts.get(name, 0) + 1
                break
    if not counts:
        return ""
    if counts.get("ja", 0) >= 2:
        return "ja"
    if counts.get("ko", 0) >= 2:
        return "ko"
    return max(counts.items(), key=lambda kv: kv[1])[0]


def text_of(content: Any) -> str:
    """The words of a message's content: a string, or the text parts of a list."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def last_user_text(messages: Any) -> str:
    """The person's last message in a request's messages, as words."""
    if not isinstance(messages, list):
        return ""
    for m in reversed(messages):
        if isinstance(m, dict) and m.get("role") == "user":
            return text_of(m.get("content"))
    return ""


# -- the export's subject -------------------------------------------------------------------


def subject_of(secret: str, account_id: str) -> str:
    """The pseudonym an exported line carries instead of the account id: HMAC-SHA256 of
    the account id under the relay's secret, 16 hex characters. The same account is the
    same subject in every export, so a training set can group one person's turns and
    drop them all when the person deletes their data (the deletions ledger names the
    subject); without the secret the account cannot be recovered from it."""
    return hmac.new(secret.encode("utf-8"), b"export-subject:" + account_id.encode("utf-8"), hashlib.sha256).hexdigest()[:16]


# -- what the apps report after a turn -------------------------------------------------------
#
# A client event is a short record of something that happened in the app around a turn,
# never a message: the Sentinel's decision on a tool call, an approval's result, why the
# person stopped a reply, a thumb up or down. Codes and numbers only; the shape is enforced
# here and the privacy page repeats it.

#: the event kinds the apps send and what their `data` holds (docs/cloud.md "Client events")
KNOWN_EVENTS = (
    "sentinel.decision",  # {tool, risk: safe|moderate|sensitive|critical, decision: allow|ask|deny, grant?: once|schedule|conversation|always}
    "approval.result",  # {tool?, risk?, allow: bool, scope?: once|schedule|conversation|always, by?: person|timeout|other_device}
    "turn.stopped",  # {reason: wrong|slow|changed_mind|done|other|skipped}
    "turn.feedback",  # {vote: up|down, reason?: wrong|unhelpful|unsafe|slow|other}
    "turn.ended",  # {ended: aborted|error|abandoned}
    "hands.step",  # {trajectory, step, action, outcome?: ok|failed|handed_over}
    "app.opened",  # {screen?}
    "screen.viewed",  # {screen}
)
_EVENT_KIND_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){0,3}")
_CODE_RE = re.compile(r"[A-Za-z0-9_.:/ -]{0,120}")
EVENT_DATA_MAX_KEYS = 16
EVENT_DATA_MAX_CHARS = 2000
EVENTS_PER_REQUEST = 100


def clean_event(e: Any) -> dict[str, Any] | None:
    """One event as the relay keeps it, or None for one it will not: a `kind` of the
    dotted form, an optional `ts` (seconds), optional `turn` and `conversation` ids, and
    `data`, a flat object of short codes, numbers and booleans (no nested objects, no
    text past 120 characters). The limits are the promise that no message travels here."""
    if not isinstance(e, dict):
        return None
    kind = str(e.get("kind") or "").strip()
    if not _EVENT_KIND_RE.fullmatch(kind) or len(kind) > 48:
        return None
    data_in = e.get("data") if isinstance(e.get("data"), dict) else {}
    data: dict[str, Any] = {}
    for k, v in list(data_in.items())[:EVENT_DATA_MAX_KEYS]:
        key = str(k)[:32]
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key):
            continue
        if isinstance(v, bool) or v is None:
            data[key] = v
        elif isinstance(v, (int, float)):
            data[key] = v if abs(v) < 1e15 else None
        elif isinstance(v, str):
            s = v.strip()
            if _CODE_RE.fullmatch(s):
                data[key] = s
        # lists and objects: never
    if len(json.dumps(data, ensure_ascii=False)) > EVENT_DATA_MAX_CHARS:
        return None
    ts = e.get("ts")
    turn = str(e.get("turn") or "").strip()
    return {
        "kind": kind,
        "ts": int(ts) if isinstance(ts, (int, float)) and 0 < ts < 4_102_444_800 else 0,
        "turn_id": turn if _TURN_ID_RE.fullmatch(turn) else "",
        "conversation": _ident(e.get("conversation")),
        "data": data,
    }


#: a turn's id as the apps saw it: the relay's request id, or an uploaded turn's `own-…` id
_TURN_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,96}")
