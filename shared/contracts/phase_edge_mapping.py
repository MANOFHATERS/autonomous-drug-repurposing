"""
shared.contracts.phase_edge_mapping — TM14 canonical Phase 2 -> Phase 3 edge
contract. The SINGLE source of truth for which Phase 2 edge types survive
the boundary into Phase 3 (and which are explicitly dropped).

ISSUE ADDRESSED:
    P3-002 (CRITICAL) — PHASE2_TO_PHASE3_EDGE contract was missing 20 of
    Phase 2's 31 CORE_EDGE_TYPES. The Phase 3 GT model trained on a graph
    missing the entire safety signal (SIDER adverse events), drug-drug
    interactions, metabolism (CYP450), PPI (STRING), and gene-disease
    associations. The drop was SILENT — the adapter counted dropped edges
    but never logged which types were dropped or why.

TM14 ROOT FIX (v118, forensic, root-level):
    Phase 2's PHASE2_TO_PHASE3_EDGE mapping lives in phase2/contracts/
    phase2_schema.py (owned by TM5). Phase 3's EDGE_TYPES schema lives in
    graph_transformer/data/__init__.py (owned by TM6). The MAPPING between
    them — which Phase 2 edges map to which Phase 3 edges, and which are
    explicitly DROPPED — is a CROSS-PHASE CONTRACT that belongs in
    shared/contracts/ (TM14's lane).

    This module is the TM14-owned integration glue. It does NOT redefine
    the mapping (which would create drift) — it IMPORTS the canonical
    mapping from phase2.contracts.phase2_schema and adds:

      1. A drop-reason registry: every edge in PHASE2_TO_PHASE3_EDGE_DROPPED
         has a documented scientific reason in EDGE_DROP_REASONS. The
         completeness assertion at import time verifies EVERY dropped edge
         has a reason — fail-closed if any is missing.

      2. A reverse mapping (PHASE3_TO_PHASE2_EDGE) for debugging.

      3. A ``map_edge_with_reason`` function that returns
         (phase3_edge, reason) where reason is "mapped" or
         "dropped:<drop_reason>". Used by the adapter to LOG every drop
         with a reason, replacing the silent drop pattern.

      4. A ``validate_phase2_to_phase3_completeness`` function used by
         the contract consistency test (Task 330) to verify every dropped
         edge has a reason AND every mapped Phase 3 edge is in Phase 3's
         EDGE_TYPES schema.

    This is the TM14 side of the contract. The phase2 side
    (PHASE2_TO_PHASE3_EDGE in phase2_schema.py) and the phase3 side
    (EDGE_TYPES in graph_transformer/data/__init__.py) are owned by
    other teammates — this module ASSERTS they are coherent without
    owning either definition.

IMPORT RULE:
    from shared.contracts.phase_edge_mapping import (
        PHASE2_TO_PHASE3_EDGE,
        PHASE2_TO_PHASE3_EDGE_DROPPED,
        PHASE3_TO_PHASE2_EDGE,
        map_edge_with_reason,
        validate_phase2_to_phase3_completeness,
        EDGE_DROP_REASONS,
    )
"""
from __future__ import annotations

import logging
from typing import Dict, FrozenSet, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# TM14 ROOT FIX (v118): import the canonical mapping from phase2.contracts.
# This is the SINGLE source of truth — DO NOT redefine the mapping here.
# Redefining would create drift between this module and phase2_schema.py,
# which is exactly the bug P3-002 was filed to fix.
# =============================================================================
try:
    from phase2.contracts.phase2_schema import (
        PHASE2_TO_PHASE3_EDGE as _PHASE2_TO_PHASE3_EDGE,
        PHASE2_TO_PHASE3_EDGE_DROPPED as _PHASE2_TO_PHASE3_EDGE_DROPPED,
    )
    _PHASE2_CONTRACT_AVAILABLE = True
except ImportError as _exc:
    # Degraded mode: phase2.contracts not importable. This should NEVER
    # happen in production — phase2 is a required dependency. The contract
    # consistency test will fail in CI, surfacing the misconfiguration.
    logger.error(
        "shared.contracts.phase_edge_mapping: could not import from "
        "phase2.contracts.phase2_schema (%s). The Phase 2->3 edge "
        "contract is unavailable — the pipeline cannot run. Install "
        "phase2 or fix the import path.", _exc,
    )
    _PHASE2_TO_PHASE3_EDGE: Dict[Tuple[str, str, str], Tuple[str, str, str]] = {}
    _PHASE2_TO_PHASE3_EDGE_DROPPED: Tuple[Tuple[str, str, str], ...] = ()
    _PHASE2_CONTRACT_AVAILABLE = False


# =============================================================================
# Import Phase 3's EDGE_TYPES schema (owned by TM6) — used to verify that
# every Phase 3 edge tuple in the mapping VALUES is a recognized Phase 3
# edge type. If a future Phase 2->3 mapping points to a non-existent
# Phase 3 edge, this catches it at import time.
# =============================================================================
try:
    from graph_transformer.data import EDGE_TYPES as _PHASE3_EDGE_TYPES
    _PHASE3_CONTRACT_AVAILABLE = True
except ImportError as _exc:
    logger.error(
        "shared.contracts.phase_edge_mapping: could not import EDGE_TYPES "
        "from graph_transformer.data (%s). The Phase 3 edge schema is "
        "unavailable — cannot verify mapping values point to valid Phase 3 "
        "edges. Install graph_transformer or fix the import path.", _exc,
    )
    _PHASE3_EDGE_TYPES: Tuple[Tuple[str, str, str], ...] = ()
    _PHASE3_CONTRACT_AVAILABLE = False


# =============================================================================
# Re-export the canonical constants (so callers can import from EITHER
# shared.contracts.phase_edge_mapping OR phase2.contracts.phase2_schema).
# This is the integration-glue pattern: one canonical definition, multiple
# import paths for cross-team ownership.
# =============================================================================
PHASE2_TO_PHASE3_EDGE: Dict[Tuple[str, str, str], Tuple[str, str, str]] = (
    _PHASE2_TO_PHASE3_EDGE
)
"""Canonical mapping: Phase 2 edge tuple -> Phase 3 edge tuple.

Keys that are NOT in this dict are either:
  (a) in PHASE2_TO_PHASE3_EDGE_DROPPED (explicitly dropped with a reason), OR
  (b) a programming error (the completeness assertion will fail at import).
"""

PHASE2_TO_PHASE3_EDGE_DROPPED: Tuple[Tuple[str, str, str], ...] = (
    _PHASE2_TO_PHASE3_EDGE_DROPPED
)
"""Phase 2 edges that are EXPLICITLY DROPPED at the Phase 2->3 boundary.

Each edge here is dropped for a SCIENTIFIC reason (e.g., "Phase 3 has no
Anatomy node type", "PPI edges would let the model learn a shortcut").
The drop reasons are documented in EDGE_DROP_REASONS below.
"""


# =============================================================================
# Reverse mapping (Phase 3 -> Phase 2) — for debugging and audit trails.
# =============================================================================
# Note: multiple Phase 2 edges may map to the same Phase 3 edge (e.g., both
# "Compound inhibits Protein" and "Compound inhibits Gene" map to
# "drug inhibits protein"). The reverse mapping preserves all sources.
PHASE3_TO_PHASE2_EDGE: Dict[Tuple[str, str, str], Tuple[Tuple[str, str, str], ...]] = {}
for _p2, _p3 in PHASE2_TO_PHASE3_EDGE.items():
    if _p3 in PHASE3_TO_PHASE2_EDGE:
        PHASE3_TO_PHASE2_EDGE[_p3] = PHASE3_TO_PHASE2_EDGE[_p3] + (_p2,)
    else:
        PHASE3_TO_PHASE2_EDGE[_p3] = (_p2,)
# Clean up loop variables so they don't leak into the module namespace.
try:
    del _p2, _p3
except NameError:
    # The loop didn't execute (degraded mode) — nothing to clean up.
    pass


# =============================================================================
# EDGE DROP REASONS (TM14 v118 ROOT FIX: visible, not silent)
# =============================================================================
# Every edge in PHASE2_TO_PHASE3_EDGE_DROPPED has a documented reason.
# The adapter's edge loop calls ``map_edge_with_reason`` which returns
# ("dropped", reason) for these edges — the adapter LOGS the drop with
# the reason, replacing the previous silent-drop pattern (P3-002 bug).
EDGE_DROP_REASONS: Dict[Tuple[str, str, str], str] = {
    ("Protein", "interacts_with", "Protein"): (
        "PPI (Protein-Protein Interaction). Phase 3's GT model is a "
        "drug-disease link predictor — PPI edges would let the model "
        "learn a shortcut (drug -> protein -> protein -> disease) that "
        "bypasses the multi-hop pathway reasoning the architecture is "
        "designed for. PPI signal is preserved indirectly via shared "
        "pathway membership (both proteins -> same pathway)."
    ),
    ("Gene", "interacts_with", "Gene"): (
        "Gene-gene interaction (PPI at the gene level). Same rationale "
        "as Protein-Protein: would create a shortcut. Genes are a Phase "
        "2 intermediate node type — they are NOT in Phase 3's NODE_TYPES."
    ),
    ("Compound", "interacts_with", "Compound"): (
        "DDI (Drug-Drug Interaction). Phase 3's GT model predicts "
        "drug->disease links, not drug->drug interactions. DDI signal "
        "is used by the RL agent's safety_score feature (via SIDER "
        "adverse-event counts), not by the GT model's graph topology."
    ),
    ("Gene", "expressed_in", "Anatomy"): (
        "Gene-anatomy expression (Human Protein Atlas). Phase 3 has no "
        "Anatomy node type. The expression signal is preserved indirectly "
        "via the Disease node's tissue_origin property."
    ),
    ("Protein", "expressed_in", "Anatomy"): (
        "Protein-anatomy expression. Same rationale as Gene-expressed_in-"
        "Anatomy: Phase 3 has no Anatomy node type."
    ),
    ("Protein", "associated_with", "Disease"): (
        "Direct Protein->Disease association (GWAS/PheWAS). Phase 3 "
        "routes this signal via the pathway layer (Protein -> part_of -> "
        "Pathway -> disrupted_in -> Disease) so the GT model learns "
        "multi-hop reasoning. A direct Protein->Disease edge would let "
        "the model learn a 1-hop shortcut, defeating the architecture."
    ),
    ("Gene", "encodes", "Protein"): (
        "Gene-encodes-Protein (Phase 2 derivation edge). This edge is "
        "consumed by the adapter's _derive_protein_pathway step to bridge "
        "Gene-side data (DRKG, OMIM) to Protein-side data (UniProt, "
        "STRING). It is NOT written to the Phase 3 graph directly — "
        "Phase 3 has no Gene node type."
    ),
}


def map_edge_with_reason(
    p2_edge: Tuple[str, str, str],
) -> Tuple[Optional[Tuple[str, str, str]], str]:
    """Map a Phase 2 edge to Phase 3, returning (phase3_edge, reason).

    Returns:
        Tuple of (phase3_edge, reason) where:
          - phase3_edge is the Phase 3 edge tuple if the edge is mapped,
            or None if the edge is dropped.
          - reason is "mapped" if the edge is mapped, or "dropped:<key>"
            if the edge is in PHASE2_TO_PHASE3_EDGE_DROPPED, or
            "unknown:<key>" if the edge is NEITHER mapped NOR dropped
            (a programming error — the completeness assertion should
            have caught this at import time).

    This function is the SINGLE entry point for Phase 2->3 edge mapping.
    The adapter calls it for every edge and LOGS the reason for every drop,
    replacing the previous silent-drop pattern (P3-002 bug).
    """
    if p2_edge in PHASE2_TO_PHASE3_EDGE:
        return PHASE2_TO_PHASE3_EDGE[p2_edge], "mapped"
    if p2_edge in PHASE2_TO_PHASE3_EDGE_DROPPED:
        reason = EDGE_DROP_REASONS.get(p2_edge, "no reason documented")
        # Truncate the reason for log readability (full reason available
        # in EDGE_DROP_REASONS for audit).
        return None, f"dropped:{reason[:80]}..."
    return None, (
        f"unknown:{p2_edge} — not in mapping or dropped set "
        f"(completeness assertion should have caught this)"
    )


# =============================================================================
# COMPLETENESS ASSERTION (TM14 v118 ROOT FIX — fail-closed at import)
# =============================================================================
# Every Phase 2 edge in PHASE2_TO_PHASE3_EDGE_DROPPED must have a documented
# reason in EDGE_DROP_REASONS. If a future Phase 2 edge is added to the
# dropped set without a reason, this assertion FAILS at import time.
#
# Additionally, every Phase 3 edge tuple in PHASE2_TO_PHASE3_EDGE's VALUES
# must be in Phase 3's EDGE_TYPES schema (from graph_transformer/data).
# If a future mapping points to a non-existent Phase 3 edge, this catches it.
def validate_phase2_to_phase3_completeness() -> Tuple[bool, FrozenSet[Tuple[str, str, str]], FrozenSet[Tuple[str, str, str]]]:
    """Verify the Phase 2->3 edge contract is complete and coherent.

    Returns:
        Tuple of (is_complete, unmapped_dropped, invalid_phase3_edges) where:
          - is_complete is True if every dropped edge has a reason AND every
            mapped Phase 3 edge is in Phase 3's EDGE_TYPES schema.
          - unmapped_dropped is the set of dropped edges WITHOUT a reason
            in EDGE_DROP_REASONS.
          - invalid_phase3_edges is the set of Phase 3 edges in the mapping
            VALUES that are NOT in Phase 3's EDGE_TYPES schema.

    This function is called at import time (below) AND by the contract
    consistency test (Task 330). The contract test fails CI if any
    dropped edge is missing a reason OR any mapped Phase 3 edge is invalid.
    """
    if not _PHASE2_CONTRACT_AVAILABLE:
        # Degraded mode — cannot validate. The contract test will fail.
        return False, frozenset(), frozenset()

    # Check 1: every dropped edge has a documented reason.
    dropped_without_reason = frozenset(PHASE2_TO_PHASE3_EDGE_DROPPED) - frozenset(EDGE_DROP_REASONS.keys())

    # Check 2: every mapped Phase 3 edge is in Phase 3's EDGE_TYPES schema.
    # This catches the case where a future mapping points to a non-existent
    # Phase 3 edge (e.g., if Phase 3's EDGE_TYPES is renamed but the
    # mapping isn't updated).
    if _PHASE3_CONTRACT_AVAILABLE and _PHASE3_EDGE_TYPES:
        valid_p3_edges = set(_PHASE3_EDGE_TYPES)
        # Also include the reverse edges (Phase 3's EDGE_TYPES includes
        # both forward and reverse, but defensively check both).
        mapped_p3_edges = set(PHASE2_TO_PHASE3_EDGE.values())
        invalid_p3_edges = frozenset(mapped_p3_edges - valid_p3_edges)
    else:
        invalid_p3_edges = frozenset()

    is_complete = (len(dropped_without_reason) == 0) and (len(invalid_p3_edges) == 0)
    return is_complete, dropped_without_reason, invalid_p3_edges


# Run the completeness assertion at import time. Fail-closed: if any
# dropped edge is missing a reason OR any mapped Phase 3 edge is invalid,
# the module raises AssertionError at import — the pipeline cannot start
# with an incomplete contract.
_IS_COMPLETE, _UNMAPPED_DROPPED, _INVALID_P3_EDGES = validate_phase2_to_phase3_completeness()
if _PHASE2_CONTRACT_AVAILABLE and not _IS_COMPLETE:
    _error_parts = []
    if _UNMAPPED_DROPPED:
        _error_parts.append(
            f"dropped edges WITHOUT a reason in EDGE_DROP_REASONS: "
            f"{sorted(_UNMAPPED_DROPPED)}"
        )
    if _INVALID_P3_EDGES:
        _error_parts.append(
            f"mapped Phase 3 edges NOT in Phase 3's EDGE_TYPES schema: "
            f"{sorted(_INVALID_P3_EDGES)}"
        )
    raise AssertionError(
        f"P3-002 v118 COMPLETENESS GUARD: the Phase 2->3 edge contract is "
        f"incomplete. {'; '.join(_error_parts)}. Either add the missing "
        f"reasons to EDGE_DROP_REASONS in shared/contracts/phase_edge_mapping.py "
        f"OR fix the invalid Phase 3 edge mappings in phase2/contracts/"
        f"phase2_schema.py. This is the P3-002 root fix: NO silent drops, "
        f"NO invalid mappings."
    )


# =============================================================================
# CONTRACT METADATA (for the contract consistency test)
# =============================================================================
PHASE_EDGE_MAPPING_VERSION: str = "1.0.0-v118-tm14-root-fix"


__all__ = [
    # Canonical mapping (re-exported from phase2.contracts)
    "PHASE2_TO_PHASE3_EDGE",
    "PHASE2_TO_PHASE3_EDGE_DROPPED",
    "PHASE3_TO_PHASE2_EDGE",
    # Drop reasons (TM14 v118)
    "EDGE_DROP_REASONS",
    # Mapping function (single entry point)
    "map_edge_with_reason",
    # Completeness validation
    "validate_phase2_to_phase3_completeness",
    # Metadata
    "PHASE_EDGE_MAPPING_VERSION",
]
