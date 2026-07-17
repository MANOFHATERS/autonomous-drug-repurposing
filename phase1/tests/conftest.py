"""
Shared pytest fixtures for the Drug Repurposing ETL test suite.

Provides:
  - SQLite in-memory database session for testing
  - Sample DataFrames for drugs, proteins
  - Temp directory for file operations
"""

from __future__ import annotations

import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Ensure project root AND phase1/ are importable
# ---------------------------------------------------------------------------
# v114 round 5 FORENSIC ROOT FIX: the previous code added only the REPO
# ROOT (PROJECT_ROOT). But phase1 test files do `from config.settings
# import ...` and `import pipelines.omim_pipeline` — both require
# phase1/ itself on sys.path (config/ and pipelines/ live inside phase1/).
# The root conftest.py adds phase1/ early, but some root test files
# manipulate sys.path (insert/remove) during collection, which can push
# phase1/ off or remove it entirely. By the time phase1/tests/ files are
# imported, `from config.settings` fails with ModuleNotFoundError.
# ROOT FIX: add phase1/ HERE too (defense-in-depth). This conftest runs
# right before phase1/tests/ collection, re-asserting phase1/ on sys.path
# even if a root test removed it. Idempotent (checks before inserting).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root
PHASE1_ROOT = Path(__file__).resolve().parent.parent          # phase1/ dir
for _p in (PROJECT_ROOT, PHASE1_ROOT):
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

# v114 round 5 FORENSIC ROOT FIX (module-name collision):
# There are TWO packages named `config` in the repo:
#   1. phase1/config/        (has settings.py — the Phase 1 config)
#   2. graph_transformer/config/  (has only __init__.py — GT config)
# When root tests/ import graph_transformer modules, Python registers
# `config` in sys.modules pointing to graph_transformer/config/ (which
# has NO settings submodule). Then phase1/tests/ files that do
# `from config.settings import ...` fail with ModuleNotFoundError because
# `config` in sys.modules is the WRONG package.
# ROOT FIX: explicitly import phase1/config/ and register it as `config`
# in sys.modules BEFORE any phase1 test runs. This ensures
# `from config.settings import` resolves to phase1/config/settings.py.
# This is safe because graph_transformer uses RELATIVE imports
# (`from .config import ...`) — it never does bare `import config`.
import importlib as _importlib_p1cfg
_p1_config_path = PHASE1_ROOT / "config"
if _p1_config_path.exists():
    # Insert phase1/ at position 0 so `import config` finds phase1/config/ first.
    _p1_str = str(PHASE1_ROOT)
    if _p1_str in sys.path:
        sys.path.remove(_p1_str)
    sys.path.insert(0, _p1_str)
    # Force (re)import of config as phase1/config/.
    if "config" in sys.modules:
        # The existing `config` module is likely graph_transformer/config.
        # Save it under its qualified name so graph_transformer's relative
        # imports still work, then replace `config` with phase1's.
        _gt_config = sys.modules.pop("config")
        sys.modules["graph_transformer.config"] = _gt_config
    try:
        _p1_config = _importlib_p1cfg.import_module("config")
        sys.modules["config"] = _p1_config
    except ImportError:
        pass  # phase1/config/ not importable — skip (defensive)


# ---------------------------------------------------------------------------
# Logger-level isolation fixture (prevents test-isolation bugs)
# ---------------------------------------------------------------------------
# Some test modules set LOG_LEVEL=WARNING at import time via
# ``os.environ.setdefault("LOG_LEVEL", "WARNING")``.  When ``setup_logging()``
# is later called by another test, the ``pipelines`` logger level is
# permanently set to WARNING, which causes ``caplog`` to miss INFO records
# in subsequent tests (e.g. test_string_pipeline_institutional_v149).
# This autouse fixture resets the key namespace loggers to NOTSET after
# every test so that each test starts with a clean logger state.  It does
# NOT affect tests that explicitly set the level within their own scope
# (those tests set the level after this fixture's setup phase).
@pytest.fixture(autouse=True)
def _reset_namespace_logger_levels():
    """Reset platform namespace logger levels to NOTSET after each test.

    This prevents test-isolation bugs where one test sets a logger level
    (e.g. via ``setup_logging()`` or ``set_log_level()``) and the level
    persists into subsequent tests, breaking ``caplog`` capture.
    """
    _namespaces = (
        "config",
        "pipelines",
        "pipelines.base_pipeline",
        "pipelines.chembl_pipeline",
        "pipelines.drugbank_pipeline",
        "pipelines.uniprot_pipeline",
        "pipelines.string_pipeline",
        "pipelines.disgenet_pipeline",
        "pipelines.omim_pipeline",
        "pipelines.pubchem_pipeline",
        "database",
        "cleaning",
        "entity_resolution",
        "exporters",
    )
    _saved_levels: dict[str, int] = {}
    for ns in _namespaces:
        _saved_levels[ns] = logging.getLogger(ns).level
    yield
    for ns in _namespaces:
        logger = logging.getLogger(ns)
        # Only reset if the level was changed during the test
        if logger.level != _saved_levels[ns]:
            logger.setLevel(_saved_levels[ns])

# v114 round 7 FORENSIC ROOT FIX (Base class dual-import — tables missing):
# The models in database/models.py import Base via the QUALIFIED path
# `from phase1.database.base import Base` (line 68). But this conftest
# imported via the BARE path `from database.base import Base`. Even
# though both resolve to the same FILE, Python's import system treats
# `database.base` and `phase1.database.base` as DIFFERENT modules
# (different __name__) — so they create DIFFERENT DeclarativeBase
# subclasses with DIFFERENT metadata. The models register their tables
# on `phase1.database.base.Base.metadata`, but the conftest's
# `Base.metadata.create_all(engine)` used `database.base.Base.metadata`
# which had ZERO tables. Result: every db_engine fixture created an
# empty SQLite DB → "no such table: drugs/proteins/pipeline_runs" in
# 34+ tests.
#
# Teammate-3's v117 fix (00a164e) made the two Base.metadata objects
# the SAME, but the models still register on a separate Base — the
# dual-import was NOT fully resolved for the test path.
#
# ROOT FIX: import Base via the SAME qualified path the models use:
# `from phase1.database.base import Base`. This ensures the conftest's
# Base.metadata is the SAME object the models registered their tables
# on. create_all() will then create all tables correctly.
from phase1.database.base import Base
# v114 round 7: import models via the SAME qualified path (phase1.database.models)
# that the models themselves use internally. Importing via the bare
# `database.models` path causes Python to execute the module TWICE (once as
# `phase1.database.models` via internal imports, once as `database.models`
# via this conftest import) — every class gets defined twice on Base,
# causing "Multiple classes found for path" errors and "index already
# exists" errors during create_all.
from phase1.database.models import (
    Drug,
    DrugProteinInteraction,
    EntityMapping,
    GeneDiseaseAssociation,
    PipelineRun,
    Protein,
    ProteinProteinInteraction,
)
# v114 round 7 FORENSIC ROOT FIX (sys.modules alias for dual-import):
# Test files (test_bug_fixes, test_db_loaders, test_omim_pipeline, db_helpers)
# import via the BARE path `from database.models import ...`. Python treats
# `database.models` and `phase1.database.models` as DIFFERENT modules —
# executing the file twice, defining every ORM class twice on Base. This
# causes "Multiple classes found for path DrugProteinInteraction" and
# "index uq_drugs_chembl_id already exists" during create_all.
#
# ROOT FIX: alias `database` -> `phase1.database` in sys.modules so both
# import paths resolve to the SAME module object. This is the standard
# Python pattern for packages that need to work under two names. The alias
# is set AFTER phase1.database is imported (above), so it points to the
# already-loaded module. Test files' `from database.models import ...` will
# then hit the cached `phase1.database.models` instead of re-executing.
import sys as _sys_alias
if "phase1.database" in _sys_alias.modules and "database" not in _sys_alias.modules:
    _sys_alias.modules["database"] = _sys_alias.modules["phase1.database"]
if "phase1.database.models" in _sys_alias.modules and "database.models" not in _sys_alias.modules:
    _sys_alias.modules["database.models"] = _sys_alias.modules["phase1.database.models"]
if "phase1.database.base" in _sys_alias.modules and "database.base" not in _sys_alias.modules:
    _sys_alias.modules["database.base"] = _sys_alias.modules["phase1.database.base"]

# v90 ROOT FIX (BUG #10): _get_environment() now defaults to "production"
# (fail-closed) when DRUGOS_ENVIRONMENT / ENVIRONMENT / ENV is unset. Tests
# need dev-sized pools and dev-mode logging, so explicitly opt into dev.
# This MUST run before any connection.py module-level code executes.
import os as _os
_os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")


# ============================================================================
# Database fixtures
# ============================================================================


@pytest.fixture(scope="function")
def db_engine():
    """Create a fresh SQLite in-memory engine with FK enforcement and ``now()`` support."""
    engine = create_engine("sqlite:///:memory:", echo=False)

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_conn, connection_record):
        if isinstance(dbapi_conn, sqlite3.Connection):
            # Enable foreign-key enforcement (off by default in SQLite)
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
            # FIX #31: Return datetime string that SQLite can parse for DEFAULT values
            dbapi_conn.create_function(
                "now", 0,
                lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S+00:00")
            )

    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(db_engine):
    """Yield a transactional SQLAlchemy ``Session`` bound to an in-memory SQLite DB.

    The session is rolled back after each test to keep the DB clean.
    """
    session = sessionmaker(bind=db_engine)()
    yield session
    session.rollback()
    session.close()


# ============================================================================
# Sample-data fixtures
# ============================================================================


@pytest.fixture
def sample_drug_df() -> pd.DataFrame:
    """Minimal drug DataFrame matching the ``Drug`` model columns.

    Returns a fresh copy each time so tests that mutate the DataFrame
    don't pollute other tests (test-isolation hygiene).
    """
    return pd.DataFrame(
        {
            "inchikey": [
                "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "WFXAZNNJSJXTJZ-UHFFFAOYSA-N",
            ],
            "name": ["Aspirin", "Ibuprofen"],
            "chembl_id": ["CHEMBL25", "CHEMBL521"],
            "drugbank_id": ["DB00945", "DB01050"],
            "pubchem_cid": [2244, 3672],
            "molecular_formula": ["C9H8O4", "C13H18O2"],
            "molecular_weight": [180.16, 206.28],
            "smiles": [
                "CC(=O)Oc1ccccc1C(=O)O",
                "CC(C)Cc1ccc(cc1)C(C)C(=O)O",
            ],
            "is_fda_approved": [True, True],
            "max_phase": [4, 4],
            "drug_type": ["small_molecule", "small_molecule"],
            "mechanism_of_action": ["COX inhibitor", "COX inhibitor"],
        }
    ).copy()


@pytest.fixture
def sample_protein_df() -> pd.DataFrame:
    """Minimal protein DataFrame matching the ``Protein`` model columns.

    FIX C4/D9: gene_name stores CANONICAL PROTEIN NAME, NOT gene symbols.
    gene_symbol is the actual gene symbol used for GDA resolution.

    Returns a fresh copy each time so tests that mutate the DataFrame
    don't pollute other tests (test-isolation hygiene).
    """
    return pd.DataFrame(
        {
            "uniprot_id": ["P23219", "P04637"],
            "gene_name": [
                "Prostaglandin G/H synthase 1",  # protein name, NOT "PTGS1"
                "Cellular tumor antigen p53",        # protein name, NOT "TP53"
            ],
            "gene_symbol": ["PTGS1", "TP53"],  # actual gene symbols
            "protein_name": [
                "Prostaglandin G/H synthase 1",
                "Cellular tumor antigen p53",
            ],
            "organism": ["Homo sapiens", "Homo sapiens"],
            "sequence": ["M" * 100, "M" * 100],
            "function_desc": ["COX enzyme", "Tumor suppressor"],
            "string_id": [
                "9606.ENSP00000269305",
                "9606.ENSP00000269306",
            ],
        }
    ).copy()


@pytest.fixture
def temp_dir(tmp_path) -> Path:
    """Temporary directory for file-based tests."""
    return tmp_path


# ============================================================================
# PostgreSQL integration test fixtures (FIX #20)
# ============================================================================


@pytest.fixture(scope="session")
def pg_engine():
    """Create a PostgreSQL engine for integration testing.

    FIX #20: Allows running integration tests against a real PostgreSQL
    database. Set TEST_DATABASE_URL environment variable to enable.
    Skips tests if not configured.
    """
    import os
    test_db_url = os.getenv("TEST_DATABASE_URL")
    if not test_db_url:
        pytest.skip("TEST_DATABASE_URL not set, skipping PostgreSQL tests")
    engine = create_engine(test_db_url, echo=False)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture(scope="function")
def pg_session(pg_engine):
    """Yield a session bound to a PostgreSQL test database.

    FIX #20: Provides a session connected to a real PostgreSQL instance
    for integration testing. Rolls back after each test.
    """
    session = sessionmaker(bind=pg_engine)()
    yield session
    session.rollback()
    session.close()
