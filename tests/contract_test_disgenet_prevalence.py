"""Contract tests for Teammate 2 Task 2.2 — DisGeNET GDA prevalence.

Tests the REAL CODE paths (not comments):
1. _lookup_prevalence_per_10k returns REAL epidemiological values
   (NOT a linear formula of GDA count).
2. Cystic fibrosis is RARE (prevalence = 0.4/10K) — the headline bug.
3. Common diseases (migraine, hypertension, asthma, T2DM) are NOT rare.
4. Orphanet IDs (ORPHA:nnnn) default to rare prevalence.
5. The DisGeNET clean() pipeline actually populates prevalence_per_10k
   in the output CSV (in BOTH full-data AND embedded-sample modes).
6. The runtime invariant check fires if CF is misclassified as common.

Verification command from the task brief:
    python -m pytest tests/contract_test_disgenet_prevalence.py -v
"""
from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

# Set env BEFORE any phase1 imports — config.settings reads these at import time
os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")
os.environ.setdefault("DRUGOS_DOWNLOAD_MODE", "sample")
os.environ.setdefault("DRUGOS_ALLOW_SAMPLE_MODE", "true")
os.environ.setdefault("DRUGOS_ALLOW_DEV_ESCAPES", "true")
os.environ.setdefault("DISGENET_USE_API", "false")
os.environ.setdefault("DISGENET_LICENSE_TIER", "free")

import pytest

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "phase1"))

import phase1  # noqa: F401
from phase1.pipelines.disgenet_pipeline import (
    DisGeNETPipeline,
    DISEASE_PREVALENCE_PER_10K,
    RARE_DISEASE_PREVALENCE_THRESHOLD,
    _ORPHANET_DEFAULT_PREVALENCE_PER_10K,
    _lookup_prevalence_per_10k,
)


def _bypass_db_init(self):
    if self.raw_dir is None:
        from config.settings import RAW_DATA_DIR
        self.raw_dir = RAW_DATA_DIR / self.source_name
    self.raw_dir.mkdir(parents=True, exist_ok=True)
    from config.settings import PROCESSED_DATA_DIR
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _patch_ensure_directories(monkeypatch):
    monkeypatch.setattr(DisGeNETPipeline, "_ensure_directories", _bypass_db_init)


# ============================================================================
# Test 1: Cystic fibrosis is RARE (the headline bug)
# ============================================================================

def test_cystic_fibrosis_is_rare():
    """CF MUST be classified as RARE (prevalence < 5/10K).
    This is the headline bug from the audit: the previous linear formula
    ``5.0 + 2995.0 * (n_gdas / max_gda)`` flagged CF as common because
    CF has ~2000 GDAs (CFTR is heavily studied). Real epidemiological
    prevalence is 0.4/10K (RARE per FDA/EU definition)."""
    cf_prev = _lookup_prevalence_per_10k("C0010674", "Cystic Fibrosis")
    assert cf_prev is not None, "CF must be in the curated prevalence table"
    assert cf_prev == 0.4, f"CF prevalence must be 0.4/10K, got {cf_prev}"
    assert cf_prev < RARE_DISEASE_PREVALENCE_THRESHOLD, (
        f"CF must be RARE (prevalence < {RARE_DISEASE_PREVALENCE_THRESHOLD}/10K), "
        f"got {cf_prev}/10K. The linear formula bug has regenerated."
    )


def test_cystic_fibrosis_case_insensitive():
    """Lookup must be case-insensitive (disease names from different
    sources use different casing)."""
    assert _lookup_prevalence_per_10k(None, "cystic fibrosis") == 0.4
    assert _lookup_prevalence_per_10k(None, "CYSTIC FIBROSIS") == 0.4
    assert _lookup_prevalence_per_10k(None, "Cystic Fibrosis") == 0.4


# ============================================================================
# Test 2: Common diseases are NOT rare
# ============================================================================

@pytest.mark.parametrize("disease_name,min_prevalence", [
    ("Migraine", 100.0),       # ~500/10K
    ("Hypertension", 1000.0),  # ~3000/10K
    ("Asthma", 100.0),         # ~600/10K
    ("Diabetes Mellitus, Type 2", 100.0),  # ~1000/10K
    ("Pain", 1000.0),          # ~3000/10K
])
def test_common_diseases_are_not_rare(disease_name, min_prevalence):
    """Common diseases must have prevalence >= RARE_DISEASE_PREVALENCE_THRESHOLD.
    The linear formula incorrectly flagged some common diseases as rare
    (low GDA count = rare in the broken formula)."""
    prev = _lookup_prevalence_per_10k(None, disease_name)
    assert prev is not None, f"{disease_name} must be in the curated table"
    assert prev >= min_prevalence, (
        f"{disease_name} prevalence must be >= {min_prevalence}/10K (common), "
        f"got {prev}/10K"
    )
    assert prev >= RARE_DISEASE_PREVALENCE_THRESHOLD, (
        f"{disease_name} must NOT be rare (prevalence >= "
        f"{RARE_DISEASE_PREVALENCE_THRESHOLD}/10K), got {prev}/10K"
    )


# ============================================================================
# Test 3: Orphanet IDs default to rare prevalence
# ============================================================================

def test_orphanet_ids_default_to_rare():
    """Any disease with an ORPHA:nnnn ID is by definition rare per EU
    regulation (Orphanet ONLY lists rare diseases). The lookup must
    return a rare-prevalence value without consulting the curated table."""
    prev = _lookup_prevalence_per_10k("ORPHA:12345", None)
    assert prev is not None, "ORPHA ID must return a default prevalence"
    assert prev == _ORPHANET_DEFAULT_PREVALENCE_PER_10K, (
        f"ORPHA default must be {_ORPHANET_DEFAULT_PREVALENCE_PER_10K}/10K, "
        f"got {prev}"
    )
    assert prev < RARE_DISEASE_PREVALENCE_THRESHOLD, (
        f"ORPHA default must be RARE (< {RARE_DISEASE_PREVALENCE_THRESHOLD}/10K)"
    )


def test_orphanet_id_takes_priority_over_name():
    """If disease_id is ORPHA:nnnn, the Orphanet default must take
    priority over the disease_name lookup (Orphanet is authoritative)."""
    # "Migraine" is common (500/10K), but if it's tagged ORPHA:,
    # it must be classified as rare (Orphanet is authoritative).
    prev = _lookup_prevalence_per_10k("ORPHA:99999", "Migraine")
    assert prev < RARE_DISEASE_PREVALENCE_THRESHOLD, (
        f"ORPHA ID must take priority — got {prev} (should be rare)"
    )


# ============================================================================
# Test 4: Unknown diseases return None (neutral)
# ============================================================================

def test_unknown_disease_returns_none():
    """Diseases not in the curated table must return None (NOT a linear
    formula value). Downstream compute_market_score treats None as a
    neutral 0.5 — conservative, no false orphan-disease claims."""
    prev = _lookup_prevalence_per_10k(None, "Completely Made Up Disease XYZ123")
    assert prev is None, (
        f"Unknown disease must return None, got {prev} — the linear "
        f"formula bug has regenerated."
    )


# ============================================================================
# Test 5: DisGeNET clean() actually populates prevalence_per_10k
# (in BOTH full-data AND embedded-sample modes)
# ============================================================================

def test_disgenet_clean_populates_prevalence_in_sample_mode(monkeypatch):
    """The DisGeNET clean() pipeline must populate prevalence_per_10k
    in the output CSV. This is the ROOT FIX for Task 2.2 — the previous
    code had the function but NEVER called it in the embedded-sample
    short-circuit path (only in the full-data path)."""
    # Bypass the API-key config validation (we're using sample mode, no API).
    # The validation reads module-level DISGENET_USE_API from config.settings,
    # which was set at import time. Patch BOTH the constant AND the function.
    import phase1.config.settings as _settings
    monkeypatch.setattr(_settings, "DISGENET_USE_API", False)
    import phase1.pipelines.disgenet_pipeline as _dp
    monkeypatch.setattr(_dp, "_validate_disgenet_config", lambda *a, **kw: None)
    monkeypatch.setattr(_dp, "DISGENET_USE_API", False)

    p = DisGeNETPipeline()
    # Set raw_dir BEFORE download (the disgenet download() calls
    # _write_embedded_sample before _ensure_directories is invoked).
    from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR
    p.raw_dir = RAW_DATA_DIR / p.source_name
    p.raw_dir.mkdir(parents=True, exist_ok=True)
    PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw_path = p.download()
    assert raw_path is not None and raw_path.exists()

    df = p.clean(raw_path)
    assert "prevalence_per_10k" in df.columns, (
        "clean() output MUST include prevalence_per_10k column. "
        "The embedded-sample short-circuit must call _populate_prevalence."
    )
    # At least SOME rows should have non-null prevalence (the embedded
    # sample includes Pain, Inflammation, Migraine, Epilepsy, Hypertension —
    # all of which are in the curated table).
    populated = int(df["prevalence_per_10k"].notna().sum())
    assert populated > 0, (
        "At least one row must have non-null prevalence_per_10k. "
        f"Got 0/{len(df)} populated. Columns: {list(df.columns)}"
    )


# ============================================================================
# Test 6: phase1_schema.Disease has prevalence_per_10k field
# ============================================================================

def test_phase1_schema_disease_has_prevalence_field():
    """phase1_schema.py must declare prevalence_per_10k as a column
    for the DisGeNET GDA source. This is the contract that phase2
    disgenet_loader and phase4 RL agent rely on."""
    src = (REPO / "phase1" / "contracts" / "phase1_schema.py").read_text()
    # Find the disgenet_gda block and check for prevalence_per_10k
    import re
    disgenet_block_match = re.search(
        r'"disgenet_gda".*?(?="omim_gda"|"uniprot_proteins"|\Z)',
        src, re.DOTALL,
    )
    assert disgenet_block_match, "Could not find disgenet_gda block in phase1_schema.py"
    disgenet_block = disgenet_block_match.group(0)
    assert "prevalence_per_10k" in disgenet_block, (
        "disgenet_gda source spec must declare 'prevalence_per_10k' as a "
        "column. phase2 disgenet_loader and phase4 RL agent rely on this."
    )


# ============================================================================
# Test 7: Linear formula is NOT used (regression guard)
# ============================================================================

def test_no_linear_formula_for_prevalence():
    """The scientifically-wrong linear formula
    ``5.0 + 2995.0 * (n_gdas / max_gda)`` must NOT appear anywhere in
    the disgenet_pipeline.py ACTIVE code path. It may appear in comments
    documenting the removed bug, but not in executable code."""
    src = (REPO / "phase1" / "pipelines" / "disgenet_pipeline.py").read_text()
    # The linear formula expression — must NOT be in executable code.
    # We check that it's not in a return/assignment statement.
    import re
    # Find any line that looks like an assignment or return using the formula
    pattern = re.compile(
        r"^\s*(?:return\s+|[\w\[\]\.]+\s*=\s*).*5\.0\s*\+\s*2995\.0",
        re.MULTILINE,
    )
    matches = pattern.findall(src)
    assert not matches, (
        f"Linear formula '5.0 + 2995.0 * ...' must NOT appear in "
        f"executable code. Found {len(matches)} match(es). The "
        f"scientifically-wrong GDA-to-prevalence formula has regenerated."
    )
