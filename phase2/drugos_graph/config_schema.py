"""DrugOS Graph — Schema Configuration (extracted from config.py).

v108 ROOT FIX (ISSUE-P2-056): this module is the FIRST real extraction
from the 8400-line ``config.py`` monolith. The previous v107 "fix" only
DOCUMENTED the intended split and explicitly deferred execution to
"v2.1.0" — a surface-level fix. This module DOES the split: the schema
constants are MOVED here (not copied), and ``config.py`` imports them
back so ``from .config import CORE_NODE_TYPES`` continues to work.

The schema constants are leaf definitions (lists of strings / tuples)
with no dependencies on other config sections, so they can be extracted
cleanly without circular imports.

Consumers should prefer importing from this module directly:
    from .config_schema import CORE_NODE_TYPES, CORE_EDGE_TYPES
The legacy ``from .config import CORE_NODE_TYPES`` continues to work via
re-export in ``config.py``.
"""

from __future__ import annotations

from typing import Tuple

# ─── Core Node Types ─────────────────────────────────────────────────────────
#
# The 7 canonical node types in the DrugOS knowledge graph.
#
# v107 ROOT FIX (ISSUE-P2-040 / P2-053 invariant): this list is the
# AUTHORITATIVE schema. ``CORE_EDGE_TYPES`` (below) MUST only reference
# node types from this list (or from ``DRKG_NODE_TYPES`` for legacy
# DRKG edges). The ``kg_builder._validate_core_edge_types`` invariant
# check enforces this at import time.

CORE_NODE_TYPES = ["Compound", "Disease", "Gene", "Protein", "Pathway",
                   # FIX-F / C-16: DOCX Phase 2 spec mandates 5 node types
                   # (Drugs, Proteins, Pathways, Diseases, Clinical Outcomes).
                   # The bridge previously emitted only 4 (Compound, Protein,
                   # Gene, Disease) — ClinicalOutcome was missing entirely.
                   # phase1_bridge._load_clinical_outcomes() now derives
                   # ClinicalOutcome nodes from drugbank_indications.csv.
                   "ClinicalOutcome",
                   # Phase 0.3 (master_prompt D2.9 / D14.12) — SIDER uses
                   # MedDRA vocabulary for adverse events.
                   # v38 ROOT FIX (Phase 2 Issue #12): the previous code
                   # shipped BOTH "MedDRA_Term" (canonical, underscore) AND
                   # "Side Effect" (legacy, space) as "migration-period
                   # dual-write". Neo4j labels with spaces require backtick
                   # quoting (``:``Side Effect````) which is fragile and
                   # error-prone. The fix: standardize on "MedDRA_Term".
                   "MedDRA_Term"]  # "Side Effect" deprecated — see v38 fix

# Extended node types from DRKG (13 entity types)
# NOTE: 'Protein' is NOT in DRKG; DRKG uses 'Gene' for both gene and protein
# product. Protein nodes are added only when UniProt data is loaded.
# Fixes audit issue 3.2 — Added "Atc" and "Tax" DRKG node types
DRKG_NODE_TYPES = [
    "Compound", "Disease", "Gene", "Anatomy",
    "Pharmacologic Class", "Side Effect", "Symptom",
    "Pathway", "Biological Process", "Molecular Function",
    "Cellular Component", "Taxonomy", "Gene Expression",
    "Atc", "Tax",
    # Fixes audit issue 3.10 — MedDRA_Term for SIDER adverse events
    "MedDRA_Term",
]

# ─── Core Edge Types ─────────────────────────────────────────────────────────
#
# The core edge types in DrugOS (spec + Gene-encodes-Protein bridge)
#
# SCIENTIFIC CORRECTNESS FIXES (highest priority):
#   - Added ("Gene", "encodes", "Protein") — the biological bridge
#     between gene and protein product. Without this edge, the graph
#     is disconnected between Gene-side and Protein-side data.
#   - Added ("Compound", "targets", "Protein") — drug-target edges
#     from UniProt/ChEMBL/STITCH/STRING all use the Protein endpoint.
#   - Added ("Protein", "participates_in", "Pathway") — protein (not
#     gene) participates in pathways per Reactome / KEGG convention.
#   - Added ("Compound", "inhibits", "Protein") — issue 3.1: many
#     ChEMBL/DrugBank inhibition targets are proteins (not genes).
#   - Added ("Compound", "activates", "Protein") — issue 3.1: many
#     activation targets are proteins.
#   - Added ("Compound", "tested_for", "Disease") — issue 3.8:
#     clinical trial records use "tested for" rather than "treats"
#     for unapproved indications.
#   - Added ("Protein", "associated_with", "Disease") — issue 3.3:
#     GWAS and PheWAS associate PROTEINS (gene products) with diseases,
#     not genes directly.
#   - Added ("Compound", "causes_adverse_event", "MedDRA_Term") —
#     issue 3.10: SIDER uses MedDRA terms, not "Side Effect" generic.
#   - Added ("Protein", "expressed_in", "Anatomy") — issue 3.11:
#     protein expression (from HPA) is distinct from gene expression.
#   - Added ("Pathway", "associated_with", "Disease") — issue 3.9:
#     pathway-disease associations (e.g., from KEGG Disease).

CORE_EDGE_TYPES: list[Tuple[str, str, str]] = [
    # ── Original edges (backward compat) ──
    ("Compound", "treats", "Disease"),
    ("Compound", "inhibits", "Gene"),          # DRKG drug-gene inhibition
    ("Compound", "activates", "Gene"),          # DRKG drug-gene activation
    ("Compound", "targets", "Protein"),         # cross-database drug-protein
    ("Compound", "binds", "Protein"),           # physical binding (ChEMBL/STITCH)
    ("Gene", "encodes", "Protein"),             # gene -> protein product bridge
    ("Gene", "associated_with", "Disease"),     # DRKG gene-disease
    ("Gene", "interacts_with", "Gene"),         # PPI (DRKG uses Gene for both ends)
    ("Protein", "interacts_with", "Protein"),   # STRING PPI (UniProt accession IDs)
    ("Compound", "causes_side_effect", "Side Effect"),  # SIDER legacy
    ("Gene", "expressed_in", "Anatomy"),
    ("Gene", "participates_in", "Pathway"),
    ("Protein", "participates_in", "Pathway"),  # Reactome uses protein participants
    ("Pathway", "disrupted_in", "Disease"),     # spec edge from Phase 2 doc
    # ── New edges from scientific correctness audit ──
    ("Compound", "inhibits", "Protein"),        # ChEMBL/DrugBank protein targets
    ("Compound", "activates", "Protein"),       # ChEMBL/DrugBank protein targets
    ("Compound", "tested_for", "Disease"),
    # v102 ROOT FIX (P2-042): "failed_for" edge for clinical trials that
    # completed but FAILED their primary endpoint.
    ("Compound", "failed_for", "Disease"),
    ("Protein", "associated_with", "Disease"),
    ("Compound", "causes_adverse_event", "MedDRA_Term"),
    ("Protein", "expressed_in", "Anatomy"),
    ("Pathway", "associated_with", "Disease"),
    # ── DrugBank v2.0 audit-fix edge types ──
    ("Compound", "metabolized_by", "Protein"),
    ("Compound", "carried_by", "Protein"),
    ("Compound", "transported_by", "Protein"),
    ("Compound", "induces", "Protein"),
    ("Compound", "allosterically_modulates", "Protein"),
    ("Compound", "unknown", "Protein"),
    ("Compound", "interacts_with", "Compound"),
    # ── v15 ROOT FIX (REM-12): OMIM susceptibility vs causative GDA ──
    ("Gene", "susceptible_to", "Disease"),
    # ── FIX-F / C-16: ClinicalOutcome node + edge ──
    ("Compound", "has_clinical_outcome", "ClinicalOutcome"),
    # v107 ROOT FIX (ISSUE-P2-040): "validated_treats" was missing from
    # CORE_EDGE_TYPES — the data flywheel creates these edges.
    ("Drug", "validated_treats", "Disease"),
]

# Fixes audit issue 2.1 — CORE_EDGE_TYPES_SET for O(1) lookup
CORE_EDGE_TYPES_SET: frozenset[Tuple[str, str, str]] = frozenset(CORE_EDGE_TYPES)


__all__ = [
    "CORE_NODE_TYPES",
    "DRKG_NODE_TYPES",
    "CORE_EDGE_TYPES",
    "CORE_EDGE_TYPES_SET",
]
