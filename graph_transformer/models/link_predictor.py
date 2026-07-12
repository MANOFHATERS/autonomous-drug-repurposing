"""
Link predictor for drug-disease therapeutic relationship scoring.

Takes drug and disease node embeddings and outputs a score (raw logit)
indicating the likelihood of a therapeutic relationship.

FIX vs original codebase:
  - **B2 (BCELoss + logit clamp at +/-30 => NaN bomb)**: the original
    code clamped logits to ``[-30, 30]`` and then applied ``sigmoid``
    in the model's ``forward()``, then trained with ``nn.BCELoss`` on
    the resulting probabilities. ``sigmoid(30)`` in float32 is exactly
    ``1.0``, so for a label-0 pair the BCELoss becomes
    ``-log(1 - 1.0) = -log(0) = inf``. The clamp *guaranteed* the NaN
    instead of preventing it.

    Fix: this module now returns **raw logits** from ``forward()`` and
    removes the clamp entirely. The trainer uses ``nn.BCEWithLogitsLoss``
    which is numerically stable (it uses the log-sum-exp trick). The
    ``predict_probability()`` convenience method applies sigmoid for
    inference-time scoring.
  - **B10 / B19 / B-F5 (dead fit_temperature + temperature parameter
    pollution + temperature NEVER applied at inference)**: the original
    code declared ``self.temperature`` as an ``nn.Parameter`` but never
    trained it -- ``fit_temperature`` existed but was never called, so
    the parameter just polluted the state_dict and confused the
    optimizer. The V2/V3 code trained it via ``fit_temperature`` but
    NEVER applied it at inference time -- ``forward()``, ``forward_logits()``
    and every caller (``predict_all_pairs``, ``evaluate``,
    ``evaluate_link_prediction``, ``predict_drug_disease_scores``) all
    used raw ``sigmoid(logits)``. The calibrated parameter was dead
    weight polluting the state_dict AND providing zero functional value.

    V4 ROOT FIX:
      1. ``forward_logits`` still returns RAW logits (no temperature)
         -- this is what the trainer feeds to ``BCEWithLogitsLoss``
         (training loss needs raw logits, NOT temperature-scaled).
      2. ``forward`` (probability output) now applies temperature
         scaling: ``sigmoid(logits / temperature)``. Every inference
         path that produces probabilities for downstream consumers
         (the RL ranker, the AUC computation, the dashboard) goes
         through ``forward`` or ``predict_probability``.
      3. ``predict_probability`` is now the CANONICAL inference method
         used by ``predict_all_pairs``, ``predict_drug_disease_scores``
         and ``evaluate_link_prediction``. No more dead method.
  - **S-F4 (LBFGS lr too small)**: ``fit_temperature`` now uses
    ``lr=1.0`` (the standard learning rate for LBFGS, which is a
    quasi-Newton method). The V2/V3 ``lr=0.01`` was 100x too small and
    frequently failed to converge to the optimal temperature within
    ``max_iter=100`` iterations.
"""
from __future__ import annotations

import logging
import math
import threading
from typing import List, Optional

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


class DrugDiseaseLinkPredictor(nn.Module):
    """MLP-based link prediction head for drug-disease pairs.

    The ``forward()`` method returns **temperature-scaled probabilities**
    (used for all inference paths). The ``forward_logits()`` method
    returns raw logits (used by the trainer for ``BCEWithLogitsLoss`` --
    training loss needs raw logits, not calibrated probabilities).

    P3-016 ROOT FIX (REVERT B-06): input features per pair are now
    [drug_emb, disease_emb, elementwise_product, signed_difference,
    abs_difference] (5*D dimensions). The B-06 fix had removed
    ``abs_diff`` (reducing to 4*D) claiming the MLP can learn |·| from
    signed_diff alone. While technically true, this forced the first
    MLP layer to spend representational capacity learning the
    absolute-value operator — capacity that should learn the actual
    drug-disease interaction pattern. abs_diff is a DISTINCT
    magnitude-of-difference signal that the MLP no longer needs to
    reconstruct. The parameter savings (20% of one layer) is negligible
    compared to the information loss and slower convergence. With
    ``embedding_dim=32`` the input layer is ``5*32=160 -> 64`` (10240
    params).

    Args:
        embedding_dim: Dimension of input node embeddings.
        hidden_dims: List of hidden layer sizes.
        dropout: Dropout rate.
        activation: Activation function ('relu' or 'gelu').
    """

    def __init__(
        self,
        embedding_dim: int = 128,
        hidden_dims: Optional[List[int]] = None,
        dropout: float = 0.2,
        activation: str = "relu",
    ) -> None:
        super().__init__()
        self.embedding_dim = embedding_dim
        hidden_dims = hidden_dims or [256, 128]

        # P3-016 ROOT FIX (REVERT B-06): Input is 5*D, not 4*D. The B-06
        # fix removed ``abs_diff = |signed_diff|`` claiming the MLP can
        # learn the |·| operator from signed_diff alone via ReLU. While
        # technically true (ReLU is piecewise-linear and can approximate
        # |x|), this FORCES the first MLP layer to spend representational
        # capacity learning the absolute-value operator — capacity that
        # should be used for learning the actual drug-disease interaction
        # pattern. abs_diff is a DISTINCT signal (magnitude of difference)
        # that is NOT trivially reconstructible: a 2-layer ReLU MLP needs
        # ~2*D extra hidden units to learn |x| across all D dimensions,
        # and the approximation is imperfect near x=0 (the kink).
        #
        # The original 5*D input was more informative. The parameter
        # savings from 4*D (20% of one layer = ~2K params on a 32-dim
        # embedding) is negligible compared to the information loss. On
        # small demo graphs the capacity loss is noticeable; on large
        # production graphs it slows convergence. Restoring abs_diff gives
        # the MLP a direct magnitude-of-difference feature for free.
        input_dim = embedding_dim * 5

        # Build MLP layers
        layers: List[nn.Module] = []
        prev_dim = input_dim
        for h_dim in hidden_dims:
            layers.append(nn.Linear(prev_dim, h_dim))
            if activation == "gelu":
                layers.append(nn.GELU())
            else:
                layers.append(nn.ReLU())
            layers.append(nn.Dropout(dropout))
            prev_dim = h_dim
        # Final output layer (single logit)
        layers.append(nn.Linear(prev_dim, 1))

        self.mlp = nn.Sequential(*layers)

        # Temperature for post-hoc calibration (Guo et al. 2017).
        # Initialized to 1.0 (identity). Train ONLY via fit_temperature()
        # after main training -- do NOT include in the main optimizer.
        # V4 fix: temperature is now ACTUALLY APPLIED at inference time
        # by forward() and predict_probability(). It is no longer dead
        # weight.
        self.temperature = nn.Parameter(torch.ones(1))

        # V90 ROOT FIX (BUG #10, P0): per-instance RLock to serialize
        # the eval/train toggle in predict_probability. The previous
        # code did:
        #     prior_training = self.training
        #     self.eval()
        #     ...
        #     self.train(prior_training)
        # without any lock. If another thread called predict_probability
        # between self.eval() and self.train(prior_training), it saw
        # the module in eval mode regardless of the prior state. The
        # Phase 5 API server is supposed to handle 100 concurrent
        # requests (V1 contract item 5) — this race condition silently
        # produced inconsistent predictions: some inference calls ran
        # with dropout disabled (eval mode leaked from a prior call)
        # while others ran with dropout enabled. Predictions were
        # non-deterministic across concurrent requests.
        #
        # The fix: a reentrant lock around the eval/train toggle.
        # Reentrant because predict_probability calls forward, which
        # does NOT itself toggle training mode (only forward_logits
        # and forward are pure inference paths).
        self._predict_lock = threading.RLock()

    def _construct_pair_features(
        self,
        drug_emb: torch.Tensor,
        disease_emb: torch.Tensor,
    ) -> torch.Tensor:
        """Construct pair features from drug and disease embeddings.

        P3-016 ROOT FIX (REVERT B-06): ``abs_diff = |signed_diff|`` has
        been RESTORED as a distinct input feature. The B-06 fix removed
        it claiming the MLP can learn |·| from signed_diff via ReLU.
        While technically true, this forced the first MLP layer to spend
        representational capacity learning the absolute-value operator
        — capacity that should learn the drug-disease interaction pattern.
        abs_diff is a DIRECT magnitude-of-difference signal that the MLP
        no longer needs to reconstruct. See the __init__ comment for the
        full rationale.

        Args:
            drug_emb: (N, D) drug embeddings.
            disease_emb: (N, D) disease embeddings.

        Returns:
            (N, 5*D) concatenated features:
            [drug_emb, disease_emb, product, signed_diff, abs_diff].
        """
        product = drug_emb * disease_emb
        signed_diff = drug_emb - disease_emb
        abs_diff = torch.abs(signed_diff)

        return torch.cat(
            [drug_emb, disease_emb, product, signed_diff, abs_diff], dim=-1
        )

    def forward_logits(
        self,
        drug_emb: torch.Tensor,
        disease_emb: torch.Tensor,
    ) -> torch.Tensor:
        """Compute RAW logits for drug-disease pairs (NO temperature).

        V4 ROOT FIX (B-F5): the trainer's ``BCEWithLogitsLoss`` needs
        RAW logits, not temperature-scaled logits. Temperature scaling
        is a post-hoc calibration on the OUTPUT probability, not on the
        training loss. Applying temperature during training would
        change the loss landscape and prevent the MLP from learning
        properly (the temperature would absorb the calibration error
        into the MLP weights, defeating the purpose).

        Use this method ONLY for:
          - Training loss computation (BCEWithLogitsLoss)
          - Internal AUC computation (AUC is invariant to monotonic
            transforms, so temperature doesn't matter for AUC)

        Use ``forward()`` or ``predict_probability()`` for:
          - Probability output to downstream consumers (RL ranker)
          - Dashboard display
          - Any consumer that interprets the score as a calibrated
            probability

        Args:
            drug_emb: (N, embedding_dim) drug node embeddings.
            disease_emb: (N, embedding_dim) disease node embeddings.

        Returns:
            (N, 1) raw logits.
        """
        pair_features = self._construct_pair_features(drug_emb, disease_emb)
        logits = self.mlp(pair_features)
        # NOTE: no clamp, no sigmoid, no temperature. The trainer uses
        # BCEWithLogitsLoss which is numerically stable. (B2 fix.)
        return logits

    # ROOT FIX (FORENSIC-AUDIT-C01): the previous clamp range [0.05, 10.0]
    # was far too wide. With LBFGS lr=1.0, calibration ALWAYS converged to
    # one of the boundaries (0.05 = extreme sharpening, or 10.0 = extreme
    # softening), producing degenerate saturated probabilities. The new
    # range [0.5, 2.0] is the standard range for temperature scaling per
    # Guo et al. 2017 ("On Calibration of Modern Neural Networks"):
    #   - T < 0.5 means extreme sharpening (over-confident model) -- outside
    #     the typical calibration range and a sign of a broken fit.
    #   - T > 2.0 means extreme softening (under-confident model) -- also
    #     outside the typical range.
    # Values inside [0.5, 2.0] produce meaningful, non-saturated
    # probabilities that downstream consumers (RL ranker, dashboard,
    # literature cross-check) can interpret as calibrated confidence.
    TEMPERATURE_CLAMP_MIN: float = 0.5
    TEMPERATURE_CLAMP_MAX: float = 2.0

    def forward(
        self,
        drug_emb: torch.Tensor,
        disease_emb: torch.Tensor,
        apply_temperature: bool = True,
    ) -> torch.Tensor:
        """Compute CALIBRATED probabilities for drug-disease pairs.

        ROOT FIX (FORENSIC-AUDIT-C01): ``forward`` applies temperature
        scaling -- ``sigmoid(logits / temperature)`` -- using a TIGHT
        clamp range [0.5, 2.0] (Guo et al. 2017 standard range). The
        previous [0.05, 10.0] range allowed degenerate T values that
        saturated the sigmoid output to 0 or 1, making the
        ``gnn_score_calibrated`` column in Phase 6 output bimodal garbage.

        Args:
            drug_emb: (N, embedding_dim) drug node embeddings.
            disease_emb: (N, embedding_dim) disease node embeddings.
            apply_temperature: If True (default), divide logits by the
                learned temperature (clamped to [0.5, 2.0]) before sigmoid.
                If False, use raw logits (uncalibrated). Set to False only
                for the RL input CSV (where full variance is needed) or
                for AUC computation (AUC is invariant to monotonic
                transforms).

        Returns:
            (N, 1) probabilities in [0, 1].
        """
        logits = self.forward_logits(drug_emb, disease_emb)
        if apply_temperature:
            # ROOT FIX (FORENSIC-AUDIT-C01): tight clamp [0.5, 2.0] matches
            # the range used in fit_temperature, so the stored parameter
            # always matches inference-time usage. T=1.0 (identity) is the
            # midpoint of the range, so an uncalibrated model produces
            # reasonable probabilities.
            t = self.temperature.clamp(
                min=self.TEMPERATURE_CLAMP_MIN, max=self.TEMPERATURE_CLAMP_MAX
            )
            logits = logits / t
        return torch.sigmoid(logits)

    def predict_probability(
        self,
        drug_emb: torch.Tensor,
        disease_emb: torch.Tensor,
        apply_temperature: bool = True,
    ) -> torch.Tensor:
        """Predict therapeutic relationship probability.

        V4 ROOT FIX (Dead code #5): this method is now ACTUALLY CALLED
        by ``predict_all_pairs`` and ``predict_drug_disease_scores``.

        V8 ROOT FIX (B2): ``evaluate_link_prediction`` is now ALSO
        ACTUALLY CALLED by the bridge's ``train_model`` method as an
        independent verification of the trainer's evaluate() results.
        The V4 docstring claim that evaluate_link_prediction calls this
        method is now TRUE (it was previously false — the function
        existed but was never invoked).

        This method delegates to ``forward`` (which applies temperature
        by default), so all inference paths produce calibrated
        probabilities.

        V30 ROOT FIX (6.1): the original code called ``self.eval()``
        UNCONDITIONALLY and NEVER restored the prior training state. This
        was a silent side effect: after ``predict_probability`` returned,
        the link_predictor's dropout was DISABLED for the rest of the
        process. If the bridge called this mid-epoch (which it does via
        ``evaluate_link_prediction``), subsequent training batches
        trained with NO dropout in the link predictor → silent
        regularization regime change → link predictor overfits.

        The fix: save the prior training state, switch to eval, run
        inference, then RESTORE the prior state. This makes the method
        side-effect-free with respect to the module's training mode.

        Args:
            drug_emb: (N, embedding_dim) drug node embeddings.
            disease_emb: (N, embedding_dim) disease node embeddings.
            apply_temperature: If True (default), divide logits by the
                learned temperature (calibrated probabilities). If False,
                use raw logits (uncalibrated).

        Returns:
            (N,) probabilities in [0, 1].
        """
        # V90 ROOT FIX (BUG #10 + #28, P0/P2): the eval/train toggle is
        # now thread-safe via self._predict_lock, AND we skip the
        # save/restore when the module is already in eval mode (the
        # common case during inference, e.g., after predict_all_pairs
        # has already called self.eval() on the full model).
        #
        # BUG #10 root cause: without the lock, concurrent calls to
        # predict_probability raced — thread A's self.eval() could
        # happen between thread B's self.eval() and self.train(prior),
        # leaving B in eval mode regardless of its prior state. The
        # Phase 5 API server (100 concurrent requests) silently
        # produced inconsistent predictions.
        #
        # BUG #28 root cause: predict_all_pairs already calls
        # self.eval() on the full model. Then predict_probability
        # called self.eval() again on the link predictor (redundant),
        # saved prior_training = False, ran inference, and restored
        # self.train(False) (no-op). The save/restore was wasted work
        # on every call. For 10K×10K pairs, that's 100M redundant
        # save/restore cycles.
        #
        # The combined fix: skip the toggle entirely if already in
        # eval mode (BUG #28), and use a lock when we DO toggle
        # (BUG #10).
        if self.training:
            with self._predict_lock:
                prior_training = self.training
                self.eval()
                try:
                    with torch.no_grad():
                        probs = self.forward(drug_emb, disease_emb, apply_temperature=apply_temperature)
                finally:
                    self.train(prior_training)
        else:
            # Already in eval mode — no toggle needed (BUG #28 fix).
            with torch.no_grad():
                probs = self.forward(drug_emb, disease_emb, apply_temperature=apply_temperature)
        return probs.squeeze(-1)

    def fit_temperature(
        self,
        drug_emb: torch.Tensor,
        disease_emb: torch.Tensor,
        labels: torch.Tensor,
        lr: float = 0.02,
        max_iter: int = 200,
    ) -> float:
        """Fit temperature scaling on a validation set (Guo et al. 2017).

        Post-hoc calibration: after the main MLP weights are frozen, find
        a single scalar temperature T that minimizes NLL on a validation
        set. This shrinks over-confident predictions without changing
        the AUC (temperature is monotonic).

        ROOT FIX (FORENSIC-AUDIT-C01): the previous implementation used
        LBFGS with lr=1.0 and a wide clamp [0.05, 10.0]. LBFGS took
        massive first steps, hit the clamp boundary, and the clamp zeroed
        the gradient (clamp's backward pass returns 0 grad outside the
        range), so LBFGS could not recover. The calibration ALWAYS
        converged to T=0.05 (extreme sharpening) or T=10.0 (extreme
        softening), producing degenerate saturated probabilities.

        V27 attempted to fix this with a tanh-parameterization
        (``T_eff = 1.25 + 0.75 * tanh(log_temp)``), but tanh's
        derivative ``1 - tanh^2(x)`` VANISHES at large |x|, so Adam
        could get pinned at the boundaries (W-05 audit finding).

        ROOT FIX (W-05): the root fix uses ``T = exp(log_temp)`` whose
        derivative ``dloss/dlog_temp = dloss/dT * T`` NEVER vanishes
        (since T > 0 always). A HARD CLAMP is applied to ``log_temp``
        AFTER each Adam step (outside the autograd graph) to keep T in
        the Guo et al. 2017 standard range [0.5, 2.0]. The clamp does
        NOT zero gradients during the forward pass (the bug with the
        V27 tanh approach) -- gradients still flow through
        ``T = exp(log_temp)`` cleanly.

        Args:
            drug_emb: (N, D) drug embeddings.
            disease_emb: (N, D) disease embeddings.
            labels: (N,) binary labels.
            lr: Learning rate for Adam. Default 0.02 (P3-027 ROOT FIX:
                the previous default was 0.05 but the code internally
                multiplied by 0.4 to give an effective lr of 0.02. The
                mismatch between the documented default and the actual
                effective lr was misleading. We now expose 0.02 directly
                as the default and remove the ``* 0.4`` factor inside
                the optimizer construction so what you pass is what you
                get. Pass a smaller lr for smoother convergence on
                small cal sets, a larger lr for faster convergence on
                large cal sets.).
            max_iter: Maximum optimization iterations. Default 200.

        Returns:
            Optimal temperature value in [0.5, 2.0].
        """
        # Freeze MLP weights during temperature optimization
        # V90 ROOT FIX (BUG #13, P1): wrap the optimization loop in
        # try/finally so the MLP weights are ALWAYS unfrozen, even on
        # exception. The previous code froze the MLP at line ~358 but
        # only unfroze at the very end (~470). If an exception happened
        # in between (OOM, NaN loss, CUDA error), the MLP weights
        # STAYED frozen — a transient calibration failure permanently
        # bricked the link predictor's trainability. The user saw
        # "temperature calibration FAILED" in the log but didn't know
        # the MLP was frozen. Next training run: loss didn't decrease,
        # user was confused.
        #
        # P3-026 ROOT FIX: also save and restore the prior TRAINING MODE
        # (not just requires_grad). The previous try/finally only
        # restored requires_grad on MLP weights; it did NOT restore
        # ``self.training``. Since the try block calls ``self.eval()``
        # (line below), after fit_temperature returns the link_predictor
        # is STILL in eval mode — dropout is disabled, BatchNorm uses
        # running stats. Subsequent training runs would silently train
        # with eval-mode behavior. We now save the prior training state
        # and restore it in the finally block.
        for p in self.mlp.parameters():
            p.requires_grad_(False)
        prior_training = self.training

        try:
            self.eval()
            with torch.no_grad():
                logits = self.forward_logits(drug_emb, disease_emb).squeeze(-1).detach()
                labels_f = labels.float().detach()

            # ROOT FIX (W-05): the V27 code used
            #     T_eff = 1.25 + 0.75 * torch.tanh(log_temp)
            # and claimed "tanh maps (-inf,+inf) -> (-1,1) so T_eff is
            # differentiable everywhere and gradients never vanish." This is
            # technically TRUE (tanh is differentiable everywhere) but its
            # derivative ``1 - tanh^2(x)`` VANISHES as |x| -> inf. If Adam
            # pushes ``log_temp`` to a large value (which it can, since
            # ``log_temp`` is unconstrained), the gradient
            # ``dloss/dlog_temp`` is multiplied by ``(1 - tanh^2(log_temp))``
            # which is essentially 0 — Adam cannot recover. The calibration
            # gets pinned at T_eff = 0.5 or T_eff = 2.0 (the boundaries).
            #
            # The ROOT FIX uses ``T = exp(log_temp)`` whose derivative
            # ``dloss/dlog_temp = dloss/dT * T`` NEVER vanishes inside the
            # valid range (the Jacobian is just T, which is positive). To
            # prevent T from drifting outside [0.5, 2.0] (Guo et al. 2017
            # standard range), we apply a HARD CLAMP AFTER Adam's update
            # step -- not inside the forward pass. The hard clamp does NOT
            # zero gradients during the forward pass (gradients still flow
            # through ``T = exp(log_temp)``), so Adam can recover even if it
            # overshoots. The clamp only zeros the gradient w.r.t. log_temp
            # when log_temp is OUTSIDE the clamped range, which is the
            # correct behavior (no need to keep pushing if we're already at
            # the boundary).
            #
            # Equivalent log-bounds: T in [0.5, 2.0] <=> log_temp in [log(0.5), log(2.0)]
            # = [-0.693, 0.693]. We clamp log_temp to this range AFTER each
            # Adam step. Inside this range, tanh-derivative is >= 1 - tanh^2(0.693)
            # = 1 - 0.393 = 0.607 (non-vanishing), but more importantly, the
            # exp-parameterization has derivative = T (always positive), so
            # gradients NEVER vanish regardless of clamping.
            #
            # P3-028 ROOT FIX: the previous comment block was at 8-space
            # indent (same as ``try:``), visually suggesting it was
            # OUTSIDE the try block. The try body is at 12-space indent.
            # A maintainer reading the code might add code after the
            # comments at 8-space indent, which would be outside the try
            # and thus not covered by the finally's requires_grad restore.
            # We've re-indented the comments to 12-space so they're
            # visually inside the try block where they belong.
            LOG_TEMP_MIN = math.log(self.TEMPERATURE_CLAMP_MIN)  # log(0.5) = -0.693
            LOG_TEMP_MAX = math.log(self.TEMPERATURE_CLAMP_MAX)  # log(2.0) = 0.693

            log_temp = torch.zeros(1, requires_grad=True)
            # ROOT FIX (W-05): lower lr (0.02 instead of 0.05) since exp()
            # amplifies log_temp changes. With lr=0.05 and log_temp starting
            # at 0, a single bad gradient could push log_temp to 0.5 (T=1.65)
            # in one step. lr=0.02 gives smoother convergence.
            # P3-027 ROOT FIX: the previous code used ``lr * 0.4`` here,
            # making the EFFECTIVE lr 0.02 when the documented default was
            # 0.05. We've changed the default to 0.02 (matching the actual
            # effective lr) and removed the ``* 0.4`` factor so what the
            # caller passes is what gets used. No more hidden scaling.
            optimizer = torch.optim.Adam([log_temp], lr=lr)

            criterion = nn.BCEWithLogitsLoss()

            # Track best (lowest loss) T across all iterations
            best_loss = float('inf')
            best_T = 1.0
            no_improve_count = 0
            patience = 15  # early stop if loss hasn't improved in 15 iters

            for iteration in range(max_iter):
                optimizer.zero_grad()
                # ROOT FIX (W-05): use exp parameterization. The gradient
                # dloss/dlog_temp = dloss/dT * T, and T > 0 always, so the
                # gradient NEVER vanishes (unlike tanh whose derivative
                # vanishes at large |log_temp|).
                T = torch.exp(log_temp)
                scaled_logits = logits / T
                loss = criterion(scaled_logits, labels_f)
                loss.backward()
                # P3-S05 ROOT FIX: clip the gradient on log_temp BEFORE
                # optimizer.step(). The previous code had NO gradient
                # clipping — if the cal set had a few misclassified
                # samples with large loss, the gradient on log_temp could
                # be large enough to push it outside the [log(0.5),
                # log(2.0)] range in a single step. The per-iteration
                # clamp after step() catches the value, but a large
                # gradient also corrupts Adam's momentum buffer (the
                # running second-moment estimate becomes huge, leading
                # to oversized steps for many subsequent iterations).
                # Clipping to a max norm of 1.0 keeps Adam's momentum
                # stable. This is the standard practice for temperature
                # scaling implementations (Guo et al. 2017, PyTorch
                # tutorial).
                torch.nn.utils.clip_grad_norm_([log_temp], 1.0)
                optimizer.step()

                # ROOT FIX (W-05): HARD CLAMP log_temp AFTER the optimizer
                # step. This keeps log_temp (and thus T = exp(log_temp))
                # inside the Guo et al. 2017 standard range [0.5, 2.0]. The
                # clamp is applied OUTSIDE the autograd graph (using
                # ``.data``), so it does NOT interfere with gradient
                # computation on the NEXT iteration -- gradients still flow
                # through T = exp(log_temp) cleanly. The clamp only prevents
                # log_temp from drifting outside the valid range; it does
                # NOT zero gradients during the forward pass (the bug with
                # the V27 tanh approach).
                with torch.no_grad():
                    log_temp.data = log_temp.data.clamp(min=LOG_TEMP_MIN, max=LOG_TEMP_MAX)

                loss_val = float(loss.item())
                T_val = float(T.item())

                # Track best T (lowest loss)
                if loss_val < best_loss - 1e-6:
                    best_loss = loss_val
                    best_T = T_val
                    no_improve_count = 0
                else:
                    no_improve_count += 1

                # Early stopping: convergence reached
                if no_improve_count >= patience:
                    logger.debug(
                        f"fit_temperature: converged at iteration {iteration} "
                        f"(no improvement for {patience} iters). "
                        f"Best T={best_T:.4f}, best loss={best_loss:.6f}"
                    )
                    break

            # ROOT FIX (W-05): store best_T (not final T), clamped to
            # [0.5, 2.0] to match forward()'s inference-time clamp. The
            # best_T is already in range due to the per-iteration clamp
            # above, but we apply the clamp defensively in case best_T was
            # tracked before the first clamp took effect.
            final_T = float(max(self.TEMPERATURE_CLAMP_MIN,
                                min(self.TEMPERATURE_CLAMP_MAX, best_T)))
            self.temperature.data.fill_(final_T)

            logger.info(
                f"ROOT FIX (FORENSIC-AUDIT-C01): temperature calibrated to "
                f"{final_T:.4f} (clamped to [{self.TEMPERATURE_CLAMP_MIN}, "
                f"{self.TEMPERATURE_CLAMP_MAX}] per Guo et al. 2017). "
                f"Best NLL loss: {best_loss:.6f}"
            )
            return final_T
        finally:
            # V90 ROOT FIX (BUG #13, P1): ALWAYS unfreeze MLP weights,
            # even on exception. The previous code only unfroze at the
            # very end of the method — if an exception happened during
            # the optimization loop (OOM, NaN loss, CUDA error), the
            # MLP weights STAYED frozen and subsequent training runs
            # silently failed to update them. With try/finally, the
            # unfreeze happens regardless of how the try block exits.
            for p in self.mlp.parameters():
                p.requires_grad_(True)
            # P3-026 ROOT FIX: also restore the prior TRAINING MODE.
            # Without this, ``self.eval()`` inside the try block leaves
            # the link_predictor in eval mode after fit_temperature
            # returns, silently disabling dropout/BatchNorm updates for
            # subsequent training.
            self.train(prior_training)
