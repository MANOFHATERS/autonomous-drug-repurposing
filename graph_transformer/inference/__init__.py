"""Inference utilities for the Graph Transformer."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import torch

from ..data import LABEL_LEAKING_EDGES


@torch.no_grad()
def predict_drug_disease_scores(
    model: Any,
    node_features: Dict[str, torch.Tensor],
    edge_indices: Dict,
    drug_indices: torch.Tensor,
    disease_indices: torch.Tensor,
    batch_size: int = 1024,
    exclude_edges: Any = None,
    device: str = "cpu",
    apply_temperature: bool = True,
) -> np.ndarray:
    """Predict probability scores for a list of (drug, disease) pairs.

    Args:
        model: Trained DrugRepurposingGraphTransformer.
        node_features: Dict of node feature tensors.
        edge_indices: Dict of edge index tensors.
        drug_indices: (N,) drug node indices.
        disease_indices: (N,) disease node indices.
        batch_size: Batch size.
        exclude_edges: Edge types to exclude (defaults to LABEL_LEAKING_EDGES).
        device: Device.
        apply_temperature: If True, apply the link predictor's learned
            temperature (calibrated probabilities).

    Returns:
        (N,) numpy array of probabilities in [0, 1].
    """
    if exclude_edges is None:
        exclude_edges = set(LABEL_LEAKING_EDGES)

    # V90 ROOT FIX (BUG #19, P1): save prior training state and restore
    # in finally. The previous code called ``model.eval()`` and NEVER
    # restored training mode. If predict_drug_disease_scores was called
    # mid-training (by a background thread, an API server, or an
    # interactive notebook), it silently disabled dropout and BatchNorm
    # updates for the rest of the process.
    prior_training = model.training
    model.eval()
    try:
        model.to(device)
        nf = {k: v.to(device) for k, v in node_features.items()}
        ei = {k: v.to(device) for k, v in edge_indices.items()}

        all_probs: List[torch.Tensor] = []
        n = len(drug_indices)

        for start in range(0, n, batch_size):
            end = min(start + batch_size, n)
            d_idx = drug_indices[start:end].to(device)
            ds_idx = disease_indices[start:end].to(device)
            # V4 B-F5 fix: pass apply_temperature through to model.forward,
            # which applies sigmoid(logits / temperature). The original code
            # accepted apply_temperature but never used it (dead parameter).
            probs = model(
                nf, ei, d_idx, ds_idx,
                exclude_edges=exclude_edges,
                apply_temperature=apply_temperature,
            )
            all_probs.append(probs.cpu())

        return torch.cat(all_probs).numpy()
    finally:
        # V90 ROOT FIX (BUG #19): restore the prior training state.
        model.train(prior_training)


@torch.no_grad()
def top_k_novel_predictions(
    model: Any,
    node_features: Dict[str, torch.Tensor],
    edge_indices: Dict,
    drug_names: List[str],
    disease_names: List[str],
    known_pairs: List[Tuple[str, str]],
    top_k: int = 50,
    exclude_edges: Any = None,
    device: str = "cpu",
) -> List[Tuple[str, str, float]]:
    """Return the top-K highest-scoring NOVEL (drug, disease) pairs.

    "Novel" = (drug, disease) not in ``known_pairs``. This is what the
    V1 launch contract requires for the PubMed literature cross-check
    (Phase 6 DOCX: "We take the model's top 50 novel predictions").

    Args:
        model: Trained model.
        node_features: Node features dict.
        edge_indices: Edge indices dict.
        drug_names: List of all drug names (index = node index).
        disease_names: List of all disease names (index = node index).
        known_pairs: List of (drug_name, disease_name) tuples that are
            already known and should be excluded from the "novel" set.
        top_k: Number of top novel predictions to return.
        exclude_edges: Edge types to exclude (defaults to LABEL_LEAKING_EDGES).
        device: Device.

    Returns:
        List of (drug_name, disease_name, score) tuples, sorted by score desc.
    """
    num_drugs = len(drug_names)
    num_diseases = len(disease_names)

    # V31 ROOT FIX (P1-12 / Compound #10): pass apply_temperature=False
    # to match the RL training distribution. The audit found that
    # ``generate_rl_input`` uses ``apply_temperature=False`` (raw sigmoid,
    # full variance) for the RL training CSV, but ``top_k_novel_predictions``
    # used the default ``apply_temperature=True`` (calibrated, compressed
    # variance) for Phase 6 inference. The RL policy was trained on raw
    # scores but inferred on calibrated scores → out-of-distribution
    # features → unreliable Phase 6 rankings.
    #
    # The fix: use ``apply_temperature=False`` here so Phase 6's candidate
    # pool is scored with the SAME distribution the RL agent was trained
    # on. This ensures the RL policy operates on in-distribution features.
    score_matrix = model.predict_all_pairs(
        node_features, edge_indices,
        num_drugs=num_drugs, num_diseases=num_diseases,
        exclude_edges=exclude_edges,
        apply_temperature=False,  # V31 P1-12: match RL training distribution
    )  # (num_drugs, num_diseases) on device — raw sigmoid, same as RL training

    # Flatten and find top-K novel
    known_set = set((d.lower(), v.lower()) for d, v in known_pairs)
    flat_scores = score_matrix.cpu().numpy().flatten()
    flat_indices = np.argsort(-flat_scores)  # descending

    results: List[Tuple[str, str, float]] = []
    for flat_idx in flat_indices:
        d_idx = int(flat_idx // num_diseases)
        ds_idx = int(flat_idx % num_diseases)
        d_name = drug_names[d_idx]
        ds_name = disease_names[ds_idx]
        if (d_name.lower(), ds_name.lower()) in known_set:
            continue  # skip known positives
        results.append((d_name, ds_name, float(flat_scores[flat_idx])))
        if len(results) >= top_k:
            break

    return results
