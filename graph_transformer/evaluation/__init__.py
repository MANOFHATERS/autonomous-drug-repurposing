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

    P3-017 ROOT FIX: SINGLE-ENCODE evaluation path. The previous
    implementation (V90 BUG #36 "genuinely independent path") called
    ``model.forward_logits`` per batch (which internally encodes the
    graph) and then ``model.forward`` per batch (which AGAIN encodes
    internally) — TWO full graph encodings per batch. The encode is
    the most expensive op in the pipeline (4-layer transformer over
    the full graph), so this roughly DOUBLED eval compute.

    The P3-017 fix encodes the graph exactly ONCE (via ``model.encode``),
    extracts the drug and disease embedding tables, then calls
    ``link_predictor.forward_logits`` and ``link_predictor.forward``
    directly on the pre-computed per-batch embeddings. This matches
    the trainer's ``evaluate()`` pattern (W-06 fix) and makes this
    function O(layers * edges * dim + batches) instead of
    O(layers * edges * dim * batches * 2).

    The "verified AUC" still provides cross-check value because:
      1. It uses a FRESH ``nn.BCEWithLogitsLoss()`` (no pos_weight),
         matching the trainer's _eval_criterion (BUG #26 fix).
      2. It re-applies the link_predictor's temperature via
         ``link_predictor.forward(apply_temperature=True)`` — if
         temperature calibration is wrong, the verified accuracy
         will diverge from the trainer's accuracy.
      3. The independent code path (this function vs trainer's
         evaluate) catches integration bugs in either caller.

    ROOT FIX (E18): the original code applied ``torch.sigmoid(logits)``
    to get probabilities, but did NOT apply temperature scaling. This
    was inconsistent with ``predict_probability`` in link_predictor.py
    which DOES apply temperature. The E18 fix adds the
    ``apply_temperature`` parameter (default True for consistency with
    ``predict_drug_disease_scores``) and applies temperature via the
    link_predictor's ``forward`` method instead of manual sigmoid.

    ROOT FIX (S-09): DOCUMENT that ``apply_temperature`` has NO EFFECT
    on AUC. AUC measures RANKING quality — it computes the probability
    that a randomly chosen positive is ranked above a randomly chosen
    negative. Temperature scaling is MONOTONIC (sigmoid(logits/T)
    preserves order), so the ranking is unchanged, so the AUC is
    unchanged. The audit's finding S-09 was that the previous code
    implied the parameter affected AUC (it doesn't — it only affects
    ACCURACY, which uses a fixed 0.5 threshold).

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
            predict_drug_disease_scores (E19 fix). NOTE: this has NO
            EFFECT on AUC (AUC is invariant to monotonic transforms).

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
        # ROOT FIX (v92): the file previously contained TWO parallel
        # implementations mashed together — a legacy path that ended
        # with an ``if`` statement and NO body, followed by a newer
        # per-batch path at the WRONG indent level (outside the ``try``
        # block). This caused ``compileall`` to fail with IndentationError,
        # breaking CI's build job for every PR. The fix below is the
        # SINGLE canonical implementation (P3-017: single encode + direct
        # link_predictor calls on pre-computed embeddings).
        model.to(device)
        nf = {k: v.to(device) for k, v in node_features.items()}
        ei = {k: v.to(device) for k, v in edge_indices.items()}

        # P3-017 ROOT FIX: encode the graph ONCE for the entire evaluation
        # (not twice per batch). The previous code called the model-level
        # forward per batch (which internally encodes the graph) and then
        # called it AGAIN per batch for probabilities — TWO full graph
        # encodings per batch. The encode is the most expensive op in the
        # pipeline (4-layer transformer over the full graph), so this
        # roughly DOUBLED eval compute.
        #
        # The fix encodes the graph exactly ONCE, extracts the drug and
        # disease embedding tables, then calls
        # ``link_predictor.forward_logits`` and ``link_predictor.forward``
        # directly on the pre-computed per-batch embeddings. This matches
        # the trainer's ``evaluate()`` pattern (W-06 fix) and makes
        # ``evaluate_link_prediction`` O(layers * edges * dim + batches)
        # instead of O(layers * edges * dim * batches * 2).
        #
        # The "verified AUC" still provides cross-check value because:
        #   1. It uses a FRESH ``nn.BCEWithLogitsLoss()`` (no pos_weight),
        #      matching the trainer's _eval_criterion (BUG #26 fix).
        #   2. It re-applies the link_predictor's temperature via
        #      ``link_predictor.forward(apply_temperature=True)`` — if
        #      temperature calibration is wrong, the verified accuracy
        #      will diverge from the trainer's accuracy.
        #   3. The independent code path (this function vs trainer's
        #      evaluate) catches integration bugs in either caller.
        embeddings = model.encode(
            nf, ei,
            exclude_edges_override=set(exclude_edges),
        )
        drug_emb_all = embeddings["drug"]
        disease_emb_all = embeddings["disease"]

        all_probs = []
        # V90 ROOT FIX (BUG #27, P1): use a FRESH BCEWithLogitsLoss()
        # (no pos_weight) to match trainer.evaluate's _eval_criterion
        # (BUG #26 fix). Both paths now use unweighted BCEWithLogitsLoss.
        criterion = nn.BCEWithLogitsLoss()
        total_loss = 0.0
        n_samples = len(labels)

        for start in range(0, n_samples, batch_size):
            end = min(start + batch_size, n_samples)
            d_idx = drug_indices[start:end].to(device)
            ds_idx = disease_indices[start:end].to(device)
            batch_labels = labels[start:end].float().to(device)

            # Index into the pre-computed embeddings (NO redundant encode).
            drug_emb_batch = drug_emb_all[d_idx]
            disease_emb_batch = disease_emb_all[ds_idx]

            # P3-017: call link_predictor methods directly on the
            # pre-computed batch embeddings (no encode call inside).
            logits = model.link_predictor.forward_logits(
                drug_emb_batch, disease_emb_batch,
            ).squeeze(-1)
            loss = criterion(logits, batch_labels)
            total_loss += loss.item()

            # Compute probabilities from the SAME pre-computed embeddings.
            # Apply temperature if requested (AUC is invariant to monotonic
            # transforms, but probabilities are used for accuracy).
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
        # was on CUDA.
        all_labels = labels.detach().cpu().numpy()

        pred_binary = (all_probs > 0.5).astype(int)
        accuracy = float(accuracy_score(all_labels, pred_binary))

        if len(np.unique(all_labels)) < 2:
            logger.warning(
                "evaluate_link_prediction: only one class in labels "
                f"({np.unique(all_labels)}). AUC is undefined; returning 0.5."
            )
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
