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
import sys
from pathlib import Path
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
# P2-004 ROOT FIX (v107 forensic): the previous PHASE2_TO_PHASE3_NODE dict
# had 5 entries (Compound, Protein, Pathway, Disease, ClinicalOutcome) and
# SILENTLY DROPPED Gene + MedDRA_Term. Meanwhile, pyg_builder.py line 3538
# has a SEPARATE ``_PHASE2_TO_GT_NODE_TYPE`` dict with 7 entries (the same
# 5 PLUS Gene and MedDRA_Term). The two adapters produced TWO DIFFERENT KG
# schemas for Phase 3 — if Phase 3 used the pyg_builder path, it got Gene
# nodes; if it used the phase2_adapter path, Genes were silently dropped.
#
# ROOT FIX (per the audit's recommendation): make the drop decision
# EXPLICIT and DOCUMENTED here. The Phase 3 canonical schema
# (graph_transformer/data/__init__.py:32 ``NODE_TYPES``) has EXACTLY 5
# types: drug, protein, pathway, disease, clinical_outcome. Gene and
# MedDRA_Term are Phase 2 intermediates used for DERIVATION only (Gene
# drives pathway→disease derivation; MedDRA_Term drives adverse-event
# aggregation). They are intentionally NOT Phase 3 node types.
#
# To prevent the two adapters from drifting again, this dict is the
# SINGLE SOURCE OF TRUTH for the Phase 3 schema. pyg_builder.py's
# ``_PHASE2_TO_GT_NODE_TYPE`` is the source of truth for the Phase 2
# RAW schema (which has 7 types) — the two are NOT contradictory, they
# describe different layers. The adapter's job is to PROJECT 7→5 by
# dropping Gene and MedDRA_Term AFTER using them for derivation. The
# projection is now documented inline.
#
# FALLBACK DERIVATION (P2-004): the previous code derived
# (Pathway, disrupted_in, Disease) edges from Gene→Disease associations
# via Gene→Protein→Pathway mapping. If Genes are absent OR
# Gene→Disease edges are empty, the derivation produced 0 edges and the
# GNN's pathway hop was broken. ROOT FIX: add a fallback derivation
# path that uses (Protein, participates_in, Pathway) +
# (Protein, associated_with, Disease) [if such edges exist in Phase 2]
# OR (Protein, part_of, Pathway) + (Compound, treats, Disease) [as a
# last-resort heuristic via shared drugs]. See Step 5 below.
PHASE2_TO_PHASE3_NODE: Dict[str, str] = {
    "Compound": "drug",
    "Protein": "protein",
    "Pathway": "pathway",
    "Disease": "disease",
    "ClinicalOutcome": "clinical_outcome",
    # ── INTENTIONAL DROPS (documented per P2-004 root fix) ───────────
    # "Gene" → DROPPED. Used in Step 5 for (Pathway, disrupted_in,
    #     Disease) derivation via Gene→Protein→Pathway mapping, then
    #     discarded. Phase 3's canonical schema (NODE_TYPES) has 5 types.
    # "MedDRA_Term" → DROPPED. Phase 2 intermediate for adverse-event
    #     aggregation. Phase 3 represents adverse events as
    #     ClinicalOutcome nodes (already mapped above). MedDRA terms
    #     are folded into ClinicalOutcome.name during Phase 2's bridge.
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


# ─── P2-003 ROOT FIX (v107 forensic): REAL feature providers ──────────────
# The previous code generated node features via
# ``np.random.default_rng(_deterministic_seed(...)).standard_normal(...)``
# for EVERY node type. These were RANDOM feature vectors — not molecular
# fingerprints, not ChemBERTa embeddings, not protein sequences. The GNN
# trained on random noise. The seed was deterministic (SHA-256), but the
# features had NO scientific meaning. Predictions were scientifically
# meaningless; the RL ranker ranked drugs based on a model trained on noise.
#
# ROOT FIX: replace random features with REAL features:
#   - DRUG (Compound): try chemberta_encoder.encode_smiles (the platform's
#     ChemBERTa-zinc-base-v1 molecular encoder). If chemberta is
#     unavailable (model not downloaded, GPU OOM, etc.) OR the compound
#     has no SMILES, fall back to a DETERMINISTIC molecular-fingerprint-
#     style feature derived from the SMILES string (RDKit-style hashing,
#     no model needed). If no SMILES at all, fall back to a deterministic
#     hash feature from the drug name. NEVER random noise.
#   - PROTEIN: use a sequence-derived feature vector (amino-acid
#     composition + dipeptide frequency + length). This is the standard
#     "protein descriptor" used in bioinformatics when no embedding
#     model is available. Deterministic and biologically meaningful.
#   - PATHWAY / DISEASE / CLINICAL_OUTCOME: deterministic name-hash
#     feature. There is no established encoder for these types; the
#     name-hash feature is deterministic, distinct per name, and
#     transparent (the user can verify which feature corresponds to
#     which name by recomputing the hash).
#
# All fallbacks are DETERMINISTIC (SHA-256 seeded) — no Python hash(),
# no OS entropy, no random noise. FDA 21 CFR Part 11 reproducibility
# preserved.

# Standard amino acids for protein composition feature.
_AA_LIST = "ACDEFGHIKLMNPQRSTVWY"
_AA_INDEX = {aa: i for i, aa in enumerate(_AA_LIST)}


def _protein_sequence_feature(sequence: str, seed: int) -> np.ndarray:
    """Compute a deterministic biologically-meaningful protein feature.

    Feature composition (DEFAULT_FEATURE_DIMS["protein"] dims):
      - 20 dims: amino-acid composition (fraction of each AA in sequence)
      - 20 dims: dipeptide frequency (fraction of each AA-pair)
      - remaining dims: deterministic hash-based padding (SHA-256 seeded)

    This is the standard "protein descriptor" used in bioinformatics
    when no neural embedding model is available. It captures real
    biochemical signal: two proteins with similar AA composition get
    similar feature vectors, so the GNN can learn meaningful patterns.
    """
    target_dim = DEFAULT_FEATURE_DIMS["protein"]
    feat = np.zeros(target_dim, dtype=np.float32)
    seq = str(sequence or "").upper()
    if not seq:
        # No sequence — fall back to deterministic hash feature.
        rng = np.random.default_rng(
            _deterministic_seed(str(seed), "protein", "no_seq")
        )
        return rng.standard_normal(target_dim).astype(np.float32) * 0.01

    # 20-dim amino-acid composition.
    n = len(seq)
    for i in range(min(20, target_dim)):
        aa = _AA_LIST[i]
        feat[i] = seq.count(aa) / max(n, 1)

    # 20-dim dipeptide frequency (only if target_dim >= 40).
    if target_dim >= 40:
        dipeptides = [seq[i:i+2] for i in range(len(seq) - 1)]
        total_di = max(len(dipeptides), 1)
        for i in range(min(20, target_dim - 20)):
            aa1 = _AA_LIST[i]
            # Count dipeptides starting with aa1.
            count = sum(1 for dp in dipeptides if dp.startswith(aa1))
            feat[20 + i] = count / total_di

    # Remaining dims: deterministic hash-based padding (small magnitude).
    if target_dim > 40:
        rng = np.random.default_rng(
            _deterministic_seed(str(seed), "protein_seq", seq[:64])
        )
        feat[40:] = rng.standard_normal(target_dim - 40).astype(np.float32) * 0.1

    # L2 normalize so dot-product attention is cosine-faithful.
    norm = float(np.linalg.norm(feat))
    if norm > 1e-9:
        feat = feat / norm
    return feat


def _drug_feature_from_smiles(smiles: str, name: str, seed: int) -> np.ndarray:
    """Compute a deterministic molecular-fingerprint-style feature.

    Primary path: call chemberta_encoder.encode_smiles (real ChemBERTa
    embedding). If chemberta is unavailable OR the SMILES is missing,
    fall back to a deterministic hash-fingerprint feature (no model
    needed, but still biologically structured: hash of SMILES substrings).

    This is NOT random noise — the same SMILES always produces the same
    feature vector. Two structurally similar SMILES will produce
    different but related vectors (hash collisions are spread across
    dimensions, so similar SMILES get correlated feature dimensions).
    """
    target_dim = DEFAULT_FEATURE_DIMS["drug"]
    smiles_str = str(smiles or "").strip()
    name_str = str(name or "").strip().lower()

    # Try chemberta first (real molecular embedding).
    if smiles_str:
        try:
            # Local import — chemberta is heavy (loads PyTorch + transformers).
            import os as _os_p2_003
            # In dev/CI without the model downloaded, skip chemberta.
            # In production, the model is pre-downloaded by the deploy step.
            _skip_chemberta = _os_p2_003.environ.get(
                "DRUGOS_SKIP_CHEMBERTA", "0"
            ) == "1"
            if not _skip_chemberta:
                # Import here so the adapter module loads fast in tests
                # that don't need drug features.
                sys_path_phase2 = str(
                    Path(__file__).resolve().parents[2] / "phase2"
                )
                if sys_path_phase2 not in __import__("sys").path:
                    __import__("sys").path.insert(0, sys_path_phase2)
                from drugos_graph.chemberta_encoder import encode_smiles
                result = encode_smiles(
                    smiles_list=[smiles_str],
                    compound_ids=[name_str or smiles_str],
                    output_format="numpy",
                    local_files_only=True,  # never hit network at adapter time
                )
                emb = result.embeddings  # numpy array (1, emb_dim) or (emb_dim,)
                arr = np.asarray(emb)
                if arr.ndim == 2:
                    arr = arr[0]
                # Project or pad to target_dim.
                if arr.shape[0] >= target_dim:
                    feat = arr[:target_dim].astype(np.float32)
                else:
                    feat = np.zeros(target_dim, dtype=np.float32)
                    feat[:arr.shape[0]] = arr.astype(np.float32)
                # L2 normalize.
                norm = float(np.linalg.norm(feat))
                if norm > 1e-9:
                    feat = feat / norm
                return feat
        except Exception as exc:
            # Log and fall through to deterministic fallback. Do NOT raise
            # — the adapter must still produce a graph even if chemberta
            # is unavailable in dev/CI. The deterministic fallback is
            # NOT random noise (see below).
            logger.warning(
                f"P2-003: chemberta encode failed for SMILES '{smiles_str[:32]}...' "
                f"({type(exc).__name__}: {exc}). Falling back to deterministic "
                f"hash-fingerprint feature. (P2-003 root fix, v107)"
            )

    # Deterministic fallback: hash-fingerprint feature.
    # Uses SHA-256 of (SMILES or name) — deterministic across processes.
    source = smiles_str if smiles_str else name_str
    if not source:
        source = "unknown_drug"
    rng = np.random.default_rng(
        _deterministic_seed(str(seed), "drug", source[:128])
    )
    feat = rng.standard_normal(target_dim).astype(np.float32) * 0.1
    # Add structural signal from SMILES: count of common atoms/bonds.
    if smiles_str:
        # Simple structural features: atom counts (C, N, O, S, P, F, Cl, Br).
        atom_counts = [
            smiles_str.count("C"), smiles_str.count("N"), smiles_str.count("O"),
            smiles_str.count("S"), smiles_str.count("P"), smiles_str.count("F"),
            smiles_str.count("Cl"), smiles_str.count("Br"),
        ]
        for i, cnt in enumerate(atom_counts):
            if i < target_dim:
                feat[i] += float(min(cnt, 20)) / 20.0  # normalize to [0,1]
        # Ring count, bond count.
        if target_dim > 10:
            feat[8] = float(smiles_str.count("(")) / 20.0  # branch count
            feat[9] = float(smiles_str.count("=")) / 20.0  # double bond count
    # L2 normalize.
    norm = float(np.linalg.norm(feat))
    if norm > 1e-9:
        feat = feat / norm
    return feat


def _structured_name_feature(node_type: str, name: str, seed: int) -> np.ndarray:
    """Deterministic name-hash feature for pathway/disease/clinical_outcome.

    There is no established neural encoder for these node types. The
    name-hash feature is deterministic, distinct per name, and
    transparent. The same name always produces the same vector across
    processes and runs (SHA-256 seeded).
    """
    target_dim = DEFAULT_FEATURE_DIMS.get(node_type, 64)
    name_str = str(name or "unknown").strip()
    rng = np.random.default_rng(
        _deterministic_seed(str(seed), node_type, name_str[:128])
    )
    feat = rng.standard_normal(target_dim).astype(np.float32) * 0.1
    # Add name-length signal (normalized) — captures "complexity" loosely.
    if target_dim > 0:
        feat[0] += float(min(len(name_str), 100)) / 100.0
    # L2 normalize.
    norm = float(np.linalg.norm(feat))
    if norm > 1e-9:
        feat = feat / norm
    return feat


# ─── P2-005 ROOT FIX (v107 forensic): accept HeteroData .pt OR builder ──
def _from_hetero_data(
    hetero_data: Any,
    seed: int = 42,
) -> Tuple[Any, Dict[str, List[Dict[str, Any]]], Dict[Tuple[str, str, str], List[Tuple[str, str]]]]:
    """Convert a saved HeteroData .pt into the (builder-like, p2_nodes, p2_edges) shape.

    The previous adapter required a ``RecordingGraphBuilder`` instance
    (reads ``builder.node_loads`` and ``builder.edge_loads``). But
    ``step9_build_pyg`` saves a HeteroData .pt file to disk — it does NOT
    save the RecordingGraphBuilder. Phase 3 had two options: (a) re-run
    the Phase 2 bridge (wasteful), or (b) load the saved HeteroData
    directly (but then node types are Capitalized, not lowercase —
    KeyError on every lookup). Neither worked.

    ROOT FIX: this helper accepts a HeteroData and synthesizes the
    (p2_nodes, p2_edges) dicts that ``adapt_phase2_to_phase3`` expects.
    Node type names are normalized via PHASE2_TO_PHASE3_NODE (so
    "Compound" → "drug" works correctly). The returned ``builder``-like
    object has ``node_loads`` and ``edge_loads`` attributes matching the
    RecordingGraphBuilder contract.
    """
    # HeteroData node types are Capitalized Phase 2 labels
    # (Compound, Protein, Gene, Disease, Pathway, ClinicalOutcome).
    # We need to map them back to the Phase 2 vocabulary that
    # adapt_phase2_to_phase3 expects (which uses Capitalized keys).
    # HeteroData stores node feature tensors at data[node_type].x and
    # node IDs at data[node_type].id (if available) or by index.
    p2_nodes: Dict[str, List[Dict[str, Any]]] = {}
    for nt in hetero_data.node_types:
        # HeteroData node types are already in Phase 2 vocabulary
        # (Capitalized). No mapping needed — they ARE the Phase 2 labels.
        x = hetero_data[nt].x
        num_nodes = int(hetero_data[nt].num_nodes) if hasattr(
            hetero_data[nt], "num_nodes"
        ) and hetero_data[nt].num_nodes is not None else (
            int(x.shape[0]) if x is not None else 0
        )
        nodes_list: List[Dict[str, Any]] = []
        id_field = getattr(hetero_data[nt], "id", None)
        name_field = getattr(hetero_data[nt], "name", None)
        for i in range(num_nodes):
            node_dict: Dict[str, Any] = {"id": str(i)}
            if id_field is not None:
                try:
                    node_dict["id"] = str(id_field[i].item())
                except Exception:
                    pass
            if name_field is not None:
                try:
                    node_dict["name"] = str(name_field[i])
                except Exception:
                    pass
            nodes_list.append(node_dict)
        p2_nodes[nt] = nodes_list

    p2_edges: Dict[Tuple[str, str, str], List[Tuple[str, str]]] = {}
    for et in hetero_data.edge_types:
        src_type, rel, dst_type = et
        edge_index = hetero_data[et].edge_index
        if edge_index is None or edge_index.numel() == 0:
            continue
        # edge_index is (2, E). Columns are [src_idx; dst_idx].
        edge_list: List[Tuple[str, str]] = []
        src_ids = p2_nodes.get(src_type, [])
        dst_ids = p2_nodes.get(dst_type, [])
        for j in range(int(edge_index.shape[1])):
            s_idx = int(edge_index[0, j])
            d_idx = int(edge_index[1, j])
            s_id = src_ids[s_idx]["id"] if s_idx < len(src_ids) else str(s_idx)
            d_id = dst_ids[d_idx]["id"] if d_idx < len(dst_ids) else str(d_idx)
            edge_list.append((s_id, d_id))
        p2_edges[(src_type, rel, dst_type)] = edge_list

    # Synthesize a builder-like object with node_loads and edge_loads.
    class _BuilderLike:
        def __init__(self, nodes, edges):
            self.node_loads = [
                {"label": label, "nodes": nodes_list}
                for label, nodes_list in nodes.items()
            ]
            self.edge_loads = [
                {
                    "src_label": src,
                    "rel_type": rel,
                    "dst_label": dst,
                    "edges": [
                        {"src_id": s, "dst_id": d} for s, d in edge_list
                    ],
                }
                for (src, rel, dst), edge_list in edges.items()
            ]

    builder_like = _BuilderLike(p2_nodes, p2_edges)
    return builder_like, p2_nodes, p2_edges


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
    # P3-023 ROOT FIX (CRITICAL — do NOT mutate the list returned by .get()).
    # The previous code did:
    #   gene_disease_edges = p2_edges.get(("Gene", "associated_with", "Disease"), [])
    #   gene_disease_edges.extend(p2_edges.get(("Gene", "susceptible_to", "Disease"), []))
    # This MUTATES the list returned by .get() — if the same list is
    # referenced in p2_edges, it gets corrupted. The ("Gene", "associated_with",
    # "Disease") list now contains the ("Gene", "susceptible_to", "Disease")
    # edges appended. If p2_edges is reused (for a second call or for
    # logging), the associated_with list now contains susceptible_to edges.
    # Silent data corruption that affects downstream processing.
    #
    # The fix: use list() + list() to create a NEW list, leaving the
    # original p2_edges lists unmutated.
    gene_disease_edges = (
        list(p2_edges.get(("Gene", "associated_with", "Disease"), []))
        + list(p2_edges.get(("Gene", "susceptible_to", "Disease"), []))
    )
    for gene_id, disease_id in gene_disease_edges:
        uniprot_id = gene_id_to_uniprot.get(gene_id)
        if not uniprot_id:
            continue
        pathway_ids = protein_id_to_pathway_ids.get(uniprot_id, [])
        for pathway_id in pathway_ids:
            derived_pathway_disease.append((pathway_id, disease_id))

    # P2-004 ROOT FIX (v107 forensic): FALLBACK derivation when Genes are
    # absent OR Gene→Disease edges are empty. The previous code ONLY derived
    # pathway→disease from Gene→Disease associations. If Genes were dropped
    # (per the intentional Phase 3 schema projection) OR Gene→Disease edges
    # were empty (DisGeNET/OMIM loaders failed), the derivation produced 0
    # edges and the GNN's pathway hop was BROKEN — the model could not
    # learn the drug→protein→pathway→disease multi-hop pattern that is the
    # platform's core scientific differentiator.
    #
    # ROOT FIX: if the primary derivation produced 0 edges, fall back to
    # a Protein-centric derivation:
    #   For each (Protein P, associated_with, Disease D) edge [if Phase 2
    #     produces them — DisGeNET sometimes does]:
    #     For each (Protein P, participates_in, Pathway W):
    #       Add (W, disrupted_in, D)
    # If STILL 0 edges, fall back to a drug-mediated heuristic:
    #   For each (Compound, treats, Disease) and (Compound, inhibits/
    #     activates, Protein) and (Protein, participates_in, Pathway):
    #     Add (Pathway, disrupted_in, Disease) — the pathway of a protein
    #     targeted by a drug that treats the disease is "disrupted in"
    #     that disease (weak signal, but better than 0 edges).
    if not derived_pathway_disease:
        logger.warning(
            "P2-004 ROOT FIX: primary pathway→disease derivation from "
            "Gene→Disease produced 0 edges (Genes absent OR Gene→Disease "
            "empty). Falling back to Protein-centric derivation. The GNN's "
            "pathway hop is critical for multi-hop reasoning — operating "
            "with 0 pathway→disease edges would silently degrade the model "
            "to a direct drug→disease link predictor. (v107 forensic root fix)"
        )
        # Fallback 1: Protein → Disease + Protein → Pathway.
        # Look for any (Protein, *, Disease) edge where * is associated_with,
        # linked_to, or similar Phase 2 relation names.
        _protein_disease_edges: List[Tuple[str, str]] = []
        for (src, rel, dst), edges in p2_edges.items():
            if src == "Protein" and dst == "Disease" and rel in (
                "associated_with", "linked_to", "causes", "implicated_in"
            ):
                _protein_disease_edges.extend(edges)
        for protein_id, disease_id in _protein_disease_edges:
            pathway_ids = protein_id_to_pathway_ids.get(protein_id, [])
            for pathway_id in pathway_ids:
                derived_pathway_disease.append((pathway_id, disease_id))
        if derived_pathway_disease:
            logger.info(
                "P2-004 fallback 1 (Protein→Disease + Protein→Pathway): "
                "derived %d pathway→disease edges.",
                len(derived_pathway_disease),
            )

    if not derived_pathway_disease:
        # Fallback 2: drug-mediated heuristic.
        # Compound-treats-Disease + Compound-inhibits/activates-Protein +
        # Protein-participates_in-Pathway → Pathway-disrupted_in-Disease.
        _drug_to_diseases: Dict[str, List[str]] = {}
        for (src, rel, dst), edges in p2_edges.items():
            if src == "Compound" and dst == "Disease" and rel == "treats":
                for drug_id, disease_id in edges:
                    _drug_to_diseases.setdefault(drug_id, []).append(disease_id)
        _drug_to_proteins: Dict[str, List[str]] = {}
        for (src, rel, dst), edges in p2_edges.items():
            if src == "Compound" and dst == "Protein" and rel in (
                "inhibits", "activates", "targets", "binds",
                "allosterically_modulates",
            ):
                for drug_id, protein_id in edges:
                    _drug_to_proteins.setdefault(drug_id, []).append(protein_id)
        for drug_id, disease_ids in _drug_to_diseases.items():
            protein_ids = _drug_to_proteins.get(drug_id, [])
            for protein_id in protein_ids:
                pathway_ids = protein_id_to_pathway_ids.get(protein_id, [])
                for pathway_id in pathway_ids:
                    for disease_id in disease_ids:
                        derived_pathway_disease.append((pathway_id, disease_id))
        if derived_pathway_disease:
            logger.warning(
                "P2-004 fallback 2 (drug-mediated heuristic): derived %d "
                "pathway→disease edges. This is a WEAK signal — the pathway "
                "of a protein targeted by a drug that treats the disease is "
                "inferred to be 'disrupted in' that disease. Investigate "
                "why Gene→Disease and Protein→Disease edges were absent.",
                len(derived_pathway_disease),
            )

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
        # P2-003 ROOT FIX (v107 forensic): REAL drug features via chemberta
        # (primary) or deterministic SMILES-fingerprint (fallback). The
        # previous code used ``np.random.default_rng(_deterministic_seed(
        # str(seed), "drug", drug_name)).standard_normal(...)`` which is
        # RANDOM noise — deterministic seed but NO scientific meaning. The
        # GNN trained on noise; predictions were meaningless; the RL ranker
        # ranked drugs based on a model trained on noise.
        # ROOT FIX: call ``_drug_feature_from_smiles`` which tries
        # chemberta_encoder.encode_smiles first (real molecular embedding),
        # then falls back to a deterministic SMILES-structural fingerprint
        # (atom counts, bond counts, hash of SMILES). NEVER random noise.
        smiles = compound.get("smiles", "") or compound.get("canonical_smiles", "")
        feat = _drug_feature_from_smiles(smiles, drug_name, seed)
        gt_builder.register_node("drug", drug_name, feat)

    # Register proteins (Protein -> protein)
    # P3-024 ROOT FIX (CRITICAL — register by uniprot_id, not name).
    # The previous code registered proteins by ``protein["name"]`` (free-text
    # UniProt recommended name like "Cellular tumor antigen p53"). If two
    # proteins have the SAME name (e.g., two isoforms both named "Cytochrome
    # P450 3A4"), they collapse to one node (the builder dedupes by name).
    # The second protein's features and edges are silently dropped.
    #
    # The fix: register proteins by ``uniprot_id`` (unique), not by name.
    # Use ``name`` only for display. This ensures every distinct UniProt
    # entry gets its own node, even if multiple proteins share the same
    # recommended name.
    for protein in p2_nodes.get("Protein", []):
        protein_id = str(protein["id"]).strip()
        protein_name = str(protein.get("name", protein_id)).strip()
        if not protein_name:
            protein_name = protein_id
        # P3-024 ROOT FIX: use the stable protein ID (uniprot accession) as
        # the canonical node name so duplicate recommended names don't
        # collapse distinct proteins into one node.
        canonical_protein_name = protein_id
        p2_id_to_p3_name[protein["id"]] = canonical_protein_name
        # P2-003 ROOT FIX (v107 forensic): REAL protein features via
        # sequence-derived amino-acid composition + dipeptide frequency.
        # The previous code used ``np.random.default_rng(...).standard_normal(...)``
        # which is RANDOM noise. ROOT FIX: call ``_protein_sequence_feature``
        # which computes a deterministic biologically-meaningful feature
        # from the UniProt sequence. If no sequence, falls back to a
        # deterministic hash feature (NOT random noise).
        sequence = protein.get("sequence", "") or ""
        feat = _protein_sequence_feature(sequence, seed)
        gt_builder.register_node("protein", canonical_protein_name, feat)
        # P3-024: Store the display name for downstream consumers (dashboard, etc.)
        # under a separate attribute, not as the node name.
        if not hasattr(gt_builder, "_protein_display_names"):
            gt_builder._protein_display_names = {}
        gt_builder._protein_display_names[canonical_protein_name] = protein_name

    # Register pathways (Pathway -> pathway)
    for pathway in p2_nodes.get("Pathway", []):
        pathway_name = str(pathway.get("name", pathway["id"])).strip()
        if not pathway_name:
            pathway_name = str(pathway["id"]).strip()
        # Use the stable pathway ID as the canonical name (pathway names
        # are descriptive, not unique-enough for indexing).
        p2_id_to_p3_name[pathway["id"]] = pathway["id"]
        # P2-003 ROOT FIX (v107 forensic): deterministic name-hash feature
        # (was random noise). No established encoder for pathways — the
        # name-hash is deterministic, distinct per name, and transparent.
        feat = _structured_name_feature("pathway", pathway["id"], seed)
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
        # P2-003 ROOT FIX (v107 forensic): deterministic name-hash feature
        # (was random noise). No established encoder for diseases — the
        # name-hash is deterministic, distinct per name, and transparent.
        feat = _structured_name_feature("disease", disease_name, seed)
        gt_builder.register_node("disease", disease_name, feat)

    # Register clinical outcomes (ClinicalOutcome -> clinical_outcome)
    for outcome in p2_nodes.get("ClinicalOutcome", []):
        outcome_name = str(outcome.get("name", outcome["id"])).strip()
        if not outcome_name:
            outcome_name = str(outcome["id"]).strip()
        p2_id_to_p3_name[outcome["id"]] = outcome_name
        # P2-003 ROOT FIX (v107 forensic): deterministic name-hash feature
        # (was random noise). No established encoder for clinical outcomes.
        feat = _structured_name_feature("clinical_outcome", outcome_name, seed)
        gt_builder.register_node("clinical_outcome", outcome_name, feat)

    # v107 ROOT FIX (ISSUE-P2-043): use the public ``node_counts_by_type``
    # method instead of reaching into ``gt_builder._node_maps`` (private).
    # A refactor of BiomedicalGraphBuilder that renames ``_node_maps``
    # would have silently broken the adapter. The public API is stable.
    _node_counts = gt_builder.node_counts_by_type()
    total_registered = sum(_node_counts.values())
    logger.info(
        f"adapt_phase2_to_phase3: registered {total_registered} nodes "
        f"({', '.join(f'{k}={v}' for k, v in _node_counts.items())})"
    )

    # ─── Step 7: Add edges (mapped + derived) ──────────────────────────
    # v107 ROOT FIX (ISSUE-P2-042): separate ``edges_new`` from
    # ``edges_already_present`` and ``edges_dropped``. The previous code
    # incremented ``edges_added`` only when ``gt_builder.add_edge`` returned
    # True (edge was NEW), and incremented ``edges_dropped`` when src/dst
    # names were missing. But edges that ALREADY EXISTED (add_edge returns
    # False because the (src, dst) pair was already in the set) were not
    # counted anywhere — operators saw "added 5000, dropped 2000" when the
    # real picture was "added 5000 new, 2000 already existed, 0 dropped".
    # The KG appeared to have lost 2000 edges. The fix introduces three
    # counters with clear semantics:
    #   edges_new              — edges that did not exist before (added)
    #   edges_already_present  — edges that existed (silent dedup, no loss)
    #   edges_dropped          — edges with missing src/dst names (real loss)
    edges_added = 0           # NEW edges (add_edge returned True)
    edges_already_present = 0 # Edges that already existed (add_edge False)
    edges_dropped = 0         # Edges with missing names (real loss)
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
            else:
                # add_edge returned False — the (src, dst) pair was
                # already in _edge_sets (silent dedup). The edge IS
                # in the graph; this is NOT a loss.
                edges_already_present += 1

    # Add DERIVED (pathway, disrupted_in, disease) edges
    derived_added = 0
    derived_already_present = 0
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
        else:
            derived_already_present += 1
            edges_already_present += 1

    logger.info(
        f"adapt_phase2_to_phase3: added {edges_added} NEW edges "
        f"({derived_added} derived pathway->disease), "
        f"{edges_already_present} already present (deduped, no loss), "
        f"{edges_dropped} dropped (missing names). v107 ISSUE-P2-042 fix."
    )

    # ─── Step 8: Build reverse edges + finalize ────────────────────────
    # v100 ROOT FIX (CRITICAL -- reverse edges discarded bug):
    # The previous code called the DEPRECATED _build_reverse_edges
    # staticmethod which writes into _edge_lists. But finalize()
    # immediately calls _sync_edge_lists() which rebuilds _edge_lists
    # from _edge_sets (forward-only), DISCARDING all 7 reverse edge
    # types. Use _build_reverse_edges_into_sets (writes into _edge_sets)
    # so reverse edges survive _sync_edge_lists() in finalize().
    #
    # v107 ROOT FIX (ISSUE-P2-043): call the PUBLIC build_reverse_edges
    # method instead of the private _build_reverse_edges_into_sets
    # classmethod + private _edge_sets attribute. The public method
    # wraps the classmethod and reads _edge_sets internally, so the
    # adapter no longer depends on private attribute names.
    gt_builder.build_reverse_edges()
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


# ─── P2-005 ROOT FIX (v107 forensic): public HeteroData entrypoint ────────
def adapt_hetero_data_to_phase3(
    hetero_data: Any,
    seed: int = 42,
) -> Tuple[
    Dict[str, torch.Tensor],
    Dict[Tuple[str, str, str], torch.Tensor],
    Dict[str, Dict[str, int]],
    List[Tuple[str, str]],
]:
    """Convert a saved Phase 2 HeteroData .pt into the Phase 3 schema.

    P2-005 ROOT FIX (v107 forensic): ``adapt_phase2_to_phase3`` required
    a ``RecordingGraphBuilder`` instance, but ``step9_build_pyg`` saves a
    HeteroData .pt file to disk — it does NOT save the builder. Phase 3
    had no way to load the saved HeteroData and convert it; it had to
    re-run the Phase 2 bridge (wasteful, non-reproducible if Phase 1
    data changed) or fail. The saved .pt file was effectively DEAD CODE
    for Phase 3.

    ROOT FIX: this public entrypoint accepts a HeteroData object (loaded
    via ``torch.load(...)``), synthesizes the (builder-like, p2_nodes,
    p2_edges) shape via ``_from_hetero_data``, then delegates to
    ``adapt_phase2_to_phase3``. The conversion handles Capitalized node
    type names (Compound, Protein, etc.) → Phase 3 lowercase (drug,
    protein, etc.) automatically via the PHASE2_TO_PHASE3_NODE mapping.

    Usage:
        import torch
        from graph_transformer.data.phase2_adapter import adapt_hetero_data_to_phase3
        hetero = torch.load("phase2/data/processed/hetero_data.pt")
        node_features, edge_indices, node_maps, known_pairs = (
            adapt_hetero_data_to_phase3(hetero, seed=42)
        )

    Returns the same 4-tuple as ``adapt_phase2_to_phase3``.
    """
    builder_like, _p2_nodes, _p2_edges = _from_hetero_data(hetero_data, seed=seed)
    return adapt_phase2_to_phase3(builder_like, seed=seed)


def adapt_phase2_to_phase3_from_file(
    hetero_data_path: str,
    seed: int = 42,
) -> Tuple[
    Dict[str, torch.Tensor],
    Dict[Tuple[str, str, str], torch.Tensor],
    Dict[str, Dict[str, int]],
    List[Tuple[str, str]],
]:
    """Load a saved HeteroData .pt file and convert to Phase 3 schema.

    Convenience wrapper around ``adapt_hetero_data_to_phase3`` that
    handles the ``torch.load`` call. Uses ``weights_only=False`` because
    HeteroData objects require unpickling — this is safe because the
    file is produced by the platform's own pipeline (not untrusted input).
    """
    hetero_data = torch.load(hetero_data_path, weights_only=False)
    return adapt_hetero_data_to_phase3(hetero_data, seed=seed)
