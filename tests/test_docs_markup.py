"""Every page under docs/ renders without a literal emphasis marker.

CommonMark's flanking rules: a ``**…**`` or ``*…*`` run that closes right after a punctuation
mark (a full-width colon, a closing bracket) and is followed by a letter has no closer, so the
asterisks show on the site; the same when a run opens on punctuation after a letter. Chinese
prose runs into this where English, with its spaces, does not. markdown-it-py follows the
same rules as the markdown-it VitePress renders with, so a hit here is a hit on the site.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from markdown_it import MarkdownIt

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SKIP = {"releases", "archive", "tasks", "traces", "briefs"}
CJK = "\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"
# A doubled asterisk anywhere, or a single one touching a Chinese character or punctuation.
STRAY = re.compile(rf"\*\*|(?<=[{CJK}])\*|\*(?=[\u4e00-\u9fff])")


def pages() -> list[Path]:
    under_docs = (p for p in DOCS.rglob("*.md") if not set(p.relative_to(DOCS).parts[:-1]) & SKIP)
    return sorted([ROOT / "README.md", *under_docs])


def rendered_text(source: str) -> str:
    """The page's text as the site shows it: HTML rendered, code left out, tags stripped."""
    source = re.sub(r"\A---\n[\s\S]*?\n---\n", "", source)
    html = MarkdownIt("commonmark").enable("table").enable("strikethrough").render(source)
    html = re.sub(r"<pre[\s\S]*?</pre>|<code[\s\S]*?</code>", "", html)
    return re.sub(r"<[^>]+>", "", html)


@pytest.mark.parametrize("page", pages(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_literal_emphasis_marker(page: Path) -> None:
    text = rendered_text(page.read_text(encoding="utf-8"))
    hits = [text[max(0, m.start() - 20) : m.end() + 20] for m in STRAY.finditer(text)]
    assert not hits, hits
