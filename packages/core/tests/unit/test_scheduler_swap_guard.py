"""Issue #55: scheduled workflow runs (morning_brief/end_of_day_digest via
``_run_principal_brief``, and cadence-fired dynamic workflows via
``_run_dynamic_workflow``) must not complete_run/deliver using state that
belongs to a different company than the one live when the run started.

Same defence, and same test shape, as issue #54's
``test_today_route.py::test_narrative_regen_skips_the_write_if_the_company_swapped_mid_flight``:
the "workflow.run()" await point bumps the swap-generation counter mid-flight,
modeling the trigger condition for the guard (the counter is also bumped by
`delete_attachment_docs`, an admin attachment purge, not only by a full
company swap), and the handler must skip delivery/`complete_run` instead of
completing/DMing with the outgoing company's content.

The unconditional guarantee under every trigger is `delivered == []` (and no
`complete_run`). The `fail_run(run_id, "stale: ...")` bookkeeping call is
best-effort, NOT part of that guarantee: a real company swap (fixture
load/unload, reset, client-slot switch) replaces the on-disk DB file the
`run_id` row lives in, so the `UPDATE ... WHERE run_id = ?` can find zero rows
post-swap (see `runner.py`'s `swap_guard` comments and
`architecture-facts.yaml`'s `scheduler.swap_guard` for the full explanation).
The `run["status"] == "error"` assertions below hold for THIS test's
simulation (which bumps the counter without replacing the DB file, so the
row is still there to update) — they exercise the bookkeeping path, but
are not a claim that `fail_run` reliably lands after every real trigger.

`expected_generation` is captured by the CALLER (mirroring
``run_scheduler``'s claim-time snapshot — see that function's docstring for
why capturing it any later is already too late), so every test captures it
before invoking the handler, exactly as production does.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from openexecutive.audit.logger import AuditLogger, set_audit_logger
from openexecutive.memory import episodic
from openexecutive.orchestrator import store_access
from openexecutive.scheduler import runner
from openexecutive.workflows import WORKFLOW_REGISTRY
from openexecutive.workflows import base as workflow_base
from openexecutive.workflows import persistence as wf_persistence
from openexecutive.workflows.dynamic_models import DynamicWorkflowDef


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolated episodic + workflow_runs + audit DB.

    Three separate module-level `DB_PATH` bindings need patching:
    `persistence.py` and `audit/logger.py` each import `DB_PATH` from
    `memory.episodic` at their own module-load time, so patching
    `episodic.DB_PATH` alone doesn't reach them. `AuditLogger` additionally
    caches its resolved path at construction time in `self._db_path`, and
    `audit.logger`'s `_default_logger` singleton is created lazily on first
    use and never rebuilt — so without an explicit `set_audit_logger` here,
    a test that exercises `_run_principal_brief`'s (real) audit_log call
    would write into whatever real DB some earlier test in the process
    already constructed the singleton against.

    `set_audit_logger` is a bare module global, not `monkeypatch`-tracked, so
    it must be restored explicitly on teardown (`set_audit_logger(None)`,
    same convention as `test_narration_consultation_integrity.py`'s `audit`
    fixture) — otherwise this singleton leaks into every later test in the
    same pytest process that reads the default logger without setting its
    own.
    """
    path = tmp_path / "episodic.db"
    monkeypatch.setattr(episodic, "DB_PATH", path)
    monkeypatch.setattr(wf_persistence, "DB_PATH", path)
    episodic.initialize_db(path)
    wf_persistence.initialize_runs_db(path)
    set_audit_logger(AuditLogger(path))
    yield path
    set_audit_logger(None)


class _StubWorkflow:
    """Minimal Workflow stand-in whose `run()` bumps the swap generation
    mid-stream, then yields an artifact event — simulating a company swap
    (or an attachment purge) landing while the workflow's LLM call was in
    flight."""

    title = "Stub Brief"

    def __init__(self, *, swap_mid_run: bool, artifact: str = "the artifact") -> None:
        self._swap_mid_run = swap_mid_run
        self._artifact = artifact

    def input_model(self) -> type:
        class _Inputs:
            def model_dump(self) -> dict[str, Any]:
                return {}

        return _Inputs

    async def run(self, *, inputs: Any, store: Any):  # noqa: ANN001, ARG002
        if self._swap_mid_run:
            store_access.bump_store_generation()
        yield workflow_base.WorkflowEvent(type="artifact", content=self._artifact)


# --------------------------------------------------------------------------- #
# _run_principal_brief (morning_brief / end_of_day_digest)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_principal_brief_marks_run_stale_and_skips_delivery_on_swap_mid_run(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        WORKFLOW_REGISTRY, "morning_brief", _StubWorkflow(swap_mid_run=True)
    )

    # A recorder, not a raise: `_run_principal_brief`'s outer `except
    # Exception` would swallow a raised assertion and (mis)report it via
    # `fail_run`, which could accidentally satisfy a `"stale" in error`
    # check for the wrong reason. Asserting the recorder stayed empty is
    # the real, unambiguous signal that delivery was skipped.
    delivered: list[str] = []

    async def _record_deliver(artifact: str) -> tuple[bool, str]:
        delivered.append(artifact)
        return True, "telegram"

    monkeypatch.setattr(runner, "_deliver_to_principal", _record_deliver)
    monkeypatch.setattr("uuid.uuid4", lambda: "fixed-run-id")

    action_id = episodic.insert_scheduled_action(
        run_at=datetime.now(UTC).isoformat(),
        channel="__internal__",
        channel_ref="principal",
        intent_text="morning brief",
        kind="principal_brief_morning",
    )
    action = episodic.get_scheduled_action(action_id)
    assert action is not None

    # Captured before the handler runs — mirrors run_scheduler's claim-time
    # snapshot (issue #55: capturing any later can already be too late).
    expected_generation = store_access.get_store_generation()
    await runner._run_principal_brief(action, datetime.now(UTC), expected_generation)

    run = wf_persistence.get_run("fixed-run-id")
    assert run is not None
    assert run["status"] == "error"
    assert run["error"] == "stale: store generation changed mid-run"
    assert delivered == []

    # Bookkeeping always proceeds regardless of the swap (issue #58's scope,
    # not this fix's) — the recurring cadence must not stall.
    stored = episodic.get_scheduled_action(action_id)
    assert stored is not None and stored.status == "done"
    chained = episodic.list_scheduled_actions(status="pending")
    assert any(a.kind == "principal_brief_morning" for a in chained)


@pytest.mark.asyncio
async def test_principal_brief_completes_and_delivers_normally_without_a_swap(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(
        WORKFLOW_REGISTRY, "morning_brief", _StubWorkflow(swap_mid_run=False)
    )

    delivered: list[str] = []

    async def _record_deliver(artifact: str) -> tuple[bool, str]:
        delivered.append(artifact)
        return True, "telegram"

    monkeypatch.setattr(runner, "_deliver_to_principal", _record_deliver)
    monkeypatch.setattr("uuid.uuid4", lambda: "fixed-run-id-2")

    action_id = episodic.insert_scheduled_action(
        run_at=datetime.now(UTC).isoformat(),
        channel="__internal__",
        channel_ref="principal",
        intent_text="morning brief",
        kind="principal_brief_morning",
    )
    action = episodic.get_scheduled_action(action_id)
    assert action is not None

    expected_generation = store_access.get_store_generation()
    await runner._run_principal_brief(action, datetime.now(UTC), expected_generation)

    run = wf_persistence.get_run("fixed-run-id-2")
    assert run is not None and run["status"] == "done"
    assert delivered == ["the artifact"]


# --------------------------------------------------------------------------- #
# _run_dynamic_workflow (cadence-fired user-created workflows)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_dynamic_workflow_marks_run_stale_and_skips_delivery_on_swap_mid_run(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    defn = DynamicWorkflowDef(name="my_wf", title="My Workflow", cadence=None)
    monkeypatch.setattr(
        "openexecutive.workflows.dynamic_store.get_definition", lambda _name: defn
    )
    monkeypatch.setattr(
        "openexecutive.workflows.get_workflow",
        lambda _name: _StubWorkflow(swap_mid_run=True),
    )

    # A recorder, not a raise: `handle_message_person`'s call site has its
    # own local `except Exception` (a delivery failure must not regress a
    # completed run) — a raised assertion there is caught and logged, not
    # propagated, so it can't be trusted as the test's signal. The recorder
    # staying empty is the unambiguous check.
    delivered: list[dict[str, Any]] = []

    async def _record_message(kwargs: dict[str, Any]) -> None:
        delivered.append(kwargs)

    monkeypatch.setattr(
        "openexecutive.orchestrator.schedule_tools.handle_message_person", _record_message
    )
    monkeypatch.setattr("uuid.uuid4", lambda: "fixed-dyn-run-id")

    action_id = episodic.insert_scheduled_action(
        run_at=datetime.now(UTC).isoformat(),
        channel="__internal__",
        channel_ref="my_wf",
        intent_text="dynamic workflow",
        kind="dynamic_workflow",
        assigned_to_person_id=1,
    )
    action = episodic.get_scheduled_action(action_id)
    assert action is not None

    expected_generation = store_access.get_store_generation()
    await runner._run_dynamic_workflow(action, datetime.now(UTC), expected_generation)

    run = wf_persistence.get_run("fixed-dyn-run-id")
    assert run is not None
    assert run["status"] == "error"
    assert run["error"] == "stale: store generation changed mid-run"
    assert delivered == []

    stored = episodic.get_scheduled_action(action_id)
    assert stored is not None and stored.status == "done"


@pytest.mark.asyncio
async def test_dynamic_workflow_completes_and_delivers_normally_without_a_swap(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression guard for the swap-guard's negative case: a mutation that
    makes the guard always treat the run as swapped (e.g. `swapped = True`
    hardcoded) would silently stop every cadence-fired dynamic-workflow DM
    — this must fail if that happens."""
    defn = DynamicWorkflowDef(name="my_wf", title="My Workflow", cadence=None)
    monkeypatch.setattr(
        "openexecutive.workflows.dynamic_store.get_definition", lambda _name: defn
    )
    monkeypatch.setattr(
        "openexecutive.workflows.get_workflow",
        lambda _name: _StubWorkflow(swap_mid_run=False),
    )

    delivered: list[dict[str, Any]] = []

    async def _record_message(kwargs: dict[str, Any]) -> None:
        delivered.append(kwargs)

    monkeypatch.setattr(
        "openexecutive.orchestrator.schedule_tools.handle_message_person", _record_message
    )
    monkeypatch.setattr("uuid.uuid4", lambda: "fixed-dyn-run-id-2")

    action_id = episodic.insert_scheduled_action(
        run_at=datetime.now(UTC).isoformat(),
        channel="__internal__",
        channel_ref="my_wf",
        intent_text="dynamic workflow",
        kind="dynamic_workflow",
        assigned_to_person_id=1,
    )
    action = episodic.get_scheduled_action(action_id)
    assert action is not None

    expected_generation = store_access.get_store_generation()
    await runner._run_dynamic_workflow(action, datetime.now(UTC), expected_generation)

    run = wf_persistence.get_run("fixed-dyn-run-id-2")
    assert run is not None and run["status"] == "done"
    assert delivered == [{"person_id": 1, "text": "the artifact"}]


# --------------------------------------------------------------------------- #
# Claim-time capture: _execute_action threads the caller's generation
# through rather than re-reading it itself (the actual bug the round-1
# security review caught — a capture inside the handler is already too
# late relative to the real dispatch gap between claim and task start).
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_execute_action_threads_expected_generation_into_principal_brief(
    db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[int] = []

    async def _spy(
        _action: episodic.ScheduledAction, _now: datetime, expected_generation: int
    ) -> None:
        captured.append(expected_generation)

    monkeypatch.setattr(runner, "_run_principal_brief", _spy)

    action_id = episodic.insert_scheduled_action(
        run_at=datetime.now(UTC).isoformat(),
        channel="__internal__",
        channel_ref="principal",
        intent_text="morning brief",
        kind="principal_brief_morning",
    )
    action = episodic.get_scheduled_action(action_id)
    assert action is not None

    # Bump generation BEFORE dispatch, then pass a stale snapshot explicitly
    # — the value `_execute_action` receives, not whatever `_run_scheduler`
    # would compute internally, is the one that must reach the handler.
    store_access.bump_store_generation()
    sentinel = store_access.get_store_generation()
    await runner._execute_action(action, gateway=None, expected_generation=sentinel)

    assert captured == [sentinel]
