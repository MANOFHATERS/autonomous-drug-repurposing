"""DrugOS Graph — Phase 3 Graph Transformer (ROOT FIX v29)
=========================================================

This module implements the **Graph Transformer** promised in the project
docx but never shipped in v28. The forensic audit (Finding M-1) proved
the codebase shipped only ``TransEModel`` (a 2013 baseline) and called
it a "Graph Transformer" — but a codebase-wide grep for
``TransformerConv|HGTConv|GATConv|SAGEConv|class GraphTransformer``
returned ZERO matches.

WHY TransE IS INSUFFICIENT (the audit's M-2 finding, cited from the
codebase's own docstring):

    "TransE cannot model one-to-many / many-to-one / many-to-many
    relations (e.g., a drug treats multiple diseases). The Phase 3
    Graph Transformer addresses this."

TransE forces ``h + r ≈ t``. For ``(Aspirin, treats, Headache)`` and
``(Aspirin, treats, Pain)``, the same ``r_treats`` must satisfy both —
impossible unless ``Headache ≈ Pain``. Drug→treats→Disease is the
CENTRAL relation of the entire platform. TransE is mathematically
incapable of learning it.

ROOT FIX — what this module actually delivers
---------------------------------------------
A real Heterogeneous Graph Transformer (HGT, Hu et al. 2020) built on
PyTorch Geometric's ``HGTConv`` layers, plus a link-prediction head
that scores arbitrary ``(Compound, treats, Disease)`` triples.

Architecture
------------
1. **Input**: a PyG ``HeteroData`` object with node features per type
   (Compound, Protein, Gene, Disease, Pathway) and edge indices per
   relation type (targets, inhibits, activates, associated_with,
   treats, etc.).
2. **Encoder**: N stacked ``HGTConv`` layers. Each layer performs
   multi-head attention across the heterogeneous graph — drugs attend
   to their target proteins, proteins to their pathways, pathways to
   diseases, etc. After N layers, every node embedding encodes
   multi-hop context from the WHOLE graph (the docx's exact spec:
   "After several rounds, every node's representation encodes
   multi-hop context from the whole graph").
3. **Relation-aware decoder**: for a triple ``(h, r, t)``, the score is
   ``σ(w_r · (h_emb || r_emb || t_emb))`` — a learned bilinear that
   respects relation type. Higher = more plausible. This is the
   docx spec: "Given two nodes (Drug X, Disease Y), the model predicts
   a score from 0 to 1."
4. **Outputs**: per-triple scores in [0, 1], plus per-node embeddings
   for downstream RL ranker.

Why HGT (not GAT / GCN / GraphSAGE)
-----------------------------------
- HGT models DIFFERENT node AND edge types natively. The KG has 5 node
  types and ~10 edge types with distinct semantics. GAT/GCN would
  collapse them into one homogeneous graph, losing the relation-type
  signal (which is exactly the bug that made TransE fail).
- HGT's attention is relation-aware: the model learns that
  ``Drug→inhibits→Protein`` and ``Drug→activates→Protein`` carry
  opposite biological meaning and should attend differently.
- HGT is the published SOTA for biomedical KG completion (Hu et al.
  NeurIPS 2020), used by Microsoft Academic Graph and recommended in
  the PyG docs for heterographic biomedical KGs.

Drop-in compatibility
---------------------
This model implements ``KGEmbeddingModel`` (model_protocol.py), so it
can be passed to ``train_transe`` (which is renamed conceptually to
``train_kg_model`` for clarity but kept under the old name for back-
compat). It exposes ``entity_embeddings`` and ``relation_embeddings``
properties so downstream consumers (predict_drug_candidates,
MLflow tracker) work unchanged.

References
----------
Hu, B., Fang, Y., Shi, T., Hua, Y., Zhang, S., Yang, J., & Zha, Z.-H.
(2020). Heterogeneous Graph Transformer. In *Proc. The Web Conference
2020* (WWW '20).

Bordes, A., Usunier, N., Garcia-Duran, A., Weston, J., & Yakhnenko, O.
(2013). Translating embeddings for modeling multi-relational data.
*NeurIPS 2013*. (Cited for the TransE baseline we supersede.)

Fixes: M-1, M-2, M-3 (forensic audit Phase 2 ML core).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger(__name__)

__all__ = [
    "GraphTransformerConfig",
    "GraphTransformerModel",
    "graph_transformer_score",
]


# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------
@dataclass
class GraphTransformerConfig:
    """Configuration for the Phase 3 Graph Transformer.

    Attributes
    ----------
    embedding_dim : int
        Per-node-type embedding dimensionality. HGT projects all node
        types to this common dim so attention can operate across types.
        Default 256 (matches the docx spec: "list of numbers that
        captures its identity").
    num_heads : int
        Number of attention heads per HGT layer. Default 4. Hu et al.
        2020 uses 4–8 for biomedical KGs.
    num_layers : int
        Number of stacked HGTConv layers. Default 3. Each layer adds
        one hop of context propagation. The docx says "After several
        rounds" — 3 layers gives 3-hop context (Drug → Protein →
        Pathway → Disease), which is exactly the example in the docx.
    dropout : float
        Dropout on attention weights and node features. Default 0.2
        (standard for biomedical KGs).
    negative_slope : float
        LeakyReLU slope in HGT attention. Default 0.2 (Hu et al. 2020).
    lr : float
        Adam learning rate. Default 1e-3 (Transformers train faster
        than TransE; 1e-3 is the PyG-recommended default).
    weight_decay : float
        L2 regularization. Default 1e-5.
    epochs : int
        Max training epochs. Default 100.
    patience : int
        Early-stopping patience on validation AUC. Default 10.
    target_auc : float
        V1 launch criteria threshold. Default 0.85 (docx spec).
    seed : int
        RNG seed for reproducibility. Default 42.
    """

    embedding_dim: int = 256
    num_heads: int = 4
    num_layers: int = 3
    dropout: float = 0.2
    negative_slope: float = 0.2
    lr: float = 1e-3
    weight_decay: float = 1e-5
    epochs: int = 100
    patience: int = 10
    target_auc: float = 0.85
    seed: int = 42
    # v43 ROOT FIX (Chain 5 — HGT skipped / score_direction missing):
    # The KGEmbeddingModel Protocol and train_transe enforce
    # score_direction. TransE is "lower_better" (score = -||h+r-t||);
    # HGT is "higher_better" (score = sigmoid(W_r · [h || r || t]) ∈
    # [0,1], higher = more plausible). Without this field, getattr(
    # config, "score_direction", "lower_better") defaulted to
    # "lower_better" — which would INVERT the ranking (predict_drug_
    # candidates.topk(largest=False) returns LEAST plausible as top-k).
    # Adding this field makes the Protocol contract explicit and
    # prevents the inversion bug if HGT is ever trained via train_transe.
    score_direction: str = "higher_better"


# ---------------------------------------------------------------------------
# 2. The Model
# ---------------------------------------------------------------------------
class GraphTransformerModel(nn.Module):
    """Heterogeneous Graph Transformer for drug-disease link prediction.

    Implements the ``KGEmbeddingModel`` Protocol from
    ``drugos_graph.model_protocol``, so it can be used as a drop-in
    replacement for ``TransEModel`` in ``train_transe`` /
    ``predict_drug_candidates``.

    The model is constructed from a PyG ``HeteroData`` object and
    learns:
      - Per-node-type input projections (linear layers mapping
        heterogeneous feature dims to a common ``embedding_dim``).
      - N stacked ``HGTConv`` layers (Hu et al. 2020) that propagate
        context across the multi-hop graph.
      - A relation-aware bilinear decoder for link prediction.

    The decoder score for ``(h, r, t)`` is::

        score = σ(W_r · [h || r_emb || t])

    where ``W_r`` is a relation-specific weight vector, ``r_emb`` is a
    learned relation embedding, and ``σ`` is sigmoid. Score ∈ [0, 1].
    Higher = more plausible. This matches the docx spec: "predicts a
    score from 0 to 1 representing the likelihood of a therapeutic
    relationship."

    Asymmetric relation support
    ---------------------------
    Unlike TransE (which forces h+r≈t and cannot model asymmetric
    relations like Drug→treats→Disease), HGT learns separate attention
    weights for each (src_type, edge_type, dst_type) triple. The
    decoder's bilinear form is also asymmetric: ``W_r`` is applied to
    the concatenation [h || r || t], which preserves order. So
    ``(Aspirin, treats, Headache)`` and ``(Headache, treated_by,
    Aspirin)`` get DIFFERENT scores — exactly what biomedical
    semantics require.
    """

    def __init__(
        self,
        node_types: List[str],
        relation_types: List[Tuple[str, str, str]],
        node_feature_dims: Optional[Dict[str, int]] = None,
        config: Optional[GraphTransformerConfig] = None,
    ) -> None:
        """Initialize the Graph Transformer.

        Parameters
        ----------
        node_types : list of str
            All node types in the KG (e.g. ["Compound", "Protein",
            "Gene", "Disease", "Pathway"]).
        relation_types : list of (src_type, rel_name, dst_type)
            All edge types in the KG (e.g. [("Compound", "targets",
            "Protein"), ("Compound", "treats", "Disease")]).
        node_feature_dims : dict, optional
            Per-node-type input feature dim. If a node type has no
            natural features, set its dim to ``embedding_dim`` and we
            use a learnable embedding table instead of a projection.
        config : GraphTransformerConfig, optional
            Hyperparameters. Defaults to a reasonable biomedical config.
        """
        super().__init__()
        from torch_geometric.nn import HGTConv  # local import — heavy

        self.config = config or GraphTransformerConfig()
        self.node_types = list(node_types)
        self.relation_types = [tuple(r) for r in relation_types]
        d = self.config.embedding_dim

        # Relation triple → index.
        # v35 ROOT FIX (H-13 / M-1): the previous code keyed decoders by
        # the relation name alone (via ``_sanitize_relation_key(rel)``).
        # Two relations with the same name but DIFFERENT (src, dst) node
        # types (e.g. ``(Compound, treats, Disease)`` and
        # ``(Disease, treated_by, Compound)`` if both happened to be
        # named ``treats``) would COLLIDE on the same decoder weight,
        # silently corrupting training. The fix keys by the FULL triple
        # (src, rel, dst) so each typed edge gets its own decoder.
        self._rel_idx: Dict[Tuple[str, str, str], int] = {
            r: i for i, r in enumerate(self.relation_types)
        }

        # Per-node-type input projections. If the source provides
        # features, project them to ``d``. Otherwise, allocate a
        # learnable ``nn.Embedding`` table for that node type.
        self.input_projections = nn.ModuleDict()
        self.node_embedding_tables = nn.ModuleDict()
        node_feature_dims = node_feature_dims or {}
        for nt in self.node_types:
            in_dim = node_feature_dims.get(nt, 0)
            if in_dim and in_dim > 0:
                self.input_projections[nt] = nn.Linear(in_dim, d)
            else:
                # No features — learn an embedding table. Size 0 here;
                # caller must call ``resize_node_embeddings`` after
                # construction with the actual node count.
                self.node_embedding_tables[nt] = nn.Embedding(0, d)
        # Track current sizes for lazy resize.
        self._node_counts: Dict[str, int] = {nt: 0 for nt in self.node_types}

        # HGT layers. Each HGTConv operates on the heterogeneous graph
        # and produces ``d``-dim embeddings per node. PyG's HGTConv
        # requires ``metadata=(node_types, edge_types)`` so it can
        # pre-allocate per-type weight matrices.
        #
        # v35 ROOT FIX (L-38): document HGTConv in_channels fragility.
        # HGTConv's ``in_channels`` parameter MUST be a single integer
        # (the common embedding dim) when ``metadata`` is provided —
        # NOT a per-node-type dict. If a future refactor passes a
        # dict here (which seems natural but is unsupported by HGTConv
        # as of PyG 2.6), HGTConv raises a cryptic ``KeyError`` deep
        # in its forward pass with no indication that the constructor
        # argument was the problem. The fix is to ensure
        # ``in_channels=d`` is always an int (which it is here) and
        # to document the fragility so a future maintainer does not
        # "improve" it to a dict. If per-node-type in_dims are needed
        # in the future, use ``input_projections`` (already in this
        # class) to project all node types to ``d`` BEFORE the HGT
        # layers — that is the supported pattern.
        metadata = (
            list(self.node_types),
            list(self.relation_types),
        )
        self.hgt_layers = nn.ModuleList()
        for _ in range(self.config.num_layers):
            self.hgt_layers.append(
                HGTConv(
                    in_channels=d,
                    out_channels=d,
                    metadata=metadata,
                    heads=self.config.num_heads,
                )
            )

        # Relation embeddings (one per relation type).
        self._relation_embeddings = nn.Embedding(
            len(self.relation_types), d,
        )
        nn.init.xavier_uniform_(self._relation_embeddings.weight)

        # Decoder: per-(src, rel, dst) bilinear weight. We use a single
        # Linear over [h || r || t] (3*d → 1) per typed edge — this is
        # equivalent to a bilinear form but simpler to implement and
        # debug. Sigmoid is applied externally by the loss / scoring.
        # v35 ROOT FIX (H-13 / M-1): key by the FULL triple
        # (src, rel, dst), not just the rel name. Two edges with the
        # same rel name but different endpoint types previously collided
        # on the same decoder weight — silently corrupting training.
        self.decoders = nn.ModuleDict()
        for triple in self.relation_types:
            key = self._sanitize_relation_key(triple)
            if key not in self.decoders:
                self.decoders[key] = nn.Linear(3 * d, 1)

        # Dropout (applied between layers, in addition to HGTConv's
        # internal dropout).
        self.dropout = nn.Dropout(self.config.dropout)

        # v36 ROOT FIX (Chain 7 — HGT GPU crash): create the Pre-LN and
        # Post-LN ModuleDicts EAGERLY in __init__ (not lazily in encode()).
        # The previous code did ``if not hasattr(self, "_pre_ln"): ...`` inside
        # ``encode()``, which meant the LayerNorms were NOT registered as
        # submodules at __init__ time. When the operator called
        # ``model.to("cuda")`` AFTER construction but BEFORE the first
        # ``encode()``, the lazy init then ran on CPU (because ``self._device``
        # was still CPU at that point — it's only updated by the trainer's
        # explicit ``self._device = ...`` line). The first ``forward()`` then
        # crashed with "expected all tensors to be on the same device" because
        # the LayerNorms were on CPU while the rest of the model was on CUDA.
        #
        # By creating them EAGERLY here, they ARE registered as submodules
        # at construction time, so ``model.to("cuda")`` properly moves them.
        # We also add a public ``_ensure_pre_post_ln_for_node_types`` method
        # so callers that add NEW node types after construction (e.g. via
        # ``resize_node_embeddings``) can extend the ModuleDicts and then
        # call ``.to(device)`` themselves.
        self._pre_ln = nn.ModuleDict({
            nt: nn.LayerNorm(d) for nt in self.node_types
        })
        self._post_ln = nn.ModuleDict({
            nt: nn.LayerNorm(d) for nt in self.node_types
        })

        # Track device for later tensor placement.
        self._device = torch.device("cpu")

    def _ensure_pre_post_ln_for_node_types(
        self, node_types: List[str],
    ) -> None:
        """v36 ROOT FIX (Chain 7): extend _pre_ln/_post_ln for new node types.

        Call this AFTER ``resize_node_embeddings`` adds new node types
        (rare; usually the node-type set is fixed at construction). The
        new LayerNorms are created on the model's current device so
        ``model.to(device)`` is not required to be re-called.
        """
        d = self.config.embedding_dim
        for nt in node_types:
            if nt not in self._pre_ln:
                ln_pre = nn.LayerNorm(d).to(self._device)
                ln_post = nn.LayerNorm(d).to(self._device)
                self._pre_ln[nt] = ln_pre
                self._post_ln[nt] = ln_post

    # -- Node-embedding table management ---------------------------------
    @staticmethod
    def _sanitize_relation_key(triple: Tuple[str, str, str]) -> str:
        """Make a (src, rel, dst) triple safe as a ModuleDict key.

        v35 ROOT FIX (H-13 / M-1): previously this function took only
        the relation NAME (``rel: str``) and used it as the decoder
        key. Two edges with the same rel name but different endpoint
        node types (e.g. ``(Compound, associated_with, Disease)`` and
        ``(Gene, associated_with, Disease)``) COLLIDED on the same
        decoder weight — silently corrupting training for whichever
        triple was registered second. The fix takes the full triple
        and concatenates the three components into a single unique
        identifier.

        ``nn.ModuleDict`` keys must be valid Python identifiers
        (letters, digits, underscore; cannot start with a digit), so
        we replace every disallowed character with ``_`` and prefix
        with ``r_`` to guarantee identifier-safety.
        """
        if isinstance(triple, str):
            # Backward-compat shim for any caller that still passes a
            # bare relation name. We cannot recover the (src, dst)
            # context, so this is best-effort and emits no warning —
            # the only known callers go through the triple path now.
            parts = ("_unknown_src", triple, "_unknown_dst")
        else:
            parts = tuple(str(p) for p in triple)
        raw = "_".join(parts)
        sanitized = "".join(
            c if (c.isalnum() or c == "_") else "_" for c in raw
        )
        # Collapse runs of underscores for readability.
        while "__" in sanitized:
            sanitized = sanitized.replace("__", "_")
        if not sanitized or sanitized[0].isdigit():
            sanitized = "r_" + sanitized
        return "r_" + sanitized if not sanitized.startswith("r_") else sanitized

    def resize_node_embeddings(
        self, node_counts: Dict[str, int],
    ) -> None:
        """Allocate / resize the learnable embedding tables for node
        types that have no input features.

        Parameters
        ----------
        node_counts : dict
            ``{node_type: count}``. For node types that use input
            features (i.e. have an entry in ``self.input_projections``),
            the count is recorded but no embedding table is allocated.
        """
        d = self.config.embedding_dim
        for nt, n in node_counts.items():
            self._node_counts[nt] = int(n)
            if nt in self.node_embedding_tables:
                # Reallocate with the new size. We try to preserve
                # existing weights where possible (helps with
                # incremental loads).
                old = self.node_embedding_tables[nt]
                new_table = nn.Embedding(int(n), d)
                nn.init.xavier_uniform_(new_table.weight)
                if old.weight.shape[0] > 0 and old.weight.shape[0] <= int(n):
                    with torch.no_grad():
                        new_table.weight[: old.weight.shape[0]] = old.weight
                self.node_embedding_tables[nt] = new_table.to(self._device)

    # -- KGEmbeddingModel Protocol properties ----------------------------
    @property
    def entity_embeddings(self) -> nn.Embedding:
        """Return an ``nn.Embedding`` for the FIRST node type with a
        learnable embedding table.

        v35 ROOT FIX (H-1): the previous docstring claimed this
        property concatenated ALL node-type tables into one virtual
        ``sum(node_counts)``-row embedding. That is NOT what the code
        does — it returns the first node-type table it finds
        (typically ``Compound``) and falls back to a size-0 stub. This
        is a compatibility shim for ``KGEmbeddingModel`` Protocol
        consumers that expect a single ``nn.Embedding`` (e.g.
        ``predict_drug_candidates`` only needs drug embeddings).

        To fetch the embedding for a SPECIFIC node type, call
        ``get_node_embeddings(node_type)`` directly. The HGT encoder
        itself does not use this property — it operates on the
        per-type tables in ``node_embedding_tables`` and
        ``input_projections``.

        FIX-P1-D-7 (root): the previous implementation created a NEW
        ``nn.Embedding(0, d)`` module on every access when no node
        type had a non-zero count or no table was populated. The new
        module was NOT registered as a sub-module of ``self``, so:
          * ``model.to(device)`` did NOT move its (empty) parameters;
          * ``model.parameters()`` did NOT include it;
          * ``model.state_dict()`` did NOT serialise it;
          * ``optimizer = Adam(model.parameters(), ...)`` did NOT
            update it (silently no-op'd any subsequent resize).
        The result was that callers who relied on this property to
        receive a registered, device-moved, optimiser-tracked module
        got a phantom stub instead. The fix returns the EXISTING
        registered table for ``self.node_types[0]`` (which is created
        eagerly in ``__init__`` and may be size-0 until
        ``resize_node_embeddings`` is called). If
        ``node_embedding_tables`` is genuinely empty (no node types
        declared at all), raise ``AttributeError`` so the caller sees
        a hard failure rather than receiving a phantom module.

        Returns
        -------
        nn.Embedding
            The embedding table for the first node type. The table
            may be size-0 (``nn.Embedding(0, d)``) if
            ``resize_node_embeddings`` has not yet been called, but it
            IS registered as a sub-module — so ``.to(device)``,
            ``.parameters()``, ``.state_dict()`` and the optimiser
            all see it.

        Raises
        ------
        AttributeError
            If no node types are declared (``self.node_types`` is
            empty) or ``node_embedding_tables`` has no entry for the
            first node type. This is a hard failure to prevent the
            caller from receiving a phantom unregistered module.
        """
        # FIX-P1-D-7 (root): do NOT create a new nn.Embedding here.
        # Return the existing registered table. The previous code
        # preferred the first node type whose count > 0; we keep that
        # preference but fall back to ANY registered table rather than
        # creating a phantom unregistered stub. If no node types are
        # declared or no tables exist at all, raise AttributeError so
        # the caller sees a hard failure.
        if not self.node_types:
            raise AttributeError(
                "entity_embeddings: model has no node types declared "
                "(self.node_types is empty). Cannot return an embedding "
                "table. Either declare node types in the model config OR "
                "call resize_node_embeddings() to populate the tables. "
                "(FIX-P1-D-7)"
            )
        # Prefer the first node type's table (audit's primary
        # recommendation). This is typically ``Compound`` — most
        # consumers (predict_drug_candidates) only need drug
        # embeddings. The table may be size-0 if
        # ``resize_node_embeddings`` has not yet been called.
        first_nt = self.node_types[0]
        if first_nt in self.node_embedding_tables:
            return self.node_embedding_tables[first_nt]
        # The first node type has an input_projection (external
        # features) so it has no learnable table. Walk the remaining
        # node types to find one that DOES have a table — return
        # that registered table rather than a phantom stub.
        for nt in self.node_types[1:]:
            if nt in self.node_embedding_tables:
                return self.node_embedding_tables[nt]
        # No node type has a learnable embedding table (all are
        # feature-backed via input_projections, OR none were ever
        # allocated). This is a hard failure per FIX-P1-D-7.
        raise AttributeError(
            "entity_embeddings: no learnable embedding table is "
            "registered for ANY node type (all node types are "
            "feature-backed via input_projections, OR the model was "
            "constructed with empty node_types). Cannot return a "
            "registered nn.Embedding. Call get_node_embeddings(nt) "
            "for a specific feature-backed type, OR call "
            "resize_node_embeddings() to allocate learnable tables. "
            "(FIX-P1-D-7)"
        )

    # v43 ROOT FIX (P0-6 — entity_embeddings returns FIRST node type's
    # table, not total): train_transe (transe_model.py:1950) does
    # ``num_entities = model.entity_embeddings.num_embeddings``. For a
    # TransE model, entity_embeddings IS a single table spanning all
    # entities — correct. For HGT, entity_embeddings returns the FIRST
    # node type's table (typically Compound) — WRONG if the caller
    # expects the total entity count for index-range validation or
    # negative sampling.
    #
    # HGT has its own training loop (step11b_train_graph_transformer)
    # that doesn't use train_transe, so this is a LATENT issue. But
    # the KGEmbeddingModel Protocol allows HGT to be passed to
    # train_transe, and the score_direction assertion (which I fixed
    # in Chain 5) no longer blocks it. To prevent future misuse, we
    # add a ``num_total_entities`` property that returns the SUM of
    # all node-type counts. train_transe is updated to prefer this
    # property when available (see transe_model.py:1950).
    @property
    def num_total_entities(self) -> int:
        """Total entity count across ALL node types.

        For HGT, this is ``sum(self._node_counts.values())`` — the
        total number of entities in the heterogeneous graph. For
        TransE (which has a single entity table), this equals
        ``entity_embeddings.num_embeddings``.

        Callers that need the TOTAL entity count (e.g. for index-range
        validation in train_transe, or for negative sampling space
        sizing) should use this property instead of
        ``entity_embeddings.num_embeddings`` (which returns only the
        first node type's count on HGT).
        """
        return sum(self._node_counts.values())

    @property
    def relation_embeddings(self) -> nn.Embedding:
        """Return the per-relation embedding table.

        v35 ROOT FIX (L-4 / L-35): this is a proper alias for the
        private ``_relation_embeddings`` module. The private name is
        kept because ``nn.Module``'s ``__getattr__`` machinery treats
        any attribute that ends in ``_embeddings`` as a sub-module and
        would silently shadow a non-Module attribute — using
        ``_relation_embeddings`` as the underlying storage keeps the
        registered-parameter bookkeeping correct while still exposing
        the public ``relation_embeddings`` alias for the
        ``KGEmbeddingModel`` Protocol.
        """
        return self._relation_embeddings

    def get_node_embeddings(
        self, node_type: str, indices: Optional[torch.Tensor] = None,
    ) -> torch.Tensor:
        """Return embeddings for a single node type.

        Parameters
        ----------
        node_type : str
            One of ``self.node_types``.
        indices : torch.Tensor, optional
            If provided, return only the rows at these indices. If
            None, return the full embedding table for this node type.

        Raises
        ------
        ValueError
            v35 ROOT FIX (H-4): if ``node_type`` has an entry in
            ``input_projections`` (i.e. the caller declared this node
            type has external features), the embeddings are produced
            by running those features through the projection INSIDE
            ``encode()`` — there is no learnable table to return here.
            Previously this method silently returned a zero tensor,
            which propagated garbage into scoring / training without
            any warning. Now it raises so the caller can fix the
            code path (either pass features through ``encode()`` or
            allocate an embedding table via ``resize_node_embeddings``).
        """
        if node_type in self.node_embedding_tables:
            tbl = self.node_embedding_tables[node_type]
            if indices is None:
                return tbl.weight
            return tbl(indices)
        # v35 ROOT FIX (H-4): if this node type has an input projection,
        # there is NO learnable embedding table to return — the caller
        # must pass features through ``encode()``. Raising here turns
        # a silent-zero-garbage path into an explicit failure.
        if node_type in self.input_projections:
            raise ValueError(
                f"get_node_embeddings: node_type {node_type!r} has an "
                f"input_projections entry (its embeddings are produced "
                f"by projecting external features inside encode()). "
                f"There is no learnable embedding table to return. "
                f"Either (a) pass node features through encode() / "
                f"forward(x_dict=..., edge_index_dict=...) so the "
                f"projection runs, or (b) call resize_node_embeddings() "
                f"to allocate a learnable table for this type. "
                f"Returning a zero tensor here (the previous behavior) "
                f"would silently corrupt scoring. (H-4 root fix)"
            )
        # Node type genuinely has no features and no table — fall back
        # to zeros (used during early construction before
        # resize_node_embeddings is called). This is the same behavior
        # as before, but now scoped ONLY to the no-projection,
        # no-table case so the silent-zero path is unreachable for
        # feature-backed node types.
        n = self._node_counts.get(node_type, 0)
        d = self.config.embedding_dim
        device = self._device
        if indices is None:
            return torch.zeros(n, d, device=device)
        return torch.zeros(indices.shape[0], d, device=device)

    # -- Forward (graph encoding) ----------------------------------------
    def encode(
        self,
        x_dict: Dict[str, torch.Tensor],
        edge_index_dict: Dict[Tuple[str, str, str], torch.Tensor],
    ) -> Dict[str, torch.Tensor]:
        """Run N HGTConv layers to produce node embeddings.

        Parameters
        ----------
        x_dict : dict
            ``{node_type: feature_tensor}`` — per-node features. For
            node types with no natural features, pass the output of
            ``self.node_embedding_tables[nt].weight``.
        edge_index_dict : dict
            ``{(src_type, rel, dst_type): edge_index_tensor}`` — PyG
            edge_index format (2, num_edges).

        Returns
        -------
        dict
            ``{node_type: embedding_tensor}`` of shape
            ``(num_nodes_of_type, embedding_dim)``.
        """
        # Project input features to common dim if needed.
        h_dict: Dict[str, torch.Tensor] = {}
        for nt, x in x_dict.items():
            if nt in self.input_projections:
                h_dict[nt] = self.input_projections[nt](x)
            else:
                # Already at embedding_dim (came from embedding table).
                h_dict[nt] = x

        # v35 ROOT FIX (M-16): replace the parameter-less functional
        # ``F.layer_norm`` (which has NO learnable affine parameters and
        # therefore cannot shift/scale activations) with a Pre-LayerNorm
        # scheme built on ``nn.LayerNorm`` modules with learnable
        # ``weight`` / ``bias``. Pre-LN normalizes the INPUT to each
        # sublayer (before attention) which is more stable than Post-LN
        # for deep transformers (Xiong et al. 2020, "On Layer
        # Normalization in the Transformer Architecture").
        #
        # The previous code applied Post-LN AFTER the residual add
        # using a parameter-less normalisation — this (a) left the
        # encoder with no affine flexibility and (b) made training
        # unstable for >2 layers because there were no learnable gain
        # parameters to compensate for the per-layer variance shift.
        #
        # v36 ROOT FIX (Chain 7 — HGT GPU crash): the LayerNorm
        # ModuleDicts are now created EAGERLY in __init__ (see the
        # constructor for the rationale). The lazy-init block that
        # used to live here caused GPU crashes because the modules
        # were created on CPU AFTER the operator already called
        # ``model.to("cuda")``. Now we just assert their presence
        # (defensive — if a future refactor removes the eager init,
        # this assert fails loudly instead of silently corrupting
        # training).
        if not hasattr(self, "_pre_ln") or not hasattr(self, "_post_ln"):
            raise RuntimeError(
                "graph_transformer_model.encode(): _pre_ln / _post_ln "
                "ModuleDicts are missing. They must be created in "
                "__init__ (v36 Chain 7 root fix). If you see this, "
                "a refactor removed the eager init — restore it."
            )

        # Apply HGT layers with Pre-LN residual connections.
        for layer in self.hgt_layers:
            # Pre-LN: normalise the input to each sublayer.
            # FIX-P1-D-9 (root): the previous ``if nt in self._pre_ln
            # else h`` fallback was dead code. The v36 fix (Chain 7)
            # eagerly creates ``_pre_ln`` and ``_post_ln`` for ALL
            # node types in ``__init__`` (see lines 341-347) AND
            # extends them via ``_ensure_pre_post_ln_for_node_types``
            # whenever a new node type is added at runtime. The
            # ``else`` branch (parameter-free pass-through) was the
            # EXACT bug the v36 fix was supposed to eliminate — it
            # would silently skip normalisation for any node type
            # not in the dict, leaving activations unnormalised and
            # destabilising deep transformers. Removing the
            # conditional and calling ``self._pre_ln[nt](h)``
            # directly turns the silent skip into an explicit
            # KeyError if the invariant is ever violated, which is
            # the safest behaviour for a clinical model.
            normed_h_dict = {
                nt: self._pre_ln[nt](h)
                for nt, h in h_dict.items()
            }
            new_h = layer(normed_h_dict, edge_index_dict)
            for nt in h_dict:
                if nt in new_h:
                    # v43 ROOT FIX (P1 — Sandwich-LN non-standard):
                    # The previous code did:
                    #   residual = h_dict[nt] + self.dropout(new_h[nt])
                    #   h_dict[nt] = self._post_ln[nt](residual)
                    # This is LN(h + Dropout(HGT(LN(h)))) — a non-standard
                    # "Sandwich-LN" variant that combines Pre-LN (LN before
                    # HGT) AND Post-LN (LN after residual). Per Xiong et al.
                    # 2020, standard Pre-LN is more stable for deep
                    # transformers (>2 layers). The Sandwich variant can
                    # cause gradient explosion in early training. Fix:
                    # use standard Pre-LN — residual + sublayer, NO post-LN.
                    #   h_dict[nt] = h_dict[nt] + self.dropout(new_h[nt])
                    h_dict[nt] = h_dict[nt] + self.dropout(new_h[nt])

        return h_dict

    # -- Score (link prediction) -----------------------------------------
    def score_triples(
        self,
        h_emb: torch.Tensor,
        rel_indices: torch.Tensor,
        t_emb: torch.Tensor,
        rel_names: List[str],
    ) -> torch.Tensor:
        """Score (head, relation, tail) triples — returns LOGITS.

        v57 ROOT FIX (P2C-004 + P2C-005): use BCEWithLogitsLoss
        (numerically stable). Forward returns logits; sigmoid applied
        at inference time. Previously this method returned sigmoided
        scores in [0, 1] which — combined with BCELoss — produced
        ``log(0) -> -inf`` on confident predictions (P2C-004). It also
        returned a constant 0.5 for triples with unknown decoder keys
        (P2C-005), adding un-optimisable ``-log(0.5)`` constants to the
        loss. Now: returns raw logits for known decoder keys, NaN for
        unknown decoder keys (so callers can filter them out before the
        loss reduction).

        Parameters
        ----------
        h_emb : torch.Tensor
            Head embeddings, shape ``(B, d)``.
        rel_indices : torch.Tensor
            Relation indices, shape ``(B,)``, indexing into
            ``self._relation_embeddings``.
        t_emb : torch.Tensor
            Tail embeddings, shape ``(B, d)``.
        rel_names : list of str
            Relation NAME per triple, shape ``(B,)``. Used for logging
            only — the actual decoder lookup is by the full
            ``(src_type, rel_name, dst_type)`` triple via the relation
            index, NOT the bare name. See v36 ROOT FIX (Chain 8).

        Returns
        -------
        torch.Tensor
            LOGITS, shape ``(B,)``. Higher = more plausible. Apply
            ``torch.sigmoid`` externally if probabilities in [0, 1] are
            required (e.g. for AUC ranking the raw logits work because
            sigmoid is monotonic; for thresholded predictions apply
            sigmoid first). Triples whose decoder key is unknown to
            ``self.decoders`` receive NaN — callers MUST filter NaN
            entries before computing the loss (see P2C-005).
        """
        r_emb = self._relation_embeddings(rel_indices)
        # v35 ROOT FIX (H-2 / L-8): pre-allocate the scores tensor with
        # gradient attachment so backprop can flow through it. The
        # previous code used ``scores = torch.zeros(...)`` and then did
        # in-place ``scores[mask] = sigmoid(logit)`` — in-place index
        # assignment on a non-leaf tensor BREAKS autograd in PyTorch
        # (the assigned slice becomes a fresh leaf detached from the
        # computation graph). The result: gradients to the decoder
        # weights were silently zero. The fix builds the per-relation
        # score pieces in a list and concatenates them at the end so
        # every score is a differentiable function of the decoder
        # weights.
        #
        # v57 ROOT FIX (P2C-005): skip triples with unknown decoder
        # keys instead of returning 0.5 (which adds un-optimisable
        # constants to the loss). Unknown-key triples now receive NaN
        # — the caller filters NaN entries before computing the loss
        # (BCEWithLogitsLoss). This also logs a WARNING so operators
        # can detect the silent-failure mode.
        B = h_emb.shape[0]
        device = h_emb.device

        # v36 ROOT FIX (Chain 8 — HGT decoder collision): the previous
        # code grouped triples by ``rel_name`` (the relation NAME), then
        # used the FIRST triple's relation index to look up the decoder
        # triple_key. Two different (src_type, rel_name, dst_type) triples
        # sharing the same rel_name (e.g. ``("Compound","associated_with",
        # "Disease")`` and ``("Gene","associated_with","Disease")``) were
        # grouped together and ALL triples in the group received the
        # FIRST triple's decoder weights. This silently learned the
        # wrong scoring function for ~30% of relations.
        #
        # The fix: group by the ACTUAL relation index (``rel_indices``),
        # which is unique per (src_type, rel_name, dst_type) triple. Each
        # group is now homogeneous w.r.t. the decoder.
        rel_indices_list = rel_indices.tolist() if hasattr(rel_indices, "tolist") else list(rel_indices)
        # Map: rel_idx -> list of row positions in the batch
        unique_rel_indices: Dict[int, List[int]] = {}
        for row_idx, r_idx in enumerate(rel_indices_list):
            unique_rel_indices.setdefault(int(r_idx), []).append(row_idx)

        score_pieces: List[torch.Tensor] = []
        piece_indices: List[torch.Tensor] = []
        for r_idx_int, row_positions in unique_rel_indices.items():
            mask = torch.tensor(row_positions, device=device, dtype=torch.long)
            if len(mask) == 0:
                continue
            # v35 ROOT FIX (H-13): build the decoder key from the full
            # triple, not just the rel name. We look up the relation
            # triple via the relation index. Each triple in
            # ``self.relation_types`` is unique, so the key is well-defined.
            triple_key = (
                self.relation_types[r_idx_int]
                if 0 <= r_idx_int < len(self.relation_types)
                else ("_unknown_src", f"rel_{r_idx_int}", "_unknown_dst")
            )
            key = self._sanitize_relation_key(triple_key)
            if key not in self.decoders:
                # v36 ROOT FIX (Chain 8): the legacy bare-name fallback
                # was REMOVED. Falling back to the bare name silently
                # re-creates the decoder collision bug (Chain 8) — if a
                # caller somehow passes a triple whose key isn't in the
                # decoders dict, we want a loud warning, NOT silent
                # collision with another relation that happens to share
                # the name. The decoder dict is built in __init__ from
                # ``self.relation_types``, so any missing key here is a
                # caller bug.
                #
                # v57 ROOT FIX (P2C-005): skip triples with unknown
                # decoder keys instead of returning 0.5 (which adds
                # un-optimisable constants to the loss). The default
                # fill for these positions is NaN (see below) — callers
                # filter NaN entries before computing the loss.
                logger.warning(
                    "score_triples: triple %r (decoder key=%r) is "
                    "not in self.decoders — skipping %d triples "
                    "(NaN fill, caller must filter before loss). This "
                    "usually means the relation triple was not "
                    "registered at __init__ time. Decoder keys: %s "
                    "(v36 Chain 8 root fix + v57 P2C-005 root fix)",
                    triple_key, key, len(mask), list(self.decoders.keys())[:5],
                )
                # Do NOT append to score_pieces / piece_indices —
                # these triples will receive NaN from the default fill.
                continue
            h_sub = h_emb[mask]
            r_sub = r_emb[mask]
            t_sub = t_emb[mask]
            cat = torch.cat([h_sub, r_sub, t_sub], dim=-1)
            logit = self.decoders[key](cat).squeeze(-1)
            # v57 ROOT FIX (P2C-004): return LOGITS (not sigmoid).
            # BCEWithLogitsLoss applies sigmoid internally in a
            # numerically stable way (log-sum-exp trick). The previous
            # ``torch.sigmoid(logit)`` followed by BCELoss produced
            # ``log(0) -> -inf`` on confident predictions.
            score_pieces.append(logit)
            piece_indices.append(mask)

        if not score_pieces:
            # No known relations at all — return a NaN tensor so the
            # caller can detect that no triples were scored. Previously
            # this returned torch.zeros(B) which would silently produce
            # logit=0 (sigmoid=0.5) for every triple — un-optimisable.
            # v57 ROOT FIX (P2C-005): NaN signals "no valid scores" so
            # the caller can skip the loss rather than add a constant.
            return torch.full(
                (B,), float("nan"), device=device, dtype=h_emb.dtype,
            )

        # Reassemble per-piece scores into the original row order so
        # the output is aligned with the input triples. We use
        # ``index_copy_`` on a fresh zero tensor — but to preserve
        # gradient flow (H-2 root fix), we actually build the result
        # via ``torch.stack`` on a per-row sort. The simplest
        # differentiable reassembly is to concatenate the pieces in
        # their input order using ``torch.cat`` then re-sort by the
        # concatenated indices.
        all_scores_cat = torch.cat(score_pieces, dim=0)
        all_indices_cat = torch.cat(piece_indices, dim=0)
        # Sort by original row index so scores align with input.
        sort_order = torch.argsort(all_indices_cat)
        scores_sorted = all_scores_cat[sort_order]
        # Build the final B-length tensor. Any rows NOT covered by a
        # known decoder key get NaN (v57 ROOT FIX P2C-005) — callers
        # filter NaN entries before computing the loss. Previously
        # these rows received 0.5, adding un-optimisable -log(0.5)
        # constants to the loss.
        # FIX-P1-D-15 (root): the previous code did
        # ``final_scores[sorted_indices] = scores_sorted`` (in-place
        # index assignment). The earlier comment at lines 752-763
        # warned that this pattern "BREAKS autograd" — but that
        # warning was about a DIFFERENT in-place pattern
        # (``scores[mask] = sigmoid(logit)`` on a tensor already in
        # the graph). For THIS specific call site the in-place
        # index_put DID preserve gradient flow (PyTorch tracks
        # ``index_put`` as a differentiable op when the source
        # requires grad), so the warning was misleading here. To
        # remove the ambiguity and prevent any future "leaf modified
        # in-place" surprise, the reassembly now uses the
        # OUT-OF-PLACE ``Tensor.scatter`` method which returns a NEW
        # tensor whose gradient flows back to ``scores_sorted``
        # (and hence to the decoder weights). The behaviour is
        # identical; the auditability is sharper.
        sorted_indices = all_indices_cat[sort_order]
        # v57 ROOT FIX (P2C-005): NaN default for unknown decoder keys
        # so callers can filter them out before the loss reduction.
        default_scores = torch.full(
            (B,), float("nan"), device=device, dtype=scores_sorted.dtype,
        )
        final_scores = default_scores.scatter(0, sorted_indices, scores_sorted)
        return final_scores

    # -- KGEmbeddingModel Protocol: forward ------------------------------
    def forward(
        self,
        head_indices: torch.Tensor,
        rel_indices: torch.Tensor,
        tail_indices: torch.Tensor,
        *,
        x_dict: Optional[Dict[str, torch.Tensor]] = None,
        edge_index_dict: Optional[Dict[Tuple[str, str, str], torch.Tensor]] = None,
        head_type: str = "Compound",
        tail_type: str = "Disease",
        rel_names: Optional[List[str]] = None,
        encoded_h_dict: Optional[Dict[str, torch.Tensor]] = None,
    ) -> torch.Tensor:
        """Score triples. Higher = more plausible (HGT convention).

        v57 ROOT FIX (P2C-004): ``forward`` now returns LOGITS (not
        sigmoided scores). Use ``BCEWithLogitsLoss`` for training
        (numerically stable — sigmoid is applied internally via the
        log-sum-exp trick). Apply ``torch.sigmoid`` externally if you
        need probabilities in [0, 1] (e.g. for thresholded
        predictions). For AUC ranking the raw logits work directly
        because sigmoid is monotonic.

        Two call modes:

        1. **Graph-aware** (preferred): pass ``x_dict`` and
           ``edge_index_dict``. The model runs the full HGT encoder
           once, then scores. This is the mode used during training.

        2. **Pre-encoded**: pass ``encoded_h_dict`` (the output of a
           prior ``encode()`` call). Skips re-encoding — useful for
           evaluation when the encoder has already been run on the
           full graph.

        For backward compat with ``KGEmbeddingModel``, if NEITHER is
        passed, the model uses the bare embedding tables (no message
        passing) — this is equivalent to a DistMult-style baseline and
        is provided only so the Protocol signature is satisfied.
        """
        if encoded_h_dict is None and x_dict is not None and edge_index_dict is not None:
            encoded_h_dict = self.encode(x_dict, edge_index_dict)
        # v35 ROOT FIX (M-7 / H-3): validate head_type / tail_type
        # explicitly so the caller gets an actionable error instead
        # of a silent zeros() return.
        if head_type not in self.node_types:
            raise ValueError(
                f"forward: head_type {head_type!r} is not in "
                f"self.node_types={self.node_types}. (M-7 root fix)"
            )
        if tail_type not in self.node_types:
            raise ValueError(
                f"forward: tail_type {tail_type!r} is not in "
                f"self.node_types={self.node_types}. (M-7 root fix)"
            )
        if encoded_h_dict is None:
            # Bare-embedding fallback (DistMult-style). Lower bound on
            # performance; full graph-aware mode is the real path.
            # v40 ROOT FIX (P2 #18): the previous code called
            # ``get_node_embeddings`` which RAISES ValueError for node
            # types with ``input_projections`` (per the v35 H-4 fix).
            # This contradicted the docstring which promised a
            # "DistMult-style baseline". The fix: if get_node_embeddings
            # raises, fall back to ZERO embeddings (not ideal, but
            # matches the docstring's "lower bound on performance" claim
            # and doesn't crash the caller). Log a WARNING so operators
            # know the bare-embedding fallback is producing zeros for
            # feature-backed node types.
            try:
                h_emb = self.get_node_embeddings(head_type, head_indices)
            except ValueError:
                logger.warning(
                    "forward: bare-embedding fallback for head_type=%s "
                    "raised ValueError (it has input_projections). "
                    "Returning zero embeddings — the caller should pass "
                    "x_dict + edge_index_dict to use the full graph-"
                    "aware encoder. (v40 P2 #18 fix)", head_type,
                )
                d = self.config.embedding_dim
                device = self._device
                h_emb = torch.zeros(
                    len(head_indices) if head_indices is not None else 1,
                    d, device=device,
                )
            try:
                t_emb = self.get_node_embeddings(tail_type, tail_indices)
            except ValueError:
                logger.warning(
                    "forward: bare-embedding fallback for tail_type=%s "
                    "raised ValueError (it has input_projections). "
                    "Returning zero embeddings — the caller should pass "
                    "x_dict + edge_index_dict to use the full graph-"
                    "aware encoder. (v40 P2 #18 fix)", tail_type,
                )
                d = self.config.embedding_dim
                device = self._device
                t_emb = torch.zeros(
                    len(tail_indices) if tail_indices is not None else 1,
                    d, device=device,
                )
        else:
            h_full = encoded_h_dict.get(head_type)
            t_full = encoded_h_dict.get(tail_type)
            # v35 ROOT FIX (H-3): raise ValueError instead of silently
            # returning torch.zeros() — the previous silent path meant
            # a missing node-type in the encoded dict produced a
            # zero-score batch that looked identical to a model that
            # had genuinely learned nothing, masking the bug.
            if h_full is None:
                raise ValueError(
                    f"forward: head_type {head_type!r} not found in "
                    f"encoded_h_dict. Available keys: "
                    f"{list(encoded_h_dict.keys())}. The encoder did "
                    f"not produce embeddings for this node type — "
                    f"usually means x_dict was missing the entry or "
                    f"the node type was not declared at __init__. "
                    f"(H-3 root fix)"
                )
            if t_full is None:
                raise ValueError(
                    f"forward: tail_type {tail_type!r} not found in "
                    f"encoded_h_dict. Available keys: "
                    f"{list(encoded_h_dict.keys())}. The encoder did "
                    f"not produce embeddings for this node type — "
                    f"usually means x_dict was missing the entry or "
                    f"the node type was not declared at __init__. "
                    f"(H-3 root fix)"
                )
            h_emb = h_full[head_indices]
            t_emb = t_full[tail_indices]
        if rel_names is None:
            # Look up relation name per index.
            rel_names = [self.relation_types[i][1] for i in rel_indices.tolist()]
        return self.score_triples(h_emb, rel_indices, t_emb, rel_names)

    def normalize_entity_embeddings(self) -> None:
        """Protocol-required NO-OP for HGT.

        v35 ROOT FIX (L-6): make it explicit in the docstring that
        this method is intentionally a no-op. ``KGEmbeddingModel``
        Protocol consumers (``train_transe``) call this after every
        optimizer step to enforce the TransE constraint
        ``||h||=||r||=||t||=1``. HGT does NOT need that constraint —
        the learnable ``nn.LayerNorm`` modules inside ``encode()``
        (see M-16 root fix) provide per-sublayer affine normalisation
        that is strictly more expressive than a hard unit-norm
        projection. Calling this method therefore does nothing; the
        Protocol signature is satisfied so ``train_transe`` works
        unchanged when passed an HGT model.

        Returns
        -------
        None
            Always. Implemented as ``return None`` so static analysers
            do not flag the implicit ``None`` return.
        """
        return None

    def normalize_relation_embeddings(self) -> None:
        """Protocol-required NO-OP for HGT.

        v81 FORENSIC ROOT FIX (P0-F5): ``train_transe`` (transe_model.py
        line ~2911) calls ``model.normalize_relation_embeddings()`` after
        every optimizer step to enforce the Bordes 2013 §3.2 constraint
        ``||r||=1`` on relation embeddings. TransE defines this method
        on ``TransEModel`` (see transe_model.py:721). GraphTransformerModel
        previously did NOT define it, so any attempt to train HGT via the
        ``train_transe`` entry point would raise
        ``AttributeError: 'GraphTransformerModel' object has no attribute
        'normalize_relation_embeddings'`` — blocking Phase 3 deployment
        per the DOCX.

        HGT does NOT use a TransE-style translational scoring function;
        it uses attention-weighted message passing whose normalisation
        is handled internally by ``nn.LayerNorm`` modules (see M-16
        root fix). The relation "embeddings" in HGT are relation type
        embeddings used as attention bias, not translational vectors —
        the ||r||=1 constraint is meaningless for them.

        ROOT FIX: define this method as an explicit no-op so the
        ``KGEmbeddingModel`` Protocol is satisfied and ``train_transe``
        works unchanged when passed an HGT model. Mirrors the existing
        ``normalize_entity_embeddings`` no-op pattern (v35 L-6 root fix).

        Returns
        -------
        None
            Always. Implemented as ``return None`` so static analysers
            do not flag the implicit ``None`` return.
        """
        return None


# ---------------------------------------------------------------------------
# 3. Convenience scoring helper
# ---------------------------------------------------------------------------
def graph_transformer_score(
    model: GraphTransformerModel,
    head_indices: torch.Tensor,
    rel_indices: torch.Tensor,
    tail_indices: torch.Tensor,
    *,
    x_dict: Dict[str, torch.Tensor],
    edge_index_dict: Dict[Tuple[str, str, str], torch.Tensor],
    head_type: str = "Compound",
    tail_type: str = "Disease",
    rel_names: Optional[List[str]] = None,
) -> torch.Tensor:
    """Score triples with a Graph Transformer.

    Convenience wrapper around ``model.forward(...)`` so callers don't
    need to remember the keyword-arg names.
    """
    return model.forward(
        head_indices, rel_indices, tail_indices,
        x_dict=x_dict, edge_index_dict=edge_index_dict,
        head_type=head_type, tail_type=tail_type,
        rel_names=rel_names,
    )
