"""Phase 2 -- DrugOS Knowledge Graph package.

v43 ROOT FIX (Chain 7 -- bare-import packaging): the Phase 2 modules
under ``drugos_graph/`` use absolute imports like
``from drugos_graph.kg_builder import ...`` which require ``phase2/``
to be on ``sys.path``. Previously this required the operator to either
``cd phase2/`` first OR run via ``run_unified.py`` (which bootstraps
sys.path).

This ``__init__.py`` bootstraps ``sys.path`` so that imports work from
ANY current working directory. Operators can now do::

    from phase2.drugos_graph.kg_builder import DrugOSGraphBuilder

without setting up sys.path themselves.

The bootstrap is idempotent -- it only inserts paths into ``sys.path``
if they're not already there.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Idempotent sys.path bootstrap.
_PHASE2_ROOT = Path(__file__).resolve().parent
_PHASE2_ROOT_STR = str(_PHASE2_ROOT)
if _PHASE2_ROOT_STR not in sys.path:
    sys.path.insert(0, _PHASE2_ROOT_STR)

# Also add the project root so `from phase2.xxx import ...` works.
_PROJECT_ROOT = _PHASE2_ROOT.parent
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)
