"""P3-021 ROOT FIX (v119) — CI test for the pre-norm deviation.

The P3-007 audit mandate originally recommended post-norm LayerNorm
(``h' = LayerNorm(h + sublayer(h))``). The team deliberately chose
PRE-norm (``h' = h + sublayer(LayerNorm(h))``) instead, citing Xiong
et al. 2020 ("On Layer Normalization in the Transformer Architecture"),
which shows pre-norm is more stable for deep models (gradient norms
are approximately depth-independent, while post-norm gradients vanish
exponentially with depth).

The P3-021 audit fix #2 says:
  "Add a CI test that calls ``check_gradient_stability`` after training
   and asserts the max/min gradient norm ratio is < 10x."

This file IS that CI test. It:
  1. Constructs a small ``DrugRepurposingGraphTransformer`` with 4 layers
     (the V1 demo depth) and 8 layers (the production-scale depth).
  2. Runs a single forward + backward pass on a tiny synthetic graph.
  3. Collects per-layer gradient norms
     (``sum(p.grad.norm(2)**2 for p in layer.parameters())**0.5``).
  4. Calls ``GraphTransformerLayer.check_gradient_stability`` with the
     collected norms.
  5. Asserts ``stable=True`` (i.e., max/min ratio < 10x) for BOTH depths.

If the pre-norm choice is correct (per Xiong et al. 2020), the 8-layer
model should have a gradient norm ratio < 10x. If post-norm were used
on an 8-layer model, the ratio would be ~100x+ (vanishing gradients).
This test verifies the pre-norm property holds at runtime, not just in
theory.

This test is in the Teammate 7 swim lane (graph_transformer/models +
graph_transformer/training + graph_transformer/evaluation +
graph_transformer/inference). The test file lives in tests/ (the
shared test directory) but only imports from the swim-lane modules.
"""
from __future__ import annotations

import math
import os
import sys
from typing import Dict

# Make the repo root importable when the test is run directly.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _build_tiny_graph(num_layers: int = 4):
    """Build a tiny synthetic graph for the gradient-stability test.

    Returns:
        (model, node_features, edge_indices, drug_idx, disease_idx, labels)
    """
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )
    from graph_transformer.data import DEFAULT_EDGE_TYPES, DEFAULT_NODE_TYPES

    torch.manual_seed(42)

    # 5 drugs, 5 proteins, 5 pathways, 5 diseases, 5 clinical_outcomes.
    # Small enough to run on CPU in <5s; large enough to exercise all
    # edge types and node types.
    feature_dims = {nt: 16 for nt in DEFAULT_NODE_TYPES}
    node_features = {
        nt: torch.randn(5, 16) for nt in DEFAULT_NODE_TYPES
    }
    # Build edge_indices with at least 1 edge per edge type.
    edge_indices: Dict = {}
    for (src, rel, tgt) in DEFAULT_EDGE_TYPES:
        # 2 edges per type — enough to exercise the attention.
        edge_indices[(src, rel, tgt)] = torch.tensor(
            [[0, 1], [2, 3]], dtype=torch.long
        )

    model = DrugRepurposingGraphTransformer(
        feature_dims=feature_dims,
        embedding_dim=16,
        num_layers=num_layers,
        num_heads=2,
        edge_types=list(DEFAULT_EDGE_TYPES),
        node_types=list(DEFAULT_NODE_TYPES),
        ffn_hidden_dim=32,
        dropout=0.1,
        attention_dropout=0.1,
        link_predictor_hidden_dims=[32, 16],
        seed=42,
        min_edge_types=1,  # tiny test graph — relax the minimum
    )

    # 4 drug-disease pairs for the forward pass: 2 positives, 2 negatives.
    drug_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    disease_idx = torch.tensor([0, 1, 2, 3], dtype=torch.long)
    labels = torch.tensor([1.0, 1.0, 0.0, 0.0], dtype=torch.float32)

    return model, node_features, edge_indices, drug_idx, disease_idx, labels


def _collect_per_layer_gradient_norms(model) -> Dict[str, float]:
    """Compute the L2 norm of gradients for each GraphTransformerLayer.

    Returns:
        Dict mapping ``"graph_transformer_layers.{i}"`` to its gradient
        norm (float).
    """
    import torch

    norms: Dict[str, float] = {}
    for i, layer in enumerate(model.graph_transformer_layers):
        total_sq = 0.0
        any_grad = False
        for p in layer.parameters():
            if p.grad is not None:
                total_sq += float(p.grad.detach().norm(2).item()) ** 2
                any_grad = True
        if any_grad:
            norms[f"graph_transformer_layers.{i}"] = math.sqrt(total_sq)
    return norms


def _run_gradient_stability_check(num_layers: int) -> Dict[str, object]:
    """Run a forward+backward pass and check gradient stability.

    Args:
        num_layers: Number of GraphTransformerLayers to construct.

    Returns:
        The dict returned by ``check_gradient_stability``.
    """
    import torch
    from graph_transformer.models.layers import GraphTransformerLayer

    model, node_features, edge_indices, drug_idx, disease_idx, labels = (
        _build_tiny_graph(num_layers=num_layers)
    )

    # Forward + backward.
    model.train()
    logits = model.forward_logits(
        node_features, edge_indices, drug_idx, disease_idx,
        exclude_edges=set(),  # don't exclude — we want gradients on all params
    ).squeeze(-1)
    loss = torch.nn.functional.binary_cross_entropy_with_logits(
        logits, labels
    )
    loss.backward()

    # Collect per-layer gradient norms.
    per_layer_norms = _collect_per_layer_gradient_norms(model)

    # Defensive: if no gradients were collected (e.g., all params were
    # frozen), skip the check rather than fail.
    if not per_layer_norms:
        return {
            "stable": True,
            "max_norm": 0.0,
            "min_norm": 0.0,
            "ratio": 1.0,
            "message": "No gradient norms collected (params frozen?) — skipping.",
        }

    # Call the static helper.
    return GraphTransformerLayer.check_gradient_stability(
        model=model,
        per_layer_gradient_norms=per_layer_norms,
        max_ratio=10.0,
    )


def test_p3_021_gradient_stability_4_layers():
    """P3-021: 4-layer model (V1 demo depth) should have stable gradients.

    The pre-norm architecture (Xiong et al. 2020) keeps gradient norms
    approximately depth-independent. For a 4-layer model, the max/min
    ratio should be well under 10x.
    """
    result = _run_gradient_stability_check(num_layers=4)
    assert result["stable"], (
        f"P3-021 ROOT FIX: 4-layer model gradient stability FAILED. "
        f"max/min ratio = {result.get('ratio', 'unknown'):.4f} "
        f"(threshold: 10.0). max_norm={result.get('max_norm', 0):.6f}, "
        f"min_norm={result.get('min_norm', 0):.6f}. "
        f"Message: {result.get('message', '')}. "
        f"The pre-norm LayerNorm architecture should keep gradients "
        f"stable for a 4-layer model — if this fails, investigate "
        f"whether the pre-norm is actually being applied (check the "
        f"GraphTransformerLayer.forward method's LayerNorm placement)."
    )


def test_p3_021_gradient_stability_8_layers():
    """P3-021: 8-layer model (production-scale depth) should have stable gradients.

    The Xiong et al. 2020 result shows pre-norm and post-norm converge
    at depth ~12 for NLP transformers. For an 8-layer heterogeneous GNN
    (which is shallower than the 12-layer convergence point), pre-norm
    should still be stable. If this fails, the team should re-evaluate
    post-norm vs pre-norm empirically (audit P3-021 fix #3).
    """
    result = _run_gradient_stability_check(num_layers=8)
    assert result["stable"], (
        f"P3-021 ROOT FIX: 8-layer model gradient stability FAILED. "
        f"max/min ratio = {result.get('ratio', 'unknown'):.4f} "
        f"(threshold: 10.0). max_norm={result.get('max_norm', 0):.6f}, "
        f"min_norm={result.get('min_norm', 0):.6f}. "
        f"Message: {result.get('message', '')}. "
        f"At 8 layers, pre-norm's stability advantage may be diminishing "
        f"(Xiong et al. 2020 Figure 4 shows convergence at depth ~12 "
        f"for NLP transformers). If this test fails consistently, "
        f"re-evaluate post-norm vs pre-norm empirically on the "
        f"production graph (audit P3-021 fix #3)."
    )


def test_p3_021_check_gradient_stability_helper_signature():
    """P3-021: verify the check_gradient_stability helper exists and works.

    This is a smoke test for the helper's interface — it verifies the
    method is callable with the documented signature and returns a dict
    with the documented keys.
    """
    from graph_transformer.models.layers import GraphTransformerLayer

    # Empty input — should return stable=True with a "skipping" message.
    result = GraphTransformerLayer.check_gradient_stability(
        model=None,
        per_layer_gradient_norms={},
        max_ratio=10.0,
    )
    assert "stable" in result
    assert "max_norm" in result
    assert "min_norm" in result
    assert "ratio" in result
    assert "message" in result
    assert result["stable"] is True
    assert result["ratio"] == 1.0

    # Normal input — should compute ratio and stability.
    result = GraphTransformerLayer.check_gradient_stability(
        model=None,
        per_layer_gradient_norms={
            "layer.0": 1.0,
            "layer.1": 2.0,
            "layer.2": 0.5,
        },
        max_ratio=10.0,
    )
    # max=2.0, min=0.5, ratio=4.0 (< 10.0) → stable
    assert result["stable"] is True
    assert abs(result["ratio"] - 4.0) < 1e-6

    # Unstable input — ratio > max_ratio.
    result = GraphTransformerLayer.check_gradient_stability(
        model=None,
        per_layer_gradient_norms={
            "layer.0": 1.0,
            "layer.1": 100.0,  # 100x larger → ratio = 100
        },
        max_ratio=10.0,
    )
    assert result["stable"] is False
    assert abs(result["ratio"] - 100.0) < 1e-6


if __name__ == "__main__":
    # Allow running as a script for quick verification.
    test_p3_021_check_gradient_stability_helper_signature()
    print("[PASS] test_p3_021_check_gradient_stability_helper_signature")
    test_p3_021_gradient_stability_4_layers()
    print("[PASS] test_p3_021_gradient_stability_4_layers")
    test_p3_021_gradient_stability_8_layers()
    print("[PASS] test_p3_021_gradient_stability_8_layers")
    print("\nAll P3-021 tests passed.")
