"""Pictures for a session's avatar studio: a new look for the Muse, drawn on the showcase's key.

A container's model lives at ``http://gateway/llm/<id>/main`` and speaks the OpenAI images
API there (``POST …/images/generations`` and ``…/images/edits``) when it is told an image
model's name — the gateway tells it ``IMAGE_MODEL``. Those two calls land here rather than
on the chat lane's upstream: Alibaba Cloud Model Studio draws on its own multimodal endpoint,
not an OpenAI-shaped one, so the request is translated the way nanoMuse Cloud's relay does it
(``cloud/nanomuse_cloud/api.py``) and the picture comes back inline as ``b64_json``. A new
face is eight pictures (four candidates, four more poses of the chosen one); a session may
have ``IMAGE_PER_SESSION`` of them and the showcase ``DAILY_IMAGES`` a day, counted apart
from the chat budget so a long chat and a new look do not get in each other's way.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from urllib.parse import urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import Settings
from .sessions import Refused, Session, SessionManager

log = logging.getLogger("showcase.images")

GENERATION_PATH = "/services/aigc/multimodal-generation/generation"
# the provider draws a couple of pictures per account at a time; a 429 behind the gate is
# waited out, with growing pauses
PAUSES = (2.0, 5.0, 10.0)
MAX_UPLOAD = 8 * 2**20


def is_image_path(path: str) -> bool:
    """Whether a model-lane path is one of the two images calls (with or without ``v1/``)."""
    tail = path.strip("/").removeprefix("v1/")
    return tail in ("images/generations", "images/edits")


def _size(raw: object) -> str:
    text = str(raw or "1024x1024").lower().replace("x", "*")
    return text if "*" in text else "1024*1024"


def provider_message(r: httpx.Response) -> str:
    """Model Studio's ``message`` from an error body, or "" when the body is not JSON or
    not an object (a list or a string would otherwise turn the refusal into a 500 here)."""
    try:
        body = r.json()
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return str(message) if message else ""


def _reply(exc: Refused) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": exc.message, "type": exc.code, "code": exc.code}},
        status_code=exc.status,
    )


class Pictures:
    """The translation and the count."""

    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self.s = settings
        self.http = http
        self.gate = asyncio.Semaphore(2)

    @property
    def enabled(self) -> bool:
        return bool(self.s.image_model and self.s.image_api_key and self.s.image_base_url)

    async def handle(
        self, request: Request, manager: SessionManager, sess: Session, path: str
    ) -> Response:
        """One images call from a session's container: the key, the count, the picture."""
        header = request.headers.get("authorization", "")
        key = header[7:].strip() if header.lower().startswith("bearer ") else None
        try:
            manager.authenticate_key(sess, key)
            if not self.enabled or sess.byok:
                raise Refused(404, "no_image_model", "This showcase draws no pictures.")
            manager.check_pictures(sess)
            if path.strip("/").endswith("generations"):
                png = await self._generation(request)
            else:
                png = await self._edit(request)
        except Refused as exc:
            return _reply(exc)
        manager.record_picture(sess)
        return JSONResponse(
            {
                "created": int(time.time()),
                "data": [{"b64_json": base64.b64encode(png).decode()}],
            }
        )

    async def _generation(self, request: Request) -> bytes:
        try:
            body = json.loads(await request.body() or b"{}")
        except ValueError as exc:
            raise Refused(400, "bad_request", "The request is not JSON.") from exc
        prompt = str(body.get("prompt", "")).strip() if isinstance(body, dict) else ""
        if not prompt:
            raise Refused(400, "bad_request", "prompt is required")
        params = {"size": _size(body.get("size")), "watermark": False}
        if self.s.image_model.startswith("qwen-image"):
            params["prompt_extend"] = False
        return await self._draw(self.s.image_model, [{"text": prompt}], params)

    async def _edit(self, request: Request) -> bytes:
        form = await request.form(max_files=2, max_fields=10)
        prompt = str(form.get("prompt", "")).strip()
        image = form.get("image")
        if not prompt or image is None or isinstance(image, str):
            raise Refused(400, "bad_request", "prompt and image are required")
        data = await image.read()
        if len(data) > MAX_UPLOAD:
            raise Refused(413, "too_large", "The picture is too large.")
        mime = image.content_type or "image/png"
        model = self.s.image_model
        # the same rules as the runtime's own Model Studio path: qwen-image-3.x and wan take
        # the picture themselves; an older text-only qwen-image is posed by qwen-image-edit-max
        three_x = model.startswith("qwen-image-3") or model.startswith("wan")
        edit_model = model if (three_x or "edit" in model) else "qwen-image-edit-max"
        params = (
            {"size": _size(form.get("size")), "prompt_extend": False, "watermark": False}
            if three_x
            else {"n": 1, "watermark": False}
        )
        content = [
            {"image": f"data:{mime};base64," + base64.b64encode(data).decode()},
            {"text": prompt},
        ]
        return await self._draw(edit_model, content, params)

    async def _draw(self, model: str, content: list[dict], parameters: dict) -> bytes:
        url = self.s.image_base_url.rstrip("/") + GENERATION_PATH
        payload = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": parameters,
        }
        headers = {
            "authorization": f"Bearer {self.s.image_api_key}",
            "content-type": "application/json",
        }
        for pause in (*PAUSES, None):
            # the gate is held for the request only: a pause before a retry must not keep
            # another visitor's picture waiting
            async with self.gate:
                try:
                    r = await self.http.post(url, headers=headers, content=json.dumps(payload))
                except httpx.HTTPError as exc:
                    log.warning("image provider: %s", exc)
                    raise Refused(502, "upstream", "The image provider did not answer.") from exc
            if r.status_code != 429 and r.status_code < 500 or pause is None:
                break
            log.info("image provider HTTP %s; again in %.0fs", r.status_code, pause)
            await asyncio.sleep(pause)
        if r.status_code == 429:
            raise Refused(429, "provider_busy", "The image provider is busy; try again shortly.")
        if r.status_code >= 400:
            log.warning("image provider HTTP %s: %s", r.status_code, r.text[:300])
            message = provider_message(r)
            detail = f": {message}" if message else ""
            raise Refused(502, "upstream", f"The image provider refused ({r.status_code}{detail})")
        try:
            image_url = r.json()["output"]["choices"][0]["message"]["content"][0]["image"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise Refused(502, "upstream", "The image provider sent no picture.") from exc
        if not self._own_host(str(image_url)):
            log.warning("image URL off the provider's hosts: %s", str(image_url)[:120])
            raise Refused(502, "upstream", "The picture came from somewhere unexpected.")
        try:
            img = await self.http.get(str(image_url))
        except httpx.HTTPError as exc:
            raise Refused(502, "upstream", "The picture could not be fetched.") from exc
        # the client follows no redirects: a 3xx would be an empty body, not a picture
        if img.status_code != 200 or not img.content:
            raise Refused(502, "upstream", "The picture could not be fetched.")
        return img.content

    def _own_host(self, url: str) -> bool:
        """The picture's URL must be on the provider's own hosts (its storage is one of its
        cloud's), never somewhere the reply could send us to."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host:
            return False
        own = (urlsplit(self.s.image_base_url).hostname or "").lower()
        root = ".".join(own.split(".")[-2:]) if own else ""
        return bool(root) and (host == own or host.endswith("." + root))
