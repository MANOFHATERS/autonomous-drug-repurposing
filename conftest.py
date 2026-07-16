"""Top-level conftest.py for the Autonomous Drug Repurposing Platform.

Task 359 ROOT FIX: the previous CI workflow ran only 13 of 76 test
files via hand-picked paths. Switching to ``pytest tests/`` requires
a top-level conftest.py that:

1. Inserts phase1, phase2, rl, graph_transformer, scripts, and the
   repo root onto ``sys.path`` so test files that import
   ``from pipelines.chembl_pipeline import ...`` or
   ``from graph_transformer.models... import ...`` resolve regardless
   of where pytest was invoked from.

2. Registers the ``slow`` / ``network`` / ``gpu`` / ``integration`` /
   ``forensic`` markers (also declared in pytest.ini — declared in
   both places because some test runners ignore pytest.ini if a
   setup.cfg / pyproject.toml is also present; this is belt-and-
   suspenders).

3. Provides a ``pytest_collection_modifyitems`` hook that auto-marks
   tests in directories named ``forensic`` with the ``forensic`` marker
   so they always run (audit-driven root-cause verification tests).

This file is intentionally LIGHT — it does NOT mock anything, does NOT
patch sys.path with fake modules, does NOT skip tests based on env vars
(unless explicitly requested via the standard ``-m`` flag). Tests that
need to skip when an optional dep is missing should use
``pytest.importorskip("rdkit")`` at the top of the test file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent
# Every directory that contains importable Python packages must be on
# sys.path. This list MUST stay in sync with the actual package layout.
# Adding a new top-level package? Add its directory here too.
_PATHS_TO_ADD = [
    _REPO_ROOT,
    _REPO_ROOT / "phase1",
    _REPO_ROOT / "phase2",
    _REPO_ROOT / "phase2" / "drugos_graph",
    _REPO_ROOT / "graph_transformer",
    _REPO_ROOT / "rl",
    _REPO_ROOT / "scripts",
    _REPO_ROOT / "common",
]
for _p in _PATHS_TO_ADD:
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers (mirrors pytest.ini)."""
    for marker, desc in [
        ("slow", "long-running tests — skipped in PR CI, run on merge"),
        ("network", "tests that hit live external APIs — skipped in PR CI"),
        ("gpu", "tests that require a CUDA GPU — skipped in CPU CI"),
        ("integration", "multi-phase end-to-end tests — run on merge to main"),
        ("forensic", "audit-driven root-cause verification tests — always run"),
    ]:
        config.addinivalue_line("markers", f"{marker}: {desc}")


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    """Auto-mark tests in any directory containing 'forensic'."""
    for item in items:
        # Auto-mark forensic tests so they always run.
        if "forensic" in str(item.fspath).lower():
            item.add_marker(pytest.mark.forensic)
