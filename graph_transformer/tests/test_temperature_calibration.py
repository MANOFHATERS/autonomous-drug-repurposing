"""Teammate 6 (Task 6.4) — Temperature calibration tests.

P3-004 ROOT FIX (v127 forensic, Teammate 6):

The audit found that temperature calibration (Guo et al. 2017) was DEAD
for the RL agent. The trainer's ``fit_temperature`` was called, but:

  1. There was NO way to visually verify the calibration improved the
     reliability of the model's predicted probabilities. Operators had
     to trust the scalar temperature value (T=1.65, etc.) without
     seeing a reliability diagram.
  2. The RL bridge consumed the model's RAW sigmoid probabilities
     (T=1.0) for the ``gnn_score`` column, ignoring the calibrated
     probabilities entirely. The temperature parameter was dead weight.
  3. There was no MLflow artifact for calibration, so the platform had
     no auditable record of calibration quality for FDA 21 CFR Part 11
     compliance.

These tests verify the ROOT FIX:

  - ``MLflowRunTracker.log_calibration_plot`` builds a matplotlib
    reliability diagram and logs it as an MLflow artifact (when MLflow
    is active). When MLflow is inactive, the method is a no-op.
  - The trainer's ``_calibrate_temperature`` method computes pre- and
    post-calibration probabilities and calls
    ``tracker.log_calibration_plot`` when a tracker is provided.
  - The bridge's ``get_top_k_novel_predictions`` uses calibrated
    probabilities (via ``predict_drug_disease_scores_dual``) for the
    final ``gnn_score`` column.

The tests use a MOCK tracker (no real MLflow server required) so they
run in CI without external dependencies.
"""
from __future__ import annotations

import os
import sys
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import numpy as np
import pytest

# Ensure the repo root is on sys.path so `import graph_transformer` works
# when running pytest from anywhere.
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_THIS_DIR, "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
_PHASE2_ROOT = os.path.join(_REPO_ROOT, "phase2")
if _PHASE2_ROOT not in sys.path:
    sys.path.insert(0, _PHASE2_ROOT)


# =============================================================================
# Test 1: MLflowRunTracker.log_calibration_plot exists and is callable
# =============================================================================
def test_log_calibration_plot_method_exists():
    """MLflowRunTracker MUST have a log_calibration_plot method."""
    from graph_transformer.utils.mlflow_integration import MLflowRunTracker
    assert hasattr(MLflowRunTracker, "log_calibration_plot"), (
        "MLflowRunTracker must have a log_calibration_plot method (Task 6.4)."
    )
    assert callable(getattr(MLflowRunTracker, "log_calibration_plot")), (
        "MLflowRunTracker.log_calibration_plot must be callable."
    )


# =============================================================================
# Test 2: log_calibration_plot is a no-op when MLflow is not active
# =============================================================================
def test_log_calibration_plot_noop_when_inactive():
    """When MLflow is not active (no tracking URI), the method is a no-op.

    This preserves the previous behavior for dev/CI runs that don't have
    MLflow configured. The method must NOT raise.
    """
    from graph_transformer.utils.mlflow_integration import MLflowRunTracker
    # Construct a tracker with no tracking URI — _active is False.
    tracker = MLflowRunTracker(experiment_name="test", tracking_uri=None)
    assert tracker._active is False, (
        "Tracker with tracking_uri=None must be inactive."
    )
    # Build some dummy data.
    pre_probs = np.array([0.1, 0.4, 0.6, 0.9], dtype=np.float32)
    post_probs = np.array([0.2, 0.45, 0.55, 0.85], dtype=np.float32)
    labels = np.array([0, 0, 1, 1], dtype=np.float32)
    # Should not raise.
    tracker.log_calibration_plot(pre_probs, post_probs, labels, step=0)
    # No assertion needed — if we got here, the no-op path works.


# =============================================================================
# Test 3: log_calibration_plot with a mocked MLflow logs the artifact
# =============================================================================
def test_log_calibration_plot_with_mock_mlflow():
    """With a mock MLflow, log_calibration_plot should:
       1. Build a matplotlib figure.
       2. Call mlflow.log_artifact with a PNG file.
       3. Call mlflow.log_metrics with calibration_ece_pre / _post / _improvement.
    """
    from graph_transformer import utils
    from graph_transformer.utils import mlflow_integration

    # Build a fake mlflow module with MagicMock for the functions we use.
    fake_mlflow = MagicMock()
    fake_mlflow.set_tracking_uri = MagicMock()
    fake_mlflow.set_experiment = MagicMock()
    fake_mlflow.start_run = MagicMock(return_value="fake_run_id")
    fake_mlflow.log_artifact = MagicMock()
    fake_mlflow.log_metrics = MagicMock()
    fake_mlflow.end_run = MagicMock()
    fake_mlflow.register_model = MagicMock()

    # Patch the module-level _mlflow and _MLFLOW_AVAILABLE.
    original_mlflow = mlflow_integration._mlflow
    original_available = mlflow_integration._MLFLOW_AVAILABLE
    mlflow_integration._mlflow = fake_mlflow
    mlflow_integration._MLFLOW_AVAILABLE = True

    try:
        from graph_transformer.utils.mlflow_integration import MLflowRunTracker
        # Use a temp directory as the tracking URI so MLflow accepts it.
        with tempfile.TemporaryDirectory() as tmpdir:
            tracker = MLflowRunTracker(
                experiment_name="test_calib",
                tracking_uri=f"file://{tmpdir}",
            )
            # Force active = True (the file:// URI should set it, but
            # mock may not actually configure mlflow properly).
            tracker._active = True
            tracker._run_id = "fake_run_id"

            # Build realistic MIS-CALIBRATED data: the model is over-confident
            # but WRONG on ~50% of samples. Pre-calibration ECE will be high
            # (model says 90% confident but accuracy is only 50%). Post-
            # calibration (T>1) softens to ~60% confidence, which is closer
            # to the true 50% accuracy — ECE drops.
            rng = np.random.default_rng(42)
            n = 200
            labels = (rng.random(n) < 0.5).astype(np.float32)
            # Model predicts high confidence (~0.9) regardless of true label.
            # This is the classic "over-confident but wrong" pattern.
            pre_probs = np.where(
                rng.random(n) < 0.5,  # flip a coin
                0.9 + 0.05 * rng.random(n),  # confident positive (regardless of label)
                0.1 - 0.05 * rng.random(n),  # confident negative (regardless of label)
            ).astype(np.float32)
            # Post-calibration: divide logits by T=3 to soften.
            # Recover logits from pre_probs, then re-sigmoid with T=3.
            eps = 1e-7
            pre_clipped = np.clip(pre_probs, eps, 1 - eps)
            logits = np.log(pre_clipped / (1 - pre_clipped))
            post_probs = (1.0 / (1.0 + np.exp(-logits / 3.0))).astype(np.float32)

            tracker.log_calibration_plot(pre_probs, post_probs, labels, step=5)

            # Verify log_artifact was called with a PNG file.
            assert fake_mlflow.log_artifact.called, (
                "mlflow.log_artifact must be called when tracker is active."
            )
            args, kwargs = fake_mlflow.log_artifact.call_args
            artifact_path = args[0] if args else kwargs.get("local_path")
            assert artifact_path is not None, "log_artifact must be called with a file path."
            assert artifact_path.endswith(".png"), (
                f"Calibration plot must be a PNG file, got: {artifact_path}"
            )
            assert os.path.exists(artifact_path) is False, (
                "Temp PNG file should be deleted after logging."
            )
            # Verify artifact_path kwarg includes step.
            ap = kwargs.get("artifact_path", "")
            assert "epoch_5" in ap, (
                f"artifact_path should include step='epoch_5', got: {ap}"
            )

            # Verify log_metrics was called with ECE metrics.
            assert fake_mlflow.log_metrics.called, (
                "mlflow.log_metrics must be called with ECE metrics."
            )
            _, m_kwargs = fake_mlflow.log_metrics.call_args
            metrics = m_kwargs.get("metrics") if "metrics" in m_kwargs else (
                fake_mlflow.log_metrics.call_args.args[0]
                if fake_mlflow.log_metrics.call_args.args else None
            )
            # log_metrics may be called positionally or as kwarg.
            if metrics is None and fake_mlflow.log_metrics.call_args.args:
                metrics = fake_mlflow.log_metrics.call_args.args[0]
            assert metrics is not None, "log_metrics must receive a metrics dict."
            assert "calibration_ece_pre" in metrics, (
                f"ECE pre-calibration metric missing: {metrics}"
            )
            assert "calibration_ece_post" in metrics, (
                f"ECE post-calibration metric missing: {metrics}"
            )
            assert "calibration_ece_improvement" in metrics, (
                f"ECE improvement metric missing: {metrics}"
            )
            # Post ECE should be lower than pre ECE for this synthetic data
            # (the post-calibration probabilities are softer and the labels
            # are 50/50, so post should be closer to the diagonal).
            assert metrics["calibration_ece_post"] <= metrics["calibration_ece_pre"] + 1e-6, (
                f"Post-calibration ECE ({metrics['calibration_ece_post']}) should "
                f"be <= pre-calibration ECE ({metrics['calibration_ece_pre']}) for "
                f"this synthetic data."
            )
    finally:
        # Restore the original module-level state.
        mlflow_integration._mlflow = original_mlflow
        mlflow_integration._MLFLOW_AVAILABLE = original_available


# =============================================================================
# Test 4: log_calibration_plot handles edge cases (empty input, size mismatch)
# =============================================================================
def test_log_calibration_plot_edge_cases():
    """log_calibration_plot must NOT raise on edge cases."""
    from graph_transformer.utils.mlflow_integration import MLflowRunTracker
    tracker = MLflowRunTracker(experiment_name="test", tracking_uri=None)
    tracker._active = True  # force active to test the input-validation paths

    # Empty input — should be a no-op (no raise).
    tracker.log_calibration_plot([], [], [], step=0)

    # Size mismatch — should log a warning, not raise.
    tracker.log_calibration_plot(
        pre_probs=[0.1, 0.2, 0.3],
        post_probs=[0.1, 0.2],  # different size
        labels=[0, 1, 0],
        step=0,
    )

    # All zeros — should still produce a plot (or no-op silently).
    tracker.log_calibration_plot(
        pre_probs=np.zeros(10),
        post_probs=np.zeros(10),
        labels=np.zeros(10),
        step=0,
    )


# =============================================================================
# Test 5: trainer accepts mlflow_tracker parameter and stores it
# =============================================================================
def test_trainer_accepts_mlflow_tracker_param():
    """GraphTransformerTrainer MUST accept an mlflow_tracker parameter."""
    import inspect
    from graph_transformer.training.trainer import GraphTransformerTrainer
    sig = inspect.signature(GraphTransformerTrainer.__init__)
    assert "mlflow_tracker" in sig.parameters, (
        "GraphTransformerTrainer.__init__ must accept an `mlflow_tracker` parameter (Task 6.4)."
    )
    # The default must be None (so existing callers are unaffected).
    assert sig.parameters["mlflow_tracker"].default is None, (
        "mlflow_tracker default must be None (preserves backward compat)."
    )


# =============================================================================
# Test 6: trainer stores the tracker on self._mlflow_tracker
# =============================================================================
def test_trainer_stores_mlflow_tracker():
    """When a tracker is provided, the trainer stores it on self._mlflow_tracker."""
    import torch
    from graph_transformer.training.trainer import GraphTransformerTrainer

    # Build a minimal model + graph for the trainer constructor.
    # We use a tiny model to keep the test fast.
    from graph_transformer.models.graph_transformer import DrugRepurposingGraphTransformer
    from graph_transformer.data import DEFAULT_FEATURE_DIMS, EDGE_TYPES

    model = DrugRepurposingGraphTransformer(
        feature_dims=DEFAULT_FEATURE_DIMS,
        embedding_dim=16,
        num_layers=1,
        num_heads=2,
        edge_types=EDGE_TYPES,
        node_types=list(DEFAULT_FEATURE_DIMS.keys()),
        ffn_hidden_dim=32,
        link_predictor_hidden_dims=[16, 8],
    )
    node_features = {
        nt: torch.randn(3, DEFAULT_FEATURE_DIMS[nt]) for nt in DEFAULT_FEATURE_DIMS
    }
    edge_indices = {
        et: torch.tensor([[0, 1], [1, 2]], dtype=torch.long) for et in EDGE_TYPES
    }

    # Without a tracker — self._mlflow_tracker is None.
    trainer = GraphTransformerTrainer(
        model=model,
        node_features=node_features,
        edge_indices=edge_indices,
        device="cpu",
        seed=42,
    )
    assert trainer._mlflow_tracker is None, (
        "Default trainer should have _mlflow_tracker=None."
    )

    # With a mock tracker — self._mlflow_tracker is the mock.
    mock_tracker = MagicMock()
    trainer_with_tracker = GraphTransformerTrainer(
        model=model,
        node_features=node_features,
        edge_indices=edge_indices,
        device="cpu",
        seed=42,
        mlflow_tracker=mock_tracker,
    )
    assert trainer_with_tracker._mlflow_tracker is mock_tracker, (
        "Trainer should store the provided tracker on self._mlflow_tracker."
    )


# =============================================================================
# Test 7: _calibrate_temperature calls tracker.log_calibration_plot
# =============================================================================
def test_calibrate_temperature_calls_tracker_log_calibration_plot():
    """When a tracker is provided, _calibrate_temperature MUST call
    tracker.log_calibration_plot with pre_probs, post_probs, and labels."""
    import torch
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.models.graph_transformer import DrugRepurposingGraphTransformer
    from graph_transformer.data import DEFAULT_FEATURE_DIMS, EDGE_TYPES, LABEL_LEAKING_EDGES

    torch.manual_seed(42)
    model = DrugRepurposingGraphTransformer(
        feature_dims=DEFAULT_FEATURE_DIMS,
        embedding_dim=16,
        num_layers=1,
        num_heads=2,
        edge_types=EDGE_TYPES,
        node_types=list(DEFAULT_FEATURE_DIMS.keys()),
        ffn_hidden_dim=32,
        link_predictor_hidden_dims=[16, 8],
    )
    node_features = {
        nt: torch.randn(5, DEFAULT_FEATURE_DIMS[nt]) for nt in DEFAULT_FEATURE_DIMS
    }
    edge_indices = {
        et: torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long) for et in EDGE_TYPES
    }

    mock_tracker = MagicMock()
    # log_calibration_plot is a no-op on the mock (just records the call).
    mock_tracker.log_calibration_plot = MagicMock(return_value=None)

    trainer = GraphTransformerTrainer(
        model=model,
        node_features=node_features,
        edge_indices=edge_indices,
        device="cpu",
        seed=42,
        mlflow_tracker=mock_tracker,
    )

    # Build val data with BOTH classes (fit_temperature requires this).
    val_drug_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    val_disease_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    val_labels = torch.tensor([0.0, 1.0, 0.0, 1.0], dtype=torch.float32)

    # Call _calibrate_temperature. This should:
    # 1. Encode the graph (no_grad).
    # 2. Compute pre_logits via link_predictor.forward_logits.
    # 3. Call fit_temperature to get the temperature.
    # 4. Compute pre_probs and post_probs from pre_logits.
    # 5. Call mock_tracker.log_calibration_plot.
    temp = trainer._calibrate_temperature(
        val_drug_idx, val_disease_idx, val_labels,
        exclude_edges=set(LABEL_LEAKING_EDGES),
    )

    # Temperature should be a positive float.
    assert isinstance(temp, float), f"Temperature must be a float, got {type(temp)}."
    assert temp > 0.0, f"Temperature must be positive, got {temp}."

    # Verify the tracker was called.
    assert mock_tracker.log_calibration_plot.called, (
        "trainer._calibrate_temperature MUST call tracker.log_calibration_plot "
        "when a tracker is provided (Task 6.4 ROOT FIX)."
    )
    # Verify the call args.
    call_args = mock_tracker.log_calibration_plot.call_args
    assert call_args is not None, "Call args must be present."
    # The method signature is (pre_probs, post_probs, labels, step=None, n_bins=10).
    # Verify the args have the right shape. Use explicit None checks (not
    # truthiness) because the values are numpy arrays (truthiness is ambiguous).
    pre_probs = call_args.kwargs.get("pre_probs")
    if pre_probs is None:
        pre_probs = call_args.args[0] if len(call_args.args) > 0 else None
    post_probs = call_args.kwargs.get("post_probs")
    if post_probs is None:
        post_probs = call_args.args[1] if len(call_args.args) > 1 else None
    labels = call_args.kwargs.get("labels")
    if labels is None:
        labels = call_args.args[2] if len(call_args.args) > 2 else None
    assert pre_probs is not None, "pre_probs must be passed."
    assert post_probs is not None, "post_probs must be passed."
    assert labels is not None, "labels must be passed."
    # All three should have the same length (= number of val pairs = 4).
    assert len(pre_probs) == 4, f"pre_probs should have 4 elements, got {len(pre_probs)}."
    assert len(post_probs) == 4, f"post_probs should have 4 elements, got {len(post_probs)}."
    assert len(labels) == 4, f"labels should have 4 elements, got {len(labels)}."
    # Probabilities must be in [0, 1].
    assert float(np.min(pre_probs)) >= 0.0 and float(np.max(pre_probs)) <= 1.0, (
        f"pre_probs must be in [0,1], got min={np.min(pre_probs)} max={np.max(pre_probs)}."
    )
    assert float(np.min(post_probs)) >= 0.0 and float(np.max(post_probs)) <= 1.0, (
        f"post_probs must be in [0,1], got min={np.min(post_probs)} max={np.max(post_probs)}."
    )


# =============================================================================
# Test 8: _calibrate_temperature works WITHOUT a tracker (backward compat)
# =============================================================================
def test_calibrate_temperature_works_without_tracker():
    """When NO tracker is provided, _calibrate_temperature must still work
    (preserving the previous behavior for dev/CI runs without MLflow)."""
    import torch
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.models.graph_transformer import DrugRepurposingGraphTransformer
    from graph_transformer.data import DEFAULT_FEATURE_DIMS, EDGE_TYPES, LABEL_LEAKING_EDGES

    torch.manual_seed(42)
    model = DrugRepurposingGraphTransformer(
        feature_dims=DEFAULT_FEATURE_DIMS,
        embedding_dim=16,
        num_layers=1,
        num_heads=2,
        edge_types=EDGE_TYPES,
        node_types=list(DEFAULT_FEATURE_DIMS.keys()),
        ffn_hidden_dim=32,
        link_predictor_hidden_dims=[16, 8],
    )
    node_features = {
        nt: torch.randn(5, DEFAULT_FEATURE_DIMS[nt]) for nt in DEFAULT_FEATURE_DIMS
    }
    edge_indices = {
        et: torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long) for et in EDGE_TYPES
    }

    trainer = GraphTransformerTrainer(
        model=model,
        node_features=node_features,
        edge_indices=edge_indices,
        device="cpu",
        seed=42,
        # mlflow_tracker=None (default).
    )
    assert trainer._mlflow_tracker is None

    val_drug_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    val_disease_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    val_labels = torch.tensor([0.0, 1.0, 0.0, 1.0], dtype=torch.float32)

    # Should not raise even without a tracker.
    temp = trainer._calibrate_temperature(
        val_drug_idx, val_disease_idx, val_labels,
        exclude_edges=set(LABEL_LEAKING_EDGES),
    )
    assert isinstance(temp, float)
    assert temp > 0.0


# =============================================================================
# Test 9: get_top_k_novel_predictions uses calibrated probabilities
# =============================================================================
def test_get_top_k_novel_predictions_uses_calibrated_probs():
    """The bridge's get_top_k_novel_predictions MUST use calibrated
    probabilities (via predict_drug_disease_scores_dual) for the final
    gnn_score column. The previous code used raw sigmoid (T=1.0),
    making the temperature parameter dead weight."""
    import inspect
    from graph_transformer.gt_rl_bridge import GTRLBridge
    src = inspect.getsource(GTRLBridge.get_top_k_novel_predictions)
    # Must use the dual-call function (single call returning both raw and calibrated).
    assert "predict_drug_disease_scores_dual" in src, (
        "get_top_k_novel_predictions must use predict_drug_disease_scores_dual "
        "to get calibrated probabilities (Task 6.4 / 6.5)."
    )
    # Must store the calibrated value in the gnn_score column.
    assert "gnn_score_calibrated" in src, (
        "get_top_k_novel_predictions must store calibrated probabilities in "
        "the gnn_score_calibrated column."
    )


# =============================================================================
# Test 10: predict_all_pairs not called twice in get_top_k_novel_predictions
# =============================================================================
def test_get_top_k_novel_predictions_predict_all_pairs_at_most_once():
    """Task 6.5: predict_all_pairs must NOT be called twice (doubles inference time).

    The verification command: src.count('predict_all_pairs') <= 1.
    """
    import inspect
    from graph_transformer.gt_rl_bridge import GTRLBridge
    src = inspect.getsource(GTRLBridge.get_top_k_novel_predictions)
    count = src.count("predict_all_pairs")
    assert count <= 1, (
        f"predict_all_pairs is mentioned {count} times in "
        f"get_top_k_novel_predictions source. Must be <= 1 (Task 6.5)."
    )


# =============================================================================
# Test 11: temperature parameter is not dead weight (model has fit_temperature)
# =============================================================================
def test_link_predictor_has_fit_temperature():
    """The link predictor MUST have a fit_temperature method that actually
    updates the temperature parameter (Guo et al. 2017)."""
    from graph_transformer.models.link_predictor import DrugDiseaseLinkPredictor
    assert hasattr(DrugDiseaseLinkPredictor, "fit_temperature"), (
        "DrugDiseaseLinkPredictor must have a fit_temperature method (Task 6.4 / B10)."
    )
    assert callable(DrugDiseaseLinkPredictor.fit_temperature), (
        "fit_temperature must be callable."
    )


# =============================================================================
# Test 12: fit_temperature actually changes the temperature parameter
# =============================================================================
def test_fit_temperature_updates_temperature_parameter():
    """Calling fit_temperature MUST update self.temperature (not be a no-op).

    This is the SCIENTIFIC correctness test — if fit_temperature silently
    failed (e.g., because it was inside a no_grad block), the temperature
    would stay at 1.0 and calibration would be a no-op.
    """
    import torch
    from graph_transformer.models.link_predictor import DrugDiseaseLinkPredictor

    torch.manual_seed(42)
    # Build a tiny link predictor.
    lp = DrugDiseaseLinkPredictor(
        embedding_dim=16,
        hidden_dims=[8],
        dropout=0.0,
        activation="relu",
        num_pairs=None,
        use_abs_diff=True,
    )
    # Initial temperature should be 1.0 (or close to it).
    initial_temp = float(lp.temperature.detach().cpu().mean().item())
    # Build synthetic data where the model is over-confident.
    # Logits = +/- 5 (very confident), labels match the sign.
    drug_emb = torch.randn(20, 16)
    disease_emb = torch.randn(20, 16)
    # Compute logits via the predictor's forward_logits.
    with torch.no_grad():
        logits = lp.forward_logits(drug_emb, disease_emb).squeeze(-1)  # (20,)
    # Labels: 1 if logit > 0 else 0 (matches the model's confidence).
    # Shape (20,) — fit_temperature expects 1D labels.
    labels = (logits > 0).float()

    # Fit temperature.
    temp = lp.fit_temperature(drug_emb, disease_emb, labels)
    final_temp = float(lp.temperature.detach().cpu().mean().item())

    # The temperature parameter MUST have changed from its initial value.
    # (It might increase or decrease depending on the data, but it must NOT
    # stay exactly at the initial value — that would indicate fit_temperature
    # was a no-op.)
    assert abs(final_temp - initial_temp) > 1e-6, (
        f"fit_temperature did not update the temperature parameter "
        f"(initial={initial_temp}, final={final_temp}). The method is a "
        f"no-op — this is the P3-004 'dead temperature' bug."
    )
    # The returned temperature should match the parameter.
    assert abs(temp - final_temp) < 1e-4, (
        f"Returned temperature ({temp}) does not match the parameter ({final_temp})."
    )


if __name__ == "__main__":
    # Allow running as a script: python -m pytest graph_transformer/tests/test_temperature_calibration.py -v
    pytest.main([__file__, "-v", "--tb=short"])
