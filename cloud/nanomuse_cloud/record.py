"""The record (0.29): every chat turn, in full, and the pictures that went with it.

Until 0.28 the relay kept, of a conversation, the training view of a turn and nothing else
(service.keep_sample): the words, with the system prompt, the tools' results and every
picture left out. The record keeps the turn as it happened — the request as the app sent
it and the answer as the model gave it — so a person's devices can read it back and the
operator can see a problem whole. Three parts live here, none of them touching the
database:

- :class:`MediaStore` — pictures, audio and clips on disk, one file per distinct content,
  named by its SHA-256. A screenshot that an app resends with every turn of a screen
  operation (the whole history goes up each time) is stored once.
- :func:`detach_media` — a request's messages with every inline picture (``data:`` URL)
  taken out and a ``blob:sha256:<hex>`` reference left in its place, plus the list of what
  was taken out. :func:`attach_media` is the way back.
- :class:`StreamAssembler` — the assistant's message put together from a stream's deltas
  (content, reasoning, tool calls), so a streamed turn is kept in the same shape as one
  answered whole.

``pack`` / ``unpack`` are the row format: JSON, zlib-compressed. A turn's words compress
five- to tenfold; the pictures are files.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import tempfile
import zlib
from pathlib import Path
from typing import Any

#: the reference left in a message where a picture was; the hash is the file's name
BLOB_PREFIX = "blob:sha256:"
_DATA_URL = re.compile(r"^data:([\w.+-]+/[\w.+-]+)?(;[\w.=-]+)*;base64,(.*)$", re.DOTALL)
_SHA = re.compile(r"^[0-9a-f]{64}$")

#: what an ``input_audio`` part's ``format`` means as a MIME type
_AUDIO_MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "ogg": "audio/ogg", "flac": "audio/flac", "webm": "audio/webm", "m4a": "audio/mp4"}


def is_sha256(text: str) -> bool:
    return bool(_SHA.fullmatch(text or ""))


def pack(obj: Any) -> bytes:
    """A JSON value as the compressed bytes a turn's row holds."""
    return zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode(), 6)


def unpack(blob: bytes | None) -> Any:
    """The value back; ``None`` for an empty or unreadable row (a page never fails on one)."""
    if not blob:
        return None
    try:
        return json.loads(zlib.decompress(blob))
    except (zlib.error, ValueError, TypeError):
        return None


class MediaStore:
    """Content-addressed files under one directory: ``<root>/<aa>/<sha256>``.

    Without a root (the tests, a relay on an in-memory database) the files go to a
    temporary directory that is removed with the store. Writes are atomic (a temporary
    name, then a rename), so a crash never leaves a half picture under a real hash."""

    #: files a delete could not remove (a permission, a disk that was read-only for a moment),
    #: one hash a line, tried again by :meth:`retry_deletes` from the hourly sweep — the
    #: database forgot them the moment they were deleted, so the store remembers for it
    RETRY_FILE = ".unlink-retry"

    def __init__(self, root: str | Path | None = None):
        self._tmp: tempfile.TemporaryDirectory | None = None
        if root is None or root == "":
            self._tmp = tempfile.TemporaryDirectory(prefix="nanomuse-media-")
            root = self._tmp.name
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sha: str) -> Path:
        if not is_sha256(sha):
            raise ValueError("not a sha256")
        return self.root / sha[:2] / sha

    def has(self, sha: str) -> bool:
        return is_sha256(sha) and self.path(sha).is_file()

    def put(self, data: bytes) -> tuple[str, bool]:
        """The bytes under their hash; answers the hash and whether the file is new."""
        sha = hashlib.sha256(data).hexdigest()
        target = self.path(sha)
        if target.is_file():
            return sha, False
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{sha}.{secrets.token_hex(4)}.part")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
        return sha, True

    def read(self, sha: str) -> bytes | None:
        try:
            return self.path(sha).read_bytes()
        except (OSError, ValueError):
            return None

    def delete(self, sha: str) -> bool:
        """The file gone; True when it was there. A file that will not go (anything but
        "already gone") is noted for :meth:`retry_deletes`, so the person's delete is
        honoured even when the disk said no at that moment."""
        try:
            self.path(sha).unlink()
            return True
        except FileNotFoundError:
            return False
        except ValueError:
            return False
        except OSError:
            self._remember(sha)
            return False

    def _remember(self, sha: str) -> None:
        try:
            with open(self.root / self.RETRY_FILE, "a", encoding="ascii") as f:
                f.write(sha + "\n")
        except OSError:
            pass  # the disk is the problem; the next sweep's walk of the list will not help either

    def retry_deletes(self) -> int:
        """Try the noted deletes again (the hourly sweep); answers how many files went. What
        still will not go stays on the list."""
        path = self.root / self.RETRY_FILE
        try:
            pending = [line.strip() for line in path.read_text(encoding="ascii").splitlines() if is_sha256(line.strip())]
        except OSError:
            return 0
        if not pending:
            return 0
        left: list[str] = []
        gone = 0
        for sha in dict.fromkeys(pending):  # each once, in order
            try:
                self.path(sha).unlink()
                gone += 1
            except FileNotFoundError:
                continue
            except OSError:
                left.append(sha)
        try:
            if left:
                path.write_text("".join(s + "\n" for s in left), encoding="ascii")
            else:
                path.unlink()
        except OSError:
            pass
        return gone

    def close(self) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


def parse_data_url(url: str) -> tuple[str, bytes] | None:
    """``(mime, bytes)`` of a base64 ``data:`` URL; ``None`` for anything else (an https URL,
    a reference, a URL that does not decode)."""
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    m = _DATA_URL.match(url)
    if m is None:
        return None
    try:
        raw = base64.b64decode(m.group(3), validate=False)
    except (ValueError, binascii.Error):
        return None
    return (m.group(1) or "application/octet-stream").lower(), raw


class _Detacher:
    """One pass over a request's messages, collecting the media it takes out."""

    def __init__(self, store: MediaStore):
        self.store = store
        self.media: dict[str, dict] = {}

    def keep(self, mime: str, raw: bytes) -> str:
        sha, _new = self.store.put(raw)
        self.media.setdefault(sha, {"sha256": sha, "mime": mime, "bytes": len(raw)})
        return BLOB_PREFIX + sha

    def url(self, value: Any) -> Any:
        parsed = parse_data_url(value) if isinstance(value, str) else None
        return self.keep(*parsed) if parsed else value

    def part(self, part: Any) -> Any:
        if not isinstance(part, dict):
            return part
        kind = part.get("type")
        out = dict(part)
        if kind == "image_url" and isinstance(part.get("image_url"), dict):
            out["image_url"] = {**part["image_url"], "url": self.url(part["image_url"].get("url"))}
        elif kind == "image_url" and isinstance(part.get("image_url"), str):
            out["image_url"] = self.url(part["image_url"])
        elif kind == "input_audio" and isinstance(part.get("input_audio"), dict):
            audio = part["input_audio"]
            data = audio.get("data")
            if isinstance(data, str) and not data.startswith(BLOB_PREFIX):
                parsed = parse_data_url(data)
                if parsed is None:
                    try:
                        raw = base64.b64decode(data, validate=False)
                    except (ValueError, binascii.Error):
                        raw = b""
                    parsed = (_AUDIO_MIME.get(str(audio.get("format") or "").lower(), "application/octet-stream"), raw) if raw else None
                if parsed:
                    out["input_audio"] = {**audio, "data": self.keep(*parsed)}
        elif kind in ("video_url", "video"):
            inner = part.get(kind)
            if isinstance(inner, dict):
                out[kind] = {**inner, "url": self.url(inner.get("url"))}
            elif isinstance(inner, list):  # Qwen's frame list
                out[kind] = [self.url(v) for v in inner]
            elif isinstance(inner, str):
                out[kind] = self.url(inner)
        return out

    def message(self, m: Any) -> Any:
        if not isinstance(m, dict):
            return m
        content = m.get("content")
        if isinstance(content, list):
            return {**m, "content": [self.part(p) for p in content]}
        return m


def detach_media(messages: list, store: MediaStore) -> tuple[list, list[dict]]:
    """The messages with every inline picture, audio clip or video written to ``store`` and
    a ``blob:sha256:`` reference in its place; and the list of what was written
    (``sha256``, ``mime``, ``bytes``), each once."""
    d = _Detacher(store)
    out = [d.message(m) for m in messages]
    return out, list(d.media.values())


def media_refs(value: Any) -> set[str]:
    """Every ``blob:sha256:`` hash referenced anywhere in a JSON value (a turn a client
    uploaded names the files it put up first; the relay checks it may)."""
    found: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, str):
            if v.startswith(BLOB_PREFIX) and is_sha256(v[len(BLOB_PREFIX) :]):
                found.add(v[len(BLOB_PREFIX) :])
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return found


def inline_media_bytes(value: Any) -> int:
    """About how many bytes the ``data:`` URLs anywhere in a JSON value decode to (three
    quarters of the base64), before any is decoded: an upload is measured against the
    account's cap first, written after."""
    total = 0

    def walk(v: Any) -> None:
        nonlocal total
        if isinstance(v, str):
            if v.startswith("data:"):
                comma = v.find(",")
                if comma > 0:
                    total += (len(v) - comma - 1) * 3 // 4
        elif isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(value)
    return total


def attach_media(value: Any, store: MediaStore, mimes: dict[str, str]) -> Any:
    """The way back: every reference replaced by the ``data:`` URL it stood for, when the
    file is still there (a trimmed picture stays a reference)."""
    if isinstance(value, str):
        if value.startswith(BLOB_PREFIX):
            sha = value[len(BLOB_PREFIX) :]
            raw = store.read(sha) if is_sha256(sha) else None
            if raw is not None:
                return f"data:{mimes.get(sha, 'application/octet-stream')};base64,{base64.b64encode(raw).decode()}"
        return value
    if isinstance(value, dict):
        return {k: attach_media(v, store, mimes) for k, v in value.items()}
    if isinstance(value, list):
        return [attach_media(v, store, mimes) for v in value]
    return value


class StreamAssembler:
    """The assistant's message from a stream's chunks, in the shape a whole answer has:
    ``content`` joined, ``reasoning_content`` joined when the model thought aloud,
    ``tool_calls`` merged by index with their arguments concatenated, ``finish_reason``."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.tool_calls: dict[int, dict] = {}
        self.finish_reason: str | None = None
        self.chunks = 0

    def feed(self, obj: dict) -> None:
        self.chunks += 1
        for ch in obj.get("choices") or []:
            if not isinstance(ch, dict):
                continue
            delta = ch.get("delta")
            if isinstance(delta, dict):
                if isinstance(delta.get("content"), str):
                    self.content.append(delta["content"])
                for key in ("reasoning_content", "reasoning"):
                    if isinstance(delta.get(key), str):
                        self.reasoning.append(delta[key])
                for tc in delta.get("tool_calls") or []:
                    if not isinstance(tc, dict):
                        continue
                    try:
                        idx = int(tc.get("index", len(self.tool_calls)))
                    except (TypeError, ValueError):
                        idx = len(self.tool_calls)
                    cur = self.tool_calls.setdefault(idx, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        cur["id"] = str(tc["id"])
                    if tc.get("type"):
                        cur["type"] = str(tc["type"])
                    fn = tc.get("function")
                    if isinstance(fn, dict):
                        if fn.get("name"):
                            cur["function"]["name"] = str(fn["name"])
                        if isinstance(fn.get("arguments"), str):
                            cur["function"]["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                self.finish_reason = str(ch["finish_reason"])

    def message(self) -> dict:
        out: dict = {"role": "assistant", "content": "".join(self.content)}
        if self.reasoning:
            out["reasoning_content"] = "".join(self.reasoning)
        if self.tool_calls:
            out["tool_calls"] = [self.tool_calls[i] for i in sorted(self.tool_calls)]
        if self.finish_reason:
            out["finish_reason"] = self.finish_reason
        return out
