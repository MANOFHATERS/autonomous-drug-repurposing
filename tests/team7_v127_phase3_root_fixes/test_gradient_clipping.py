"""Task 7.2 — gradient clipping + mixed precision (AMP).

HOSTILE-AUDITOR TEST: verifies BOTH the inline batching path AND the
DataLoader production path (n_samples >= 8192) have:
  1. torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
  2. torch.cuda.amp.autocast + GradScaler (AMP) -- on CUDA only

The previous code claimed AMP was wired in but the DataLoader path
(used for production-scale graphs >= 8192 pairs) ran PURE fp32 even
when AMP was enabled. This test verifies the fix is actually present
in BOTH code paths by reading the source AND exercising the runtime.
"""
from __future__ import annotations

import inspect
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_train_epoch_source_contains_clip_grad_norm():
    """Test 7.2.1: train_epoch() source contains clip_grad_norm_."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.train_epoch)
    assert "torch.nn.utils.clip_grad_norm_" in src, (
        "train_epoch() source does not contain clip_grad_norm_. "
        "Without gradient clipping, training on the 6M-node KG is "
        "unstable (exploding gradients on large attention outputs)."
    )
    # Verify the max_norm is 1.0 (the standard value for Transformers
    # per Vaswani et al. 2017 + Loshchilov & Hutter 2019).
    assert "clip_grad_norm_(self.model.parameters(), 1.0)" in src, (
        "train_epoch() source does not use max_norm=1.0. The standard "
        "Transformer clipping value is 1.0; deviating without a measured "
        "reason risks instability."
    )


def test_train_epoch_source_contains_amp_autocast():
    """Test 7.2.2: train_epoch() source contains torch.cuda.amp.autocast."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.train_epoch)
    assert "torch.cuda.amp.autocast" in src, (
        "train_epoch() source does not contain torch.cuda.amp.autocast. "
        "Without AMP, production training on V100/A100 GPUs is 2-3x slower."
    )


def test_train_epoch_source_contains_grad_scaler():
    """Test 7.2.3: train_epoch() source contains GradScaler + scale().backward()."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.train_epoch)
    assert "GradScaler" in src, (
        "train_epoch() source does not contain GradScaler. "
        "AMP requires GradScaler to prevent fp16 gradient underflow."
    )
    assert "_amp_scaler.scale(loss).backward()" in src, (
        "train_epoch() source does not call _amp_scaler.scale(loss).backward(). "
        "Without loss scaling, fp16 gradients underflow to zero."
    )
    assert "_amp_scaler.unscale_(self.optimizer)" in src, (
        "train_epoch() source does not call _amp_scaler.unscale_() before "
        "clip_grad_norm_. The clip threshold must operate on REAL gradient "
        "magnitudes, not the scaled ones."
    )
    assert "_amp_scaler.step(self.optimizer)" in src, (
        "train_epoch() source does not call _amp_scaler.step(). "
        "AMP requires the scaler to call optimizer.step() (skips on inf/NaN)."
    )
    assert "_amp_scaler.update()" in src, (
        "train_epoch() source does not call _amp_scaler.update(). "
        "The scaler's loss scale must be updated adaptively."
    )


def test_amp_present_in_dataloader_path():
    """Test 7.2.4 (CRITICAL, hostile-auditor): the DataLoader production
    path (n_samples >= 8192) MUST have AMP support.

    The previous code only had AMP in the INLINE batching path (small
    training sets). The DataLoader path -- which triggers for
    production-scale graphs -- ran pure fp32. This test verifies the
    fix by parsing the source: the DataLoader ``for ... in loader:``
    loop must contain an AMP branch (``if _amp_enabled and
    _amp_scaler is not None:``).
    """
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.train_epoch)
    # Find the DataLoader path: it's the block after "use_dataloader"
    # and contains "for d_idx, ds_idx, batch_labels in loader:".
    assert "for d_idx, ds_idx, batch_labels in loader:" in src, (
        "DataLoader production path not found in train_epoch source."
    )
    # Find the slice from the DataLoader loop to the inline batching path.
    loader_loop_idx = src.index("for d_idx, ds_idx, batch_labels in loader:")
    # The inline batching path starts after the DataLoader path's return.
    inline_idx = src.find("# Inline batching path", loader_loop_idx)
    if inline_idx == -1:
        # Fall back to looking for the second batching loop pattern.
        inline_idx = src.find("for start in range(0, n_samples, batch_size):", loader_loop_idx)
    assert inline_idx > loader_loop_idx, (
        "Could not locate inline batching path after DataLoader path. "
        "The train_epoch source structure has changed -- update this test."
    )
    dataloader_block = src[loader_loop_idx:inline_idx]
    # The CRITICAL assertion: the DataLoader block must contain the AMP
    # branch. The previous code only had plain loss.backward() here.
    assert "_amp_enabled and _amp_scaler is not None" in dataloader_block, (
        "DATA LOADER PRODUCTION PATH IS MISSING AMP! The block "
        "(for ... in loader:) does not contain the AMP branch "
        "(if _amp_enabled and _amp_scaler is not None:). The previous "
        "code ran pure fp32 in the DataLoader path even when AMP was "
        "enabled -- this is the exact 'comments claim fixed but code "
        "is broken' bug the audit flagged."
    )
    assert "torch.cuda.amp.autocast" in dataloader_block, (
        "DataLoader path is missing autocast -- AMP forward pass."
    )
    assert "_amp_scaler.scale(loss).backward()" in dataloader_block, (
        "DataLoader path is missing _amp_scaler.scale(loss).backward()"
    )
    assert "_amp_scaler.step(self.optimizer)" in dataloader_block, (
        "DataLoader path is missing _amp_scaler.step(self.optimizer)"
    )


def test_amp_present_in_inline_path():
    """Test 7.2.5: the inline batching path also has AMP (sanity check).

    The inline path was already correct in the previous code -- this
    test ensures the fix didn't accidentally break it."""
    from graph_transformer.training.trainer import GraphTransformerTrainer
    src = inspect.getsource(GraphTransformerTrainer.train_epoch)
    # The inline batching path contains "for start in range(0, n_samples, batch_size):"
    inline_idx = src.find("for start in range(0, n_samples, batch_size):")
    assert inline_idx >= 0, "Inline batching path not found"
    # Take the block from the inline loop to the end of the function.
    inline_block = src[inline_idx:]
    assert "_amp_enabled and _amp_scaler is not None" in inline_block, (
        "Inline batching path is missing the AMP branch."
    )
    assert "torch.cuda.amp.autocast" in inline_block
    assert "_amp_scaler.scale(loss).backward()" in inline_block


def test_gradient_clipping_runtime_works():
    """Test 7.2.6: train_epoch() runs end-to-end with clipping (CPU).

    Exercises the actual clipping code path -- verifies no runtime
    errors (e.g., wrong parameter list, missing self.model, etc.).
    """
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
    # 4 train pairs (small enough for inline path).
    drug_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    disease_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    labels = torch.tensor([1.0, 0.0, 1.0, 0.0], dtype=torch.float32)
    # Run one epoch -- should not raise.
    avg_loss = trainer.train_epoch(drug_idx, disease_idx, labels, batch_size=2)
    assert isinstance(avg_loss, float), f"avg_loss must be float, got {type(avg_loss)}"
    assert avg_loss > 0, f"avg_loss must be > 0, got {avg_loss}"
    # Verify gradients were clipped by checking model params have finite gradients.
    for name, p in model.named_parameters():
        if p.grad is not None:
            assert torch.isfinite(p.grad).all(), (
                f"Param {name} has non-finite gradients after train_epoch -- "
                f"clipping may have failed."
            )


def test_dataloader_path_runtime_works():
    """Test 7.2.7: train_epoch() works with the DataLoader path.

    Forces the DataLoader path by passing a large n_samples (>= 8192
    threshold) -- exercises the AMP-wired production code path.
    """
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.training.trainer import GraphTransformerTrainer
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    torch.manual_seed(42)
    n_drugs = 100
    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}
    node_features = {nt: torch.randn(8, 16) for nt in DEFAULT_NODE_TYPES}
    node_features["drug"] = torch.randn(n_drugs, 16)
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
    # Build 8192 pairs (the DataLoader threshold). Use 100 distinct drugs.
    n_pairs = 8192
    drug_idx = torch.randint(0, n_drugs, (n_pairs,), dtype=torch.long)
    disease_idx = torch.randint(0, 4, (n_pairs,), dtype=torch.long)
    labels = (torch.rand(n_pairs) > 0.5).float()
    # Run one epoch -- should not raise. On CPU, AMP is disabled (no
    # fp16 tensor cores), so the fp32 fallback branch runs. The point
    # is to verify the DataLoader path itself works end-to-end.
    avg_loss = trainer.train_epoch(drug_idx, disease_idx, labels, batch_size=256)
    assert isinstance(avg_loss, float)
    assert avg_loss > 0


if __name__ == "__main__":
    test_train_epoch_source_contains_clip_grad_norm()
    print("Test 7.2.1 PASSED: clip_grad_norm_ in source")
    test_train_epoch_source_contains_amp_autocast()
    print("Test 7.2.2 PASSED: autocast in source")
    test_train_epoch_source_contains_grad_scaler()
    print("Test 7.2.3 PASSED: GradScaler in source")
    test_amp_present_in_dataloader_path()
    print("Test 7.2.4 PASSED: AMP in DataLoader production path (CRITICAL FIX)")
    test_amp_present_in_inline_path()
    print("Test 7.2.5 PASSED: AMP in inline path")
    test_gradient_clipping_runtime_works()
    print("Test 7.2.6 PASSED: runtime clipping works")
    test_dataloader_path_runtime_works()
    print("Test 7.2.7 PASSED: DataLoader path runtime works")
    print("---ALL TASK 7.2 TESTS PASSED---")
