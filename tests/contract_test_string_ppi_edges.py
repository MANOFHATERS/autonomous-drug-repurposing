"""Contract tests for Teammate 2 Task 2.1 — STRING PPI pipeline.

Tests the REAL CODE paths (not comments, not aspirational fixes):
1. phase1 STRING pipeline writes to the canonical filename
   ``string_protein_protein_interactions.csv`` (NOT the legacy alias).
2. phase1 STRING output includes ``uniprot_ac_a`` / ``uniprot_ac_b`` columns
   (so phase2 StringLoader can read them).
3. phase2 StringLoader (DEFAULT_STRING_PPI_CSV) expects the same canonical
   filename.
4. PHASE2_TO_PHASE3_EDGE includes ('Protein', 'interacts_with', 'Protein').
5. End-to-end: phase1 CSV → phase2 StringLoader → KG edges.

Verification command from the task brief:
    python -m phase1.pipelines string && wc -l phase1/processed_data/protein_protein_interactions.csv

NOTE: The full CLI command requires a working DB (the SQLite migration 001
has an unrelated syntax bug owned by another teammate). These tests bypass
the DB init to verify the STRING clean() + persist + phase2-consumption
logic directly.
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

# Set dev/sample mode env BEFORE importing phase1
os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")
os.environ.setdefault("DRUGOS_DOWNLOAD_MODE", "sample")
os.environ.setdefault("DRUGOS_ALLOW_SAMPLE_MODE", "true")
os.environ.setdefault("DRUGOS_ALLOW_DEV_ESCAPES", "true")

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "phase1"))

import phase1  # noqa: F401  (bootstraps sys.path for bare imports)
from phase1.pipelines.string_pipeline import StringPipeline, EXPECTED_OUTPUT_COLUMNS


def _bypass_db_init(self):
    """Skip DB init — verify download+clean+persist only."""
    if self.raw_dir is None:
        from config.settings import RAW_DATA_DIR
        self.raw_dir = RAW_DATA_DIR / self.source_name
    self.raw_dir.mkdir(parents=True, exist_ok=True)
    from config.settings import PROCESSED_DATA_DIR
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _patch_ensure_directories(monkeypatch):
    """Bypass DB init for all tests (migration 001 SQLite bug is not ours)."""
    monkeypatch.setattr(StringPipeline, "_ensure_directories", _bypass_db_init)


# ============================================================================
# Test 1: Canonical filename is used (NOT the legacy alias)
# ============================================================================

def test_string_pipeline_writes_canonical_filename():
    """phase1 base_pipeline must use the canonical filename declared in
    phase1_schema.py, NOT the legacy alias. Phase 2 StringLoader looks up
    the canonical name and will FileNotFoundError on the alias."""
    from phase1.pipelines.string_pipeline import StringPipeline
    p = StringPipeline.__new__(StringPipeline)  # bypass __init__
    p.source_name = "string"
    p.processed_filename = None
    filename = p._get_processed_filename()
    assert filename == "string_protein_protein_interactions.csv", (
        f"Task 2.1 REGRESSION: phase1 writes {filename!r} but phase2 "
        f"StringLoader expects 'string_protein_protein_interactions.csv'. "
        f"The legacy alias 'protein_protein_interactions.csv' must NOT "
        f"be used — phase2 cannot find it."
    )


def test_phase2_string_loader_expects_canonical_filename():
    """phase2 StringLoader.DEFAULT_STRING_PPI_CSV must point to the
    canonical filename that phase1 writes."""
    from phase2.drugos_graph.string_loader import DEFAULT_STRING_PPI_CSV
    assert DEFAULT_STRING_PPI_CSV.name == "string_protein_protein_interactions.csv", (
        f"phase2 StringLoader expects {DEFAULT_STRING_PPI_CSV.name!r} — "
        f"must match phase1's canonical output filename."
    )


# ============================================================================
# Test 2: phase1 STRING output includes uniprot_ac_a / uniprot_ac_b columns
# ============================================================================

def test_string_output_includes_uniprot_ac_columns():
    """phase1 STRING output MUST include ``uniprot_ac_a`` / ``uniprot_ac_b``
    as alias columns. Phase 2 StringLoader.string_to_edge_records_from_phase1
    reads ONLY these names (with protein_a/b fallback) — it does NOT check
    uniprot_id_a/b. Without these columns, phase2 produces ZERO edges."""
    assert "uniprot_ac_a" in EXPECTED_OUTPUT_COLUMNS, (
        "EXPECTED_OUTPUT_COLUMNS must include 'uniprot_ac_a' so phase2 "
        "StringLoader can read UniProt accessions."
    )
    assert "uniprot_ac_b" in EXPECTED_OUTPUT_COLUMNS, (
        "EXPECTED_OUTPUT_COLUMNS must include 'uniprot_ac_b' so phase2 "
        "StringLoader can read UniProt accessions."
    )


# ============================================================================
# Test 3: PHASE2_TO_PHASE3_EDGE includes PPI
# ============================================================================

def test_phase2_to_phase3_edge_includes_ppi():
    """PHASE2_TO_PHASE3_EDGE must include ('Protein', 'interacts_with',
    'Protein') so PPI edges flow from Phase 2 to Phase 3. It must NOT be
    in PHASE2_TO_PHASE3_EDGE_DROPPED."""
    from phase2.contracts.phase2_schema import (
        PHASE2_TO_PHASE3_EDGE,
        PHASE2_TO_PHASE3_EDGE_DROPPED,
    )
    ppi_key = ("Protein", "interacts_with", "Protein")
    assert ppi_key in PHASE2_TO_PHASE3_EDGE, (
        f"PPI edge {ppi_key} must be in PHASE2_TO_PHASE3_EDGE so it "
        f"flows to Phase 3. Found keys: {len(PHASE2_TO_PHASE3_EDGE)}."
    )
    assert ppi_key not in PHASE2_TO_PHASE3_EDGE_DROPPED, (
        f"PPI edge {ppi_key} must NOT be in PHASE2_TO_PHASE3_EDGE_DROPPED."
    )
    # Verify it maps to the lowercase Phase 3 form
    assert PHASE2_TO_PHASE3_EDGE[ppi_key] == ("protein", "interacts_with", "protein"), (
        f"PPI edge must map to ('protein', 'interacts_with', 'protein') "
        f"for Phase 3, got {PHASE2_TO_PHASE3_EDGE[ppi_key]!r}."
    )


# ============================================================================
# Test 4: End-to-end — phase1 CSV → phase2 StringLoader → KG edges
# ============================================================================

def test_end_to_end_string_pipeline_produces_kg_edges():
    """Run the REAL phase1 STRING pipeline (sample mode), write the CSV,
    then verify phase2 StringLoader can read it and produce
    (Protein, interacts_with, Protein) KG edges."""
    p = StringPipeline()
    raw_path = p.download()
    assert raw_path is not None and raw_path.exists(), "download() must return an existing path"

    df = p.clean(raw_path)
    assert not df.empty, "clean() must produce non-empty DataFrame in sample mode"
    assert "uniprot_ac_a" in df.columns, "clean() output must have uniprot_ac_a"
    assert "uniprot_ac_b" in df.columns, "clean() output must have uniprot_ac_b"

    # Persist the CSV (this is what phase2 reads)
    clean_df = p._sanitize_csv_output(df)
    csv_path = p._persist_cleaned_data(clean_df)
    assert csv_path.exists(), f"CSV must be persisted at {csv_path}"
    assert csv_path.name == "string_protein_protein_interactions.csv", (
        f"CSV filename must be canonical, got {csv_path.name!r}"
    )

    # phase2 StringLoader reads the CSV
    from phase2.drugos_graph.string_loader import (
        parse_string_ppi_from_phase1_csv,
        string_to_edge_records_from_phase1,
    )
    df2 = parse_string_ppi_from_phase1_csv()
    assert len(df2) == len(df), (
        f"phase2 must read all rows: phase1={len(df)}, phase2={len(df2)}"
    )

    edges = string_to_edge_records_from_phase1(df2)
    assert len(edges) > 0, "phase2 must produce KG edges from phase1 CSV"

    # Verify edge structure
    first = edges[0]
    assert first["src_type"] == "Protein", f"src_type must be Protein, got {first['src_type']!r}"
    assert first["dst_type"] == "Protein", f"dst_type must be Protein, got {first['dst_type']!r}"
    assert first["rel_type"] == "interacts_with", (
        f"rel_type must be 'interacts_with', got {first['rel_type']!r}"
    )
    assert first["src_id"] != "", "src_id (UniProt accession) must not be empty"
    assert first["dst_id"] != "", "dst_id (UniProt accession) must not be empty"
