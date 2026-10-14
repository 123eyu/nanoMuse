"""Custom OpenAI-compatible endpoints, several at once (#294): ``/api/connections/endpoints``.

The list behind the web console's *Add endpoint*: each entry has a stable id, a name, an
address, a key in the vault and a model list; a slot is set from one by id. The endpoint's
``/models`` is a stub on ``fetch_models``; nothing reaches the network.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nanomuse.config import Settings, apply_app_settings, load_app_settings
from nanomuse.llm import MockLLM
from nanomuse.server import connections as conn_module
from nanomuse.server import create_app
from nanomuse.server.endpoints import host_label, key_name, typed_model_ids
from nanomuse.server.service import MuseService
from nanomuse.vault.vault import CredentialVault

GATEWAY = "https://gateway.example/v1"
SECOND = "https://second.example/v1"


def _make(settings: Settings) -> tuple[TestClient, MuseService]:
    settings.server.token = "secret-token"
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer secret-token"
    return client, service


@pytest.fixture()
def server(settings: Settings) -> Iterator[tuple[TestClient, MuseService]]:
    client, service = _make(settings)
    with client:
        yield client, service


@pytest.fixture()
def listed(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """What each address lists on ``GET /models``, and the key each call carried."""
    answers: dict[str, list[str]] = {GATEWAY: ["b-model", "a-model", "qwen-image-3.0"]}
    keys: list[str] = []

    async def fake(base_url: str, key: str = "") -> tuple[list[str], str]:
        keys.append(key)
        ids = answers.get(base_url, [])
        return (sorted(ids), "") if ids else ([], "404")

    monkeypatch.setattr(conn_module, "fetch_models", fake)
    answers["__keys__"] = keys
    return answers


def _endpoints(client: TestClient) -> list[dict]:
    return client.get("/api/connections").json()["endpoints"]


def test_helpers():
    assert key_name("ep-7f3a9c21") == "ENDPOINT_EP_7F3A9C21_API_KEY"
    assert host_label("https://gateway.example/v1") == "gateway.example"
    assert host_label("http://127.0.0.1:8000/v1") == "127.0.0.1:8000"
    assert typed_model_ids("glm-4.7, glm-4.7,  , kimi-k2.5\n") == ["glm-4.7", "kimi-k2.5"]
    assert typed_model_ids(["a", "b", "a"]) == ["a", "b"]
    assert typed_model_ids(None) == []
    assert len(typed_model_ids(",".join(f"m{i}" for i in range(60)))) == 40


def test_add_lists_the_models_and_keeps_the_key_in_the_vault(server, listed, settings):
    client, service = server
    assert _endpoints(client) == []
    assert client.get("/api/connections").json()["llm"]["endpoint"] == ""
    # the key is refused with a space; the relay is the account, not an endpoint; no scheme is added
    assert (
        client.post(
            "/api/connections/endpoints", json={"base_url": GATEWAY, "api_key": "sk x"}
        ).status_code
        == 400
    )
    relay = client.post(
        "/api/connections/endpoints", json={"base_url": settings.cloud.base_url + "/v1"}
    )
    assert relay.status_code == 400 and "nanoMuse Cloud" in relay.json()["detail"]
    added = client.post(
        "/api/connections/endpoints",
        json={"base_url": "gateway.example/v1", "api_key": "sk-gateway-1", "models": "my-typed"},
    )
    assert added.status_code == 200, added.text
    entry = added.json()
    assert entry["id"].startswith("ep-") and entry["label"] == "gateway.example"
    assert entry["base_url"] == GATEWAY and entry["key_source"] == "vault"
    # typed ids first, the endpoint's own after them; nothing uses it yet
    assert entry["models"] == ["my-typed", "a-model", "b-model", "qwen-image-3.0"]
    assert entry["slots"] == []
    assert listed["__keys__"][-1] == "sk-gateway-1"
    # the key is in the vault under the endpoint's own name and nowhere else
    name = key_name(entry["id"])
    assert service.app.vault.get(name) == "sk-gateway-1"
    assert "sk-gateway" not in json.dumps(entry)
    saved = load_app_settings(settings.data_dir)
    assert "sk-gateway" not in json.dumps(saved)
    assert saved["endpoints"][0]["api_key"] == "{{vault:" + name + "}}"
    # an endpoint that lists nothing and had nothing typed is refused before anything is written
    silent = client.post("/api/connections/endpoints", json={"base_url": SECOND, "api_key": "sk-2"})
    assert silent.status_code == 400 and "type the ids" in silent.json()["detail"]
    assert len(_endpoints(client)) == 1
    assert [n for n in service.app.vault.names() if n.startswith("ENDPOINT_")] == [name]
    # typed ids make it; a server without a key is allowed
    second = client.post(
        "/api/connections/endpoints",
        json={"base_url": SECOND, "label": "Second", "models": ["s-1"]},
    )
    assert second.status_code == 200
    assert second.json()["key_source"] == "none" and second.json()["models"] == ["s-1"]


def test_slots_are_set_from_an_endpoint_by_id(server, listed, settings):
    client, service = server
    first = client.post(
        "/api/connections/endpoints", json={"base_url": GATEWAY, "api_key": "sk-1"}
    ).json()
    second = client.post(
        "/api/connections/endpoints", json={"base_url": SECOND, "models": "s-1, s-vl"}
    ).json()
    # the chat model: the slot takes the address and the key by reference, never the key itself
    r = client.put("/api/connections/llm", json={"endpoint": first["id"], "model": "a-model"})
    assert r.status_code == 200, r.text
    assert r.json()["endpoint"] == first["id"] and r.json()["key_source"] == "vault"
    assert settings.llm.base_url == GATEWAY and settings.llm.model == "a-model"
    assert settings.llm.api_key == "{{vault:" + key_name(first["id"]) + "}}"
    assert "sk-1" not in json.dumps(load_app_settings(settings.data_dir))
    # the hands and the pictures from the other endpoint, which has no key
    hands = client.put("/api/connections/gui", json={"endpoint": second["id"], "model": "s-vl"})
    assert hands.status_code == 200, hands.text
    assert hands.json()["endpoint"] == second["id"] and settings.gui.base_url == SECOND
    assert settings.gui.model == "s-vl" and settings.gui.api_key == ""
    image = client.put("/api/connections/image", json={"endpoint": second["id"], "model": "s-1"})
    assert image.status_code == 200, image.text
    assert image.json()["endpoint"] == second["id"] and settings.image.base_url == SECOND
    view = {e["id"]: e for e in _endpoints(client)}
    assert view[first["id"]]["slots"] == ["chat"]
    assert view[second["id"]]["slots"] == ["hands", "image"]
    # an id nobody has is a plain 400
    assert (
        client.put("/api/connections/llm", json={"endpoint": "ep-nobody", "model": "x"}).status_code
        == 400
    )
    # the model list for a slot's picker: the endpoint's own key is used, the saved list stands in
    models = client.post("/api/llm/models", json={"endpoint": first["id"]}).json()
    assert models["source"] == "live" and listed["__keys__"][-1] == "sk-1"
    kept = client.post("/api/llm/models", json={"endpoint": second["id"]}).json()
    assert kept == {
        "models": ["s-1", "s-vl"],
        "image_models": [],
        "video_models": [],
        "source": "catalogue",
        "error": "404",
    }
    assert service.app.vault.get(key_name(second["id"])) is None


def test_rename_rekey_and_move_follow_through_to_the_slots(server, listed, settings):
    client, service = server
    first = client.post(
        "/api/connections/endpoints", json={"base_url": GATEWAY, "api_key": "sk-1"}
    ).json()
    client.put("/api/connections/llm", json={"endpoint": first["id"], "model": "a-model"})
    client.put("/api/connections/gui", json={"endpoint": first["id"], "model": "b-model"})
    path = f"/api/connections/endpoints/{first['id']}"
    # rename: the label only
    renamed = client.put(path, json={"label": "  Work gateway  "}).json()
    assert renamed["label"] == "Work gateway" and renamed["slots"] == ["chat", "hands"]
    # a new key: the vault entry changes under the same name, so the slots need no change
    rekeyed = client.put(path, json={"api_key": "sk-1-new"})
    assert rekeyed.status_code == 200
    assert service.app.vault.get(key_name(first["id"])) == "sk-1-new"
    assert settings.llm.api_key == "{{vault:" + key_name(first["id"]) + "}}"
    assert listed["__keys__"][-1] == "sk-1-new", (
        "the model list is asked for again with the new key"
    )
    # a new address: the slots that used the endpoint move with it; the models stay when it lists none
    listed[SECOND] = []
    moved = client.put(path, json={"base_url": SECOND}).json()
    assert moved["base_url"] == SECOND
    assert moved["models"] == ["a-model", "b-model", "qwen-image-3.0"]
    assert settings.llm.base_url == SECOND and settings.gui.base_url == SECOND
    assert settings.llm.model == "a-model" and settings.gui.model == "b-model"
    assert client.get("/api/connections").json()["llm"]["endpoint"] == first["id"]
    # typed models replace the list, the endpoint's own after them
    listed[SECOND] = ["listed-z"]
    retyped = client.put(path, json={"models": "typed-one"}).json()
    assert retyped["models"] == ["typed-one", "listed-z"]
    # asking again keeps typed ids out: the live list is the truth
    again = client.post(path + "/models").json()
    assert again["source"] == "live" and again["models"] == ["listed-z"]
    # an unknown id is 404 for both
    assert (
        client.put("/api/connections/endpoints/ep-nobody", json={"label": "x"}).status_code == 404
    )
    assert client.post("/api/connections/endpoints/ep-nobody/models").status_code == 404


def test_remove_refuses_the_chat_endpoint_and_clears_the_dependent_slots(server, listed, settings):
    client, service = server
    first = client.post(
        "/api/connections/endpoints", json={"base_url": GATEWAY, "api_key": "sk-1"}
    ).json()
    second = client.post(
        "/api/connections/endpoints", json={"base_url": SECOND, "api_key": "sk-2", "models": "s-1"}
    ).json()
    client.put("/api/connections/llm", json={"endpoint": first["id"], "model": "a-model"})
    client.put("/api/connections/gui", json={"endpoint": first["id"], "model": "b-model"})
    client.put("/api/connections/video", json={"endpoint": first["id"], "model": "wan-x"})
    # the chat model answers through it: refused with a sentence, nothing changes
    refused = client.delete(f"/api/connections/endpoints/{first['id']}")
    assert refused.status_code == 409 and "chat model" in refused.json()["detail"]
    assert len(_endpoints(client)) == 2 and service.app.vault.get(key_name(first["id"])) == "sk-1"
    # the chat moves to the other endpoint; now the first can go, and the hands and clips go back to automatic
    client.put("/api/connections/llm", json={"endpoint": second["id"], "model": "s-1"})
    assert settings.llm.api_key == "{{vault:" + key_name(second["id"]) + "}}"
    gone = client.delete(f"/api/connections/endpoints/{first['id']}")
    assert gone.status_code == 200 and gone.json() == {"ok": True}
    assert [e["id"] for e in _endpoints(client)] == [second["id"]]
    assert service.app.vault.get(key_name(first["id"])) is None
    assert settings.gui.model == "" and settings.gui.base_url is None
    assert settings.video.configured is False
    data = client.get("/api/connections").json()
    assert data["gui"]["endpoint"] == "" and data["video"]["endpoint"] == ""
    assert data["llm"]["endpoint"] == second["id"]
    assert client.delete(f"/api/connections/endpoints/{first['id']}").status_code == 404


def _app_settings(settings: Settings, llm: dict) -> None:
    """A chat slot set from the app on an earlier run, layered as ``load_settings`` does."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "app-settings.json").write_text(
        json.dumps({"version": 1, "llm": llm}), "utf-8"
    )
    apply_app_settings(settings, load_app_settings(settings.data_dir))


def test_the_single_custom_slot_is_migrated_once(settings: Settings, listed):
    """An existing user with *Other OpenAI-compatible endpoint* set from the app finds it as
    endpoint number one: same address, the key copied to the endpoint's own vault entry,
    the chat slot pointed at that entry, nothing asked."""
    vault = CredentialVault(settings.vault_file, settings.vault_key_file)
    vault.set("LLM_API_KEY", "sk-old-gateway")
    _app_settings(
        settings,
        {
            "provider": "openai",
            "model": "my-model",
            "base_url": GATEWAY,
            "api_key": "{{vault:LLM_API_KEY}}",
        },
    )
    client, service = _make(settings)
    with client:
        rows = _endpoints(client)
        assert len(rows) == 1
        one = rows[0]
        assert one["label"] == "gateway.example" and one["base_url"] == GATEWAY
        assert one["models"] == ["my-model"] and one["key_source"] == "vault"
        assert one["slots"] == ["chat"]
        name = key_name(one["id"])
        assert service.app.vault.get(name) == "sk-old-gateway"
        assert service.app.vault.get("LLM_API_KEY") == "sk-old-gateway", (
            "the old entry is left alone"
        )
        # the chat slot works as before, through the endpoint's own entry now
        llm = client.get("/api/connections").json()["llm"]
        assert (
            llm["endpoint"] == one["id"]
            and llm["key_source"] == "vault"
            and llm["model"] == "my-model"
        )
        assert settings.llm.api_key == "{{vault:" + name + "}}" and settings.llm.base_url == GATEWAY
        saved = load_app_settings(settings.data_dir)
        assert saved["llm"]["api_key"] == "{{vault:" + name + "}}"
        assert "sk-old" not in json.dumps(saved)
    # a second start finds the list and adds nothing
    client2, _ = _make(settings)
    with client2:
        assert [e["id"] for e in _endpoints(client2)] == [one["id"]]


def test_migration_keeps_a_dangling_key_reference_and_takes_a_slot_without_a_key(
    settings: Settings, listed
):
    """Two edges of the first start: a chat slot whose key names a vault entry nothing
    answers (the vault was lost, or the key never set) becomes an endpoint that says
    ``missing`` just as the slot does, and a key set on the endpoint later mends both; a slot
    with no key at all becomes an endpoint with none."""
    _app_settings(
        settings,
        {
            "provider": "openai",
            "model": "my-model",
            "base_url": GATEWAY,
            "api_key": "{{vault:LLM_API_KEY}}",
        },
    )
    client, service = _make(settings)
    with client:
        (one,) = _endpoints(client)
        assert one["key_source"] == "missing" and one["slots"] == ["chat"]
        assert client.get("/api/connections").json()["llm"]["key_source"] == "missing"
        assert service.app.vault.get(key_name(one["id"])) is None
        saved = load_app_settings(settings.data_dir)
        assert saved["endpoints"][0]["api_key"] == "{{vault:LLM_API_KEY}}"
        assert saved["llm"]["api_key"] == "{{vault:LLM_API_KEY}}"
        # a key typed on the endpoint goes to its own entry and the chat slot follows
        mended = client.put(
            f"/api/connections/endpoints/{one['id']}", json={"api_key": "sk-found-again"}
        )
        assert mended.status_code == 200 and mended.json()["key_source"] == "vault"
        assert service.app.vault.get(key_name(one["id"])) == "sk-found-again"
        assert settings.llm.api_key == "{{vault:" + key_name(one["id"]) + "}}"
        assert client.get("/api/connections").json()["llm"]["key_source"] == "vault"
        # taking the key away again: the endpoint and the slot both have none
        cleared = client.put(f"/api/connections/endpoints/{one['id']}", json={"api_key": ""})
        assert cleared.status_code == 200 and cleared.json()["key_source"] == "none"
        assert service.app.vault.get(key_name(one["id"])) is None
        assert settings.llm.api_key == ""
        assert client.get("/api/connections").json()["llm"]["key_source"] == "none"

    other = Settings()
    other.data_dir = settings.data_dir.parent / "no-key"
    other.agent.workspace = settings.data_dir.parent / "no-key-ws"
    other.llm.stream = False
    other.ensure_dirs()
    _app_settings(other, {"provider": "openai", "model": "local-model", "base_url": SECOND})
    client2, service2 = _make(other)
    with client2:
        (row,) = _endpoints(client2)
        assert row["base_url"] == SECOND and row["key_source"] == "none"
        assert row["models"] == ["local-model"] and row["slots"] == ["chat"]
        assert [n for n in service2.app.vault.names() if n.startswith("ENDPOINT_")] == []


def test_app_settings_are_written_whole_and_private(settings: Settings, listed):
    """``app-settings.json`` is written whole or not at all: after a save the file parses,
    no temporary file is left next to it, and it is readable by its owner only (where the
    platform keeps modes)."""
    client, _ = _make(settings)
    with client:
        added = client.post(
            "/api/connections/endpoints",
            json={"base_url": GATEWAY, "api_key": "sk-gateway-1"},
        )
        assert added.status_code == 200
    path = settings.data_dir / "app-settings.json"
    assert json.loads(path.read_text("utf-8"))["endpoints"][0]["base_url"] == GATEWAY
    assert not path.with_suffix(".tmp").exists()
    if sys.platform != "win32":
        assert path.stat().st_mode & 0o777 == 0o600


def test_a_catalogue_slot_or_a_config_slot_is_not_migrated(settings: Settings, tmp_path: Path):
    _app_settings(
        settings,
        {
            "provider": "openai",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "{{vault:LLM_API_KEY}}",
        },
    )
    client, _ = _make(settings)
    with client:
        assert _endpoints(client) == []
        assert load_app_settings(settings.data_dir)["endpoints"] == []
    # a slot from config.toml alone (nothing set from the app) stays the file's business
    other = Settings()
    other.data_dir = tmp_path / "other"
    other.agent.workspace = tmp_path / "other-ws"
    other.llm.base_url = GATEWAY
    other.llm.api_key = "plain"
    other.llm.stream = False
    other.ensure_dirs()
    client2, _ = _make(other)
    with client2:
        assert _endpoints(client2) == []


def test_an_address_belongs_to_one_endpoint(server, listed):
    client, _ = server
    first = client.post(
        "/api/connections/endpoints", json={"base_url": GATEWAY, "label": "Gateway"}
    ).json()
    # the same address again, with or without the trailing slash, is refused and the
    # answer names the endpoint that has it; the list does not grow
    again = client.post("/api/connections/endpoints", json={"base_url": GATEWAY + "/"})
    assert again.status_code == 409 and "Gateway" in again.json()["detail"]
    assert len(_endpoints(client)) == 1
    second = client.post(
        "/api/connections/endpoints", json={"base_url": SECOND, "models": "s-1"}
    ).json()
    # moving the second onto the first's address is refused the same way; its own address
    # and a plain rename are not a move
    moved = client.put(f"/api/connections/endpoints/{second['id']}", json={"base_url": GATEWAY})
    assert moved.status_code == 409 and "Gateway" in moved.json()["detail"]
    assert client.get("/api/connections").json()["endpoints"][1]["base_url"] == SECOND
    same = client.put(f"/api/connections/endpoints/{second['id']}", json={"base_url": SECOND + "/"})
    assert same.status_code == 200
    assert (
        client.put(f"/api/connections/endpoints/{first['id']}", json={"label": "G"}).status_code
        == 200
    )
    assert client.put("/api/connections/endpoints/ep-nope", json={"label": "x"}).status_code == 404


def test_two_changes_at_once_both_land(settings: Settings, monkeypatch: pytest.MonkeyPatch):
    """A rekey (which asks the endpoint for its models, a slow network call) and a rename
    sent while it waits: the list is changed one request at a time, so neither is lost."""
    import asyncio

    _, service = _make(settings)
    gate = asyncio.Event()

    async def slow(base_url: str, key: str = "") -> tuple[list[str], str]:
        if key:
            await gate.wait()
        return (["m-1"], "")

    monkeypatch.setattr(conn_module, "fetch_models", slow)

    async def scenario() -> dict:
        eps = service.connections.endpoints
        first = await eps.add({"base_url": GATEWAY})
        rekey = asyncio.ensure_future(eps.update(first["id"], {"api_key": "sk-new"}))
        await asyncio.sleep(0)  # the rekey is now waiting on the endpoint
        rename = asyncio.ensure_future(eps.update(first["id"], {"label": "Renamed"}))
        await asyncio.sleep(0)
        assert not rename.done()  # it waits for the rekey instead of writing past it
        gate.set()
        await asyncio.gather(rekey, rename)
        return eps.get(first["id"]) or {}

    row = asyncio.run(scenario())
    assert row["label"] == "Renamed"
    assert row["api_key"] == "{{vault:" + key_name(row["id"]) + "}}"
    assert service.app.vault.get(key_name(row["id"])) == "sk-new"
