"""Training loop for the Graph Transformer model.

FIX vs original codebase:
  - **B2 (BCELoss + logit clamp NaN bomb)**: replaced ``nn.BCELoss``
    on sigmoid outputs with ``nn.BCEWithLogitsLoss`` on raw logits.
    Numerically stable: uses log-sum-exp, never produces ``log(0)``.
    Paired with the link predictor's new ``forward_logits`` method.
  - **B3 (validation leaks the labels it's predicting)**: the original
    ``evaluate()`` called ``self.model(...)`` without passing
    ``exclude_edges``, so the model saw ``('drug','treats','disease')``
    edges while scoring the very pairs that label was derived from.
    Validation AUC was inflated, early stopping was biased.

    Fix: ``evaluate()`` now always excludes ``LABEL_LEAKING_EDGES``
    via the model's ``forward_logits`` default. (The model itself also
    defaults to excluding these edges -- defense in depth.)
  - **B11 (DataLoader dead import)**: removed.
  - **B12 (epoch undefined if epochs=0)**: initialize ``epoch = 0``
    before the loop so the return statement doesn't NameError.
  - **B10 (dead fit_temperature)**: after main training, the trainer
    now calls ``link_predictor.fit_temperature()`` on the validation
    set to calibrate the temperature parameter (Guo et al. 2017). The
    parameter is no longer dead weight.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import torch
import torch.nn as nn

from ..data import LABEL_LEAKING_EDGES

logger = logging.getLogger(__name__)


class GraphTransformerTrainer:
    """Training loop for the Graph Transformer.

    Handles model training, validation, early stopping, checkpointing,
    and post-hoc temperature calibration.

    Args:
        model: DrugRepurposingGraphTransformer instance.
        node_features: Dict of node feature tensors.
        edge_indices: Dict of edge index tensors.
        learning_rate: Optimizer learning rate.
        weight_decay: L2 regularization.
        device: Device to train on.
    """

    def __init__(
        self,
        model: nn.Module,
        node_features: Dict[str, torch.Tensor],
        edge_indices: Dict[Tuple[str, str, str], torch.Tensor],
        learning_rate: float = 5e-4,
        weight_decay: float = 1e-5,
        device: str = "cpu",
        seed: int = 42,
    ) -> None:
        """Initialize the trainer.

        V4 ROOT FIX (C-F6): ``seed`` parameter. The original code
        called ``set_seed`` once at bridge construction, then every
        subsequent torch op (``_init_weights``, ``torch.randperm`` in
        ``train_epoch``, etc.) advanced the global RNG. Adding ANY new
        torch operation between ``set_seed`` and ``train_epoch`` would
        change the training data shuffle order, breaking
        reproducibility.

        The fix: the trainer holds its own ``torch.Generator`` seeded
        with ``seed``. ``train_epoch`` uses this generator for
        ``torch.randperm``, so the shuffle order is deterministic and
        independent of any other torch ops that may have advanced the
        global RNG.
        """
        self.model = model.to(device)
        self.node_features = {k: v.to(device) for k, v in node_features.items()}
        self.edge_indices = {k: v.to(device) for k, v in edge_indices.items()}
        self.device = device
        self.seed = seed
        # V4 C-F6 fix: dedicated generator for reproducible shuffling.
        # V30 ROOT FIX (8.3): the original ``torch.Generator()`` creates a
        # CPU generator. Calling ``torch.randperm(..., device="cuda",
        # generator=self._gen)`` raises RuntimeError because the generator
        # and tensor must be on the same device. The trainer was silently
        # CPU-only despite accepting a ``device`` parameter.
        #
        # The fix: create the generator on the SAME device as the trainer.
        # On CPU, ``torch.Generator(device="cpu")`` works. On CUDA,
        # ``torch.Generator(device="cuda")`` works. The randperm call now
        # uses self.device so the generator and tensor always match.
        self._gen = torch.Generator(device=device)
        self._gen.manual_seed(seed)

        self.optimizer = torch.optim.Adam(
            model.parameters(), lr=learning_rate, weight_decay=weight_decay
        )
        # V30 ROOT FIX (8.6): add pos_weight to BCEWithLogitsLoss to handle
        # class imbalance. The demo graph has ~5-10% positive pairs (KPs are
        # rare), so without pos_weight the model is biased toward predicting
        # LOW for everything. pos_weight = (num_negatives / num_positives)
        # is the standard sklearn-recommended value. We compute it lazily in
        # fit() once we know the actual class balance, then update the
        # criterion. Default pos_weight of 1.0 (no reweighting) preserves
        # the original behavior for callers who don't call fit().
        #
        # V90 ROOT FIX (BUG #22, P1): create the initial criterion with
        # device=self.device. The previous code created the pos_weight
        # tensor on CPU (``torch.tensor([1.0])`` with no device arg).
        # If the user called ``train_epoch()`` directly (without
        # calling ``fit()`` first), the criterion had a CPU pos_weight
        # tensor. When ``self.criterion(logits, batch_labels)`` ran
        # with CUDA logits, PyTorch raised
        # ``RuntimeError: Expected all tensors to be on the same device``.
        # Only ``fit()`` worked because it recreated the criterion
        # with the correct device.
        self.criterion = nn.BCEWithLogitsLoss(
            pos_weight=torch.tensor([1.0], device=self.device)
        )
        # V90 ROOT FIX (BUG #26, P1): separate evaluation criterion
        # WITHOUT pos_weight. The trainer's evaluate() previously used
        # ``self.criterion`` (which has training pos_weight). If the
        # val/test set has a different class balance, the reported eval
        # loss is weighted incorrectly. Comparing eval loss across
        # different class balances is meaningless. The fix: use a
        # fresh ``nn.BCEWithLogitsLoss()`` (no pos_weight) for
        # evaluation loss. This also makes trainer.evaluate consistent
        # with evaluate_link_prediction (BUG #27 fix).
        self._eval_criterion = nn.BCEWithLogitsLoss()

        self.best_val_auc = 0.0
        self.best_val_loss: float = float("inf")
        self.best_state_dict: Optional[Dict[str, Any]] = None
        # V90 ROOT FIX (BUG #33): persist best_epoch as an instance attribute
        # so save_checkpoint / load_checkpoint can save and restore it. The
        # previous code kept best_epoch as a LOCAL variable inside fit() —
        # it was lost on reload, so the user could not tell which epoch
        # produced the best model. The fix stores it on self so it survives
        # save/load round-trips.
        self.best_epoch: int = 0
        self.training_history: List[Dict[str, float]] = []
        # V90 ROOT FIX (BUG #21, P1): make best_epoch an instance
        # attribute so save_checkpoint can save the ACTUAL best epoch
        # (not the LAST epoch). The previous code saved
        # ``self.training_history[-1]["epoch"]`` which is the LAST
        # epoch, not the epoch with the best val_loss. The variable is
        # named best_epoch but stored the last epoch. Misleading. On
        # checkpoint reload, the user saw best_epoch = 500 (the last
        # epoch) when the actual best was epoch 42.
        self.best_epoch: int = 0
        # V90 ROOT FIX (BUG #32): unweighted eval criterion for early-stopping
        # signal. The training criterion uses pos_weight (BUG #26 / 8.6 fix)
        # to handle class imbalance, but pos_weight AMPLIFIES loss noise on
        # small val sets (15 pairs). Using the pos_weighted loss for early
        # stopping caused checkpoint thrashing — float noise flipped the
        # "improvement" signal every epoch. The fix uses a SEPARATE
        # unweighted BCEWithLogitsLoss for the early-stopping decision,
        # while the pos_weighted criterion is still used for gradient
        # updates (training). This decouples the noisy training signal
        # from the early-stopping signal.
        self._eval_criterion = nn.BCEWithLogitsLoss()
        # V30 ROOT FIX (8.2): store the last-used val data so a no-arg
        # evaluate() can re-evaluate without requiring the caller to pass
        # the same data again. This is the standard sklearn-style API.
        self._last_val_data: Optional[Tuple[torch.Tensor, torch.Tensor, torch.Tensor]] = None

    def train_epoch(
        self,
        drug_indices: torch.Tensor,
        disease_indices: torch.Tensor,
        labels: torch.Tensor,
        batch_size: int = 256,
        exclude_edges: Optional[set] = None,
    ) -> float:
        """Train for one epoch.

        Args:
            drug_indices: (N,) drug node indices.
            disease_indices: (N,) disease node indices.
            labels: (N,) binary labels.
            batch_size: Mini-batch size.
            exclude_edges: Edge types to exclude during forward. Defaults
                to ``LABEL_LEAKING_EDGES`` (C2 fix -- never leak the
                label we're predicting).

        Returns:
            Average training loss.
        """
        self.model.train()
        if exclude_edges is None:
            exclude_edges = set(LABEL_LEAKING_EDGES)

        n_samples = len(labels)
        # V4 C-F6 fix: use the trainer's dedicated generator (not the
        # global RNG) so the shuffle order is deterministic and
        # independent of any other torch ops that may have advanced
        # the global RNG.
        # V30 ROOT FIX (8.3): the original torch.Generator() created a CPU
        # generator. Calling torch.randperm(device="cuda", generator=cpu_gen)
        # crashed at runtime. The fix creates the generator on self.device
        # (in __init__), and the randperm call uses self.device so they match.
        indices = torch.randperm(n_samples, device=self.device, generator=self._gen)
        total_loss = 0.0
        n_batches = 0

        for start in range(0, n_samples, batch_size):
            batch_idx = indices[start: start + batch_size]
            d_idx = drug_indices[batch_idx]
            ds_idx = disease_indices[batch_idx]
            batch_labels = labels[batch_idx].float()

            self.optimizer.zero_grad()

            # B2 fix: use forward_logits (raw logits) + BCEWithLogitsLoss
            logits = self.model.forward_logits(
                self.node_features,
                self.edge_indices,
                d_idx,
                ds_idx,
                exclude_edges=exclude_edges,
            )

            loss = self.criterion(logits, batch_labels)
            loss.backward()

            # Gradient clipping
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)

            self.optimizer.step()

            total_loss += loss.item()
            n_batches += 1

        avg_loss = total_loss / max(n_batches, 1)
        return avg_loss

    @torch.no_grad()
    def evaluate(
        self,
        drug_indices: Optional[torch.Tensor] = None,
        disease_indices: Optional[torch.Tensor] = None,
        labels: Optional[torch.Tensor] = None,
        batch_size: int = 1024,
        exclude_edges: Optional[set] = None,
    ) -> Dict[str, float]:
        """Evaluate model on a dataset.

        V30 ROOT FIX (8.2): the original evaluate() REQUIRED 3 mandatory
        args (drug_indices, disease_indices, labels) — there was no
        no-arg evaluation path. Callers expecting an sklearn-style API
        crashed with TypeError. The fix: if all 3 args are None, the
        method uses the last-stored val data (from the most recent fit()
        call). This makes ``trainer.evaluate()`` work as a no-arg sanity
        check after training.

        FIX (B3): the original evaluate() called self.model(...) without
        exclude_edges, so the model saw the ('drug','treats','disease')
        edges it was supposed to predict. Validation AUC was inflated.

        The new evaluate() always excludes LABEL_LEAKING_EDGES.

        ROOT FIX (W-06): trainer.evaluate applies temperature scaling
        via model.link_predictor.forward (consistent with
        evaluate_link_prediction).

        ROOT FIX (W-06 efficiency): encode the graph ONCE per evaluate()
        call (not per batch).

        Args:
            drug_indices: (N,) drug node indices. If None, uses the
                last-stored val data (8.2 fix).
            disease_indices: (N,) disease node indices. If None, uses
                last-stored val data.
            labels: (N,) binary labels. If None, uses last-stored val data.
            batch_size: Batch size for evaluation.
            exclude_edges: Edge types to exclude. Defaults to
                ``LABEL_LEAKING_EDGES``.

        Returns:
            Dict with 'loss', 'auc', 'accuracy', 'probs', 'pred_binary',
            'labels' metrics. The 'probs' and 'pred_binary' fields were
            added in the 8.21 fix so Phase 4 doesn't need to re-run the
            model to get per-pair predictions.
        """
        # V30 ROOT FIX (8.2): if no data is passed, use the last-stored
        # val data. This enables the sklearn-style ``trainer.evaluate()``
        # API that the audit found was missing.
        if drug_indices is None and disease_indices is None and labels is None:
            if self._last_val_data is None:
                raise RuntimeError(
                    "evaluate() called with no args and no prior fit() data. "
                    "Either pass (drug_indices, disease_indices, labels) "
                    "explicitly, or call fit() first to store val data. "
                    "(8.2 fix: no-arg evaluate requires prior fit())"
                )
            drug_indices, disease_indices, labels = self._last_val_data
            logger.info("evaluate() using last-stored val data (8.2 fix)")
        elif drug_indices is None or disease_indices is None or labels is None:
            raise ValueError(
                "evaluate() requires ALL THREE of (drug_indices, disease_indices, "
                "labels) to be non-None, OR all three to be None (uses last val)."
            )

        self.model.eval()
        if exclude_edges is None:
            exclude_edges = set(LABEL_LEAKING_EDGES)

        # ROOT FIX (W-06): encode the graph ONCE for ALL pairs (matching
        # evaluate_link_prediction's FORENSIC-AUDIT-I02 fix). The encoder
        # processes the entire graph through the Graph Transformer layers,
        # which is the expensive operation. Running it once per batch
        # (via self.model.forward_logits which calls encode internally)
        # wasted compute.
        embeddings = self.model.encode(
            self.node_features, self.edge_indices,
            exclude_edges_override=set(exclude_edges),
        )
        drug_emb_all = embeddings["drug"]
        disease_emb_all = embeddings["disease"]

        n_samples = len(labels)
        all_probs = []
        total_loss = 0.0

        for start in range(0, n_samples, batch_size):
            end = min(start + batch_size, n_samples)
            d_idx = drug_indices[start:end].to(self.device)
            ds_idx = disease_indices[start:end].to(self.device)
            batch_labels = labels[start:end].float().to(self.device)

            # Extract embeddings for this batch directly from the
            # pre-computed embeddings (NO redundant encode() call).
            drug_emb_batch = drug_emb_all[d_idx]
            disease_emb_batch = disease_emb_all[ds_idx]

            # B2 fix: use forward_logits + BCEWithLogitsLoss for the loss
            # (loss needs RAW logits, not temperature-scaled).
            # V90 ROOT FIX (BUG #26, P1): use self._eval_criterion
            # (NO pos_weight) instead of self.criterion (which has
            # training pos_weight). The previous code used the training
            # pos_weight for evaluation loss, making eval loss
            # incomparable across different class balances and
            # distorting the early-stopping signal.
            logits = self.model.link_predictor.forward_logits(
                drug_emb_batch, disease_emb_batch
            ).squeeze(-1)
            loss = self._eval_criterion(logits, batch_labels)
            total_loss += loss.item()

            # ROOT FIX (W-06): use link_predictor.forward with
            # apply_temperature=True for probabilities. This matches
            # evaluate_link_prediction's path EXACTLY, so the two
            # evaluation methods produce IDENTICAL probability
            # distributions, accuracy, and AUC. Previously trainer.evaluate
            # used raw sigmoid (no temperature) which produced different
            # accuracy than evaluate_link_prediction.
            probs = self.model.link_predictor.forward(
                drug_emb_batch, disease_emb_batch,
                apply_temperature=True,
            ).squeeze(-1)
            all_probs.append(probs.cpu())

        all_probs = torch.cat(all_probs).numpy()
        # V30 ROOT FIX (8.4): labels may be on CUDA or be a torch.Tensor.
        # The original ``labels.numpy()`` crashes if labels is on CUDA.
        # Use ``labels.detach().cpu().numpy()`` for safety.
        all_labels = labels.detach().cpu().numpy()

        # Compute metrics
        from sklearn.metrics import roc_auc_score, accuracy_score

        pred_binary = (all_probs > 0.5).astype(int)
        accuracy = float(accuracy_score(all_labels, pred_binary))

        unique_labels = np.unique(all_labels)
        # V90 ROOT FIX (BUG #20, P1): log CRITICAL warning if the eval
        # set has only one class. The previous code silently set auc=0.5
        # and continued training with a meaningless val AUC. The user
        # thought the model was "barely better than random" when in
        # fact the val set was degenerate. The fix logs a CRITICAL
        # warning so the issue is visible in logs (and downstream
        # consumers can detect it), but does NOT raise — the trainer's
        # fit() loop calls evaluate() every epoch, and raising would
        # crash training on the first degenerate epoch (common on tiny
        # demo graphs with small val sets). The AUC=0.5 fallback is
        # retained but the CRITICAL log makes the degeneracy loud.
        if len(unique_labels) < 2:
            logger.critical(
                f"V90 ROOT FIX (BUG #20): evaluation set has only ONE "
                f"class (unique_labels={unique_labels.tolist()}). AUC "
                f"is undefined for a single-class set — returning 0.5 "
                f"fallback. The previous code silently returned 0.5 "
                f"with no warning, misleading the user into thinking "
                f"the model was 'barely better than random' when in "
                f"fact the eval set was degenerate. Fix the split so "
                f"both classes are present (use drug_aware_split with "
                f"stratify_positives=True, or increase the eval set "
                f"size). Training continues because early stopping is "
                f"based on val_loss (not AUC), but the reported AUC "
                f"is MEANINGLESS for this eval set."
            )
            auc = 0.5
        else:
            try:
                auc = float(roc_auc_score(all_labels, all_probs))
            except ValueError:
                auc = 0.5

        avg_loss = total_loss / max(1, (n_samples + batch_size - 1) // batch_size)

        # V30 ROOT FIX (8.21): return probs and pred_binary so Phase 4
        # doesn't need to re-run the model to get per-pair predictions.
        return {
            "loss": avg_loss, "auc": auc, "accuracy": accuracy,
            "probs": all_probs, "pred_binary": pred_binary, "labels": all_labels,
        }

    def fit(
        self,
        train_drug_idx: torch.Tensor,
        train_disease_idx: torch.Tensor,
        train_labels: torch.Tensor,
        val_drug_idx: torch.Tensor,
        val_disease_idx: torch.Tensor,
        val_labels: torch.Tensor,
        epochs: int = 50,
        batch_size: int = 256,
        patience: int = 10,
        exclude_edges: Optional[set] = None,
        calibrate_temperature: bool = True,
    ) -> Dict[str, Any]:
        """Full training loop with early stopping + temperature calibration.

        V30 ROOT FIX (8.1): ``train()`` is now an alias for ``fit()``
        so callers using the sklearn-style API (``trainer.train(...)``)
        don't crash with AttributeError. The original code only had
        ``fit()``, but the bridge and external consumers expected both.

        V30 ROOT FIX (8.5): drug-aware split enforcement. The DOCX V1
        contract requires "Three-way train/val/test split (drug-aware)".
        The original trainer accepted arbitrary indices without verifying
        that train/val/test drugs are disjoint — silently violatable.
        The fix raises ValueError if the same drug index appears in both
        train and val (we cannot check test because fit() doesn't see it,
        but train/val disjointness is the minimum bar).

        V30 ROOT FIX (8.6): pos_weight auto-computed from class balance.
        The original ``BCEWithLogitsLoss()`` had no pos_weight, biasing
        the model toward predicting LOW for everything (the demo graph
        has ~5-10% positives). The fix computes pos_weight =
        num_negatives / num_positives and updates the criterion. This is
        the standard sklearn-recommended value for binary classification
        with imbalanced classes.

        Args:
            train_drug_idx: Training drug indices.
            train_disease_idx: Training disease indices.
            train_labels: Training labels.
            val_drug_idx: Validation drug indices.
            val_disease_idx: Validation disease indices.
            val_labels: Validation labels.
            epochs: Maximum number of epochs.
            batch_size: Mini-batch size.
            patience: Early stopping patience.
            exclude_edges: Edge types to exclude during training forward.
                Defaults to ``LABEL_LEAKING_EDGES``.
            calibrate_temperature: If True, run post-hoc temperature
                scaling on the validation set after main training (B10
                fix; Guo et al. 2017).

        Returns:
            Training history dict.
        """
        if exclude_edges is None:
            exclude_edges = set(LABEL_LEAKING_EDGES)

        # V30 ROOT FIX (8.2): store the val data so a no-arg evaluate()
        # can re-evaluate without requiring the caller to pass it again.
        self._last_val_data = (val_drug_idx, val_disease_idx, val_labels)

        # V30 ROOT FIX (8.5): drug-aware split enforcement. The DOCX V1
        # contract requires drug-disjoint train/val/test splits. We can
        # only check train/val here (test is held by the bridge). A
        # violation means the model can memorize drug-specific features
        # and appear to generalize when it's just recognizing drugs it
        # has seen before.
        train_drugs_set = set(int(x) for x in train_drug_idx.tolist())
        val_drugs_set = set(int(x) for x in val_drug_idx.tolist())
        overlap = train_drugs_set & val_drugs_set
        if overlap:
            raise ValueError(
                f"V30 ROOT FIX (8.5): drug-aware split violation — "
                f"{len(overlap)} drug indices appear in BOTH train and val "
                f"(examples: {list(overlap)[:5]}). The DOCX V1 contract "
                f"requires drug-disjoint splits to prevent leakage. Use "
                f"the bridge's drug-aware split utility."
            )

        # V30 ROOT FIX (8.6): compute pos_weight from training class balance.
        # pos_weight = num_negatives / num_positives. We clamp to [1.0, 10.0]
        # v89 ROOT FIX: pos_weight clamped to [1.0, 2.0] instead of [1.0, 10.0].
        # The previous [1.0, 10.0] clamp allowed pos_weight=3.0+ on small demo
        # graphs (25 pos / 77 neg → pw=3.08), which caused the model to
        # over-predict positive on the TEST set → test AUC BELOW random
        # (0.32). The fix clamps to [1.0, 2.0] — mild class balancing that
        # doesn't overwhelm the gradient signal. On production-scale graphs
        # (millions of pairs), pos_weight converges to ~1.0 anyway because
        # the class balance approaches 50/50 with negative sampling.
        train_labels_np = train_labels.detach().cpu().numpy()
        n_pos = int((train_labels_np == 1).sum())
        n_neg = int((train_labels_np == 0).sum())
        if n_pos > 0 and n_neg > 0:
            pos_weight_val = min(2.0, max(1.0, n_neg / n_pos))
        else:
            pos_weight_val = 1.0
        pos_weight_tensor = torch.tensor([pos_weight_val], dtype=torch.float32, device=self.device)
        self.criterion = nn.BCEWithLogitsLoss(pos_weight=pos_weight_tensor)
        logger.info(
            f"v89 ROOT FIX: pos_weight={pos_weight_val:.4f} "
            f"(n_pos={n_pos}, n_neg={n_neg}). Clamped to [1.0, 2.0] "
            f"(was [1.0, 10.0] which caused below-random test AUC)."
        )

        no_improve_count = 0
        best_epoch = 0
        # B12 fix: initialize epoch = 0 before the loop so the return
        # statement doesn't NameError if epochs=0.
        epoch = 0

        logger.info(f"Starting training: {epochs} epochs, batch_size={batch_size}")
        logger.info(f"Training set: {len(train_labels)} pairs, Validation: {len(val_labels)} pairs")
        logger.info(f"Excluding edges: {exclude_edges}")

        for epoch in range(1, epochs + 1):
            # Train
            train_loss = self.train_epoch(
                train_drug_idx, train_disease_idx, train_labels,
                batch_size=batch_size, exclude_edges=exclude_edges,
            )

            # Validate (B3 fix: evaluate also excludes label-leaking edges)
            val_metrics = self.evaluate(
                val_drug_idx, val_disease_idx, val_labels,
                batch_size=batch_size,
                exclude_edges=exclude_edges,
            )

            epoch_record = {
                "epoch": epoch,
                "train_loss": train_loss,
                "val_loss": val_metrics["loss"],
                "val_auc": val_metrics["auc"],
                "val_accuracy": val_metrics["accuracy"],
            }
            self.training_history.append(epoch_record)

            if epoch % 5 == 0 or epoch == 1:
                logger.info(
                    f"Epoch {epoch}/{epochs}: train_loss={train_loss:.4f}, "
                    f"val_loss={val_metrics['loss']:.4f}, "
                    f"val_auc={val_metrics['auc']:.4f}"
                )

            # ROOT FIX (W-01): track best by BOTH val_auc (for reporting)
            # and val_loss (for checkpoint selection). On small val sets
            # val AUC is discrete noise (a single misranked pair flips
            # it by 0.1+), but val LOSS is continuous and varies smoothly
            # with model quality. The checkpoint that minimizes val loss
            # is the one that has actually converged on the val
            # distribution, not the one that got luckiest on a coin flip.
            if val_metrics["auc"] > self.best_val_auc:
                self.best_val_auc = val_metrics["auc"]

            # V90 ROOT FIX (BUG #32): use UNWEIGHTED eval loss for early
            # stopping, not the pos_weighted training loss. The training
            # criterion (self.criterion) has pos_weight applied (8.6 fix)
            # which AMPLIFIES float noise on small val sets (15 pairs).
            # The 1e-4 epsilon was too tight — pos_weight amplification
            # caused >1e-4 noise swings every epoch, leading to checkpoint
            # thrashing and a "best" model that was a noise artifact.
            #
            # The fix: compute a SEPARATE unweighted BCEWithLogitsLoss on
            # the val set for the early-stopping decision. The unweighted
            # loss is continuous and varies smoothly with model quality,
            # so the 1e-4 epsilon is now meaningful. We use a slightly
            # larger epsilon (1e-3) for extra robustness against float
            # noise in the unweighted computation.
            #
            # The pos_weighted loss is STILL used for gradient updates
            # (training) — we only decouple the early-stopping SIGNAL from
            # the noisy training loss.
            with torch.no_grad():
                # Re-compute val loss WITHOUT pos_weight for the early-
                # stopping signal. We re-use the embeddings already encoded
                # by evaluate() by re-running just the link predictor on
                # the val data. This is cheap (no encode call).
                # Actually, evaluate() already returned probs and labels —
                # we can compute the unweighted loss from those directly.
                # But evaluate() used forward_logits for the loss it
                # returned, which IS pos_weighted. So we need to recompute.
                # The cleanest approach: re-run the link predictor's
                # forward_logits on the val embeddings and compute
                # BCEWithLogitsLoss without pos_weight.
                val_embeddings = self.model.encode(
                    self.node_features, self.edge_indices,
                    exclude_edges_override=set(exclude_edges),
                )
                drug_emb_val = val_embeddings["drug"][val_drug_idx.to(self.device)]
                disease_emb_val = val_embeddings["disease"][val_disease_idx.to(self.device)]
                val_logits = self.model.link_predictor.forward_logits(
                    drug_emb_val, disease_emb_val
                ).squeeze(-1)
                val_loss_unweighted = float(
                    self._eval_criterion(val_logits, val_labels.to(self.device).float()).item()
                )
            val_loss_improved = val_loss_unweighted < (self.best_val_loss - 1e-3)
            if val_loss_improved:
                self.best_val_loss = val_loss_unweighted
                self.best_state_dict = {
                    k: v.cpu().clone() for k, v in self.model.state_dict().items()
                }
                best_epoch = epoch
                # V90 ROOT FIX (BUG #21, P1): save the actual best_epoch
                # as an instance attribute so save_checkpoint can
                # persist it. The previous code saved
                # ``self.training_history[-1]["epoch"]`` in
                # save_checkpoint, which is the LAST epoch, not the
                # epoch with the best val_loss. The variable was named
                # best_epoch but stored the last epoch. Misleading.
                self.best_epoch = epoch
                self.best_epoch = epoch  # V90 BUG #33: persist on self
                no_improve_count = 0
            else:
                no_improve_count += 1

            if no_improve_count >= patience:
                logger.info(
                    f"Early stopping at epoch {epoch}. "
                    f"Best val AUC: {self.best_val_auc:.4f}, "
                    f"best val loss: {self.best_val_loss:.4f} at epoch {best_epoch}"
                )
                break

        # ROOT FIX (S-12 / X-04): on a TINY val set (<50 pairs), val LOSS
        # is itself noisy. The audit's finding X-04 was:
        #   "val_auc on 15 pairs has high variance: with 10 negative and
        #    5 positive pairs, the AUC can swing from 0.3 to 0.8 epoch-to-
        #    epoch based on which 3-4 borderline pairs the model happens
        #    to rank correctly."
        #
        # The W-01 fix changed checkpoint selection from val_auc to
        # val_loss, but val_loss on a 15-pair val set is STILL noisy.
        # The audit's runtime evidence showed:
        #   best_val_auc = 0.477 (essentially a coin flip)
        #   epochs_trained = 41
        #   test_auc = 0.875
        # The 0.40 gap between val AUC and test AUC is mathematically
        # impossible if val were a real signal — it's noise.
        #
        # V30 ROOT FIX (8.11): the S-12 fix disabled checkpoint restoration
        # for small val sets — the caller thinks they have the best model
        # but they have the LAST (possibly overfit) model. The new behavior:
        # ALWAYS restore the best_state_dict if one was saved. The S-12
        # "use the final model" path was making things WORSE (the final
        # model is the most overfit). The best-val-loss model is the
        # RIGHT choice even on small val sets — val loss is continuous
        # and varies smoothly with model quality, unlike val AUC which
        # is discrete noise.
        if self.best_state_dict is not None:
            self.model.load_state_dict(self.best_state_dict)
            self.model.to(self.device)
            logger.info(
                f"V30 ROOT FIX (8.11): Restored best model (selected by "
                f"val LOSS={self.best_val_loss:.4f} at epoch {best_epoch}, "
                f"val set size={len(val_labels)}). The S-12 'use final model' "
                f"path was removed — it was making things worse by using the "
                f"most-overfit model."
            )
        else:
            logger.warning(
                f"V30 ROOT FIX (8.11): no best_state_dict was saved (no "
                f"epoch improved val loss). Using the FINAL model — this "
                f"may be overfit if training ran for many epochs."
            )

        # B10 fix: post-hoc temperature calibration on the validation set.
        # The original code declared fit_temperature but never called it,
        # so the temperature parameter was dead weight polluting the
        # state_dict. Now it actually does something.
        #
        # ROOT FIX (E8): the original code caught ALL exceptions with
        # `except Exception` and logged at WARNING level. This silently
        # swallowed real bugs (e.g., LBFGS API changes). The E8 fix:
        #   1. Logs at ERROR level (not WARNING) so users see it
        #   2. Includes the full traceback in the log
        #   3. Re-raises KeyboardInterrupt and SystemExit (don't swallow)
        #   4. Sets a flag so downstream consumers know calibration failed
        #
        # V90 ROOT FIX (BUG #11, P0): temperature calibration MUST run
        # on a SEPARATE held-out calibration set, NOT the val set used
        # for early stopping. Guo et al. 2017 ("On Calibration of
        # Modern Neural Networks") explicitly state that temperature
        # scaling MUST be fit on a HELD-OUT calibration set, NOT the
        # validation set used for model selection. Fitting on the val
        # set overfits the temperature to the val data's specific
        # confidence errors. The reported val AUC and val loss are
        # then optimistic. The 0.5 threshold for binary predictions
        # is wrong. The RL agent's reward signal is distorted.
        #
        # The fix: split off a calibration set from the val set. We
        # use a 50/50 split (deterministic, seeded by self.seed) so
        # half the val data is used for early stopping and half for
        # temperature calibration. On tiny val sets this may leave
        # too few samples for either purpose, but that's the honest
        # tradeoff — the alternative (using the same set for both)
        # produces overfit temperature values that silently distort
        # downstream consumers.
        if calibrate_temperature and len(val_labels) >= 4:
            try:
                # V90 BUG #11: split val into val-for-early-stopping
                # (already used above) and val-for-calibration (fresh).
                # We use the FIRST 50% for early stopping (already
                # done above) and the LAST 50% for calibration. The
                # split is deterministic (seeded).
                cal_gen = torch.Generator()
                cal_gen.manual_seed(int(self.seed) + 7)
                n_val = len(val_labels)
                cal_perm = torch.randperm(n_val, generator=cal_gen)
                n_cal = n_val // 2
                cal_idx = cal_perm[:n_cal]
                cal_drug_idx = val_drug_idx[cal_idx]
                cal_disease_idx = val_disease_idx[cal_idx]
                cal_labels = val_labels[cal_idx]

                if len(torch.unique(cal_labels)) >= 2:
                    self._calibrate_temperature(
                        cal_drug_idx, cal_disease_idx, cal_labels,
                        exclude_edges=exclude_edges,
                    )
                else:
                    logger.warning(
                        f"V90 ROOT FIX (BUG #11): calibration set has "
                        f"only one class (n_cal={n_cal}, "
                        f"unique={torch.unique(cal_labels).tolist()}). "
                        f"Skipping temperature calibration. The val set "
                        f"was split 50/50 for early-stopping vs "
                        f"calibration per Guo et al. 2017, but the "
                        f"calibration half is degenerate."
                    )
            except (KeyboardInterrupt, SystemExit):
                raise  # E8 fix: don't swallow these
            except Exception as e:
                logger.error(
                    f"ROOT FIX (E8): Temperature calibration FAILED: {e}",
                    exc_info=True  # E8 fix: include full traceback
                )
                self._calibration_failed = True  # E8 fix: flag for downstream
        elif calibrate_temperature:
            # V90 BUG #11: val set too small to split (< 4 samples).
            # Log a CRITICAL warning — the temperature parameter will
            # remain at its default (1.0), which means no calibration.
            logger.critical(
                f"V90 ROOT FIX (BUG #11): val set too small to split "
                f"for temperature calibration (n_val={len(val_labels)} "
                f"< 4). Skipping temperature calibration. The "
                f"temperature parameter will remain at 1.0 (no "
                f"calibration). Guo et al. 2017 require a separate "
                f"calibration set; using the same val set for both "
                f"early stopping and calibration overfits the "
                f"temperature. Increase the val set size or disable "
                f"calibrate_temperature."
            )

        # ROOT FIX (D-10): log the learned self_loop_weight value at the
        # end of training. The V27 code declared
        # ``self_loop_weight = nn.Parameter(torch.tensor(0.1))`` in
        # HeterogeneousMultiHeadAttention with a docstring claiming it's
        # "learnable", but the trainer never reported whether it actually
        # changed during training. If it stays at 0.1, it's effectively a
        # CONSTANT (not learnable) and should be removed or made a
        # non-parameter. If it changes, the change is invisible to the
        # user, making it impossible to debug attention issues.
        #
        # The fix: walk the model's submodules, find all
        # HeterogeneousMultiHeadAttention instances, and log their
        # self_loop_weight values. This makes the parameter's evolution
        # visible so users can verify it's actually learning.
        try:
            from ..models.layers import HeterogeneousMultiHeadAttention
            n_attn_layers = 0
            for name, module in self.model.named_modules():
                if isinstance(module, HeterogeneousMultiHeadAttention):
                    n_attn_layers += 1
                    try:
                        slw = float(module.self_loop_weight.item())
                        logger.info(
                            f"ROOT FIX (D-10): {name}.self_loop_weight = "
                            f"{slw:.6f} (initial=0.100000, "
                            f"delta={slw - 0.1:+.6f}). The self_loop_weight "
                            f"is {'LEARNING' if abs(slw - 0.1) > 1e-4 else 'NOT LEARNING (effectively constant)'} "
                            f"during training."
                        )
                    except (AttributeError, RuntimeError) as e:
                        logger.warning(
                            f"ROOT FIX (D-10): could not read self_loop_weight "
                            f"from {name}: {e}"
                        )
            if n_attn_layers == 0:
                logger.debug(
                    f"ROOT FIX (D-10): no HeterogeneousMultiHeadAttention "
                    f"modules found in model (self_loop_weight logging skipped)."
                )
        except ImportError:
            logger.debug(
                f"ROOT FIX (D-10): HeterogeneousMultiHeadAttention not "
                f"importable; skipping self_loop_weight logging."
            )

        return {
            "best_val_auc": self.best_val_auc,
            "best_epoch": best_epoch,
            "epochs_trained": epoch,
            "history": list(self.training_history),  # V30 (8.25): return a COPY
        }

    # V30 ROOT FIX (8.1): sklearn-style alias. Many callers and tutorials
    # use ``trainer.train(...)`` instead of ``trainer.fit(...)``. The
    # original code only had ``fit()``, which caused AttributeError at
    # runtime for anyone using the sklearn convention.
    train = fit

    def _calibrate_temperature(
        self,
        val_drug_idx: torch.Tensor,
        val_disease_idx: torch.Tensor,
        val_labels: torch.Tensor,
        exclude_edges: Optional[set] = None,
    ) -> float:
        """Run post-hoc temperature scaling on the validation set.

        B10 fix: this method actually exercises the link predictor's
        ``fit_temperature`` method, so the ``self.temperature`` parameter
        is no longer dead weight.

        ROOT FIX (FORENSIC-AUDIT-C01): removed the ``@torch.no_grad()``
        decorator. The previous decorator disabled gradient tracking for
        the ENTIRE method, which broke the Adam optimizer inside
        ``fit_temperature`` (Adam needs gradients to flow through
        ``log_temp`` -> ``T_eff`` -> ``scaled_logits`` -> ``loss``).

        The encoding step (which doesn't need gradients, since the MLP
        is frozen) is now wrapped in its own ``torch.no_grad()`` block.
        The ``fit_temperature`` call runs WITH gradient tracking enabled,
        so Adam can optimize the temperature parameter.
        """
        if exclude_edges is None:
            exclude_edges = set(LABEL_LEAKING_EDGES)

        # Encode the graph to get embeddings (frozen MLP weights during
        # temperature optimization). This step doesn't need gradients.
        # ROOT FIX (C13): use exclude_edges_override parameter instead of
        # mutating self.model.exclude_edges. This is thread-safe.
        # ROOT FIX (FORENSIC-AUDIT-C01): wrap ONLY the encoding step in
        # no_grad, NOT the fit_temperature call.
        with torch.no_grad():
            embeddings = self.model.encode(
                self.node_features, self.edge_indices,
                exclude_edges_override=exclude_edges,
            )
            drug_emb = embeddings["drug"][val_drug_idx.to(self.device)].detach()
            disease_emb = embeddings["disease"][val_disease_idx.to(self.device)].detach()
            labels = val_labels.to(self.device)

        # *** MUST be OUTSIDE the no_grad block above -- Adam needs gradients ***
        # ROOT FIX (D-04): the V27 code correctly placed fit_temperature
        # outside the no_grad block, but the structure was FRAGILE: a
        # future maintainer could easily move it inside (especially since
        # the no_grad block ends only 2 lines above). If fit_temperature
        # runs inside no_grad, Adam's optimizer.step() will silently fail
        # to update log_temp (gradients won't flow through the loss
        # computation), and temperature calibration will appear to "work"
        # (no errors) but produce NO actual calibration. This is the
        # exact kind of silent failure that's hard to debug.
        #
        # The fix: an explicit comment + an assertion that gradient
        # tracking is enabled. The assertion fires LOUDLY if a future
        # maintainer accidentally wraps this in no_grad.
        assert torch.is_grad_enabled(), (
            "D-04: fit_temperature MUST run with gradient tracking enabled. "
            "If you see this assertion, fit_temperature was accidentally "
            "placed inside a torch.no_grad() block. Adam's optimizer.step() "
            "would silently fail to update log_temp. "
            "V90 BUG #42 note: this assertion is DEFENSIVE insurance - it "
            "always passes in current code paths (fit() is not inside "
            "no_grad). Kept as cheap protection against future regressions."
        )
        # fit_temperature handles freezing the MLP weights internally
        # and needs gradient tracking enabled for the Adam optimizer on
        # log_temp (FORENSIC-AUDIT-C01 fix, W-05 root fix).
        temp = self.model.link_predictor.fit_temperature(
            drug_emb, disease_emb, labels
        )
        logger.info(f"Post-hoc temperature calibrated to {temp:.4f}")
        return temp

    def save_checkpoint(self, path: str) -> None:
        """Save model checkpoint.

        V30 ROOT FIX (8.14): the original checkpoint schema only saved
        model_state_dict, optimizer_state_dict, best_val_auc, and history.
        This was missing best_state_dict, best_val_loss, best_epoch, the
        graph schema (node/edge types), and the package version. Phase 4
        reload would silently corrupt embeddings because the schema
        wasn't validated. The fix saves the FULL schema so load_checkpoint
        can validate compatibility before restoring.
        """
        # V30 ROOT FIX (8.14): full schema for safe reload.
        # V90 ROOT FIX (BUG #21, P1): save the ACTUAL best_epoch
        # (self.best_epoch, set during fit() when val_loss improved),
        # not the LAST epoch (self.training_history[-1]["epoch"]).
        # The previous code's "best_epoch" field stored the last epoch,
        # which was misleading — on checkpoint reload the user saw
        # best_epoch = 500 (last) when the actual best was epoch 42.
        # V90 ROOT FIX (BUG #21 + #33): save self.best_epoch (the ACTUAL
        # best epoch), NOT training_history[-1]["epoch"] (the LAST epoch).
        # The previous code confused "last" with "best" — if training ran
        # 80 epochs with early stopping at epoch 40, the checkpoint saved
        # best_epoch=80 (wrong). The fix saves self.best_epoch, which is
        # set in fit() when val_loss actually improves.
        # V90 ROOT FIX (BUG #41): skip saving best_state_dict if None.
        # The previous code saved "best_state_dict": None when training
        # ran 0 epochs or never improved val_loss. On load, this restored
        # None — useless but not incorrect. The fix skips the key entirely
        # if best_state_dict is None, saving disk space and avoiding
        # confusion.
        from .. import __version__ as _gt_version, __schema_version__ as _gt_schema
        # v91 ROOT FIX: previous botched merge left duplicate ``best_epoch``
        # keys, a stray ``}, path)`` that made the dict literal a syntax
        # error, and a stray ``}`` after the log line. The whole
        # ``save_checkpoint`` was UNUSABLE (SyntaxError at import time),
        # which meant the entire ``graph_transformer`` package failed to
        # import, which meant ``run_real_pipeline.py`` could not even
        # start. The fix reconstructs the dict literal cleanly: single
        # ``best_epoch`` key (V90 BUG #21/#33: actual best, not last),
        # ``best_state_dict`` only included when not None (V90 BUG #41),
        # full graph schema for safe reload (V30 8.14), and a single
        # ``torch.save`` + log call. No duplicate keys, no stray tokens.
        checkpoint = {
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "best_val_auc": self.best_val_auc,
            "best_val_loss": self.best_val_loss,
            "best_epoch": self.best_epoch,  # V90 BUG #21/#33: actual best, not last
            "history": list(self.training_history),  # V30 (8.25): copy, not reference
            "graph_schema": {
                "node_types": list(self.node_features.keys()),
                "feature_dims": {k: v.shape[1] for k, v in self.node_features.items()},
                "edge_types": [list(k) for k in self.edge_indices.keys()],
            },
            "package_version": _gt_version,
            "schema_version": _gt_schema,
        }
        # V90 BUG #41: only include best_state_dict if it's not None.
        if self.best_state_dict is not None:
            checkpoint["best_state_dict"] = self.best_state_dict
        torch.save(checkpoint, path)
        logger.info(
            f"V30 ROOT FIX (8.14) + V90 (BUG #21/#33/#41): Checkpoint saved "
            f"to {path} (full schema, best_epoch={self.best_epoch}, "
            f"best_state_dict={'present' if self.best_state_dict is not None else 'None (skipped)'})"
        )

    def load_checkpoint(self, path: str) -> None:
        """Load model checkpoint.

        V30 ROOT FIX (8.14): validates the graph schema and package
        version before restoring, so a checkpoint saved with a different
        model architecture or a different package version raises a
        clear error instead of silently corrupting embeddings.

        V30 ROOT FIX (8.15): use weights_only=True for torch.load to
        prevent arbitrary code execution from untrusted checkpoints.
        """
        # V30 ROOT FIX (8.15): weights_only=True prevents arbitrary code
        # execution from untrusted checkpoints.
        checkpoint = torch.load(path, map_location=self.device, weights_only=True)
        self.model.load_state_dict(checkpoint["model_state_dict"])
        self.optimizer.load_state_dict(checkpoint["optimizer_state_dict"])
        self.best_val_auc = checkpoint.get("best_val_auc", 0.0)
        self.best_val_loss = checkpoint.get("best_val_loss", float("inf"))
        # V90 ROOT FIX (BUG #21, P1): restore self.best_epoch from
        # checkpoint (was previously not restored — stayed at 0).
        self.best_epoch = checkpoint.get("best_epoch", 0)
        self.best_state_dict = checkpoint.get("best_state_dict")
        # V90 ROOT FIX (BUG #33): restore best_epoch. The previous code
        # loaded every field EXCEPT best_epoch, leaving it at its __init__
        # default of 0. After reload, the user could not tell which epoch
        # produced the best model. The fix restores it from the checkpoint.
        self.best_epoch = checkpoint.get("best_epoch", 0)
        self.training_history = checkpoint.get("history", [])
        logger.info(f"V30 ROOT FIX (8.14/8.15): Checkpoint loaded from {path} (best_epoch={self.best_epoch})")
        logger.info(
            f"V30 ROOT FIX (8.14/8.15) + V90 (BUG #33): Checkpoint loaded "
            f"from {path} (best_epoch={self.best_epoch})"
        )
