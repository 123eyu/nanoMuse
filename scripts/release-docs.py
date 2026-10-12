#!/usr/bin/env python3
"""The release-day edits to the pages that name the current version.

    scripts/release-docs.py <old> <new> <old-codename> <new-codename> <date> [--dry-run]
                            [--roadmap "<one line>" --roadmap-zh "<一句话>"]
    scripts/release-docs.py 0.1.40 0.1.41 Clear Still 2026-10-20

- ``CHANGELOG.md``: a ``## [<new>] - <date> · <Codename>`` heading goes right under
  ``## [Unreleased]`` so the Unreleased bullets fall under it; the empty ``### Cloud / Runtime /
  …`` headings for the next version are put back by hand (see the previous release commit).
- ``README.md``, ``docs/readme/README_*.md``, ``docs/index.md``, ``docs/zh/index.md``: ``<old> <OldName>`` becomes
  ``<new> <NewName>``, every ``v<old>`` tag and ``nanoMuse-<old>`` asset name moves to the new
  version. A *News* list (lines ``- `<date>` …``) is left as written: its lines are milestones,
  and a milestone's line keeps its date, its version and its words for good. A major version
  (``x.0.0``) is a milestone and gets a new line, written by hand and put first, in the ten
  READMEs and on the homepage's ``index.html``; a smaller release gets none, the download
  tables and the version line say what the current version is.
- ``docs/release-notes-template.md``: the new codename joins the list.
- ``.github/ISSUE_TEMPLATE/*.yml``: the example versions (``1.0.0 (43)``, ``desktop 1.0.0``)
  move to the new version and the ``VERSION_CODE`` that ``scripts/rebrand.py`` carries.
- ``docs/roadmap.md`` and ``docs/zh/roadmap.md``: with ``--roadmap`` and ``--roadmap-zh`` (both
  or neither) a row ``| <new> | <NewName> | <one line> |`` goes at the end of the past-releases
  table of each; without them the row is written by hand, like the ``docs/parity.md`` cells
  that said "next release".

``--dry-run`` writes nothing and prints, for every page with a News list, its first three News
lines (unchanged by this script) so the reviewer sees what a new milestone line would go above.

Run from the repository root. ``scripts/release-bump.sh`` comes before this, ``scripts/rebrand.py``
in between; CONTRIBUTING.md has the whole recipe.
"""

from __future__ import annotations

import glob
import re
import sys
from pathlib import Path

NEWS_LINE = re.compile(r"^- `(\d{4}-\d{2}-\d{2})` ")

PAGES = [
    "README.md",
    "docs/index.md",
    "docs/zh/index.md",
]


def news_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """``(start, end)`` of every run of consecutive News lines, ``end`` exclusive."""
    blocks: list[tuple[int, int]] = []
    i = 0
    while i < len(lines):
        if NEWS_LINE.match(lines[i]):
            j = i
            while j < len(lines) and NEWS_LINE.match(lines[j]):
                j += 1
            blocks.append((i, j))
            i = j
        else:
            i += 1
    return blocks


def move_version(line: str, old: str, new: str, oldname: str, newname: str) -> str:
    """Every mention of the old version in one line becomes the new one."""
    line = line.replace(f"{old} {oldname}", f"{new} {newname}")
    line = line.replace(f"v{old}", f"v{new}").replace(f"nanoMuse-{old}", f"nanoMuse-{new}")
    return re.sub(rf"(?<![\d.]){re.escape(old)}-", f"{new}-", line)


def edit_page(text: str, old: str, new: str, oldname: str, newname: str) -> tuple[str, int]:
    """The page after the release edit, and how many lines still name the old version.

    Outside the News lists every mention moves to the new version. A News list is left as
    written: every line of it is a milestone and keeps its date, its version and its words."""
    lines = text.split("\n")
    blocks = news_blocks(lines)
    in_news = {i for start, end in blocks for i in range(start, end)}
    out = [
        line if i in in_news else move_version(line, old, new, oldname, newname)
        for i, line in enumerate(lines)
    ]
    left = sum(1 for i, line in enumerate(out) if old in line and i not in in_news)
    return "\n".join(out), left


def is_major(version: str) -> bool:
    """``x.0.0``: a milestone with a News line of its own."""
    parts = version.split(".")
    return len(parts) == 3 and parts[1] == "0" and parts[2] == "0"


def edit_issue_form(text: str, old: str, new: str, code: str) -> tuple[str, int]:
    """An issue form's example versions moved to the new version, and how many lines moved.

    ``<old> (<any code>)`` becomes ``<new> (<code>)`` and ``desktop <old>`` (or its Chinese
    twin ``桌面版 <old>``) becomes ``desktop <new>``; anything else that names the old version
    is left alone."""
    out, n1 = re.subn(rf"{re.escape(old)} \(\d+\)", f"{new} ({code})", text)
    out, n2 = re.subn(rf"(desktop|桌面版) {re.escape(old)}(?![\d.])", rf"\1 {new}", out)
    return out, n1 + n2


def add_roadmap_row(text: str, version: str, codename: str, line: str) -> str:
    """The roadmap page with ``| version | codename | line |`` as the last row of its
    past-releases table (the table whose separator is ``|---|---|---|``).

    Raises ``ValueError`` when the page has no such table or already has a row for the
    version; the one line is taken as written, with ``|`` not allowed in it."""
    if "|" in line:
        raise ValueError("the roadmap line cannot contain |")
    lines = text.split("\n")
    try:
        start = next(i for i, text_line in enumerate(lines) if text_line.strip() == "|---|---|---|")
    except StopIteration:
        raise ValueError("no past-releases table (|---|---|---|)") from None
    end = start + 1
    while end < len(lines) and lines[end].startswith("|"):
        end += 1
    if any(text_line.startswith(f"| {version} |") for text_line in lines[start:end]):
        raise ValueError(f"a row for {version} is already there")
    lines.insert(end, f"| {version} | {codename} | {line.strip()} |")
    return "\n".join(lines)


def version_code() -> str:
    """The Android ``VERSION_CODE`` as ``scripts/rebrand.py`` carries it after the bump."""
    m = re.search(r"^VERSION_CODE = (\d+)$", Path("scripts/rebrand.py").read_text("utf-8"), re.M)
    if not m:
        raise SystemExit("scripts/rebrand.py: no VERSION_CODE line")
    return m.group(1)


def usage() -> str:
    """The two usage lines of the module docstring."""
    return "\n".join(line[4:] for line in __doc__.strip().splitlines()[2:4])


def main(argv: list[str]) -> int:
    if argv in (["-h"], ["--help"]):
        print(__doc__.strip())
        return 0
    dry_run = "--dry-run" in argv
    argv = [a for a in argv if a != "--dry-run"]
    roadmap: dict[str, str] = {}
    for flag, page in (("--roadmap", "docs/roadmap.md"), ("--roadmap-zh", "docs/zh/roadmap.md")):
        if flag in argv:
            at = argv.index(flag)
            if at + 1 >= len(argv):
                print(f"{flag} needs the one line of the row", file=sys.stderr)
                return 2
            roadmap[page] = argv[at + 1]
            del argv[at : at + 2]
    if len(roadmap) == 1:
        print("--roadmap and --roadmap-zh go together (one row per language)", file=sys.stderr)
        return 2
    unknown = [a for a in argv if a.startswith("-")]
    if unknown:
        print(f"unknown option {' '.join(unknown)} (try --help)", file=sys.stderr)
        return 2
    if len(argv) != 5:
        print(usage(), file=sys.stderr)
        return 2
    old, new, oldname, newname, date = argv
    if not Path("pyproject.toml").is_file():
        print("run from the repository root", file=sys.stderr)
        return 2
    # The roadmap rows are worked out before anything is written, so a page without the table
    # or with the row already there refuses the run while every file is still as it was.
    roadmap_texts: dict[str, str] = {}
    for name, line in roadmap.items():
        try:
            roadmap_texts[name] = add_roadmap_row(
                Path(name).read_text(encoding="utf-8"), new, newname, line
            )
        except ValueError as exc:
            print(f"{name}: {exc}", file=sys.stderr)
            return 1

    changelog = Path("CHANGELOG.md")
    s = changelog.read_text(encoding="utf-8")
    marker = "## [Unreleased]\n\n"
    if marker not in s or f"## [{new}]" in s:
        print(
            "CHANGELOG.md: no empty Unreleased marker, or the version is already there",
            file=sys.stderr,
        )
        return 1
    if not dry_run:
        changelog.write_text(
            s.replace(marker, f"{marker}## [{new}] - {date} · {newname}\n\n", 1), encoding="utf-8"
        )

    pages = [*PAGES, *sorted(glob.glob("docs/readme/README_*.md"))]
    for name in pages:
        p = Path(name)
        text, left = edit_page(p.read_text(encoding="utf-8"), old, new, oldname, newname)
        if dry_run:
            lines = text.split("\n")
            for start, end in news_blocks(lines):
                print(f"{name}: News, left as written")
                for line in lines[start : min(end, start + 3)]:
                    print(f"  {line}")
        else:
            p.write_text(text, encoding="utf-8")
        print(f"{name}: {left} line(s) still naming {old} outside the News list")

    code = version_code()
    for name in sorted(glob.glob(".github/ISSUE_TEMPLATE/*.yml")):
        p = Path(name)
        text, moved = edit_issue_form(p.read_text(encoding="utf-8"), old, new, code)
        if moved and not dry_run:
            p.write_text(text, encoding="utf-8")
        if moved:
            print(f"{name}: {moved} example version(s) now {new} ({code})")

    for name, text in roadmap_texts.items():
        if not dry_run:
            Path(name).write_text(text, encoding="utf-8")
        print(f"{name}: a row for {new} {newname} at the end of the past-releases table")

    template = Path("docs/release-notes-template.md")
    s = template.read_text(encoding="utf-8")
    if f", {oldname} (CHANGELOG" not in s:
        print(
            "docs/release-notes-template.md: the codename list does not end with the previous name",
            file=sys.stderr,
        )
        return 1
    if not dry_run:
        template.write_text(
            s.replace(f", {oldname} (CHANGELOG", f", {oldname}, {newname} (CHANGELOG"),
            encoding="utf-8",
        )
    news = (
        f"a News line for {new} {newname} (`{date}`, first in the list, the same words in the "
        "ten READMEs and on the homepage; the earlier versions' lines stay), "
        if is_major(new)
        else "no News line (the News lists are milestones; this is not a major version), "
    )
    roadmap_note = (
        ""
        if roadmap
        else "docs/roadmap.md's and docs/zh/roadmap.md's past-releases rows (or run again with "
        "--roadmap and --roadmap-zh), "
    )
    print(
        ("dry run, nothing written. " if dry_run else "")
        + "by hand now: "
        + news
        + "the homepage's version line and download links, "
        + roadmap_note
        + "docs/parity.md, the empty Unreleased headings in CHANGELOG.md"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
