"""Evaluation utilities for the Graph Transformer."""
from __future__ import annotations

from typing import Any, Dict

import numpy as np
import torch


@torch.no_grad()
def evaluate_link_prediction(
    model: Any,
    node_features: Dict[str, torch.Tensor],
    edge_indices: Dict,
    drug_indices: torch.Tensor,
    disease_indices: torch.Tensor,
    labels: torch.Tensor,
    batch_size: int = 1024,
    exclude_edges: Any = None,
    device: str = "cpu",
    apply_temperature: bool = True,
) -> Dict[str, float]:
    """Evaluate link-prediction AUC + accuracy on a set of pairs.

    ROOT FIX (E18): the original code applied ``torch.sigmoid(logits)``
    to get probabilities, but did NOT apply temperature scaling. This
    was inconsistent with ``predict_probability`` in link_predictor.py
    which DOES apply temperature. The E18 fix adds the
    ``apply_temperature`` parameter (default True for consistency with
    ``predict_drug_disease_scores``) and applies temperature via the
    model's ``forward`` method instead of ``forward_logits`` + manual
    sigmoid.

    ROOT FIX (E19): the original code didn't accept ``apply_temperature``
    as a parameter, while ``predict_drug_disease_scores`` did. This made
    the API inconsistent. The E19 fix adds the parameter with the same
    default (True) as ``predict_drug_disease_scores`` for consistency.

    ROOT FIX (S-09): DOCUMENT that ``apply_temperature`` has NO EFFECT
    on AUC. AUC measures RANKING quality — it computes the probability
    that a randomly chosen positive is ranked above a randomly chosen
    negative. Temperature scaling is MONOTONIC (sigmoid(logits/T)
    preserves order), so the ranking is unchanged, so the AUC is
    unchanged. The audit's finding S-09 was that the previous code
    implied the parameter affected AUC (it doesn't — it only affects
    ACCURACY, which uses a fixed 0.5 threshold).

    Concretely:
      - ``auc(apply_temperature=True) == auc(apply_temperature=False)``
        (AUC is invariant to monotonic transforms)
      - ``accuracy(apply_temperature=True) != accuracy(apply_temperature=False)``
        (accuracy depends on the 0.5 threshold, which is sensitive to
        temperature compression/sharpening)

    The parameter is RETAINED for backward-compatibility and because it
    DOES affect accuracy (which is also returned). But callers should
    understand: do NOT toggle this parameter expecting AUC to change.

    ROOT FIX (FORENSIC-AUDIT-I02): the previous code called
    ``model.forward_logits(...)`` (which internally calls ``encode``)
    and then, when ``apply_temperature=True``, called
    ``model.forward(...)`` (which ALSO internally calls ``encode``).
    This ran the Graph Transformer encoder TWICE per batch, doubling
    evaluation compute. 3 batches → 6 encoder calls (should be 3).

    The root fix calls ``model.encode(...)`` ONCE per evaluation (not
    per batch — the encoder processes the ENTIRE graph, so it only needs
    to run once for all pairs). Then, for each batch, it extracts the
    drug/disease embeddings and calls ``link_predictor.forward_logits``
    and ``link_predictor.forward`` directly on those embeddings — no
    redundant encoding.

    This reduces encoder calls from ``2 * n_batches`` to ``1`` total,
    cutting evaluation compute by ~50% on large datasets.

    Args:
        model: Trained DrugRepurposingGraphTransformer.
        node_features: Dict of node feature tensors.
        edge_indices: Dict of edge index tensors.
        drug_indices: (N,) drug node indices.
        disease_indices: (N,) disease node indices.
        labels: (N,) binary labels.
        batch_size: Batch size.
        exclude_edges: Edge types to exclude (defaults to LABEL_LEAKING_EDGES).
        device: Device.
        apply_temperature: If True (default), apply the link predictor's
            learned temperature (calibrated probabilities). If False, use
            raw sigmoid (uncalibrated). Consistent with
            predict_drug_disease_scores (E19 fix).

    Returns:
        Dict with 'auc', 'accuracy', 'loss'.
    """
    from sklearn.metrics import roc_auc_score, accuracy_score
    import torch.nn as nn

    from ..data import LABEL_LEAKING_EDGES

    if exclude_edges is None:
        exclude_edges = set(LABEL_LEAKING_EDGES)

    # V90 ROOT FIX (BUG #19, P1): save the prior training state and
    # restore it in a finally block. The previous code called
    # ``model.eval()`` and NEVER restored training mode. If this
    # function was called mid-training (by a background thread, an
    # API server, or an interactive notebook), it silently disabled
    # dropout and BatchNorm updates for the rest of the process.
    prior_training = model.training
    model.eval()
    try:
        model.to(device)
        nf = {k: v.to(device) for k, v in node_features.items()}
        ei = {k: v.to(device) for k, v in edge_indices.items()}

        # ROOT FIX (FORENSIC-AUDIT-I02): encode the graph ONCE for ALL pairs.
        # The encoder processes the entire graph through the Graph Transformer
        # layers, producing node embeddings. This is the expensive operation
        # (O(num_layers * num_edges * embedding_dim)). The previous code ran
        # it 2x per batch (once inside forward_logits, once inside forward).
        # Now we run it exactly ONCE for the entire evaluation call.
        embeddings = model.encode(
            nf, ei,
            exclude_edges_override=set(exclude_edges),
        )
        drug_emb_all = embeddings["drug"]
        disease_emb_all = embeddings["disease"]

        all_probs = []
        # V90 ROOT FIX (BUG #27, P1): use a FRESH BCEWithLogitsLoss()
        # (no pos_weight) to match trainer.evaluate's _eval_criterion
        # (BUG #26 fix). The previous code used a fresh criterion here
        # while trainer.evaluate used the pos_weighted criterion,
        # producing different loss values for the same data. The
        # bridge's C-4 fix compared test_auc (trainer) vs
        # test_auc_verified (this function) but the LOSS values were
        # computed with different criteria. Now both use unweighted
        # BCEWithLogitsLoss, so losses are comparable.
        criterion = nn.BCEWithLogitsLoss()
        total_loss = 0.0
        n_samples = len(labels)

        for start in range(0, n_samples, batch_size):
            end = min(start + batch_size, n_samples)
            d_idx = drug_indices[start:end].to(device)
            ds_idx = disease_indices[start:end].to(device)
            batch_labels = labels[start:end].float().to(device)

            # ROOT FIX (FORENSIC-AUDIT-I02): extract embeddings for this
            # batch's drug/disease indices directly from the pre-computed
            # embeddings. NO redundant encode() call.
            drug_emb_batch = drug_emb_all[d_idx]
            disease_emb_batch = disease_emb_all[ds_idx]

            # Compute raw logits for the loss (BCEWithLogitsLoss is stable).
            # link_predictor.forward_logits does NOT call encode — it only
            # runs the MLP on the provided embeddings.
            logits = model.link_predictor.forward_logits(
                drug_emb_batch, disease_emb_batch
            ).squeeze(-1)
            loss = criterion(logits, batch_labels)
            total_loss += loss.item()

            # ROOT FIX (FORENSIC-AUDIT-I02 + E18): compute probabilities
            # from the SAME embeddings (no second encode call).
            # link_predictor.forward applies temperature when
            # apply_temperature=True. This is consistent with
            # predict_probability and predict_drug_disease_scores.
            if apply_temperature:
                probs = model.link_predictor.forward(
                    drug_emb_batch, disease_emb_batch,
                    apply_temperature=True,
                ).squeeze(-1).cpu()
            else:
                probs = torch.sigmoid(logits).cpu()
            all_probs.append(probs)

        all_probs = torch.cat(all_probs).numpy()
        # V90 ROOT FIX (BUG #12, P1): use labels.detach().cpu().numpy()
        # instead of labels.numpy(). The previous code crashed if labels
        # was on CUDA: ``TypeError: can't convert cuda:0 device type
        # tensor to numpy``. The bridge wrapped this in a try/except
        # and logged a warning, so test_auc_verified was silently None
        # — and the scientific_validation gate fell back to the
        # trainer's AUC (which may be inflated). The "verified AUC"
        # feature was theater on CUDA.
        all_labels = labels.detach().cpu().numpy()

        pred_binary = (all_probs > 0.5).astype(int)
        accuracy = float(accuracy_score(all_labels, pred_binary))

        if len(np.unique(all_labels)) < 2:
            auc = 0.5
        else:
            try:
                auc = float(roc_auc_score(all_labels, all_probs))
            except ValueError:
                auc = 0.5

        avg_loss = total_loss / max(1, (n_samples + batch_size - 1) // batch_size)
        return {"loss": avg_loss, "auc": auc, "accuracy": accuracy}
    finally:
        # V90 ROOT FIX (BUG #19): restore the prior training state.
        model.train(prior_training)
