"""What this runtime tells nanoMuse Cloud about its turns beyond the requests themselves.

Two things, both for a signed-in account and nothing without one (docs/cloud.md, relay 0.31):

* **Client events** — short records of codes and numbers around a turn, never a message:
  what the Sentinel decided about a tool call and at which risk, how an approval went, why
  the person stopped a reply, a thumb up or down on an answer, how a screen-operation step
  went. :func:`event` queues one; they go up in batches (``POST /v1/events``).
* **Turns on the person's own key** — a turn answered by a provider the person configured
  is sent up afterwards (``POST /v1/turns``), so the account's record is whole wherever the
  model was and, with *Help improve* on, the relay keeps its training view like a Cloud
  turn's. Pictures go first as files by their hash (``PUT /v1/blobs``), the turn names them.
  :func:`own_turn` queues one.

The queues are bounded and in memory; a relay that is away loses nothing but the oldest
notes, and a failure is logged, never the person's problem. The hub service installs the
relay client when the account is signed in (:func:`install`) and takes it away at sign-out.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from loguru import logger

from nanomuse.cloud import CloudClient, CloudError
from nanomuse.llm import session as llm_session

#: the relay takes at most this many events in one request
EVENTS_PER_REQUEST = 100
#: how long a note waits before it goes up with the others
FLUSH_EVERY_S = 5.0
#: an own-key turn with pictures past this size inline goes up as files first
INLINE_LIMIT = 2 * 1024 * 1024
#: an own-key turn the relay would refuse outright (TURN_MAX_CHARS) is cut, not dropped
TURN_MAX_CHARS = 1_900_000

_DATA_URL = re.compile(r"^data:([\w.+-]+/[\w.+-]+);base64,(.*)$", re.DOTALL)
_CODE = re.compile(r"[A-Za-z0-9_.:/ -]{0,120}")


class Telemetry:
    def __init__(self) -> None:
        self._cloud: CloudClient | None = None
        self._key: Callable[[], str] = lambda: ""
        self._events: deque[dict[str, Any]] = deque(maxlen=1000)
        self._turns: deque[dict[str, Any]] = deque(maxlen=50)
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._sent_blobs: set[str] = set()
        self._paused_until = 0.0
        #: own-key turns go up while this says so (the person's *Help improve* switch is the
        #: relay's to apply; this is whether the account is signed in at all)
        self.upload_turns = True

    # ------------------------------------------------------------------ lifecycle
    def install(self, cloud: CloudClient | None, key: Callable[[], str] | None = None) -> None:
        """The relay client to send through (``None`` at sign-out: the queues are dropped,
        nothing of an account that signed out is sent later)."""
        self._cloud = cloud
        self._key = key or (lambda: "")
        if cloud is None:
            self._events.clear()
            self._turns.clear()

    def forget(self) -> None:
        """At sign-out: nothing queued under the account goes up later."""
        self._events.clear()
        self._turns.clear()
        self._sent_blobs.clear()

    @property
    def ready(self) -> bool:
        return self._cloud is not None and bool(self._key())

    def start(self) -> None:
        """The sender loop on the running loop; idempotent."""
        if self._task is not None and not self._task.done():
            return
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="telemetry")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - shutting down
            pass
        # one last try for what is queued, briefly
        try:
            await asyncio.wait_for(self.flush(), timeout=3)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ notes
    def event(
        self,
        kind: str,
        data: dict[str, Any] | None = None,
        *,
        turn: tuple[str, str] | None = None,
        conversation: str = "",
    ) -> None:
        """One client event. ``turn`` is ``(id, source)`` as :func:`llm_session.last_turn`
        gives it, else the last call of this context; ``data`` holds codes, numbers and
        booleans only (a value that is not one is left out here, before it travels)."""
        if not self.ready:
            return
        tid, source = turn if turn is not None else llm_session.last_turn()
        clean: dict[str, Any] = {}
        for k, v in (data or {}).items():
            key = str(k)[:32]
            if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
                clean[key] = v
            elif isinstance(v, str):
                s = v.strip()[:120]
                if _CODE.fullmatch(s):
                    clean[key] = s
        e: dict[str, Any] = {"kind": kind, "ts": int(time.time()), "data": clean}
        if tid:
            e["turn"] = tid
            if source == "own":
                e["source"] = "own"
        conv = conversation or llm_session.current_conversation() or ""
        if conv:
            e["conversation"] = conv[:80]
        self._events.append(e)
        self._nudge()

    def own_turn(self, turn: dict[str, Any]) -> None:
        """A turn run on the person's own key, in the relay's shape (``model``, ``messages``,
        ``response``, ``provider``, ``ts``, ``conversation``, ``id`` and the provenance
        fields); queued, sent in the background."""
        if not self.ready or not self.upload_turns:
            return
        self._turns.append(turn)
        self._nudge()

    def _nudge(self) -> None:
        if self._wake is not None:
            self._wake.set()

    # ------------------------------------------------------------------ sending
    async def _loop(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=FLUSH_EVERY_S)
            except TimeoutError:
                pass
            self._wake.clear()
            # notes made within the same few seconds go together
            await asyncio.sleep(0.5)
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - never the person's problem
                logger.debug("telemetry: {}", exc)

    async def flush(self) -> None:
        """Send what is queued, oldest first; a relay that refuses or is away keeps the rest
        for the next round (a 429 pauses a minute)."""
        if not self.ready or time.monotonic() < self._paused_until:
            return
        cloud = self._cloud
        assert cloud is not None
        cloud.api_key = self._key()
        headers = self._headers()
        while self._events:
            batch = [
                self._events.popleft() for _ in range(min(EVENTS_PER_REQUEST, len(self._events)))
            ]
            try:
                await cloud.post_events(batch, headers=headers)
            except CloudError as exc:
                self._handle(exc, batch, self._events)
                break
        while self._turns:
            turn = self._turns.popleft()
            try:
                await self._send_turn(cloud, turn, headers)
            except CloudError as exc:
                self._handle(exc, [turn], self._turns)
                break

    def _handle(self, exc: CloudError, batch: list[dict[str, Any]], queue: deque[Any]) -> None:
        if exc.status == 429:
            self._paused_until = time.monotonic() + 60
        elif exc.status in (0, 502, 503, 504):
            self._paused_until = time.monotonic() + 15
        elif exc.status in (401, 403, 404):
            # signed out meanwhile, or a relay without the record: nothing to resend
            logger.debug(
                "telemetry: the relay said {} ({}); dropping the batch", exc.status, exc.code
            )
            return
        else:
            logger.debug("telemetry: {} {}; dropping the batch", exc.status, exc.code)
            return
        # put the batch back in front, newest of them last
        for item in reversed(batch):
            queue.appendleft(item)

    def _headers(self) -> dict[str, str]:
        headers = llm_session.relay_headers()
        # the provenance of the call under way is not that of the notes going up now
        return {
            k: v for k, v in headers.items() if k in ("X-NanoMuse-Device", "X-NanoMuse-Platform")
        }

    async def _send_turn(
        self, cloud: CloudClient, turn: dict[str, Any], headers: dict[str, str]
    ) -> None:
        body = dict(turn)
        messages = body.get("messages")
        if isinstance(messages, list) and inline_bytes(messages) > INLINE_LIMIT:
            body["messages"] = await self._detach(cloud, messages)
        text = json.dumps(body, ensure_ascii=False)
        if len(text) > TURN_MAX_CHARS:
            msgs = body.get("messages") or []
            rest = len(text) - len(json.dumps(msgs, ensure_ascii=False))
            body["messages"] = _cut(msgs, TURN_MAX_CHARS - rest)
        await cloud.post_turn(body, headers=headers)

    async def _detach(self, cloud: CloudClient, messages: list[Any]) -> list[Any]:
        """Pictures out of the messages and up as files, a ``blob:sha256:`` reference where
        each was. A file the relay would not take stays inline."""
        out: list[Any] = []
        for m in messages:
            if not isinstance(m, dict) or not isinstance(m.get("content"), list):
                out.append(m)
                continue
            parts = []
            for part in m["content"]:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    url = (
                        (part.get("image_url") or {}).get("url")
                        if isinstance(part.get("image_url"), dict)
                        else None
                    )
                    match = _DATA_URL.match(str(url or ""))
                    if match:
                        try:
                            data = base64.b64decode(match.group(2), validate=False)
                        except ValueError:
                            parts.append(part)
                            continue
                        sha = hashlib.sha256(data).hexdigest()
                        try:
                            if sha not in self._sent_blobs and not await cloud.has_blob(sha):
                                await cloud.put_blob(sha, match.group(1), data)
                            self._sent_blobs.add(sha)
                        except CloudError as exc:
                            if exc.status in (0, 429, 502, 503, 504):
                                raise
                            parts.append(part)  # too big, or a kind the relay keeps no file of
                            continue
                        image = dict(part["image_url"])
                        image["url"] = f"blob:sha256:{sha}"
                        parts.append({**part, "image_url": image})
                        continue
                parts.append(part)
            out.append({**m, "content": parts})
        return out


def inline_bytes(messages: list[Any]) -> int:
    """How many bytes of pictures the messages carry inline, roughly (base64 as it is)."""
    n = 0
    for m in messages:
        if isinstance(m, dict) and isinstance(m.get("content"), list):
            for part in m["content"]:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    image = part.get("image_url")
                    url = str(image.get("url") or "") if isinstance(image, dict) else ""
                    if url.startswith("data:"):
                        n += len(url)
    return n


def _cut(messages: list[Any], room: int) -> list[Any]:
    """The messages within ``room`` characters of JSON: the first and the last kept, the
    middle dropped oldest first; the relay's own cut would refuse the turn whole."""
    msgs = list(messages)
    while len(msgs) > 2 and len(json.dumps(msgs, ensure_ascii=False)) > room:
        del msgs[1]
    return msgs


def turn_body(
    *,
    turn_id: str,
    model: str,
    provider: str,
    messages: list[dict[str, Any]],
    response: dict[str, Any],
    usage: dict[str, Any] | None,
    conversation: str,
    extra: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The relay's shape of an own-key turn (``POST /v1/turns``): the request as it was sent
    (less the key, which never leaves the client), the assistant's message, the usage, and
    where the turn sits."""
    body: dict[str, Any] = {
        "id": turn_id,
        "ts": int(time.time()),
        "model": model[:120],
        "provider": provider[:60],
        "messages": messages,
        "response": {"message": response},
    }
    if usage:
        u = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
        }
        details = usage.get("prompt_tokens_details")
        cached = (
            details.get("cached_tokens")
            if isinstance(details, dict)
            else usage.get("cached_tokens")
        )
        if cached:
            u["cached_tokens"] = int(cached or 0)
        body["response"]["usage"] = u
    if conversation:
        body["conversation"] = conversation[:80]
    for k, v in (extra or {}).items():
        if k in ("temperature", "max_tokens", "tools", "tool_choice", "top_p") and v is not None:
            body[k] = v
    for k, v in (provenance or {}).items():
        if v:
            body[k] = v
    return body


#: the one instance the runtime shares
telemetry = Telemetry()

__all__ = ["Telemetry", "telemetry", "turn_body", "inline_bytes"]
