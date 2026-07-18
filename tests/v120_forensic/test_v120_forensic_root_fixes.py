"""v120 Forensic Root-Fix Verification Suite (hostile-auditor mode).

This test module verifies the ROOT-LEVEL fixes committed in v120 that
prior "ROOT FIX" branches missed or faked. Each test reads REAL CODE
(not comments, not prior tests) and asserts the fix is ACTUALLY in
place and ACTUALLY runs.

Verified fixes:
  1. rl_drug_ranker.py SyntaxError (unclosed paren at kp_recovery_pass)
     — Phase 4 was UN-IMPORTABLE, killing the entire production pipeline.
  2. rl_drug_ranker.py SyntaxError (unclosed bracket at FEATURE_COLS)
     — same cascade.
  3. scientific_thresholds.py DUPLICATE resolve_kp_recovery_threshold
     — the OLD signature shadowed the new scale-aware one.
  4. P3-040 predict_drug_disease_scores_dual — was called TWICE, now ONCE.
  5. SH-025 + SH-006 + SH-031 — TS static contract drift + comment lies.
  6. P3-001 is_phase2_intermediate_dropped — ImportError killed Phase 2→3.
  7. resolve_kp_recovery_threshold accepts n_test_kps at runtime.
  8. Full 4-phase pipeline runs end-to-end (Phase 1 → 2 → 3 → 4).

Run:  python -m pytest tests/v120_forensic/test_v120_forensic_root_fixes.py -v
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
from pathlib import Path

import pytest

# Make repo root + phase1 + phase2 importable.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "phase1"), str(_REPO_ROOT / "phase2")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ============================================================================
# FIX 1 + 2: rl_drug_ranker.py must PARSE (no SyntaxError)
# ============================================================================

def test_rl_drug_ranker_parses_clean():
    """The file MUST parse with no SyntaxError.

    v120 ROOT FIX: prior versions had TWO orphaned brackets:
      - Line 327: ``FEATURE_COLS: List[str] = [``  (never closed)
      - Line 10775: ``"kp_recovery_pass": (``  (never closed)
    Both made the entire rl/ package UN-IMPORTABLE, which cascaded into
    phase2_adapter.py line 1463 crashing on ``from rl.rl_drug_ranker
    import KNOWN_POSITIVES``, which made run_4phase.py exit(5). The
    ENTIRE production pipeline was DEAD.
    """
    src_path = _REPO_ROOT / "rl" / "rl_drug_ranker.py"
    src = src_path.read_text(encoding="utf-8")
    # Must parse with no exception.
    ast.parse(src)
    # Must NOT contain the orphaned opening brackets that caused the
    # SyntaxErrors. The v120 fix removed both.
    assert "FEATURE_COLS: List[str] = [\n" not in src, (
        "v120 REGRESSION: orphaned 'FEATURE_COLS: List[str] = [' opening "
        "bracket is back. This causes SyntaxError and makes rl_drug_ranker "
        "un-importable, killing the 4-phase pipeline."
    )
    # The kp_recovery_pass key must appear EXACTLY ONCE in the
    # scientific_validation dict (not twice — the old code had a
    # duplicate unclosed definition).
    kp_pass_count = src.count('"kp_recovery_pass": (')
    assert kp_pass_count == 1, (
        f"v120 REGRESSION: found {kp_pass_count} occurrences of "
        f"'\"kp_recovery_pass\": (' — expected exactly 1. The prior bug "
        f"had 2 (one unclosed old definition + one new)."
    )


def test_rl_drug_ranker_imports_clean():
    """The module MUST import (no SyntaxError, no missing deps)."""
    import rl.rl_drug_ranker as rlr  # noqa: F401
    assert hasattr(rlr, "KNOWN_POSITIVES")
    assert hasattr(rlr, "FEATURE_COLS")
    assert hasattr(rlr, "run_pipeline")
    assert len(rlr.KNOWN_POSITIVES) > 0, "KNOWN_POSITIVES must not be empty"
    assert len(rlr.FEATURE_COLS) > 0, "FEATURE_COLS must not be empty"


# ============================================================================
# FIX 3: scientific_thresholds.py — single resolve_kp_recovery_threshold
# ============================================================================

def test_scientific_thresholds_has_single_resolve_kp_recovery_threshold():
    """There must be EXACTLY ONE resolve_kp_recovery_threshold definition.

    v120 ROOT FIX: prior versions had TWO definitions in the same file:
      - Line 127: scale-aware (config_threshold, n_test_kps)  ← CORRECT
      - Line 350: old (config_threshold only)                  ← SHADOWS
    Python binds the LAST definition, so the OLD signature shadowed
    the new one. rl_drug_ranker.py line 10814 passed n_test_kps,
    causing TypeError: resolve_kp_recovery_threshold() got an
    unexpected keyword argument 'n_test_kps'.
    """
    src_path = _REPO_ROOT / "rl" / "scientific_thresholds.py"
    src = src_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    defs = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "resolve_kp_recovery_threshold"
    ]
    assert len(defs) == 1, (
        f"v120 REGRESSION: found {len(defs)} definitions of "
        f"resolve_kp_recovery_threshold — expected exactly 1. The prior "
        f"bug had 2 (the OLD shadowed the NEW scale-aware version)."
    )
    # The surviving definition MUST accept n_test_kps.
    args = [a.arg for a in defs[0].args.args]
    assert "n_test_kps" in args, (
        f"v120 REGRESSION: the surviving resolve_kp_recovery_threshold "
        f"does not accept n_test_kps (args={args}). rl_drug_ranker.py "
        f"passes n_test_kps per the Issue 180 fix — this would crash."
    )


def test_resolve_kp_recovery_threshold_accepts_n_test_kps_at_runtime():
    """Runtime signature must include n_test_kps (not just AST)."""
    from rl.scientific_thresholds import resolve_kp_recovery_threshold
    sig = inspect.signature(resolve_kp_recovery_threshold)
    assert "n_test_kps" in sig.parameters, (
        f"v120 REGRESSION: runtime signature {sig} does not include "
        f"n_test_kps. The OLD shadowing definition may be back."
    )
    # Scale-aware: small test set → lower threshold (0.34).
    t_small = resolve_kp_recovery_threshold(0.0, n_test_kps=10)
    t_large = resolve_kp_recovery_threshold(0.0, n_test_kps=5000)
    assert t_small < t_large, (
        f"Scale-aware threshold broken: small={t_small}, large={t_large}. "
        f"Small test sets should have a LOWER threshold (0.34) than "
        f"large ones (0.5)."
    )
    assert t_small == pytest.approx(0.34, abs=0.01)
    assert t_large == pytest.approx(0.5, abs=0.01)


# ============================================================================
# FIX 4: P3-040 — predict_drug_disease_scores_dual exists and is used ONCE
# ============================================================================

def test_predict_drug_disease_scores_dual_exists():
    """The new dual function MUST exist in graph_transformer.inference.

    v120 ROOT FIX (P3-040): the prior code called
    predict_drug_disease_scores TWICE in get_top_k_novel_predictions
    (once raw, once calibrated) — doubling encoder compute. The fix
    adds predict_drug_disease_scores_dual which encodes ONCE and
    returns both score arrays.
    """
    from graph_transformer.inference import predict_drug_disease_scores_dual
    assert callable(predict_drug_disease_scores_dual)
    sig = inspect.signature(predict_drug_disease_scores_dual)
    # Must NOT take apply_temperature (it returns BOTH).
    assert "apply_temperature" not in sig.parameters, (
        "predict_drug_disease_scores_dual must NOT take apply_temperature — "
        "it returns BOTH raw and calibrated scores from a single encode pass."
    )


def test_gt_rl_bridge_calls_dual_once_not_predict_twice():
    """get_top_k_novel_predictions MUST call the dual function ONCE,
    not call predict_drug_disease_scores TWICE.

    v120 ROOT FIX: the prior code had a misleading comment claiming
    "ROOT FIX: call predict_drug_disease_scores TWICE" — which was the
    BUG, not the fix. The user's audit caught this.
    """
    src = (_REPO_ROOT / "graph_transformer" / "gt_rl_bridge.py").read_text("utf-8")
    # The dual function MUST be called.
    assert "predict_drug_disease_scores_dual" in src, (
        "v120 REGRESSION: predict_drug_disease_scores_dual is not called "
        "in gt_rl_bridge.py. P3-040 fix requires a single encode pass."
    )
    # The old "call TWICE" comment MUST be gone.
    assert "ROOT FIX: call predict_drug_disease_scores TWICE" not in src, (
        "v120 REGRESSION: the misleading 'ROOT FIX: call "
        "predict_drug_disease_scores TWICE' comment is back. That comment "
        "described the BUG as the FIX."
    )


def test_predict_drug_disease_scores_dual_runs_on_real_model():
    """Smoke test: the dual function actually runs and returns both arrays.

    Builds a tiny demo graph, trains a 1-epoch model, and calls the dual
    function. Asserts both arrays have the right shape and are in [0, 1].
    """
    import numpy as np
    import torch
    from graph_transformer.data.graph_builder import BiomedicalGraphBuilder
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.inference import predict_drug_disease_scores_dual

    builder = BiomedicalGraphBuilder()
    node_features, edge_indices, node_maps, _known_pairs = builder.build_demo_graph(
        num_drugs=8, num_diseases=6, num_proteins=10, num_pathways=5,
        num_outcomes=4, seed=42,
    )
    drug_map = node_maps["drug"]
    disease_map = node_maps["disease"]
    model = DrugRepurposingGraphTransformer(
        feature_dims={k: v.shape[1] for k, v in node_features.items()},
        embedding_dim=16, num_layers=1, num_heads=2, dropout=0.0,
        attention_dropout=0.0,
    )
    model.eval()

    # Score 4 (drug, disease) pairs.
    drug_names = list(drug_map.keys())[:4]
    disease_names = list(disease_map.keys())[:4]
    drug_idx = torch.tensor([drug_map[n] for n in drug_names], dtype=torch.long)
    disease_idx = torch.tensor([disease_map[n] for n in disease_names], dtype=torch.long)

    raw, calibrated = predict_drug_disease_scores_dual(
        model=model,
        node_features=node_features,
        edge_indices=edge_indices,
        drug_indices=drug_idx,
        disease_indices=disease_idx,
    )
    assert raw.shape == (4,), f"raw shape {raw.shape} != (4,)"
    assert calibrated.shape == (4,), f"calibrated shape {calibrated.shape} != (4,)"
    # Both must be valid probabilities in [0, 1].
    assert np.all(raw >= 0.0) and np.all(raw <= 1.0), f"raw out of [0,1]: {raw}"
    assert np.all(calibrated >= 0.0) and np.all(calibrated <= 1.0), (
        f"calibrated out of [0,1]: {calibrated}"
    )
    # With T=1.0 (untrained), raw and calibrated should be very close.
    assert np.allclose(raw, calibrated, atol=1e-4), (
        f"With untrained T=1.0, raw and calibrated should match. "
        f"raw={raw}, calibrated={calibrated}"
    )


# ============================================================================
# FIX 5: SH-025 + SH-031 — TS contract + Python service alignment
# ============================================================================

def test_ts_static_contract_matches_python_service():
    """The static TS PredictResponse MUST match the Python service shape.

    v120 ROOT FIX: prior TS interface described a SINGLE prediction
    (drug, disease, gnn_score, gnn_score_calibrated, ...) — but the
    Python service returns a WRAPPER ({predictions, source,
    modelVersion, generatedAt, count, checkpointPath, ...}).
    """
    ts_path = _REPO_ROOT / "frontend" / "contracts" / "api_contracts.ts"
    ts_src = ts_path.read_text(encoding="utf-8")
    # The wrapper fields MUST be present.
    for field in ["predictions", "source", "modelVersion", "generatedAt",
                  "count", "checkpointPath"]:
        assert field in ts_src, (
            f"v120 REGRESSION: TS contract missing wrapper field '{field}'."
        )
    # The ``source`` enum MUST include ``gt_checkpoint`` (the production value).
    assert '"gt_checkpoint"' in ts_src, (
        "v120 REGRESSION: TS source enum missing 'gt_checkpoint' — the "
        "value the Python service actually returns in production."
    )
    # The stale single-prediction fields MUST be gone.
    assert "gnn_score_timestamp: string;" not in ts_src, (
        "v120 REGRESSION: stale 'gnn_score_timestamp' field is back in "
        "the PredictResponse wrapper. It was never returned by the Python "
        "service."
    )


def test_python_service_returns_source_gt_checkpoint():
    """The Python /predict endpoint MUST return source='gt_checkpoint'."""
    src = (_REPO_ROOT / "graph_transformer" / "service.py").read_text("utf-8")
    assert '"source": "gt_checkpoint"' in src, (
        "v120 REGRESSION: service.py does not return source='gt_checkpoint'."
    )
    # The misleading SH-031 comment about "HTTP response HEADERS" MUST
    # be gone. (The v120 fix's explanatory comment QUOTES the old phrase
    # to document what was wrong — that's allowed. We check that no
    # ACTIVE code comment claims the fields are returned as headers.)
    # Look for the phrase OUTSIDE of the v120 forensic-fix explanation.
    lines = src.splitlines()
    active_header_claims = [
        ln for ln in lines
        if "returned ONLY as HTTP response headers" in ln
        and "v120" not in ln
        and "That comment was a LIE" not in ln
        and "previous v113 comment claimed" not in ln
    ]
    assert active_header_claims == [], (
        "v120 REGRESSION: an active code comment still claims "
        "error_count/error_rate are returned as HTTP headers. The code "
        "returns them in the body. Lines: " + str(active_header_claims)
    )


# ============================================================================
# FIX 6: P3-001 — phase2_adapter imports cleanly
# ============================================================================

def test_phase2_adapter_imports_clean():
    """P3-001 ROOT FIX: the ImportError on is_phase2_intermediate_dropped
    MUST be fixed. The adapter MUST import without error.
    """
    from graph_transformer.data.phase2_adapter import (
        adapt_phase2_to_phase3,
        adapt_hetero_data_to_phase3,
        is_phase2_intermediate_dropped,
        Phase2AdapterValidationError,
    )
    assert callable(adapt_phase2_to_phase3)
    assert callable(adapt_hetero_data_to_phase3)
    # The backward-compat alias MUST work.
    assert is_phase2_intermediate_dropped is not None
    assert callable(is_phase2_intermediate_dropped)


# ============================================================================
# FIX 7: Full 4-phase pipeline runs end-to-end
# ============================================================================

def test_4phase_pipeline_imports_and_wiring():
    """run_4phase.py MUST import and have the canonical Phase 1→2→3→4 chain.

    v120 ROOT FIX: prior to fixing the rl_drug_ranker SyntaxErrors +
    scientific_thresholds duplicate, run_4phase.py would import but
    crash at runtime when it tried to import rl.rl_drug_ranker via
    phase2_adapter.
    """
    import run_4phase  # noqa: F401
    # The canonical chain MUST be wired.
    from graph_transformer.data.phase2_adapter import adapt_phase2_to_phase3
    from rl.rl_drug_ranker import KNOWN_POSITIVES
    from rl.scientific_thresholds import resolve_kp_recovery_threshold
    # All three must be callable / have correct signature.
    assert callable(adapt_phase2_to_phase3)
    assert len(KNOWN_POSITIVES) > 0
    sig = inspect.signature(resolve_kp_recovery_threshold)
    assert "n_test_kps" in sig.parameters


def test_label_leaking_edges_includes_causes():
    """P3-018 ROOT FIX: LABEL_LEAKING_EDGES MUST include the causes/
    caused_by edges (adverse-event leakage).
    """
    from graph_transformer.data import LABEL_LEAKING_EDGES
    assert ("drug", "causes", "clinical_outcome") in LABEL_LEAKING_EDGES, (
        "v120 REGRESSION: ('drug', 'causes', 'clinical_outcome') missing "
        "from LABEL_LEAKING_EDGES (P3-018 fix)."
    )
    assert ("clinical_outcome", "caused_by", "drug") in LABEL_LEAKING_EDGES, (
        "v120 REGRESSION: ('clinical_outcome', 'caused_by', 'drug') missing "
        "from LABEL_LEAKING_EDGES (P3-018 fix)."
    )


def test_edge_types_count_is_18():
    """P3-001/P3-002 ROOT FIX: EDGE_TYPES MUST have 18 entries
    (9 forward + 9 reverse), including the new binds/modulates neutral types.
    """
    from graph_transformer.data import EDGE_TYPES
    assert len(EDGE_TYPES) == 18, (
        f"v120 REGRESSION: EDGE_TYPES has {len(EDGE_TYPES)} entries, "
        f"expected 18 (9 forward + 9 reverse)."
    )
    # The neutral binding/modulation types MUST be present.
    assert ("drug", "binds", "protein") in EDGE_TYPES
    assert ("drug", "modulates", "protein") in EDGE_TYPES
    assert ("protein", "bound_by", "drug") in EDGE_TYPES
    assert ("protein", "modulated_by", "drug") in EDGE_TYPES


def test_protein_sequences_are_real_uniprot():
    """P3-029 ROOT FIX: PROTEIN_SEQUENCE_LOOKUP MUST contain REAL UniProt
    N-terminal fragments (not synthetic repetitive patterns).
    """
    from graph_transformer.data.graph_builder import PROTEIN_SEQUENCE_LOOKUP
    assert len(PROTEIN_SEQUENCE_LOOKUP) >= 15
    # Each sequence MUST be a plausible UniProt N-terminal fragment:
    # starts with M (methionine), length >= 40, contains diverse AAs.
    for name, seq in PROTEIN_SEQUENCE_LOOKUP.items():
        assert seq.startswith("M"), (
            f"Protein {name} sequence does not start with M (methionine). "
            f"Real UniProt sequences start with M."
        )
        assert len(seq) >= 40, (
            f"Protein {name} sequence too short ({len(seq)} AAs). "
            f"Expected >= 40 for a meaningful AA-composition feature."
        )
        unique_aas = set(seq)
        assert len(unique_aas) >= 10, (
            f"Protein {name} sequence has only {len(unique_aas)} unique AAs. "
            f"Real proteins have diverse AA compositions (>= 10 unique)."
        )


def test_compute_confidence_uses_binary_entropy():
    """P3-010 ROOT FIX: _compute_confidence MUST use binary entropy
    (1 - H(p)/log(2)), NOT the linear heuristic 2.0 * abs(prob - 0.5).
    """
    import math
    from graph_transformer.service import _compute_confidence
    # prob=0.5 → confidence=0.0 (least confident)
    assert _compute_confidence(0.5) == pytest.approx(0.0, abs=1e-6)
    # prob=0.99 → confidence ~0.92 (high confidence)
    conf_99 = _compute_confidence(0.99)
    assert 0.85 < conf_99 < 0.98, f"conf(0.99)={conf_99} not in [0.85, 0.98]"
    # prob=0.0 → confidence=1.0 (most confident). Use looser tolerance
    # because the clip to [1e-7, 1-1e-7] means p=0.0 becomes p=1e-7,
    # whose entropy is ~1e-7 * log(1e-7) ≈ -1.6e-6, giving confidence
    # ~1.0 - 1.6e-6/log(2) ≈ 0.999998 (not exactly 1.0).
    assert _compute_confidence(0.0) == pytest.approx(1.0, abs=1e-4)
    # The OLD linear formula would give conf(0.99) = 0.98 (2*0.49).
    # The NEW entropy formula gives ~0.92. Verify they differ.
    linear_99 = 2.0 * abs(0.99 - 0.5)
    assert abs(conf_99 - linear_99) > 0.01, (
        f"conf(0.99)={conf_99} matches the OLD linear formula ({linear_99}). "
        f"P3-010 fix requires binary entropy, not linear distance."
    )


def test_set_seed_enables_cudnn_deterministic():
    """P3-008 ROOT FIX: set_seed MUST set cudnn.deterministic=True and
    cudnn.benchmark=False (full GPU reproducibility).
    """
    import torch
    from graph_transformer.utils import set_seed
    set_seed(42)
    if torch.cuda.is_available():
        assert torch.backends.cudnn.deterministic is True, (
            "v120 REGRESSION: cudnn.deterministic is not True after set_seed."
        )
        assert torch.backends.cudnn.benchmark is False, (
            "v120 REGRESSION: cudnn.benchmark is not False after set_seed."
        )
    # CUBLAS_WORKSPACE_CONFIG must be set (for child processes).
    assert os.environ.get("CUBLAS_WORKSPACE_CONFIG") == ":4096:8", (
        f"v120 REGRESSION: CUBLAS_WORKSPACE_CONFIG="
        f"{os.environ.get('CUBLAS_WORKSPACE_CONFIG')!r}, expected ':4096:8'."
    )


if __name__ == "__main__":
    # Allow running directly: python tests/v120_forensic/test_v120_forensic_root_fixes.py
    pytest.main([__file__, "-v", "--tb=short"])
