"""The mirror box's two scripts, run against a temporary directory: the traffic tally keeps
every line across a roll of the log, and the release mirror never shows half a package."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import pytest

MIRROR = Path(__file__).resolve().parents[2] / "mirror"


def load(name: str, **env: str) -> ModuleType:
    """Import a mirror script by path (``release-sync.py`` is not an importable name) with
    its environment set first, since both read theirs at import time."""
    for k, v in env.items():
        os.environ[k] = v
    spec = importlib.util.spec_from_file_location(f"mirror_{name}", MIRROR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def traffic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    logs = tmp_path / "caddy"
    logs.mkdir()
    monkeypatch.setenv("TRAFFIC_LOG_DIR", str(logs))
    monkeypatch.setenv("TRAFFIC_LOG_NAME", "site.log")
    monkeypatch.setenv("TRAFFIC_DB", str(tmp_path / "traffic.db"))
    mod = load("traffic")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript(mod.SCHEMA)
    return mod, logs, conn


def lines(mod: ModuleType, conn: sqlite3.Connection) -> list[bytes]:
    return list(mod.read_new_lines(conn))


def roll(logs: Path, name: str, when: float) -> Path:
    """What Caddy does: the current file is renamed, a new empty one takes its place."""
    rolled = logs / name
    (logs / "site.log").rename(rolled)
    os.utime(rolled, (when, when))
    (logs / "site.log").write_bytes(b"")
    return rolled


def test_the_lines_of_a_rolled_log_are_read_once_and_in_order(traffic):
    mod, logs, conn = traffic
    now = 1_700_000_000.0
    current = logs / "site.log"
    current.write_bytes(b"a1\na2\n")
    assert lines(mod, conn) == [b"a1\n", b"a2\n"]
    # more is written, then the file rolls: the rest of the old file is read from the copy
    current.write_bytes(b"a1\na2\na3\n")
    roll(logs, "site-2024-01-01T00-00-00.000.log", now - 300)
    current.write_bytes(b"b1\n")
    assert lines(mod, conn) == [b"a3\n", b"b1\n"]
    # two rolls between two runs: the middle file used to be lost
    current.write_bytes(b"b1\nb2\n")
    roll(logs, "site-2024-01-01T01-00-00.000.log", now - 200)
    current.write_bytes(b"c1\nc2\n")
    roll(logs, "site-2024-01-01T02-00-00.000.log", now - 100)
    current.write_bytes(b"d1")  # not finished yet: next run
    assert lines(mod, conn) == [b"b2\n", b"c1\n", b"c2\n"]
    current.write_bytes(b"d1\n")
    assert lines(mod, conn) == [b"d1\n"]
    assert lines(mod, conn) == []


def test_a_rolled_file_that_is_gone_loses_only_itself(traffic):
    mod, logs, conn = traffic
    now = 1_700_000_000.0
    current = logs / "site.log"
    current.write_bytes(b"a1\n")
    assert lines(mod, conn) == [b"a1\n"]
    mod.set_state(conn, "last_run", str(int(now - 1000)))
    # the file that was current then has been rolled and then removed (past roll_keep);
    # one older rolled file is from before the last run and was tallied by it already
    older = logs / "site-2023-12-31T00-00-00.000.log"
    older.write_bytes(b"old\n")
    os.utime(older, (now - 5000, now - 5000))
    current.write_bytes(b"a1\na2\n")
    gone = roll(logs, "site-2024-01-01T00-00-00.000.log", now - 500)
    held = gone.open("rb")  # keeps the inode taken, so no new file here gets the saved number
    gone.unlink()
    current.write_bytes(b"b1\n")
    roll(logs, "site-2024-01-01T01-00-00.000.log", now - 100)
    gz = logs / "site-2024-01-01T01-30-00.000.log.gz"
    gz.write_bytes(b"not read\n")
    os.utime(gz, (now - 50, now - 50))
    current.write_bytes(b"c1\n")
    assert lines(mod, conn) == [b"b1\n", b"c1\n"]
    held.close()


def test_a_truncated_log_is_read_from_the_top(traffic):
    mod, logs, conn = traffic
    current = logs / "site.log"
    current.write_bytes(b"a1\na2\na3\n")
    assert len(lines(mod, conn)) == 3
    current.write_bytes(b"z\n")
    assert lines(mod, conn) == [b"z\n"]


class Body(io.BytesIO):
    """What urlopen hands back: a readable that is also a context manager."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def test_a_download_is_a_dot_file_until_it_is_whole(tmp_path: Path, monkeypatch):
    rs = load("release-sync")
    seen: list[str] = []

    def fake_urlopen(req, timeout=0):
        seen.append(sorted(p.name for p in tmp_path.iterdir()).__repr__())
        return Body(b"PACKAGE")

    monkeypatch.setattr(rs.urllib.request, "urlopen", fake_urlopen)
    dst = tmp_path / "nanoMuse-1.0.apk"
    assert rs.part_of(dst).name == ".nanoMuse-1.0.apk.part"
    rs.download("https://example.invalid/a.apk", dst, len(b"PACKAGE"))
    assert dst.read_bytes() == b"PACKAGE"
    assert [p.name for p in tmp_path.iterdir()] == ["nanoMuse-1.0.apk"]
    # a size that does not match leaves nothing behind, visible or hidden
    with pytest.raises(OSError):
        rs.download("https://example.invalid/b.apk", tmp_path / "b.apk", 3)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["nanoMuse-1.0.apk"]
    # nor does a connection that breaks
    monkeypatch.setattr(
        rs.urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError())
    )
    with pytest.raises(OSError):
        rs.download("https://example.invalid/c.apk", tmp_path / "c.apk", 0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["nanoMuse-1.0.apk"]


def test_the_releases_list_must_be_a_list(monkeypatch):
    rs = load("release-sync")
    monkeypatch.setattr(
        rs.urllib.request,
        "urlopen",
        lambda *a, **k: Body(b'{"message": "API rate limit exceeded"}'),
    )
    with pytest.raises(ValueError):
        rs.releases()
    monkeypatch.setattr(
        rs.urllib.request,
        "urlopen",
        lambda *a, **k: Body(
            b'[{"tag_name": "v2", "published_at": "2024-02-01T00:00:00Z"}, "odd",'
            b' {"tag_name": "v1", "published_at": "2024-01-01T00:00:00Z", "draft": true}]'
        ),
    )
    assert [r["tag_name"] for r in rs.releases()] == ["v2"]


LATEST_YML = """version: {version}
files:
  - url: {name}
    sha512: {sha512}
    size: {size}
path: {name}
sha512: {sha512}
releaseDate: '2026-10-14T00:00:00.000Z'
"""


def windows_set(folder: Path, version: str, installer: bytes, blockmap: bool = True) -> str:
    """What the Windows build attaches to a release, written into a tag's folder: the
    installer, its .blockmap and latest.yml with the installer's SHA-512. Returns the name."""
    name = f"nanoMuse-Desktop-{version}-win-x64.exe"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(installer)
    if blockmap:
        (folder / f"{name}.blockmap").write_bytes(b"blocks")
    sha512 = base64.b64encode(hashlib.sha512(installer).digest()).decode()
    yml = LATEST_YML.format(version=version, name=name, sha512=sha512, size=len(installer))
    (folder / "latest.yml").write_text(yml)
    return name


def test_the_update_metadata_is_read_in_both_shapes():
    rs = load("release-sync")
    text = LATEST_YML.format(version="1.0.5", name="a%20b.exe", sha512="c2hh", size=3)
    assert rs.update_metadata(text) == ("a b.exe", "c2hh")
    assert rs.update_metadata("path: only.exe\nsha512: 'YWJj'\n") == ("only.exe", "YWJj")
    assert rs.update_metadata("version: 1.0.5\nfiles: []\n") is None
    assert rs.update_metadata("<html>not found</html>") is None


def test_the_windows_feed_moves_only_to_a_complete_set(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("WWW_ROOT", str(tmp_path))
    rs = load("release-sync")
    root = tmp_path / "dl"
    tags: list[str] = []
    monkeypatch.setattr(
        rs, "releases", lambda: [{"tag_name": t, "published_at": f"2026-{t[1:]}"} for t in tags]
    )
    monkeypatch.setattr(rs, "sync_release", lambda rel, root: [])

    def index() -> dict:
        return json.loads((root / "index.json").read_text())

    # one release with the whole set: the feed is it
    tags[:] = ["v1"]
    windows_set(root / "v1", "1.0.1", b"ONE")
    assert rs.main() == 0
    assert os.readlink(root / "latest") == "v1" and os.readlink(root / "win") == "v1"
    assert index()["win"] == "v1"
    # a newer release whose Windows build has not finished (no blockmap yet): latest moves,
    # the feed stays on the release whose set is whole
    tags[:] = ["v2", "v1"]
    windows_set(root / "v2", "1.0.2", b"TWO", blockmap=False)
    assert rs.main() == 0
    assert os.readlink(root / "latest") == "v2" and os.readlink(root / "win") == "v1"
    assert index()["win"] == "v1"
    # the blockmap arrives: the feed moves
    (root / "v2" / "nanoMuse-Desktop-1.0.2-win-x64.exe.blockmap").write_bytes(b"blocks")
    assert rs.main() == 0
    assert os.readlink(root / "win") == "v2" and index()["win"] == "v2"
    # a release whose installer is not the one latest.yml promises never becomes the feed,
    # and the journal says so; v1 is gone (older than the two kept) and the feed is still v2
    tags[:] = ["v3", "v2"]
    name = windows_set(root / "v3", "1.0.3", b"THREE")
    (root / "v3" / name).write_bytes(b"THREE?")
    capsys.readouterr()
    assert rs.main() == 0
    assert os.readlink(root / "win") == "v2" and not (root / "v1").exists()
    assert "v3/nanoMuse-Desktop-1.0.3-win-x64.exe: not the SHA-512" in capsys.readouterr().err
    # nothing kept carries a set any more: the link goes rather than dangling
    tags[:] = ["v4", "v3"]
    (root / "v4").mkdir()
    assert rs.main() == 0
    assert not (root / "win").is_symlink() and index()["win"] is None
    assert os.readlink(root / "latest") == "v4"
