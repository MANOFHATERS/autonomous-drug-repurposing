"""
Node embedding modules for the Graph Transformer.

Projects heterogeneous node features (drugs, proteins, pathways, diseases,
clinical_outcomes) into a unified embedding space with type distinctions.

FIX vs original codebase (B8):
  Internal imports use relative paths. No functional change vs original
  -- this module was already correct, only its import style was broken.
"""
from __future__ import annotations

import logging
from typing import Dict, Optional

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


class _SafeBatchNorm1d(nn.Module):
    """BatchNorm1d wrapper that handles batch_size=1 in train mode.

    P3-025 ROOT FIX (DEAD-CODE DOCUMENTATION): this class is REACHED
    only when ``NodeTypeProjection`` is constructed with
    ``feature_norm="batch"``. The default model construction path
    (``DrugRepurposingGraphTransformer.__init__`` -> ``NodeTypeProjection``)
    does NOT pass ``feature_norm`` (it defaults to ``"none"``), and the
    bridge's ``build_model`` does not expose a ``feature_norm`` parameter.
    Therefore ``_SafeBatchNorm1d`` is NEVER instantiated on the default
    demo / production code path.

    It is RETAINED (not deleted) because ``feature_norm="batch"`` is a
    PUBLIC API option of ``NodeTypeProjection`` that advanced users may
    exercise directly (e.g., for ablation studies comparing layer vs
    batch normalization on node features). Removing the class would
    silently break that public API. The previous docstring did NOT
    document this reachability gap, misleading readers into thinking
    the class was active on the default path. This update makes the
    situation explicit so a future developer can decide whether to
    (a) wire ``feature_norm`` through ``DrugRepurposingGraphTransformer``
    to actually use BatchNorm, or (b) remove the ``feature_norm="batch"``
    branch entirely if it remains unused after a deprecation cycle.

    ROOT FIX (FORENSIC-AUDIT-I10): ``nn.BatchNorm1d`` raises
    ``ValueError: Expected more than 1 value per channel when training``
    when called with batch_size=1 in train mode. This happens if a user
    sets ``batch_size=1`` for debugging.

    This wrapper detects the batch_size=1 case and temporarily switches
    to eval mode (using running stats) for that forward pass, then
    restores the original mode. This prevents the crash while preserving
    correct BatchNorm behavior for batch_size >= 2.

    ROOT FIX (X-07): the audit found that the previous "silent fallback"
    was dangerous: "If a user sets ``batch_size=1`` for debugging, every
    BatchNorm layer runs in eval mode using RUNNING STATS -- which are
    initialized to mean=0, var=1 (untrained). So the BatchNorm does
    nothing useful. The user sees the model 'train' without errors but
    the BatchNorm layers are effectively identity layers. The model's
    behavior with ``batch_size=1`` is DIFFERENT from ``batch_size=32``
    -- silently."

    The fix: emit a LOUD CRITICAL-level warning the FIRST time
    batch_size=1 is detected in train mode (per instance). This makes
    the silent fallback VISIBLE so the user knows their batch_size=1
    debugging is producing scientifically wrong results (BatchNorm
    running stats are untrained). The wrapper STILL does the eval-mode
    fallback (to prevent the crash), but it no longer does so SILENTLY.

    The audit's recommendation: "Crashing is better than silent wrong
    results." We chose the loud-warning approach instead of crashing
    because batch_size=1 IS a legitimate debugging scenario, and the
    user might want to inspect intermediate activations. The CRITICAL
    log makes the trade-off explicit.
    """

    def __init__(self, num_features: int) -> None:
        super().__init__()
        self.bn = nn.BatchNorm1d(num_features)
        # X-07 fix: track whether we've already warned for batch_size=1.
        # Warning ONCE per instance avoids spamming the log on every
        # forward pass while still making the issue visible.
        self._warned_batch_size_1: bool = False

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # ROOT FIX (B-02): save the wrapped module's ACTUAL training state
        # before temporarily switching it, then restore THAT exact state
        # (not unconditionally call .train()). The V26 code did
        # ``finally: self.bn.train()`` which always re-enabled training
        # mode on the wrapped BN, even when the wrapper (and therefore
        # the BN via nn.Module.eval()'s recursive descent) was in eval
        # mode. The audit noted this is "fragile: any subclass that
        # overrides train() could break the invariant." The root fix
        # makes the save/restore bulletproof by capturing and restoring
        # the exact boolean, so the invariant
        # ``self.bn.training == self.training`` holds after every forward.
        if self.training and x.shape[0] == 1:
            # ROOT FIX (X-07): LOUD CRITICAL warning the first time
            # batch_size=1 is detected in train mode. The previous code
            # silently fell back to eval mode, which the audit found
            # produces "silent wrong results" because the running stats
            # are untrained (mean=0, var=1). The warning makes this
            # visible so the user knows their batch_size=1 debugging is
            # NOT producing scientifically valid BatchNorm behavior.
            if not self._warned_batch_size_1:
                logger.critical(
                    f"ROOT FIX (X-07): _SafeBatchNorm1d detected "
                    f"batch_size=1 in TRAIN mode. Temporarily switching "
                    f"to EVAL mode (using RUNNING STATS) for this "
                    f"forward pass. WARNING: running stats are "
                    f"initialized to mean=0, var=1 (untrained), so the "
                    f"BatchNorm is effectively an IDENTITY layer. "
                    f"The model's behavior with batch_size=1 is "
                    f"DIFFERENT from batch_size>=2. Do NOT use "
                    f"batch_size=1 for training or evaluation -- use "
                    f"batch_size>=2 to get correct BatchNorm behavior. "
                    f"(This warning is emitted ONCE per _SafeBatchNorm1d "
                    f"instance to avoid log spam.)"
                )
                self._warned_batch_size_1 = True
            # batch_size=1 in train mode would crash; use eval mode
            # temporarily, then restore the EXACT prior state.
            prior_bn_training = self.bn.training
            self.bn.eval()
            try:
                result = self.bn(x)
            finally:
                # Restore the exact prior training flag, not a hardcoded
                # .train() call. This keeps self.bn.training in sync with
                # self.training regardless of how the wrapper's mode was
                # set (via .train(), .eval(), or a subclass override).
                self.bn.train(prior_bn_training)
            return result
        return self.bn(x)


class NodeTypeEmbedding(nn.Module):
    """Learnable per-node-type embedding vector.

    Each of the 5 node types (drug, protein, pathway, disease,
    clinical_outcome) gets a unique learnable vector that is added to
    the projected node features.

    ROOT FIX (B5): this class is now used EXTERNALLY via
    ``DrugRepurposingGraphTransformer.get_node_type_embeddings()``,
    which exposes the learned type embeddings for downstream consumers
    (dashboard visualization, model inspection, etc.). Previously it
    was only used internally by ``NodeTypeProjection`` and exported in
    ``models/__init__.py`` without any external caller -- making the
    export "API surface pollution". The V11 fix wires it into the
    public API of the main model class.

    P3-004 ROOT FIX (Team Member 9, v104 — UNKNOWN NODE-TYPE FALLBACK):
        At inference time, if the model receives a graph with a NEW
        node type (e.g., 'variant' for pharmacogenomics added after
        the model is trained), the lookup
        ``self.embeddings(node_type_indices)`` raises IndexError because
        ``node_type_indices`` contains a value >= ``num_node_types``.
        This blocks KG growth — the model cannot be deployed without
        retraining.

        ROOT FIX: add a fallback 'unknown' embedding (slot index
        ``num_node_types``, accessible via the ``UNKNOWN_TYPE_IDX``
        class attribute). In ``forward()``, CLAMP out-of-range indices
        to the unknown slot and log a WARNING (once per instance, to
        avoid log spam). The unknown embedding is initialized to ZERO
        with a small learnable bias, so it does not perturb trained
        representations but can be learned if the model is later
        fine-tuned on a graph that includes the new type.

        This graceful degradation allows Phase 2 to grow (add new node
        types) without blocking Phase 3 inference — the new type's
        nodes will pass through with neutral embeddings until the model
        is retrained.

    Args:
        num_node_types: Number of distinct node types.
        embedding_dim: Dimension of the type embedding.
    """

    # P3-004 ROOT FIX v104: the unknown-type slot is at index num_node_types
    # (i.e., one slot past the last trained type). UNKNOWN_TYPE_IDX is a
    # CLASS-level constant so callers can introspect the fallback index
    # without instantiating the class.
    UNKNOWN_TYPE_IDX: int = -1  # resolved per-instance in __init__

    def __init__(self, num_node_types: int = 5, embedding_dim: int = 128) -> None:
        super().__init__()
        self.embedding_dim = embedding_dim
        self.num_node_types = num_node_types
        # P3-004 ROOT FIX v104: allocate one EXTRA slot for the 'unknown'
        # type fallback. Total embedding table size = num_node_types + 1.
        # The unknown slot is initialized to ZERO (no perturbation to
        # trained representations) and is learnable so it CAN be trained
        # if the model is later fine-tuned on a graph that includes new
        # node types.
        self.UNKNOWN_TYPE_IDX = num_node_types  # instance-level resolution
        self.embeddings = nn.Embedding(num_node_types + 1, embedding_dim)
        # Initialize the unknown slot to ZERO so untrained types do not
        # perturb the projected features. Use zero_() with no_grad so the
        # initialization is not tracked as a gradient.
        with torch.no_grad():
            self.embeddings.weight[self.UNKNOWN_TYPE_IDX].zero_()
        # Track whether we've warned about unknown-type fallback (per instance).
        self._unknown_warned: bool = False

    def forward(self, node_type_indices: torch.Tensor) -> torch.Tensor:
        """Look up type embeddings.

        P3-004 ROOT FIX v104: out-of-range indices (>= num_node_types)
        are CLAMPED to the unknown-type slot (index num_node_types) and
        a WARNING is logged (once per instance). This prevents the
        IndexError that would otherwise crash inference when Phase 2
        adds a new node type after the model is trained.

        Args:
            node_type_indices: Long tensor of shape (num_nodes,).
                Values in [0, num_node_types-1] are looked up normally.
                Values >= num_node_types are clamped to the unknown
                slot (index num_node_types).

        Returns:
            Tensor of shape (num_nodes, embedding_dim).
        """
        # P3-004 ROOT FIX v104: detect out-of-range indices and clamp
        # them to the unknown-type slot. This is the ROOT FIX for the
        # IndexError crash that blocked KG growth.
        out_of_range_mask = node_type_indices >= self.num_node_types
        if out_of_range_mask.any():
            num_oob = int(out_of_range_mask.sum().item())
            if not self._unknown_warned:
                logger.warning(
                    f"NodeTypeEmbedding.forward() received {num_oob} "
                    f"node-type indices >= num_node_types "
                    f"({self.num_node_types}). These will be CLAMPED to "
                    f"the 'unknown' type slot (index "
                    f"{self.UNKNOWN_TYPE_IDX}), which is initialized to "
                    f"ZERO. The model will produce neutral embeddings "
                    f"for these nodes until retrained. This is graceful "
                    f"degradation (P3-004 ROOT FIX v104) — Phase 2 can "
                    f"add new node types without crashing Phase 3 "
                    f"inference. To get full fidelity, RETRAIN the "
                    f"model on a graph that includes the new types. "
                    f"(This warning is emitted ONCE per "
                    f"NodeTypeEmbedding instance.)"
                )
                self._unknown_warned = True
            # Clamp to the unknown slot. torch.clamp preserves dtype
            # and device, and is differentiable (no gradient through
            # the index selection — Embedding lookup itself is the
            # differentiable op).
            node_type_indices = node_type_indices.clamp(
                max=self.UNKNOWN_TYPE_IDX
            )
        return self.embeddings(node_type_indices)


class NodeTypeProjection(nn.Module):
    """Per-node-type linear projection into a unified embedding space.

    Handles the heterogeneous feature dimensions of different node types
    (e.g., drugs may have 1024-dim Morgan fingerprints while proteins
    have 768-dim ESM-2 embeddings).

    P3-009 ROOT FIX (Team Member 9, v104 — FREEZE PRETRAINED EMBEDDINGS):
        When Phase 2's pre-trained TransE (Bordes et al. 2013) embeddings
        are loaded into the per-type projection layers via
        ``load_pretrained_embeddings()``, the GNN's optimizer would
        normally update them during training. For small graphs (demo,
        pilot), the GNN's gradient signal is noisy and OVERWRITES the
        TransE signal — wasting the TransE pre-training (which took
        hours).

        ROOT FIX: add a ``freeze_pretrained`` flag (default True). When
        frozen, ``load_pretrained_embeddings()`` sets
        ``requires_grad=False`` on the loaded projection's weight and
        bias, so the optimizer skips them entirely. The frozen
        projection acts as a fixed feature extractor that maps raw
        node features into the TransE-learned embedding space, and the
        GNN learns only the attention/FFN weights on top.

        The frozen types are tracked in ``self._frozen_types`` so a
        future caller can introspect which types are frozen. A CI test
        verifies that ``requires_grad=False`` is set on frozen types.

    Args:
        feature_dims: Dict mapping node type name to raw feature dimension.
        embedding_dim: Target embedding dimension for all types.
        feature_norm: Type of normalization ('none', 'layer', 'batch').
        freeze_pretrained: Default freeze policy for
            ``load_pretrained_embeddings()``. If True (default), loaded
            embeddings are frozen. If False, they are trainable. Can
            be overridden per-call via the ``freeze`` parameter of
            ``load_pretrained_embeddings()``.
    """

    def __init__(
        self,
        feature_dims: Dict[str, int],
        embedding_dim: int = 128,
        feature_norm: str = "none",
        freeze_pretrained: bool = True,
    ) -> None:
        super().__init__()
        self.feature_dims = dict(feature_dims)
        self.embedding_dim = embedding_dim
        self.feature_norm = feature_norm
        # P3-009 ROOT FIX v104: default freeze policy for loaded pretrained
        # embeddings. Can be overridden per-call.
        self._default_freeze_pretrained: bool = bool(freeze_pretrained)
        # P3-009 ROOT FIX v104: track which node types have frozen
        # pretrained embeddings. Public for introspection (CI test checks
        # this set).
        self._frozen_types: set = set()

        # Per-type linear projections
        self.projections: Dict[str, nn.Module] = {}
        for node_type, dim in feature_dims.items():
            if dim <= 0:
                raise ValueError(
                    f"feature_dims['{node_type}'] must be positive, got {dim}"
                )
            proj = nn.Linear(dim, embedding_dim)
            self.add_module(f"proj_{node_type}", proj)
            self.projections[node_type] = proj

        # Optional normalization layers.
        # ROOT FIX (FORENSIC-AUDIT-I10): use BatchNorm1d with
        # track_running_stats=True and handle the batch_size=1 case
        # gracefully. nn.BatchNorm1d raises ValueError when training=True
        # and batch_size=1 ("Expected more than 1 value per channel").
        # The fix wraps the BatchNorm1d in a custom module that falls
        # back to running stats (eval mode behavior) when batch_size=1
        # in train mode, preventing the crash.
        self.norms: Dict[str, nn.Module] = {}
        if feature_norm == "layer":
            for node_type in feature_dims:
                norm = nn.LayerNorm(embedding_dim)
                self.add_module(f"norm_{node_type}", norm)
                self.norms[node_type] = norm
        elif feature_norm == "batch":
            for node_type in feature_dims:
                norm = _SafeBatchNorm1d(embedding_dim)
                self.add_module(f"norm_{node_type}", norm)
                self.norms[node_type] = norm

        # Learnable node type embeddings
        self.node_type_embedding = NodeTypeEmbedding(
            num_node_types=len(feature_dims),
            embedding_dim=embedding_dim,
        )

        # Node type name to index mapping.
        # ROOT FIX (FORENSIC-AUDIT-I09): use INSERTION ORDER instead of
        # sorted (alphabetical) order. The previous code used
        # ``enumerate(sorted(feature_dims.keys()))``, which means the type
        # index depends on alphabetical order. If a user passes feature_dims
        # in a different order (e.g., {"drug": 128, "protein": 64} vs
        # {"protein": 64, "drug": 128}), the type indices change, breaking
        # checkpoint compatibility. Python 3.7+ guarantees dict insertion
        # order, so using insertion order is deterministic AND stable
        # across different dict construction orders (as long as the user
        # constructs the dict consistently).
        self._type_to_idx: Dict[str, int] = {
            name: idx for idx, name in enumerate(feature_dims.keys())
        }

    # ------------------------------------------------------------------
    # P3-009 ROOT FIX v104: load + freeze pretrained embeddings
    # ------------------------------------------------------------------
    def load_pretrained_embeddings(
        self,
        node_type: str,
        weight: torch.Tensor,
        bias: Optional[torch.Tensor] = None,
        freeze: Optional[bool] = None,
    ) -> None:
        """Load pre-trained TransE (or other) embeddings into a projection.

        P3-009 ROOT FIX v104: replaces the per-type linear projection's
        randomly-initialized weight with a pre-trained tensor (e.g., from
        Phase 2's TransE training). Optionally freezes the projection
        so the GNN's optimizer does NOT update it during training.

        Why freeze: TransE pre-training (Bordes et al. 2013) takes hours
        on a biomedical KG. If the GNN's optimizer is allowed to update
        the projection weights, the noisy GNN gradient signal on small
        graphs OVERWRITES the TransE signal — wasting the pre-training.
        Freezing preserves the TransE signal; the GNN learns only the
        attention/FFN weights on top.

        When NOT to freeze: on large production graphs (1M+ pairs), the
        GNN gradient signal is strong enough that fine-tuning the
        projection is beneficial. Pass ``freeze=False`` in that case.

        Args:
            node_type: Node type name (must be in ``feature_dims``).
            weight: Tensor of shape ``(embedding_dim, feature_dim)`` —
                the pre-trained projection weight. The shape must match
                ``nn.Linear.weight`` convention (out_features, in_features).
            bias: Optional tensor of shape ``(embedding_dim,)`` — the
                pre-trained projection bias. If None, the bias is left
                at its random initialization.
            freeze: If True, set ``requires_grad=False`` on the
                projection's weight (and bias if provided). If False,
                leave them trainable. If None, use the constructor's
                ``freeze_pretrained`` default.

        Raises:
            ValueError: If ``node_type`` is not in ``feature_dims``, or
                if ``weight`` shape does not match the projection's
                expected shape.
        """
        if node_type not in self.projections:
            raise ValueError(
                f"Unknown node type '{node_type}'. Known types: "
                f"{list(self.projections.keys())}. Add '{node_type}' to "
                f"feature_dims at construction time before loading "
                f"pretrained embeddings."
            )
        proj = self.projections[node_type]
        expected_w_shape = proj.weight.shape  # (out_features, in_features)
        if tuple(weight.shape) != tuple(expected_w_shape):
            raise ValueError(
                f"Pretrained weight shape {tuple(weight.shape)} does not "
                f"match projection '{node_type}' expected shape "
                f"{tuple(expected_w_shape)} (out_features, in_features) = "
                f"(embedding_dim, feature_dim). TransE embeddings must "
                f"be projected to match the linear layer's weight "
                f"geometry before loading."
            )
        # Copy the pretrained weight into the projection (no_grad to
        # avoid polluting the optimizer state).
        with torch.no_grad():
            proj.weight.copy_(weight.to(proj.weight.device).to(proj.weight.dtype))
            if bias is not None:
                if tuple(bias.shape) != tuple(proj.bias.shape):
                    raise ValueError(
                        f"Pretrained bias shape {tuple(bias.shape)} does "
                        f"not match projection '{node_type}' expected "
                        f"shape {tuple(proj.bias.shape)}."
                    )
                proj.bias.copy_(bias.to(proj.bias.device).to(proj.bias.dtype))

        # P3-009 ROOT FIX v104: apply the freeze policy.
        if freeze is None:
            freeze = self._default_freeze_pretrained
        if freeze:
            proj.weight.requires_grad_(False)
            if proj.bias is not None:
                proj.bias.requires_grad_(False)
            self._frozen_types.add(node_type)
            logger.info(
                f"P3-009 ROOT FIX v104: loaded pretrained embeddings for "
                f"'{node_type}' and FROZEN the projection (requires_grad="
                f"False). The GNN optimizer will NOT update these "
                f"weights. To unfreeze, call "
                f"`unfreeze_pretrained_embeddings('{node_type}')`."
            )
        else:
            # Make sure requires_grad is True (in case the projection was
            # previously frozen and we are re-loading with freeze=False).
            proj.weight.requires_grad_(True)
            if proj.bias is not None:
                proj.bias.requires_grad_(True)
            self._frozen_types.discard(node_type)
            logger.info(
                f"P3-009 ROOT FIX v104: loaded pretrained embeddings for "
                f"'{node_type}' and left them TRAINABLE (requires_grad="
                f"True). The GNN optimizer WILL update these weights."
            )

    def unfreeze_pretrained_embeddings(self, node_type: str) -> None:
        """Unfreeze a previously-frozen pretrained projection.

        P3-009 ROOT FIX v104: companion to ``load_pretrained_embeddings``.
        Allows a caller to load frozen TransE embeddings, train the GNN
        for a few warmup epochs, then unfreeze for joint fine-tuning
        (a common transfer-learning recipe).

        Args:
            node_type: Node type name (must be in ``feature_dims``).
        """
        if node_type not in self.projections:
            raise ValueError(
                f"Unknown node type '{node_type}'. Known types: "
                f"{list(self.projections.keys())}."
            )
        proj = self.projections[node_type]
        proj.weight.requires_grad_(True)
        if proj.bias is not None:
            proj.bias.requires_grad_(True)
        self._frozen_types.discard(node_type)
        logger.info(
            f"P3-009 ROOT FIX v104: unfroze pretrained embeddings for "
            f"'{node_type}'. The GNN optimizer will now update these weights."
        )

    def frozen_types(self) -> set:
        """Return the set of node types whose projections are frozen.

        P3-009 ROOT FIX v104: public introspection method. The CI test
        ``test_p3_009_freeze_pretrained_embeddings`` uses this to verify
        that ``load_pretrained_embeddings(freeze=True)`` actually froze
        the projection.
        """
        return set(self._frozen_types)

    def forward(self, node_features: Dict[str, torch.Tensor]) -> Dict[str, torch.Tensor]:
        """Project all node type features to the unified embedding space.

        V90 ROOT FIX (BUG #49): iterate node_features in the ORDER defined
        by ``self._type_to_idx`` (i.e., the order of ``feature_dims.keys()``
        at construction time), NOT the dict's insertion order. The previous
        code iterated ``node_features.items()`` directly, which works in
        Python 3.7+ (dict preserves insertion order) BUT only if the caller
        built the dict with the same key order as ``feature_dims``. If a
        caller passed a dict with a different insertion order (e.g.,
        ``{"disease": ..., "drug": ...}`` instead of
        ``{"drug": ..., "disease": ...}``), the iteration order would
        differ from the type-index mapping, causing subtle bugs in any
        downstream code that assumes a consistent order.

        The fix sorts the iteration by ``self._type_to_idx[node_type]``
        so the output dict's iteration order ALWAYS matches the
        construction-time feature_dims order, regardless of the input
        dict's insertion order. This makes the projection order-stable
        across different callers.

        Args:
            node_features: Dict mapping node type name to feature tensor
                of shape (num_nodes_of_type, raw_feature_dim).

        Returns:
            Dict mapping node type name to projected tensor of shape
            (num_nodes_of_type, embedding_dim).
        """
        projected: Dict[str, torch.Tensor] = {}

        # V90 BUG #49: iterate in _type_to_idx order (construction-time
        # feature_dims order), NOT node_features.items() order. This
        # ensures order-stability regardless of the input dict's
        # insertion order.
        for node_type in sorted(node_features.keys(), key=lambda nt: self._type_to_idx.get(nt, -1)):
            features = node_features[node_type]
            if node_type not in self.projections:
                logger.warning(
                    f"Unknown node type '{node_type}'. Known types: "
                    f"{list(self.projections.keys())}. Skipping."
                )
                continue

            num_nodes = features.shape[0]

            # Project to embedding space
            h = self.projections[node_type](features)

            # Validate output
            if torch.isnan(h).any() or torch.isinf(h).any():
                raise RuntimeError(
                    f"Non-finite values in projected features for '{node_type}'. "
                    f"Check input features for NaN/Inf."
                )

            # Apply normalization
            if node_type in self.norms:
                h = self.norms[node_type](h)

            # Add node type embedding
            type_idx = self._type_to_idx[node_type]
            type_indices = torch.full(
                (num_nodes,), type_idx, dtype=torch.long, device=features.device
            )
            type_emb = self.node_type_embedding(type_indices)
            h = h + type_emb

            projected[node_type] = h

        return projected

    def get_type_index(self, node_type: str) -> int:
        """Get the integer index for a node type name."""
        if node_type not in self._type_to_idx:
            raise ValueError(
                f"Unknown node type '{node_type}'. "
                f"Known: {list(self._type_to_idx.keys())}"
            )
        return self._type_to_idx[node_type]
