"""Phase 1 -- Data Ingestion Pipelines package.

v43 ROOT FIX (Chain 7 -- bare-import packaging): the Phase 1 modules use
bare imports like ``from cleaning.normalizer import ...`` which only
work when ``phase1/`` is on ``sys.path``. Previously this required the
operator to either ``cd phase1/`` first OR run via ``run_unified.py``
(which bootstraps sys.path). Importing ``phase1.pipelines.chembl``
from outside the package raised ``ModuleNotFoundError: No module named
'cleaning'``.

This ``__init__.py`` bootstraps ``sys.path`` so that bare imports work
from ANY current working directory. Operators can now do::

    from phase1.pipelines.chembl_pipeline import ChEMBLPipeline

without setting up sys.path themselves.

The bootstrap is idempotent -- it only inserts ``phase1/`` into
``sys.path`` if it's not already there.
"""
from __future__ import annotations

import importlib.abc
import importlib.util
import sys
from pathlib import Path

# Idempotent sys.path bootstrap for bare imports.
_PHASE1_ROOT = Path(__file__).resolve().parent
_PHASE1_ROOT_STR = str(_PHASE1_ROOT)
if _PHASE1_ROOT_STR not in sys.path:
    sys.path.insert(0, _PHASE1_ROOT_STR)

# Also add the project root so `from phase1.xxx import ...` works.
_PROJECT_ROOT = _PHASE1_ROOT.parent
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
if _PROJECT_ROOT_STR not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT_STR)

# ---------------------------------------------------------------------------
# v117 ROOT FIX (Task 1-a NEW finding — schema_version dual-import):
# Install a meta-path import finder that redirects ALL bare imports
# (`database`, `cleaning`, `config`, `dags`, `entity_resolution`,
# `exporters`, `contracts`, `pipelines`, `_circuit_breaker`) to their
# canonical absolute paths (`phase1.database`, `phase1.cleaning`, ...).
#
# ROOT CAUSE being fixed:
#   The Phase 1 codebase uses bare imports like `from database.models
#   import Drug` (intra-package) AND absolute imports like `from
#   phase1.database.models import Drug` (cross-phase bridges). When
#   BOTH paths are exercised in the same Python process, Python's import
#   machinery creates TWO separate module objects for the same .py file.
#   The module body executes twice, so `class SchemaVersion(Base)` /
#   `class Drug(Base)` register the same table twice against the same
#   `Base.metadata`, raising:
#       sqlalchemy.exc.InvalidRequestError: Table 'schema_version' is
#       already defined for this MetaData instance.
#   This breaks `import phase1.database.loaders` entirely, which blocks
#   Phase 2's KG builder, Phase 3's GNN trainer, and Phase 4's RL ranker
#   — none of them can import Phase 1's models.
#
# ROOT FIX:
#   This finder intercepts every `import database.*` / `import cleaning.*`
#   / etc. and redirects it to `import phase1.database.*` / etc., then
#   aliases the bare name in `sys.modules` to point to the canonical
#   module. Both import paths now resolve to the SAME module object, so
#   the module body executes exactly once and each table is registered
#   exactly once.
#
# Defense-in-depth: the ORM model classes in phase1/database/models.py
# also set `__table_args__ = {..., "extend_existing": True}` so that
# even if a bare import slips through before this finder is installed
# (e.g., `cd phase1 && python -c "from database.models import Drug"`
# before `import phase1` runs), SQLAlchemy reuses the existing Table
# instead of raising.
# ---------------------------------------------------------------------------
_BARE_TO_ABSOLUTE: dict[str, str] = {
    "database": "phase1.database",
    "cleaning": "phase1.cleaning",
    "config": "phase1.config",
    "contracts": "phase1.contracts",
    "entity_resolution": "phase1.entity_resolution",
    "exporters": "phase1.exporters",
    "dags": "phase1.dags",
    "pipelines": "phase1.pipelines",
    "scripts": "phase1.scripts",
    "_circuit_breaker": "phase1._circuit_breaker",
}


class _Phase1BareImportRedirector(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """Redirect bare intra-Phase-1 imports to their canonical ``phase1.*`` paths.

    When Python resolves ``import database.models``, this finder matches
    the ``database`` prefix, imports ``phase1.database.models`` (the
    canonical absolute module), and aliases ``sys.modules["database.models"]``
    to that canonical module. Both import paths then resolve to the SAME
    module object, preventing the dual-module ``InvalidRequestError``.
    """

    def find_spec(self, fullname: str, path, target=None):
        canonical = None
        # Top-level bare package (e.g. "database", "cleaning")
        if fullname in _BARE_TO_ABSOLUTE:
            canonical = _BARE_TO_ABSOLUTE[fullname]
        else:
            # Submodule of a bare package (e.g. "database.models", "cleaning.normalizer")
            for bare, canonical_pkg in _BARE_TO_ABSOLUTE.items():
                prefix = bare + "."
                if fullname.startswith(prefix):
                    canonical = canonical_pkg + fullname[len(bare):]
                    break
        if canonical is None:
            return None
        # Resolve the canonical module. If it's already loaded, reuse it
        # (this is the KEY: we never re-execute the module body). If not,
        # import it now (which registers it under its canonical name).
        canonical_mod = sys.modules.get(canonical)
        if canonical_mod is None:
            try:
                canonical_mod = importlib.import_module(canonical)
            except ImportError:
                return None
        # Alias the bare name to the canonical module IN sys.modules.
        sys.modules[fullname] = canonical_mod
        # Return a spec with ourselves as loader. create_module will return
        # the canonical module (already in sys.modules), and exec_module is
        # a no-op. This ensures the module body is NEVER re-executed.
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        # Return the canonical module that find_spec already placed in
        # sys.modules. This MUST be non-None, otherwise module_from_spec
        # creates a brand-new empty ModuleType and overwrites our alias.
        mod = sys.modules.get(spec.name)
        return mod

    def exec_module(self, module):
        # The canonical module is already fully executed; nothing to do.
        # This MUST be a no-op to prevent re-execution of the module body.
        pass


# Install at the FRONT of sys.meta_path so we run BEFORE the default
# path-based finders (which would otherwise import `database/` as a
# fresh top-level package from the sys.path entry we just added).
if not any(isinstance(f, _Phase1BareImportRedirector) for f in sys.meta_path):
    sys.meta_path.insert(0, _Phase1BareImportRedirector())
