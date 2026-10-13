"""Clips for a session's avatar studio: the chosen face animated, on the showcase's key.

After the stills (``images.py``) the studio animates the face — four short clips, one per
mood — through the asynchronous video API, which it looks for at ``[llm] video_base_url``:
the gateway names the session's own model address there (``http://gateway/llm/<id>/main``),
so the four calls of a clip land here. ``GET api/v1/uploads?action=getPolicy`` asks where to
put the first frame, the frame is posted there, ``POST api/v1/services/aigc/video-generation/
video-synthesis`` starts a task, ``GET api/v1/tasks/<id>`` is polled, and the MP4 is fetched.

Model Studio wants the frame in its own storage (an OSS upload with a signed policy) or at a
public URL — neither is to be had from inside the sessions network, which has no internet —
so the gateway stands in for the storage: the policy points the runtime back at the gateway,
the frame is kept for a few minutes, and the task goes up with the frame inline as a data
URL. The finished clip comes back through the gateway too (the provider's storage is just
as unreachable from the container). A session may have ``CLIPS_PER_SESSION`` clips and the
showcase ``DAILY_CLIPS`` a day, counted apart from the pictures and the chat.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import Settings
from .images import provider_message
from .sessions import Refused, Session, SessionManager

log = logging.getLogger("showcase.clips")

SYNTHESIS_PATH = "/services/aigc/video-generation/video-synthesis"
UPLOADS = "api/v1/uploads"
UPLOAD_PUT = "api/v1/uploads/put"
SYNTHESIS = "api/v1/services/aigc/video-generation/video-synthesis"
TASKS = "api/v1/tasks/"
CLIPS = "api/v1/clips/"

MAX_FRAME = 6 * 2**20  # a 1024² PNG is one or two megabytes
MAX_CLIP = 64 * 2**20
FRAMES_PER_SESSION = 4  # live first frames; the studio makes two clips at a time
FRAME_TTL_S = 10 * 60
TASK_TTL_S = 60 * 60
# what a session may say about a clip; the rest of the parameters is ours
PARAMETERS = ("resolution", "duration", "watermark", "prompt_extend", "negative_prompt")
MAX_SECONDS = 5
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{4,80}$")


def is_clip_path(path: str) -> bool:
    """Whether a model-lane path is one of the video API's (the runtime sends them with
    ``api/v1/``, the way Model Studio has them)."""
    tail = path.strip("/")
    return tail in (UPLOADS, UPLOAD_PUT, SYNTHESIS) or tail.startswith((TASKS, CLIPS))


def _reply(exc: Refused) -> JSONResponse:
    # the shape the studio reads an error's message from: Model Studio's `message`, and the
    # OpenAI-style `error` for good measure
    return JSONResponse(
        {
            "code": exc.code,
            "message": exc.message,
            "error": {"message": exc.message, "type": exc.code, "code": exc.code},
        },
        status_code=exc.status,
    )


@dataclass
class Frame:
    sid: str
    data: bytes
    mime: str
    at: float


@dataclass
class Task:
    sid: str
    at: float
    video_url: str = ""  # the provider's, once the task succeeded


class Clips:
    """The stand-in storage, the translation and the count."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.s = settings
        self.http = http
        self.grants: dict[str, tuple[str, float]] = {}  # upload grant → (sid, when)
        self.frames: dict[str, Frame] = {}  # "<sid>/<dir>/first-frame.png" → the frame
        self.tasks: dict[str, Task] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.s.video_model and self.s.video_api_key and self.s.video_base_url)

    def _sweep(self, now: float) -> None:
        for grant, (_sid, at) in list(self.grants.items()):
            if now - at > FRAME_TTL_S:
                del self.grants[grant]
        for key, frame in list(self.frames.items()):
            if now - frame.at > FRAME_TTL_S:
                del self.frames[key]
        for tid, task in list(self.tasks.items()):
            if now - task.at > TASK_TTL_S:
                del self.tasks[tid]

    def _base(self, manager: SessionManager, sess: Session) -> str:
        """The session's model address as its container reaches it, with the video API's
        prefix: where the policy sends the frame and where a finished clip is fetched."""
        return f"{manager.internal_url.rstrip('/')}/llm/{sess.id}/main/"

    async def handle(
        self, request: Request, manager: SessionManager, sess: Session, path: str
    ) -> Response:
        """One call of the video API from a session's container."""
        now = manager.clock()
        self._sweep(now)
        tail = path.strip("/")
        try:
            if not self.enabled or sess.byok:
                raise Refused(404, "no_video_model", "This showcase makes no clips.")
            if tail == UPLOAD_PUT:
                return await self._put(request, sess, now)
            if tail.startswith(CLIPS):
                return await self._clip(sess, tail[len(CLIPS) :])
            # the policy, the task and the polling carry the session's key
            header = request.headers.get("authorization", "")
            key = header[7:].strip() if header.lower().startswith("bearer ") else None
            manager.authenticate_key(sess, key)
            if tail == UPLOADS:
                return self._policy(manager, sess, now)
            if tail == SYNTHESIS:
                return await self._submit(request, manager, sess, now)
            if tail.startswith(TASKS):
                return await self._poll(manager, sess, tail[len(TASKS) :])
            raise Refused(404, "not_found", "No such call.")
        except Refused as exc:
            return _reply(exc)

    # ------------------------------------------------------------------ the frame
    def _policy(self, manager: SessionManager, sess: Session, now: float) -> Response:
        """Where to put the first frame: here. The shape Model Studio's policy has, with a
        grant in place of the signature; the runtime posts it back with the frame."""
        grant = secrets.token_urlsafe(18)
        self.grants[grant] = (sess.id, now)
        return JSONResponse(
            {
                "request_id": secrets.token_hex(8),
                "data": {
                    "upload_host": self._base(manager, sess) + UPLOAD_PUT,
                    "upload_dir": f"{sess.id}/{secrets.token_hex(4)}",
                    "oss_access_key_id": "showcase",
                    "signature": "showcase",
                    "policy": grant,
                    "x_oss_object_acl": "private",
                    "x_oss_forbid_overwrite": "true",
                },
            }
        )

    async def _put(self, request: Request, sess: Session, now: float) -> Response:
        form = await request.form(max_files=1, max_fields=10)
        grant = str(form.get("policy", ""))
        granted = self.grants.pop(grant, None)
        if granted is None or granted[0] != sess.id:
            raise Refused(403, "no_grant", "The upload policy is not one of ours.")
        key = str(form.get("key", "")).strip("/")
        if not key.startswith(sess.id + "/") or "/" not in key[len(sess.id) + 1 :]:
            raise Refused(400, "bad_request", "The key is not one the policy gave.")
        file = form.get("file")
        if file is None or isinstance(file, str):
            raise Refused(400, "bad_request", "file is required")
        data = await file.read()
        if not data:
            raise Refused(400, "bad_request", "The frame is empty.")
        if len(data) > MAX_FRAME:
            raise Refused(413, "too_large", "The frame is too large.")
        mime = (file.content_type or "image/png").split(";")[0].strip() or "image/png"
        if not mime.startswith("image/"):
            raise Refused(400, "bad_request", "The frame must be a picture.")
        mine = sorted(
            (k for k, f in self.frames.items() if f.sid == sess.id), key=lambda k: self.frames[k].at
        )
        while len(mine) >= FRAMES_PER_SESSION:
            del self.frames[mine.pop(0)]
        self.frames[key] = Frame(sess.id, data, mime, now)
        # OSS answers an upload with an empty 204
        return Response(status_code=204)

    # ------------------------------------------------------------------ the task
    async def _submit(
        self, request: Request, manager: SessionManager, sess: Session, now: float
    ) -> Response:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError as exc:
            raise Refused(400, "bad_request", "The request is not JSON.") from exc
        if not isinstance(body, dict):
            raise Refused(400, "bad_request", "The request is not JSON.")
        raw_input = body.get("input")
        inp: dict[str, Any] = raw_input if isinstance(raw_input, dict) else {}
        prompt = str(inp.get("prompt", "")).strip()[:800]
        img = str(inp.get("img_url", ""))
        if not prompt or not img.startswith("oss://"):
            raise Refused(400, "bad_request", "prompt and the uploaded frame are required")
        frame = self.frames.get(img[len("oss://") :].strip("/"))
        if frame is None or frame.sid != sess.id:
            raise Refused(
                400, "no_frame", "The frame is not here (uploaded, and not too long ago?)"
            )
        manager.check_clips(sess)
        raw_parameters = body.get("parameters")
        given: dict[str, Any] = raw_parameters if isinstance(raw_parameters, dict) else {}
        parameters = {k: v for k, v in given.items() if k in PARAMETERS}
        if isinstance(parameters.get("duration"), (int, float)):
            parameters["duration"] = min(int(parameters["duration"]), MAX_SECONDS)
        parameters["watermark"] = False
        payload = {
            "model": self.s.video_model,
            "input": {
                "prompt": prompt,
                "img_url": f"data:{frame.mime};base64,{base64.b64encode(frame.data).decode()}",
            },
            "parameters": parameters,
        }
        url = self.s.video_base_url.rstrip("/") + SYNTHESIS_PATH
        headers = {
            "authorization": f"Bearer {self.s.video_api_key}",
            "content-type": "application/json",
            "x-dashscope-async": "enable",
        }
        try:
            r = await self.http.post(url, headers=headers, content=json.dumps(payload))
        except httpx.HTTPError as exc:
            log.warning("video provider: %s", exc)
            raise Refused(502, "upstream", "The video provider did not answer.") from exc
        if r.status_code == 429:
            # the studio itself waits and asks again
            raise Refused(429, "provider_busy", "The video provider is busy; try again shortly.")
        if r.status_code >= 400:
            raise Refused(
                502, "upstream", f"The video provider refused ({r.status_code}{self._detail(r)})"
            )
        try:
            data = r.json()
            task = str(data["output"]["task_id"])
        except (ValueError, KeyError, TypeError) as exc:
            raise Refused(502, "upstream", "The video provider started no task.") from exc
        if not _TASK_ID.match(task):
            raise Refused(502, "upstream", "The video provider started no task.")
        self.tasks[task] = Task(sess.id, now)
        manager.record_clip(sess)
        log.info(
            "session %s: clip task started (%d of %d)",
            sess.id,
            sess.clips,
            self.s.clips_per_session,
        )
        return JSONResponse(data)

    async def _poll(self, manager: SessionManager, sess: Session, task: str) -> Response:
        item = self.tasks.get(task)
        if item is None or item.sid != sess.id:
            raise Refused(404, "no_task", "No such task.")
        url = f"{self.s.video_base_url.rstrip('/')}/tasks/{task}"
        try:
            r = await self.http.get(
                url, headers={"authorization": f"Bearer {self.s.video_api_key}"}
            )
        except httpx.HTTPError as exc:
            raise Refused(502, "upstream", "The video provider did not answer.") from exc
        if r.status_code >= 400:
            raise Refused(
                502, "upstream", f"The video provider refused ({r.status_code}{self._detail(r)})"
            )
        try:
            data = r.json()
        except ValueError as exc:
            raise Refused(502, "upstream", "The video provider sent no task.") from exc
        out = (
            data.get("output")
            if isinstance(data, dict) and isinstance(data.get("output"), dict)
            else None
        )
        if out is not None and out.get("video_url"):
            video_url = str(out["video_url"])
            if not self._own_host(video_url):
                log.warning("clip URL off the provider's hosts: %s", video_url[:120])
                raise Refused(502, "upstream", "The clip came from somewhere unexpected.")
            item.video_url = video_url
            # the container cannot reach the provider's storage: the clip comes through here
            out["video_url"] = self._base(manager, sess) + CLIPS + task
        return JSONResponse(data)

    async def _clip(self, sess: Session, task: str) -> Response:
        item = self.tasks.get(task)
        if item is None or item.sid != sess.id or not item.video_url:
            raise Refused(404, "no_clip", "No such clip.")
        try:
            r = await self.http.get(item.video_url)
        except httpx.HTTPError as exc:
            raise Refused(502, "upstream", "The clip could not be fetched.") from exc
        if r.status_code >= 400 or not r.content:
            raise Refused(502, "upstream", "The clip could not be fetched.")
        if len(r.content) > MAX_CLIP:
            raise Refused(502, "too_large", "The clip is too large.")
        return Response(r.content, media_type=r.headers.get("content-type", "video/mp4"))

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        log.warning("video provider HTTP %s: %s", r.status_code, r.text[:300])
        message = provider_message(r)
        return f": {message}" if message else ""

    def _own_host(self, url: str) -> bool:
        """The clip's URL must be on the provider's own hosts (its storage is one of its
        cloud's), never somewhere the reply could send us to."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host:
            return False
        own = (urlsplit(self.s.video_base_url).hostname or "").lower()
        root = ".".join(own.split(".")[-2:]) if own else ""
        return bool(root) and (host == own or host.endswith("." + root))
