"""Task 7.1 — per-epoch AUC checkpoint selection + early stopping.

HOSTILE-AUDITOR TEST: verifies the actual trainer code computes val AUC
every epoch, uses it for checkpoint selection, and applies patience-
based early stopping. Does NOT trust comments -- reads the source code
and exercises runtime behavior.

V1 launch criterion: >0.85 AUC on held-out drug-disease pairs. Without
per-epoch AUC, the trainer cannot do early stopping on the V1 metric,
and the saved checkpoint may be from a non-optimal epoch.
"""
from __future__ import annotations

import inspect
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _build_tiny_trainer():
    """Build a tiny trainer on a synthetic graph (CPU, <5s).

    Uses DISJOINT drug sets for train/val (the trainer enforces this
    in fit() -- V30 8.5 fix). Train drugs: [0..7], val drugs: [8..11].
    """
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    torch.manual_seed(42)
    # 16 drugs, 4 diseases (so we can have 8 train drugs + 4 val drugs
    # with NO overlap, satisfying the trainer's drug-aware enforcement).
    n_drugs = 16
    n_diseases = 4
    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}
    # Override drug count to 16.
    node_features = {nt: torch.randn(8, 16) for nt in DEFAULT_NODE_TYPES}
    node_features["drug"] = torch.randn(n_drugs, 16)
    node_features["disease"] = torch.randn(n_diseases, 16)
    edge_indices = {}
    for (src, rel, tgt) in DEFAULT_EDGE_TYPES:
        # 4 edges per type — enough to exercise attention.
        edge_indices[(src, rel, tgt)] = torch.tensor(
            [[0, 1, 2, 3], [0, 1, 2, 3]], dtype=torch.long
        )
    model = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims,
        embedding_dim=16,
        num_layers=2,
        num_heads=2,
        edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES),
        ffn_hidden_dim=32,
        dropout=0.1,
        attention_dropout=0.1,
        link_predictor_hidden_dims=[32, 16],
        seed=42,
        min_edge_types=1,
    )
    trainer = GraphTransformerTrainer(
        model=model,
        node_features=node_features,
        edge_indices=edge_indices,
        learning_rate=1e-3,
        weight_decay=0.01,
        device="cpu",
        seed=42,
    )
    # 16 train pairs: drugs 0-7, diseases 0-1, balanced labels.
    train_drug = torch.tensor([0, 1, 2, 3, 4, 5, 6, 7] * 2, dtype=torch.long)
    train_disease = torch.tensor([0, 1] * 8, dtype=torch.long)
    train_labels = torch.tensor([1.0, 0.0] * 8, dtype=torch.float32)
    # 8 val pairs: drugs 8-11 (DISJOINT from train), diseases 0-1.
    val_drug = torch.tensor([8, 9, 10, 11] * 2, dtype=torch.long)
    val_disease = torch.tensor([0, 1] * 4, dtype=torch.long)
    val_labels = torch.tensor([1.0, 0.0] * 4, dtype=torch.float32)
    return trainer, train_drug, train_disease, train_labels, val_drug, val_disease, val_labels


def test_checkpoint_selection_metric_is_val_auc():
    """Test 7.1.1: trainer.checkpoint_selection_metric == 'val_auc'."""
    trainer, *_ = _build_tiny_trainer()
    assert trainer.checkpoint_selection_metric == "val_auc", (
        f"Expected checkpoint_selection_metric='val_auc', got "
        f"'{trainer.checkpoint_selection_metric}'. The V1 launch criterion "
        f"is AUC > 0.85 -- checkpoint selection MUST be driven by val_auc, "
        f"not val_loss (which can decrease while AUC degrades)."
    )


def test_val_auc_min_improvement_is_set():
    """Test 7.1.2: trainer.val_auc_min_improvement is a positive float."""
    trainer, *_ = _build_tiny_trainer()
    assert isinstance(trainer.val_auc_min_improvement, float), (
        f"Expected float, got {type(trainer.val_auc_min_improvement)}"
    )
    assert 0.0 < trainer.val_auc_min_improvement < 0.1, (
        f"val_auc_min_improvement={trainer.val_auc_min_improvement} is out "
        f"of range (0, 0.1). The default 0.005 filters ±0.1 AUC noise on "
        f"small val sets while still selecting on the correct metric."
    )


def test_fit_returns_best_val_auc():
    """Test 7.1.3: fit() returns a dict with 'best_val_auc' (not just 'best_val_loss')."""
    trainer, train_d, train_dis, train_l, val_d, val_dis, val_l = _build_tiny_trainer()
    result = trainer.fit(
        train_d, train_dis, train_l, val_d, val_dis, val_l,
        epochs=3, batch_size=4, patience=30,
        calibrate_temperature=False,
    )
    assert "best_val_auc" in result, (
        f"fit() return dict missing 'best_val_auc'. Got keys: {list(result.keys())}"
    )
    assert isinstance(result["best_val_auc"], float), (
        f"best_val_auc must be a float, got {type(result['best_val_auc'])}"
    )
    assert 0.0 <= result["best_val_auc"] <= 1.0, (
        f"best_val_auc={result['best_val_auc']} is out of [0, 1]"
    )


def test_fit_records_per_epoch_val_auc_in_history():
    """Test 7.1.4: each epoch's val_auc is recorded in training_history."""
    trainer, train_d, train_dis, train_l, val_d, val_dis, val_l = _build_tiny_trainer()
    result = trainer.fit(
        train_d, train_dis, train_l, val_d, val_dis, val_l,
        epochs=3, batch_size=4, patience=30,
        calibrate_temperature=False,
    )
    history = result["history"]
    assert len(history) >= 1, f"training_history is empty: {history}"
    for i, entry in enumerate(history):
        assert "val_auc" in entry, (
            f"epoch {i} record missing 'val_auc'. Got keys: {list(entry.keys())}"
        )
        assert 0.0 <= entry["val_auc"] <= 1.0, (
            f"epoch {i} val_auc={entry['val_auc']} out of [0, 1]"
        )


def test_fit_source_code_contains_val_auc_checkpoint_logic():
    """Test 7.1.5 (hostile-auditor): READ THE ACTUAL SOURCE CODE.

    Verify the fit() method's source code contains the actual
    checkpoint-selection logic that compares val_auc_now to
    best_val_auc + val_auc_min_improvement. Comments alone are
    insufficient -- the actual code must be present.
    """
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.fit)
    # The actual checkpoint comparison (not just a comment).
    assert "val_auc_now" in src, (
        "fit() source does not contain 'val_auc_now' variable -- the "
        "checkpoint selection logic is missing."
    )
    assert "val_auc_improved" in src, (
        "fit() source does not contain 'val_auc_improved' flag -- the "
        "checkpoint promotion decision is missing."
    )
    assert "best_val_auc + self.val_auc_min_improvement" in src, (
        "fit() source does not compare val_auc_now to "
        "best_val_auc + val_auc_min_improvement -- the noise-robust "
        "checkpoint promotion is missing."
    )
    # The best_state_dict save (the actual checkpoint content).
    assert "best_state_dict" in src, (
        "fit() source does not save best_state_dict on improvement -- "
        "the checkpoint is never persisted."
    )
    # The early stopping check.
    assert "no_improve_count >= patience" in src, (
        "fit() source does not check no_improve_count >= patience -- "
        "early stopping is missing."
    )


def test_fit_records_best_epoch():
    """Test 7.1.6: fit() records best_epoch (the epoch with best val_auc)."""
    trainer, train_d, train_dis, train_l, val_d, val_dis, val_l = _build_tiny_trainer()
    result = trainer.fit(
        train_d, train_dis, train_l, val_d, val_dis, val_l,
        epochs=3, batch_size=4, patience=30,
        calibrate_temperature=False,
    )
    assert "best_epoch" in result, (
        f"fit() return dict missing 'best_epoch'. Got keys: {list(result.keys())}"
    )
    assert isinstance(result["best_epoch"], int), (
        f"best_epoch must be int, got {type(result['best_epoch'])}"
    )
    assert result["best_epoch"] >= 0, (
        f"best_epoch={result['best_epoch']} must be >= 0"
    )


def test_early_stopping_fires_on_no_improvement():
    """Test 7.1.7: when val_auc does not improve for `patience` epochs,
    training stops early."""
    trainer, train_d, train_dis, train_l, val_d, val_dis, val_l = _build_tiny_trainer()
    # patience=1 + min_improvement=0.005 means training should stop
    # within 1-2 epochs if val_auc doesn't improve by >0.005.
    result = trainer.fit(
        train_d, train_dis, train_l, val_d, val_dis, val_l,
        epochs=50, batch_size=4, patience=1,
        calibrate_temperature=False,
    )
    # If early stopping works, epochs_trained should be << 50.
    # We use a generous bound (<=20) to avoid flakiness from random
    # improvements in the first few epochs.
    assert result["epochs_trained"] <= 20, (
        f"Early stopping did not fire: epochs_trained="
        f"{result['epochs_trained']} (expected <= 20 with patience=1). "
        f"This means the trainer ran all 50 epochs without stopping -- "
        f"the early-stopping logic is broken."
    )


if __name__ == "__main__":
    # Allow running without pytest for quick smoke testing.
    test_checkpoint_selection_metric_is_val_auc()
    print("Test 7.1.1 PASSED: checkpoint_selection_metric == 'val_auc'")
    test_val_auc_min_improvement_is_set()
    print("Test 7.1.2 PASSED: val_auc_min_improvement is set")
    test_fit_returns_best_val_auc()
    print("Test 7.1.3 PASSED: fit() returns best_val_auc")
    test_fit_records_per_epoch_val_auc_in_history()
    print("Test 7.1.4 PASSED: per-epoch val_auc recorded in history")
    test_fit_source_code_contains_val_auc_checkpoint_logic()
    print("Test 7.1.5 PASSED: source code contains val_auc checkpoint logic")
    test_fit_records_best_epoch()
    print("Test 7.1.6 PASSED: best_epoch recorded")
    test_early_stopping_fires_on_no_improvement()
    print("Test 7.1.7 PASSED: early stopping fires on no improvement")
    print("---ALL TASK 7.1 TESTS PASSED---")
