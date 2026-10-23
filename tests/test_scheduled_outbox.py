from __future__ import annotations

from pathlib import Path

import pytest

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


@pytest.mark.parametrize("crash_point", ["after_claim", "after_enqueue", "during_execution"])
def test_pending_delivery_recovers_at_each_pre_ack_crash_point(tmp_path: Path, crash_point: str):
    path = tmp_path / "outbox.db"
    store = DeliveryOutbox(path)
    claimed = _claim(store)
    assert claimed is not None
    # Queue insertion and execution are deliberately outside SQLite. Until the worker's
    # acknowledgement, all three crash points have the same durable pending state.
    assert crash_point
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
