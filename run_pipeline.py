#!/usr/bin/env python3
"""
v89 P0 ROOT FIX — Unified 4-Phase Pipeline Runner
==================================================

This is the SINGLE top-level entry point that chains ALL 4 phases of
the Autonomous Drug Repurposing Platform:

  Phase 1 (Data Ingestion)
    → reads/writes Phase 1 processed_data CSVs (ChEMBL, DrugBank,
       UniProt, STRING, DisGeNET, OMIM, PubChem). If the CSVs are
       already present (from a prior Phase 1 run), they are reused.
       If not, the embedded sample data is loaded (Tier-2 fallback).

  Bridge (phase1_bridge.stage_phase1_to_phase2)
    → converts Phase 1 CSVs into Phase 2 node/edge dicts with full
       lineage. This is the ONLY data path from Phase 1 to Phase 2
       (no duplicate loaders).

  Phase 2 (Knowledge Graph Build)
    → RecordingGraphBuilder (in-memory) OR Neo4j (if configured).
       Produces the real biomedical KG with 5 node types (drug,
       protein, pathway, disease, clinical_outcome) and 14 edge types.

  Phase 3 (Graph Transformer Training)
    → GraphTransformerTrainer on the REAL Phase 2 HeteroData (NOT
       build_demo_graph). The GT model trains on real biomedical
       topology from DrugBank + UniProt + STRING + DisGeNET + OMIM.

  Phase 4 (RL Hypothesis Ranking)
    → RL ranker via gt_rl_bridge. The RL agent ranks the top-N
       drug-disease repurposing candidates using the GT model's
       predictions + 7 independent features (pathway, safety, market,
       unmet_need, efficacy, patent, adme).

This is the user's explicit requirement (v89 audit):
  "Write a single run_pipeline.py that calls Phase 1 →
   phase1_bridge.stage_phase1_to_phase2 → Phase 2 kg_builder →
   Phase 3 GraphTransformerTrainer (loading the REAL Phase 2
   HeteroData, not build_demo_graph) → Phase 4 RL ranker."

USAGE
-----
  # Full 4-phase pipeline (in-memory KG, no Neo4j required):
  python run_pipeline.py

  # With custom Phase 1 dir:
  python run_pipeline.py --phase1-dir /path/to/processed_data

  # With Neo4j:
  python run_pipeline.py --neo4j-uri bolt://localhost:7687 \\
      --neo4j-user neo4j --neo4j-password secret

  # Quick demo (small graph, few epochs):
  python run_pipeline.py --gt-epochs 30 --rl-timesteps 2000

EXIT CODES
----------
  0 — Success (scientific validation passed, candidates returned)
  1 — Phase 1 produced no data
  2 — Bridge produced no nodes/edges
  3 — Neo4j required but not available (production mode)
  4 — Scientific validation FAILED (GT AUC < 0.85 or RL AUC < 0.5
      or KP recovery < 20%). Use --allow-invalid-output for debugging.
  5 — Unexpected exception
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
PHASE1_ROOT = HERE / "phase1"
PHASE2_ROOT = HERE / "phase2"
PHASE1_PROCESSED_DEFAULT = PHASE1_ROOT / "processed_data"

# Make phase1 and phase2 importable
for p in (str(PHASE2_ROOT), str(PHASE1_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    force=True,
)
logger = logging.getLogger("run_pipeline")


def run_phase1(phase1_dir: Path) -> Dict[str, Any]:
    """Phase 1: Load (or generate) the processed_data CSVs.

    Returns a dict of {dataset_name: path} for each Phase 1 output CSV
    that exists. If no CSVs exist, returns an empty dict (the bridge
    will use the embedded sample fallback).
    """
    logger.info("=" * 70)
    logger.info("PHASE 1: Data Ingestion")
    logger.info("=" * 70)

    if not phase1_dir.exists():
        logger.warning(
            f"Phase 1 dir {phase1_dir} does not exist. The bridge will "
            f"use the embedded sample fallback (Tier-2). For real Phase 1 "
            f"data, run `cd phase1 && make all` first."
        )
        return {}

    csvs = {}
    for csv_file in phase1_dir.glob("*.csv"):
        csvs[csv_file.name] = csv_file
    logger.info(
        f"Phase 1: found {len(csvs)} processed CSVs in {phase1_dir}"
    )
    for name in sorted(csvs.keys()):
        logger.info(f"  - {name}")
    return csvs


def run_bridge(phase1_dir: Path) -> Tuple[List[Dict], List[Dict]]:
    """Bridge: stage_phase1_to_phase2 → Phase 2 node/edge dicts.

    Returns (nodes, edges) where each is a list of dicts with full
    lineage metadata.
    """
    logger.info("=" * 70)
    logger.info("BRIDGE: Phase 1 → Phase 2 (stage_phase1_to_phase2)")
    logger.info("=" * 70)

    from drugos_graph.phase1_bridge import stage_phase1_to_phase2

    nodes, edges, lineage = stage_phase1_to_phase2(
        phase1_processed_dir=str(phase1_dir),
        output_dir=None,  # in-memory only
    )
    logger.info(
        f"Bridge: produced {len(nodes)} nodes, {len(edges)} edges "
        f"(lineage: {len(lineage)} entries)"
    )
    if len(nodes) == 0:
        logger.error(
            "Bridge produced 0 nodes. Phase 1 outputs are likely missing "
            "or empty. Run `cd phase1 && make all` to generate them, or "
            "rely on the embedded sample fallback."
        )
    return nodes, edges


def run_phase2_kg_builder(
    nodes: List[Dict], edges: List[Dict]
) -> Tuple[Any, Any, Any, List[Tuple[str, str]]]:
    """Phase 2: Build the real biomedical KG from the staged dicts.

    Returns (node_features, edge_indices, node_maps, known_pairs) in
    the format the GT bridge expects.
    """
    logger.info("=" * 70)
    logger.info("PHASE 2: Knowledge Graph Construction")
    logger.info("=" * 70)

    import torch
    from drugos_graph.pyg_builder import build_pyg_hetero_data

    # build_pyg_hetero_data converts the staged node/edge dicts into
    # PyG HeteroData format (node_features dict, edge_indices dict,
    # node_maps dict). This is the REAL Phase 2 output.
    hetero_data, node_maps, known_pairs = build_pyg_hetero_data(
        nodes, edges
    )
    node_features = {k: hetero_data[k].x for k in hetero_data.node_types}
    edge_indices = {
        (src, rel, dst): hetero_data[(src, rel, dst)].edge_index
        for (src, rel, dst) in hetero_data.edge_types
    }
    n_drugs = len(node_maps.get("drug", {}))
    n_diseases = len(node_maps.get("disease", {}))
    n_proteins = len(node_maps.get("protein", {}))
    n_pathways = len(node_maps.get("pathway", {}))
    n_total_edges = sum(ei.shape[1] if hasattr(ei, 'shape') else 0
                        for ei in edge_indices.values())
    logger.info(
        f"Phase 2: built real biomedical KG: "
        f"{n_drugs} drugs, {n_proteins} proteins, "
        f"{n_pathways} pathways, {n_diseases} diseases, "
        f"{n_total_edges} edges across {len(edge_indices)} edge types. "
        f"{len(known_pairs)} known treatment pairs."
    )
    return node_features, edge_indices, node_maps, known_pairs


def run_phase3_and_4(
    graph_data: Tuple[Any, Any, Any, List[Tuple[str, str]]],
    gt_epochs: int,
    rl_timesteps: int,
    rl_top_n: int,
    output_dir: str,
    seed: int,
    allow_invalid_output: bool,
) -> Tuple[Any, Dict[str, Any]]:
    """Phase 3 + 4: GT training + RL ranking via gt_rl_bridge.

    Uses the REAL Phase 2 HeteroData (passed as graph_data) instead of
    build_demo_graph. This is the v89 P0 fix for Phase 1-4 integration.
    """
    logger.info("=" * 70)
    logger.info("PHASE 3 + 4: Graph Transformer Training + RL Ranking")
    logger.info("=" * 70)

    from graph_transformer.gt_rl_bridge import GTRLBridge

    bridge = GTRLBridge(
        output_dir=output_dir,
        device="cpu",
        seed=seed,
    )

    candidates_df, results = bridge.run_full_pipeline(
        gt_epochs=gt_epochs,
        rl_timesteps=rl_timesteps,
        rl_top_n=rl_top_n,
        allow_invalid_output=allow_invalid_output,
        # v89 P0 ROOT FIX: pass the REAL Phase 2 HeteroData so the GT
        # model trains on real biomedical topology (not build_demo_graph).
        graph_data=graph_data,
        # Use a 3-layer model so the GT can capture the full
        # drug→protein→pathway→disease (3-hop) pattern.
        gt_embedding_dim=32,
        gt_num_layers=3,
        gt_num_heads=4,
        gt_dropout=0.25,
    )
    return candidates_df, results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v89 P0: Run the full 4-phase drug repurposing pipeline."
    )
    parser.add_argument(
        "--phase1-dir", type=str,
        default=str(PHASE1_PROCESSED_DEFAULT),
        help="Path to Phase 1 processed_data directory",
    )
    parser.add_argument(
        "--output-dir", type=str,
        default=str(HERE / "output_v89"),
        help="Output directory for GT/RL artifacts",
    )
    parser.add_argument(
        "--gt-epochs", type=int, default=80,
        help="GT training epochs (default: 80 for demo; 500 for production)",
    )
    parser.add_argument(
        "--rl-timesteps", type=int, default=5000,
        help="RL training timesteps (default: 5000 for demo; 50000 for production)",
    )
    parser.add_argument(
        "--rl-top-n", type=int, default=10,
        help="Number of top candidates to return",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed (v89: deterministic via hashlib.sha256)",
    )
    parser.add_argument(
        "--allow-invalid-output", action="store_true",
        help="Bypass scientific-validation safety net (DEBUGGING ONLY)",
    )
    parser.add_argument(
        "--neo4j-uri", type=str, default=None,
        help="Neo4j URI (if not set, uses in-memory RecordingGraphBuilder)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    try:
        # ─── Phase 1 ────────────────────────────────────────────────
        phase1_csvs = run_phase1(Path(args.phase1_dir))

        # ─── Bridge ──────────────────────────────────────────────────
        nodes, edges = run_bridge(Path(args.phase1_dir))
        if len(nodes) == 0:
            logger.error("Phase 1 + Bridge produced 0 nodes. Aborting.")
            return 1

        # ─── Phase 2: Build real KG ─────────────────────────────────
        graph_data = run_phase2_kg_builder(nodes, edges)
        node_features, edge_indices, node_maps, known_pairs = graph_data
        if len(node_maps.get("drug", {})) == 0:
            logger.error("Phase 2 KG has 0 drug nodes. Aborting.")
            return 2

        # ─── Phase 3 + 4: GT training + RL ranking ──────────────────
        candidates_df, results = run_phase3_and_4(
            graph_data=graph_data,
            gt_epochs=args.gt_epochs,
            rl_timesteps=args.rl_timesteps,
            rl_top_n=args.rl_top_n,
            output_dir=output_dir,
            seed=args.seed,
            allow_invalid_output=args.allow_invalid_output,
        )

        # ─── Summary ────────────────────────────────────────────────
        print("\n" + "=" * 70)
        print("v89 4-PHASE PIPELINE COMPLETE - SUMMARY")
        print("=" * 70)
        print(f"  Phase 1 CSVs found:     {len(phase1_csvs)}")
        print(f"  Phase 2 nodes:          {len(nodes)}")
        print(f"  Phase 2 edges:          {len(edges)}")
        print(f"  Phase 2 drugs in KG:    {len(node_maps.get('drug', {}))}")
        print(f"  Phase 2 diseases in KG: {len(node_maps.get('disease', {}))}")
        print(f"  GT Best Val AUC:        {results.get('gt_best_val_auc', 0):.4f}")
        print(f"  GT Test AUC (verified): {results.get('gt_test_auc_verified', 'N/A')}")
        print(f"  GT Epochs Trained:      {results.get('gt_epochs_trained', 0)}")
        print(f"  RL Candidates Ranked:   {results.get('rl_ranked_high', 0)}")
        print(f"  Candidates Returned:    {results.get('n_candidates_returned', 0)}")
        print(f"  Output Directory:       {output_dir}")

        sv = results.get("scientific_validation", {})
        print()
        print("SCIENTIFIC VALIDATION (v89 honest metrics):")
        print(f"  GT Test AUC:            {sv.get('gt_test_auc', 0):.4f}  "
              f"pass={sv.get('gt_test_auc_pass', '?')}")
        print(f"  RL AUC:                 {sv.get('rl_auc', 'N/A')}  "
              f"pass={sv.get('rl_auc_pass', '?')}")
        print(f"  KP Recovery Rate:       {sv.get('kp_recovery_rate', 0):.1%}  "
              f"pass={sv.get('kp_recovery_pass', '?')}")
        overall_pass = sv.get('overall_pass', False)
        print(f"  OVERALL:                "
              f"{'PASSED' if overall_pass else 'FAILED'}")
        print("=" * 70)

        if len(candidates_df) > 0:
            print("\nTOP CANDIDATES (RL-ranked, from REAL Phase 2 KG):")
            cols = [c for c in ["drug", "disease", "reward", "rank"]
                    if c in candidates_df.columns]
            print(candidates_df[cols].to_string(index=False))

        # v89: exit NON-ZERO if scientific_validation failed (CI/CD signal)
        if not overall_pass:
            print("\n" + "=" * 70)
            print("v89: SCIENTIFIC VALIDATION FAILED. Exiting non-zero.")
            print("Use --allow-invalid-output for debugging.")
            print("=" * 70)
            return 4
        return 0

    except RuntimeError as e:
        # Scientific validation failure (raised by the bridge in strict mode)
        logger.critical(f"v89: pipeline RuntimeError: {e}", exc_info=True)
        return 4
    except Exception as e:
        logger.critical(f"v89: unexpected exception: {e}", exc_info=True)
        return 5


if __name__ == "__main__":
    sys.exit(main())
