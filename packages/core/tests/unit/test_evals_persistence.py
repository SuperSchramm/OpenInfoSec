"""Regression guard for issue #5 Phase 4: evals/persistence.py's DB_PATH.

evals/persistence.py had no dedicated test file before this. This one exists
specifically to cover the bare-call-after-monkeypatch path that the rest of
the module's CRUD behavior (exercised indirectly via api/routes/evals.py's
own tests) doesn't isolate.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from openexecutive.evals import persistence as evals_persistence


def test_initialize_eval_runs_db_bare_call_follows_monkeypatched_db_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """initialize_eval_runs_db()'s default must be resolved at call time,
    not frozen at def time, or a test-isolation monkeypatch of
    evals.persistence.DB_PATH silently fails to reach it.
    """
    patched_path = tmp_path / "patched.db"
    monkeypatch.setattr(evals_persistence, "DB_PATH", patched_path)

    evals_persistence.initialize_eval_runs_db()  # bare call, no explicit db_path

    assert patched_path.exists(), (
        "initialize_eval_runs_db() must create its schema at the current "
        "evals.persistence.DB_PATH, not a value frozen at import time"
    )
    with closing(sqlite3.connect(str(patched_path))) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "eval_runs" in tables
