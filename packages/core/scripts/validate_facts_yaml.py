#!/usr/bin/env python3
"""Fail fast if architecture-facts.yaml doesn't parse.

This file is almost all hand-authored prose. The recurring failure mode
(issue #6) is a plain (unquoted) scalar value containing ": " — PyYAML reads
that as the start of a nested mapping key and raises ScannerError. Every prior
incident was caught only by the full pytest suite (test_doc_code_consistency.py
/ test_architecture_facts.py both call yaml.safe_load on this file), which is
slow and not part of `ruff`/`mypy`. This script gives the same signal in
milliseconds via `make lint` / CI, without adding new CI infrastructure.

Usage:
    python scripts/validate_facts_yaml.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

_FACTS_YAML = (
    Path(__file__).resolve().parent.parent
    / "openexecutive"
    / "architecture"
    / "architecture-facts.yaml"
)


def main() -> int:
    try:
        text = _FACTS_YAML.read_text()
    except OSError as exc:
        print(f"validate_facts_yaml: cannot read {_FACTS_YAML}: {exc}", file=sys.stderr)
        return 1

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print(f"validate_facts_yaml: {_FACTS_YAML} failed to parse:\n{exc}", file=sys.stderr)
        print(
            "\nLikely cause: an unquoted scalar value containing ': ' (colon-space), "
            "which YAML reads as a nested mapping key. Quote the value or move it "
            "into a block literal (`key: |`). See issue #6.",
            file=sys.stderr,
        )
        return 1

    if not isinstance(data, dict):
        print(
            f"validate_facts_yaml: {_FACTS_YAML} parsed to {type(data).__name__}, "
            "not a mapping — file is likely empty or truncated",
            file=sys.stderr,
        )
        return 1

    print(f"validate_facts_yaml: {_FACTS_YAML.name} parses cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
