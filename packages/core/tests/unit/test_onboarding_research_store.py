"""Unit tests for onboarding.py's post-onboarding research background task.

Regression guard for issue #17: _fire_post_onboarding_research constructed
its own ChromaDBStore() instead of reusing the process-wide store the way
every other background-task call site (no Request/app access available)
does via orchestrator.store_access.get_shared_store().
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


class _FakeInputs:
    def __init__(self, note: str = "") -> None:
        self.note = note

    def model_dump(self) -> dict[str, Any]:
        return {"note": self.note}


class _FakeEvent:
    def __init__(self, type_: str, content: str = "", message: str = "") -> None:
        self.type = type_
        self.content = content
        self.message = message


class _FakeWorkflow:
    title = "Executive Research"
    received_store: Any = None

    def input_model(self) -> type[_FakeInputs]:
        return _FakeInputs

    async def run(self, inputs: _FakeInputs, store: Any):
        type(self).received_store = store
        yield _FakeEvent("artifact", content="done")


@pytest.mark.asyncio
async def test_fire_post_onboarding_research_uses_shared_store() -> None:
    from openexecutive.api.routes import onboarding

    fake_store = MagicMock()
    fake_workflow = _FakeWorkflow()

    with (
        patch(
            "openexecutive.orchestrator.store_access.get_shared_store",
            return_value=fake_store,
        ) as mock_get_store,
        patch("openexecutive.workflows.WORKFLOW_REGISTRY", {"executive_research": fake_workflow}),
        patch("openexecutive.workflows.persistence.create_run"),
        patch("openexecutive.workflows.persistence.complete_run"),
        patch("openexecutive.workflows.persistence.fail_run"),
    ):
        await onboarding._fire_post_onboarding_research("session-123")

    mock_get_store.assert_called_once()
    assert fake_workflow.received_store is fake_store
