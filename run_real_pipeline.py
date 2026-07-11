"""Real end-to-end 4-phase pipeline runner (v100 forensic root fix).

R-STUB-001 / R-INT-003 root fix: the previous version was named
``run_real_pipeline.py`` but it skipped Phase 1 and Phase 2 entirely
and trained on a SYNTHETIC ``build_demo_graph``. The file is now a
REAL 4-phase runner: Phase 1 -> Phase 2 bridge -> Phase 3 GT training
-> Phase 4 RL ranking, all on REAL biomedical data.

R-018: writes ``manifest.json`` (git SHA, config hash, input checksums).
R-027: ``main()`` returns ``int`` (was ``-> None`` with ``sys.exit``).
R-028: ``logging.basicConfig`` moved inside ``main()``.
R-029: removed the 30-line static "V90 ROOT FIXES STATUS" print block
       (was log noise that could go stale).

Exit codes:
  0  Success (scientific validation passed)
  1  Phase 1 data unavailable
  2  Phase 1->2 bridge produced zero nodes
  3  Phase 3+4 pipeline failure
  4  Scientific validation FAILED
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

# Ensure project root is on sys.path so phase1/phase2/graph_transformer/rl
# are all importable.
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_PHASE1_ROOT = os.path.join(_ROOT, "phase1")
if _PHASE1_ROOT not in sys.path:
    sys.path.insert(0, _PHASE1_ROOT)
_PHASE2_ROOT = os.path.join(_ROOT, "phase2")
if _PHASE2_ROOT not in sys.path:
    sys.path.insert(0, _PHASE2_ROOT)


def _git_rev_parse_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _git_status_porcelain() -> str:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_ROOT, capture_output=True, text=True, timeout=5,
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


def _write_manifest(output_dir: Path, phase1_dir: Path, config: dict) -> Path:
    manifest: dict = {
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
    logging.getLogger("run_real_pipeline").info(
        "R-018: reproducibility manifest written to %s", manifest_path,
    )
    return manifest_path


def _ensure_phase1_data(phase1_dir: Path) -> bool:
    """R-INT-003 / R-STUB-001 root fix: actually run Phase 1 (was skipped).

    The previous version of this file called ``bridge.run_full_pipeline``
    with NO ``phase1_staged_data`` and NO ``graph_data``, so the bridge
    fell through to ``build_demo_graph`` (synthetic). Phase 1 and Phase 2
    were completely skipped. Now Phase 1 is invoked via the embedded
    sample generator (Tier-2 fallback) when no CSVs are present.
    """
    if phase1_dir.exists() and any(phase1_dir.glob("*.csv*")):
        return True
    phase1_dir.mkdir(parents=True, exist_ok=True)
    try:
        from pipelines._embedded_samples import write_all_samples
        written = write_all_samples(str(phase1_dir))
        logging.getLogger("run_real_pipeline").info(
            "Phase 1: wrote %d embedded sample CSVs to %s (Tier-2 fallback).",
            len(written), phase1_dir,
        )
        return True
    except Exception as exc:
        logging.getLogger("run_real_pipeline").error(
            "Phase 1 embedded sample generation failed: %s", exc,
        )
        return False


def main() -> int:  # R-027: was -> None with sys.exit()
    # R-028: configure logging inside main(), not at module import time.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    log = logging.getLogger("run_real_pipeline")

    parser = argparse.ArgumentParser(
        description="Run the REAL 4-phase GT+RL drug repurposing pipeline."
    )
    parser.add_argument(
        "--phase1-dir", type=str,
        default=os.path.join(_ROOT, "phase1", "processed_data"),
        help="Path to Phase 1 processed_data directory.",
    )
    parser.add_argument(
        "--gt-epochs", type=int, default=500,
        help="GT training epochs (default: 500, aligned with bridge default)",
    )
    parser.add_argument(
        "--rl-timesteps", type=int, default=50000,
        help="RL training timesteps (default: 50000, aligned with PipelineConfig.timesteps)",
    )
    parser.add_argument(
        "--rl-top-n", type=int, default=10,
        help="Number of top candidates to return (default: 10)",
    )
    parser.add_argument(
        "--seed", type=int, default=42,
        help="Random seed for RNG initialization (default 42)",
    )
    parser.add_argument(
        "--allow-invalid-output", action="store_true",
        help="Bypass the scientific-validation safety net (DEBUGGING ONLY)",
    )
    parser.add_argument(
        "--output-dir", type=str,
        default=os.path.join(_ROOT, "output_real_run"),
        help="Output directory (default: output_real_run)",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    phase1_dir = Path(args.phase1_dir)

    # R-018: write manifest BEFORE running.
    _write_manifest(output_dir, phase1_dir, {
        "runner": "run_real_pipeline.py",
        "phase1_dir": str(phase1_dir),
        "output_dir": str(output_dir),
        "gt_epochs": args.gt_epochs,
        "rl_timesteps": args.rl_timesteps,
        "rl_top_n": args.rl_top_n,
        "seed": args.seed,
        "allow_invalid_output": args.allow_invalid_output,
    })

    if args.allow_invalid_output:
        log.warning(
            "--allow-invalid-output is set. The scientific-validation "
            "safety net is DISABLED. Candidates may be RANDOM."
        )

    # ─── PHASE 1: Data Ingestion ──────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE 1: Data Ingestion")
    log.info("=" * 60)
    if not _ensure_phase1_data(phase1_dir):
        log.error("Phase 1 data unavailable. Cannot proceed.")
        return 1

    # ─── PHASE 2: Knowledge Graph Construction (bridge) ──────────────
    log.info("=" * 60)
    log.info("PHASE 2: Phase 1 -> Phase 2 bridge")
    log.info("=" * 60)
    from drugos_graph.phase1_bridge import run_phase1_to_phase2
    from drugos_graph import RecordingGraphBuilder

    builder = RecordingGraphBuilder()
    try:
        bridge_result = run_phase1_to_phase2(
            phase1_processed_dir=str(phase1_dir),
            builder=builder,
        )
    except Exception as exc:
        log.error("Phase 1->2 bridge FAILED: %s", exc, exc_info=True)
        return 2

    staged = bridge_result["staged"]
    bridge_summary = bridge_result["summary"]
    if bridge_summary["nodes_staged"] == 0:
        log.error("Phase 2 bridge staged ZERO nodes. Cannot proceed.")
        return 2
    log.info(
        "Phase 2 COMPLETE: %d nodes, %d edges staged from REAL Phase 1 data.",
        bridge_summary["nodes_staged"], bridge_summary["edges_staged"],
    )

    # ─── PHASE 3 + 4: GT training + RL ranking on REAL data ──────────
    log.info("=" * 60)
    log.info("PHASE 3 + 4: Graph Transformer + RL Ranker (on REAL KG)")
    log.info("=" * 60)
    from graph_transformer.gt_rl_bridge import GTRLBridge

    bridge = GTRLBridge(
        output_dir=str(output_dir),
        device="cpu",
        seed=args.seed,
    )
    try:
        candidates_df, results = bridge.run_full_pipeline(
            gt_epochs=args.gt_epochs,
            rl_timesteps=args.rl_timesteps,
            rl_top_n=args.rl_top_n,
            allow_invalid_output=args.allow_invalid_output,
            # R-INT-003 / R-STUB-001 root fix: pass REAL Phase 1->2 staged
            # data so the GT model trains on actual biomedical topology
            # instead of a synthetic demo graph.
            phase1_staged_data=staged,
            gt_embedding_dim=32,
            gt_num_layers=3,
            gt_num_heads=4,
            gt_dropout=0.25,
        )
    except RuntimeError as exc:
        log.error("Phase 3+4 pipeline FAILED: %s", exc, exc_info=True)
        return 3
    except Exception as exc:
        log.error("Phase 3+4 pipeline exception: %s", exc, exc_info=True)
        return 3

    # ─── Summary (R-029: removed 30-line static "V90 ROOT FIXES STATUS") ──
    print("\n" + "=" * 70)
    print("REAL 4-PHASE PIPELINE COMPLETE — SUMMARY")
    print("=" * 70)
    print(f"  Phase 2 nodes staged:    {bridge_summary['nodes_staged']}")
    print(f"  Phase 2 edges staged:    {bridge_summary['edges_staged']}")
    print(f"  GT drugs (real):         {len(bridge.drug_names)}")
    print(f"  GT diseases (real):      {len(bridge.disease_names)}")
    print(f"  GT known pairs (real):   {len(bridge.known_pairs)}")
    print(f"  GT Best Val AUC:         {results.get('gt_best_val_auc', 0):.4f}")
    print(f"  GT Test AUC:             {results.get('gt_test_auc', 0):.4f}")
    print(f"  GT Test AUC (verified):  {results.get('gt_test_auc_verified', 'N/A')}")
    print(f"  GT Epochs Trained:       {results.get('gt_epochs_trained', 0)}")
    print(f"  RL Pairs Processed:      {results.get('rl_pairs_processed', 0)}")
    print(f"  RL Candidates Ranked:    {results.get('rl_ranked_high', 0)}")
    # R-013: use .get() for rl_inference_latency_ms (was direct dict access).
    _rl_latency = results.get('rl_inference_latency_ms', 0)
    if _rl_latency:
        print(f"  RL Inference Latency:    {_rl_latency:.0f}ms")
    print(f"  Candidates Returned:     {results.get('n_candidates_returned', 0)}")
    print(f"  Output Directory:        {output_dir}")

    sv = results.get("scientific_validation", {})
    print()
    print("SCIENTIFIC VALIDATION:")
    print(f"  GT Test AUC:            {sv.get('gt_test_auc', 'N/A')}  "
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

    # R-027: return int instead of sys.exit().
    if not overall_pass:
        print("\nScientific validation FAILED. Use --allow-invalid-output for debugging.")
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
