"""History cursors preserve ties and seek past rows that were already read."""

import sqlite3

import pytest

from nanomuse_cloud.db import Database


@pytest.fixture
def history_db():
    db = Database(":memory:")
    for account in ("one", "two"):
        db.create_account(account, "email", "fixture", 0, account_id=account)
    yield db
    db.close()


def seed(db, table, rows):
    with db.tx() as c:
        c.executemany(
            f"INSERT INTO {table}(id,account_id,ts,model,request,response) VALUES(?,?,?,?,?,?)",
            [(identifier, account, ts, "fixture", "[]", "{}") for identifier, account, ts in rows],
        )


def page(db, kind, cursor=None, limit=3, **filters):
    if kind == "backfill":
        return db.samples_page_before(cursor["ts"] if cursor else 0, cursor["rowid"] if cursor else 0, limit)
    return getattr(db, kind)(
        before=cursor["ts"] if cursor else 0,
        before_id=cursor["id"] if cursor else "",
        limit=limit,
        **filters,
    )


@pytest.mark.parametrize("kind", ["samples", "turns", "backfill"])
def test_history_pages_keep_every_row_at_a_timestamp_boundary(history_db, kind):
    db = history_db
    table = "samples" if kind == "backfill" else kind
    # Random-looking ids deliberately disagree with insertion order.
    values = [(f"row-{23 - i:02d}", "one", 100 + i // 8) for i in range(24)]
    seed(db, table, values)
    expected = list(reversed([row[0] for row in values]))
    seen = []
    cursor = None
    for _ in range(10):
        rows = page(db, kind, cursor)
        if not rows:
            break
        seen.extend(row["id"] for row in rows)
        last = rows[-1]
        rowid = db._conn.execute(f"SELECT rowid FROM {table} WHERE id=?", (last["id"],)).fetchone()[0]
        cursor = {"id": last["id"], "ts": last["ts"], "rowid": rowid}
    assert seen == expected
    assert page(db, kind, cursor) == []


@pytest.mark.parametrize("kind", ["samples", "turns"])
def test_history_cursor_keeps_account_time_and_missing_id_behavior(history_db, kind):
    db = history_db
    seed(db, kind, [("old", "one", 98), ("kept", "one", 99), ("tie", "one", 100), ("cursor", "one", 100), ("foreign", "two", 99)])
    cursor = {"id": "cursor", "ts": 100}
    assert [r["id"] for r in page(db, kind, cursor, account_id="one", since=99)] == ["tie", "kept"]
    with db.tx() as c:
        c.execute(f"DELETE FROM {kind} WHERE id='cursor'")
    # A deleted cursor still returns older seconds, but cannot order same-second rows.
    assert [r["id"] for r in page(db, kind, cursor, account_id="one", since=99)] == ["kept"]
    assert [r["id"] for r in getattr(db, kind)(account_id="one", before=100, since=99)] == ["kept"]


def test_turn_cursor_keeps_conversation_filter(history_db):
    db = history_db
    seed(db, "turns", [("first", "one", 99), ("other", "one", 100), ("cursor", "one", 101)])
    with db.tx() as c:
        c.execute("UPDATE turns SET conversation='chat-a' WHERE id IN ('first','cursor')")
    assert [r["id"] for r in page(db, "turns", {"id": "cursor", "ts": 101}, account_id="one", conversation="chat-a")] == ["first"]


@pytest.mark.parametrize("kind", ["samples", "turns", "backfill"])
def test_history_deep_page_does_not_rescan_the_newer_prefix(history_db, kind):
    db = history_db
    table = "samples" if kind == "backfill" else kind
    seed(db, table, [(f"row-{i:05d}", "one", 100000 - i // 10) for i in range(20000)])
    ordered = db._conn.execute(f"SELECT rowid,id,ts FROM {table} ORDER BY ts DESC,rowid DESC").fetchall()
    cursor = dict(ordered[18000])
    expected = [row["id"] for row in ordered[18001:18021]]
    ticks = 0

    def budget():
        nonlocal ticks
        ticks += 1
        # A generous VM-work bound, not a machine-speed deadline. A full rescan of
        # the 18,000 newer rows exceeds it; a seek plus twenty results does not.
        return int(ticks >= 20)

    db._conn.set_progress_handler(budget, 1000)
    try:
        try:
            rows = page(db, kind, cursor, limit=20)
        except sqlite3.OperationalError as exc:
            pytest.fail(f"deep page exceeded its SQLite work budget: {exc}")
    finally:
        db._conn.set_progress_handler(None, 0)
    assert [row["id"] for row in rows] == expected
