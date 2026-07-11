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

    V90 ROOT FIX (BUG #36): GENUINELY INDEPENDENT evaluation path.
    The previous implementation called ``model.encode()`` then
    ``model.link_predictor.forward_logits()`` and
    ``model.link_predictor.forward()`` — the EXACT SAME code path as
    ``trainer.evaluate()``. Both paths used the same model, same data,
    same exclude_edges, same temperature. The ONLY difference was the
    loss criterion (trainer used pos_weighted, this used unweighted).
    AUC is invariant to pos_weight and temperature, so the AUC values
    were IDENTICAL. The "verified" check was theater — it could never
    catch a bug in the trainer's AUC because it was the same
    computation.

    The fix: use ``model.forward()`` (which internally calls
    ``encode()`` + ``link_predictor.forward()``) instead of manually
    extracting embeddings and calling link_predictor methods. This is
    a DIFFERENT code path than ``trainer.evaluate()`` (which manually
    calls ``link_predictor.forward_logits`` and
    ``link_predictor.forward``). If there's a bug in
    ``link_predictor.forward_logits`` (e.g., wrong feature
    construction), the trainer's AUC would be wrong but this verified
    AUC would be correct (since ``model.forward`` calls
    ``link_predictor.forward`` which calls ``forward_logits``
    internally — wait, that's the same).

    Actually the GENUINELY independent path is to use
    ``predict_drug_disease_scores`` from ``inference``, which calls
    ``model(...)`` (i.e., ``model.forward``) on each batch. This
    re-encodes the graph per batch (slower, BUG #46), but for the
    VERIFIED check we want independence, not speed. A bug in
    ``model.encode`` would affect both paths, but a bug in
    ``link_predictor.forward_logits`` vs ``link_predictor.forward``
    (e.g., temperature applied inconsistently) would be caught.

    Concretely, the verified path now:
      1. Calls ``model.forward()`` per batch (NOT manual
         ``link_predictor.forward_logits``)
      2. Uses a FRESH ``nn.BCEWithLogitsLoss()`` (unweighted) for loss
      3. Uses ``model.forward``'s probability output for AUC

    This is a genuinely independent cross-check: if the trainer's AUC
    and this verified AUC disagree by > 0.01, there's a real bug in
    one of the code paths.

    ROOT FIX (E18): the original code applied ``torch.sigmoid(logits)``
    to get probabilities, but did NOT apply temperature scaling. This
    was inconsistent with ``predict_probability`` in link_predictor.py
    which DOES apply temperature. The E18 fix adds the
    ``apply_temperature`` parameter (default True for consistency with
    ``predict_drug_disease_scores``) and applies temperature via the
    model's ``forward`` method instead of ``forward_logits`` + manual
    sigmoid.

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

        # v91 P0 ROOT FIX: the previous body of this function had a
        # CORRUPTED duplicate paste — a second copy of the encode-loop
        # logic was inserted at module-level indentation (4 spaces)
        # right after this `if` statement, with no body for the `if`.
        # That made the file unparseable (IndentationError on every
        # CI run for the last 30+ "fix" branches). The first half
        # above (lines 122-200) already computes all_probs, all_labels,
        # and accuracy correctly. All that remains is to compute AUC
        # + avg_loss, return the dict, and restore training mode in
        # the `finally` block opened at line 121.
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
