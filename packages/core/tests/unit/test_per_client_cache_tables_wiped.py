"""Issue #54: briefing_narrative and person_insights must be wiped by every
path that swaps the live company, not just some of them.

Both are DERIVED caches, but /today serves them unconditionally and only
regenerates in a background task. Leave a row behind after a swap and the
next request renders the OUTGOING company's cached text under the incoming
one -- a real cross-tenant leak, worst on the fixture-load path since a demo
is what gets screen-shared. Three separate paths swap live company state
(fixture load/unload's shared _apply_state_from_source, reset_all_state, and
clients.slots' blank-slot activation); each is covered here.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from openexecutive.cli.fixture_loader import (
    PER_CLIENT_CACHE_TABLES,
    _delete_all_rows,
    _seed_episodic_memory,
    reset_all_state,
)


@pytest.fixture(autouse=True)
def _isolate_episodic_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from openexecutive.briefing import narrative_cache
    from openexecutive.memory import episodic
    from openexecutive.people import insights_cache

    db_path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", db_path)
    monkeypatch.setattr(narrative_cache, "DB_PATH", db_path)
    monkeypatch.setattr(insights_cache, "DB_PATH", db_path)
    episodic.initialize_db(db_path)
    narrative_cache.initialize_db(db_path)
    insights_cache.initialize_db(db_path)
    return db_path


def _seed_caches(db_path: Path) -> None:
    from openexecutive.briefing.narrative_cache import BriefingNarrative
    from openexecutive.briefing.narrative_cache import put as put_narrative
    from openexecutive.people.insights_cache import PersonInsight
    from openexecutive.people.insights_cache import put as put_insight

    put_narrative(
        BriefingNarrative(
            scope="principal", input_hash="h1",
            narrative_text="Alex is closing the Acme deal.",
            generated_at="2026-01-01T00:00:00+00:00",
        ),
        db_path=db_path,
    )
    put_insight(
        PersonInsight(
            person_id=1, input_hash="h1", insight_text="On track, no blockers.",
            generated_at="2026-01-01T00:00:00+00:00",
        ),
        db_path=db_path,
    )
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM briefing_narrative").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM person_insights").fetchone()[0] == 1


def test_delete_all_rows_clears_both_cache_tables(_isolate_episodic_db: Path) -> None:
    _seed_caches(_isolate_episodic_db)
    cleared = _delete_all_rows(_isolate_episodic_db, PER_CLIENT_CACHE_TABLES)
    assert cleared == {"briefing_narrative": 1, "person_insights": 1}
    with sqlite3.connect(str(_isolate_episodic_db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM briefing_narrative").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM person_insights").fetchone()[0] == 0


def test_fixture_load_unload_path_clears_both_caches(
    tmp_path: Path, _isolate_episodic_db: Path
) -> None:
    """_seed_episodic_memory is what _apply_state_from_source (shared by
    load_fixture and unload_fixture) calls to clear/reseed episodic state --
    a cached narrative from the OUTGOING company must not survive it."""
    _seed_caches(_isolate_episodic_db)

    memory_path = tmp_path / "memory.json"
    memory_path.write_text(json.dumps({"decisions": [], "initiatives": [], "advice_given": []}))
    settings = type("S", (), {"episodic_db_path": _isolate_episodic_db})()
    _seed_episodic_memory(memory_path, settings)

    with sqlite3.connect(str(_isolate_episodic_db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM briefing_narrative").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM person_insights").fetchone()[0] == 0


def test_seed_episodic_memory_tolerates_a_db_with_no_cache_tables_yet(
    tmp_path: Path, _isolate_episodic_db: Path
) -> None:
    """A minimal/legacy DB that has never served a briefing lacks these
    tables; the existence guard must not raise on it (mirrors the alerts
    guard already in this function)."""
    with sqlite3.connect(str(_isolate_episodic_db)) as conn:
        conn.execute("DROP TABLE briefing_narrative")
        conn.execute("DROP TABLE person_insights")

    memory_path = tmp_path / "memory.json"
    memory_path.write_text(json.dumps({"decisions": [], "initiatives": [], "advice_given": []}))
    settings = type("S", (), {"episodic_db_path": _isolate_episodic_db})()
    _seed_episodic_memory(memory_path, settings)  # must not raise


def test_reset_all_state_wipe_list_includes_the_cache_tables() -> None:
    """reset_all_state is an expensive async function (ChromaDB, Honcho, file
    I/O), so assert the table names appear in its source -- the same cheap
    guard pattern used for the monitoring tables (test_fixture_loader_clears
    _watchlist.py)."""
    src = inspect.getsource(reset_all_state)
    for table in PER_CLIENT_CACHE_TABLES:
        assert f'"{table}"' in src or "*PER_CLIENT_CACHE_TABLES" in src, (
            f"reset_all_state no longer mentions {table!r} -- a factory reset "
            "will stop clearing this cache and the previous company's cached "
            "text will survive it."
        )
    assert "narrative_cache.initialize_db" in src
    assert "insights_cache.initialize_db" in src
    assert "bump_store_generation()" in src, (
        "reset_all_state no longer bumps the swap generation next to the "
        "cache wipe (issue #54 round-2 review) -- a background /today regen "
        "already in flight could write stale text into the reset company."
    )


def test_restore_db_from_file_bumps_the_swap_generation() -> None:
    """_restore_db_from_file does a real sqlite3 backup-API copy (client-slot
    switch into a slot that HAS state.db); cheap source-inspection guard
    rather than standing up two real db files for a two-line assertion."""
    from openexecutive.clients.slots import _restore_db_from_file

    assert "bump_store_generation()" in inspect.getsource(_restore_db_from_file)


def test_blank_slot_wipe_list_includes_the_cache_tables() -> None:
    """clients.slots._BLANK_WIPE_TABLES gates activating a blank/seed slot."""
    from openexecutive.clients.slots import _BLANK_WIPE_TABLES

    for table in PER_CLIENT_CACHE_TABLES:
        assert table in _BLANK_WIPE_TABLES


def test_blank_slot_activation_actually_wipes_the_caches(
    _isolate_episodic_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openexecutive.clients import slots as slots_mod

    monkeypatch.setattr(slots_mod, "_episodic_db_path", lambda: _isolate_episodic_db)
    _seed_caches(_isolate_episodic_db)

    slots_mod._wipe_per_client_tables()

    with sqlite3.connect(str(_isolate_episodic_db)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM briefing_narrative").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM person_insights").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# Explicit swap-generation bump next to each wipe (issue #54 round-2 review)
# --------------------------------------------------------------------------- #
#
# A background /today regen skips its cache write when
# orchestrator.store_access.get_store_generation() has moved past the value
# it captured at scheduling time. The bumps these three call sites already
# got via publish_swapped_store (called later, and skipped when app_state is
# None) are not enough on their own to guarantee that -- an explicit bump
# right next to each SQLite wipe removes the dependency on that later,
# conditional call.

def test_seed_episodic_memory_bumps_the_swap_generation(
    tmp_path: Path, _isolate_episodic_db: Path
) -> None:
    from openexecutive.orchestrator import store_access

    before = store_access.get_store_generation()
    memory_path = tmp_path / "memory.json"
    memory_path.write_text(json.dumps({"decisions": [], "initiatives": [], "advice_given": []}))
    settings = type("S", (), {"episodic_db_path": _isolate_episodic_db})()
    _seed_episodic_memory(memory_path, settings)
    assert store_access.get_store_generation() > before


def test_wipe_per_client_tables_bumps_the_swap_generation(
    _isolate_episodic_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from openexecutive.clients import slots as slots_mod
    from openexecutive.orchestrator import store_access

    monkeypatch.setattr(slots_mod, "_episodic_db_path", lambda: _isolate_episodic_db)
    before = store_access.get_store_generation()
    slots_mod._wipe_per_client_tables()
    assert store_access.get_store_generation() > before
