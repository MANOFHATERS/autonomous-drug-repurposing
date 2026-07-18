"""P3-032 v119 ROOT FIX — Per-edge-type out_proj CI test.

The P3-032 audit found that ``HeterogeneousMultiHeadAttention`` used a
SINGLE shared ``out_proj`` for all edge types. Standard HGT (Wang et al.
2019, "Heterogeneous Graph Transformer") uses PER-EDGE-TYPE output
projections so each edge type can learn a different "message
transformation".

The v119 fix adds an optional ``per_edge_type_out_proj`` constructor
flag (default False for backward compatibility). When True:
  - Each edge type gets its own ``out_proj`` module in a ModuleDict.
  - The per-edge-type out_proj is applied to weighted_V_flat BEFORE
    scatter_add (so each edge type's messages are transformed
    independently, then summed).
  - The shared ``self.out_proj`` is NOT applied (it would double-
    transform the messages).

When False (default):
  - The shared ``self.out_proj`` is applied AFTER scatter_add
    (backward compat with existing trained checkpoints).
  - ``self.out_proj_per_edge_type`` is an empty ModuleDict.

This test verifies:
  1. Default constructor (per_edge_type_out_proj=False) preserves the
     old behavior — the shared out_proj is used, the per-edge-type
     ModuleDict is empty.
  2. Explicit per_edge_type_out_proj=True creates per-edge-type
     modules and uses them in the forward pass.
  3. State_dict keys are stable for both flag values.
  4. The forward pass produces valid output in both modes.
"""
from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_p3_032_v119_default_is_shared_out_proj():
    """P3-032 v119: default constructor uses shared out_proj (backward compat)."""
    import torch
    from graph_transformer.models.layers import HeterogeneousMultiHeadAttention

    edge_types = [("drug", "inhibits", "protein"), ("protein", "inhibited_by", "drug")]
    attn = HeterogeneousMultiHeadAttention(
        embedding_dim=16,
        num_heads=2,
        edge_types=edge_types,
        dropout=0.0,
        node_types=["drug", "protein"],
    )
    # Default flag is False.
    assert attn.per_edge_type_out_proj is False, \
        "Default per_edge_type_out_proj must be False (backward compat)"
    # Shared out_proj exists.
    assert hasattr(attn, "out_proj"), "Shared out_proj must always exist"
    assert isinstance(attn.out_proj, torch.nn.Linear)
    # Per-edge-type ModuleDict is EMPTY (no per-edge-type modules).
    assert hasattr(attn, "out_proj_per_edge_type"), \
        "out_proj_per_edge_type ModuleDict must always exist (even if empty)"
    assert isinstance(attn.out_proj_per_edge_type, torch.nn.ModuleDict)
    assert len(attn.out_proj_per_edge_type) == 0, \
        "Default per_edge_type_out_proj=False must produce empty ModuleDict"


def test_p3_032_v119_explicit_flag_creates_per_edge_type_modules():
    """P3-032 v119: per_edge_type_out_proj=True creates per-edge-type modules."""
    import torch
    from graph_transformer.models.layers import HeterogeneousMultiHeadAttention

    edge_types = [("drug", "inhibits", "protein"), ("protein", "inhibited_by", "drug")]
    attn = HeterogeneousMultiHeadAttention(
        embedding_dim=16,
        num_heads=2,
        edge_types=edge_types,
        dropout=0.0,
        node_types=["drug", "protein"],
        per_edge_type_out_proj=True,
    )
    assert attn.per_edge_type_out_proj is True
    # Shared out_proj STILL EXISTS (so old checkpoints load via strict=True,
    # even though it's not used in the forward pass).
    assert hasattr(attn, "out_proj")
    assert isinstance(attn.out_proj, torch.nn.Linear)
    # Per-edge-type ModuleDict has one entry per edge type.
    assert len(attn.out_proj_per_edge_type) == len(edge_types), \
        f"Expected {len(edge_types)} per-edge-type modules, got {len(attn.out_proj_per_edge_type)}"
    # Each entry is an nn.Linear.
    for (src, rel, tgt) in edge_types:
        edge_key = f"{src}_{rel}_{tgt}"
        assert edge_key in attn.out_proj_per_edge_type, \
            f"Missing per-edge-type out_proj for {edge_key}"
        per_edge = attn.out_proj_per_edge_type[edge_key]
        assert isinstance(per_edge, torch.nn.Linear), \
            f"Per-edge-type out_proj for {edge_key} must be nn.Linear"
        # Shape: (num_heads * head_dim) -> embedding_dim
        # embedding_dim=16, num_heads=2 → head_dim=8 → input=16, output=16
        assert per_edge.in_features == 16, \
            f"Per-edge-type out_proj in_features must be 16, got {per_edge.in_features}"
        assert per_edge.out_features == 16, \
            f"Per-edge-type out_proj out_features must be 16, got {per_edge.out_features}"


def test_p3_032_v119_forward_pass_shared_mode():
    """P3-032 v119: forward pass works in shared (default) mode."""
    import torch
    from graph_transformer.models.layers import HeterogeneousMultiHeadAttention

    torch.manual_seed(42)
    edge_types = [("drug", "inhibits", "protein"), ("protein", "inhibited_by", "drug")]
    attn = HeterogeneousMultiHeadAttention(
        embedding_dim=16,
        num_heads=2,
        edge_types=edge_types,
        dropout=0.0,
        node_types=["drug", "protein"],
        per_edge_type_out_proj=False,  # default
    )
    attn.eval()  # disable dropout

    node_embeddings = {
        "drug": torch.randn(3, 16),
        "protein": torch.randn(3, 16),
    }
    edge_indices = {
        ("drug", "inhibits", "protein"): torch.tensor([[0, 1], [0, 2]], dtype=torch.long),
        ("protein", "inhibited_by", "drug"): torch.tensor([[0, 1], [1, 2]], dtype=torch.long),
    }

    output = attn(node_embeddings, edge_indices)
    assert "drug" in output
    assert "protein" in output
    assert output["drug"].shape == (3, 16)
    assert output["protein"].shape == (3, 16)
    # Output should be finite (no NaN/Inf from gradient issues).
    assert torch.isfinite(output["drug"]).all(), "drug output has NaN/Inf"
    assert torch.isfinite(output["protein"]).all(), "protein output has NaN/Inf"


def test_p3_032_v119_forward_pass_per_edge_type_mode():
    """P3-032 v119: forward pass works in per-edge-type mode."""
    import torch
    from graph_transformer.models.layers import HeterogeneousMultiHeadAttention

    torch.manual_seed(42)
    edge_types = [("drug", "inhibits", "protein"), ("protein", "inhibited_by", "drug")]
    attn = HeterogeneousMultiHeadAttention(
        embedding_dim=16,
        num_heads=2,
        edge_types=edge_types,
        dropout=0.0,
        node_types=["drug", "protein"],
        per_edge_type_out_proj=True,
    )
    attn.eval()

    node_embeddings = {
        "drug": torch.randn(3, 16),
        "protein": torch.randn(3, 16),
    }
    edge_indices = {
        ("drug", "inhibits", "protein"): torch.tensor([[0, 1], [0, 2]], dtype=torch.long),
        ("protein", "inhibited_by", "drug"): torch.tensor([[0, 1], [1, 2]], dtype=torch.long),
    }

    output = attn(node_embeddings, edge_indices)
    assert "drug" in output
    assert "protein" in output
    assert output["drug"].shape == (3, 16)
    assert output["protein"].shape == (3, 16)
    assert torch.isfinite(output["drug"]).all(), "drug output has NaN/Inf (per-edge-type mode)"
    assert torch.isfinite(output["protein"]).all(), "protein output has NaN/Inf (per-edge-type mode)"


def test_p3_032_v119_state_dict_keys_stable():
    """P3-032 v119: state_dict keys are stable for both flag values.

    Old checkpoints (trained with per_edge_type_out_proj=False) must
    load into a per_edge_type_out_proj=False model via strict=True.
    New models (per_edge_type_out_proj=True) have additional keys
    that old checkpoints don't have — strict=True on old → new FAILS
    (this is the desired behavior; the operator must retrain).
    """
    import torch
    from graph_transformer.models.layers import HeterogeneousMultiHeadAttention

    edge_types = [("drug", "inhibits", "protein"), ("protein", "inhibited_by", "drug")]

    # Shared mode (default).
    attn_shared = HeterogeneousMultiHeadAttention(
        embedding_dim=16, num_heads=2, edge_types=edge_types,
        node_types=["drug", "protein"], per_edge_type_out_proj=False,
    )
    sd_shared = attn_shared.state_dict()
    # Shared out_proj key MUST be present.
    assert "out_proj.weight" in sd_shared, \
        "Shared out_proj.weight must be in state_dict (backward compat)"
    # No per-edge-type keys in shared mode.
    per_edge_keys = [k for k in sd_shared if k.startswith("out_proj_per_edge_type")]
    assert len(per_edge_keys) == 0, \
        f"Shared mode must NOT have per-edge-type keys, got: {per_edge_keys}"

    # Per-edge-type mode.
    attn_per = HeterogeneousMultiHeadAttention(
        embedding_dim=16, num_heads=2, edge_types=edge_types,
        node_types=["drug", "protein"], per_edge_type_out_proj=True,
    )
    sd_per = attn_per.state_dict()
    # Shared out_proj key STILL present (for old-checkpoint load compat).
    assert "out_proj.weight" in sd_per, \
        "Shared out_proj.weight must be present even in per-edge-type mode (for old-checkpoint load)"
    # Per-edge-type keys present.
    per_edge_keys = [k for k in sd_per if k.startswith("out_proj_per_edge_type")]
    assert len(per_edge_keys) == len(edge_types), \
        f"Expected {len(edge_types)} per-edge-type keys, got {len(per_edge_keys)}: {per_edge_keys}"

    # Old checkpoint (shared mode) → new model (per-edge-type mode):
    # strict=True should FAIL (missing keys).
    try:
        attn_per.load_state_dict(sd_shared, strict=True)
        assert False, \
            "load_state_dict(strict=True) on shared-checkpoint → per-edge-type model " \
            "must FAIL (missing per-edge-type keys). This is the desired behavior: " \
            "silently initializing per-edge-type modules to zero would corrupt the model."
    except RuntimeError as e:
        # Expected: missing keys for out_proj_per_edge_type.*
        assert "out_proj_per_edge_type" in str(e), \
            f"Missing-key error must mention out_proj_per_edge_type, got: {e}"

    # Old checkpoint → shared model: strict=True should SUCCEED.
    attn_shared2 = HeterogeneousMultiHeadAttention(
        embedding_dim=16, num_heads=2, edge_types=edge_types,
        node_types=["drug", "protein"], per_edge_type_out_proj=False,
    )
    attn_shared2.load_state_dict(sd_shared, strict=True)  # should not raise


def test_p3_032_v119_model_level_flag_propagation():
    """P3-032 v119: DrugRepurposingGraphTransformer propagates the flag to layers."""
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}

    # Default model — shared out_proj.
    model_default = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims,
        embedding_dim=16,
        num_layers=2,
        num_heads=2,
        edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES),
        ffn_hidden_dim=32,
        min_edge_types=1,
    )
    assert model_default.per_edge_type_out_proj is False
    for i, layer in enumerate(model_default.graph_transformer_layers):
        assert layer.per_edge_type_out_proj is False, \
            f"Layer {i} should have per_edge_type_out_proj=False"
        assert layer.attention.per_edge_type_out_proj is False, \
            f"Layer {i} attention should have per_edge_type_out_proj=False"

    # Per-edge-type model.
    model_per = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims,
        embedding_dim=16,
        num_layers=2,
        num_heads=2,
        edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES),
        ffn_hidden_dim=32,
        min_edge_types=1,
        per_edge_type_out_proj=True,
    )
    assert model_per.per_edge_type_out_proj is True
    for i, layer in enumerate(model_per.graph_transformer_layers):
        assert layer.per_edge_type_out_proj is True, \
            f"Layer {i} should have per_edge_type_out_proj=True"
        assert layer.attention.per_edge_type_out_proj is True, \
            f"Layer {i} attention should have per_edge_type_out_proj=True"
        # Each layer's attention should have per-edge-type modules.
        assert len(layer.attention.out_proj_per_edge_type) == len(DEFAULT_EDGE_TYPES), \
            f"Layer {i} should have {len(DEFAULT_EDGE_TYPES)} per-edge-type modules"


if __name__ == "__main__":
    test_p3_032_v119_default_is_shared_out_proj()
    print("[PASS] test_p3_032_v119_default_is_shared_out_proj")
    test_p3_032_v119_explicit_flag_creates_per_edge_type_modules()
    print("[PASS] test_p3_032_v119_explicit_flag_creates_per_edge_type_modules")
    test_p3_032_v119_forward_pass_shared_mode()
    print("[PASS] test_p3_032_v119_forward_pass_shared_mode")
    test_p3_032_v119_forward_pass_per_edge_type_mode()
    print("[PASS] test_p3_032_v119_forward_pass_per_edge_type_mode")
    test_p3_032_v119_state_dict_keys_stable()
    print("[PASS] test_p3_032_v119_state_dict_keys_stable")
    test_p3_032_v119_model_level_flag_propagation()
    print("[PASS] test_p3_032_v119_model_level_flag_propagation")
    print("\nAll P3-032 v119 tests passed.")
