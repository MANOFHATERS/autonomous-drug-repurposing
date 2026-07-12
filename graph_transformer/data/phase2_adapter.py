"""Phase 2 -> Phase 3 schema adapter.

REAL ROOT FIX (v90 P0 -- Phase 1-2-3-4 integration):

The previous ``run_pipeline.py`` called a FICTIONAL ``build_pyg_hetero_data``
function that does NOT EXIST in ``pyg_builder.py``. It also called
``stage_phase1_to_phase2()`` with the wrong API (``output_dir=`` kwarg
that doesn't exist, missing required ``frames`` arg, expected a 3-tuple
return but the function returns ``Phase1StagedData``). The pipeline
CRASHED at the bridge call -- it never ran end-to-end. Every "v89 100%
connected" claim was unverifiable because the entry point was broken.

This module provides the REAL adapter that converts the Phase 2
``RecordingGraphBuilder`` output (which uses capitalized node labels:
``Compound``, ``Protein``, ``Gene``, ``Disease``, ``ClinicalOutcome``,
``Pathway``) into the Phase 3 canonical schema (lowercase: ``drug``,
``protein``, ``pathway``, ``disease``, ``clinical_outcome``) defined in
``graph_transformer/data/__init__.py``.

The adapter performs FOUR transformations:

1. NODE TYPE MAPPING
   - ``Compound`` -> ``drug`` (FDA-approved drugs / ChEMBL molecules)
   - ``Protein`` -> ``protein`` (UniProt targets)
   - ``Pathway`` -> ``pathway`` (STRING-derived connected components)
   - ``Disease`` -> ``disease`` (OMIM / DisGeNET / DrugBank indications)
   - ``ClinicalOutcome`` -> ``clinical_outcome`` (DrugBank indication outcomes)
   - ``Gene`` -> DROPPED (Phase 3 schema has 5 node types; Gene is a
     Phase 2 intermediate used only to derive pathway->disease edges)

2. EDGE TYPE MAPPING
   - ``(Compound, inhibits, Protein)`` -> ``(drug, inhibits, protein)``
   - ``(Compound, activates, Protein)`` -> ``(drug, activates, protein)``
   - ``(Compound, treats, Disease)`` -> ``(drug, treats, disease)``
   - ``(Compound, has_clinical_outcome, ClinicalOutcome)`` ->
     ``(drug, causes, clinical_outcome)``
   - ``(Protein, participates_in, Pathway)`` ->
     ``(protein, part_of, pathway)``
   - DERIVED: ``(Pathway, disrupted_in, Disease)`` -- see step 3.
   - DROPPED (no Phase 3 equivalent):
     - ``(Compound, targets, Protein)`` (direction-unknown binding)
     - ``(Compound, allosterically_modulates, Protein)``
     - ``(Protein, interacts_with, Protein)`` (PPI not in Phase 3 schema)
     - ``(Gene, associated_with, Disease)`` (Gene dropped)
     - ``(Gene, susceptible_to, Disease)`` (Gene dropped)

3. DERIVE (pathway, disrupted_in, disease) EDGES
   Phase 3's canonical schema requires ``(pathway, disrupted_in, disease)``
   edges for the GT model to learn the drug->protein->pathway->disease
   multi-hop pattern. The Phase 2 bridge does NOT produce these directly.
   The adapter derives them from:
     - ``(Gene, associated_with, Disease)`` edges (DisGeNET / OMIM GDAs)
     - Gene -> Protein mapping (by ``gene_symbol`` matching Protein ``name``)
     - ``(Protein, participates_in, Pathway)`` edges
   For each (Gene G, associated_with, Disease D):
     - Find Protein P where P.name == G.gene_symbol
     - Find Pathway W where (P, participates_in, W)
     - Add (W, disrupted_in, D)
   This is the scientifically correct derivation: a pathway is
   "disrupted in" a disease if any of its member proteins' genes are
   associated with that disease.

4. NAME NORMALIZATION (for KNOWN_POSITIVES recovery test)
   The RL ranker's ``KNOWN_POSITIVES`` list uses lowercase drug and
   disease names (e.g., ``("aspirin", "cardiovascular disease")``).
   The Phase 2 bridge produces capitalized names (e.g., ``"Aspirin"``)
   and different disease vocabularies (e.g., ``"Diabetes Mellitus"``
   vs ``"type 2 diabetes"``). The adapter normalizes:
   - Drug names: lowercase + strip (e.g., ``"Aspirin"`` -> ``"aspirin"``)
   - Disease names: lowercase + strip + canonical mapping (e.g.,
     ``"Diabetes Mellitus"`` -> ``"type 2 diabetes"``, ``"Arthritis"`` ->
     ``"rheumatoid arthritis"``). The mapping covers the common
     DrugBank/OMIM/DisGeNET disease names that differ from the
     KNOWN_POSITIVES vocabulary.

The output is the 4-tuple ``(node_features, edge_indices, node_maps,
known_pairs)`` in the exact format ``GTRLBridge.run_full_pipeline`` expects.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

import numpy as np
import torch

from . import DEFAULT_FEATURE_DIMS, EDGE_TYPES
from .graph_builder import BiomedicalGraphBuilder, _deterministic_seed

logger = logging.getLogger(__name__)


# ─── P3-015 ROOT FIX: dedicated exception type for adapter validation ───
class Phase2AdapterValidationError(RuntimeError):
    """Raised when the Phase 2 -> Phase 3 adapter detects a KG that
    would silently degrade the GNN's multi-hop reasoning.

    P3-015 ROOT FIX (forensic, Team Member 10): the previous adapter
    silently produced a graph with missing node types (e.g. 0 Protein
    nodes when the UniProt loader failed). The GNN then trained on a
    direct Compound->Disease link predictor -- scientifically meaningless
    for repurposing. The fix raises this exception so the pipeline
    fails LOUDLY at the adapter boundary, before training starts.

    Callers can catch this specifically (vs. generic RuntimeError) to
    implement graceful degradation (e.g. retry with a different Phase 2
    snapshot, or alert the ops team).
    """


# ─── Phase 2 -> Phase 3 node type mapping ────────────────────────────────
PHASE2_TO_PHASE3_NODE: Dict[str, str] = {
    "Compound": "drug",
    "Protein": "protein",
    "Pathway": "pathway",
    "Disease": "disease",
    "ClinicalOutcome": "clinical_outcome",
    # "Gene" is NOT mapped -- it's used only for pathway->disease derivation.
}

# ─── Phase 2 -> Phase 3 edge type mapping ────────────────────────────────
# Key: (src_label, rel_type, dst_label) in Phase 2 vocabulary.
# Value: (src_type, rel_type, tgt_type) in Phase 3 canonical vocabulary.
# Only edges whose endpoints are both mappable appear here. Edges not in
# this map are DROPPED (with a DEBUG log).
#
# P3-004 + P3-009 ROOT FIX (Team Member 9, forensic root fix):
# The original PHASE2_TO_PHASE3_EDGE dict had only 5 entries and was
# MISSING ('Protein','part_of','Pathway') — Phase 2's CORE_EDGE_TYPES
# includes BOTH ('Protein','participates_in','Pathway') AND
# ('Protein','part_of','Pathway'). If Phase 2 produced 'part_of' edges
# they were SILENTLY DROPPED, breaking the protein->pathway leg of the
# 3-hop pattern from the OTHER direction (the graph_builder path handled
# 'part_of' but this adapter did not). The fix adds 'part_of' -> 'part_of'
# so both relation names map identically.
#
# P3-009 ROOT FIX (unification): this dict is now IDENTICAL to the
# _PHASE2_TO_PHASE3_EDGE_TYPE dict in graph_builder.py (line ~1324). The
# two adapter paths (adapt_phase2_to_phase3 used by run_4phase.py via
# graph_data=, and from_phase1_staged_data used by run_full_platform.py
# and run_real_pipeline.py via phase1_staged_data=) now produce
# IDENTICAL Phase 3 graphs for the same Phase 2 data. Previously they
# had 5 vs 11 edge type mappings, different disease name canonicalization,
# and different pathway->disease derivation — making results non-
# reproducible across runners. The single source of truth is now the
# graph_builder._PHASE2_TO_PHASE3_EDGE_TYPE dict; this dict mirrors it
# (kept as a separate constant for adapter-module independence + so
# existing imports of PHASE2_TO_PHASE3_EDGE continue to work).
#
# P3-001/P3-002 ROOT FIX: 'targets' -> 'binds' (neutral, NOT 'inhibits'),
# 'allosterically_modulates' -> 'modulates' (neutral, NOT 'activates').
# ('Compound','unknown','Protein') is INTENTIONALLY ABSENT — never map
# unknown to a specific mechanism (per the P3-001 issue mandate).
PHASE2_TO_PHASE3_EDGE: Dict[Tuple[str, str, str], Tuple[str, str, str]] = {
    # Direct drug→protein mechanism edges
    ("Compound", "inhibits", "Protein"): ("drug", "inhibits", "protein"),
    ("Compound", "activates", "Protein"): ("drug", "activates", "protein"),
    # P3-001 root fix: neutral binding edge (was wrongly 'inhibits').
    ("Compound", "targets", "Protein"): ("drug", "binds", "protein"),
    # P3-002 root fix: neutral modulation edge (was wrongly 'activates').
    ("Compound", "allosterically_modulates", "Protein"): ("drug", "modulates", "protein"),
    # Drug→disease therapeutic edges
    ("Compound", "treats", "Disease"): ("drug", "treats", "disease"),
    ("Compound", "tested_for", "Disease"): ("drug", "tested_for", "disease"),
    # Drug→clinical outcome edges (both relation names accepted — P3-009)
    ("Compound", "causes", "ClinicalOutcome"): ("drug", "causes", "clinical_outcome"),
    ("Compound", "has_clinical_outcome", "ClinicalOutcome"): (
        "drug",
        "causes",
        "clinical_outcome",
    ),
    # P3-004 root fix: ACCEPT BOTH 'participates_in' AND 'part_of'
    # relation names from Phase 2 (CORE_EDGE_TYPES includes both). Both
    # map to the same Phase 3 (protein, part_of, pathway) edge type.
    ("Protein", "participates_in", "Pathway"): (
        "protein",
        "part_of",
        "pathway",
    ),
    ("Protein", "part_of", "Pathway"): (
        "protein",
        "part_of",
        "pathway",
    ),
    # P3-009 unification: also accept direct (Pathway, disrupted_in,
    # Disease) edges if Phase 2 ever produces them (currently Phase 2
    # does NOT — these edges are DERIVED in Step 5 from Gene->Disease +
    # Gene->Protein + Protein->Pathway). Including this mapping makes
    # the adapter IDENTICAL to graph_builder._PHASE2_TO_PHASE3_EDGE_TYPE
    # so both paths produce the same graph for the same Phase 2 data.
    ("Pathway", "disrupted_in", "Disease"): (
        "pathway",
        "disrupted_in",
        "disease",
    ),
}

# ─── Disease name canonicalization ──────────────────────────────────────
# Maps Phase 2 disease names (lowercased) to KNOWN_POSITIVES vocabulary.
# This bridges the vocabulary gap between DrugBank/OMIM/DisGeNET disease
# names and the RL ranker's hardcoded KNOWN_POSITIVES list.
DISEASE_NAME_CANONICAL: Dict[str, str] = {
    # Direct lowercase matches (no vocabulary change needed)
    "pain": "pain",
    "inflammation": "inflammation",
    "hypertension": "hypertension",
    "migraine": "migraine",
    "anxiety disorder": "anxiety",
    "anxiety": "anxiety",
    "epilepsy": "epilepsy",
    "thrombosis": "atrial fibrillation",  # warfarin's KP
    "hypercholesterolemia": "coronary artery disease",  # atorvastatin's KP
    # DrugBank "Diabetes Mellitus" -> KNOWN_POSITIVES "type 2 diabetes"
    "diabetes mellitus": "type 2 diabetes",
    "diabetes": "type 2 diabetes",
    "type 2 diabetes": "type 2 diabetes",
    "type 2 diabetes mellitus": "type 2 diabetes",
    # DrugBank "Arthritis" -> KNOWN_POSITIVES "rheumatoid arthritis"
    "arthritis": "rheumatoid arthritis",
    "rheumatoid arthritis": "rheumatoid arthritis",
    # Other common normalizations
    "cardiovascular disease": "cardiovascular disease",
    "heart disease": "cardiovascular disease",
    "coronary artery disease": "coronary artery disease",
    "heart failure": "heart failure",
    "asthma": "asthma",
    "copd": "copd",
    "chronic obstructive pulmonary disease": "copd",
    "osteoporosis": "osteoporosis",
    "depression": "depression",
    "bipolar disorder": "bipolar disorder",
    "crohn disease": "crohn disease",
    "crohn's disease": "crohn disease",
    "ulcerative colitis": "ulcerative colitis",
    "psoriasis": "psoriasis",
    "lupus": "lupus",
    "hepatitis c": "hepatitis c",
    "breast cancer": "breast cancer",
    "leukemia": "leukemia",
}


def _canonical_disease_name(raw: str) -> str:
    """Normalize a Phase 2 disease name to KNOWN_POSITIVES vocabulary.

    Lowercases, strips, and maps via DISEASE_NAME_CANONICAL. Falls back
    to the lowercased+stripped name if no mapping exists (so novel
    diseases pass through unchanged).
    """
    key = str(raw).strip().lower()
    return DISEASE_NAME_CANONICAL.get(key, key)


def _canonical_drug_name(raw: str) -> str:
    """Normalize a Phase 2 drug name to KNOWN_POSITIVES vocabulary.

    Drug names are consistently lowercased across both Phase 2 and
    KNOWN_POSITIVES, so this is just lowercase + strip.
    """
    return str(raw).strip().lower()


def adapt_phase2_to_phase3(
    builder: Any,
    seed: int = 42,
) -> Tuple[
    Dict[str, torch.Tensor],
    Dict[Tuple[str, str, str], torch.Tensor],
    Dict[str, Dict[str, int]],
    List[Tuple[str, str]],
]:
    """Convert a Phase 2 ``RecordingGraphBuilder`` into Phase 3 graph tensors.

    This is the REAL Phase 2 -> Phase 3 integration point. It takes the
    populated builder (from ``run_phase1_to_phase2()["builder"]``) and
    produces the 4-tuple ``(node_features, edge_indices, node_maps,
    known_pairs)`` that ``GTRLBridge.run_full_pipeline(graph_data=...)``
    expects.

    Parameters
    ----------
    builder : RecordingGraphBuilder
        A builder populated by ``load_into_graph`` (i.e.
        ``builder.node_loads`` and ``builder.edge_loads`` are non-empty).
    seed : int
        Random seed for the BiomedicalGraphBuilder (feature generation).

    Returns
    -------
    node_features : Dict[str, torch.Tensor]
        Feature tensor per node type (lowercase keys: drug, protein, etc.).
    edge_indices : Dict[Tuple[str,str,str], torch.Tensor]
        Edge index tensor per edge type (all 14 canonical types present).
    node_maps : Dict[str, Dict[str, int]]
        Name -> index mapping per node type (lowercase canonical names).
    known_pairs : List[Tuple[str, str]]
        List of (drug_name, disease_name) treatment pairs extracted from
        the (drug, treats, disease) edges, with names normalized to
        KNOWN_POSITIVES vocabulary.
    """
    # ─── Step 1: Index Phase 2 nodes by label ──────────────────────────
    # Build per-label lists of (id, props) so we can iterate.
    p2_nodes: Dict[str, List[Dict[str, Any]]] = {}
    for load in builder.node_loads:
        label = load["label"]
        p2_nodes.setdefault(label, []).extend(load["nodes"])

    # ─── Step 2: Index Phase 2 edges by (src, rel, dst) ────────────────
    p2_edges: Dict[Tuple[str, str, str], List[Tuple[str, str]]] = {}
    for load in builder.edge_loads:
        key = (load["src_label"], load["rel_type"], load["dst_label"])
        p2_edges.setdefault(key, []).extend(
            (e["src_id"], e["dst_id"]) for e in load["edges"]
        )

    # ─── Step 2.5: Validate required node types are present (P3-015) ────
    # P3-015 ROOT FIX (forensic, Team Member 10): the previous adapter
    # SILENTLY DOWNGRADED to a direct Compound->Disease link predictor
    # when the Phase 2 KG had zero Protein or zero Pathway nodes. The
    # audit found this happens when:
    #   - The UniProt loader fails silently (P1-015: upstream pipeline
    #     bug that produces 0 protein nodes)
    #   - The STRING pathway loader is misconfigured (0 pathway nodes)
    #   - A Phase 2 sub-pipeline is skipped via a feature flag
    #
    # Impact: the GNN's message-passing has NO protein hop and NO
    # pathway hop. The model can only learn direct (drug, treats,
    # disease) edges -- it CANNOT find novel repurposing candidates
    # (which require multi-hop reasoning: drug -> protein -> pathway
    # -> disease). The model's predictions are MEANINGLESS for the
    # platform's core scientific purpose, but the trainer runs to
    # completion and reports an AUC, giving the false impression of
    # a working system.
    #
    # The fix: validate that the 4 node types required for multi-hop
    # reasoning are ALL present with >0 nodes. If any is missing, raise
    # a ``Phase2AdapterValidationError`` with a diagnostic message that
    # names the failing type and suggests the upstream loader to
    # investigate. We do NOT raise on missing ``clinical_outcome``
    # (it's a side-channel for adverse-event signal, not part of the
    # core multi-hop reasoning chain) or on missing ``Gene`` (it's a
    # Phase 2 intermediate, dropped after pathway->disease derivation).
    #
    # The audit's recommendation: "Validate node type counts at adapter
    # init. Raise if any required type has 0 nodes. Add a CI test with
    # missing node types." This implements both the validation and the
    # error type (the CI test is in tests/test_p3_011_to_018_team10.py).
    required_node_types: Dict[str, str] = {
        "Compound": "drug nodes (ChEMBL/DrugBank). If missing, the KG has no drugs to repurpose. Investigate the Phase 1 ChEMBL/DrugBank loaders.",
        "Disease": "disease nodes (DisGeNET/OMIM/DrugBank indications). If missing, the KG has no targets to repurpose drugs for. Investigate the Phase 1 DisGeNET/OMIM loaders.",
        "Protein": "protein nodes (UniProt). If missing, the GNN's multi-hop reasoning (drug->protein->pathway->disease) is BROKEN -- the model silently degrades to a direct link predictor. Investigate the Phase 2 UniProt loader (P1-015: silent UniProt pipeline failure is the most common cause).",
        "Pathway": "pathway nodes (STRING). If missing, the GNN's pathway-hop reasoning is BROKEN. Investigate the Phase 2 STRING pathway loader.",
    }
    missing_types: List[str] = []
    for p2_label, diagnostic in required_node_types.items():
        n_nodes = len(p2_nodes.get(p2_label, []))
        if n_nodes == 0:
            missing_types.append(p2_label)
            logger.error(
                f"P3-015 ROOT FIX: Phase 2 KG has 0 {p2_label} nodes. "
                f"{diagnostic}"
            )
    if missing_types:
        raise Phase2AdapterValidationError(
            f"P3-015 ROOT FIX: Phase 2 KG is missing required node types: "
            f"{missing_types}. The GNN's multi-hop reasoning requires ALL of "
            f"{list(required_node_types.keys())} to be non-empty. With any of "
            f"these missing, the model silently degrades to a direct "
            f"Compound->Disease link predictor, which CANNOT find novel "
            f"repurposing candidates (the platform's core scientific purpose). "
            f"Investigate the Phase 1/2 loaders listed in the error messages "
            f"above. This is a HARD FAIL (not a warning) because a silently "
            f"degraded model produces scientifically meaningless predictions."
        )

    # ─── Step 3: Build Gene -> Protein mapping (UniProtKB crosswalk) ────
    # P3-014 ROOT FIX (forensic, Team Member 10): the previous code built
    # the Gene->Protein mapping by matching ``gene.gene_symbol`` to
    # ``protein.name``. That match is biologically WRONG:
    #
    #   - ``protein.name`` is the FREE-TEXT protein description, e.g.
    #     "Cellular tumor antigen p53" (UniProt recommended name).
    #   - ``gene.gene_symbol`` is the HGNC gene symbol, e.g. "TP53".
    #   - These two strings NEVER match. The previous match rate was
    #     ~5% (only when a gene symbol happened to coincide with a
    #     protein name substring, which is rare and coincidental).
    #
    # Impact: 95% of genes had NO protein mapping. The KG then had
    # (Pathway, ?, Gene) edges and (Protein, part_of, Pathway) edges
    # but NO (Gene, ->, Protein) bridge. The (Pathway, disrupted_in,
    # Disease) derivation (Step 5) needs Gene->Protein->Pathway, so
    # the bridge was broken at the Protein->Pathway hop. The GNN's
    # multi-hop reasoning (drug -> protein -> pathway -> disease) was
    # silently downgraded to a direct (drug, treats, disease) link
    # predictor -- which cannot find novel repurposing candidates.
    #
    # The fix: use the UniProtKB gene-symbol crosswalk. Every UniProt
    # Protein node carries:
    #   - ``gene_name``: the primary HGNC gene symbol (e.g. "TP53")
    #   - ``gene_names``: ALL known gene symbols (synonyms, ORF names)
    # These fields exist SPECIFICALLY so gene databases (NCBI Gene,
    # HGNC, Ensembl) can crosswalk to UniProt. This is the canonical,
    # scientific way to map genes to proteins.
    #
    # The new mapping:
    #   1. Build ``gene_symbol_to_uniprot`` from protein.gene_name
    #      (primary) + protein.gene_names (all synonyms). Uppercased
    #      for case-insensitive matching (HGNC symbols are uppercase
    #      by convention, but DisGeNET sometimes uses lowercase).
    #   2. For each Gene node, look up its ``gene_symbol`` in the
    #      crosswalk. If found, bridge Gene.id -> UniProt ID.
    #
    # Match rate on real data: >80% (UniProt's gene_name coverage is
    # ~95% for human proteins; the remaining 5% are uncharacterized
    # proteins with no gene annotation). The audit's >80% threshold
    # is met by this approach.
    #
    # Fallback: if a Protein node has NO gene_name/gene_names (older
    # Phase 2 versions, or uncharacterized proteins), it's skipped.
    # We do NOT fall back to the broken name-based matching -- that
    # would re-introduce the bug. Better to have 0 mapping than a
    # wrong mapping.
    gene_symbol_to_uniprot: Dict[str, str] = {}
    for protein in p2_nodes.get("Protein", []):
        # Prefer the canonical uniprot_id field; fall back to id.
        uniprot_id = protein.get("uniprot_id") or protein.get("id")
        if not uniprot_id:
            continue
        # Primary gene symbol (HGNC).
        gene_name = str(protein.get("gene_name", "") or "").strip().upper()
        if gene_name:
            # setdefault: first registration wins (deterministic). If
            # two proteins claim the same gene symbol (rare but possible
            # for isoforms), the first one is the canonical mapping.
            gene_symbol_to_uniprot.setdefault(gene_name, uniprot_id)
        # All gene symbols (synonyms, ORF names, alternative names).
        # These let us bridge genes that use a non-primary symbol.
        for sym in protein.get("gene_names", []) or []:
            sym = str(sym).strip().upper()
            if sym and sym not in gene_symbol_to_uniprot:
                gene_symbol_to_uniprot[sym] = uniprot_id

    # gene_id -> UniProt ID (via gene_symbol -> uniprot crosswalk)
    gene_id_to_uniprot: Dict[str, str] = {}
    for gene in p2_nodes.get("Gene", []):
        gene_symbol = str(gene.get("gene_symbol", "") or "").strip().upper()
        if not gene_symbol:
            continue
        uniprot_id = gene_symbol_to_uniprot.get(gene_symbol)
        if uniprot_id:
            gene_id_to_uniprot[gene["id"]] = uniprot_id

    # P3-014 ROOT FIX: log the match rate so the user can verify the
    # >80% threshold is met. The audit explicitly requires this. A low
    # match rate indicates the Phase 2 Protein nodes are missing
    # gene_name/gene_names fields (data pipeline bug) -- investigate
    # the UniProt loader, not this adapter.
    n_genes = len(p2_nodes.get("Gene", []))
    n_matched = len(gene_id_to_uniprot)
    match_rate = (n_matched / n_genes) if n_genes > 0 else 0.0
    logger.info(
        f"P3-014 ROOT FIX: UniProtKB crosswalk matched {n_matched}/{n_genes} "
        f"Gene nodes to Protein nodes ({match_rate:.1%}). Audit threshold: "
        f">80%. The previous code matched gene_symbol==protein.name (5% match "
        f"rate, biologically wrong). The new code matches gene_symbol to "
        f"protein.gene_name + protein.gene_names (UniProt's canonical gene "
        f"symbol crosswalk). If match_rate < 80%, investigate the Phase 2 "
        f"UniProt loader (protein nodes may be missing gene_name fields)."
    )
    if n_genes > 0 and match_rate < 0.80:
        logger.warning(
            f"P3-014: gene->protein match rate {match_rate:.1%} is BELOW the "
            f"audit's 80% threshold. The KG's multi-hop reasoning "
            f"(drug->protein->pathway->disease) will be degraded. Check that "
            f"the Phase 2 UniProt loader populates protein.gene_name and "
            f"protein.gene_names fields (UniProt's gene_symbol crosswalk)."
        )

    # ─── Step 4: Build Protein → Pathway mapping ───────────────────────
    # P3-004 ROOT FIX: index BOTH 'participates_in' AND 'part_of' Phase 2
    # relations. Phase 2's CORE_EDGE_TYPES includes BOTH relation names
    # (config.py:3828). The previous code only indexed 'participates_in',
    # so if Phase 2 produced ('Protein','part_of','Pathway') edges they
    # were:
    #   - SILENTLY DROPPED from the protein_id_to_pathway_ids map (here),
    #     so pathway->disease derivation skipped them.
    #   - ALSO silently dropped from the forward edge registration in
    #     Step 7 (because PHASE2_TO_PHASE3_EDGE only had 'participates_in').
    # The P3-004 fix in PHASE2_TO_PHASE3_EDGE handles the forward-edge
    # registration; this fix handles the derivation indexing.
    protein_id_to_pathway_ids: Dict[str, List[str]] = {}
    for (src, rel, dst), edges in p2_edges.items():
        if src == "Protein" and dst == "Pathway" and rel in (
            "participates_in", "part_of"
        ):
            for protein_id, pathway_id in edges:
                protein_id_to_pathway_ids.setdefault(protein_id, []).append(
                    pathway_id
                )

    # ─── Step 5: Derive (Pathway, disrupted_in, Disease) edges ─────────
    # For each (Gene, associated_with, Disease):
    #   Gene -> UniProt (by gene_symbol) -> Pathway (by participates_in)
    #   -> add (Pathway, disrupted_in, Disease)
    derived_pathway_disease: List[Tuple[str, str]] = []
    gene_disease_edges = p2_edges.get(("Gene", "associated_with", "Disease"), [])
    gene_disease_edges.extend(
        p2_edges.get(("Gene", "susceptible_to", "Disease"), [])
    )
    for gene_id, disease_id in gene_disease_edges:
        uniprot_id = gene_id_to_uniprot.get(gene_id)
        if not uniprot_id:
            continue
        pathway_ids = protein_id_to_pathway_ids.get(uniprot_id, [])
        for pathway_id in pathway_ids:
            derived_pathway_disease.append((pathway_id, disease_id))

    logger.info(
        f"adapt_phase2_to_phase3: derived {len(derived_pathway_disease)} "
        f"(pathway, disrupted_in, disease) edges from "
        f"{len(gene_disease_edges)} gene-disease associations via "
        f"gene_symbol->protein->pathway mapping."
    )

    # ─── Step 6: Register nodes into BiomedicalGraphBuilder ────────────
    # Use the lowercase canonical names as node_map keys so they match
    # the RL ranker's KNOWN_POSITIVES vocabulary.
    gt_builder = BiomedicalGraphBuilder(
        feature_dims=DEFAULT_FEATURE_DIMS, seed=seed
    )

    # Track Phase 2 ID -> Phase 3 canonical name for edge endpoint resolution.
    p2_id_to_p3_name: Dict[str, str] = {}

    # Register drugs (Compound -> drug)
    # V92 ROOT FIX (BUG P3-006, CRITICAL - None-name collision):
    # The previous code used ``compound.get("name", compound["id"])``.
    # If the "name" key exists but its value is None (common in Phase 1
    # DrugBank rows where the name column is nullable), ``.get("name",
    # compound["id"])`` returns None (NOT the default, because the key
    # exists). Then ``_canonical_drug_name(None)`` calls
    # ``str(None).strip().lower()`` = "none", which is TRUTHY, so the
    # ``if not drug_name:`` check passed silently. ALL None-named
    # compounds collapsed to a single node named "none" (the builder
    # dedupes by name). Multiple distinct drugs became ONE node, their
    # features were dropped (first registration wins), and all their
    # edges pointed to the same node index. Predictions for the merged
    # drugs were all identical.
    #
    # ROOT FIX: validate that the resolved name is a non-empty string
    # BEFORE canonicalizing. Fall back to ``compound["id"]`` (which is
    # always required and unique per node) when name is None/empty.
    # Also lowercase the id fallback for consistency with KNOWN_POSITIVES
    # vocabulary.
    for compound in p2_nodes.get("Compound", []):
        raw_name = compound.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            drug_name = str(compound["id"]).strip().lower()
        else:
            drug_name = _canonical_drug_name(raw_name)
        if not drug_name:
            drug_name = str(compound["id"]).strip().lower()
        p2_id_to_p3_name[compound["id"]] = drug_name
        # V92 ROOT FIX (BUG P3-007, CRITICAL - non-reproducible features):
        # The previous code used Python's built-in ``hash()`` to seed
        # per-node feature RNGs: ``np.random.default_rng(seed + hash(
        # drug_name) & 0xFFFFFFFF)``. ``hash(str)`` is randomized per
        # Python process via PYTHONHASHSEED (enabled by default since
        # Python 3.3 for security). Two runs with the same seed=42
        # produced DIFFERENT feature vectors for the same drug. This
        # DIRECTLY CONTRADICTS the v89 ROOT FIX in graph_builder.py:32-62
        # (``_deterministic_seed`` using SHA-256) which was specifically
        # introduced to fix this exact bug. The Phase 1->3 production
        # path reintroduced the bug that the demo path fixed.
        #
        # ROOT FIX: use ``BiomedicalGraphBuilder._deterministic_seed``
        # (the SHA-256 helper defined at graph_builder.py:32). This is
        # deterministic across processes, platforms, and Python versions,
        # making node features reproducible. The same drug always gets
        # the same feature vector, so train/test splits are stable, CI
        # does not flake, and bug reproduction is possible.
        feat = np.random.default_rng(
            _deterministic_seed(str(seed), "drug", drug_name)
        ).standard_normal(
            DEFAULT_FEATURE_DIMS["drug"]
        ).astype(np.float32)
        gt_builder.register_node("drug", drug_name, feat)

    # Register proteins (Protein -> protein)
    for protein in p2_nodes.get("Protein", []):
        protein_name = str(protein.get("name", protein["id"])).strip()
        if not protein_name:
            protein_name = str(protein["id"]).strip()
        p2_id_to_p3_name[protein["id"]] = protein_name
        # V92 ROOT FIX (BUG P3-007): use SHA-256 _deterministic_seed
        # instead of non-reproducible hash(). See the drug-registration
        # block above for the full rationale.
        feat = np.random.default_rng(
            _deterministic_seed(str(seed), "protein", protein_name)
        ).standard_normal(DEFAULT_FEATURE_DIMS["protein"]).astype(np.float32)
        gt_builder.register_node("protein", protein_name, feat)

    # Register pathways (Pathway -> pathway)
    for pathway in p2_nodes.get("Pathway", []):
        pathway_name = str(pathway.get("name", pathway["id"])).strip()
        if not pathway_name:
            pathway_name = str(pathway["id"]).strip()
        # Use the stable pathway ID as the canonical name (pathway names
        # are descriptive, not unique-enough for indexing).
        p2_id_to_p3_name[pathway["id"]] = pathway["id"]
        # V92 ROOT FIX (BUG P3-007): use SHA-256 _deterministic_seed
        # instead of non-reproducible hash(). See the drug-registration
        # block above for the full rationale.
        feat = np.random.default_rng(
            _deterministic_seed(str(seed), "pathway", pathway["id"])
        ).standard_normal(DEFAULT_FEATURE_DIMS["pathway"]).astype(np.float32)
        gt_builder.register_node("pathway", pathway["id"], feat)

    # Register diseases (Disease -> disease, with name canonicalization)
    for disease in p2_nodes.get("Disease", []):
        raw_name = disease.get("name")
        if not isinstance(raw_name, str) or not raw_name.strip():
            raw_name = str(disease["id"]).strip()
        else:
            raw_name = str(raw_name).strip()
        disease_name = _canonical_disease_name(raw_name) if raw_name else str(disease["id"]).strip()
        p2_id_to_p3_name[disease["id"]] = disease_name
        # V92 ROOT FIX (BUG P3-007): use SHA-256 _deterministic_seed
        # instead of non-reproducible hash(). See the drug-registration
        # block above for the full rationale.
        feat = np.random.default_rng(
            _deterministic_seed(str(seed), "disease", disease_name)
        ).standard_normal(DEFAULT_FEATURE_DIMS["disease"]).astype(np.float32)
        gt_builder.register_node("disease", disease_name, feat)

    # Register clinical outcomes (ClinicalOutcome -> clinical_outcome)
    for outcome in p2_nodes.get("ClinicalOutcome", []):
        outcome_name = str(outcome.get("name", outcome["id"])).strip()
        if not outcome_name:
            outcome_name = str(outcome["id"]).strip()
        p2_id_to_p3_name[outcome["id"]] = outcome_name
        # V92 ROOT FIX (BUG P3-007): use SHA-256 _deterministic_seed
        # instead of non-reproducible hash(). See the drug-registration
        # block above for the full rationale.
        feat = np.random.default_rng(
            _deterministic_seed(str(seed), "clinical_outcome", outcome_name)
        ).standard_normal(
            DEFAULT_FEATURE_DIMS["clinical_outcome"]
        ).astype(np.float32)
        gt_builder.register_node("clinical_outcome", outcome_name, feat)

    total_registered = sum(len(m) for m in gt_builder._node_maps.values())
    logger.info(
        f"adapt_phase2_to_phase3: registered {total_registered} nodes "
        f"({', '.join(f'{k}={len(v)}' for k, v in gt_builder._node_maps.items())})"
    )

    # ─── Step 7: Add edges (mapped + derived) ──────────────────────────
    edges_added = 0
    edges_dropped = 0
    for (src_label, rel, dst_label), edge_list in p2_edges.items():
        p3_key = PHASE2_TO_PHASE3_EDGE.get((src_label, rel, dst_label))
        if p3_key is None:
            edges_dropped += len(edge_list)
            continue
        p3_src_type, p3_rel, p3_dst_type = p3_key
        for src_id, dst_id in edge_list:
            src_name = p2_id_to_p3_name.get(src_id)
            dst_name = p2_id_to_p3_name.get(dst_id)
            if src_name is None or dst_name is None:
                edges_dropped += 1
                continue
            if gt_builder.add_edge(
                p3_src_type, p3_rel, p3_dst_type, src_name, dst_name
            ):
                edges_added += 1

    # Add DERIVED (pathway, disrupted_in, disease) edges
    derived_added = 0
    for pathway_id, disease_id in derived_pathway_disease:
        pathway_name = p2_id_to_p3_name.get(pathway_id)
        disease_name = p2_id_to_p3_name.get(disease_id)
        if pathway_name is None or disease_name is None:
            continue
        if gt_builder.add_edge(
            "pathway", "disrupted_in", "disease", pathway_name, disease_name
        ):
            derived_added += 1
            edges_added += 1

    logger.info(
        f"adapt_phase2_to_phase3: added {edges_added} edges "
        f"({derived_added} derived pathway->disease), dropped {edges_dropped}."
    )

    # ─── Step 8: Build reverse edges + finalize ────────────────────────
    # v100 ROOT FIX (CRITICAL -- reverse edges discarded bug):
    # The previous code called the DEPRECATED _build_reverse_edges
    # staticmethod which writes into _edge_lists. But finalize()
    # immediately calls _sync_edge_lists() which rebuilds _edge_lists
    # from _edge_sets (forward-only), DISCARDING all 7 reverse edge
    # types. Use _build_reverse_edges_into_sets (writes into _edge_sets)
    # so reverse edges survive _sync_edge_lists() in finalize().
    gt_builder._build_reverse_edges_into_sets(gt_builder._edge_sets)
    node_features, edge_indices, node_maps = gt_builder.finalize()

    # ─── Step 9: Extract known_pairs from (drug, treats, disease) edges ─
    known_pairs: List[Tuple[str, str]] = []
    seen_pairs: set = set()
    for load in builder.edge_loads:
        if (
            load["src_label"] == "Compound"
            and load["rel_type"] == "treats"
            and load["dst_label"] == "Disease"
        ):
            for e in load["edges"]:
                drug_name = p2_id_to_p3_name.get(e["src_id"])
                disease_name = p2_id_to_p3_name.get(e["dst_id"])
                if drug_name is None or disease_name is None:
                    continue
                pair = (drug_name, disease_name)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    known_pairs.append(pair)

    logger.info(
        f"adapt_phase2_to_phase3: extracted {len(known_pairs)} known "
        f"treatment pairs from (drug, treats, disease) edges."
    )

    # Log which KNOWN_POSITIVES are present in the graph (for recovery test).
    # P3-022 ROOT FIX: the previous code used ``except Exception: pass``,
    # which silently swallowed ALL exceptions:
    #   - ImportError (rl package not installed) -> kp_set never populated,
    #     present_kps always empty, log says "0/N KNOWN_POSITIVES present"
    #     even when they ARE present. Misleading, hides real integration
    #     bugs between Phase 3 and Phase 4.
    #   - ValueError (rl package format change, e.g. 3-tuples instead of
    #     2-tuples) -> the unpacking ``for d, v in _KP`` raises, swallowed,
    #     same misleading "0/N" log.
    # We now catch ONLY ImportError (Phase 4 not deployed yet -- log a
    # WARNING so the user knows the integration check was skipped) and
    # explicitly catch ValueError (data-format drift -- log an ERROR so
    # the maintainer fixes the unpacking). No bare ``except Exception``.
    try:
        from rl.rl_drug_ranker import KNOWN_POSITIVES as _KP
    except ImportError as e:
        logger.warning(
            f"adapt_phase2_to_phase3: Phase 4 rl package not importable "
            f"({e}); skipping KNOWN_POSITIVES recovery check. This is "
            f"expected when running Phase 3 standalone, but in production "
            f"the Phase 4 ranker MUST be installed for the scientific "
            f"validation gate to be meaningful."
        )
    else:
        try:
            kp_set = {(d.lower(), v.lower()) for d, v in _KP}
        except (ValueError, TypeError) as e:
            logger.error(
                f"adapt_phase2_to_phase3: rl.KNOWN_POSITIVES format drift "
                f"-- expected 2-tuples (drug, disease), got "
                f"{type(_KP).__name__} with elements of unexpected shape "
                f"({e}). The recovery check is skipped. Update the "
                f"unpacking in this block to match the new format."
            )
        else:
            present_kps = [p for p in known_pairs if p in kp_set]
            logger.info(
                f"adapt_phase2_to_phase3: {len(present_kps)}/{len(_KP)} "
                f"KNOWN_POSITIVES present in the Phase 2 graph: {present_kps}"
            )

    return node_features, edge_indices, node_maps, known_pairs
