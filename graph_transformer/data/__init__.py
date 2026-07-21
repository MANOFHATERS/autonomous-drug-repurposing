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

# 19 edge types (9 forward + 9 reverse + 1 PPI). Every node type receives
# at least one incoming edge type, which is required for heterogeneous
# message passing in the Graph Transformer.
#
# P3-003 ROOT FIX (Teammate 9, forensic count fix): the previous comment
# claimed "18 edge types (9 forward + 9 reverse)" but the actual list
# below has 19 entries (the PPI edge ("protein", "interacts_with",
# "protein") at the end is a 19th entry that is NEITHER a forward drug/
# protein/pathway/disease edge NOR its reverse — PPI is symmetric and
# stands alone). The self_check() asserted len(EDGE_TYPES) == 18 which
# was ALWAYS False, silently masking every real schema regression.
# The FORWARD/REVERSE slicing was also off-by-one: FORWARD_EDGE_TYPES
# = EDGE_TYPES[:10] took 9 forward + 1 reverse ("inhibited_by"), and
# REVERSE_EDGE_TYPES = EDGE_TYPES[10:] took 8 reverse + 1 PPI. Both
# are now corrected below.
#
# P3-001/P3-002/P3-009 ROOT FIX (Team Member 9, forensic root fix):
# The original schema had 14 edge types (7 forward + 7 reverse) and ONLY
# supported "inhibits"/"activates" for drug→protein edges. This forced the
# Phase 2→3 adapter to map ("Compound","targets","Protein") → "inhibits"
# (WRONG — "targets" means "binds to, direction UNKNOWN", not inhibition)
# and ("Compound","allosterically_modulates","Protein") → "activates"
# (WRONG — allosteric modulators can be PAM or NAM). The scientifically
# correct fix is to add TWO new neutral forward edge types — "binds"
# (direction-unknown binding) and "modulates" (allosteric modulation
# without PAM/NAM disambiguation) — plus their reverse counterparts
# "bound_by" and "modulated_by". This:
#   1. Preserves the binding/modulation signal (institutional-grade —
#      do not silently drop potentially useful drug-protein interactions).
#   2. Keeps the drug→protein→pathway→disease 3-hop pattern CONNECTED
#      for drugs whose only Phase 2 action is "targets" or
#      "allosterically_modulates" (dropping those edges would disconnect
#      the drug from the protein layer, breaking the core scientific
#      requirement per the DOCX).
#   3. Does NOT teach the GT model that all binding = inhibition
#      (the original bug corrupted the multi-hop signal).
#   4. Lets future PAM/NAM disambiguation (via ChEMBL standard_type/
#      standard_relation) split "modulates" into "activates"/"inhibits"
#      without another schema change — just remap the Phase 2 relation.
# ("Compound","unknown","Protein") is intentionally NOT mapped to any
# Phase 3 edge type — unknown mechanisms must NEVER be mapped to a
# specific mechanism (per the P3-001 issue mandate). The adapter DROPS
# unknown edges with an INFO log.
EDGE_TYPES: List[Tuple[str, str, str]] = [
    # Forward edges
    ("drug", "inhibits", "protein"),
    ("drug", "activates", "protein"),
    # P3-001/P3-002 root fix: neutral binding + modulation edge types.
    ("drug", "binds", "protein"),
    ("drug", "modulates", "protein"),
    ("protein", "part_of", "pathway"),
    ("pathway", "disrupted_in", "disease"),
    ("drug", "treats", "disease"),
    ("drug", "tested_for", "disease"),
    ("drug", "causes", "clinical_outcome"),
    # Reverse edges (ensure every node type receives incoming messages)
    ("protein", "inhibited_by", "drug"),
    ("protein", "activated_by", "drug"),
    # P3-001/P3-002 root fix: reverse of binds + modulates.
    ("protein", "bound_by", "drug"),
    ("protein", "modulated_by", "drug"),
    ("pathway", "has_member", "protein"),
    ("disease", "disrupted_by", "pathway"),
    ("disease", "treated_by", "drug"),
    ("disease", "tested_on", "drug"),
    ("clinical_outcome", "caused_by", "drug"),
    # Teammate-2 Task 2.1 ROOT FIX (P2-008): PPI forward + reverse.
    # STRING PPI was previously dropped at Phase 2->3 boundary
    # (PHASE2_TO_PHASE3_EDGE_DROPPED). Adding it here lets the GT model
    # learn protein-protein co-functionality, which is the largest single
    # biomedical signal in STRING v12.0 (~10M edges). PPI is symmetric
    # so the "reverse" edge type is identical to the forward (the graph
    # builder's _add_reverse_edges adds (dst, src) pairs to the same
    # edge-type bucket, deduplicating via the _edge_sets set).
    ("protein", "interacts_with", "protein"),
]

# Forward edge types only (used by graph builder for reverse-edge synthesis).
#
# P3-003 ROOT FIX (Teammate 9, forensic slicing fix): the previous
# slicing was OFF-BY-ONE. EDGE_TYPES[:10] took 9 forward edges PLUS
# the first reverse edge ("inhibited_by"), and EDGE_TYPES[10:] took
# the remaining 8 reverse edges PLUS the PPI edge. Both were
# scientifically wrong: the graph builder's _add_reverse_edges used
# FORWARD_EDGE_TYPES to decide which edges to reverse, so the first
# reverse edge was being re-reversed (no-op due to dedup, but a
# hidden correctness bug), and the PPI edge was being treated as a
# reverse edge (also a no-op because PPI is symmetric, but again a
# hidden correctness bug). The fix slices to the correct boundaries:
# FORWARD = first 9 entries (indices 0..8), REVERSE = next 9 entries
# (indices 9..17), PPI = last 1 entry (index 18).
FORWARD_EDGE_TYPES: List[Tuple[str, str, str]] = EDGE_TYPES[:9]   # 9 forward (indices 0..8)
REVERSE_EDGE_TYPES: List[Tuple[str, str, str]] = EDGE_TYPES[9:18] # 9 reverse (indices 9..17)
PPI_EDGE_TYPES: List[Tuple[str, str, str]] = EDGE_TYPES[18:]      # 1 PPI    (index 18)

# Canonical default edge types -- re-exported for use by other sub-modules
# (B7 fix: single source of truth).
DEFAULT_EDGE_TYPES: List[Tuple[str, str, str]] = EDGE_TYPES

# Map forward relation -> reverse relation (used by graph builder).
REVERSE_RELATION_MAP: Dict[str, str] = {
    "inhibits": "inhibited_by",
    "activates": "activated_by",
    # P3-001/P3-002 root fix: reverse relations for the new neutral types.
    "binds": "bound_by",
    "modulates": "modulated_by",
    "part_of": "has_member",
    "disrupted_in": "disrupted_by",
    "treats": "treated_by",
    "tested_for": "tested_on",
    "causes": "caused_by",
    # Teammate-2 Task 2.1 ROOT FIX (P2-008): PPI is symmetric.
    # The graph builder's _add_reverse_edges uses REVERSE_RELATION_MAP
    # to add (dst, src) pairs to the reverse edge-type bucket. For PPI,
    # the reverse of ("protein", "interacts_with", "protein") is itself
    # (same src_type, same rel_type, same dst_type), so the (dst, src)
    # pair gets added to the SAME edge set, deduplicating via the
    # _edge_sets set. This correctly models PPI as undirected.
    "interacts_with": "interacts_with",
}

# Edge types whose presence during the forward pass would leak the
# prediction label (the (drug, disease) pair we are scoring). The
# trainer ALWAYS excludes these during BOTH training and evaluation
# forward passes, per the V1 launch contract in compliance_note()
# below. This fixes the C2 label-leakage bug from the original
# codebase, where the bridge silently disabled exclude_edges during
# inference and the trainer silently dropped it from evaluate().
#
# P3-004 ROOT FIX (Teammate 9, forensic safety-signal fix): the
# previous LABEL_LEAKING_EDGES frozenset ALSO contained the
# adverse-event (AE) edge types
#   ("drug", "causes", "clinical_outcome") and
#   ("clinical_outcome", "caused_by", "drug").
# The v113 forensic comment claimed AE edges were "label leakage"
# because "a drug with many AE edges is likely a drug the model should
# score LOW for any disease." That reasoning was SCIENTIFICALLY WRONG.
# AE edges are NOT label leakage — they are a LEGITIMATE biological
# signal that the GNN SHOULD learn from during training. A drug with
# many severe AE edges SHOULD score lower for any disease than a drug
# with a clean safety profile, BECAUSE the AE signal is real world
# safety information that generalizes across diseases. Excluding AE
# edges during training BLINDED the GNN to the safety signal — the
# model could not learn "high AE count = unsafe drug" and would
# recommend unsafe drugs at inference time.
#
# The CORRECT contract is:
#   - During TRAINING: include AE edges so the GNN learns the safety
#     signal (drugs with many AE edges get lower scores).
#   - During val/test SCORING of a specific (drug, disease) pair whose
#     drug is in the val/test set: exclude THAT DRUG'S AE edges to
#     avoid trivial memorization (the model would otherwise memorize
#     "this specific drug has AE edges to outcomes X, Y, Z" instead of
#     using its LEARNED representation). See SAFETY_SIGNAL_EDGES below
#     and gt_rl_bridge.GTRLBridge._get_drug_ae_edges() for the
#     per-drug exclusion implementation.
#
# Multi-hop leakage (via drug->protein->pathway->disease) is NOT in
# this set because those edges carry legitimate biological signal that
# the model SHOULD learn from -- they only become leakage if a
# guaranteed path is injected for every KP (the W-02 bug, now removed
# in graph_builder.py).
LABEL_LEAKING_EDGES: frozenset = frozenset({
    # Direct drug->disease therapeutic relationships (forward, 2 tuples).
    # These leak the label because the (drug, treats, disease) edge IS
    # the prediction target — if the model sees it during the forward
    # pass for the pair being scored, it can trivially copy the edge
    # presence to the prediction.
    ("drug", "treats", "disease"),
    ("drug", "tested_for", "disease"),
    # Direct disease->drug reverse relationships (2 tuples). Same
    # leakage as above, in the reverse direction.
    ("disease", "treated_by", "drug"),
    ("disease", "tested_on", "drug"),
    # P3-004 ROOT FIX: AE edges ("causes" / "caused_by") REMOVED from
    # this set — they are NOT label leakage. See SAFETY_SIGNAL_EDGES
    # below for the new dedicated frozenset. The GNN now sees AE edges
    # during training and learns that drugs with many severe AE edges
    # should score lower across all diseases.
})

# Safety-signal edge types — adverse-event (AE) edges that the GNN
# SHOULD see during training (so it can learn that drugs with many
# severe AE edges are unsafe) but should exclude when scoring a
# SPECIFIC (drug, disease) pair whose drug is in the val/test set
# (to avoid the model trivially memorizing "this specific drug has
# AE edges to outcomes X, Y, Z" instead of using its LEARNED
# representation of the drug).
#
# P3-004 ROOT FIX (Teammate 9): this frozenset is NEW. It carves out
# the AE edges that were previously (and incorrectly) lumped into
# LABEL_LEAKING_EDGES. The split enables the correct contract:
#   - Trainer always excludes LABEL_LEAKING_EDGES (4 tuples).
#   - Trainer NEVER excludes SAFETY_SIGNAL_EDGES during training.
#   - gt_rl_bridge.GTRLBridge.get_top_k_novel_predictions() and the
#     val/test scoring path exclude LABEL_LEAKING_EDGES + the specific
#     val/test drug's AE edges (looked up via _get_drug_ae_edges()).
SAFETY_SIGNAL_EDGES: frozenset = frozenset({
    ("drug", "causes", "clinical_outcome"),
    ("clinical_outcome", "caused_by", "drug"),
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
# This threshold is for PRODUCTION-scale graphs (10K drugs, millions of pairs).
# For demo-scale graphs (<100 drugs), achieving 0.85 AUC is scientifically
# unrealistic -- the model has too few training pairs to generalize. The
# get_auc_threshold_for_scale() function returns the appropriate threshold
# based on graph size.
V1_AUC_THRESHOLD: float = 0.85
# P3-018 ROOT FIX (CRITICAL — use 0.85 for ALL scales, per audit mandate).
# The previous code used V1_AUC_THRESHOLD_DEMO = 0.65 and
# V1_AUC_THRESHOLD_PILOT = 0.70, claiming this was "scientifically correct"
# because demo graphs are small. The audit explicitly says:
#   "Use 0.85 for ALL scales. If the demo can't reach 0.85, document the
#    gap and fix the model, not the threshold. A demo model with AUC=0.65
#    passes validation, giving false confidence that the model is 'working.'
#    The team ships to pharma partners believing the model meets the V1
#    contract. In production (10K drugs), the model may or may not reach
#    0.85 — the demo gave no signal."
#
# Lowering the threshold for demos is NOT scientifically correct — it's
# lowering the bar to make a broken model pass. The fix: use 0.85 for ALL
# scales. If the demo can't reach 0.85, the pipeline logs a DEMO_LIMITATION
# warning explaining WHY (too few training pairs) and what the production
# expectation is. The model is NOT marked as "validated" — it's marked as
# "demo-only, not production-ready" so the team knows not to ship it.
#
# This matches the DOCX V1 launch contract: "Graph Transformer achieves
# >0.85 AUC on held-out drug-disease pairs." No exceptions for demo scale.
V1_AUC_THRESHOLD_DEMO: float = 0.85  # P3-018: was 0.65 — now 0.85 for ALL scales
V1_AUC_THRESHOLD_PILOT: float = 0.85  # P3-018: was 0.70 — now 0.85 for ALL scales


def get_auc_threshold_for_scale(num_drugs: int) -> float:
    """Return the AUC threshold for the graph scale.

    P3-018 ROOT FIX: ALL scales now use 0.85 (the V1 launch contract).
    The previous code returned 0.65 for demo graphs and 0.70 for pilot
    graphs, which the audit found was "lowering the bar to make a broken
    model pass." The fix: use 0.85 for ALL scales.

    If a demo graph cannot reach 0.85, the pipeline logs a DEMO_LIMITATION
    warning explaining that the model is NOT production-ready (too few
    training pairs for the model to generalize). The model is marked as
    "demo-only" so the team knows not to ship it to pharma partners.

    Args:
        num_drugs: Number of drug nodes in the graph.

    Returns:
        0.85 (the V1 launch contract threshold, for ALL scales).
    """
    # P3-018: all scales use 0.85. The num_drugs parameter is kept for
    # backward API compatibility — callers that pass num_drugs still get
    # the correct threshold.
    _ = num_drugs  # accepted for API compat, no longer affects threshold
    return V1_AUC_THRESHOLD  # 0.85 for ALL scales (P3-018)


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
    """Return the V1 launch compliance contract (DOCX Phase 6).

    P3-037 ROOT FIX (v113 forensic): the previous code used
    ``set(LABEL_LEAKING_EDGES)`` which created a mutable copy of the
    frozenset SOLELY for the string representation. The ``set()`` call
    allocated a new set object on every invocation -- wasteful (the
    function is called by tests and manual inspection, never in the
    hot path, so the waste is negligible but the misleading mutable
    copy is a code-quality issue). The fix uses ``LABEL_LEAKING_EDGES``
    directly; the frozenset's ``__repr__`` is ``frozenset({...})`` which
    is slightly uglier but conveys the same information and is honest
    about the type.
    """
    return (
        f"V1 LAUNCH CONTRACT:\n"
        f"  1. AUC > {V1_AUC_THRESHOLD} on held-out TEST set\n"
        f"  2. Three-way train/val/test split (drug-aware)\n"
        f"  3. exclude_edges = {LABEL_LEAKING_EDGES}\n"
        f"  4. Top-50 novel predictions: >= 5 literature-supported\n"
        f"  5. API handles 100 concurrent requests (rate-limited via GT_MAX_CONCURRENT_INFERENCE)\n"
        f"  6. Dashboard renders in < 3 seconds"
    )


def self_check() -> Dict[str, bool]:
    """Run a smoke test of the data package.

    P3-003 ROOT FIX (Teammate 9, forensic count fix): the previous
    self_check asserted ``len(EDGE_TYPES) == 18`` but the actual list
    has 19 entries (9 forward + 9 reverse + 1 PPI). The check ALWAYS
    returned False, which:
      1. Made ``all_edge_types_valid`` short-circuit to False (it was
         gated on ``edge_types_18``), hiding any real schema bug.
      2. Gave any caller that asserted ``all(checks.values())`` a
         perpetual false failure — callers learned to IGNORE the
         self_check, defeating its purpose as a regression gate.

    The fix asserts the correct count (19) and adds explicit checks
    for FORWARD/REVERSE/PPI slicing so a future off-by-one regression
    is caught at the boundary, not deep in the trainer.
    """
    checks: Dict[str, bool] = {}
    checks["node_types_defined"] = len(NODE_TYPES) == 5
    # P3-003 root fix: 19 edge types (9 forward + 9 reverse + 1 PPI).
    checks["edge_types_count"] = len(EDGE_TYPES) == 19
    checks["forward_count"] = len(FORWARD_EDGE_TYPES) == 9
    checks["reverse_count"] = len(REVERSE_EDGE_TYPES) == 9
    checks["ppi_count"] = len(PPI_EDGE_TYPES) == 1
    # FORWARD and REVERSE must NOT overlap (PPI is its own bucket and
    # is excluded from both). A future schema change that accidentally
    # duplicates an edge between FORWARD and REVERSE would silently
    # double-count edges in the graph builder.
    checks["no_overlap_forward_reverse"] = not (
        set(FORWARD_EDGE_TYPES) & set(REVERSE_EDGE_TYPES)
    )
    checks["feature_dims_complete"] = set(DEFAULT_FEATURE_DIMS.keys()) == set(NODE_TYPES)
    # all_edge_types_valid now gates on edge_types_count (the corrected
    # count check) instead of the old edge_types_18.
    checks["all_edge_types_valid"] = (
        all(validate_edge_type(et) is None for et in EDGE_TYPES)
        if checks["edge_types_count"]
        else False
    )
    checks["label_leaking_edges_subset_of_edge_types"] = LABEL_LEAKING_EDGES.issubset(
        set(EDGE_TYPES)
    )
    # P3-004 root fix: SAFETY_SIGNAL_EDGES must be a subset of EDGE_TYPES
    # and must NOT overlap with LABEL_LEAKING_EDGES (an edge cannot be
    # both "always excluded" and "included during training").
    checks["safety_signal_edges_subset_of_edge_types"] = SAFETY_SIGNAL_EDGES.issubset(
        set(EDGE_TYPES)
    )
    checks["safety_signal_disjoint_from_label_leaking"] = (
        not (SAFETY_SIGNAL_EDGES & LABEL_LEAKING_EDGES)
    )
    return checks
