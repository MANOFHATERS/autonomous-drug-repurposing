"""Shared sys.path bootstrap for all Airflow DAG files (v89 ROOT FIX BUG #39).

PROBLEM (BUG #39)
-----------------
The ``_PROJECT_ROOT`` sys.path insertion block was duplicated verbatim
in ALL 8 DAG files (master_pipeline_dag + 7 standalone DAGs):

    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)

If the project structure ever changes (e.g. ``dags/`` moves), all 8
files must be updated in lock-step — a classic copy-paste maintenance
hazard. A single missed update produces ``ImportError`` at DAG parse
time in only SOME DAGs, with no compile-time signal.

ROOT FIX (master-grade, no sugar-coating)
-----------------------------------------
Extract the bootstrap into THIS shared module. Every DAG file now
imports it once:

    from dags._dags_init import ensure_project_root  # noqa: F401

The import itself executes ``ensure_project_root()`` at module load
time (via the module-level call at the end of this file), so the
side-effect is identical to the previous inline block. The DAG files
shrink by 4 lines each, and the path-setup logic lives in ONE place.

This module has NO Airflow dependency and can be imported in any
Python context (CI, pytest, manual inspection).
"""

from __future__ import annotations

import sys
from pathlib import Path

#: The Phase 1 project root (parent of the ``dags/`` directory). All
#: pipeline / config / entity_resolution imports resolve from here.
_PROJECT_ROOT: str = str(Path(__file__).resolve().parent.parent)


def ensure_project_root() -> str:
    """Insert ``_PROJECT_ROOT`` at the front of ``sys.path`` (idempotent).

    Returns the project-root string so callers can reuse it without
    recomputing the path.
    """
    if _PROJECT_ROOT not in sys.path:
        sys.path.insert(0, _PROJECT_ROOT)
    return _PROJECT_ROOT


# Module-level side effect: ensure the path is set up the moment this
# module is imported. This preserves the previous inline-block semantics
# so existing DAG files that did ``sys.path.insert(0, _PROJECT_ROOT)``
# at module top continue to work after the refactor.
ensure_project_root()
