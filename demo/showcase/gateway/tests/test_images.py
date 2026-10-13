"""A new look for a demo Muse: the images calls, translated and counted."""

from __future__ import annotations

import base64
from dataclasses import replace

import httpx

from showcase_gateway.app import create_app
from showcase_gateway.images import is_image_path
from showcase_gateway.sessions import SessionManager

from .conftest import body


async def client_for(app) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://localhost:8000")


def test_the_two_paths():
    assert is_image_path("images/generations") and is_image_path("/v1/images/edits")
    assert not is_image_path("chat/completions") and not is_image_path("images/variations")


async def test_a_new_look_is_drawn_on_model_studio_and_counted(world):
    settings, runner, upstream, clock, manager, app = world
    async with app.router.lifespan_context(app):
        c = await client_for(app)
        info = body(await c.get("/api/demo/info"))
        assert info["image_model"] == "qwen-image-3.0" and info["quota"]["pictures"] == 2
        sess = body(await c.post("/api/demo/session", json={}))
        env = runner.running[f"nm-{sess['id']}"]
        # the container is told the image model's name, so its studio draws through us
        assert env["NANOMUSE_LLM_IMAGE_MODEL"] == "qwen-image-3.0"
        key = env["NANOMUSE_LLM_API_KEY"]
        auth = {"authorization": f"Bearer {key}"}
        base = f"/llm/{sess['id']}/main"

        # the wrong key, then a generation
        r = await c.post(
            f"{base}/images/generations",
            json={"model": "qwen-image-3.0", "prompt": "a red panda"},
            headers={"authorization": "Bearer nope"},
        )
        assert r.status_code == 401
        r = await c.post(
            f"{base}/images/generations",
            json={
                "model": "qwen-image-3.0",
                "prompt": "a red panda",
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
            },
            headers=auth,
        )
        assert r.status_code == 200, r.text
        assert base64.b64decode(body(r)["data"][0]["b64_json"]) == b"PNGBYTES"
        sent = upstream.calls[-2]  # the drawing, then the fetch of the picture
        assert sent.headers["authorization"] == "Bearer sk-draw"
        assert str(sent.url) == (
            "https://draw.example/api/v1/services/aigc/multimodal-generation/generation"
        )
        drawn = upstream.drawn[-1]
        assert drawn["model"] == "qwen-image-3.0"
        assert drawn["input"]["messages"][0]["content"] == [{"text": "a red panda"}]
        assert drawn["parameters"] == {
            "size": "1024*1024",
            "watermark": False,
            "prompt_extend": False,
        }

        # an edit: the picture goes along, inline; 3.x models pose the picture themselves
        r = await c.post(
            f"{base}/v1/images/edits",
            data={
                "model": "qwen-image-3.0",
                "prompt": "now with headphones",
                "n": "1",
                "size": "1024x1024",
                "response_format": "b64_json",
            },
            files={"image": ("idle.png", b"IDLE", "image/png")},
            headers=auth,
        )
        assert r.status_code == 200, r.text
        drawn = upstream.drawn[-1]
        assert drawn["model"] == "qwen-image-3.0"
        content = drawn["input"]["messages"][0]["content"]
        assert content[0] == {
            "image": "data:image/png;base64," + base64.b64encode(b"IDLE").decode()
        }
        assert content[1] == {"text": "now with headphones"}

        # counted apart from the chat: two pictures, no chat requests
        s = manager.get(sess["id"])
        assert s.pictures == 2 and s.requests == 0 and manager.day_pictures == 2
        shown = body(
            await c.get(
                f"/api/demo/session/{sess['id']}",
                headers={"authorization": f"Bearer {sess['token']}"},
            )
        )
        assert shown["quota"]["pictures_used"] == 2 and shown["quota"]["pictures"] == 2
        # the third is one too many for this session
        r = await c.post(
            f"{base}/images/generations", json={"model": "m", "prompt": "more"}, headers=auth
        )
        assert r.status_code == 429 and body(r)["error"]["type"] == "picture_budget"
        assert body(await c.get("/api/demo/info"))["day_pictures"] == 2


async def test_the_days_share_and_a_busy_provider(world):
    settings, runner, upstream, clock, manager, app = world
    async with app.router.lifespan_context(app):
        c = await client_for(app)
        sess = body(await c.post("/api/demo/session", json={}))
        key = runner.running[f"nm-{sess['id']}"]["NANOMUSE_LLM_API_KEY"]
        auth = {"authorization": f"Bearer {key}"}
        base = f"/llm/{sess['id']}/main"
        manager.day_pictures = 3  # today's share is spent already
        r = await c.post(
            f"{base}/images/generations", json={"model": "m", "prompt": "x"}, headers=auth
        )
        assert r.status_code == 429 and body(r)["error"]["type"] == "daily_pictures"
        manager.day_pictures = 0
        # a provider that says 429 once is asked again (the pause is skipped in tests)
        upstream.draw_busy = 1
        from showcase_gateway import images

        images.PAUSES = (0.0, 0.0, 0.0)
        r = await c.post(
            f"{base}/images/generations", json={"model": "m", "prompt": "x"}, headers=auth
        )
        assert r.status_code == 200 and len(upstream.drawn) == 2
        # and no prompt is no picture
        r = await c.post(f"{base}/images/generations", json={"model": "m"}, headers=auth)
        assert r.status_code == 400
        # a refusal whose body is JSON but not an object (a list, a string) is still a
        # refusal here, with the status in it, not a 500 from reading .get on a list
        for text in ("[]", '"quota"', '{"message": "Arrearage"}'):
            upstream.draw_refusal = (403, text)
            r = await c.post(
                f"{base}/images/generations", json={"model": "m", "prompt": "x"}, headers=auth
            )
            assert r.status_code == 502, r.text
            assert body(r)["error"]["message"].startswith("The image provider refused (403")
        assert body(r)["error"]["message"].endswith(": Arrearage)")
        upstream.draw_refusal = None


async def test_no_pictures_without_a_model_or_on_your_own_key(world):
    settings, runner, upstream, clock, manager, app = world
    async with app.router.lifespan_context(app):
        c = await client_for(app)
        # a visitor's own provider draws nothing through us: no model name for the container
        own = {"base_url": "https://byok.example/v1", "api_key": "sk-visitor-1", "model": "m"}
        sess = body(await c.post("/api/demo/session", json={"provider": own}))
        env = runner.running[f"nm-{sess['id']}"]
        assert "NANOMUSE_LLM_IMAGE_MODEL" not in env
        r = await c.post(
            f"/llm/{sess['id']}/main/images/generations",
            json={"model": "m", "prompt": "x"},
            headers={"authorization": f"Bearer {env['NANOMUSE_LLM_API_KEY']}"},
        )
        assert r.status_code == 404 and body(r)["error"]["type"] == "no_image_model"

    # a showcase without an image model: none advertised, none drawn, nothing in the env
    off = replace(settings, image_model="")
    # the first app closed the shared client on its way out; this one gets its own
    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
    manager = SessionManager(off, runner, http=client, clock=clock)
    manager.resolve = lambda host, port: ["93.184.216.34"]
    app = create_app(off, manager, client=client)
    async with app.router.lifespan_context(app):
        c = await client_for(app)
        info = body(await c.get("/api/demo/info"))
        assert info["image_model"] is None and info["quota"]["pictures"] == 0
        r = await c.post("/api/demo/session", json={})
        assert r.status_code == 201, r.text
        sess = body(r)
        assert "NANOMUSE_LLM_IMAGE_MODEL" not in runner.running[f"nm-{sess['id']}"]
