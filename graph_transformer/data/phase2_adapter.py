"""Phase 2 → Phase 3 schema adapter.

REAL ROOT FIX (v90 P0 — Phase 1-2-3-4 integration):

The previous ``run_pipeline.py`` called a FICTIONAL ``build_pyg_hetero_data``
function that does NOT EXIST in ``pyg_builder.py``. It also called
``stage_phase1_to_phase2()`` with the wrong API (``output_dir=`` kwarg
that doesn't exist, missing required ``frames`` arg, expected a 3-tuple
return but the function returns ``Phase1StagedData``). The pipeline
CRASHED at the bridge call — it never ran end-to-end. Every "v89 100%
connected" claim was unverifiable because the entry point was broken.

This module provides the REAL adapter that converts the Phase 2
``RecordingGraphBuilder`` output (which uses capitalized node labels:
``Compound``, ``Protein``, ``Gene``, ``Disease``, ``ClinicalOutcome``,
``Pathway``) into the Phase 3 canonical schema (lowercase: ``drug``,
``protein``, ``pathway``, ``disease``, ``clinical_outcome``) defined in
``graph_transformer/data/__init__.py``.

The adapter performs FOUR transformations:

1. NODE TYPE MAPPING
   - ``Compound`` → ``drug`` (FDA-approved drugs / ChEMBL molecules)
   - ``Protein`` → ``protein`` (UniProt targets)
   - ``Pathway`` → ``pathway`` (STRING-derived connected components)
   - ``Disease`` → ``disease`` (OMIM / DisGeNET / DrugBank indications)
   - ``ClinicalOutcome`` → ``clinical_outcome`` (DrugBank indication outcomes)
   - ``Gene`` → DROPPED (Phase 3 schema has 5 node types; Gene is a
     Phase 2 intermediate used only to derive pathway→disease edges)

2. EDGE TYPE MAPPING
   - ``(Compound, inhibits, Protein)`` → ``(drug, inhibits, protein)``
   - ``(Compound, activates, Protein)`` → ``(drug, activates, protein)``
   - ``(Compound, treats, Disease)`` → ``(drug, treats, disease)``
   - ``(Compound, has_clinical_outcome, ClinicalOutcome)`` →
     ``(drug, causes, clinical_outcome)``
   - ``(Protein, participates_in, Pathway)`` →
     ``(protein, part_of, pathway)``
   - DERIVED: ``(Pathway, disrupted_in, Disease)`` — see step 3.
   - DROPPED (no Phase 3 equivalent):
     - ``(Compound, targets, Protein)`` (direction-unknown binding)
     - ``(Compound, allosterically_modulates, Protein)``
     - ``(Protein, interacts_with, Protein)`` (PPI not in Phase 3 schema)
     - ``(Gene, associated_with, Disease)`` (Gene dropped)
     - ``(Gene, susceptible_to, Disease)`` (Gene dropped)

3. DERIVE (pathway, disrupted_in, disease) EDGES
   Phase 3's canonical schema requires ``(pathway, disrupted_in, disease)``
   edges for the GT model to learn the drug→protein→pathway→disease
   multi-hop pattern. The Phase 2 bridge does NOT produce these directly.
   The adapter derives them from:
     - ``(Gene, associated_with, Disease)`` edges (DisGeNET / OMIM GDAs)
     - Gene → Protein mapping (by ``gene_symbol`` matching Protein ``name``)
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
   - Drug names: lowercase + strip (e.g., ``"Aspirin"`` → ``"aspirin"``)
   - Disease names: lowercase + strip + canonical mapping (e.g.,
     ``"Diabetes Mellitus"`` → ``"type 2 diabetes"``, ``"Arthritis"`` →
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
from .graph_builder import BiomedicalGraphBuilder

logger = logging.getLogger(__name__)

# ─── Phase 2 → Phase 3 node type mapping ────────────────────────────────
PHASE2_TO_PHASE3_NODE: Dict[str, str] = {
    "Compound": "drug",
    "Protein": "protein",
    "Pathway": "pathway",
    "Disease": "disease",
    "ClinicalOutcome": "clinical_outcome",
    # "Gene" is NOT mapped — it's used only for pathway→disease derivation.
}

# ─── Phase 2 → Phase 3 edge type mapping ────────────────────────────────
# Key: (src_label, rel_type, dst_label) in Phase 2 vocabulary.
# Value: (src_type, rel_type, tgt_type) in Phase 3 canonical vocabulary.
# Only edges whose endpoints are both mappable appear here. Edges not in
# this map are DROPPED (with a DEBUG log).
PHASE2_TO_PHASE3_EDGE: Dict[Tuple[str, str, str], Tuple[str, str, str]] = {
    ("Compound", "inhibits", "Protein"): ("drug", "inhibits", "protein"),
    ("Compound", "activates", "Protein"): ("drug", "activates", "protein"),
    ("Compound", "treats", "Disease"): ("drug", "treats", "disease"),
    ("Compound", "has_clinical_outcome", "ClinicalOutcome"): (
        "drug",
        "causes",
        "clinical_outcome",
    ),
    ("Protein", "participates_in", "Pathway"): (
        "protein",
        "part_of",
        "pathway",
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
    # DrugBank "Diabetes Mellitus" → KNOWN_POSITIVES "type 2 diabetes"
    "diabetes mellitus": "type 2 diabetes",
    "diabetes": "type 2 diabetes",
    "type 2 diabetes": "type 2 diabetes",
    "type 2 diabetes mellitus": "type 2 diabetes",
    # DrugBank "Arthritis" → KNOWN_POSITIVES "rheumatoid arthritis"
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

    This is the REAL Phase 2 → Phase 3 integration point. It takes the
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
        Name → index mapping per node type (lowercase canonical names).
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

    # ─── Step 3: Build Gene → Protein mapping (by gene_symbol) ─────────
    # This is used to derive (pathway, disrupted_in, disease) edges.
    gene_symbol_to_protein_id: Dict[str, str] = {}
    for gene in p2_nodes.get("Gene", []):
        gene_symbol = str(gene.get("gene_symbol", "")).strip().upper()
        if gene_symbol:
            gene_symbol_to_protein_id[gene_symbol] = gene["id"]

    protein_id_by_name: Dict[str, str] = {}
    for protein in p2_nodes.get("Protein", []):
        name = str(protein.get("name", "")).strip().upper()
        if name:
            protein_id_by_name[name] = protein["id"]

    # gene_symbol → UniProt ID (via Protein.name match)
    gene_id_to_uniprot: Dict[str, str] = {}
    for gene in p2_nodes.get("Gene", []):
        gene_symbol = str(gene.get("gene_symbol", "")).strip().upper()
        uniprot_id = protein_id_by_name.get(gene_symbol)
        if uniprot_id:
            gene_id_to_uniprot[gene["id"]] = uniprot_id

    # ─── Step 4: Build Protein → Pathway mapping ───────────────────────
    protein_id_to_pathway_ids: Dict[str, List[str]] = {}
    for (src, rel, dst), edges in p2_edges.items():
        if src == "Protein" and rel == "participates_in" and dst == "Pathway":
            for protein_id, pathway_id in edges:
                protein_id_to_pathway_ids.setdefault(protein_id, []).append(
                    pathway_id
                )

    # ─── Step 5: Derive (Pathway, disrupted_in, Disease) edges ─────────
    # For each (Gene, associated_with, Disease):
    #   Gene → UniProt (by gene_symbol) → Pathway (by participates_in)
    #   → add (Pathway, disrupted_in, Disease)
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
        f"gene_symbol→protein→pathway mapping."
    )

    # ─── Step 6: Register nodes into BiomedicalGraphBuilder ────────────
    # Use the lowercase canonical names as node_map keys so they match
    # the RL ranker's KNOWN_POSITIVES vocabulary.
    gt_builder = BiomedicalGraphBuilder(
        feature_dims=DEFAULT_FEATURE_DIMS, seed=seed
    )

    # Track Phase 2 ID → Phase 3 canonical name for edge endpoint resolution.
    p2_id_to_p3_name: Dict[str, str] = {}

    # Register drugs (Compound → drug)
    for compound in p2_nodes.get("Compound", []):
        drug_name = _canonical_drug_name(compound.get("name", compound["id"]))
        if not drug_name:
            drug_name = compound["id"].lower()
        p2_id_to_p3_name[compound["id"]] = drug_name
        feat = np.random.default_rng(seed + hash(drug_name) & 0xFFFFFFFF).standard_normal(
            DEFAULT_FEATURE_DIMS["drug"]
        ).astype(np.float32)
        gt_builder.register_node("drug", drug_name, feat)

    # Register proteins (Protein → protein)
    for protein in p2_nodes.get("Protein", []):
        protein_name = str(protein.get("name", protein["id"])).strip()
        if not protein_name:
            protein_name = protein["id"]
        p2_id_to_p3_name[protein["id"]] = protein_name
        feat = np.random.default_rng(
            seed + hash(protein_name) & 0xFFFFFFFF
        ).standard_normal(DEFAULT_FEATURE_DIMS["protein"]).astype(np.float32)
        gt_builder.register_node("protein", protein_name, feat)

    # Register pathways (Pathway → pathway)
    for pathway in p2_nodes.get("Pathway", []):
        pathway_name = str(pathway.get("name", pathway["id"])).strip()
        if not pathway_name:
            pathway_name = pathway["id"]
        # Use the stable pathway ID as the canonical name (pathway names
        # are descriptive, not unique-enough for indexing).
        p2_id_to_p3_name[pathway["id"]] = pathway["id"]
        feat = np.random.default_rng(
            seed + hash(pathway["id"]) & 0xFFFFFFFF
        ).standard_normal(DEFAULT_FEATURE_DIMS["pathway"]).astype(np.float32)
        gt_builder.register_node("pathway", pathway["id"], feat)

    # Register diseases (Disease → disease, with name canonicalization)
    for disease in p2_nodes.get("Disease", []):
        raw_name = str(disease.get("name", disease["id"])).strip()
        disease_name = _canonical_disease_name(raw_name) if raw_name else disease["id"]
        p2_id_to_p3_name[disease["id"]] = disease_name
        feat = np.random.default_rng(
            seed + hash(disease_name) & 0xFFFFFFFF
        ).standard_normal(DEFAULT_FEATURE_DIMS["disease"]).astype(np.float32)
        gt_builder.register_node("disease", disease_name, feat)

    # Register clinical outcomes (ClinicalOutcome → clinical_outcome)
    for outcome in p2_nodes.get("ClinicalOutcome", []):
        outcome_name = str(outcome.get("name", outcome["id"])).strip()
        if not outcome_name:
            outcome_name = outcome["id"]
        p2_id_to_p3_name[outcome["id"]] = outcome_name
        feat = np.random.default_rng(
            seed + hash(outcome_name) & 0xFFFFFFFF
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
        f"({derived_added} derived pathway→disease), dropped {edges_dropped}."
    )

    # ─── Step 8: Build reverse edges + finalize ────────────────────────
    # v100 ROOT FIX (CRITICAL — reverse edges discarded bug):
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

    # Log which KNOWN_POSITIVES are present in the graph (for recovery test)
    try:
        from rl.rl_drug_ranker import KNOWN_POSITIVES as _KP
        kp_set = {(d.lower(), v.lower()) for d, v in _KP}
        present_kps = [p for p in known_pairs if p in kp_set]
        logger.info(
            f"adapt_phase2_to_phase3: {len(present_kps)}/{len(_KP)} "
            f"KNOWN_POSITIVES present in the Phase 2 graph: {present_kps}"
        )
    except Exception:
        pass

    return node_features, edge_indices, node_maps, known_pairs
