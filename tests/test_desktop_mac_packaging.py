"""Run the macOS packaging script against controlled bundles and native CLI results."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/desktop-app/package-mac.sh"
pytestmark = pytest.mark.skipif(os.name != "posix", reason="packaging uses a POSIX shell")
CLI = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["PACKAGING_TRACE"], "a", encoding="utf-8") as trace:
    trace.write(json.dumps({"name": name, "args": args}) + "\n")
stage = ""
if name == "node":
    print("1.0.5")
elif name == "file":
    print("POSIX shell script text executable" if args[-1].endswith(".sh")
          else "Mach-O 64-bit executable x86_64")
elif name == "codesign":
    if "--verify" in args:
        stage = "verify-app" if "--deep" in args else "verify-helper"
    elif "/Resources/runtime/" in args[-1]:
        stage = "sign-inner"
elif name == "hdiutil":
    if args[0] == "verify":
        stage = "verify-dmg"
    elif args[0] == "create":
        Path(args[-1]).touch()
elif name == "ditto":
    Path(args[-1]).touch()
if stage and stage == os.environ.get("PACKAGING_FAIL"):
    sys.exit(42)
"""
PackageTree = tuple[Path, dict[str, str], Path]


@pytest.fixture
def package_tree(tmp_path: Path) -> PackageTree:
    app_dir = tmp_path / "desktop"
    for folder in ("mac", "mac-arm64"):
        app = app_dir / "dist" / folder / "nanoMuse.app"
        runtime = app / "Contents/Resources/runtime/nanomuse"
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b"fixture native executable")
        runtime.chmod(0o755)
        (app / "Contents/Helpers/nanoMuse Computer Use.app").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cli = bin_dir / "fake-cli"
    cli.write_text(CLI, encoding="utf-8")
    cli.chmod(0o755)
    for name in ("node", "codesign", "file", "hdiutil", "ditto"):
        (bin_dir / name).symlink_to(cli)
    trace = tmp_path / "trace.jsonl"
    env = dict(os.environ)
    for key in (
        "APP_NAME",
        "ARTIFACT",
        "MAC_CERT_P12_BASE64",
        "MAC_CERT_PASSWORD",
        "APP_STORE_CONNECT_KEY_ID",
        "APP_STORE_CONNECT_ISSUER_ID",
        "APP_STORE_CONNECT_KEY_P8",
        "APPLE_TEAM_ID",
        "PACKAGING_FAIL",
    ):
        env.pop(key, None)
    env.update(APP_DIR=str(app_dir), PACKAGING_TRACE=str(trace), PATH=f"{bin_dir}:{env['PATH']}")
    return app_dir, env, trace


def run_package(env: dict[str, str], arch: str = "x64") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), arch], env=env, capture_output=True, text=True, timeout=10
    )


@pytest.mark.parametrize("arch,folder", [("x64", "mac"), ("arm64", "mac-arm64")])
def test_only_the_requested_architecture_is_packaged(
    package_tree: PackageTree, arch: str, folder: str
) -> None:
    app_dir, env, trace = package_tree
    result = run_package(env, arch)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    archive = next(call for call in calls if call["name"] == "ditto")
    assert archive["args"][-2] == str(app_dir / "dist" / folder / "nanoMuse.app")


@pytest.mark.parametrize("stage", ["sign-inner", "verify-helper", "verify-app", "verify-dmg"])
def test_a_failed_signature_or_image_verification_stops_packaging(
    package_tree: PackageTree, stage: str
) -> None:
    app_dir, env, trace = package_tree
    env["PACKAGING_FAIL"] = stage
    result = run_package(env)
    assert result.returncode != 0, f"{stage} was ignored:\n{result.stdout}\n{result.stderr}"
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    if stage != "verify-dmg":
        assert not any(call["name"] == "ditto" for call in calls)
        assert not list((app_dir / "dist").glob("*.zip"))


def test_executable_scripts_are_not_sent_to_codesign(package_tree: PackageTree) -> None:
    app_dir, env, trace = package_tree
    script = app_dir / "dist/mac/nanoMuse.app/Contents/Resources/runtime/launcher.sh"
    script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    script.chmod(0o755)
    result = run_package(env)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert not any(call["name"] == "codesign" and str(script) in call["args"] for call in calls)


def test_a_single_bundle_named_after_the_executable_is_renamed(package_tree: PackageTree) -> None:
    app_dir, env, trace = package_tree
    app = app_dir / "dist/mac/nanoMuse.app"
    candidate = app.with_name("nanomuse-desktop.app")
    app.rename(candidate)
    result = run_package(env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert app.is_dir() and not candidate.exists()
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    archive = next(call for call in calls if call["name"] == "ditto")
    assert archive["args"][-2] == str(app)


def test_a_missing_architecture_never_uses_the_other_bundle(package_tree: PackageTree) -> None:
    app_dir, env, trace = package_tree
    shutil.rmtree(app_dir / "dist/mac")
    result = run_package(env)
    assert result.returncode != 0
    calls = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert not any(call["name"] in ("codesign", "ditto", "hdiutil") for call in calls)
