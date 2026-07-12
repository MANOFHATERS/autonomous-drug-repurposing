"""DrugOS Graph Module -- KG Embedding Model Protocol
=====================================================
Defines the structural interface (Protocol) for any knowledge graph
embedding model used in the DrugOS pipeline.

Why a Protocol?
  * ``TransEModel`` (Week 2 baseline) and the Phase 3 Graph Transformer
    both produce entity/relation embeddings and scores. This Protocol
    lets ``train_transe``, ``predict_drug_candidates``, and evaluation
    code accept ANY model that conforms to the interface -- enabling
    drop-in replacement without changing downstream consumers.
  * Fixes A1.6 -- transe_model.py was not interchangeable with future models.

Interoperability:
  * Any model implementing this Protocol can be passed to
    ``train_transe(model=..., ...)``, ``predict_drug_candidates(model=..., ...)``,
    and ``evaluate_link_prediction(...)`` without code changes.

Fixes: A1.6, I15.13.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable, Tuple

import torch


@runtime_checkable
class KGEmbeddingModel(Protocol):
    """Structural interface for knowledge graph embedding models.

    Any model that provides entity/relation embeddings and a score
    function ``(head, relation, tail) -> scores`` satisfies this Protocol.
    ``@runtime_checkable`` enables ``isinstance(model, KGEmbeddingModel)``
    checks at runtime (for validation guards, not for branching logic).

    Attributes:
        entity_embeddings: An ``nn.Embedding`` whose ``.weight`` tensor
            has shape ``(num_entities, embedding_dim)``.
        relation_embeddings: An ``nn.Embedding`` whose ``.weight`` tensor
            has shape ``(num_relations, embedding_dim)``.

    Methods:
        forward(head_indices, rel_indices, tail_indices) -> Tensor:
            Compute plausibility scores for triples.
        normalize_entity_embeddings() -> None:
            Normalize entity embeddings (TransE-specific convention).
    """

    @property
    def entity_embeddings(self) -> torch.nn.Embedding:
        """Entity embedding lookup table: (num_entities, embedding_dim)."""
        ...

    @property
    def relation_embeddings(self) -> torch.nn.Embedding:
        """Relation embedding lookup table: (num_relations, embedding_dim)."""
        ...

    def forward(
        self,
        head_indices: torch.Tensor,
        rel_indices: torch.Tensor,
        tail_indices: torch.Tensor,
    ) -> torch.Tensor:
        """Compute plausibility score for each (h, r, t) triple.

        Args:
            head_indices: Entity index tensor for triple heads.
            rel_indices: Relation index tensor.
            tail_indices: Entity index tensor for triple tails.

        Returns:
            Tensor of shape ``(batch_size,)`` with one score per triple.
            Convention varies by model: TransE uses L2 distance (lower=better);
            the Phase 3 Graph Transformer may use dot product (higher=better).
        """
        ...

    def normalize_entity_embeddings(self) -> None:
        """Normalize entity embeddings to unit L2 norm (TransE convention).

        Called after each optimizer step. Models that do not require
        normalization (e.g., DistMult, ComplEx) can implement this as
        a no-op.
        """
        ...

    # v43 ROOT FIX (P1 — score_direction not in Protocol): the Protocol
    # did not declare score_direction, so a model that doesn't declare
    # it silently defaults to "lower_better" via getattr in train_transe.
    # HGT (higher_better) would fail the assertion at training time —
    # but only at training time, not at Protocol compliance time. Adding
    # score_direction to the Protocol makes the contract explicit.
    @property
    def score_direction(self) -> str:
        """Scoring convention: 'lower_better' (TransE) or 'higher_better' (HGT).

        - 'lower_better': score = -||h+r-t|| (TransE, Bordes 2013).
          Lower score = more plausible triple.
        - 'higher_better': score = sigmoid(W·[h||r||t]) (HGT, Graph
          Transformer). Higher score = more plausible triple.

        train_transe uses this to decide the loss function and the AUC
        direction. predict_drug_candidates uses it to decide topk
        direction (largest=False for lower_better, largest=True for
        higher_better).
        """
        ...

    # v102 ROOT FIX (P2-039): num_total_entities not in Protocol.
    #
    # The previous train_transe code at line 2219 used
    # ``getattr(model, "num_total_entities", None)`` with a fallback to
    # ``model.entity_embeddings.num_embeddings``. The comment claimed
    # this was "for HGT" — but neither TransE NOR HGT was REQUIRED to
    # expose num_total_entities. HGT did (added in v43), TransE did
    # not. The getattr was therefore dead code for TransE: it always
    # fell through to the fallback. This is misleading for maintainers:
    # a future developer adding a new heterogeneous model would see
    # ``getattr(model, "num_total_entities", None)`` and assume the
    # existing code path uses it — but the contract was undocumented.
    #
    # ROOT FIX: add ``num_total_entities`` to the KGEmbeddingModel
    # Protocol as a REQUIRED property. Document that:
    #   - Homogeneous models (TransE): returns entity_embeddings.num_embeddings
    #     (the single entity table's row count).
    #   - Heterogeneous models (HGT): returns the SUM of all node-type
    #     entity counts (the cross-type index space used for negative
    #     sampling).
    # This makes the contract explicit and forward-compatible: any new
    # model that implements KGEmbeddingModel MUST expose
    # num_total_entities, and the train_transe code path will use it.
    @property
    def num_total_entities(self) -> int:
        """Total entity count across ALL node/entity tables.

        - Homogeneous models (TransE): equals
          ``entity_embeddings.num_embeddings`` (single entity table).
        - Heterogeneous models (HGT): equals
          ``sum(self._node_counts.values())`` — the SUM of all node-
          type counts. Used for index-range validation in train_transe
          and for negative sampling space sizing.

        Callers that need the TOTAL entity count MUST use this property
        instead of ``entity_embeddings.num_embeddings`` (which returns
        only the FIRST node type's count on heterogeneous models, leading
        to out-of-range negative samples and index-range validation
        failures — see P2-039 root cause).
        """
        ...


__all__: list[str] = ["KGEmbeddingModel"]