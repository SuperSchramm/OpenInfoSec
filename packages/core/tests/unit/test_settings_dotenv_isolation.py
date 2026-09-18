"""Regression tests for issue #28: unit tests must not read a developer's .env.

``Settings`` loads ``env_file`` on every ``get_settings()`` call. tests/conftest.py
autouse-drops it, so a local run behaves like CI (which has no ``.env``).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from openexecutive.config import Settings, get_settings


def test_env_file_is_disabled_while_a_test_runs() -> None:
    assert Settings.model_config.get("env_file") is None


def test_a_dotenv_file_does_not_reach_get_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Control: prove the mechanism -- when an env_file IS configured (what every
    # test would see without the conftest fixture), its values do load...
    dotenv = tmp_path / ".env"
    dotenv.write_text("ENABLE_WEB_SEARCH=false\n", encoding="utf-8")
    monkeypatch.delenv("ENABLE_WEB_SEARCH", raising=False)
    monkeypatch.setitem(Settings.model_config, "env_file", str(dotenv))
    assert get_settings().enable_web_search is False

    # ...and with the isolation restored, the same call ignores the file.
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    assert get_settings().enable_web_search is True


def test_explicit_env_file_argument_still_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tests that deliberately load a file (Settings(_env_file=...)) keep working.
    dotenv = tmp_path / "custom.env"
    dotenv.write_text("ENABLE_WEB_SEARCH=false\n", encoding="utf-8")
    monkeypatch.delenv("ENABLE_WEB_SEARCH", raising=False)
    assert Settings(_env_file=str(dotenv)).enable_web_search is False  # type: ignore[call-arg]
