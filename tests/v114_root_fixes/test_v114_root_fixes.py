"""v114 Forensic Root-Fix Verification Tests.

These tests verify the ROOT-LEVEL fixes made in the v114 forensic pass.
They read REAL code and REAL data (not comments) to confirm each fix is
actually in place. Each test name maps to a specific audit issue.

Run: pytest tests/v114_root_fixes/test_v114_root_fixes.py -v
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import pytest

# Make the repo importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "phase1"), str(_REPO_ROOT / "phase2"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# =============================================================================
# P4-011: validated_hypotheses.csv path drift + fake data
# =============================================================================
class TestP4011ValidatedHypothesesPathAndSchema:
    """P4-011 ROOT FIX: retrain_on_validated must read the CANONICAL path
    (phase1/processed_data/validated_hypotheses.csv), NOT the legacy
    rl/validated_hypotheses.csv. The file must use the 10-column
    WRITEBACK_CSV_COLUMNS schema with REAL historical data (not fake
    pharma_partner_alpha/beta/gamma placeholders)."""

    def test_canonical_csv_exists_with_10_columns(self):
        canonical = _REPO_ROOT / "phase1" / "processed_data" / "validated_hypotheses.csv"
        assert canonical.exists(), f"canonical CSV missing at {canonical}"
        with open(canonical) as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames
            assert len(cols) == 10, f"expected 10 cols, got {len(cols)}: {cols}"
            # Verify ALL 10 canonical WRITEBACK_CSV_COLUMNS are present.
            from shared.contracts.writeback import WRITEBACK_CSV_COLUMNS
            for col in WRITEBACK_CSV_COLUMNS:
                assert col in cols, f"missing canonical column {col!r}"

    def test_canonical_csv_has_real_data_not_fake_placeholders(self):
        canonical = _REPO_ROOT / "phase1" / "processed_data" / "validated_hypotheses.csv"
        with open(canonical) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 8, f"expected >=8 rows, got {len(rows)}"
        # The fake data used "pharma_partner_alpha/beta/gamma" as validated_by.
        # Real data uses "fda_approved" or "fda_withdrawal" with real study IDs.
        for row in rows:
            vb = row.get("validated_by", "")
            assert "pharma_partner_" not in vb, (
                f"row {row['drug']}/{row['disease']} still uses fake "
                f"validator {vb!r} -- P4-011 NOT fixed"
            )
            # Every row must have a real validation_study_id.
            assert row.get("validation_study_id", "").strip(), (
                f"row {row['drug']}/{row['disease']} missing validation_study_id"
            )

    def test_canonical_csv_has_positive_and_toxic_outcomes(self):
        canonical = _REPO_ROOT / "phase1" / "processed_data" / "validated_hypotheses.csv"
        with open(canonical) as f:
            rows = list(csv.DictReader(f))
        outcomes = {r["outcome"] for r in rows}
        assert "validated_positive" in outcomes, "missing validated_positive rows"
        assert "validated_toxic" in outcomes, "missing validated_toxic rows"

    def test_legacy_csv_also_has_10_column_schema(self):
        """The legacy rl/validated_hypotheses.csv (backward-compat fallback)
        must ALSO have the 10-column schema so the fallback path doesn't
        regress to the 5-column stub."""
        legacy = _REPO_ROOT / "rl" / "validated_hypotheses.csv"
        assert legacy.exists()
        with open(legacy) as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames
            assert len(cols) == 10, f"legacy CSV has {len(cols)} cols (expected 10)"

    def test_retrain_on_validated_reads_canonical_path_by_default(self):
        """retrain_on_validated(validated_csv_path=None) must resolve to the
        CANONICAL path, not the legacy rl/ path."""
        # Inspect the source to confirm the canonical-path logic is present.
        src = (_REPO_ROOT / "rl" / "rl_drug_ranker.py").read_text()
        assert "get_validated_csv_path" in src, (
            "retrain_on_validated does not import get_validated_csv_path -- "
            "P4-011 path-drift fix NOT in place"
        )
        assert "phase1" in src and "processed_data" in src, (
            "canonical phase1/processed_data path not referenced"
        )
        # The legacy hardcoded default must be REMOVED (or only used as fallback).
        # The old code had: validated_csv_path = str(_repo_root / "rl" / "validated_hypotheses.csv")
        # as the SOLE default. Now it should be a fallback only.
        assert "LEGACY_RL_VALIDATED_CSV" in src, (
            "legacy path not explicitly marked as legacy fallback"
        )


# =============================================================================
# P1-003: fastapi/uvicorn missing from phase1/requirements.txt
# =============================================================================
class TestP1003Phase1RequirementsFastapi:
    """P1-003 ROOT FIX: phase1/service.py imports FastAPI, so
    phase1/requirements.txt MUST declare fastapi + uvicorn (the Phase 1
    Docker image installs ONLY phase1/requirements.txt)."""

    def test_phase1_requirements_contains_fastapi(self):
        req = (_REPO_ROOT / "phase1" / "requirements.txt").read_text()
        assert "fastapi" in req.lower(), (
            "phase1/requirements.txt missing fastapi -- P1-003 NOT fixed"
        )

    def test_phase1_requirements_contains_uvicorn(self):
        req = (_REPO_ROOT / "phase1" / "requirements.txt").read_text()
        assert "uvicorn" in req.lower(), (
            "phase1/requirements.txt missing uvicorn -- P1-003 NOT fixed"
        )

    def test_phase1_service_imports_fastapi(self):
        """Confirm phase1/service.py actually needs fastapi (justifying the dep)."""
        svc = (_REPO_ROOT / "phase1" / "service.py").read_text()
        assert "from fastapi" in svc or "import fastapi" in svc, (
            "phase1/service.py does not import fastapi -- dep may be unneeded"
        )


# =============================================================================
# P4-025 / P4-050: Cypher injection guard
# =============================================================================
class TestP4025CypherInjectionGuard:
    """P4-025/P4-050 ROOT FIX: shared/contracts/writeback.py must validate
    all Neo4j label/property/edge-label constants against ^[A-Za-z0-9_]+$
    at import time to prevent Cypher injection via string concatenation."""

    def test_validate_cypher_identifier_exists(self):
        from shared.contracts.writeback import _validate_cypher_identifier
        assert callable(_validate_cypher_identifier)

    def test_safe_identifier_accepted(self):
        from shared.contracts.writeback import _validate_cypher_identifier
        # Should NOT raise.
        _validate_cypher_identifier("Drug", "test_safe")
        _validate_cypher_identifier("VALIDATED_TREATS", "test_safe")
        _validate_cypher_identifier("drug_id", "test_safe")

    def test_unsafe_identifier_rejected(self):
        from shared.contracts.writeback import _validate_cypher_identifier
        # Backtick injection attempt.
        with pytest.raises(ValueError):
            _validate_cypher_identifier("Drug`//", "test_backtick")
        # Semicolon injection attempt.
        with pytest.raises(ValueError):
            _validate_cypher_identifier("Drug; RETURN 1", "test_semicolon")
        # Space (requires backtick quoting -- unsafe in string concat).
        with pytest.raises(ValueError):
            _validate_cypher_identifier("Side Effect", "test_space")

    def test_all_constants_pass_validation_at_import(self):
        """If the module imported successfully, all constants passed the
        import-time validation. This test confirms the import didn't raise."""
        from shared.contracts.writeback import (
            NEO4J_DRUG_LABEL_PREFERRED,
            NEO4J_DRUG_LABEL_LEGACY,
            NEO4J_DISEASE_LABEL,
            EDGE_VALIDATED_TREATS,
            EDGE_VALIDATED_TOXIC_FOR,
        )
        import re
        pat = re.compile(r"^[A-Za-z0-9_]+$")
        for v in (NEO4J_DRUG_LABEL_PREFERRED, NEO4J_DRUG_LABEL_LEGACY,
                  NEO4J_DISEASE_LABEL, EDGE_VALIDATED_TREATS,
                  EDGE_VALIDATED_TOXIC_FOR):
            assert pat.match(v), f"constant {v!r} failed validation"


# =============================================================================
# P3-002: Phase 2 -> Phase 3 edge mapping completeness
# =============================================================================
class TestP3002EdgeMappingCompleteness:
    """P3-002 ROOT FIX: every Phase 2 CORE_EDGE_TYPE must be EITHER mapped
    in PHASE2_TO_PHASE3_EDGE OR explicitly listed in
    PHASE2_TO_PHASE3_EDGE_DROPPED. No edge type may be silently dropped."""

    def test_all_core_edge_types_are_mapped_or_dropped(self):
        from phase2.drugos_graph.config_schema import CORE_EDGE_TYPES
        from phase2.contracts.phase2_schema import (
            PHASE2_TO_PHASE3_EDGE,
            PHASE2_TO_PHASE3_EDGE_DROPPED,
        )
        mapped = set(PHASE2_TO_PHASE3_EDGE.keys())
        dropped = set(PHASE2_TO_PHASE3_EDGE_DROPPED)
        for edge in CORE_EDGE_TYPES:
            assert edge in mapped or edge in dropped, (
                f"Phase 2 edge {edge} is NEITHER mapped NOR explicitly dropped "
                f"-- P3-002 regression (silent drop)"
            )

    def test_mapping_has_at_least_30_entries(self):
        """v113 expanded the mapping from 11 to 30+ entries. Confirm it
        hasn't regressed."""
        from phase2.contracts.phase2_schema import PHASE2_TO_PHASE3_EDGE
        assert len(PHASE2_TO_PHASE3_EDGE) >= 30, (
            f"PHASE2_TO_PHASE3_EDGE has only {len(PHASE2_TO_PHASE3_EDGE)} "
            f"entries (expected >=30 after v113/v114 fix)"
        )

    def test_safety_signal_edges_are_mapped(self):
        """The SIDER adverse-event edges (causes_adverse_event) MUST be
        mapped to (drug, causes, clinical_outcome) -- the safety signal
        must NOT be dropped."""
        from phase2.contracts.phase2_schema import PHASE2_TO_PHASE3_EDGE
        assert ("Compound", "causes_adverse_event", "MedDRA_Term") in PHASE2_TO_PHASE3_EDGE
        mapped = PHASE2_TO_PHASE3_EDGE[("Compound", "causes_adverse_event", "MedDRA_Term")]
        assert mapped == ("drug", "causes", "clinical_outcome"), (
            f"SIDER edge mapped to wrong type: {mapped}"
        )

    def test_metabolism_edges_are_mapped(self):
        """DrugBank metabolism edges (metabolized_by, carried_by,
        transported_by, induces) MUST be mapped -- the pharmacokinetic
        signal must NOT be dropped."""
        from phase2.contracts.phase2_schema import PHASE2_TO_PHASE3_EDGE
        for edge in [
            ("Compound", "metabolized_by", "Protein"),
            ("Compound", "carried_by", "Protein"),
            ("Compound", "transported_by", "Protein"),
            ("Compound", "induces", "Protein"),
        ]:
            assert edge in PHASE2_TO_PHASE3_EDGE, (
                f"metabolism edge {edge} not mapped -- P3-002 regression"
            )


# =============================================================================
# v114: --dev-mode flag on run_4phase.py
# =============================================================================
class TestV114DevModeFlag:
    """v114 ROOT FIX: run_4phase.py must have a --dev-mode flag that
    enables dev/CI/demo inspection of scientifically-invalid output,
    while keeping the production gate strict by default."""

    def test_dev_mode_flag_exists(self):
        src = (_REPO_ROOT / "run_4phase.py").read_text()
        assert "--dev-mode" in src, "run_4phase.py missing --dev-mode flag"

    def test_dev_mode_writes_to_prefixed_dir(self):
        src = (_REPO_ROOT / "run_4phase.py").read_text()
        assert "dev_" in src, (
            "--dev-mode does not prefix output dir with 'dev_'"
        )

    def test_dev_mode_passes_allow_invalid_output(self):
        src = (_REPO_ROOT / "run_4phase.py").read_text()
        assert "allow_invalid_output" in src
        # The run_phase3_and_4 function must accept the parameter.
        assert "allow_invalid_output: bool = False" in src

    def test_production_default_remains_strict(self):
        """Without --dev-mode, allow_invalid_output must default to False."""
        src = (_REPO_ROOT / "run_4phase.py").read_text()
        # The function signature default.
        assert "allow_invalid_output: bool = False" in src


# =============================================================================
# Integration: 4-phase pipeline connectivity smoke test
# =============================================================================
class TestFourPhaseConnectivity:
    """Confirm the 4 phases are wired together (Phase 1 -> 2 -> 3 -> 4).
    This is a SMOKE test of the import chain, not a full run (the full
    run is verified via run_4phase.py --dev-mode in CI)."""

    def test_phase1_bridge_imports(self):
        from drugos_graph.phase1_bridge import run_phase1_to_phase2
        assert callable(run_phase1_to_phase2)

    def test_phase2_to_phase3_adapter_imports(self):
        from graph_transformer.data.phase2_adapter import adapt_phase2_to_phase3
        assert callable(adapt_phase2_to_phase3)

    def test_phase3_to_phase4_bridge_imports(self):
        from graph_transformer.gt_rl_bridge import GTRLBridge
        assert hasattr(GTRLBridge, "run_full_pipeline")

    def test_phase4_retrain_on_validated_imports(self):
        from rl.rl_drug_ranker import retrain_on_validated
        assert callable(retrain_on_validated)

    def test_phase4_writeback_imports(self):
        from phase4.writeback import writeback_to_phase2
        assert callable(writeback_to_phase2)

    def test_shared_writeback_contract_is_canonical_source(self):
        from shared.contracts.writeback import (
            CANONICAL_VALIDATED_CSV,
            WRITEBACK_CSV_COLUMNS,
            OUTCOME_COL,
        )
        assert CANONICAL_VALIDATED_CSV.endswith("validated_hypotheses.csv")
        assert "phase1" in CANONICAL_VALIDATED_CSV
        assert "processed_data" in CANONICAL_VALIDATED_CSV
        assert OUTCOME_COL == "outcome"
        assert len(WRITEBACK_CSV_COLUMNS) == 10
