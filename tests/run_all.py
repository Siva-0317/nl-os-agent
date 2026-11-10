"""tests/run_all.py - Thin wrapper around pytest to run and collect the full test suite."""

from __future__ import annotations

import os
import sys
import pytest

# Ensure repo root and src are on sys.path
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_src = os.path.join(_repo_root, "src")
for p in (_repo_root, _src):
    if p not in sys.path:
        sys.path.insert(0, p)


def main(args: list[str] | None = None) -> int:
    """Invoke pytest on tests/ passing forward any extra CLI flags."""
    passed_args = sys.argv[1:] if args is None else args
    if not any(arg.startswith("tests") for arg in passed_args):
        passed_args = ["tests"] + passed_args
    return pytest.main(passed_args)


if __name__ == "__main__":
    sys.exit(main())
