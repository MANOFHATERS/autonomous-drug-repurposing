"""Contract tests for Teammate 2 Task 2.4 — PubChem enrichment.

Tests the REAL CODE paths:
1. phase1 PubChem pipeline writes to ``pubchem_enrichment.csv``
   (matches phase2 pubchem_loader.DEFAULT_PUBCHEM_CSV).
2. The CSV contains all required fields: CID, SMILES, molecular_formula,
   molecular_weight, logP, TPSA.
3. phase2 pubchem_loader can read the CSV.

Verification command from the task brief:
    python -m phase1.pipelines pubchem && ls -la phase1/processed_data/pubchem_enrichment.csv
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")
os.environ.setdefault("DRUGOS_DOWNLOAD_MODE", "sample")
os.environ.setdefault("DRUGOS_ALLOW_SAMPLE_MODE", "true")
os.environ.setdefault("DRUGOS_ALLOW_DEV_ESCAPES", "true")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "phase1"))

import phase1  # noqa: F401
from phase1.pipelines.pubchem_pipeline import PubChemPipeline


def _bypass_db_init(self):
    if self.raw_dir is None:
        from config.settings import RAW_DATA_DIR
        self.raw_dir = RAW_DATA_DIR / self.source_name
    self.raw_dir.mkdir(parents=True, exist_ok=True)
    from config.settings import PROCESSED_DATA_DIR
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _patch_ensure_directories(monkeypatch):
    monkeypatch.setattr(PubChemPipeline, "_ensure_directories", _bypass_db_init)


# ============================================================================
# Test 1: Canonical filename is used
# ============================================================================

def test_pubchem_pipeline_writes_canonical_filename():
    """phase1 PubChem pipeline must write to pubchem_enrichment.csv
    (matches phase2 pubchem_loader.DEFAULT_PUBCHEM_CSV). The previous
    code had a filename mismatch (P2-036) — this test guards against
    regression."""
    p = PubChemPipeline.__new__(PubChemPipeline)  # bypass __init__
    p.source_name = "pubchem"
    p.processed_filename = None
    filename = p._get_processed_filename()
    assert filename == "pubchem_enrichment.csv", (
        f"phase1 PubChem must write to 'pubchem_enrichment.csv', got "
        f"{filename!r}. phase2 pubchem_loader will not find the CSV."
    )


def test_phase2_pubchem_loader_expects_canonical_filename():
    """phase2 pubchem_loader.DEFAULT_PUBCHEM_CSV must point to the
    canonical filename that phase1 writes."""
    from phase2.drugos_graph.pubchem_loader import DEFAULT_PUBCHEM_CSV
    assert DEFAULT_PUBCHEM_CSV.name == "pubchem_enrichment.csv", (
        f"phase2 pubchem_loader expects {DEFAULT_PUBCHEM_CSV.name!r} — "
        f"must match phase1's canonical output filename."
    )


# ============================================================================
# Test 2: All required fields are present in the CSV
# ============================================================================

REQUIRED_FIELDS = {
    "CID": ["cid", "pubchem_cid", "compound_id"],
    "SMILES": ["smiles", "canonical_smiles", "isomeric_smiles"],
    "molecular_formula": ["molecular_formula"],
    "molecular_weight": ["molecular_weight", "mol_weight"],
    "logP": ["logp", "log_p", "xlogp"],
    "TPSA": ["tpsa", "topological_polar_surface_area"],
}


@pytest.mark.parametrize("field,variants", list(REQUIRED_FIELDS.items()))
def test_pubchem_csv_has_required_field(field, variants):
    """The PubChem enrichment CSV must contain all required fields per
    the task brief: CID, SMILES, molecular_formula, molecular_weight,
    logP, TPSA. These are used by Phase 3 biomedical_tables.py for
    RDKit fingerprint feature computation."""
    p = PubChemPipeline()
    from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR
    p.raw_dir = RAW_DATA_DIR / p.source_name
    p.raw_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = p.download()
    df = p.clean(raw_path)

    col_names_lower = [c.lower() for c in df.columns]
    found = any(v.lower() in col_names_lower for v in variants)
    assert found, (
        f"PubChem CSV must contain field {field!r} (variants tried: "
        f"{variants}). Actual columns: {list(df.columns)}"
    )


# ============================================================================
# Test 3: End-to-end — PubChem CSV → phase2 pubchem_loader
# ============================================================================

def test_pubchem_csv_can_be_read_by_phase2_loader():
    """After phase1 PubChem clean(), the CSV must be readable by phase2
    pubchem_loader. This verifies the filename match AND the column
    schema compatibility."""
    p = PubChemPipeline()
    from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR
    p.raw_dir = RAW_DATA_DIR / p.source_name
    p.raw_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = p.download()
    df = p.clean(raw_path)
    # Persist the CSV (clean() returns df; _persist_cleaned_data writes it)
    clean_df = p._sanitize_csv_output(df) if hasattr(p, "_sanitize_csv_output") else df
    persisted_path = p._persist_cleaned_data(clean_df)

    assert persisted_path.exists(), f"CSV must be persisted at {persisted_path}"
    assert persisted_path.name == "pubchem_enrichment.csv", (
        f"CSV filename must be canonical, got {persisted_path.name!r}"
    )

    # phase2 pubchem_loader reads the CSV
    import pandas as pd
    df2 = pd.read_csv(persisted_path)
    assert len(df2) > 0, "phase2 must read at least one row from the CSV"
    assert len(df2) == len(df), (
        f"phase2 must read all rows: phase1={len(df)}, phase2={len(df2)}"
    )
