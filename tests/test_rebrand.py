"""Rebranding keeps Soul's default-name round-trip test valid after an upstream pull."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "rebrand.py"


@pytest.fixture()
def rebrand() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rebrand", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("include_test", [True, False])
def test_soul_default_round_trip_survives_rebranding(
    rebrand: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_test: bool,
) -> None:
    app = tmp_path / "android" / "src" / "android" / "app"
    java = app / "src" / "main" / "java" / "com" / "openminis" / "app"
    monkeypatch.setattr(rebrand, "ROOT", tmp_path)
    monkeypatch.setattr(rebrand, "APP", app)
    monkeypatch.setattr(rebrand, "JAVA", java)
    monkeypatch.setattr(rebrand, "RES", app / "src" / "main" / "res")
    soul = java / "agent" / "SoulStore.kt"
    soul.parent.mkdir(parents=True)
    soul.write_text('name = "Minis"\nconst val DISPLAY_EMOJI = "✨"\n', encoding="utf-8")
    edge = app / "src" / "test" / "java" / "com" / "openminis" / "app" / "agent"
    edge /= "SoulIconConfigEdgeTest.kt"
    if include_test:
        edge.parent.mkdir(parents=True)
        edge.write_text(
            '    assertEquals("Minis", back.metadata.name)\n'
            '    assertEquals("Minis", explicitlyNamed.metadata.name)\n',
            encoding="utf-8",
        )

    rebrand.product_name()
    rebrand.soul_defaults()

    assert 'name = "nanoMuse"' in soul.read_text(encoding="utf-8")
    assert 'DISPLAY_EMOJI = "🐾"' in soul.read_text(encoding="utf-8")
    if include_test:
        text = edge.read_text(encoding="utf-8")
        assert "assertEquals(SoulMetadata.DEFAULT.name, back.metadata.name)" in text
        assert "// nanoMuse:" in text
        # A stored name supplied by the test is independent of the product's default.
        assert 'assertEquals("Minis", explicitlyNamed.metadata.name)' in text

    before = {p: p.read_bytes() for p in tmp_path.rglob("*.kt")}
    rebrand.product_name()
    rebrand.soul_defaults()
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.kt")} == before
