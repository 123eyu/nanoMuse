"""One question laid out as the steps it took (relay 0.31, docs/cloud.md "Turns and calls").

A person asks once; the agent calls the model as many times as the task needs: once to
choose a tool, once after each tool's result, once to write the reply. The record keeps a
row per call, and every call's request carries the whole conversation so far, so the last
call of a turn holds the turn entire — the question, each tool call, each result, the
screenshots the hands took. Sending every call whole to the console would send that
history as many times as there were calls; this module lays a turn out once instead:
the person's message, then one step per call with what the model said and what came back
before the next call, then the reply. Pictures stay ``blob:sha256:`` references; the
console fetches them from GET /v1/admin/blobs/{sha256}.

Pure functions over the views service._turn_view gives; nothing here reads or writes.
"""

from __future__ import annotations

from typing import Any

from .record import BLOB_PREFIX

#: how much of a tool's result or a message the laid-out turn carries; the whole call is a
#: click away (GET /v1/admin/turns/{id})
TEXT_MAX = 4000
#: the fields of a call the console shows on a step, out of its full view
CALL_FIELDS = (
    "id",
    "ts",
    "model",
    "step",
    "status",
    "source",
    "platform",
    "prompt_tokens",
    "completion_tokens",
    "trajectory",
    "trajectory_step",
    "run",
)


def messages_of(call: dict[str, Any]) -> list[dict[str, Any]]:
    req = call.get("request")
    msgs = req.get("messages") if isinstance(req, dict) else None
    return [m for m in msgs if isinstance(m, dict)] if isinstance(msgs, list) else []


def reply_of(call: dict[str, Any]) -> dict[str, Any]:
    resp = call.get("response")
    msg = resp.get("message") if isinstance(resp, dict) else None
    return msg if isinstance(msg, dict) else {}


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def media_of(content: Any) -> list[str]:
    """The ``blob:sha256:`` references in a message's content, in order."""
    found: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, str):
            if v.startswith(BLOB_PREFIX):
                found.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(content)
    return found


def trim(message: dict[str, Any], limit: int = TEXT_MAX) -> dict[str, Any]:
    """The message with its text cut to ``limit`` characters (``truncated`` says so) and
    every other field as it is — the pictures' references among them."""
    out = dict(message)
    content = message.get("content")
    if isinstance(content, str) and len(content) > limit:
        out["content"] = content[:limit]
        out["truncated"] = True
    elif isinstance(content, list):
        parts = []
        cut = False
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text" and isinstance(p.get("text"), str) and len(p["text"]) > limit:
                parts.append({**p, "text": p["text"][:limit]})
                cut = True
            else:
                parts.append(p)
        out["content"] = parts
        if cut:
            out["truncated"] = True
    return out


def last_user(messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    for m in reversed(messages):
        if m.get("role") == "user":
            return m
    return None


def new_since(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The messages ``current`` (the next call's request) carries that ``previous`` (this
    call's) did not: the tool results, the hands' screenshots, a message the person sent
    while the agent worked. The next request repeats this one's messages and then the
    assistant's message of this call before the new ones, so the new ones start after
    that echo; when the app had compacted the history in between (fewer messages than
    before), what follows the last assistant message is the best reading."""
    n = len(previous)
    if len(current) > n and current[n].get("role") == "assistant":
        return current[n + 1 :]
    if len(current) > n:
        return current[n:]
    for i in range(len(current) - 1, -1, -1):
        if current[i].get("role") == "assistant":
            return current[i + 1 :]
    return []


def tool_names(message: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for tc in message.get("tool_calls") or []:
        fn = tc.get("function") if isinstance(tc, dict) and isinstance(tc.get("function"), dict) else None
        if fn is not None:
            names.append(str(fn.get("name") or "?"))
    return names


def lay_out(calls: list[dict[str, Any]]) -> dict[str, Any]:
    """A turn from its calls (service._turn_view dicts, oldest first): the person's
    message, a step per call — the call's figures, what the model answered (its words or
    the tools it called), and what came back before the next call — and the reply the
    person saw (the last call's words). The pictures and clips are counted and left as
    references."""
    if not calls:
        return {"person": None, "steps": [], "reply": "", "tools": [], "media": 0}
    first_messages = messages_of(calls[0])
    person = last_user(first_messages)
    steps: list[dict[str, Any]] = []
    tools: list[str] = []
    media = len(media_of(person.get("content"))) if person else 0
    for i, call in enumerate(calls):
        reply = reply_of(call)
        names = tool_names(reply)
        tools.extend(names)
        then = new_since(messages_of(call), messages_of(calls[i + 1])) if i + 1 < len(calls) else []
        for m in then:
            media += len(media_of(m.get("content")))
        steps.append(
            {
                "call": {k: call[k] for k in CALL_FIELDS if k in call},
                "messages": len(messages_of(call)),
                "reply": trim(reply),
                "then": [trim(m) for m in then],
            }
        )
    return {
        "person": trim(person) if person else None,
        "steps": steps,
        "reply": text_of(reply_of(calls[-1]).get("content")),
        "tools": tools,
        "media": media,
    }
