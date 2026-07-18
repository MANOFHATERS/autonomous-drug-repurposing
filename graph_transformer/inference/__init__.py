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

    V90 ROOT FIX (BUG #46): encode the graph ONCE, then extract per-batch
    embeddings. The previous code called ``model(...)`` (i.e.,
    ``model.forward(...)``) per batch, which internally calls
    ``model.encode(...)`` (the expensive Graph Transformer forward pass)
    for EVERY batch. For N batches, this ran N full graph encodings
    instead of 1. On a 10K-node graph with 100 batches, this was 100x
    slower than necessary.

    The fix mirrors ``trainer.evaluate()`` and ``evaluate_link_prediction()``:
    encode the graph ONCE at the start, then for each batch extract the
    drug/disease embeddings via indexing and call
    ``link_predictor.forward()`` directly (no encode call). This reduces
    encoder calls from N_batches to 1, cutting inference compute by
    ~N_batches×.

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
    #
    # V91 ROOT FIX (dead code removal): the previous edit prepended a
    # BUG #19 try/finally block but LEFT the old per-batch-encode body
    # as the executing path (calling model(...) per batch, which re-
    # encodes the graph every batch -- BUG #46 NOT actually fixed), and
    # appended the encode-once optimization as DEAD CODE after the
    # finally (unreachable because the try block returns). This combined
    # both defects: BUG #46 was never in effect, AND 35 lines of dead
    # code misled reviewers. The fix below merges both: the encode-once
    # optimization runs INSIDE the try/finally, so BUG #19 (save/restore
    # training mode) AND BUG #46 (encode once) are both live.
    #
    # P3-014 ROOT FIX (v119 forensic, THREAD-SAFE INFERENCE): the V90
    # BUG #19 fix used ``model.eval()`` / ``model.train(prior_training)``
    # to save/restore the training mode. This is the SAME racy pattern
    # that P3-014 flagged in ``predict_all_pairs``: ``nn.Module.training``
    # is SHARED MUTABLE STATE across all threads. Under concurrent
    # inference (V1 contract: 100 concurrent API requests to the
    # /api/predict endpoint, which calls this function via the service
    # layer), a concurrent training thread's ``model.train()`` call
    # could be silently overwritten by this function's
    # ``model.train(prior_training=False)`` restore, leaving the model
    # in eval mode (dropout disabled, BatchNorm frozen) for the rest
    # of the epoch.
    #
    # ROOT FIX (v119): do NOT toggle ``model.eval()`` /
    # ``model.train(prior_training)`` inside this function. The
    # ``@torch.no_grad()`` decorator already disables gradient
    # computation (per-thread, thread-safe). Callers that need
    # eval-mode behavior (dropout off, BN using running stats) MUST
    # call ``model.eval()`` BEFORE invoking this function -- the
    # standard PyTorch inference contract (identical to
    # ``predict_all_pairs`` and ``predict_all_pairs_dual``). The Phase
    # 5 API service sets ``model.eval()`` once at startup after
    # loading the checkpoint. For the rare mid-epoch-inference case
    # (training thread calls this mid-epoch), the caller MUST use a
    # separate model replica. This is the standard PyTorch guidance
    # for concurrent inference + training.
    model.to(device)
    nf = {k: v.to(device) for k, v in node_features.items()}
    ei = {k: v.to(device) for k, v in edge_indices.items()}

    # V90 BUG #46: encode the graph ONCE for ALL pairs (not per batch).
    # The encoder processes the entire graph through the Graph Transformer
    # layers, producing node embeddings. This is the expensive operation.
    # Calling model(...) per batch re-encodes every batch, wasting
    # N_batches × compute. Encode once, then index per batch.
    embeddings = model.encode(
        nf, ei,
        exclude_edges_override=set(exclude_edges),
    )
    drug_emb_all = embeddings["drug"]
    disease_emb_all = embeddings["disease"]

    all_probs: List[torch.Tensor] = []
    n = len(drug_indices)

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        d_idx = drug_indices[start:end].to(device)
        ds_idx = disease_indices[start:end].to(device)
        # V90 BUG #46: extract per-batch embeddings via indexing (NO
        # redundant encode call). Then call link_predictor.forward
        # directly with apply_temperature (V4 B-F5 fix preserved).
        drug_emb_batch = drug_emb_all[d_idx]
        disease_emb_batch = disease_emb_all[ds_idx]
        probs = model.link_predictor.forward(
            drug_emb_batch, disease_emb_batch,
            apply_temperature=apply_temperature,
        ).squeeze(-1)
        all_probs.append(probs.cpu())

    return torch.cat(all_probs).numpy()


@torch.no_grad()
def predict_drug_disease_scores_dual(
    model: Any,
    node_features: Dict[str, torch.Tensor],
    edge_indices: Dict,
    drug_indices: torch.Tensor,
    disease_indices: torch.Tensor,
    batch_size: int = 1024,
    exclude_edges: Any = None,
    device: str = "cpu",
) -> Tuple[np.ndarray, np.ndarray]:
    """P3-040 ROOT FIX (v120 forensic, hostile-auditor): predict BOTH raw
    and temperature-calibrated scores for a list of (drug, disease) pairs
    in a SINGLE encode pass.

    The previous ``get_top_k_novel_predictions`` code in
    ``gt_rl_bridge.py`` called ``predict_drug_disease_scores`` TWICE —
    once with ``apply_temperature=False`` (raw sigmoid) and once with
    ``apply_temperature=True`` (calibrated). Each call re-ran the
    expensive Graph Transformer encoder (``model.encode(...)``), which is
    the dominant inference cost (~30 s on a V100 for a 10K-drug graph).
    The two calls produce IDENTICAL logits — they differ ONLY in the
    final ``sigmoid(logits)`` vs ``sigmoid(logits / T)`` step. Calling
    the encoder twice doubled the inference compute for ZERO scientific
    benefit. The previous "ROOT FIX" comment in gt_rl_bridge.py
    (line 5408) claimed "call predict_drug_disease_scores TWICE" was the
    fix — that comment was a LIE. The user's audit ("comments and tests
    are fakes ... when I manually check code it's 100 percent broken")
    was dead right: the comment described the BUG as the FIX.

    ROOT FIX: this function encodes the graph ONCE, then for each batch
    computes ``logits = link_predictor.forward_logits(...)`` ONCE and
    applies BOTH transforms to the same logits tensor:
      - raw       = sigmoid(logits)            (apply_temperature=False)
      - calibrated = sigmoid(logits / T_mean)  (apply_temperature=True)

    This halves the encoder cost and is mathematically identical to the
    two-call version (the encoder is deterministic under ``@torch.no_grad``
    + ``model.eval()``).

    Args:
        model: Trained DrugRepurposingGraphTransformer (caller MUST set
            ``model.eval()`` before calling — see P3-014 v119 fix).
        node_features: Dict of node feature tensors.
        edge_indices: Dict of edge index tensors.
        drug_indices: (N,) drug node indices.
        disease_indices: (N,) disease node indices.
        batch_size: Batch size.
        exclude_edges: Edge types to exclude (defaults to LABEL_LEAKING_EDGES).
        device: Device.

    Returns:
        Tuple ``(raw_scores, calibrated_scores)`` — each is a (N,) numpy
        array of probabilities in [0, 1]. ``raw_scores[i]`` is the raw
        sigmoid (no temperature); ``calibrated_scores[i]`` is the
        temperature-scaled probability (Guo et al. 2017).
    """
    if exclude_edges is None:
        exclude_edges = set(LABEL_LEAKING_EDGES)

    model.to(device)
    nf = {k: v.to(device) for k, v in node_features.items()}
    ei = {k: v.to(device) for k, v in edge_indices.items()}

    # SINGLE encode pass — the expensive Graph Transformer forward.
    embeddings = model.encode(
        nf, ei,
        exclude_edges_override=set(exclude_edges),
    )
    drug_emb_all = embeddings["drug"]
    disease_emb_all = embeddings["disease"]

    # Precompute the temperature mean ONCE (the link_predictor.forward
    # method uses the mean of the per-class temperatures at inference
    # time when labels are unknown — see link_predictor.py line 491).
    # We replicate that logic here so the calibrated scores match what
    # ``predict_probability(apply_temperature=True)`` would produce.
    link_pred = model.link_predictor
    t_clamped = link_pred.temperature.clamp(
        min=link_pred.TEMPERATURE_CLAMP_MIN,
        max=link_pred.TEMPERATURE_CLAMP_MAX,
    )
    t_mean = t_clamped.mean()

    raw_probs: List[torch.Tensor] = []
    calibrated_probs: List[torch.Tensor] = []
    n = len(drug_indices)

    for start in range(0, n, batch_size):
        end = min(start + batch_size, n)
        d_idx = drug_indices[start:end].to(device)
        ds_idx = disease_indices[start:end].to(device)
        drug_emb_batch = drug_emb_all[d_idx]
        disease_emb_batch = disease_emb_all[ds_idx]
        # Compute logits ONCE per batch (the MLP forward through the
        # link predictor). Both raw and calibrated scores derive from
        # the SAME logits — only the final sigmoid differs.
        logits = link_pred.forward_logits(drug_emb_batch, disease_emb_batch)
        raw_probs.append(torch.sigmoid(logits).squeeze(-1).cpu())
        calibrated_probs.append(
            torch.sigmoid(logits / t_mean).squeeze(-1).cpu()
        )

    raw_arr = torch.cat(raw_probs).numpy()
    calibrated_arr = torch.cat(calibrated_probs).numpy()
    return raw_arr, calibrated_arr


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

    # P3-014 ROOT FIX (v119 forensic, THREAD-SAFE INFERENCE): set
    # ``model.eval()`` ONCE before calling ``predict_all_pairs_dual``.
    # The P3-014 fix removed the racy ``self.eval()`` /
    # ``self.train(prior_training)`` toggle from ``predict_all_pairs_dual``
    # (it was a race condition under concurrent inference). The new
    # contract requires the CALLER to set eval mode. We do that here,
    # with a save/restore pattern that is safe because
    # ``top_k_novel_predictions`` is called by the Phase 6 literature
    # cross-check (single-threaded, NOT concurrent). Under the V1
    # contract's concurrent API path, the service layer sets
    # ``model.eval()`` once at startup and never toggles it -- this
    # function is not in the hot path.
    _prior_training = model.training
    model.eval()
    try:
        # P3-040 + P3-004 ROOT FIX (v113 forensic): use the new
        # ``predict_all_pairs_dual`` method to compute BOTH raw and
        # calibrated scores in a SINGLE encode pass. The previous code
        # called ``predict_all_pairs`` once with apply_temperature=False
        # (raw sigmoid) -- this was already efficient (single encode),
        # but it wrote the RAW sigmoid to ``gnn_score``, which the RL
        # reward function reads. Temperature calibration was dead for
        # Phase 6.
        #
        # The fix: use ``predict_all_pairs_dual`` (single encode pass)
        # and use the CALIBRATED matrix as the source of ``gnn_score``.
        # This aligns Phase 6 with the RL training distribution (which
        # now also uses calibrated gnn_score per P3-004 fix in
        # ``generate_rl_input``). Both paths now use the SAME calibrated
        # value -- no more distribution mismatch between training and
        # Phase 6 inference.
        raw_matrix, calibrated_matrix = model.predict_all_pairs_dual(
            node_features, edge_indices,
            num_drugs=num_drugs, num_diseases=num_diseases,
            exclude_edges=exclude_edges,
        )  # SINGLE encode pass; both matrices differ only in sigmoid transform
    finally:
        # Restore prior training mode. Safe here because this function
        # is single-threaded (Phase 6 literature cross-check). The
        # concurrent API path uses predict_drug_disease_scores (also
        # P3-014-fixed) which does NOT toggle.
        model.train(_prior_training)

    # P3-004: use calibrated score as gnn_score (matches bridge fix).
    score_matrix = calibrated_matrix

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
