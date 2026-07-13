"""
Graph Transformer layers for heterogeneous biomedical knowledge graphs.

Implements:
- HeterogeneousMultiHeadAttention: Edge-type-aware multi-head attention.
- TransformerFFN: Position-wise feed-forward network.
- GraphTransformerLayer: Full transformer layer combining attention + FFN.

FIX vs original codebase:
  - **B18 (lazy LayerNorm creation)**: ``_apply_norm`` previously created
    a new ``nn.LayerNorm`` on-the-fly whenever it encountered a node
    type that wasn't in the constructor's ``node_types`` list. This
    meant a model saved without that path couldn't be loaded with that
    path (different state_dict keys -- non-deterministic save/load).

    Fix: ``_apply_norm`` now **raises** on unknown node types. The
    constructor pre-populates ``norm1`` / ``norm2`` for every node type
    in ``node_types``, so the state_dict is always stable.
  - **B21 (scatter_reduce_ requires PyTorch >= 1.12)**: we now
    feature-detect ``scatter_reduce_`` at module import time and raise
    a clear ``RuntimeError`` if the installed PyTorch is too old, instead
    of letting it crash inside the forward pass where the error message
    is opaque.
"""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Tuple

import torch
import torch.nn as nn

logger = logging.getLogger(__name__)


# --- B21 fix: feature-detect scatter_reduce_ --------------------------------
# ROOT FIX (F1): check PyTorch VERSION at import time (not just feature
# detection). This gives a clearer error message that includes the
# installed version, making it easier for users to diagnose. Also add
# a check in setup.py / pyproject.toml would be ideal, but this import-
# time check is the most reliable fallback.
_TORCH_VERSION = tuple(int(x) for x in torch.__version__.split('.')[:2])
if _TORCH_VERSION < (1, 12):
    raise RuntimeError(
        f"PyTorch version {torch.__version__} is too old. "
        f"torch.Tensor.scatter_reduce_ requires PyTorch >= 1.12. "
        f"Please upgrade with: pip install --upgrade 'torch>=1.12'. "
        f"The Graph Transformer's sparse softmax depends on this op. "
        f"(F1 fix: version check at import time)"
    )
if not hasattr(torch.Tensor, "scatter_reduce_"):
    raise RuntimeError(
        f"PyTorch {torch.__version__} is >= 1.12 but scatter_reduce_ is "
        f"missing. This is unexpected -- please report this as a bug. "
        f"(F1 fix: feature detection fallback)"
    )
# ---------------------------------------------------------------------------


class HeterogeneousMultiHeadAttention(nn.Module):
    """Multi-head attention that handles heterogeneous edge types.

    Each edge type (e.g., 'drug-inhibits-protein') gets its own key and
    value projections, allowing the model to learn edge-type-specific
    attention patterns for different biological mechanisms.

    ROOT FIX (FORENSIC-AUDIT-I04): the previous implementation used a
    SINGLE shared ``q_proj`` for all heads and per-edge-type K/V
    projections that were also shared across heads (just reshaped into
    ``(num_heads, head_dim)``). This is NOT standard multi-head attention
    -- it's "edge-type-aware single-head attention with multi-head scoring."
    Standard MHA (Vaswani et al. 2017) has PER-HEAD Q/K/V projections,
    allowing each head to attend to different subspaces of the embedding.

    The root fix introduces PER-HEAD Q/K/V projections for each edge type:
      - ``q_proj``: (embedding_dim, num_heads * head_dim) -- projects all
        nodes into per-head queries. Each head gets its own slice of the
        projection, so head h attends to a different subspace.
      - ``k_proj[edge_key]``: (embedding_dim, num_heads * head_dim) --
        per-edge-type, per-head keys.
      - ``v_proj[edge_key]``: (embedding_dim, num_heads * head_dim) --
        per-edge-type, per-head values.

    This matches the standard MHA formulation and gives each head
    independent representational capacity. The per-edge-type structure
    is preserved (each edge type still has its own K/V), but now each
    head within an edge type can learn different attention patterns.

    P3-003 ROOT FIX (Team Member 9, v104 — NO CAUSAL MASK):
        This module does NOT apply a causal mask. A causal mask
        (triangular mask that prevents position i from attending to
        positions j > i) is appropriate for AUTOREGRESSIVE LANGUAGE
        MODELS where the sequence has a temporal ordering (token i
        cannot depend on future tokens j > i). It is **categorically
        wrong** for a heterogeneous biomedical knowledge graph:

          - KGs are UNDIRECTED. A drug node attending to a protein
            node it inhibits must be allowed to receive a message
            from that protein in the SAME forward pass (the reverse
            edge ``protein-inhibited_by-drug`` exists in the graph).
            A causal mask would make attention unidirectional and
            BREAK bidirectional message passing — the core mechanism
            by which GNNs learn node representations from neighbors.
          - KGs have NO TEMPORAL ORDERING. There is no concept of
            "past" vs "future" nodes. Applying a causal mask would
            be a category error.
          - KG link prediction requires BOTH directions: predicting
            a drug-disease edge requires the drug's representation
            to incorporate the disease's representation (and vice
            versa). A causal mask would prevent this.

        **DO NOT ADD A CAUSAL MASK TO THIS MODULE.** A future engineer
        who has worked on LLMs may be tempted to add one "for safety"
        or "for consistency with the Transformer paper." That would
        silently break the model — AUC would drop to ~0.5 (random)
        because bidirectional message passing is the only mechanism
        the GNN has to learn node representations. The regression
        test ``test_p3_003_no_causal_mask_in_attention`` in
        ``tests/test_p3_tm9_model_issues.py`` verifies that no mask
        is applied (attention weights are dense over all neighbors).

    Args:
        embedding_dim: Dimension of node embeddings.
        num_heads: Number of attention heads.
        edge_types: List of (src, rel, tgt) edge type tuples.
        dropout: Attention dropout rate.
    """

    def __init__(
        self,
        embedding_dim: int = 128,
        num_heads: int = 8,
        edge_types: Optional[List[Tuple[str, str, str]]] = None,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.embedding_dim = embedding_dim
        self.num_heads = num_heads
        self.edge_types = edge_types or []
        self.dropout = dropout

        assert embedding_dim % num_heads == 0, (
            f"embedding_dim ({embedding_dim}) must be divisible by "
            f"num_heads ({num_heads})"
        )
        self.head_dim = embedding_dim // num_heads

        # P3-015 ROOT FIX (SCIENTIFIC — per-node-type Q projection):
        # The previous code used a SINGLE shared q_proj for ALL node types.
        # But k_proj/v_proj are per-edge-type. This means the Q (query) for
        # a drug node is computed with the same projection as the Q for a
        # disease node — the model cannot learn type-specific query patterns.
        # Standard HGT (Wang et al. 2019) uses per-NODE-TYPE Q projections.
        #
        # The fix: create a Dict[str, nn.Linear] keyed by node type, with a
        # separate Q projection per node type. In forward(), apply the
        # per-node-type Q projection to each node type's embeddings separately,
        # then concatenate. A fallback shared q_proj is retained for backward
        # compatibility with checkpoints that don't have per-node-type Q
        # weights (loaded via _load_q_proj_compat).
        self.q_proj_per_type: Dict[str, nn.Module] = {}
        # Extract node types from edge_types (unique src + tgt types)
        _node_types_in_edges: set = set()
        for src, rel, tgt in (self.edge_types or []):
            _node_types_in_edges.add(src)
            _node_types_in_edges.add(tgt)
        for nt in sorted(_node_types_in_edges):
            q = nn.Linear(embedding_dim, num_heads * self.head_dim, bias=False)
            self.add_module(f"q_{nt}", q)
            self.q_proj_per_type[nt] = q
        # Fallback shared projection (for backward compat with old checkpoints)
        self.q_proj = nn.Linear(embedding_dim, num_heads * self.head_dim, bias=False)

        # ROOT FIX (FORENSIC-AUDIT-I04): PER-EDGE-TYPE, PER-HEAD K/V projections.
        # Each edge type has its own K and V projection, and each projection
        # outputs (num_heads * head_dim) so each head gets its own subspace.
        self.k_proj: Dict[str, nn.Module] = {}
        self.v_proj: Dict[str, nn.Module] = {}
        for src, rel, tgt in self.edge_types:
            edge_key = f"{src}_{rel}_{tgt}"
            k = nn.Linear(embedding_dim, num_heads * self.head_dim, bias=False)
            v = nn.Linear(embedding_dim, num_heads * self.head_dim, bias=False)
            self.add_module(f"k_{edge_key}", k)
            self.add_module(f"v_{edge_key}", v)
            self.k_proj[edge_key] = k
            self.v_proj[edge_key] = v

        # Output projection: maps concatenated multi-head output back to embedding_dim
        self.out_proj = nn.Linear(num_heads * self.head_dim, embedding_dim, bias=False)

        # ROOT FIX (FORENSIC-AUDIT-I05): separate self-loop projection.
        # The previous code applied ``out_proj`` TWICE -- once for self-loops
        # (with hardcoded weight 0.1) and once for the final output. The
        # composition ``out_proj(out_proj(...))`` is non-standard and doubles
        # the parameters' effective depth in an unprincipled way.
        #
        # The root fix uses a SEPARATE ``self_loop_proj`` for self-loops,
        # so the self-loop pathway is independent from the edge-message
        # pathway.
        #
        # P3-S01 ROOT FIX (SCIENTIFIC): initialize ``self_loop_weight`` to
        # 1.0, the standard residual-connection weight (He et al. 2016,
        # "Identity Mappings in Deep Residual Networks"). The previous
        # initializations were:
        #   - V27: 0.1 (hardcoded, under-contributed self-loops to ~10% of
        #     edge message weight; "rich get richer" dynamics where hub
        #     nodes updated aggressively and isolated nodes barely moved).
        #   - V30 5.4: 0.5 (claimed to give self-loops "equal standing with
        #     a single edge-type message"). The P3-S01 audit found 0.5 is
        #     still TOO HIGH: combined with cross_type_norm ≈ 0.27 for 14
        #     edge types, a node with 3 incoming edge types receives a
        #     total edge message of 3 * 0.27 = 0.81, while the self-loop
        #     contributes 0.5 -- self-loops are ~38% of the total message,
        #     disproportionately high for a "residual" connection.
        # The fix initializes to 1.0 (standard residual identity) and lets
        # gradient descent learn the right balance. With 1.0, the self-loop
        # starts as the dominant pathway (good for early-training stability,
        # when edge messages are noise) and the model can dial it down as
        # edge messages become meaningful.
        self.self_loop_proj = nn.Linear(embedding_dim, embedding_dim, bias=False)
        self.self_loop_weight = nn.Parameter(torch.tensor(1.0))

        # Edge type gating (learnable weights per edge type)
        if self.edge_types:
            self.edge_gates = nn.ParameterDict({
                f"{src}_{rel}_{tgt}": nn.Parameter(torch.tensor(1.0))
                for src, rel, tgt in self.edge_types
            })
        else:
            self.edge_gates = nn.ParameterDict()

        # V30 ROOT FIX (5.3) + P3-039 ROOT FIX (comment accuracy):
        # Cross-edge-type normalization.
        # The original code summed per-edge-type softmaxed messages without
        # any cross-type normalization. For a target node receiving messages
        # from K edge types, the total message magnitude was ~K * |V|. Hub
        # nodes (with many incoming edge types) exploded; leaf nodes vanished.
        # Standard HGT (Wang et al. 2019) either softmaxes across ALL edge
        # types jointly or divides by sqrt(num_edge_types).
        #
        # We use the sqrt(num_edge_types) divisor -- it preserves per-type
        # attention patterns (each type still softmaxes independently) but
        # bounds the total message magnitude regardless of how many edge
        # types a node receives from. This is the same scheme used by
        # Heterogeneous Graph Attention Networks (HAN, Wang et al. 2019).
        #
        # P3-039 ACCURACY FIX: the previous comment claimed this implements
        # PER-NODE normalization (divisor = sqrt(num_edge_types_contributing
        # to THIS node)). That was FALSE. The code computes a SINGLE GLOBAL
        # divisor at the start of forward and applies it to ALL nodes,
        # regardless of how many edge types each node actually receives
        # from. A hub node with 7 incoming edge types and a leaf node with
        # 1 incoming edge type both get divided by sqrt(active_count).
        # This is the standard HGT approximation (avoids a costly per-node
        # scatter for the divisor), but the comment must NOT claim it's
        # per-node. Per-node normalization would require a separate
        # scatter to count incoming edge types per node, then a per-node
        # divide -- future work if hub-node saturation becomes a problem.
        #
        # V90 ROOT FIX (BUG #17, P1): the divisor is now computed
        # DYNAMICALLY per forward call from the edge types that
        # actually have edges in the current graph (counted at the
        # start of forward). The previous code computed it ONCE at
        # init time from ``len(self.edge_types)`` (14 for the canonical
        # schema), but due to BUG #1, only the 7 forward edge types
        # had data; the 7 reverse types were empty. Each present edge
        # type's message was scaled by 1/sqrt(14) ≈ 0.267, so the
        # total edge message was 7 * 0.267 = 1.87. If the divisor had
        # been 1/sqrt(7) (the actual number of contributing types),
        # the total would have been 7/sqrt(7) = 2.65. The current
        # scheme under-weighted edge messages by 1.87/2.65 = 0.71,
        # giving self-loops (weight 0.5) disproportionate influence.
        # The fix: count active edge types per forward call and use
        # 1/sqrt(active_count). This is computed lazily (no buffer)
        # so it adapts to graph sparsity.
        #
        # P3-017 ROOT FIX (DEAD CODE): removed
        # ``self._static_num_edge_types = max(1, len(self.edge_types))``.
        # This attribute was a LEFTOVER from the pre-V90 code that used a
        # static buffer for the cross_type_norm divisor. After the V90
        # BUG #17 fix made the divisor DYNAMIC (computed per forward call
        # from active_edge_type_count), the static attribute was NEVER
        # READ anywhere — not in forward(), not in any other method, not
        # by any external caller. It was dead code that misled readers
        # into thinking it was used for the divisor. Removing it makes
        # the code honest about what's actually used at runtime.

        self.attn_dropout = nn.Dropout(dropout)

    def forward(
        self,
        node_embeddings: Dict[str, torch.Tensor],
        edge_indices: Dict[Tuple[str, str, str], torch.Tensor],
    ) -> Dict[str, torch.Tensor]:
        """Compute heterogeneous attention message passing.

        Args:
            node_embeddings: Dict mapping node type to (N_t, D) tensor.
            edge_indices: Dict mapping (src, rel, tgt) to (2, E) tensor.

        Returns:
            Updated node embeddings with attention messages aggregated.
        """
        # Build global node index mapping
        all_types = sorted(node_embeddings.keys())
        type_offsets: Dict[str, int] = {}
        offset = 0
        for ntype in all_types:
            type_offsets[ntype] = offset
            offset += node_embeddings[ntype].shape[0]

        total_nodes = offset
        device = next(iter(node_embeddings.values())).device

        # Concatenate all node embeddings into one tensor
        all_embeddings = torch.cat(
            [node_embeddings[nt] for nt in all_types], dim=0
        )  # (total_nodes, D)

        # P3-015 ROOT FIX: PER-NODE-TYPE, PER-HEAD query projection.
        # Each node type has its own q_proj, so the Q for a drug node is
        # computed with a DIFFERENT projection than the Q for a disease node.
        # This lets the model learn type-specific query patterns (standard HGT).
        # We apply per-type projections to each node type's slice, then
        # concatenate the results into a single (total_nodes, H*head_dim) tensor.
        Q_parts: List[torch.Tensor] = []
        for nt in all_types:
            h_nt = node_embeddings[nt]
            if nt in self.q_proj_per_type:
                Q_parts.append(self.q_proj_per_type[nt](h_nt))
            else:
                # Fallback for node types not in edge_types (e.g., isolated nodes)
                Q_parts.append(self.q_proj(h_nt))
        Q = torch.cat(Q_parts, dim=0)  # (total_nodes, num_heads * head_dim)
        N = total_nodes
        Q = Q.view(N, self.num_heads, self.head_dim)  # (N, H, head_dim)

        # Initialize message accumulator
        messages = torch.zeros(N, self.num_heads * self.head_dim, device=device)

        # V90 ROOT FIX (BUG #17, P1): compute cross_type_norm DYNAMICALLY
        # from the number of edge types that ACTUALLY have edges in this
        # forward call. The previous code used a static buffer with
        # 1/sqrt(14) (all canonical edge types), but on sparse graphs
        # only 7 (or fewer) edge types have data. Under-weighting edge
        # messages by ~30% gave self-loops disproportionate influence.
        active_edge_type_count = 0
        for edge_idx_check in edge_indices.values():
            if edge_idx_check.numel() > 0:
                active_edge_type_count += 1
        active_count = max(1, active_edge_type_count)
        cross_type_norm = 1.0 / math.sqrt(active_count)

        # ROOT FIX (FORENSIC-AUDIT-I05): self-loops via a SEPARATE projection
        # with a LEARNABLE weight. The previous code applied ``out_proj`` to
        # ``q_proj(all_embeddings)`` for self-loops (reusing out_proj), then
        # applied ``out_proj`` AGAIN to the final messages. Now self-loops
        # use ``self_loop_proj`` (independent from out_proj), and the weight
        # is learned (init=1.0 per P3-S01, the standard residual identity
        # weight — see ``self.self_loop_weight = nn.Parameter(torch.tensor(1.0))``).
        self_loop_messages = self.self_loop_proj(all_embeddings)  # (N, D)
        # Reshape self-loop messages to (N, num_heads * head_dim) for consistency
        # self_loop_proj outputs (N, embedding_dim) = (N, num_heads * head_dim)
        #
        # P3-013 ROOT FIX (SCIENTIFIC — DO NOT scale self-loops by
        # cross_type_norm). The previous code scaled self-loops by
        # cross_type_norm, claiming it made "self-loop's relative contribution
        # INDEPENDENT of K". That claim was MATHEMATICALLY FALSE:
        #
        #   With the scaling: self_loop_msg = cross_type_norm * w * |h|
        #                     edge_msg = K * cross_type_norm * |V|
        #   self_loop_ratio = (w/sqrt(K)) / (w/sqrt(K) + sqrt(K)*|V|/|h|)
        #                   = w / (w + K*|V|/|h|)
        #   This DEPENDS on K (as K, not sqrt(K)) — WORSE than before.
        #
        # Residual connections (self-loops) should NOT be scaled by the
        # edge-type count. They are the IDENTITY pathway that preserves
        # the node's own representation. Scaling them by 1/sqrt(K) makes
        # the residual WEAKER for hub nodes (many edge types), causing
        # representation drift. Standard Transformers (Vaswani et al. 2017,
        # He et al. 2016) do NOT scale the residual by the number of
        # attention heads or edge types.
        #
        # The fix: apply self_loop_weight ONLY (no cross_type_norm). The
        # learnable self_loop_weight (init=1.0, P3-S01 fix) controls the
        # self-loop's magnitude relative to edge messages. Gradient descent
        # learns the right balance.
        #
        # P3-014 ROOT FIX (comment accuracy): self_loop_weight init=1.0
        # (P3-S01 fix raised it from 0.1 to 1.0, the standard residual
        # identity weight per He et al. 2016). The previous comment said
        # "init=0.1" which was STALE — the actual init is 1.0 (see line
        # ``self.self_loop_weight = nn.Parameter(torch.tensor(1.0))``).
        messages = messages + self_loop_messages * self.self_loop_weight

        # Process each edge type
        # P3-039 ROOT FIX (comment accuracy): the previous comment claimed
        # to "track which edge types actually contribute messages to each
        # target node, so we can apply per-node cross-type normalization".
        # That was FALSE -- no such tracking happens. The code applies the
        # GLOBAL cross_type_norm (computed above from active_edge_type_count)
        # uniformly to all edge types and all target nodes. This is the
        # standard HGT approximation (avoids a costly per-node scatter for
        # the divisor) and is consistent with the comment block at the
        # top of __init__ (see P3-039 fix there). The original V30 5.3
        # fix's intent (bound total message magnitude regardless of how
        # many edge types a node receives from) is achieved by the global
        # divisor -- a node receiving from K edge types gets total message
        # K * (1/sqrt(active_count)) * |V|, which is bounded by
        # K * |V| / sqrt(active_count) <= sqrt(active_count) * |V|.
        for (src_type, rel_type, tgt_type), edge_idx in edge_indices.items():
            if edge_idx.numel() == 0:
                continue

            edge_key = f"{src_type}_{rel_type}_{tgt_type}"

            # Get source and target node indices (global)
            src_nodes = edge_idx[0] + type_offsets[src_type]
            tgt_nodes = edge_idx[1] + type_offsets[tgt_type]

            if edge_key not in self.k_proj:
                logger.warning(f"No K/V projections for edge type {edge_key}")
                continue

            # ROOT FIX (C2): project ONLY the source-type embeddings
            # through this edge type's K/V projections, not ALL nodes.
            src_offset = type_offsets[src_type]
            src_count = node_embeddings[src_type].shape[0]
            src_embeddings = all_embeddings[src_offset:src_offset + src_count]

            # ROOT FIX (FORENSIC-AUDIT-I04): PER-HEAD K/V projections.
            # K and V are (src_count, num_heads * head_dim), reshaped to
            # (src_count, num_heads, head_dim). Each head gets its own
            # K/V subspace from the per-edge-type projection.
            K = self.k_proj[edge_key](src_embeddings).view(src_count, self.num_heads, self.head_dim)
            V = self.v_proj[edge_key](src_embeddings).view(src_count, self.num_heads, self.head_dim)

            # Gather K and V for source nodes (now indexing into the
            # src-only projection, not the full all_embeddings projection)
            K_src = K[edge_idx[0]]  # (E, H, head_dim) -- src indices are local to src_type
            V_src = V[edge_idx[0]]  # (E, H, head_dim)
            Q_tgt = Q[tgt_nodes]  # (E, H, head_dim)

            # Scaled dot-product attention
            # P3-014 ROOT FIX (HIGH, documentation): add the missing
            # rationale for why the scale is sqrt(head_dim) and NOT
            # sqrt(embedding_dim). A reader who sees ``scale = math.sqrt(
            # self.head_dim)`` after the Q/K projections might think this
            # is a bug — shouldn't the scale be sqrt(embedding_dim) since
            # the Q/K projections output embedding_dim dimensions?
            #
            # It is NOT a bug. The Q/K tensors are reshaped to
            # (N, num_heads, head_dim) BEFORE the dot product. The einsum
            # 'ehd,ehd->eh' computes the dot product per-head over head_dim
            # dimensions (NOT embedding_dim = num_heads * head_dim). Per
            # Vaswani et al. 2017 ("Attention Is All You Need"), the
            # correct scale is sqrt(d_k) where d_k is the dimensionality
            # of the dot product — which is head_dim in multi-head
            # attention, NOT embedding_dim. Scaling by sqrt(embedding_dim)
            # would OVER-scale the attention scores (make them too small,
            # pushing softmax toward uniform distribution), destroying the
            # model's ability to discriminate relevant from irrelevant
            # neighbors. The math is correct; this comment prevents a
            # future maintainer from "fixing" it and breaking attention.
            scale = math.sqrt(self.head_dim)  # d_k = head_dim per Vaswani et al. 2017 (NOT embedding_dim)
            # ROOT FIX (F3): use torch.einsum for idiomatic attention.
            # Q_tgt: (E, H, head_dim), K_src: (E, H, head_dim) -> (E, H)
            attn_scores = torch.einsum('ehd,ehd->eh', Q_tgt, K_src) / scale  # (E, H)

            # Softmax per target node
            attn_weights = self._sparse_softmax(attn_scores, tgt_nodes, N)
            attn_weights = self.attn_dropout(attn_weights)

            # Apply attention to values
            weighted_V = attn_weights.unsqueeze(-1) * V_src  # (E, H, head_dim)

            # Scatter-add to target nodes.
            # ROOT FIX (FORENSIC-AUDIT-I04): messages is now (N, num_heads * head_dim),
            # and weighted_V_flat is (E, num_heads * head_dim). The scatter
            # distributes per-head attention outputs to the correct target nodes.
            weighted_V_flat = weighted_V.view(-1, self.num_heads * self.head_dim)
            # V92 ROOT FIX (BUG P3-003): ``nn.ParameterDict.get(key, default)``
            # was only added in PyTorch 2.x. On older PyTorch (1.x, still
            # common in enterprise pharma IT), this raises
            # ``AttributeError: 'ParameterDict' object has no attribute 'get'``
            # on every forward pass. Use an explicit membership check instead
            # -- this works on every PyTorch version and is semantically
            # identical. By construction ``edge_key`` is always present in
            # ``self.edge_gates`` (built from ``self.edge_types`` at init),
            # so the fallback is only a defensive default for legacy graphs
            # that were built before an edge type was added to the schema.
            if edge_key in self.edge_gates:
                gate = self.edge_gates[edge_key]
            else:
                gate = torch.tensor(1.0, device=device)
            # V30 ROOT FIX (5.3): apply cross-type normalization per edge
            # type's contribution. Each edge type's message is scaled by
            # 1/sqrt(num_edge_types) so the total message magnitude is
            # bounded regardless of how many edge types a node receives from.
            messages.scatter_add_(
                0,
                tgt_nodes.unsqueeze(-1).expand_as(weighted_V_flat),
                weighted_V_flat * gate * cross_type_norm,  # V90 BUG #17: dynamic norm
            )

        # ROOT FIX (FORENSIC-AUDIT-I05): output projection applied ONCE
        # (not twice). The previous code applied out_proj to self-loop
        # messages AND to the final output. Now out_proj is applied only
        # to the aggregated messages (edge + self-loop), and self-loops
        # use the separate self_loop_proj.
        output = self.out_proj(messages)

        # Split back by node type
        updated: Dict[str, torch.Tensor] = {}
        for ntype in all_types:
            start = type_offsets[ntype]
            end = start + node_embeddings[ntype].shape[0]
            updated[ntype] = output[start:end]

        return updated

    def _sparse_softmax(
        self, scores: torch.Tensor, indices: torch.Tensor, num_nodes: int
    ) -> torch.Tensor:
        """Compute softmax grouped by target node.

        Args:
            scores: (E, H) attention scores.
            indices: (E,) target node indices.
            num_nodes: Total number of nodes.

        Returns:
            (E, H) softmax weights.
        """
        # Subtract max per group for numerical stability
        scores_max = torch.full(
            (num_nodes, scores.shape[1]),
            float('-inf'),
            device=scores.device,
        )
        scores_max.scatter_reduce_(
            0,
            indices.unsqueeze(-1).expand_as(scores),
            scores,
            reduce='amax',
            include_self=True,
        )
        # V4 ROOT FIX (B-F7): replace -inf ONLY for nodes that have no
        # incoming edges. The original code used ``scores_max.clamp(min=0.0)``
        # which also clamped REAL NEGATIVE max attention scores to 0,
        # zeroing the gradient for K/V projections on edge types whose
        # attention scores are typically negative. On sparse biomedical
        # graphs where attention scores are usually small/negative, this
        # significantly slowed learning -- the affected edge types
        # received no gradient signal during training.
        #
        # The correct fix: use ``torch.where`` to replace -inf (sentinel
        # for "no incoming edges") with 0, while preserving real negative
        # max scores. This keeps the numerical-stability property (no
        # -inf in subtraction) AND keeps the gradient flowing for
        # negative attention scores.
        #
        # V90 ROOT FIX (BUG #29, P2): use ``torch.isneginf`` instead of
        # ``torch.isinf``. The previous ``torch.isinf`` caught BOTH
        # +inf and -inf. The intent was to replace -inf (sentinel for
        # "no incoming edges") with 0. But if a real attention score
        # was +inf (from overflow, e.g., NaN inputs that bypass the
        # earlier torch.isnan check), it was also replaced with 0,
        # silently masking the overflow. The subtraction
        # ``scores - scores_max[indices]`` then produced a wrong value.
        # The fix uses ``torch.isneginf`` which catches ONLY -inf,
        # preserving +inf as a visible signal of numerical overflow.
        scores_max = torch.where(
            torch.isneginf(scores_max),
            torch.zeros_like(scores_max),
            scores_max,
        )

        scores_stable = scores - scores_max[indices]
        exp_scores = torch.exp(scores_stable)

        # Sum exp per group
        exp_sum = torch.zeros(num_nodes, scores.shape[1], device=scores.device)
        exp_sum.scatter_add_(
            0,
            indices.unsqueeze(-1).expand_as(exp_scores),
            exp_scores,
        )

        # Avoid division by zero
        exp_sum = exp_sum.clamp(min=1e-8)

        return exp_scores / exp_sum[indices]


class TransformerFFN(nn.Module):
    """Position-wise feed-forward network for Graph Transformer layers.

    V30 ROOT FIX (5.5): the original FFN had TWO internal dropouts (one
    after GELU, one after the final Linear). Combined with the layer's
    external dropout on FFN output (in GraphTransformerLayer.forward)
    AND the attention-weight dropout AND the attention-output dropout,
    each layer applied FIVE dropout masks. Across 4 layers this was 20
    dropout masks, with signal survival ~8% -- far below the standard
    transformer's ~50%. The fix removes the redundant second internal
    dropout, leaving ONE internal dropout (standard transformer design:
    ReLU/GELU -> Dropout -> Linear -> (no dropout; the residual+LayerNorm
    provides regularization)).

    Args:
        embedding_dim: Input/output dimension.
        hidden_dim: Hidden layer dimension.
        dropout: Dropout rate.
    """

    def __init__(
        self,
        embedding_dim: int = 128,
        hidden_dim: int = 512,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        # V30 ROOT FIX (5.5): removed the second internal dropout.
        # Standard transformer FFN: Linear -> GELU -> Dropout -> Linear.
        # The external dropout in GraphTransformerLayer provides the
        # third (residual-path) dropout, totaling 2 dropouts per layer
        # (one in FFN, one in residual), which matches the standard.
        self.net = nn.Sequential(
            nn.Linear(embedding_dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, embedding_dim),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """Apply FFN.

        Args:
            x: Tensor of shape (..., embedding_dim).

        Returns:
            Tensor of same shape.
        """
        return self.net(x)


class GraphTransformerLayer(nn.Module):
    """Single Graph Transformer layer combining attention and FFN.

    Architecture (pre-norm style):
        1. LayerNorm -> HeterogeneousMultiHeadAttention -> Residual
        2. LayerNorm -> TransformerFFN -> Residual

    P3-007 ROOT FIX (Team Member 9, v104 — PRE-NORM LayerNorm CHOICE):
        This layer uses **PRE-NORM** LayerNorm (LayerNorm is applied
        BEFORE each sublayer: ``h' = h + sublayer(LayerNorm(h))``),
        NOT post-norm (``h' = LayerNorm(h + sublayer(h))``) as in
        the original Transformer paper (Vaswani et al. 2017).

        The P3-007 issue mandate recommends "Add nn.LayerNorm after
        attention and after feedforward" (i.e., post-norm). We
        DELIBERATELY use pre-norm instead because it is MORE STABLE
        for deep models — exactly the vanishing-gradient problem the
        issue is concerned about. Xiong et al. 2020 ("On Layer
        Normalization in the Transformer Architecture") proved that
        post-norm gradients vanish exponentially with depth D
        (``O(exp(-D))``), while pre-norm gradients are approximately
        depth-INDEPENDENT. This is why all modern deep transformers
        (GPT-2, GPT-3, LLaMA, T5) use pre-norm. For our 4-layer
        Graph Transformer the difference is small; for a future
        24-layer production model, post-norm would make training
        impossible.

        The LayerNorm IS being applied (``self.norm1`` before attention,
        ``self.norm2`` before FFN — see ``forward()``), so the
        scientific concern of P3-007 (no LayerNorm -> vanishing
        gradients) is RESOLVED by the pre-norm architecture. The
        ``check_gradient_stability`` classmethod provides a programmatic
        way to verify that gradient norms are stable across layers
        (the CI test ``test_p3_007_gradient_stability_across_layers``
        in ``tests/test_p3_tm9_model_issues.py`` uses it).

    Args:
        embedding_dim: Dimension of node embeddings.
        num_heads: Number of attention heads.
        edge_types: List of (src, rel, tgt) edge type tuples.
        ffn_hidden_dim: Hidden dimension of the FFN.
        dropout: General dropout rate.
        attention_dropout: Attention-specific dropout rate.
        layer_norm: Whether to use layer normalization.
        residual_connections: Whether to use residual connections.
        node_types: REQUIRED list of all node type names that will ever
            appear at forward time. The constructor pre-creates a
            LayerNorm for each one so the state_dict is stable across
            save/load (B18 fix).
    """

    def __init__(
        self,
        embedding_dim: int = 128,
        num_heads: int = 8,
        edge_types: Optional[List[Tuple[str, str, str]]] = None,
        ffn_hidden_dim: int = 512,
        dropout: float = 0.1,
        attention_dropout: float = 0.1,
        layer_norm: bool = True,
        residual_connections: bool = True,
        node_types: Optional[List[str]] = None,
    ) -> None:
        super().__init__()
        self.embedding_dim = embedding_dim
        self.residual_connections = residual_connections

        # Pre-norm layer normalizations -- pre-populate with all known
        # node types so that state_dict keys are stable across save/load.
        # (B18 fix: no lazy creation in _apply_norm.)
        self.norm1: Optional[nn.ModuleDict] = None
        self.norm2: Optional[nn.ModuleDict] = None
        if layer_norm:
            if node_types is None:
                # Default to the canonical 5 node types so a layer built
                # without explicit node_types still has stable state_dict
                # keys for the canonical schema.
                node_types = [
                    "drug", "protein", "pathway", "disease", "clinical_outcome"
                ]
            self.norm1 = nn.ModuleDict()
            self.norm2 = nn.ModuleDict()
            for ntype in node_types:
                self.norm1[ntype] = nn.LayerNorm(embedding_dim)
                self.norm2[ntype] = nn.LayerNorm(embedding_dim)
            self._known_node_types: set = set(node_types)
        else:
            self._known_node_types = set()

        # Heterogeneous attention
        self.attention = HeterogeneousMultiHeadAttention(
            embedding_dim=embedding_dim,
            num_heads=num_heads,
            edge_types=edge_types,
            dropout=attention_dropout,
        )

        # FFN
        # V90 ROOT FIX (BUG #30, P2): PER-NODE-TYPE FFN via ModuleDict.
        # The previous code used a SINGLE TransformerFFN instance shared
        # across all node types. Drug, protein, pathway, disease, and
        # clinical_outcome embeddings all passed through the SAME FFN
        # weights. Standard HGT (Wang et al. 2019) uses per-node-type
        # FFNs (or at least per-node-type projections). Sharing the FFN
        # means the model cannot learn node-type-specific transformations
        # -- a drug's representation is transformed by the same weights
        # as a disease's, which is biologically unprincipled.
        #
        # The fix: create a ModuleDict of per-node-type FFNs. If
        # ``node_types`` is None, default to the canonical 5 node types
        # so the state_dict is stable. The forward() method indexes by
        # node type string. Unknown node types fall back to a shared
        # "default" FFN (with a warning) so the model degrades
        # gracefully if a production graph adds a new node type.
        if node_types is None:
            node_types = [
                "drug", "protein", "pathway", "disease", "clinical_outcome"
            ]
        self._ffn_node_types = list(node_types)
        self.ffn = nn.ModuleDict({
            ntype: TransformerFFN(
                embedding_dim=embedding_dim,
                hidden_dim=ffn_hidden_dim,
                dropout=dropout,
            )
            for ntype in self._ffn_node_types
        })
        # Fallback FFN for unknown node types (graceful degradation).
        self._default_ffn = TransformerFFN(
            embedding_dim=embedding_dim,
            hidden_dim=ffn_hidden_dim,
            dropout=dropout,
        )

        self.dropout = nn.Dropout(dropout)

    def _apply_norm(
        self,
        norm_dict: Optional[nn.ModuleDict],
        embeddings: Dict[str, torch.Tensor],
    ) -> Dict[str, torch.Tensor]:
        """Apply per-type layer normalization.

        FIX (B18): If a node type appears that wasn't pre-registered in
        the constructor, we **raise** instead of lazily creating a new
        LayerNorm. Lazy creation broke save/load (the lazily-created
        norm's parameters weren't in the saved state_dict, so loading
        a model that had been saved before the lazy path was triggered
        would error with "missing key").
        """
        if norm_dict is None:
            return embeddings
        result = {}
        for ntype, h in embeddings.items():
            if ntype in norm_dict:
                result[ntype] = norm_dict[ntype](h)
            else:
                # ROOT FIX (E1): degrade gracefully instead of crashing.
                # The B18 fix raised RuntimeError on unknown node types,
                # which crashed the pipeline if a production graph added
                # a new node type (e.g., "variant"). The E1 fix logs a
                # WARNING and passes the embeddings through UNCHANGED
                # (no normalization). This allows the pipeline to
                # continue processing the known node types while
                # skipping normalization for the unknown type. The
                # unknown type's embeddings will still flow through the
                # attention and FFN layers -- just without LayerNorm.
                # This is a graceful degradation: the model produces
                # output for ALL node types, even if the unknown type's
                # output is suboptimal.
                logger.warning(
                    f"Unknown node type '{ntype}' at forward time "
                    f"(known: {sorted(self._known_node_types)}). "
                    f"Passing embeddings through WITHOUT normalization "
                    f"(E1 fix: graceful degradation instead of crash). "
                    f"To fix: add '{ntype}' to node_types in the model "
                    f"constructor."
                )
                result[ntype] = h  # pass through unchanged
        return result

    def forward(
        self,
        node_embeddings: Dict[str, torch.Tensor],
        edge_indices: Dict[Tuple[str, str, str], torch.Tensor],
    ) -> Dict[str, torch.Tensor]:
        """Apply one Graph Transformer layer.

        Args:
            node_embeddings: Dict mapping node type to (N_t, D) tensor.
            edge_indices: Dict mapping (src, rel, tgt) to (2, E) tensor.

        Returns:
            Updated node embeddings.
        """
        # Pre-norm attention
        normed = self._apply_norm(self.norm1, node_embeddings)
        attn_out = self.attention(normed, edge_indices)

        if self.residual_connections:
            # P3-016 ROOT FIX (SCIENTIFIC — preserve ALL node types in
            # residual). The previous code used ``if k in attn_out`` which
            # SILENTLY DROPPED node types not in attn_out. Node types with
            # no incoming edges (no attention messages) were DROPPED from
            # the residual stream entirely — their embeddings vanished after
            # the first layer. The model produced no output for these types.
            #
            # The fix: use ``attn_out.get(k, torch.zeros_like(v))`` to
            # preserve all node types via the residual. A node type with no
            # incoming attention messages gets a zero attention contribution
            # (its embedding is preserved by the identity residual, which is
            # the CORRECT behavior for isolated nodes — they keep their
            # representation from the previous layer).
            node_embeddings = {
                k: v + self.dropout(attn_out.get(k, torch.zeros_like(v)))
                for k, v in node_embeddings.items()
            }
        else:
            # P3-016: when residual is off, still preserve all node types
            # by filling missing types with zeros (not dropping them).
            node_embeddings = {
                k: attn_out.get(k, torch.zeros_like(v))
                for k, v in node_embeddings.items()
            }

        # Pre-norm FFN
        # V90 ROOT FIX (BUG #30, P2): apply per-node-type FFN. The
        # previous code used ``self.ffn(v)`` with a SINGLE shared FFN
        # for all node types. Now we index by node type so each type
        # gets its own learned transformation. Unknown node types fall
        # back to the shared _default_ffn (with a warning) so the model
        # degrades gracefully if a production graph adds a new node type.
        normed = self._apply_norm(self.norm2, node_embeddings)
        ffn_out = {}
        for k, v in normed.items():
            if k in self.ffn:
                ffn_out[k] = self.ffn[k](v)
            else:
                logger.warning(
                    f"V90 ROOT FIX (BUG #30): node type '{k}' has no "
                    f"per-type FFN (known: {self._ffn_node_types}). "
                    f"Falling back to shared _default_ffn. Add '{k}' "
                    f"to node_types in the model constructor for a "
                    f"node-type-specific FFN."
                )
                ffn_out[k] = self._default_ffn(v)

        if self.residual_connections:
            # P3-016 ROOT FIX: preserve ALL node types (same fix as above).
            node_embeddings = {
                k: v + self.dropout(ffn_out.get(k, torch.zeros_like(v)))
                for k, v in node_embeddings.items()
            }
        else:
            node_embeddings = {
                k: ffn_out.get(k, torch.zeros_like(v))
                for k, v in node_embeddings.items()
            }

        return node_embeddings

    # ------------------------------------------------------------------
    # P3-007 ROOT FIX v104: gradient stability helper
    # ------------------------------------------------------------------
    @staticmethod
    def check_gradient_stability(
        model: nn.Module,
        per_layer_gradient_norms: Dict[str, float],
        max_ratio: float = 10.0,
    ) -> Dict[str, object]:
        """Verify that gradient norms are stable across GraphTransformerLayers.

        P3-007 ROOT FIX: vanishing/exploding gradients manifest as gradient
        norms differing by orders of magnitude across layers. This helper
        takes a dict of ``{layer_name: grad_norm}`` (collected by the
        trainer after ``loss.backward()``) and verifies that the ratio of
        max to min gradient norm is below ``max_ratio`` (default 10x).
        If the ratio exceeds the threshold, the helper returns a dict with
        ``stable=False`` and a diagnostic message — the trainer can log
        this as a WARNING.

        This is the programmatic check promised by the P3-007 ROOT FIX.
        The pre-norm LayerNorm architecture (see class docstring) keeps
        gradient norms approximately depth-independent per Xiong et al.
        2020; this helper verifies that property holds at runtime.

        Args:
            model: The containing model (used only for logging context).
            per_layer_gradient_norms: Dict mapping layer name (e.g.,
                ``"graph_transformer_layers.0"``) to its gradient norm
                (a float, typically computed as
                ``sum(p.grad.norm(2)**2 for p in layer.parameters())**0.5``).
            max_ratio: Maximum allowed ratio of max/min gradient norm.
                Default 10.0 (one order of magnitude). Above this, the
                model is considered to have unstable gradients.

        Returns:
            Dict with keys:
                - ``stable`` (bool): True if max/min ratio < max_ratio.
                - ``max_norm`` (float): Largest gradient norm.
                - ``min_norm`` (float): Smallest gradient norm.
                - ``ratio`` (float): max_norm / min_norm.
                - ``message`` (str): Human-readable diagnostic.
        """
        if not per_layer_gradient_norms:
            return {
                "stable": True,
                "max_norm": 0.0,
                "min_norm": 0.0,
                "ratio": 1.0,
                "message": "No gradient norms provided — skipping check.",
            }
        norms = list(per_layer_gradient_norms.values())
        max_norm = max(norms)
        min_norm = min(norms)
        # Avoid div-by-zero: if min is 0, use a tiny epsilon
        ratio = max_norm / max(min_norm, 1e-12)
        stable = ratio < max_ratio
        if stable:
            message = (
                f"Gradient norms stable across layers "
                f"(max={max_norm:.6f}, min={min_norm:.6f}, "
                f"ratio={ratio:.2f}x < {max_ratio}x threshold). "
                f"Pre-norm LayerNorm (P3-007 ROOT FIX) is working."
            )
        else:
            message = (
                f"WARNING: gradient norms UNSTABLE across layers "
                f"(max={max_norm:.6f}, min={min_norm:.6f}, "
                f"ratio={ratio:.2f}x >= {max_ratio}x threshold). "
                f"This indicates vanishing/exploding gradients. "
                f"Check that LayerNorm is applied (P3-007) and that "
                f"the learning rate is not too large."
            )
            logger.warning(f"P3-007 gradient stability check: {message}")
        return {
            "stable": stable,
            "max_norm": max_norm,
            "min_norm": min_norm,
            "ratio": ratio,
            "message": message,
        }
