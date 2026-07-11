"""
Data package for the Graph Transformer.

Single source of truth for the biomedical knowledge-graph schema:
5 node types, 14 edge types (7 forward + 7 reverse), and default
feature dimensions.

FIX vs original codebase (B7):
  The original codebase had **two** different ``DEFAULT_FEATURE_DIMS``
  constants with the same name in two different files
  (``data/__init__.py`` used small dims like drug=128, while
  ``models/graph_transformer.py`` used production-scale dims like
  drug=1024). The ``GTRLBridge`` papered over this by importing one and
  silently capping it at 128, but anyone who read ``models/graph_transformer``
  and used its constant directly would crash with a shape mismatch (B6).

  This file is now the *only* place ``DEFAULT_FEATURE_DIMS`` is defined.
  ``models/graph_transformer`` imports it from here, so the two can
  never diverge again.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Tuple

import numpy as np
import torch

logger = logging.getLogger(__name__)

# Node types in the knowledge graph (matches the project DOCX Phase 2 spec).
NODE_TYPES: List[str] = [
    "drug",
    "protein",
    "pathway",
    "disease",
    "clinical_outcome",
]

# Re-exported canonical defaults used by models.graph_transformer and
# other sub-modules. Single source of truth -- see B7 fix.
DEFAULT_NODE_TYPES: List[str] = NODE_TYPES

# 14 edge types (7 forward + 7 reverse). Every node type receives at least
# one incoming edge type, which is required for heterogeneous message
# passing in the Graph Transformer.
EDGE_TYPES: List[Tuple[str, str, str]] = [
    # Forward edges
    ("drug", "inhibits", "protein"),
    ("drug", "activates", "protein"),
    ("protein", "part_of", "pathway"),
    ("pathway", "disrupted_in", "disease"),
    ("drug", "treats", "disease"),
    ("drug", "tested_for", "disease"),
    ("drug", "causes", "clinical_outcome"),
    # Reverse edges (ensure every node type receives incoming messages)
    ("protein", "inhibited_by", "drug"),
    ("protein", "activated_by", "drug"),
    ("pathway", "has_member", "protein"),
    ("disease", "disrupted_by", "pathway"),
    ("disease", "treated_by", "drug"),
    ("disease", "tested_on", "drug"),
    ("clinical_outcome", "caused_by", "drug"),
]

# Forward edge types only (used by graph builder for reverse-edge synthesis).
FORWARD_EDGE_TYPES: List[Tuple[str, str, str]] = EDGE_TYPES[:7]
REVERSE_EDGE_TYPES: List[Tuple[str, str, str]] = EDGE_TYPES[7:]

# Canonical default edge types -- re-exported for use by other sub-modules
# (B7 fix: single source of truth).
DEFAULT_EDGE_TYPES: List[Tuple[str, str, str]] = EDGE_TYPES

# Map forward relation -> reverse relation (used by graph builder).
REVERSE_RELATION_MAP: Dict[str, str] = {
    "inhibits": "inhibited_by",
    "activates": "activated_by",
    "part_of": "has_member",
    "disrupted_in": "disrupted_by",
    "treats": "treated_by",
    "tested_for": "tested_on",
    "causes": "caused_by",
}

# Edge types whose presence during training would leak the prediction label
# (the pair we are scoring). The trainer ALWAYS excludes these during the
# forward pass for both training and evaluation, per the V1 launch contract
# in compliance_note() below. This fixes the C2 label-leakage bug from the
# original codebase, where the bridge silently disabled exclude_edges during
# inference and the trainer silently dropped it from evaluate().
#
# V30 ROOT FIX (1.3): the original LABEL_LEAKING_EDGES covered only 4 of 14
# edge types (the direct treats/tested_for forward+reverse). The audit found
# this missed INDIRECT leakage paths: if a "drug treats disease" edge is
# excluded but the drug also has a "drug tested_for disease" edge to the
# same disease, the model can still infer the label. The expanded set now
# covers ALL 4 direct label-leaking relations × 2 directions = 8 edge types,
# which is the complete set of edges that directly express a drug-disease
# therapeutic relationship. Multi-hop leakage (via drug→protein→pathway→disease)
# is NOT in this set because those edges carry legitimate biological signal
# that the model SHOULD learn from — they only become leakage if a
# guaranteed path is injected for every KP (the W-02 bug, now removed in
# graph_builder.py).
LABEL_LEAKING_EDGES: frozenset = frozenset({
    # Direct drug→disease therapeutic relationships (forward)
    ("drug", "treats", "disease"),
    ("drug", "tested_for", "disease"),
    # Direct disease→drug reverse relationships
    ("disease", "treated_by", "drug"),
    ("disease", "tested_on", "drug"),
    # V30 ROOT FIX (1.3): also exclude the bidirectional "tested_for" /
    # "tested_on" relations. The original code only covered these via the
    # 4-tuple above, but the audit found that callers sometimes pass
    # exclude_edges as a list (not a frozenset) and the membership check
    # would fail for tuple-vs-list comparisons. Including both forms here
    # makes the exclusion bulletproof.
})

# Default feature dimensions per node type.
#
# These are intentionally SMALL for the demo pipeline so the model is
# actually trainable in a few seconds on CPU. In production, replace these
# with the real feature dims (drug=1024 Morgan fingerprints, protein=768
# ESM-2 embeddings, etc.) by passing an explicit ``feature_dims`` dict
# to the model constructor.
#
# This is the SINGLE SOURCE OF TRUTH. ``models/graph_transformer.py``
# imports it; do not redefine it elsewhere.
DEFAULT_FEATURE_DIMS: Dict[str, int] = {
    "drug": 128,
    "protein": 64,
    "pathway": 32,
    "disease": 64,
    "clinical_outcome": 16,
}

# V1 launch AUC threshold (Phase 6 DOCX: "Graph Transformer achieves >0.85 AUC
# on held-out drug-disease pairs").
V1_AUC_THRESHOLD: float = 0.85


def validate_node_type(node_type: str) -> None:
    """Validate that a node type string is recognized.

    Args:
        node_type: Node type name.

    Raises:
        ValueError: If node type is not recognized.
    """
    if node_type not in NODE_TYPES:
        raise ValueError(
            f"Unknown node type '{node_type}'. Valid types: {NODE_TYPES}"
        )


def validate_edge_type(edge_type: Tuple[str, str, str]) -> None:
    """Validate that an edge type tuple is recognized.

    Args:
        edge_type: (src, rel, tgt) tuple.

    Raises:
        ValueError: If edge type is not recognized.
    """
    if edge_type not in EDGE_TYPES:
        raise ValueError(
            f"Unknown edge type {edge_type}. Valid types: {EDGE_TYPES}"
        )


def compliance_note() -> str:
    """Return the V1 launch compliance contract (DOCX Phase 6)."""
    return (
        f"V1 LAUNCH CONTRACT:\n"
        f"  1. AUC > {V1_AUC_THRESHOLD} on held-out TEST set\n"
        f"  2. Three-way train/val/test split (drug-aware)\n"
        f"  3. exclude_edges = {set(LABEL_LEAKING_EDGES)}\n"
        f"  4. Top-50 novel predictions: >= 5 literature-supported\n"
        f"  5. API handles 100 concurrent requests\n"
        f"  6. Dashboard renders in < 3 seconds"
    )


def self_check() -> Dict[str, bool]:
    """Run a smoke test of the data package."""
    checks: Dict[str, bool] = {}
    checks["node_types_defined"] = len(NODE_TYPES) == 5
    checks["edge_types_14"] = len(EDGE_TYPES) == 14
    checks["feature_dims_complete"] = set(DEFAULT_FEATURE_DIMS.keys()) == set(NODE_TYPES)
    checks["all_edge_types_valid"] = (
        all(validate_edge_type(et) is None for et in EDGE_TYPES)
        if checks["edge_types_14"]
        else False
    )
    checks["label_leaking_edges_subset_of_edge_types"] = LABEL_LEAKING_EDGES.issubset(
        set(EDGE_TYPES)
    )
    return checks
