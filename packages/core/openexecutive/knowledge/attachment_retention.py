"""Attachment retention sweep — periodic expiry of ATTACHMENT_COLLECTION chunks.

Issue #25 step 2: attachment-ingested content has no dedup and no per-item
purge path otherwise — a re-sent attachment just accumulates a fresh chunk
set forever (see ``knowledge/loader.py``'s ``ingest_file`` docstring). This
sweep periodically deletes chunks older than
``settings.attachment_retention_days``, closing that unbounded-growth gap
without any per-sender/per-channel identity tracking — purely keyed on the
server-set ``ingested_at`` metadata timestamp
(``ChromaDBStore.purge_expired_attachments``), never anything
attacker-influenced.

Heartbeat lifecycle mirrors ``notion_sync_scan`` / ``watchlist_research_scan``:
bootstrap on boot, run one tick, chain the next via the ``scheduled_actions``
table (see ``scheduler/runner.py``'s ``"attachment_retention_sweep"`` dispatch).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openexecutive.config import get_settings
from openexecutive.knowledge.store import ChromaDBStore
from openexecutive.memory.episodic import insert_scheduled_action

logger = logging.getLogger(__name__)

HEARTBEAT_KIND = "attachment_retention_sweep"
HEARTBEAT_CHANNEL = "__internal__"
HEARTBEAT_CHANNEL_REF = "attachment_retention"
HEARTBEAT_INTENT = "Attachment retention sweep — purge expired attachment_uploads chunks."


def _heartbeat_pending(db_path: Path | None = None) -> bool:
    from openexecutive.memory.episodic import _get_conn, _resolve_db_path

    resolved = _resolve_db_path(db_path)
    if not resolved.exists():
        return False
    with _get_conn(resolved) as conn:
        row = conn.execute(
            "SELECT 1 FROM scheduled_actions "
            "WHERE kind = ? AND status IN ('pending', 'running') LIMIT 1",
            (HEARTBEAT_KIND,),
        ).fetchone()
    return row is not None


def bootstrap_attachment_retention_sweep(db_path: Path | None = None) -> int | None:
    """Ensure exactly one pending sweep heartbeat exists. Returns its id or None."""
    if _heartbeat_pending(db_path):
        return None
    run_at = datetime.now(UTC) + timedelta(minutes=1)
    try:
        action_id = insert_scheduled_action(
            run_at=run_at.isoformat(),
            channel=HEARTBEAT_CHANNEL,
            channel_ref=HEARTBEAT_CHANNEL_REF,
            intent_text=HEARTBEAT_INTENT,
            kind=HEARTBEAT_KIND,
            db_path=db_path,
        )
        logger.info(
            "attachment_retention.bootstrap: heartbeat scheduled at %s (id=%d)",
            run_at.isoformat(), action_id,
        )
        return action_id
    except Exception:
        logger.exception("attachment_retention.bootstrap: failed to enqueue heartbeat")
        return None


def enqueue_next_attachment_retention_sweep(
    *,
    after: datetime | None = None,
    db_path: Path | None = None,
) -> int | None:
    settings = get_settings()
    base = (after or datetime.now(UTC)).astimezone(UTC)
    run_at = base + timedelta(minutes=settings.attachment_retention_sweep_interval_minutes)
    try:
        action_id = insert_scheduled_action(
            run_at=run_at.isoformat(),
            channel=HEARTBEAT_CHANNEL,
            channel_ref=HEARTBEAT_CHANNEL_REF,
            intent_text=HEARTBEAT_INTENT,
            kind=HEARTBEAT_KIND,
            db_path=db_path,
        )
        logger.info(
            "attachment_retention.enqueue_next: next sweep at %s (id=%d)",
            run_at.isoformat(), action_id,
        )
        return action_id
    except Exception:
        logger.exception("attachment_retention.enqueue_next: insert failed")
        return None


async def run_attachment_retention_sweep(
    *, store: ChromaDBStore | None = None, now: datetime | None = None
) -> dict[str, int]:
    """One sweep tick: delete attachment chunks older than
    ``settings.attachment_retention_days``. Returns ``{"purged": <count>}``.

    Async for consistency with sibling scheduler handlers (``run_notion_sync``
    et al.), and because the actual ChromaDB delete now runs via
    ``asyncio.to_thread`` (round-2 security review: a large purge is a
    synchronous local call that would otherwise block the API event loop —
    chat turns, webhooks, health checks — for as long as it takes; mirrors
    the ``asyncio.to_thread`` guidance ``ingest_file``'s docstring already
    gives API-event-loop callers).
    """
    settings = get_settings()
    stats = {"purged": 0}
    if not settings.attachment_retention_sweep_enabled:
        return stats

    if store is None:
        # Only the scheduler's cadence-fired call (runner.py) hits this path
        # — that call runs in-process under the API's lifespan, so the
        # shared singleton is available. Mirrors run_notion_sync's identical
        # fallback.
        from openexecutive.orchestrator.store_access import get_shared_store

        store = get_shared_store()

    # Normalize to UTC-aware before .timestamp() -- a naive datetime would
    # otherwise be interpreted as LOCAL time, silently shifting the cutoff
    # by the server's UTC offset (round-2 logic review). ingested_at is
    # always a UTC epoch float (time.time()), so this must match.
    effective_now = now or datetime.now(UTC)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=UTC)
    cutoff_epoch = (
        effective_now - timedelta(days=settings.attachment_retention_days)
    ).timestamp()
    stats["purged"] = await asyncio.to_thread(
        store.purge_expired_attachments, cutoff_epoch
    )
    if stats["purged"]:
        logger.info(
            "attachment_retention: purged %d expired chunk(s) older than %d day(s)",
            stats["purged"], settings.attachment_retention_days,
        )
    return stats
