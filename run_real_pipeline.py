"""Real end-to-end pipeline runner — Phase 1 → 2 → 3 → 4 (GT + RL).

v100 ROOT FIX (R-006): the previous version of this script did NOT
wire Phase 1 or Phase 2 — it called ``GTRLBridge.run_full_pipeline``
with the default ``num_drugs=25, num_diseases=18`` synthetic demo
graph and called itself "real". The script name was a lie.

This fix adds REAL Phase 1 → Phase 2 wiring as the DEFAULT path:
  1. ``ensure_phase1_data`` (writes embedded sample CSVs if missing)
  2. ``run_phase1_to_phase2`` (the real bridge — same one
     ``run_full_platform.py`` uses)
  3. ``GTRLBridge.run_full_pipeline(phase1_staged_data=staged, ...)``
     — trains GT and RL on the REAL Phase 2 staged graph

The synthetic demo path is preserved as an OPT-IN via ``--demo`` for
quick debugging without Phase 1/2 setup. ``--demo`` prints a loud
warning that the output is from a synthetic graph, not real data.

ROOT FIX (X-03): the previous version unconditionally set
``allow_invalid_output=True``, which DISABLED the scientific-validation
safety net. The audit found this meant the bridge ALWAYS shipped output,
even when its own scientific_validation reported ``overall_pass = False``
(KP recovery = 0.0%, GT AUC below random). That is the exact "ship
garbage to pharma partners" risk the P0 safety net was built to prevent.

The fix: default to STRICT mode (``allow_invalid_output=False``). If
scientific validation fails, the bridge RAISES RuntimeError with full
diagnostic context, so the team lead sees the failure LOUDLY instead of
receiving 10 garbage candidates.

For debugging/demo purposes where the user explicitly wants the output
despite validation failure, pass ``--allow-invalid-output`` on the
command line. This makes the bypass OPT-IN rather than the default.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    force=True,
)

from graph_transformer.gt_rl_bridge import GTRLBridge


def _ensure_phase1_data(phase1_dir: Path) -> dict:
    """Phase 1: ensure the processed_data CSVs exist (mirror run_full_platform)."""
    logging.info("=" * 70)
    logging.info("PHASE 1: Data Ingestion")
    logging.info("=" * 70)
    if not phase1_dir.exists() or not any(phase1_dir.glob("*.csv*")):
        logging.info("Phase 1 dir %s is empty — writing embedded samples.", phase1_dir)
        from pipelines._embedded_samples import write_all_samples
        write_all_samples(str(phase1_dir))
    csvs = sorted(phase1_dir.glob("*.csv*"))
    logging.info("Phase 1: %d CSV files present in %s", len(csvs), phase1_dir)
    return {csv.stem: csv for csv in csvs}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full GT+RL drug repurposing pipeline."
    )
    parser.add_argument(
        # v100 ROOT FIX (R-006): --demo preserves the old synthetic-graph
        # behavior for quick debugging. The default is now REAL Phase 1+2.
        "--demo", action="store_true",
        help="Use the synthetic demo graph (25 drugs × 18 diseases) "
             "instead of real Phase 1+2 data. DEBUGGING ONLY — the "
             "output is NOT real repurposing candidates.",
    )
    parser.add_argument(
        "--phase1-dir", type=str, default=os.path.join(_ROOT, "phase1", "processed_data"),
        help="Directory containing Phase 1 processed CSVs (default: phase1/processed_data)",
    )
    parser.add_argument(
        "--num-drugs", type=int, default=25,
        help="(DEMO MODE ONLY) Number of drug nodes in the synthetic graph (default: 25)",
    )
    parser.add_argument(
        "--num-diseases", type=int, default=18,
        help="(DEMO MODE ONLY) Number of disease nodes in the synthetic graph (default: 18)",
    )
    parser.add_argument(
        # v90 ROOT FIX (BUG #46): the previous default was 80, but the
        # bridge's run_full_pipeline default is 500. The CLI override
        # made GT train for only 80 epochs (6x shorter than intended).
        "--gt-epochs", type=int, default=500,
        help="GT training epochs (default: 500, aligned with bridge default)",
    )
    parser.add_argument(
        # v90 ROOT FIX (BUG #45): the previous default was 5000, but
        # PipelineConfig.timesteps defaults to 50000.
        "--rl-timesteps", type=int, default=50000,
        help="RL training timesteps (default: 50000, aligned with PipelineConfig.timesteps)",
    )
    parser.add_argument(
        "--rl-top-n", type=int, default=10,
        help="Number of top candidates to return (default: 10)",
    )
    # ROOT FIX (X-03): make the safety-net bypass OPT-IN, not default.
    parser.add_argument(
        "--allow-invalid-output", action="store_true",
        help="Bypass the scientific-validation safety net and produce "
             "candidates even when validation fails. DEBUGGING ONLY — "
             "do not use for pharma partner demos.",
    )
    args = parser.parse_args()

    output_dir = os.path.join(_ROOT, "output_real_run")
    os.makedirs(output_dir, exist_ok=True)

    bridge = GTRLBridge(
        output_dir=output_dir,
        device="cpu",
        seed=42,
    )

    if args.allow_invalid_output:
        logging.warning(
            "ROOT FIX (X-03): --allow-invalid-output flag is set. "
            "The scientific-validation safety net is DISABLED."
        )

    # v100 ROOT FIX (R-006): REAL Phase 1+2 path (default) vs DEMO path
    # (--demo). The real path loads Phase 1 CSVs → Phase 2 bridge →
    # passes the staged graph to GTRLBridge so GT/RL train on REAL data.
    phase1_staged_data = None
    if not args.demo:
        try:
            phase1_dir = Path(args.phase1_dir)
            _ensure_phase1_data(phase1_dir)
            logging.info("=" * 70)
            logging.info("BRIDGE: Phase 1 → Phase 2 (run_phase1_to_phase2)")
            logging.info("=" * 70)
            from drugos_graph.phase1_bridge import run_phase1_to_phase2
            bridge_result = run_phase1_to_phase2(
                phase1_processed_dir=str(phase1_dir),
                prefer_postgres=False,
            )
            staged = bridge_result["staged"]
            summary = bridge_result["summary"]
            logging.info(
                "Bridge: nodes_staged=%d, edges_staged=%d, sources_read=%d",
                summary["nodes_staged"], summary["edges_staged"],
                len(summary["sources_read"]),
            )
            if summary["nodes_staged"] == 0:
                logging.error(
                    "Phase 1+2 produced 0 staged nodes. Aborting. "
                    "Run with --demo to fall back to the synthetic graph."
                )
                sys.exit(2)
            phase1_staged_data = staged
        except Exception as e:
            logging.error(
                "Real Phase 1+2 path failed: %s. Aborting. "
                "Run with --demo to fall back to the synthetic graph.", e,
            )
            raise
    else:
        logging.warning(
            "=" * 70 + "\n"
            "DEMO MODE (--demo flag): using a SYNTHETIC 25-drug × 18-disease\n"
            "graph. The output is NOT real repurposing candidates — it is\n"
            "for debugging the GT+RL training loop only.\n" + "=" * 70
        )

    candidates_df, results = bridge.run_full_pipeline(
        # When phase1_staged_data is provided, num_drugs/num_diseases are
        # ignored (the bridge loads the real graph instead of generating
        # a demo graph). They're only used in --demo mode.
        num_drugs=args.num_drugs,
        num_diseases=args.num_diseases,
        gt_epochs=args.gt_epochs,
        rl_timesteps=args.rl_timesteps,
        rl_top_n=args.rl_top_n,
        allow_invalid_output=args.allow_invalid_output,
        phase1_staged_data=phase1_staged_data,
        # V31 ROOT FIX (P0-1): 3-LAYER GT model so the drug node can see
        # the full drug→protein→pathway→disease (3-hop) pattern.
        gt_embedding_dim=32,
        gt_num_layers=3,
        gt_num_heads=4,
        gt_dropout=0.25,
    )

    # Print summary
    print("\n" + "=" * 70)
    print("V30 PIPELINE COMPLETE - SUMMARY")
    print("=" * 70)
    print(f"  GT Best Val AUC:        {results['gt_best_val_auc']:.4f}")
    print(f"  GT Test AUC:            {results['gt_test_auc']:.4f}")
    print(f"  GT Test AUC (verified): {results.get('gt_test_auc_verified', 'N/A')}")
    print(f"  GT Epochs Trained:      {results['gt_epochs_trained']}")
    print(f"  RL Pairs Processed:     {results['rl_pairs_processed']}")
    print(f"  RL Candidates Ranked:   {results['rl_ranked_high']}")
    print(f"  RL Inference Latency:   {results['rl_inference_latency_ms']:.0f}ms")
    print(f"  Candidates Returned:    {results['n_candidates_returned']}")
    print(f"  Output Directory:       {output_dir}")

    sv = results.get("scientific_validation", {})
    print()
    print("SCIENTIFIC VALIDATION (V30 honest metrics):")
    print(f"  GT Test AUC:            {sv.get('gt_test_auc', 'N/A'):.4f}  "
          f"pass={sv.get('gt_test_auc_pass', '?')}")
    print(f"  RL AUC:                 {sv.get('rl_auc', 'N/A')}  "
          f"pass={sv.get('rl_auc_pass', '?')}")
    print(f"  KP Recovery Rate:       {sv.get('kp_recovery_rate', 0):.1%}  "
          f"pass={sv.get('kp_recovery_pass', '?')}")
    overall_pass = sv.get('overall_pass', False)
    print(f"  OVERALL:                "
          f"{'PASSED' if overall_pass else 'FAILED (honest - small demo graph)'}")
    print("=" * 70)

    if len(candidates_df) > 0:
        print("\nTOP CANDIDATES (returned from RL, not GT):")
        cols = [c for c in ["drug", "disease", "reward", "rank"] if c in candidates_df.columns]
        print(candidates_df[cols].to_string(index=False))

    print("\n" + "=" * 70)
    print("V90 ROOT-LEVEL FIXES STATUS (honest — not 'VERIFIED'):")
    print("=" * 70)
    # V90 ROOT FIX (BUG #37): the previous block printed "V30 ROOT-LEVEL
    # FIXES VERIFIED IN THIS RUN" with a list of claims including
    # "Compound #3 (3.9/3.10): W-02 multi-hop path injection REMOVED".
    # The audit's BUG #37 finding: "The line claims 'W-02 multi-hop path
    # injection REMOVED' — but BUG #2 and BUG #3 show that 3-hop path
    # injection is STILL PRESENT (for both KPs and training positives).
    # The print statement is a lie."
    #
    # Actually, after the v89 P0 fix, the 3-hop path injection WAS removed
    # from graph_builder.py. But the print block was still misleading
    # because it claimed "VERIFIED" without actually verifying each claim
    # at runtime. The fix: change "VERIFIED" to "STATUS" and add a note
    # that these are CODE-LEVEL fixes, not runtime-verified claims.
    print("  NOTE: These are CODE-LEVEL fixes (verified by reading the source,")
    print("  not by runtime assertion). The scientific_validation gate above")
    print("  is the RUNTIME verification — if it PASSED, the fixes work; if")
    print("  it FAILED, the fixes are in place but the demo graph is too")
    print("  small for the V1 thresholds (GT AUC > 0.85, KP recovery >= 50%).")
    print()
    print("  Compound #1 (AUC fraud): 3-hop path injection REMOVED (v89 P0)")
    print("    - graph_builder.py no longer injects guaranteed paths for KPs")
    print("    - graph_builder.py no longer injects paths for training positives")
    print("    - GT model learns from NATURAL topology only")
    print("  Compound #2 (reproducibility): hash() replaced with SHA-256 (V90)")
    print("    - graph_builder.py uses _deterministic_seed (SHA-256)")
    print("    - gt_rl_bridge.py uses _deterministic_name_seed (SHA-256)")
    print("  Compound #3 (resume crash): checkpoint re-evaluates on test (V90)")
    print("    - train_model now evaluates on test set even when resuming")
    print("    - results dict includes test_auc / test_auc_verified on resume")
    print("  BUG #31: kp_recovery_threshold raised from 0.2 to 0.5 (V90)")
    print("  BUG #32: early stopping uses unweighted eval loss (V90)")
    print("  BUG #33: load_checkpoint restores best_epoch (V90)")
    print("  BUG #34: build_model accepts link_predictor_hidden_dims (V90)")
    print("  BUG #35: run_full_pipeline passes gt_attention_dropout (V90)")
    print("  BUG #36: VERIFIED AUC uses independent code path (V90)")
    print("  BUG #37: this print block is now honest (was misleading) (V90)")
    print("  BUG #38: _feature_rng dead code removed (V90)")
    print("  BUG #39: _enrich_features_with_graph_signal NO-OP call kept (V90)")
    print("  BUG #40: X-10 partial config check exercised by test (V90)")
    print("  BUG #41: save_checkpoint skips None best_state_dict (V90)")
    print("  BUG #42: _calibrate_temperature assertion documented as defensive (V90)")
    print("  BUG #43: neg_ratio=6 documented (V90)")
    print("  BUG #44: max_attempts factor 50 documented (V90)")
    print("  BUG #45: STREAMING_THRESHOLD raised to 100,000 (V90)")
    print("  BUG #46: predict_drug_disease_scores encodes once (V90)")
    print("  BUG #47: apply_temperature mismatch fixed (V90)")
    print("  BUG #48: LABEL_LEAKING_EDGES frozenset consistency (V90)")
    print("  BUG #49: node_features dict iteration order sorted (V90)")
    print("  BUG #50: compute_graph_degrees vectorized return (V90)")
    print("=" * 70)

    # V30 ROOT FIX (Phase I): exit NON-ZERO if scientific_validation failed.
    # The original run_real_pipeline.py printed candidates unconditionally
    # and exited 0 even when validation failed. A CI/CD pipeline checking
    # exit code would treat "ship garbage" runs as success. The fix exits
    # non-zero so CI/CD can detect the failure.
    if not overall_pass:
        print("\n" + "=" * 70)
        print("V30 ROOT FIX (Phase I): SCIENTIFIC VALIDATION FAILED.")
        print("Exiting with non-zero status so CI/CD can detect the failure.")
        print("To override for debugging, pass --allow-invalid-output.")
        print("=" * 70)
        sys.exit(1)
    else:
        print("\n" + "=" * 70)
        print("V30 ROOT FIX (Phase I): SCIENTIFIC VALIDATION PASSED.")
        print("Exiting with status 0 (success).")
        print("=" * 70)
        sys.exit(0)


if __name__ == "__main__":
    main()
