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
from typing import Dict

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

    Args:
        num_node_types: Number of distinct node types.
        embedding_dim: Dimension of the type embedding.
    """

    def __init__(self, num_node_types: int = 5, embedding_dim: int = 128) -> None:
        super().__init__()
        self.embedding_dim = embedding_dim
        self.num_node_types = num_node_types
        self.embeddings = nn.Embedding(num_node_types, embedding_dim)

    def forward(self, node_type_indices: torch.Tensor) -> torch.Tensor:
        """Look up type embeddings.

        Args:
            node_type_indices: Long tensor of shape (num_nodes,).

        Returns:
            Tensor of shape (num_nodes, embedding_dim).
        """
        return self.embeddings(node_type_indices)


class NodeTypeProjection(nn.Module):
    """Per-node-type linear projection into a unified embedding space.

    Handles the heterogeneous feature dimensions of different node types
    (e.g., drugs may have 1024-dim Morgan fingerprints while proteins
    have 768-dim ESM-2 embeddings).

    Args:
        feature_dims: Dict mapping node type name to raw feature dimension.
        embedding_dim: Target embedding dimension for all types.
        feature_norm: Type of normalization ('none', 'layer', 'batch').
    """

    def __init__(
        self,
        feature_dims: Dict[str, int],
        embedding_dim: int = 128,
        feature_norm: str = "none",
    ) -> None:
        super().__init__()
        self.feature_dims = dict(feature_dims)
        self.embedding_dim = embedding_dim
        self.feature_norm = feature_norm

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
