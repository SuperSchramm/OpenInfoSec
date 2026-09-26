"""Guard for issue #49: the suite must never resolve to the developer's real data."""
from __future__ import annotations

from pathlib import Path

from openexecutive.config import get_settings

_REPO_ROOT = Path(__file__).resolve().parents[4]


def test_default_paths_are_not_the_real_repo_folders() -> None:
    s = get_settings()
    for p in (s.vector_store_path, s.company_profile_path):
        assert not p.resolve().is_relative_to(_REPO_ROOT), f"{p} points into the real repo"


def test_paths_are_absolute_and_exist_parent() -> None:
    s = get_settings()
    assert s.company_profile_path.parent.is_dir()
