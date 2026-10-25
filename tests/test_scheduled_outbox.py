from __future__ import annotations

from pathlib import Path

from nanomuse.server.outbox import DeliveryOutbox


def _claim(store: DeliveryOutbox):
    return store.claim(
        "reminder:r_1:2026-10-23T09:00:00+00:00",
        kind="reminder",
        source_id="r_1",
        occurrence="2026-10-23T09:00:00+00:00",
        thread="main",
        text="run it",
        label="Reminder: run it",
    )


def test_pending_delivery_recovers_from_a_crash_before_the_ack(tmp_path: Path):
    """Queue insertion and execution are deliberately outside SQLite: a crash after the
    claim, after the enqueue or during the run leaves the same durable pending row, and
    the next start replays it once."""
    path = tmp_path / "outbox.db"
    store = DeliveryOutbox(path)
    claimed = _claim(store)
    assert claimed is not None
    store.close()

    recovered = DeliveryOutbox(path)
    assert recovered.pending() == [claimed]
    assert _claim(recovered) is None, "the occurrence idempotency key is stable"
    recovered.close()


def test_completed_delivery_is_not_replayed_and_source_ack_can_resume(tmp_path: Path):
    path = tmp_path / "outbox.db"
    store = DeliveryOutbox(path)
    claimed = _claim(store)
    assert claimed is not None
    assert store.complete(claimed.id) == claimed
    store.close()

    recovered = DeliveryOutbox(path)
    assert recovered.pending() == []
    assert recovered.unfinalized() == [claimed]
    recovered.finalized(claimed.id)
    assert recovered.unfinalized() == []
    recovered.close()
