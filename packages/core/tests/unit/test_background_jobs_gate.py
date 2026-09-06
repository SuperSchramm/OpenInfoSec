"""Proves BACKGROUND_JOBS_ENABLED actually gates scheduler_task and
email_poller_task -- the two independent-timer background tasks started in
api/main.py's lifespan() that make real, billed API calls unattended --
rather than just existing as an unused Settings field. resumer_task is
deliberately NOT gated by this flag (it makes zero LLM/API calls of its
own; see config.py's comment on background_jobs_enabled) and is asserted
to always start, flag or no flag.

Drives the REAL lifespan() via create_app() + `with TestClient(app) as
client:` (the same pattern test_knowledge_external_routes.py already uses
to exercise lifespan), with the scheduler/resumer/poller loop bodies
swapped for a tiny recording stub -- not the gating logic itself, which
runs unmodified. Each stub sets a `threading.Event()` on its very first
line before returning, so "did this task's body actually start executing"
is observed directly rather than asserted from a mock call count.
`threading.Event` (not asyncio.Event) because Starlette's TestClient runs
the app's event loop on a separate OS thread from the test.

MCPGateway.start() is patched to a no-op -- it otherwise spawns a real
`uvx` subprocess and does a live MCP handshake, which has no place in a
fast unit test and is orthogonal to what this file verifies.

The final test is a log-grep check against the REAL (unstubbed)
run_scheduler/run_resumer -- the most convincing evidence of the bunch,
since it exercises production code and the exact "scheduler started" /
"resumer started" INFO lines an operator watching `make dev` output would
see, rather than a test double. Because that means the real claim_due_actions
path is reachable, it takes extra care an adversarial review round flagged
as missing from an earlier draft: it forces `_company_profile_active()` to
False (so no real scheduled_actions row can ever be claimed/dispatched
regardless of what's sitting in any DB this process happens to touch) and
points MCP_SERVERS_CONFIG_PATH at a path that can't exist (Settings'
_resolve_mcp() auto-flips mcp_enabled=True whenever that file exists on
disk, which merely unsetting MCP_ENABLED does not defeat). It attaches its
own logging.Handler directly to the `openexecutive` logger rather than
using pytest's `caplog` (which listens at the root logger) because
api/main.py's `_configure_logging()` sets `propagate=False` on the
`openexecutive` logger specifically so uvicorn's dictConfig can't clobber
it -- the same reason the narration-integrity smoke checks earlier in this
project's history attach their own handler instead of relying on caplog.
"""
from __future__ import annotations

import logging
import threading
import time
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from openexecutive.api.main import create_app
from openexecutive.config import Settings

# "Must NOT fire within this window" checks use a shorter budget than
# "must fire within this window" checks -- a negative assertion only needs
# long enough to catch a real false positive, while a positive assertion
# needs enough slack for asyncio to actually schedule and run the task
# after lifespan's own (nontrivial) DB/ChromaDB startup work.
_SHOULD_NOT_FIRE_TIMEOUT = 1.0
_SHOULD_FIRE_TIMEOUT = 2.0


def test_background_jobs_enabled_defaults_to_false() -> None:
    """Hermetic check of the Field default itself, independent of any real
    or test-machine .env file that might set BACKGROUND_JOBS_ENABLED=true
    for local scheduler use -- see the behavioral tests below for why they
    use explicit setenv("...", "false") rather than relying on this.
    """
    assert Settings.model_fields["background_jobs_enabled"].default is False


def _recording_coro(started: threading.Event):
    async def _coro(*_args: Any, **_kwargs: Any) -> None:
        started.set()

    return _coro


async def _noop_gateway_start(*_args: Any, **_kwargs: Any) -> None:
    return None


@pytest.fixture()
def _no_real_mcp_subprocess():
    """MCPGateway.start() spawns a real subprocess -- never let it run here."""
    with patch(
        "openexecutive.orchestrator.mcp_gateway.MCPGateway.start",
        new=_noop_gateway_start,
    ):
        yield


def test_background_jobs_disabled_starts_no_timer_tasks_but_resumer_runs(
    monkeypatch: pytest.MonkeyPatch, _no_real_mcp_subprocess: None
) -> None:
    """BACKGROUND_JOBS_ENABLED=false, with every prerequisite flag ON
    (SCHEDULER_ENABLED, MCP_ENABLED explicitly true) must still start
    neither the scheduler nor the email poller -- the new flag is the
    binding constraint, not a no-op. The resumer is unaffected by this flag
    and must start regardless.

    Explicit setenv("...", "false") rather than delenv: a developer with
    BACKGROUND_JOBS_ENABLED=true in their local .env (the expected way to
    actually use the scheduler locally) would make a delenv-based "off"
    test fail on their machine for the wrong reason. See
    test_background_jobs_enabled_defaults_to_false for the actual default.
    """
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("MCP_ENABLED", "true")

    scheduler_started = threading.Event()
    resumer_started = threading.Event()
    poller_started = threading.Event()

    with (
        patch("openexecutive.scheduler.run_scheduler", new=_recording_coro(scheduler_started)),
        patch("openexecutive.workflows.resumer.run_resumer", new=_recording_coro(resumer_started)),
        patch(
            "openexecutive.integrations.email_poller.run_email_poller",
            new=_recording_coro(poller_started),
        ),
        TestClient(create_app()) as client,
    ):
        # The MCP gateway object itself must still be created -- it's the
        # on-demand tool-call surface for live chat turns, unaffected by
        # this flag. Only the background poller task is gated.
        assert client.app.state.mcp_gateway is not None, (
            "MCP_ENABLED=true must still stand up the gateway for on-demand "
            "tool use even when BACKGROUND_JOBS_ENABLED is off"
        )
        assert not scheduler_started.wait(timeout=_SHOULD_NOT_FIRE_TIMEOUT), (
            "run_scheduler must not fire when BACKGROUND_JOBS_ENABLED is off"
        )
        assert not poller_started.wait(timeout=_SHOULD_NOT_FIRE_TIMEOUT), (
            "run_email_poller must not fire when BACKGROUND_JOBS_ENABLED is "
            "off, even though MCP_ENABLED is true"
        )
        assert resumer_started.wait(timeout=_SHOULD_FIRE_TIMEOUT), (
            "the resumer makes no LLM/API calls of its own and is not "
            "gated by BACKGROUND_JOBS_ENABLED -- it must always start"
        )


def test_background_jobs_enabled_starts_scheduler_and_poller_too(
    monkeypatch: pytest.MonkeyPatch, _no_real_mcp_subprocess: None
) -> None:
    """BACKGROUND_JOBS_ENABLED=true, with each mechanism's own prerequisite
    flag also satisfied, must start the scheduler and email poller -- this
    is a real AND-gate, not a flag that silently overrides/replaces the
    existing ones.
    """
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    monkeypatch.setenv("MCP_ENABLED", "true")

    scheduler_started = threading.Event()
    resumer_started = threading.Event()
    poller_started = threading.Event()

    with (
        patch("openexecutive.scheduler.run_scheduler", new=_recording_coro(scheduler_started)),
        patch("openexecutive.workflows.resumer.run_resumer", new=_recording_coro(resumer_started)),
        patch(
            "openexecutive.integrations.email_poller.run_email_poller",
            new=_recording_coro(poller_started),
        ),
        TestClient(create_app()) as client,
    ):
        assert client.app.state.mcp_gateway is not None
        assert scheduler_started.wait(timeout=_SHOULD_FIRE_TIMEOUT), (
            "run_scheduler must fire when BACKGROUND_JOBS_ENABLED and "
            "SCHEDULER_ENABLED are both true"
        )
        assert poller_started.wait(timeout=_SHOULD_FIRE_TIMEOUT), (
            "run_email_poller must fire when BACKGROUND_JOBS_ENABLED and "
            "MCP_ENABLED are both true"
        )
        assert resumer_started.wait(timeout=_SHOULD_FIRE_TIMEOUT), (
            "the resumer always starts, flag or no flag"
        )


def test_background_jobs_enabled_but_scheduler_disabled_still_gates_scheduler(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, _no_real_mcp_subprocess: None
) -> None:
    """BACKGROUND_JOBS_ENABLED is a superset AND-condition, not a
    replacement for SCHEDULER_ENABLED -- turning the new flag on must not
    resurrect a scheduler that its own dedicated flag explicitly disables.
    """
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "true")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    # This test doesn't care about the email poller, but BACKGROUND_JOBS_
    # ENABLED=true means it's one settings.mcp_enabled check away from
    # starting for real if a machine happens to have company/mcp_servers.json
    # on disk (Settings._resolve_mcp auto-enables on file existence) --
    # point it at a path that can't exist, same as the log-grep test below.
    monkeypatch.setenv(
        "MCP_SERVERS_CONFIG_PATH", str(tmp_path / "no-such-mcp-servers.json")
    )

    scheduler_started = threading.Event()

    with (
        patch("openexecutive.scheduler.run_scheduler", new=_recording_coro(scheduler_started)),
        TestClient(create_app()),
    ):
        assert not scheduler_started.wait(timeout=_SHOULD_NOT_FIRE_TIMEOUT), (
            "SCHEDULER_ENABLED=false must still block the scheduler even "
            "with BACKGROUND_JOBS_ENABLED=true"
        )


def _capture_openexecutive_logs() -> tuple[logging.Handler, StringIO]:
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.INFO)
    logger = logging.getLogger("openexecutive")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return handler, buf


def _wait_for_log_lines(
    buf: StringIO, phrases: list[str], *, timeout: float = 2.0, interval: float = 0.05
) -> str:
    """Poll `buf` until every phrase in `phrases` has appeared or `timeout`
    elapses. More robust than one fixed sleep -- returns as soon as the
    condition is met instead of always paying the full budget, and doesn't
    depend on guessing the right sleep duration for CI/load variance.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = buf.getvalue()
        if all(p in text for p in phrases):
            return text
        time.sleep(interval)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("background_jobs_enabled", "expect_scheduler"),
    [(False, False), (True, True)],
)
def test_log_grep_real_scheduler_and_resumer_startup_lines(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    background_jobs_enabled: bool,
    expect_scheduler: bool,
    _no_real_mcp_subprocess: None,
) -> None:
    """Log-grep check against the UNMODIFIED run_scheduler/run_resumer --
    confirms the real "scheduler started" INFO line (scheduler/runner.py)
    tracks BACKGROUND_JOBS_ENABLED, while "resumer started"
    (workflows/resumer.py) is present in BOTH cases, matching what an
    operator watching `make dev` output would actually see.

    `_company_profile_active` is forced False so the real scheduler tick
    that runs here can never reach `claim_due_actions` -- this test cares
    only about the startup log line, not about dispatching anything for
    real. MCP_SERVERS_CONFIG_PATH points at a path that cannot exist so
    Settings' file-existence auto-enable can't flip mcp_enabled=True out
    from under the test on a machine that happens to have a real
    company/mcp_servers.json configured.
    """
    monkeypatch.setenv("BACKGROUND_JOBS_ENABLED", "true" if background_jobs_enabled else "false")
    monkeypatch.delenv("MCP_ENABLED", raising=False)
    monkeypatch.setenv(
        "MCP_SERVERS_CONFIG_PATH", str(tmp_path / "no-such-mcp-servers.json")
    )
    # NOTE: this test's real run_scheduler tick reads/writes whatever DB
    # EPISODIC_DB_PATH resolves to at import time (a module-level constant
    # in memory.episodic, alerts.store, departments.store, etc. -- all
    # colocated in the same physical file by convention, each frozen
    # separately). Redirecting only one of those modules' DB_PATH desyncs
    # it from the others (tried: broke on "no such table" from a schema
    # created via the OLD path). Full isolation would mean monkeypatching
    # every such module consistently to the same tmp path -- out of scope
    # here. `_company_profile_active=False` below is what actually
    # prevents real dispatch (claim_due_actions never runs), which is the
    # property that matters; a stray write to whatever local dev DB this
    # process resolves to is a much softer, accepted residual risk.

    handler, buf = _capture_openexecutive_logs()
    try:
        with (
            patch(
                "openexecutive.scheduler.runner._company_profile_active",
                return_value=False,
            ),
            TestClient(create_app()),
        ):
            # Resumer always starts -- wait for its line first, since that's
            # a positive assertion in every parametrized case and bounds how
            # long the whole test takes. Whether or not the scheduler *also*
            # started (per expect_scheduler) is read from the same buffer
            # snapshot afterward: by the time the resumer -- created in the
            # same lifespan(), around the same moment -- has had time to log,
            # a scheduler that erroneously started has had equal opportunity
            # to log too, so this doesn't need its own separate wait.
            log_text = _wait_for_log_lines(buf, ["resumer started"], timeout=_SHOULD_FIRE_TIMEOUT)
    finally:
        logging.getLogger("openexecutive").removeHandler(handler)

    assert "resumer started" in log_text, log_text
    assert ("scheduler started" in log_text) == expect_scheduler, log_text
