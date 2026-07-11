#!/usr/bin/env python3
"""
v90 REAL ROOT FIX — Unified 4-Phase Pipeline Runner
=====================================================

This is the SINGLE top-level entry point that chains ALL 4 phases of
the Autonomous Drug Repurposing Platform. Unlike the previous v89
version which called FICTIONAL APIs (``build_pyg_hetero_data`` does
NOT EXIST; ``stage_phase1_to_phase2`` was called with wrong kwargs),
this version uses the REAL bridge API verified by actually running
the code end-to-end.

Pipeline flow:
  Phase 1 (Data Ingestion)
    → ``python -m pipelines samples`` writes embedded sample CSVs to
       ``phase1/processed_data/`` (or real Phase 1 data if present).

  Bridge (phase1_bridge.run_phase1_to_phase2)
    → reads Phase 1 CSVs, stages them into Phase 2 node/edge dicts,
       loads into a RecordingGraphBuilder. This is the ONLY data path
       from Phase 1 to Phase 2 (no duplicate loaders).

  Phase 2 → Phase 3 Schema Adapter (graph_transformer.data.phase2_adapter)
    → converts the Phase 2 RecordingGraphBuilder output (capitalized
       Compound/Protein/Disease/Pathway/ClinicalOutcome/Gene labels)
       into the Phase 3 canonical schema (lowercase drug/protein/
       disease/pathway/clinical_outcome). Derives (pathway, disrupted_in,
       disease) edges from Gene→Disease + gene_symbol→Protein +
       Protein→Pathway. Normalizes drug/disease names to match the RL
       ranker's KNOWN_POSITIVES vocabulary.

  Phase 3 (Graph Transformer Training)
    → GraphTransformerTrainer on the REAL Phase 2 HeteroData (NOT
       build_demo_graph). The GT model trains on real biomedical
       topology from DrugBank + UniProt + STRING + DisGeNET + OMIM.

  Phase 4 (RL Hypothesis Ranking)
    → RL ranker via gt_rl_bridge. The RL agent ranks the top-N
       drug-disease repurposing candidates.

USAGE
-----
  # Full 4-phase pipeline (in-memory KG, no Neo4j required):
  python run_pipeline.py

  # Quick demo (small graph, few epochs):
  python run_pipeline.py --gt-epochs 30 --rl-timesteps 2000

  # Skip scientific-validation gate (DEBUGGING ONLY):
  python run_pipeline.py --allow-invalid-output

EXIT CODES
----------
  0 — Success (scientific validation passed, candidates returned)
  1 — Phase 1 produced no data
  2 — Bridge produced no nodes/edges
  3 — Schema adapter produced 0 drug nodes
  4 — Scientific validation FAILED
  5 — Unexpected exception
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
PHASE1_ROOT = HERE / "phase1"
PHASE2_ROOT = HERE / "phase2"
PHASE1_PROCESSED_DEFAULT = PHASE1_ROOT / "processed_data"

# Make phase1, phase2, and graph_transformer importable
for p in (str(PHASE2_ROOT), str(PHASE1_ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    force=True,
)
logger = logging.getLogger("run_pipeline")


def ensure_phase1_data(phase1_dir: Path) -> Dict[str, Path]:
    """Phase 1: ensure the processed_data CSVs exist.

    If ``phase1_dir`` doesn't exist or is empty, write the embedded
    sample CSVs (the Tier-2 fallback). This guarantees the pipeline
    always has Phase 1 data to work with, even on a fresh clone.
    """
    logger.info("=" * 70)
    logger.info("PHASE 1: Data Ingestion")
    logger.info("=" * 70)

    if not phase1_dir.exists() or not any(phase1_dir.glob("*.csv")):
        logger.info(
            f"Phase 1 dir {phase1_dir} is empty or missing. "
            f"Writing embedded sample CSVs (Tier-2 fallback)."
        )
        # Import Phase 1 pipelines to write samples
        from pipelines._embedded_samples import write_all_samples
        written = write_all_samples(str(phase1_dir))
        logger.info(f"Wrote {len(written)} sample datasets to {phase1_dir}")

    csvs = sorted(phase1_dir.glob("*.csv*"))
    logger.info(f"Phase 1: {len(csvs)} CSV files present in {phase1_dir}")
    for csv in csvs:
        logger.info(f"  - {csv.name}")
    return {csv.stem: csv for csv in csvs}


def run_bridge(phase1_dir: Path) -> Tuple[Any, Any]:
    """Bridge: run_phase1_to_phase2 → RecordingGraphBuilder + staged data.

    Uses the REAL bridge API (not the fictional stage_phase1_to_phase2
    call with output_dir= kwarg that crashed in v89).

    Returns (builder, staged) where builder is a populated
    RecordingGraphBuilder and staged is the Phase1StagedData.
    """
    logger.info("=" * 70)
    logger.info("BRIDGE: Phase 1 → Phase 2 (run_phase1_to_phase2)")
    logger.info("=" * 70)

    from drugos_graph.phase1_bridge import run_phase1_to_phase2

    result = run_phase1_to_phase2(
        phase1_processed_dir=str(phase1_dir),
        prefer_postgres=False,  # CSV path (no Postgres in dev/CI)
    )
    builder = result["builder"]
    staged = result["staged"]
    summary = result["summary"]
    backend = result["backend"]

    logger.info(
        f"Bridge: backend={backend}, "
        f"nodes_staged={summary['nodes_staged']}, "
        f"edges_staged={summary['edges_staged']}, "
        f"nodes_loaded={summary['nodes_loaded']}, "
        f"edges_loaded={summary['edges_loaded']}, "
        f"sources_read={len(summary['sources_read'])}"
    )

    if summary["nodes_staged"] == 0:
        logger.error(
            "Bridge produced 0 nodes. Phase 1 outputs are likely missing "
            "or empty. The embedded sample fallback should have written "
            "data — check the Phase 1 logs above."
        )
    return builder, staged


def run_schema_adapter(
    builder: Any, seed: int = 42
) -> Tuple[Any, Any, Any, List[Tuple[str, str]]]:
    """Phase 2 → Phase 3 schema adapter.

    Converts the Phase 2 RecordingGraphBuilder (capitalized labels) into
    the Phase 3 canonical schema (lowercase labels) via
    ``adapt_phase2_to_phase3``. This is the REAL integration point that
    the v89 run_pipeline.py was missing (it called a non-existent
    ``build_pyg_hetero_data`` function).
    """
    logger.info("=" * 70)
    logger.info("PHASE 2 → PHASE 3: Schema Adapter")
    logger.info("=" * 70)

    from graph_transformer.data.phase2_adapter import adapt_phase2_to_phase3

    node_features, edge_indices, node_maps, known_pairs = adapt_phase2_to_phase3(
        builder, seed=seed
    )

    n_drugs = len(node_maps.get("drug", {}))
    n_diseases = len(node_maps.get("disease", {}))
    n_proteins = len(node_maps.get("protein", {}))
    n_pathways = len(node_maps.get("pathway", {}))
    n_total_edges = sum(
        ei.shape[1] if hasattr(ei, "shape") else 0
        for ei in edge_indices.values()
    )
    logger.info(
        f"Phase 2→3 adapter: {n_drugs} drugs, {n_proteins} proteins, "
        f"{n_pathways} pathways, {n_diseases} diseases, "
        f"{n_total_edges} edges across {len(edge_indices)} edge types. "
        f"{len(known_pairs)} known treatment pairs."
    )

    if n_drugs == 0:
        logger.error("Schema adapter produced 0 drug nodes. Aborting.")
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
    build_demo_graph.
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
        graph_data=graph_data,
    )
    return candidates_df, results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="v90 REAL: Run the full 4-phase drug repurposing pipeline."
    )
    parser.add_argument(
        "--phase1-dir", type=str,
        default=str(PHASE1_PROCESSED_DEFAULT),
        help="Path to Phase 1 processed_data directory",
    )
    parser.add_argument(
        "--output-dir", type=str,
        default=str(HERE / "output_v90"),
        help="Output directory for GT/RL artifacts",
    )
    parser.add_argument(
        "--gt-epochs", type=int, default=80,
        help="GT training epochs (default: 80 for demo; 500 for production)",
    )
    parser.add_argument(
        "--rl-timesteps", type=int, default=5000,
        help="RL training timesteps (default: 5000)",
    )
    parser.add_argument(
        "--rl-top-n", type=int, default=10,
        help="Number of top candidates to return",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed (deterministic via hashlib.sha256)",
    )
    parser.add_argument(
        "--allow-invalid-output", action="store_true",
        help="Bypass scientific-validation safety net (DEBUGGING ONLY)",
    )
    args = parser.parse_args()

    output_dir = args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    try:
        # ─── Phase 1 ────────────────────────────────────────────────
        ensure_phase1_data(Path(args.phase1_dir))

        # ─── Bridge ──────────────────────────────────────────────────
        builder, staged = run_bridge(Path(args.phase1_dir))
        if builder.total_nodes == 0:
            logger.error("Phase 1 + Bridge produced 0 nodes. Aborting.")
            return 1

        # ─── Phase 2 → Phase 3 Schema Adapter ────────────────────────
        graph_data = run_schema_adapter(builder, seed=args.seed)
        node_features, edge_indices, node_maps, known_pairs = graph_data
        if len(node_maps.get("drug", {})) == 0:
            logger.error("Schema adapter produced 0 drug nodes. Aborting.")
            return 3

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
        print("v90 4-PHASE PIPELINE COMPLETE - SUMMARY")
        print("=" * 70)
        print(f"  Phase 2 nodes (staged):  {staged.total_nodes}")
        print(f"  Phase 2 edges (staged):  {staged.total_edges}")
        print(f"  Phase 3 drugs in KG:     {len(node_maps.get('drug', {}))}")
        print(f"  Phase 3 diseases in KG:  {len(node_maps.get('disease', {}))}")
        print(f"  Known treatment pairs:   {len(known_pairs)}")
        print(f"  GT Best Val AUC:         {results.get('gt_best_val_auc', 0):.4f}")
        print(f"  GT Test AUC (verified):  {results.get('gt_test_auc_verified', 'N/A')}")
        print(f"  GT Epochs Trained:       {results.get('gt_epochs_trained', 0)}")
        print(f"  RL Candidates Ranked:    {results.get('rl_ranked_high', 0)}")
        print(f"  Candidates Returned:     {results.get('n_candidates_returned', 0)}")
        print(f"  Output Directory:        {output_dir}")

        sv = results.get("scientific_validation", {})
        print()
        print("SCIENTIFIC VALIDATION:")
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

        if not overall_pass:
            print("\n" + "=" * 70)
            print("SCIENTIFIC VALIDATION FAILED. Exiting non-zero.")
            print("Use --allow-invalid-output for debugging.")
            print("=" * 70)
            return 4
        return 0

    except RuntimeError as e:
        logger.critical(f"Pipeline RuntimeError: {e}", exc_info=True)
        return 4
    except Exception as e:
        logger.critical(f"Unexpected exception: {e}", exc_info=True)
        return 5


if __name__ == "__main__":
    sys.exit(main())
