#!/usr/bin/env python3
"""Mirrors the newest nanoMuse releases so a download in China does not have to reach GitHub.

Asks the GitHub API for the repository's releases, takes the newest KEEP that are neither
drafts nor pre-releases, and fetches every asset of each into WWW_ROOT/dl/<tag>/<name> —
Caddy serves that directory at https://nanomuse.cn/dl/ (mirror/nanomuse.cn.caddy). A file
is downloaded to a dot-file next to it (`.<name>.part`, which the site block's `hide .*`
keeps out of the listing) and renamed once whole, so a visitor never gets, or sees, half a
package; one already present with the right size is left alone. Each download is checked
against the SHA-256 GitHub records for the asset (the `digest` field), or against the
`<name>.sha256` file the release ships, or — when there is neither — recorded as unverified
in the log. `dl/latest` is a symlink to the newest tag; `dl/index.json` lists what is here.
Tags no longer among the KEEP newest are removed.

The systemd timer runs it every fifteen minutes (four unauthenticated API calls an hour, of
the sixty allowed); a run with nothing new makes one request and prints nothing. Idempotent.

    WWW_ROOT       where sites live (the showcase's www/ by default; /srv/www in the container)
    MIRROR_RELEASES_REPO   owner/name to mirror (nano-muse/nanoMuse)
    MIRROR_RELEASES_KEEP   how many releases to keep (2)
    GITHUB_TOKEN   optional; lifts the API limit, never needed at this cadence
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = os.environ.get("MIRROR_RELEASES_REPO", "nano-muse/nanoMuse")
KEEP = max(1, int(os.environ.get("MIRROR_RELEASES_KEEP", "2")))
API = f"https://api.github.com/repos/{REPO}/releases?per_page=10"
UA = "nanomuse-release-mirror (+https://nanomuse.cn)"
CHUNK = 1 << 20


def www_root() -> Path:
    env = os.environ.get("WWW_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "www"


def get(url: str, accept: str = "application/octet-stream") -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        req.add_header("Authorization", f"Bearer {token}")
    return req


def releases() -> list[dict]:
    with urllib.request.urlopen(get(API, "application/vnd.github+json"), timeout=30) as r:
        data = json.load(r)
    if not isinstance(data, list):
        raise ValueError(f"the releases API answered with {type(data).__name__}, not a list")
    good = [
        x
        for x in data
        if isinstance(x, dict)
        and not x.get("draft")
        and not x.get("prerelease")
        and x.get("tag_name")
    ]
    good.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return good[:KEEP]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def part_of(dst: Path) -> Path:
    """Where ``dst`` is written while it downloads: a dot-file beside it, so the listing and
    the file server (``hide .*``) never show a package that is not whole yet."""
    return dst.with_name(f".{dst.name}.part")


def download(url: str, dst: Path, size: int) -> None:
    part = part_of(dst)
    try:
        with urllib.request.urlopen(get(url), timeout=60) as r, part.open("wb") as f:
            shutil.copyfileobj(r, f, CHUNK)
        got = part.stat().st_size
        if size and got != size:
            raise OSError(f"{dst.name}: got {got} bytes, expected {size}")
    except BaseException:
        # a broken download (the network, a size that does not match, a stop) leaves no
        # half file behind; the next run fetches the asset again from the start
        part.unlink(missing_ok=True)
        raise
    part.replace(dst)


def expected_sha(asset: dict, assets_by_name: dict[str, dict], folder: Path) -> str | None:
    """The SHA-256 the release promises for an asset: GitHub's own digest, else its .sha256 file."""
    digest = str(asset.get("digest") or "")
    if digest.startswith("sha256:"):
        return digest[7:].lower()
    side = assets_by_name.get(asset["name"] + ".sha256")
    if side:
        local = folder / side["name"]
        if not local.exists():
            download(side["browser_download_url"], local, int(side.get("size") or 0))
        text = local.read_text(errors="replace").strip()
        if text:
            return text.split()[0].lower()
    return None


def sync_release(rel: dict, root: Path) -> list[dict]:
    tag = rel["tag_name"]
    folder = root / tag
    folder.mkdir(parents=True, exist_ok=True)
    # no other run is on this folder (the lock), so a .part here is a run that was killed
    # before it could clean up, or one left by an older version of this script that wrote
    # `<name>.part` in the open: gone before the listing is served again
    for stale in folder.glob("*.part"):
        stale.unlink(missing_ok=True)
    for stale in folder.glob(".*.part"):
        stale.unlink(missing_ok=True)
    assets = [a for a in rel.get("assets", []) if a.get("name") and a.get("browser_download_url")]
    by_name = {a["name"]: a for a in assets}
    listing: list[dict] = []
    for a in assets:
        dst = folder / a["name"]
        size = int(a.get("size") or 0)
        if dst.exists() and (not size or dst.stat().st_size == size):
            listing.append({"name": a["name"], "size": size})
            continue
        try:
            download(a["browser_download_url"], dst, size)
            want = None if a["name"].endswith(".sha256") else expected_sha(a, by_name, folder)
            if want:
                have = sha256_of(dst)
                if have != want:
                    dst.unlink(missing_ok=True)
                    print(f"{tag}/{a['name']}: SHA-256 mismatch, removed", file=sys.stderr)
                    continue
                print(f"{tag}/{a['name']}: {size} bytes, sha256 ok")
            else:
                print(f"{tag}/{a['name']}: {size} bytes, unverified (no digest in the release)")
            listing.append({"name": a["name"], "size": size})
        except (urllib.error.URLError, OSError) as exc:
            print(f"{tag}/{a['name']}: {exc}", file=sys.stderr)
    return listing


def main() -> int:
    root = www_root() / "dl"
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / ".lock").open("w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return 0  # another run is on it
    try:
        wanted = releases()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"releases: {exc}", file=sys.stderr)
        return 1
    if not wanted:
        return 0
    index = []
    for rel in wanted:
        listing = sync_release(rel, root)
        index.append(
            {
                "tag": rel["tag_name"],
                "name": rel.get("name") or rel["tag_name"],
                "published_at": rel.get("published_at"),
                "assets": listing,
            }
        )
    keep = {r["tag_name"] for r in wanted}
    for child in root.iterdir():
        if (
            child.is_dir()
            and not child.is_symlink()
            and child.name not in keep
            and child.name.startswith("v")
        ):
            shutil.rmtree(child, ignore_errors=True)
            print(f"{child.name}: removed (older than the {KEEP} kept)")
    newest = wanted[0]["tag_name"]
    latest = root / "latest"
    if not latest.is_symlink() or os.readlink(latest) != newest:
        tmp = root / ".latest.tmp"
        if tmp.is_symlink() or tmp.exists():
            tmp.unlink()
        tmp.symlink_to(newest)
        tmp.replace(latest)
        print(f"latest -> {newest}")
    # written as a dot-file and renamed into place, so the listing never shows the half-written one
    (root / ".index.json.tmp").write_text(json.dumps({"repo": REPO, "releases": index}, indent=1))
    (root / ".index.json.tmp").replace(root / "index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
