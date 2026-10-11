"""The relay's image runs as uid 10001; a data directory it cannot write stops the start with
a sentence that names the directory and the chown, not with sqlite's traceback."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from nanomuse_cloud.__main__ import check_data_dir


def test_a_writable_directory_passes_and_is_created(tmp_path: Path):
    db = tmp_path / "data" / "cloud.db"
    check_data_dir(str(db))
    assert db.parent.is_dir()
    check_data_dir(":memory:")


@pytest.mark.skipif(not hasattr(os, "getuid") or os.getuid() == 0, reason="root writes anywhere; the check is for an unprivileged user")
def test_a_directory_owned_by_someone_else_stops_the_start(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir()
    data.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(SystemExit) as exc:
            check_data_dir(str(data / "cloud.db"))
        assert str(data) in str(exc.value) and "chown -R 10001:10001" in str(exc.value)
    finally:
        data.chmod(stat.S_IRWXU)
