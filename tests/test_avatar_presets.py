"""The optional avatar inspirations stay complete, metadata-free and in every client."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from xml.etree import ElementTree

from PIL import Image

ROOT = Path(__file__).parents[1]
PRESETS = ("moss-courier", "tide-tinker", "dusk-moth", "copper-acorn")
MOODS = ("idle", "working", "waiting", "happy", "error")


def test_avatar_preset_sources_and_client_previews_match() -> None:
    source = ROOT / "assets" / "avatar-presets"
    manifest = json.loads((source / "source.json").read_text())
    ios_project = (
        ROOT / "android" / "src" / "ios" / "Minis.xcodeproj" / "project.pbxproj"
    ).read_text()
    assert manifest["generated"] == "2026-10-22"
    assert manifest["image_model"] == "qwen-image-3.0-pro"
    assert len(manifest["sha256"]) == len(PRESETS) * len(MOODS)
    assert [item["id"] for item in manifest["characters"]] == list(PRESETS)
    assert all(
        not {"name_en", "name_zh", "description_en", "description_zh"}.intersection(item)
        for item in manifest["characters"]
    )
    assert manifest["errors"] == []
    assert manifest["ai_generated"] is True
    assert manifest["generation_channel"]["authenticated_paid_api"] is True
    assert manifest["generation_channel"]["console_experience"] is False

    for preset in PRESETS:
        directory = source / preset
        for mood in MOODS:
            path = directory / f"{mood}.webp"
            assert (
                hashlib.sha256(path.read_bytes()).hexdigest()
                == manifest["sha256"][f"{preset}/{mood}.webp"]
            )
            with Image.open(path) as image:
                assert image.size == (512, 512)
                assert image.format == "WEBP"
                assert not {"exif", "xmp", "icc_profile"}.intersection(image.info)

        idle = (directory / "idle.webp").read_bytes()
        assert (
            ROOT / "web" / "public" / "avatar-presets" / f"{preset}-idle.webp"
        ).read_bytes() == idle
        assert (
            ROOT / "harness" / "dsh-nanomuse" / "assets" / f"preset-{preset}.webp"
        ).read_bytes() == idle
        assert (
            ROOT
            / "android"
            / "src"
            / "android"
            / "app"
            / "src"
            / "main"
            / "res"
            / "drawable-nodpi"
            / f"nm_preset_{preset.replace('-', '_')}.webp"
        ).read_bytes() == idle
        assert (
            ROOT / "android" / "src" / "ios" / "NanoMuse" / "Resources" / f"preset-{preset}.webp"
        ).read_bytes() == idle
        assert f"preset-{preset}.webp in Resources" in ios_project

    assert not list(ROOT.glob("assets/avatar-presets/**/*.mp4"))
    assert not list(ROOT.glob("web/public/avatar-presets/*.mp4"))
    assert not list(ROOT.glob("harness/dsh-nanomuse/assets/preset-*.mp4"))


def test_avatar_preset_manifest_contains_no_secret_material() -> None:
    text = (ROOT / "assets" / "avatar-presets" / "source.json").read_text()
    lowered = text.lower()
    assert re.search(r"\bsk-[a-z0-9_-]{12,}", lowered) is None
    assert "authorization" not in lowered
    assert "source_url" not in lowered
    assert "workspaceid" not in lowered
    assert "http://dashscope" not in lowered
    assert "https://dashscope" not in lowered
    assert ("mini" + "max") not in lowered


def test_avatar_preset_copy_is_localized_in_every_mobile_locale() -> None:
    android_res = ROOT / "android" / "src" / "android" / "app" / "src" / "main" / "res"
    keys = {
        f"nm_avatar_preset_{preset.replace('-', '_').split('_')[0]}_{part}"
        for preset in PRESETS
        for part in ("name", "description")
    }
    keys.add("nm_avatar_preset_a11y")
    keys.add("nm_avatar_preset_ai")
    locale_files = sorted(android_res.glob("values*/nm_strings.xml"))
    assert len(locale_files) == 17
    for path in locale_files:
        present = {node.attrib["name"] for node in ElementTree.parse(path).getroot()}
        assert keys <= present, path

    ios = json.loads((ROOT / "android" / "src" / "ios" / "Localizable.xcstrings").read_text())
    for key in (
        "Moss Courier",
        "a tiny moss-covered courier with leaf ears and an unmarked canvas satchel",
        "Tide Tinker",
        "a seafoam axolotl tinkerer with coral frills and a plain tool pouch",
        "Dusk Moth",
        "a thoughtful indigo moth with peach wing spots, a cream collar and round glasses",
        "Copper Acorn",
        "a small acorn-shaped copper automaton with sage panels and a muted-blue scarf",
        "AI-generated inspiration",
    ):
        assert len(ios["strings"][key]["localizations"]) == 9
