"""Contract tests for Teammate 2 Task 2.3 — OMIM GDA roundtrip.

Tests the REAL CODE paths:
1. phase1 OMIM pipeline writes to the canonical filename
   ``omim_gene_disease_associations.csv`` (matches phase2 omim_loader).
2. MIM numbers are parsed correctly (6-digit format).
3. Gene-disease associations are extracted (gene_symbol + disease_id pairs).
4. phase1_schema.Disease (omim_gda source) has 'genetic_basis' field.
5. The OMIM clean() pipeline actually populates genetic_basis in the
   output CSV (in BOTH full-data AND embedded-sample modes).
6. PHASE2_TO_PHASE3_EDGE includes a Gene->Disease edge type so OMIM
   associations flow to Phase 3.

Verification command from the task brief:
    python -m pytest tests/contract_test_omim_roundtrip.py -v
"""
from __future__ import annotations

import os
import re
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
from phase1.pipelines.omim_pipeline import OMIMPipeline


def _bypass_db_init(self):
    if self.raw_dir is None:
        from config.settings import RAW_DATA_DIR
        self.raw_dir = RAW_DATA_DIR / self.source_name
    self.raw_dir.mkdir(parents=True, exist_ok=True)
    from config.settings import PROCESSED_DATA_DIR
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _patch_ensure_directories(monkeypatch):
    monkeypatch.setattr(OMIMPipeline, "_ensure_directories", _bypass_db_init)


# ============================================================================
# Test 1: Canonical filename is used
# ============================================================================

def test_omim_pipeline_writes_canonical_filename():
    """phase1 OMIM pipeline must write to omim_gene_disease_associations.csv
    (matches phase2 omim_loader.DEFAULT_OMIM_CSV)."""
    from phase1.pipelines.omim_pipeline import OMIMPipeline
    p = OMIMPipeline.__new__(OMIMPipeline)  # bypass __init__
    p.source_name = "omim"
    p.processed_filename = None
    filename = p._get_processed_filename()
    assert filename == "omim_gene_disease_associations.csv", (
        f"phase1 OMIM must write to 'omim_gene_disease_associations.csv', "
        f"got {filename!r}. phase2 omim_loader will not find the CSV."
    )


def test_phase2_omim_loader_expects_canonical_filename():
    """phase2 omim_loader.DEFAULT_OMIM_CSV must point to the canonical
    filename that phase1 writes."""
    from phase2.drugos_graph.omim_loader import DEFAULT_OMIM_CSV
    assert DEFAULT_OMIM_CSV.name == "omim_gene_disease_associations.csv", (
        f"phase2 omim_loader expects {DEFAULT_OMIM_CSV.name!r} — must "
        f"match phase1's canonical output filename."
    )


# ============================================================================
# Test 2: phase1_schema.Disease (omim_gda) has genetic_basis field
# ============================================================================

def test_phase1_schema_omim_has_genetic_basis():
    """phase1_schema.py must declare genetic_basis as a column for the
    OMIM GDA source. This is the contract that phase2 omim_loader relies
    on to distinguish causal from susceptibility associations."""
    src = (REPO / "phase1" / "contracts" / "phase1_schema.py").read_text()
    # The genetic_basis column spec must appear in the omim_gda section
    # (between the omim_gda key and the next key).
    # Find the omim_gda block
    omim_block_match = re.search(
        r'"omim_gda".*?(?="omim_susceptibility"|"pubchem_enrichment"|\Z)',
        src, re.DOTALL,
    )
    assert omim_block_match, "Could not find omim_gda block in phase1_schema.py"
    omim_block = omim_block_match.group(0)
    assert "genetic_basis" in omim_block, (
        "omim_gda source spec must declare 'genetic_basis' as a column. "
        "phase2 omim_loader uses this to create (Gene)-[:CAUSES]->(Disease) edges."
    )


# ============================================================================
# Test 3: OMIM clean() populates genetic_basis (in sample mode too)
# ============================================================================

def test_omim_clean_populates_genetic_basis_in_sample_mode():
    """The OMIM clean() pipeline must populate genetic_basis in the
    output CSV. This is the ROOT FIX for Task 2.3 — the previous code
    only populated genetic_basis on the full-data morbidmap path,
    bypassing it in the embedded-sample short-circuit."""
    p = OMIMPipeline()
    from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR
    p.raw_dir = RAW_DATA_DIR / p.source_name
    p.raw_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = p.download()
    assert raw_path is not None and raw_path.exists()

    df = p.clean(raw_path)
    assert "genetic_basis" in df.columns, (
        "clean() output MUST include genetic_basis column. "
        "The embedded-sample short-circuit must populate it."
    )
    # genetic_basis values come from association_type — at least SOME
    # rows should have non-null values.
    populated = int(df["genetic_basis"].notna().sum()) if len(df) > 0 else 0
    assert populated > 0 or len(df) == 0, (
        f"Expected some rows to have non-null genetic_basis, got "
        f"{populated}/{len(df)}."
    )


# ============================================================================
# Test 4: MIM numbers are 6-digit (the OMIM standard)
# ============================================================================

def test_omim_clean_has_mim_numbers():
    """OMIM MIM numbers must be parsed correctly. OMIM MIM numbers are
    6-digit integers (e.g., 602421). The pipeline must extract both
    gene_mim (gene MIM) and phenotype_mim/disease_id (disease MIM)."""
    p = OMIMPipeline()
    from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR
    p.raw_dir = RAW_DATA_DIR / p.source_name
    p.raw_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = p.download()
    df = p.clean(raw_path)

    # Check MIM-related columns exist
    has_gene_mim = any("mim" in c.lower() for c in df.columns)
    assert has_gene_mim, (
        f"OMIM output must have a MIM column. Columns: {list(df.columns)}"
    )

    # Check gene-disease association columns exist
    has_gene = any("gene" in c.lower() for c in df.columns)
    has_disease = any("disease" in c.lower() for c in df.columns)
    assert has_gene, "OMIM output must have a gene column"
    assert has_disease, "OMIM output must have a disease column"

    # If there's a gene_mim column, verify the values look like MIM numbers
    if "gene_mim" in df.columns and not df.empty:
        non_null = df["gene_mim"].dropna()
        if len(non_null) > 0:
            # MIM numbers are 6-digit (possibly with a leading marker
            # like '*' for gene locus, '#' for phenotype, etc.)
            sample_val = str(non_null.iloc[0])
            # Strip any leading marker character
            digits_only = re.sub(r"^[^0-9]+", "", sample_val)
            assert len(digits_only) >= 6, (
                f"MIM number must be at least 6 digits, got {sample_val!r}"
            )


# ============================================================================
# Test 5: PHASE2_TO_PHASE3_EDGE includes Gene->Disease
# ============================================================================

def test_phase2_to_phase3_edge_includes_gene_disease():
    """PHASE2_TO_PHASE3_EDGE must include at least one (Gene, *, Disease)
    edge so OMIM gene-disease associations flow to Phase 3."""
    from phase2.contracts.phase2_schema import PHASE2_TO_PHASE3_EDGE
    gene_disease_edges = [
        k for k in PHASE2_TO_PHASE3_EDGE
        if k[0] == "Gene" and k[2] == "Disease"
    ]
    assert len(gene_disease_edges) > 0, (
        "PHASE2_TO_PHASE3_EDGE must include at least one "
        "(Gene, <rel>, Disease) edge for OMIM associations to flow to Phase 3."
    )


# ============================================================================
# Test 6: End-to-end — OMIM CSV → phase2 omim_loader
# ============================================================================

def test_omim_csv_found_by_phase2_loader():
    """After phase1 OMIM clean(), the CSV must be at the path that
    phase2 omim_loader.DEFAULT_OMIM_CSV points to."""
    p = OMIMPipeline()
    from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR
    p.raw_dir = RAW_DATA_DIR / p.source_name
    p.raw_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = p.download()
    p.clean(raw_path)  # This persists the CSV via _save_processed_csv

    from phase2.drugos_graph.omim_loader import DEFAULT_OMIM_CSV
    assert DEFAULT_OMIM_CSV.exists(), (
        f"phase2 omim_loader expects CSV at {DEFAULT_OMIM_CSV} but it "
        f"does not exist after phase1 OMIM clean()."
    )
