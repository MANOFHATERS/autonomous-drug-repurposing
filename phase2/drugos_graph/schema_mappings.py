"""Single source of truth for Phase 2 <-> Phase 3 schema mappings.

INT-004 ROOT FIX: two INDEPENDENT node-type mappings existed:
  - pyg_builder._PHASE2_TO_GT_NODE_TYPE (7 entries: includes Gene, MedDRA_Term)
  - phase2_adapter.PHASE2_TO_PHASE3_NODE (5 entries: drops Gene, MedDRA_Term)

The two adapters produced DIFFERENT PyG HeteroData graphs from the same
Phase 2 source. Phase 3 training saw a different topology than Phase 2
service exposed — model trained on wrong graph.

This module provides ONE shared mapping dict imported by both adapters.
The canonical Phase 3 schema has exactly 5 node types; Gene and MedDRA_Term
are Phase 2 intermediates used for derivation only and intentionally dropped
AFTER their derivation work is complete.

All consumers MUST import from this module — never define a local mapping.
"""
from typing import Dict, Tuple

# ---------------------------------------------------------------------------
# Canonical Phase 2 -> Phase 3 node type mapping
# ---------------------------------------------------------------------------
# Phase 2 (capitalized)        -> Phase 3 (lowercase canonical)
# ---------------------------------------------------------------------------
# Compound                     -> drug
# Protein                      -> protein
# Pathway                      -> pathway
# Disease                      -> disease
# ClinicalOutcome              -> clinical_outcome
# Gene                         -> DROPPED (intermediate for pathway->disease derivation)
# MedDRA_Term                  -> DROPPED (folded into ClinicalOutcome)
# ---------------------------------------------------------------------------
PHASE2_TO_PHASE3_NODE: Dict[str, str] = {
    "Compound": "drug",
    "Protein": "protein",
    "Pathway": "pathway",
    "Disease": "disease",
    "ClinicalOutcome": "clinical_outcome",
}

# Reverse lookup: Phase 3 -> Phase 2 (many-to-one because Gene/MedDRA_Term
# map to nothing — they are dropped in the projection).
PHASE3_TO_PHASE2_NODE: Dict[str, str] = {
    v: k for k, v in PHASE2_TO_PHASE3_NODE.items()
}

# The FULL set of Phase 2 node types (including intermediates).
# Used by pyg_builder when reading Phase 2 source data.
ALL_PHASE2_NODE_TYPES: Tuple[str, ...] = (
    "Compound", "Protein", "Gene", "Pathway",
    "Disease", "ClinicalOutcome", "MedDRA_Term",
)

# The canonical set of Phase 3 node types.
# Used by phase2_adapter when producing the final graph.
ALL_PHASE3_NODE_TYPES: Tuple[str, ...] = (
    "drug", "protein", "pathway", "disease", "clinical_outcome",
)

# Phase 2 -> Phase 3 edge type mapping.
# Key: (src_label, rel_type, dst_label) in Phase 2 vocabulary.
# Value: (src_type, rel_type, tgt_type) in Phase 3 canonical vocabulary.
PHASE2_TO_PHASE3_EDGE: Dict[Tuple[str, str, str], Tuple[str, str, str]] = {
    # Direct drug->protein mechanism edges
    ("Compound", "inhibits", "Protein"): ("drug", "inhibits", "protein"),
    ("Compound", "activates", "Protein"): ("drug", "activates", "protein"),
    # Neutral binding edge
    ("Compound", "targets", "Protein"): ("drug", "binds", "protein"),
    # Neutral modulation edge
    ("Compound", "allosterically_modulates", "Protein"): ("drug", "modulates", "protein"),
    # Drug->disease therapeutic edges
    ("Compound", "treats", "Disease"): ("drug", "treats", "disease"),
    ("Compound", "tested_for", "Disease"): ("drug", "tested_for", "disease"),
    # Drug->clinical outcome edges
    ("Compound", "causes", "ClinicalOutcome"): ("drug", "causes", "clinical_outcome"),
    ("Compound", "has_clinical_outcome", "ClinicalOutcome"): (
        "drug", "causes", "clinical_outcome",
    ),
    # Protein->pathway edges (both relation names accepted)
    ("Protein", "participates_in", "Pathway"): (
        "protein", "part_of", "pathway",
    ),
    ("Protein", "part_of", "Pathway"): (
        "protein", "part_of", "pathway",
    ),
    # Derived pathway->disease edges (if Phase 2 produces them directly)
    ("Pathway", "disrupted_in", "Disease"): (
        "pathway", "disrupted_in", "disease",
    ),
}

# Reverse lookup for edge types.
PHASE3_TO_PHASE2_EDGE: Dict[Tuple[str, str, str], Tuple[str, str, str]] = {
    v: k for k, v in PHASE2_TO_PHASE3_EDGE.items()
}


def is_phase2_intermediate_dropped(node_type: str) -> bool:
    """Return True if a Phase 2 node type is intentionally dropped in Phase 3.

    Gene and MedDRA_Term are Phase 2 intermediates used for derivation
    (e.g., pathway->disease edges are derived from Gene->Disease
    associations via Gene->Protein->Pathway mapping) but do NOT appear
    as node types in the Phase 3 canonical schema.
    """
    return node_type in ("Gene", "MedDRA_Term")
