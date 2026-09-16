"""Tests for the attachment retention sweep (issue #25 step 2).

Heartbeat bootstrap/chain tests mirror test_nudge_engine.py's
TestHeartbeat pattern; the sweep-logic tests exercise
run_attachment_retention_sweep and ChromaDBStore.purge_expired_attachments
directly against a real (tmp_path) ChromaDBStore.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from openexecutive.config import Settings
from openexecutive.knowledge import attachment_retention
from openexecutive.knowledge.store import ChromaDBStore
from openexecutive.memory import episodic


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "test.db"
    monkeypatch.setattr(episodic, "DB_PATH", db)
    episodic.initialize_db(db)


def _settings(**overrides: object) -> Settings:
    base: dict[str, Any] = dict(
        attachment_retention_sweep_enabled=True,
        attachment_retention_days=90,
        attachment_retention_sweep_interval_minutes=1440,
    )
    base.update(overrides)
    return base  # type: ignore[return-value]


def _patch_settings(monkeypatch: pytest.MonkeyPatch, **overrides: object) -> None:
    s = _settings(**overrides)

    class _Stub:
        pass

    stub = _Stub()
    for k, v in s.items():
        setattr(stub, k, v)
    monkeypatch.setattr(attachment_retention, "get_settings", lambda: stub)


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------- #
# Heartbeat bootstrap / chain
# --------------------------------------------------------------------------- #

class TestHeartbeat:
    def test_bootstrap_inserts_one_pending_row(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_settings(monkeypatch)
        action_id = attachment_retention.bootstrap_attachment_retention_sweep()
        assert action_id is not None
        rows = episodic.list_scheduled_actions(status="pending")
        assert len([r for r in rows if r.kind == "attachment_retention_sweep"]) == 1

    def test_bootstrap_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _patch_settings(monkeypatch)
        first = attachment_retention.bootstrap_attachment_retention_sweep()
        second = attachment_retention.bootstrap_attachment_retention_sweep()
        assert first is not None
        assert second is None
        rows = episodic.list_scheduled_actions(status="pending")
        assert len([r for r in rows if r.kind == "attachment_retention_sweep"]) == 1

    def test_enqueue_next_uses_configured_interval(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _patch_settings(monkeypatch, attachment_retention_sweep_interval_minutes=5)
        now = _now()
        action_id = attachment_retention.enqueue_next_attachment_retention_sweep(after=now)
        assert action_id is not None
        row = episodic.get_scheduled_action(action_id)
        assert row is not None
        run_at = datetime.fromisoformat(row.run_at)
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=UTC)
        delta = run_at - now
        assert timedelta(minutes=4, seconds=30) <= delta <= timedelta(minutes=5, seconds=30)


# --------------------------------------------------------------------------- #
# run_attachment_retention_sweep — settings gating
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_sweep_no_ops_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_settings(monkeypatch, attachment_retention_sweep_enabled=False)
    store = MagicMock()

    stats = await attachment_retention.run_attachment_retention_sweep(store=store)

    assert stats == {"purged": 0}
    store.purge_expired_attachments.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_calls_purge_with_cutoff_derived_from_retention_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test proving the retention window is actually respected:
    the cutoff passed to purge_expired_attachments must be
    now - retention_days, not some other value."""
    _patch_settings(monkeypatch, attachment_retention_days=30)
    store = MagicMock()
    store.purge_expired_attachments.return_value = 3
    now = datetime(2026, 1, 30, tzinfo=UTC)

    stats = await attachment_retention.run_attachment_retention_sweep(store=store, now=now)

    assert stats == {"purged": 3}
    store.purge_expired_attachments.assert_called_once()
    (cutoff,), _kwargs = store.purge_expired_attachments.call_args
    expected_cutoff = (now - timedelta(days=30)).timestamp()
    assert cutoff == pytest.approx(expected_cutoff)


@pytest.mark.skipif(
    not hasattr(time, "tzset"), reason="time.tzset is POSIX-only (no Windows)"
)
@pytest.mark.asyncio
async def test_sweep_treats_naive_now_as_utc_not_local_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test for issue #25 step 2 logic review round 2: a naive
    datetime passed as `now` must be interpreted as UTC, matching
    ingested_at (always a UTC epoch float from time.time()) -- NOT as
    local time, which .timestamp() does by default for a naive datetime
    and would silently shift the cutoff by the server's UTC offset.

    Forces the process's local timezone to America/Chicago (UTC-6/-5) for
    this test only (a UTC-configured CI runner would otherwise make this
    test vacuous: when local time already equals UTC, a naive datetime's
    local-time interpretation and its UTC interpretation coincide, so
    removing the fix's tzinfo guard entirely would still pass). Restores
    the ORIGINAL TZ value itself rather than relying on monkeypatch's
    teardown ordering -- monkeypatch reverts the env var only after this
    test function returns, so a `finally: time.tzset()` inside the test
    body would re-apply America/Chicago (the env var is still set at that
    point), leaving the process's C-library tzset state stuck on it for
    every later test in the same session. Confirmed this the hard way:
    an earlier version of this test using that pattern broke an unrelated
    quiet-hours test elsewhere in the suite when run as part of the full
    run (passed in isolation, failed only in combination)."""
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "America/Chicago"
    time.tzset()
    try:
        _patch_settings(monkeypatch, attachment_retention_days=30)
        store = MagicMock()
        store.purge_expired_attachments.return_value = 0
        naive_now = datetime(2026, 1, 30, 12, 0, 0)  # no tzinfo
        aware_now = datetime(2026, 1, 30, 12, 0, 0, tzinfo=UTC)

        await attachment_retention.run_attachment_retention_sweep(store=store, now=naive_now)

        (cutoff,), _kwargs = store.purge_expired_attachments.call_args
        expected_cutoff = (aware_now - timedelta(days=30)).timestamp()
        assert cutoff == pytest.approx(expected_cutoff), (
            "a naive `now` must be treated as UTC, not local time"
        )
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()


# --------------------------------------------------------------------------- #
# ChromaDBStore.purge_expired_attachments — the actual deletion logic
# --------------------------------------------------------------------------- #

def test_purge_expired_attachments_deletes_only_expired_chunks(tmp_path: Path) -> None:
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    now = time.time()
    store.add_documents(
        texts=["old attachment", "new attachment", "legacy chunk with no timestamp"],
        metadatas=[
            {"filename": "old.md", "ingested_at": now - 200 * 86400},
            {"filename": "new.md", "ingested_at": now - 1 * 86400},
            {"filename": "legacy.md"},
        ],
        ids=["old1", "new1", "legacy1"],
        collection=ChromaDBStore.ATTACHMENT_COLLECTION,
    )
    store.add_documents(
        texts=["curated policy"],
        metadatas=[{"filename": "policy.md"}],
        ids=["c1"],
        collection=ChromaDBStore.COMPANY_COLLECTION,
    )

    cutoff = now - 90 * 86400
    deleted = store.purge_expired_attachments(cutoff)

    assert deleted == 1, "only the 200-day-old chunk should be purged"
    assert store.get_collection_count(ChromaDBStore.ATTACHMENT_COLLECTION) == 2, (
        "the 1-day-old chunk and the untagged legacy chunk must both survive"
    )
    assert store.get_collection_count(ChromaDBStore.COMPANY_COLLECTION) == 1, (
        "purge_expired_attachments must never touch curated company docs"
    )


def test_purge_expired_attachments_returns_zero_when_nothing_expired(
    tmp_path: Path,
) -> None:
    store = ChromaDBStore(persist_directory=tmp_path / "chroma")
    now = time.time()
    store.add_documents(
        texts=["fresh"],
        metadatas=[{"filename": "fresh.md", "ingested_at": now}],
        ids=["a1"],
        collection=ChromaDBStore.ATTACHMENT_COLLECTION,
    )

    deleted = store.purge_expired_attachments(now - 90 * 86400)

    assert deleted == 0
    assert store.get_collection_count(ChromaDBStore.ATTACHMENT_COLLECTION) == 1
