"""The desktop's staging step keeps PyInstaller's framework and library aliases."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "posix", reason="PyInstaller uses these links on POSIX")
def test_staging_preserves_relative_framework_and_library_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/desktop-app/build-runtime.py"
    spec = importlib.util.spec_from_file_location("desktop_runtime_staging", script)
    assert spec and spec.loader
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    dist = tmp_path / "dist"
    target = tmp_path / "runtime"

    def freeze(*_args: object, **_kwargs: object) -> None:
        built = dist / "nanomuse"
        version = built / "_internal/Python.framework/Versions/3.12"
        (version / "Resources").mkdir(parents=True)
        (built / "nanomuse").write_bytes(b"runtime executable")
        (version / "Python").write_bytes(b"framework library")
        (version / "Resources/Info.plist").write_bytes(b"framework metadata")
        framework = version.parent.parent
        (framework / "Versions/Current").symlink_to("3.12", target_is_directory=True)
        (framework / "Python").symlink_to("Versions/Current/Python")
        (framework / "Resources").symlink_to("Versions/Current/Resources", target_is_directory=True)
        (built / "_internal/Python").symlink_to("Python.framework/Python")

    # Only replace PyInstaller; the real build() copies its output into the installer resource.
    monkeypatch.setattr(runtime, "DIST", dist)
    monkeypatch.setattr(runtime, "run", freeze)
    executable = runtime.build(target)

    assert executable.read_bytes() == b"runtime executable"
    for name, link in {
        "Python": "Python.framework/Python",
        "Python.framework/Python": "Versions/Current/Python",
        "Python.framework/Resources": "Versions/Current/Resources",
        "Python.framework/Versions/Current": "3.12",
    }.items():
        staged = target / "_internal" / name
        assert staged.is_symlink(), name
        assert os.readlink(staged) == link
    assert (target / "_internal/Python").read_bytes() == b"framework library"
    assert (
        target / "_internal/Python.framework/Resources/Info.plist"
    ).read_bytes() == b"framework metadata"
