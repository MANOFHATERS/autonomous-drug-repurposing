"""Task 7.4 — MLflow tracking for all training runs.

HOSTILE-AUDITOR TEST: verifies the trainer ACTUALLY calls MLflow
functions (start_run, log_params, log_metrics, log_artifact,
register_model, end_run). The previous code had a fully-implemented
MLflowRunTracker wrapper but NEVER imported or called it from
trainer.py -- grep "mlflow" trainer.py = 0 matches.

This test verifies the fix by:
  1. Reading the source code (hostile-auditor pattern -- comments lie).
  2. Exercising the runtime by mocking MLflowRunTracker and verifying
     the trainer calls the expected methods.
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import MagicMock, patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_mlflow_tracker_module_exists():
    """Test 7.4.1: MLflowRunTracker wrapper exists in graph_transformer.utils.mlflow_integration."""
    from graph_transformer.utils.mlflow_integration import MLflowRunTracker
    assert callable(MLflowRunTracker), "MLflowRunTracker is not a class"
    # Verify it has the expected methods (not just the class definition).
    for method_name in ["start_run", "log_params", "log_tags", "log_metrics", "log_artifact", "register_model", "end_run"]:
        assert hasattr(MLflowRunTracker, method_name), (
            f"MLflowRunTracker missing method '{method_name}'"
        )


def test_trainer_init_creates_mlflow_tracker():
    """Test 7.4.2: GraphTransformerTrainer.__init__ creates self.mlflow_tracker."""
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    torch.manual_seed(42)
    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}
    node_features = {nt: torch.randn(8, 16) for nt in DEFAULT_NODE_TYPES}
    edge_indices = {
        (src, rel, tgt): torch.tensor([[0, 1], [2, 3]], dtype=torch.long)
        for (src, rel, tgt) in DEFAULT_EDGE_TYPES
    }
    model = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims, embedding_dim=16, num_layers=2,
        num_heads=2, edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES), ffn_hidden_dim=32,
        dropout=0.1, attention_dropout=0.1,
        link_predictor_hidden_dims=[32, 16], seed=42, min_edge_types=1,
    )
    trainer = GraphTransformerTrainer(
        model=model, node_features=node_features, edge_indices=edge_indices,
        learning_rate=1e-3, device="cpu", seed=42,
    )
    assert hasattr(trainer, "mlflow_tracker"), (
        "Trainer does not have 'mlflow_tracker' attribute. The previous "
        "code never instantiated MLflowRunTracker -- the wrapper was dead code."
    )
    assert hasattr(trainer, "_mlflow_available"), (
        "Trainer does not have '_mlflow_available' attribute."
    )


def test_trainer_init_source_imports_mlflow_tracker():
    """Test 7.4.3 (hostile-auditor): __init__ source imports MLflowRunTracker."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.__init__)
    assert "from ..utils.mlflow_integration import MLflowRunTracker" in src, (
        "__init__ source does not import MLflowRunTracker. The previous "
        "code had the wrapper but NEVER imported it."
    )
    assert "self.mlflow_tracker = MLflowRunTracker" in src, (
        "__init__ source does not instantiate MLflowRunTracker."
    )


def test_fit_source_calls_start_run():
    """Test 7.4.4 (hostile-auditor): fit() source calls mlflow_tracker.start_run()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.fit)
    assert "self.mlflow_tracker.start_run" in src, (
        "fit() source does not call mlflow_tracker.start_run()."
    )


def test_fit_source_calls_log_params():
    """Test 7.4.5 (hostile-auditor): fit() source calls mlflow_tracker.log_params()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.fit)
    assert "self.mlflow_tracker.log_params" in src, (
        "fit() source does not call mlflow_tracker.log_params()."
    )


def test_fit_source_calls_log_metrics():
    """Test 7.4.6 (hostile-auditor): fit() source calls mlflow_tracker.log_metrics()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.fit)
    assert "self.mlflow_tracker.log_metrics" in src, (
        "fit() source does not call mlflow_tracker.log_metrics()."
    )


def test_fit_source_calls_end_run():
    """Test 7.4.7 (hostile-auditor): fit() source calls mlflow_tracker.end_run()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.fit)
    assert "self.mlflow_tracker.end_run" in src, (
        "fit() source does not call mlflow_tracker.end_run(). The previous "
        "code NEVER called end_run -- MLflow runs were left in RUNNING state."
    )


def test_fit_source_calls_log_tags():
    """Test 7.4.8 (hostile-auditor): fit() source calls mlflow_tracker.log_tags()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.fit)
    assert "self.mlflow_tracker.log_tags" in src, (
        "fit() source does not call mlflow_tracker.log_tags()."
    )


def test_save_checkpoint_source_calls_log_artifact():
    """Test 7.4.9 (hostile-auditor): save_checkpoint() source calls mlflow_tracker.log_artifact()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.save_checkpoint)
    assert "self.mlflow_tracker.log_artifact" in src, (
        "save_checkpoint() source does not call mlflow_tracker.log_artifact()."
    )


def test_save_checkpoint_source_calls_register_model():
    """Test 7.4.10 (hostile-auditor): save_checkpoint() source calls mlflow_tracker.register_model()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.save_checkpoint)
    assert "self.mlflow_tracker.register_model" in src, (
        "save_checkpoint() source does not call mlflow_tracker.register_model()."
    )


def test_fit_actually_calls_mlflow_at_runtime():
    """Test 7.4.11 (CRITICAL RUNTIME TEST): exercise fit() with a mocked
    MLflowRunTracker and verify start_run + log_params + log_metrics +
    end_run are actually called.

    This is the definitive hostile-auditor test -- it does not trust
    the source code, it verifies the runtime behavior.
    """
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    torch.manual_seed(42)
    n_drugs = 16
    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}
    node_features = {nt: torch.randn(8, 16) for nt in DEFAULT_NODE_TYPES}
    node_features["drug"] = torch.randn(n_drugs, 16)
    node_features["disease"] = torch.randn(4, 16)
    edge_indices = {
        (src, rel, tgt): torch.tensor([[0, 1], [2, 3]], dtype=torch.long)
        for (src, rel, tgt) in DEFAULT_EDGE_TYPES
    }
    model = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims, embedding_dim=16, num_layers=2,
        num_heads=2, edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES), ffn_hidden_dim=32,
        dropout=0.1, attention_dropout=0.1,
        link_predictor_hidden_dims=[32, 16], seed=42, min_edge_types=1,
    )
    trainer = GraphTransformerTrainer(
        model=model, node_features=node_features, edge_indices=edge_indices,
        learning_rate=1e-3, device="cpu", seed=42,
    )
    # Replace the real tracker (which is a no-op without MLFLOW_TRACKING_URI)
    # with a mock so we can verify the trainer calls the right methods.
    mock_tracker = MagicMock()
    trainer.mlflow_tracker = mock_tracker
    # Train drugs: 0-7, val drugs: 8-11 (DISJOINT).
    train_drug = torch.tensor([0, 1, 2, 3, 4, 5, 6, 7] * 2, dtype=torch.long)
    train_disease = torch.tensor([0, 1] * 8, dtype=torch.long)
    train_labels = torch.tensor([1.0, 0.0] * 8, dtype=torch.float32)
    val_drug = torch.tensor([8, 9, 10, 11] * 2, dtype=torch.long)
    val_disease = torch.tensor([0, 1] * 4, dtype=torch.long)
    val_labels = torch.tensor([1.0, 0.0] * 4, dtype=torch.float32)

    trainer.fit(
        train_drug, train_disease, train_labels,
        val_drug, val_disease, val_labels,
        epochs=2, batch_size=4, patience=30,
        calibrate_temperature=False,
    )
    # Verify the trainer called the expected MLflow methods.
    assert mock_tracker.start_run.called, "fit() did not call mlflow_tracker.start_run()"
    assert mock_tracker.log_params.called, "fit() did not call mlflow_tracker.log_params()"
    assert mock_tracker.log_tags.called, "fit() did not call mlflow_tracker.log_tags()"
    assert mock_tracker.log_metrics.called, "fit() did not call mlflow_tracker.log_metrics()"
    assert mock_tracker.end_run.called, "fit() did not call mlflow_tracker.end_run()"
    # Verify log_metrics was called once per epoch (2 epochs = 2 calls).
    assert mock_tracker.log_metrics.call_count >= 2, (
        f"Expected log_metrics to be called >= 2 times (once per epoch), "
        f"got {mock_tracker.log_metrics.call_count}"
    )
    # Verify the params dict has the expected keys.
    params_call = mock_tracker.log_params.call_args
    params_dict = params_call[0][0] if params_call[0] else params_call[1].get("params", {})
    expected_param_keys = {
        "epochs", "batch_size", "patience", "learning_rate", "weight_decay",
        "seed", "device",
    }
    assert expected_param_keys.issubset(set(params_dict.keys())), (
        f"log_params missing expected keys. Got: {set(params_dict.keys())}. "
        f"Missing: {expected_param_keys - set(params_dict.keys())}"
    )


def test_save_checkpoint_actually_calls_mlflow_at_runtime(tmp_path):
    """Test 7.4.12 (CRITICAL RUNTIME TEST): exercise save_checkpoint()
    with a mocked tracker and verify log_artifact + register_model
    are actually called."""
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    torch.manual_seed(42)
    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}
    node_features = {nt: torch.randn(8, 16) for nt in DEFAULT_NODE_TYPES}
    edge_indices = {
        (src, rel, tgt): torch.tensor([[0, 1], [2, 3]], dtype=torch.long)
        for (src, rel, tgt) in DEFAULT_EDGE_TYPES
    }
    model = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims, embedding_dim=16, num_layers=2,
        num_heads=2, edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES), ffn_hidden_dim=32,
        dropout=0.1, attention_dropout=0.1,
        link_predictor_hidden_dims=[32, 16], seed=42, min_edge_types=1,
    )
    trainer = GraphTransformerTrainer(
        model=model, node_features=node_features, edge_indices=edge_indices,
        learning_rate=1e-3, device="cpu", seed=42,
    )
    mock_tracker = MagicMock()
    trainer.mlflow_tracker = mock_tracker

    ckpt_path = str(tmp_path / "test_ckpt.pt")
    trainer.save_checkpoint(ckpt_path)
    assert mock_tracker.log_artifact.called, (
        "save_checkpoint() did not call mlflow_tracker.log_artifact()"
    )
    assert mock_tracker.register_model.called, (
        "save_checkpoint() did not call mlflow_tracker.register_model()"
    )
    # Verify log_artifact was called with the checkpoint path.
    artifact_call = mock_tracker.log_artifact.call_args
    assert artifact_call[0][0] == ckpt_path or artifact_call[1].get("local_path") == ckpt_path, (
        f"log_artifact was not called with the checkpoint path. "
        f"call_args: {artifact_call}"
    )


def test_mlflow_failure_is_non_blocking():
    """Test 7.4.13: MLflow failures do NOT break training.

    The tracker is observability -- it must never block the actual
    training. This test verifies that even when mlflow_tracker methods
    raise exceptions, fit() completes successfully.
    """
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    torch.manual_seed(42)
    n_drugs = 16
    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}
    node_features = {nt: torch.randn(8, 16) for nt in DEFAULT_NODE_TYPES}
    node_features["drug"] = torch.randn(n_drugs, 16)
    node_features["disease"] = torch.randn(4, 16)
    edge_indices = {
        (src, rel, tgt): torch.tensor([[0, 1], [2, 3]], dtype=torch.long)
        for (src, rel, tgt) in DEFAULT_EDGE_TYPES
    }
    model = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims, embedding_dim=16, num_layers=2,
        num_heads=2, edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES), ffn_hidden_dim=32,
        dropout=0.1, attention_dropout=0.1,
        link_predictor_hidden_dims=[32, 16], seed=42, min_edge_types=1,
    )
    trainer = GraphTransformerTrainer(
        model=model, node_features=node_features, edge_indices=edge_indices,
        learning_rate=1e-3, device="cpu", seed=42,
    )
    # Mock that raises on every method.
    failing_tracker = MagicMock()
    failing_tracker.start_run.side_effect = RuntimeError("MLflow server down")
    failing_tracker.log_params.side_effect = RuntimeError("MLflow server down")
    failing_tracker.log_metrics.side_effect = RuntimeError("MLflow server down")
    failing_tracker.end_run.side_effect = RuntimeError("MLflow server down")
    trainer.mlflow_tracker = failing_tracker

    train_drug = torch.tensor([0, 1, 2, 3, 4, 5, 6, 7] * 2, dtype=torch.long)
    train_disease = torch.tensor([0, 1] * 8, dtype=torch.long)
    train_labels = torch.tensor([1.0, 0.0] * 8, dtype=torch.float32)
    val_drug = torch.tensor([8, 9, 10, 11] * 2, dtype=torch.long)
    val_disease = torch.tensor([0, 1] * 4, dtype=torch.long)
    val_labels = torch.tensor([1.0, 0.0] * 4, dtype=torch.float32)

    # fit() should NOT raise -- MLflow failures are non-blocking.
    result = trainer.fit(
        train_drug, train_disease, train_labels,
        val_drug, val_disease, val_labels,
        epochs=2, batch_size=4, patience=30,
        calibrate_temperature=False,
    )
    assert "best_val_auc" in result, "fit() did not complete despite MLflow failures"


if __name__ == "__main__":
    test_mlflow_tracker_module_exists()
    print("Test 7.4.1 PASSED: MLflowRunTracker module exists")
    test_trainer_init_creates_mlflow_tracker()
    print("Test 7.4.2 PASSED: trainer.mlflow_tracker attribute exists")
    test_trainer_init_source_imports_mlflow_tracker()
    print("Test 7.4.3 PASSED: __init__ source imports MLflowRunTracker")
    test_fit_source_calls_start_run()
    print("Test 7.4.4 PASSED: fit() source calls start_run")
    test_fit_source_calls_log_params()
    print("Test 7.4.5 PASSED: fit() source calls log_params")
    test_fit_source_calls_log_metrics()
    print("Test 7.4.6 PASSED: fit() source calls log_metrics")
    test_fit_source_calls_end_run()
    print("Test 7.4.7 PASSED: fit() source calls end_run")
    test_fit_source_calls_log_tags()
    print("Test 7.4.8 PASSED: fit() source calls log_tags")
    test_save_checkpoint_source_calls_log_artifact()
    print("Test 7.4.9 PASSED: save_checkpoint source calls log_artifact")
    test_save_checkpoint_source_calls_register_model()
    print("Test 7.4.10 PASSED: save_checkpoint source calls register_model")
    test_fit_actually_calls_mlflow_at_runtime()
    print("Test 7.4.11 PASSED: fit() runtime calls MLflow methods")
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        test_save_checkpoint_actually_calls_mlflow_at_runtime(tmp)
    print("Test 7.4.12 PASSED: save_checkpoint runtime calls MLflow methods")
    test_mlflow_failure_is_non_blocking()
    print("Test 7.4.13 PASSED: MLflow failure is non-blocking")
    print("---ALL TASK 7.4 TESTS PASSED---")
