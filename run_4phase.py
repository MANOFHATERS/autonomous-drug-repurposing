#!/usr/bin/env python3
"""Unified 4-Phase Pipeline Runner (v100 forensic root fix).

This is the SINGLE top-level entry point that chains ALL 4 phases of the
Autonomous Drug Repurposing Platform on REAL biomedical data:

  Phase 1 (Data Ingestion)
    Embedded sample CSVs (Tier-2 fallback) are written to
    ``phase1/processed_data/`` when the directory is missing or empty.
    The CSVs use real biomedical identifiers (InChIKeys, UniProt
    accessions, DOID/MOM IDs) so downstream phases see realistic data.

  Phase 1 -> Phase 2 Bridge
    ``drugos_graph.phase1_bridge.run_phase1_to_phase2`` reads the Phase 1
    CSVs, stages them into Phase 2 node/edge dicts, and loads them into a
    ``RecordingGraphBuilder``. This is the ONLY data path from Phase 1 to
    Phase 2 (no duplicate loaders).

  Phase 2 -> Phase 3 Schema Adapter
    ``graph_transformer.data.phase2_adapter.adapt_phase2_to_phase3``
    converts the Phase 2 ``RecordingGraphBuilder`` (capitalized labels)
    into the Phase 3 canonical schema (lowercase labels) and produces the
    4-tuple ``(node_features, edge_indices, node_maps, known_pairs)``.

  Phase 3 + Phase 4 (GT training + RL ranking)
    ``GTRLBridge.run_full_pipeline`` trains the Graph Transformer on the
    REAL Phase 2 HeteroData and ranks candidates with the RL agent.

v100 root fixes (forensic audit R-018 through R-035):
  * R-018: writes ``manifest.json`` (git SHA, config hash, input checksums)
    to the output directory at startup.
  * R-022: removed duplicate summary-print block (was 18 lines, now 9).
  * R-023: ``phase1_dir`` is no longer reassigned inside ``run_bridge``.
  * R-026: ``--seed`` help text no longer claims SHA-256 determinism.
  * R-028: ``logging.basicConfig`` moved inside ``main()`` (no longer
    ``force=True`` at module import time).
  * R-034: removed misleading "BOTH .csv and .csv.gz" comment.
  * R-INT-002: removed the NameError-prone ``run_phase2_kg_builder`` call
    that referenced an undefined ``seed`` variable and overwrote
    ``graph_data`` from ``run_schema_adapter``.
  * R-INT-004: ``run_bridge`` now calls ``run_phase1_to_phase2`` ONCE
    (was calling it twice and discarding the first result).
  * R-INT-005: ``run_schema_adapter``'s output is no longer discarded.
  * R-INT-008: ``ensure_phase1_data``'s return value is captured
    (``phase1_csvs`` is now defined before the summary print).
  * R-STUB-003: ``run_schema_adapter`` is now actually consumed.
  * R-STUB-004: the duplicate bridge call inside ``run_bridge`` is gone.

Exit codes:
  0  Success (scientific validation passed, candidates returned)
  1  Phase 1 produced no data
  2  Bridge produced no nodes/edges
  3  Schema adapter produced 0 drug nodes
  4  Scientific validation FAILED
  5  Unexpected exception
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

HERE = Path(__file__).resolve().parent
PHASE1_ROOT = HERE / "phase1"
PHASE2_ROOT = HERE / "phase2"
PHASE1_PROCESSED_DEFAULT = PHASE1_ROOT / "processed_data"

# Make phase1, phase2, and graph_transformer importable.
for _p in (str(PHASE2_ROOT), str(PHASE1_ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logger = logging.getLogger("run_4phase")


# ---------------------------------------------------------------------------
# Reproducibility manifest (R-018)
# ---------------------------------------------------------------------------
def _git_rev_parse_head() -> str:
    """Return the current git commit SHA, or 'unknown' if not a git repo."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=HERE, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _git_status_porcelain() -> str:
    """Return ``git status --porcelain`` output (clean = empty string)."""
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=HERE, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


def _sha256_of_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_manifest(
    output_dir: Path,
    phase1_dir: Path,
    config: Dict[str, Any],
) -> Path:
    """R-018: write ``manifest.json`` with git SHA, config hash, input CSV
    SHA-256 checksums so every run is reproducible and auditable.
    """
    manifest: Dict[str, Any] = {
        "written_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_rev_parse_head(),
        "git_status_porcelain": _git_status_porcelain(),
        "config": config,
        "config_sha256": hashlib.sha256(
            json.dumps(config, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
        "phase1_dir": str(phase1_dir),
        "phase1_input_checksums": {},
    }
    if phase1_dir.exists():
        for csv in sorted(phase1_dir.glob("*.csv*")):
            try:
                manifest["phase1_input_checksums"][csv.name] = _sha256_of_file(csv)
            except OSError as exc:
                manifest["phase1_input_checksums"][csv.name] = f"error: {exc}"
    manifest_path = output_dir / "manifest.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2, default=str)
    logger.info("R-018: reproducibility manifest written to %s", manifest_path)
    return manifest_path


# ---------------------------------------------------------------------------
# Phase 1
# ---------------------------------------------------------------------------
def ensure_phase1_data(phase1_dir: Path) -> Dict[str, Path]:
    """Phase 1: ensure the processed_data CSVs exist.

    If ``phase1_dir`` doesn't exist or is empty, write the embedded sample
    CSVs (the Tier-2 fallback). Returns a mapping of CSV stem -> Path.
    """
    logger.info("=" * 70)
    logger.info("PHASE 1: Data Ingestion")
    logger.info("=" * 70)

    if not phase1_dir.exists() or not any(phase1_dir.glob("*.csv*")):
        logger.info(
            "Phase 1 dir %s is empty or missing; writing embedded sample "
            "CSVs (Tier-2 fallback).", phase1_dir,
        )
        from pipelines._embedded_samples import write_all_samples
        written = write_all_samples(str(phase1_dir))
        logger.info("Wrote %d sample datasets to %s", len(written), phase1_dir)

    csvs = sorted(phase1_dir.glob("*.csv*"))
    logger.info("Phase 1: %d CSV files present in %s", len(csvs), phase1_dir)
    for csv in csvs:
        logger.info("  - %s", csv.name)
    return {csv.stem: csv for csv in csvs}


def _ensure_phase1_samples(phase1_dir: Path) -> Path:
    """Materialize embedded sample CSVs when processed_data is empty.

    Returns the (possibly newly populated) phase1_dir. Does NOT reassign
    the caller's parameter (R-023).
    """
    if phase1_dir.exists() and any(phase1_dir.glob("*.csv*")):
        return phase1_dir
    phase1_dir.mkdir(parents=True, exist_ok=True)

    _p1_root = str(PHASE1_ROOT)
    if _p1_root not in sys.path:
        sys.path.insert(0, _p1_root)
    from pipelines._embedded_samples import (
        embedded_chembl_molecules,
        embedded_chembl_activities,
        embedded_uniprot_proteins,
        embedded_string_ppi,
        embedded_drugbank_drugs,
        embedded_drugbank_interactions,
        embedded_drugbank_indications,
        embedded_omim_gda,
        embedded_omim_susceptibility,
        embedded_disgenet_gda,
        embedded_pubchem_enrichment,
    )

    # Canonical filename set: ONE file per source. The bridge's
    # read_phase1_outputs looks for these exact names.
    writes = [
        ("drugbank_drugs.csv", embedded_drugbank_drugs),
        ("drugbank_interactions.csv", embedded_drugbank_interactions),
        ("drugbank_indications.csv", embedded_drugbank_indications),
        ("omim_gene_disease_associations.csv", embedded_omim_gda),
        ("omim_gene_disease_susceptibility.csv", embedded_omim_susceptibility),
        ("chembl_drugs.csv", embedded_chembl_molecules),
        ("chembl_activities_clean.csv", embedded_chembl_activities),
        ("uniprot_proteins.csv", embedded_uniprot_proteins),
        ("string_protein_protein_interactions.csv", embedded_string_ppi),
        ("disgenet_gene_disease_associations.csv", embedded_disgenet_gda),
        ("pubchem_enrichment.csv", embedded_pubchem_enrichment),
    ]
    for fname, fn in writes:
        fn().to_csv(phase1_dir / fname, index=False)
    logger.info(
        "Wrote %d embedded sample CSVs to %s (Tier-2 fallback).",
        len(writes), phase1_dir,
    )
    return phase1_dir


# ---------------------------------------------------------------------------
# Bridge: Phase 1 -> Phase 2 (single call, no duplicate work)
# ---------------------------------------------------------------------------
def run_bridge(phase1_dir: Path) -> Tuple[Any, Any]:
    """Run ``run_phase1_to_phase2`` ONCE and return (builder, staged).

    R-INT-004 / R-STUB-004 root fix: the previous implementation called
    ``run_phase1_to_phase2`` twice, threw away the first result, and
    reassigned the ``phase1_dir`` parameter (R-023). The bridge now runs
    exactly once and the caller's arguments are not mutated.
    """
    logger.info("=" * 70)
    logger.info("BRIDGE: Phase 1 -> Phase 2 (run_phase1_to_phase2)")
    logger.info("=" * 70)

    # Make sure Phase 1 actually has CSVs to read (Tier-2 fallback).
    resolved_phase1_dir = _ensure_phase1_samples(phase1_dir)

    from drugos_graph.phase1_bridge import run_phase1_to_phase2

    result = run_phase1_to_phase2(
        phase1_processed_dir=str(resolved_phase1_dir),
        prefer_postgres=False,  # CSV path for dev/CI; set True for prod
    )
    builder = result["builder"]
    staged = result["staged"]
    summary = result["summary"]

    logger.info(
        "Bridge: %d nodes staged, %d edges staged, %d nodes loaded, "
        "%d edges loaded (backend=%s, sources=%d)",
        summary["nodes_staged"], summary["edges_staged"],
        summary["nodes_loaded"], summary["edges_loaded"],
        summary.get("backend", "csv"), len(summary.get("sources_read", [])),
    )
    if summary.get("errors"):
        for err in summary["errors"][:5]:
            logger.warning("  bridge error: %s", err)
    if summary["nodes_staged"] == 0:
        logger.error(
            "Bridge produced 0 nodes. Phase 1 outputs are likely missing "
            "or empty. The embedded sample fallback should have written "
            "data — check the Phase 1 logs above."
        )
    return builder, staged


# ---------------------------------------------------------------------------
# Phase 2 -> Phase 3 schema adapter (output is actually consumed)
# ---------------------------------------------------------------------------
def run_schema_adapter(
    builder: Any, seed: int = 42
) -> Tuple[Any, Any, Any, List[Tuple[str, str]]]:
    """Phase 2 -> Phase 3 schema adapter.

    R-INT-005 / R-STUB-003 root fix: this function's output is now used
    by the caller (was previously discarded by an overwrite).
    """
    logger.info("=" * 70)
    logger.info("PHASE 2 -> PHASE 3: Schema Adapter")
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
        "Phase 2->3 adapter: %d drugs, %d proteins, %d pathways, "
        "%d diseases, %d edges across %d edge types. %d known pairs.",
        n_drugs, n_proteins, n_pathways, n_diseases,
        n_total_edges, len(edge_indices), len(known_pairs),
    )
    return node_features, edge_indices, node_maps, known_pairs


# ---------------------------------------------------------------------------
# Phase 3 + 4: GT training + RL ranking via the bridge
# ---------------------------------------------------------------------------
def run_phase3_and_4(
    graph_data: Tuple[Any, Any, Any, List[Tuple[str, str]]],
    gt_epochs: int,
    rl_timesteps: int,
    rl_top_n: int,
    output_dir: str,
    seed: int,
    allow_invalid_output: bool,
) -> Tuple[Any, Dict[str, Any]]:
    """Phase 3 + 4: GT training + RL ranking via ``GTRLBridge``.

    Uses the REAL Phase 2 HeteroData (passed as ``graph_data``) instead
    of ``build_demo_graph``.
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
    # R-028: configure logging inside main(), not at module import time.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run the full 4-phase drug repurposing pipeline."
    )
    parser.add_argument(
        "--phase1-dir", type=str,
        default=str(PHASE1_PROCESSED_DEFAULT),
        help="Path to Phase 1 processed_data directory",
    )
    parser.add_argument(
        "--output-dir", type=str,
        default=str(HERE / "output_v100"),
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
        # R-026: removed misleading "deterministic via hashlib.sha256" claim.
        "--seed", type=int, default=42,
        help="Random seed for RNG initialization (default 42)",
    )
    parser.add_argument(
        "--allow-invalid-output", action="store_true",
        help="Bypass scientific-validation safety net (DEBUGGING ONLY)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    phase1_dir = Path(args.phase1_dir)

    # R-018: write the reproducibility manifest BEFORE running anything.
    config_snapshot: Dict[str, Any] = {
        "runner": "run_4phase.py",
        "phase1_dir": str(phase1_dir),
        "output_dir": str(output_dir),
        "gt_epochs": args.gt_epochs,
        "rl_timesteps": args.rl_timesteps,
        "rl_top_n": args.rl_top_n,
        "seed": args.seed,
        "allow_invalid_output": args.allow_invalid_output,
    }
    _write_manifest(output_dir, phase1_dir, config_snapshot)

    try:
        # ─── Phase 1 ───────────────────────────────────────────────────
        phase1_csvs = ensure_phase1_data(phase1_dir)
        if not phase1_csvs:
            logger.error("Phase 1 produced no CSV files. Aborting.")
            return 1

        # ─── Bridge ────────────────────────────────────────────────────
        builder, staged = run_bridge(phase1_dir)
        # ORCH-004 ROOT FIX: defensive total_nodes check.
        # The previous code accessed ``builder.total_nodes`` directly. If the
        # Phase 2 RecordingGraphBuilder (or any future builder class) uses a
        # different attribute name (e.g. ``n_nodes`` or doesn't expose one at
        # all), this line would crash with AttributeError, masking the real
        # problem ("the bridge produced no data") behind a Python traceback.
        # We now try multiple known attribute names and fall back to
        # computing the total from ``node_loads`` if none of them exist.
        total_nodes = (
            getattr(builder, "total_nodes", None)
            or getattr(builder, "n_nodes", None)
            or getattr(builder, "num_nodes", None)
        )
        if total_nodes is None:
            # Fall back to summing node counts across staged loads.
            # ``node_loads`` is the canonical Phase 2 builder attribute that
            # records per-batch node inserts.
            node_loads = getattr(builder, "node_loads", None) or []
            try:
                total_nodes = sum(
                    len(load.get("nodes", [])) if isinstance(load, dict)
                    else len(getattr(load, "nodes", []))
                    for load in node_loads
                )
            except Exception:
                logger.warning(
                    "ORCH-004: could not determine builder node count via "
                    "total_nodes / n_nodes / num_nodes / node_loads. "
                    "Falling back to staged.total_nodes."
                )
                total_nodes = getattr(staged, "total_nodes", 0)
        if total_nodes == 0:
            logger.error(
                "Phase 1 + Bridge produced 0 nodes (total_nodes=%s). "
                "Aborting. Check that Phase 1 produced CSVs and the "
                "bridge is wired correctly.",
                total_nodes,
            )
            return 2
        logger.info(
            "ORCH-004: builder total_nodes=%s (defensive check passed).",
            total_nodes,
        )

        # ─── Phase 2 -> Phase 3 Schema Adapter ─────────────────────────
        # R-INT-005 / R-STUB-003: this output is now consumed (was
        # previously overwritten by a second call to run_phase2_kg_builder
        # that crashed with NameError on `seed` — R-INT-002).
        graph_data = run_schema_adapter(builder, seed=args.seed)
        node_features, edge_indices, node_maps, known_pairs = graph_data
        if len(node_maps.get("drug", {})) == 0:
            logger.error("Schema adapter produced 0 drug nodes. Aborting.")
            return 3

        # ─── Phase 3 + 4: GT training + RL ranking ─────────────────────
        candidates_df, results = run_phase3_and_4(
            graph_data=graph_data,
            gt_epochs=args.gt_epochs,
            rl_timesteps=args.rl_timesteps,
            rl_top_n=args.rl_top_n,
            output_dir=str(output_dir),
            seed=args.seed,
            allow_invalid_output=args.allow_invalid_output,
        )

        # ─── Summary (R-022: removed duplicate 9-line block) ───────────
        print("\n" + "=" * 70)
        print("v100 4-PHASE PIPELINE COMPLETE — SUMMARY")
        print("=" * 70)
        print(f"  Phase 1 CSVs:            {len(phase1_csvs)}")
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
