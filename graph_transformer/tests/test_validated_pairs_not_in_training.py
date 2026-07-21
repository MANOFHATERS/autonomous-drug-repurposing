"""P3-008 standalone test: validated pairs NOT in GT training data.

This test verifies that the 4 validated hypothesis pairs (thalidomide→
multiple_myeloma, sildenafil→pah, mifepristone→cushing, topiramate→
migraine) are NOT injected as 'treats' edges in the GT training graph.

The previous code injected them, making them GT training data → the GT
model learned them → gnn_score for them was inflated → they appeared as
high-scoring NOVEL predictions in Phase 6 → "novel predictions are NOT
novel" (the exact bug the audit flagged in P3-008).
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_validated_pairs_not_in_treats_edges():
    """The 4 TRUE validated pairs must NOT be in the 'treats' edge index.

    The 'treats' edge index is what _compute_training_split uses to
    build GT training labels (see gt_rl_bridge.py:847-850). If a
    validated pair is in this edge index, the GT model is trained on
    it → it's not novel.

    NOTE: _get_validated_hypotheses() returns 8 pairs (4 true validated
    + 4 known positives like aspirin). Only the 4 TRUE validated pairs
    (the data-flywheel pairs, DOCX §10) should NOT be in 'treats' edges.
    The 4 known positives (aspirin, warfarin, etc.) SHOULD be in
    'treats' edges — they're real known treatments used for GT training.
    """
    # The 4 TRUE validated pairs (data flywheel, DOCX §10).
    TRUE_VALIDATED = [
        ("thalidomide", "multiple myeloma"),
        ("sildenafil", "pulmonary arterial hypertension"),
        ("mifepristone", "cushing syndrome"),
        ("topiramate", "migraine"),
    ]

    import tempfile
    from graph_transformer.gt_rl_bridge import GTRLBridge

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge = GTRLBridge(output_dir=tmpdir, device="cpu", seed=42)
        bridge.build_demo_graph(num_drugs=25, num_diseases=15)
        bridge.build_model()

        drug_map = bridge.node_maps.get("drug", {})
        disease_map = bridge.node_maps.get("disease", {})
        treats_ei = bridge.edge_indices.get(("drug", "treats", "disease"))

        if treats_ei is None or treats_ei.numel() == 0:
            pytest.skip("No 'treats' edges in graph")

        treats_set = {
            (int(s), int(t))
            for s, t in zip(treats_ei[0].tolist(), treats_ei[1].tolist())
        }

        leaked = []
        for drug_name, disease_name in TRUE_VALIDATED:
            drug_idx = drug_map.get(drug_name)
            disease_idx = disease_map.get(disease_name)
            if drug_idx is None or disease_idx is None:
                continue  # not in this demo graph
            if (drug_idx, disease_idx) in treats_set:
                leaked.append((drug_name, disease_name))

        assert not leaked, (
            f"P3-008: {len(leaked)} TRUE validated pairs are in the "
            f"'treats' edge index (GT training data): {leaked}. The "
            f"fix must NOT inject validated pairs as 'treats' edges."
        )


@pytest.mark.integration
def test_validated_pairs_still_in_known_pairs():
    """The 4 TRUE validated pairs MUST still be in known_pairs (for
    exclusion from novel predictions).

    The fix removes the 'treats' edge injection but KEEPS validated
    pairs in known_pairs so they're excluded from get_top_k_novel_predictions
    (they are KNOWN-validated per the data flywheel, DOCX §10).
    """
    TRUE_VALIDATED = [
        ("thalidomide", "multiple myeloma"),
        ("sildenafil", "pulmonary arterial hypertension"),
        ("mifepristone", "cushing syndrome"),
        ("topiramate", "migraine"),
    ]
    import tempfile
    from graph_transformer.gt_rl_bridge import GTRLBridge

    with tempfile.TemporaryDirectory() as tmpdir:
        bridge = GTRLBridge(output_dir=tmpdir, device="cpu", seed=42)
        bridge.build_demo_graph(num_drugs=25, num_diseases=15)
        bridge.build_model()

        known_set = set(bridge.known_pairs)
        missing = []
        for vp in TRUE_VALIDATED:
            if vp not in known_set:
                missing.append(vp)

        assert not missing, (
            f"P3-008: {len(missing)} TRUE validated pairs are NOT in "
            f"known_pairs: {missing}. They MUST be in known_pairs so "
            f"they're excluded from novel predictions (they are KNOWN-"
            f"validated per the data flywheel). The fix removes the "
            f"'treats' edge but must KEEP them in known_pairs."
        )
