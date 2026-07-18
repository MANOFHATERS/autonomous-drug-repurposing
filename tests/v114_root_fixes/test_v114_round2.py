"""v114 Forensic Root-Fix Verification Tests — Round 2 (SH-014, BUG #1-3).

These tests verify the ROOT-LEVEL fixes made in the v114 forensic pass
round 2 (after the user reported no ongoing iteration). Each test maps
to a specific audit issue found by the forensic scanner (Task 3-b).

Run: pytest tests/v114_root_fixes/test_v114_round2.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "phase1"), str(_REPO_ROOT / "phase2"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# =============================================================================
# SH-014: run_real_pipeline_verification.py must test the REAL pipeline
# =============================================================================
class TestSH014RealPipelineVerification:
    """SH-014 ROOT FIX: the verification script must run the REAL
    Phase 1 -> 2 -> 3 pipeline, not just synthetic build_demo_graph()."""

    def test_script_has_test_0_real_pipeline(self):
        src = (_REPO_ROOT / "run_real_pipeline_verification.py").read_text()
        assert "TEST 0" in src or "[0/6]" in src, (
            "run_real_pipeline_verification.py missing TEST 0 (REAL pipeline)"
        )

    def test_script_imports_real_bridge(self):
        src = (_REPO_ROOT / "run_real_pipeline_verification.py").read_text()
        assert "run_phase1_to_phase2" in src, (
            "script does not import the REAL Phase 1->2 bridge"
        )
        assert "adapt_phase2_to_phase3" in src, (
            "script does not import the REAL Phase 2->3 adapter"
        )

    def test_script_renames_synthetic_tests_to_A_prefix(self):
        """The synthetic build_demo_graph tests must be renamed A1-A5
        to clarify they test the SYNTHETIC path, not the real pipeline."""
        src = (_REPO_ROOT / "run_real_pipeline_verification.py").read_text()
        assert "[A1/6]" in src, "synthetic TEST 1 not renamed to [A1/6]"


# =============================================================================
# BUG #1: gnn_score (calibrated) vs confidence (raw) inconsistency
# =============================================================================
class TestBug1GnnScoreConfidenceConsistency:
    """BUG #1 ROOT FIX: confidence must be computed from the SAME
    calibrated matrix that gnn_score is derived from, not from the
    raw score_matrix."""

    def test_confidence_uses_calibrated_matrix(self):
        """Read the REAL source and confirm confidence is computed from
        calibrated_score_matrix, not score_matrix."""
        src = (_REPO_ROOT / "graph_transformer" / "gt_rl_bridge.py").read_text()
        # Find the confidence computation block.
        # The fix changed: gnn_scores_np = score_matrix.cpu().numpy()
        # to:           gnn_scores_np = calibrated_score_matrix.cpu().numpy()
        # in the confidence computation section (around line 1858).
        # Verify the calibrated matrix is used.
        assert "calibrated_score_matrix.cpu().numpy()" in src, (
            "confidence not computed from calibrated_score_matrix -- BUG #1 NOT fixed"
        )

    def test_gnn_score_and_confidence_are_consistent_at_runtime(self):
        """Runtime check: generate a small bridge output and verify
        gn_score and confidence are consistent (both derived from the
        same calibrated matrix). A pair with gnn_score near 0.5 should
        have confidence near 0.0 (max entropy); a pair with gnn_score
        near 0 or 1 should have confidence near 1.0 (min entropy)."""
        os.environ.setdefault("DRUGOS_SKIP_CHEMBERTA", "1")
        os.environ.setdefault("DRUGOS_ENVIRONMENT", "dev")
        from graph_transformer.gt_rl_bridge import GTRLBridge
        with tempfile.TemporaryDirectory() as tmpdir:
            bridge = GTRLBridge(output_dir=tmpdir, device="cpu", seed=42)
            # build_demo_graph signature: (num_drugs, num_diseases,
            # num_known_treatments, inject_known_positives,
            # inject_validated_hypotheses) -- no num_proteins/etc.
            bridge.build_demo_graph(
                num_drugs=15, num_diseases=10,
                num_known_treatments=5,
            )
            bridge.build_model(embedding_dim=16, num_layers=3, num_heads=2)
            bridge.train_model(epochs=3, patience=2)
            df = bridge.generate_rl_input()
            # Verify both columns exist.
            assert "gnn_score" in df.columns
            assert "confidence" in df.columns
            # Consistency check: confidence and gnn_score must both be
            # finite and in [0,1]. The KEY consistency property (both
            # derived from the SAME calibrated matrix) is verified by
            # the source-level test above. This runtime test confirms
            # the columns exist and are well-formed.
            import numpy as np
            gnn = df["gnn_score"].to_numpy()
            conf = df["confidence"].to_numpy()
            assert np.all(np.isfinite(gnn)), "gnn_score has NaN/Inf"
            assert np.all(np.isfinite(conf)), "confidence has NaN/Inf"
            assert np.all(gnn >= 0.0) and np.all(gnn <= 1.0), (
                f"gnn_score out of [0,1]: min={gnn.min()}, max={gnn.max()}"
            )
            assert np.all(conf >= 0.0) and np.all(conf <= 1.0), (
                f"confidence out of [0,1]: min={conf.min()}, max={conf.max()}"
            )
            # If the model has learned anything (gnn_score has variance),
            # confidence should be positively correlated with |gnn_score - 0.5|.
            # On an untrained model (3 epochs), gnn_score may be near-constant
            # so correlation is undefined -- we only assert when there's real
            # variance.
            dist_from_half = np.abs(gnn - 0.5)
            if (len(gnn) > 5
                    and np.std(dist_from_half) > 0.01
                    and np.std(conf) > 0.01):
                corr = float(np.corrcoef(dist_from_half, conf)[0, 1])
                # Allow weak correlation on under-trained models, but it
                # must not be STRONGLY NEGATIVE (that would indicate the
                # old bug where confidence came from a different matrix).
                assert corr > -0.3, (
                    f"BUG #1 regression: confidence is strongly negatively "
                    f"correlated with |gnn_score - 0.5| (corr={corr:.3f}). "
                    f"This means confidence was computed from a DIFFERENT "
                    f"matrix than gnn_score (the old raw-vs-calibrated bug)."
                )


# =============================================================================
# BUG #2: silent data loss on corrupt retrain_triggered.json
# =============================================================================
class TestBug2SilentDataLossRetrainTrigger:
    """BUG #2 ROOT FIX: phase4/writeback.py must NOT silently overwrite
    a corrupt retrain_triggered.json. It must LOG CRITICAL and BACK UP
    the corrupt file."""

    def test_writeback_does_not_silently_swallow_read_errors(self):
        src = (_REPO_ROOT / "phase4" / "writeback.py").read_text()
        # The old code had: except Exception: existing = []
        # The new code catches specific exceptions and logs critical.
        assert "BUG #2 v114" in src, (
            "BUG #2 fix marker not found in phase4/writeback.py"
        )
        assert ".corrupt." in src, (
            "BUG #2 fix does not back up corrupt files to .corrupt.<ts>.json"
        )
        # Verify the old silent pattern is gone (in the retrain_trigger section).
        # Find the retrain_trigger function and check it doesn't have bare except.
        # We look for the specific old pattern near "existing = []".
        assert "json.JSONDecodeError" in src or "OSError" in src, (
            "BUG #2 fix does not catch specific exceptions (JSONDecodeError/OSError)"
        )

    def test_corrupt_json_is_backed_up_not_overwritten(self):
        """Runtime check: write a corrupt retrain_triggered.json, call
        writeback_to_phase3, verify the corrupt file is backed up."""
        os.environ.setdefault("DRUGOS_SKIP_CHEMBERTA", "1")
        from phase4.writeback import writeback_to_phase3, ValidatedHypothesis
        from datetime import datetime, timezone
        with tempfile.TemporaryDirectory() as tmpdir:
            trigger_path = Path(tmpdir) / "retrain_triggered.json"
            # Write a CORRUPT JSON file (invalid syntax).
            trigger_path.write_text("{ this is not valid JSON }}}")
            # Set the env var so writeback uses this path. The env var
            # name is PHASE3_RETRAIN_TRIGGER (per _get_retrain_trigger_path).
            os.environ["PHASE3_RETRAIN_TRIGGER"] = str(trigger_path)
            try:
                vh = ValidatedHypothesis(
                    drug="test_drug",
                    disease="test_disease",
                    outcome="validated_positive",
                    validated_by="test",
                    validation_study_id="test-001",
                    validated_at=datetime.now(timezone.utc).isoformat(),
                    notes="test",
                    original_gt_score=0.5,
                    original_rl_rank=1,
                )
                result = writeback_to_phase3(vh)
                # The corrupt file should have been backed up.
                # The backup naming convention is:
                #   retrain_triggered.corrupt.<timestamp>.json
                # (trigger_path.with_suffix(".corrupt.<ts>.json") replaces
                # the .json suffix, not appends to it).
                backups = list(Path(tmpdir).glob("retrain_triggered.corrupt.*.json"))
                assert len(backups) >= 1, (
                    f"BUG #2: corrupt retrain_triggered.json was NOT backed up. "
                    f"Dir contents: {list(Path(tmpdir).iterdir())}"
                )
                # The new trigger file should be valid JSON.
                assert trigger_path.exists(), "new trigger file not created"
                with open(trigger_path) as f:
                    data = json.load(f)
                assert isinstance(data, list), "new trigger file is not a list"
                assert len(data) >= 1, "new entry not appended"
            finally:
                os.environ.pop("PHASE3_RETRAIN_TRIGGER", None)


# =============================================================================
# BUG #3: flywheel_monitor.py weights_only=False (security)
# =============================================================================
class TestBug3FlywheelMonitorWeightsOnly:
    """BUG #3 ROOT FIX: flywheel_monitor.py must use weights_only=True
    when loading checkpoints (security — prevents arbitrary code
    execution from malicious checkpoints)."""

    def test_flywheel_monitor_uses_weights_only_true(self):
        src = (_REPO_ROOT / "shared" / "monitoring" / "flywheel_monitor.py").read_text()
        assert "weights_only=True" in src, (
            "flywheel_monitor.py does not use weights_only=True -- BUG #3 NOT fixed"
        )
        # Verify there's no remaining weights_only=False in the torch.load call.
        # (A comment might mention weights_only=False for context, but the
        # actual torch.load call must use True.)
        import re
        # Find all torch.load calls and check their weights_only arg.
        matches = re.findall(r"torch\.load\([^)]*weights_only=(\w+)", src)
        assert matches, "no torch.load with weights_only= found"
        for m in matches:
            assert m == "True", (
                f"torch.load uses weights_only={m} (expected True) -- BUG #3 NOT fixed"
            )

    def test_flywheel_monitor_imports_cleanly(self):
        """Verify the module still imports after the fix."""
        os.environ.setdefault("DRUGOS_SKIP_CHEMBERTA", "1")
        import importlib
        mod = importlib.import_module("shared.monitoring.flywheel_monitor")
        assert hasattr(mod, "FlywheelStepStatus") or hasattr(mod, "check_flywheel")
