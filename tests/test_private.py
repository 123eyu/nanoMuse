"""A file kept to its owner (nanomuse/private.py)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from nanomuse.private import keep_private


@pytest.mark.skipif(sys.platform == "win32", reason="the mode is the POSIX side's test")
def test_posix_takes_the_mode(tmp_path: Path) -> None:
    # the calendars and address books carry a life; the mode says whose
    f = tmp_path / "secret.txt"
    f.write_text("x", encoding="utf-8")
    keep_private(f)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


@pytest.mark.skipif(sys.platform != "win32", reason="the ACL is the Windows side's test")
def test_windows_takes_the_acl(tmp_path: Path) -> None:
    # chmod clears a read-only flag there and nothing else; the rules handed down to a
    # file went, and the one account that made it kept it alone
    f = tmp_path / "secret.txt"
    f.write_text("x", encoding="utf-8")
    keep_private(f)
    # icacls speaks the console's code page; the first line is the rule, the rest a status
    rules = subprocess.run(["icacls", str(f)], capture_output=True).stdout.decode("mbcs")
    line = rules.splitlines()[0]
    user = os.environ.get("USERNAME", "")
    assert user and user in line
    assert "Everyone" not in line
    assert "BUILTIN" not in line
