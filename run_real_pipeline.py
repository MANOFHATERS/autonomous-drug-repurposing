"""Real end-to-end pipeline runner for V28 demo.

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

v100 ROOT FIX (R-006): the previous version passed ``num_drugs=25,
num_diseases=18`` to ``bridge.run_full_pipeline`` WITHOUT
``phase1_staged_data`` or ``graph_data``. Per gt_rl_bridge.py, when
neither is provided, the bridge falls through to ``build_demo_graph`` —
a SYNTHETIC random graph. Phase 1 was completely skipped and Phase 2
was completely skipped. The filename "run_real_pipeline" was
patient-safety-critical misinformation. The fix: add Phase 1 data
preparation (via embedded samples) and Phase 1→2 bridge, then pass
``phase1_staged_data=staged`` so the GT model trains on the REAL
biomedical KG. This makes the runner honest — "real" now means real.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

# Ensure project root is on sys.path
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
_PHASE1_ROOT = os.path.join(_ROOT, "phase1")
if _PHASE1_ROOT not in sys.path:
    sys.path.insert(0, _PHASE1_ROOT)
_PHASE2_ROOT = os.path.join(_ROOT, "phase2")
if _PHASE2_ROOT not in sys.path:
    sys.path.insert(0, _PHASE2_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    force=True,
)

from graph_transformer.gt_rl_bridge import GTRLBridge


def _ensure_phase1_data(phase1_dir: str) -> bool:
    """Ensure Phase 1 processed_data exists; auto-generate if missing.

    v100 ROOT FIX (R-006): mirrors run_full_platform.py's _ensure_phase1_data
    so this runner uses REAL Phase 1 data instead of a synthetic demo graph.
    Returns True if the data exists (or was successfully generated).
    """
    if os.path.isdir(phase1_dir) and any(
        f.endswith(".csv") or f.endswith(".csv.gz")
        for f in os.listdir(phase1_dir)
    ):
        logging.info("Phase 1 processed_data found at %s", phase1_dir)
        return True

    logging.warning(
        "Phase 1 processed_data not found at %s. Auto-invoking Phase 1 "
        "in sample mode (embedded CSVs — no API calls, biologically "
        "valid real IDs).", phase1_dir,
    )

    import subprocess

    env = dict(os.environ)
    env["DRUGOS_DOWNLOAD_MODE"] = env.get("DRUGOS_DOWNLOAD_MODE", "sample")
    env["DISGENET_USE_API"] = "false"

    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pipelines", "samples"],
            cwd=_PHASE1_ROOT,
            capture_output=True, text=True, timeout=300, env=env,
        )
        if proc.returncode == 0 and os.path.isdir(phase1_dir):
            logging.info(
                "Phase 1 sample data generated at %s.", phase1_dir,
            )
            return True
        logging.error(
            "Phase 1 sample generation FAILED (rc=%d). stderr: %s",
            proc.returncode, (proc.stderr or "")[-500:],
        )
        return False
    except Exception as exc:
        logging.error("Phase 1 sample generation exception: %s", exc)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the full GT+RL drug repurposing pipeline on REAL data."
    )
    parser.add_argument(
        "--phase1-dir", type=str,
        default=os.path.join(_ROOT, "phase1", "processed_data"),
        help="Path to Phase 1 processed_data directory (default: phase1/processed_data)",
    )
    parser.add_argument(
        # v90 ROOT FIX (BUG #46): the previous default was 80, but the
        # bridge's run_full_pipeline default is 500. The CLI override
        # made GT train for only 80 epochs (6x shorter than intended).
        # The V30 fix at line 354 says "increased epochs from 300 to 500"
        # but the CLI made it 80. The fix aligns the CLI default with
        # the bridge default (500). Users who want fewer epochs for
        # debugging can pass --gt-epochs 80 explicitly.
        "--gt-epochs", type=int, default=500,
        help="GT training epochs (default: 500, aligned with bridge default)",
    )
    parser.add_argument(
        # v90 ROOT FIX (BUG #45): the previous default was 5000, but
        # PipelineConfig.timesteps defaults to 50000. The CLI override
        # made PPO train for only 5000 timesteps (10x shorter than
        # intended). The V30 docstring at line 806 says "increased from
        # 30000 to 50000 for better convergence" but the CLI override
        # made it 5000, which is 10x SHORTER than the documented value.
        # PPO didn't converge, AUC was ~0.5, and the pipeline failed
        # validation. The fix aligns the CLI default with the config
        # default (50000). Users who want fewer timesteps for debugging
        # can pass --rl-timesteps 5000 explicitly.
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
             "do not use for pharma partner demos. (X-03 fix: was the "
             "default; now opt-in.)",
    )
    args = parser.parse_args()

    output_dir = os.path.join(_ROOT, "output_real_run")
    os.makedirs(output_dir, exist_ok=True)

    # v100 ROOT FIX (R-006): Phase 1 data preparation. The previous
    # version skipped Phase 1 entirely and let the bridge build a
    # SYNTHETIC demo graph. Now we ensure REAL Phase 1 data exists.
    print("=" * 70)
    print("PHASE 1: Data Ingestion (ensuring real biomedical data)")
    print("=" * 70)
    if not _ensure_phase1_data(args.phase1_dir):
        logging.error("Phase 1 data unavailable. Cannot proceed.")
        sys.exit(1)

    csv_files = [
        f for f in os.listdir(args.phase1_dir)
        if f.endswith(".csv") or f.endswith(".csv.gz")
    ]
    print(f"Phase 1 COMPLETE: {len(csv_files)} CSV files present.\n")

    # v100 ROOT FIX (R-006): Phase 1 → Phase 2 bridge. Build the REAL
    # staged data from Phase 1 CSVs so the GT model trains on real
    # biomedical topology (Drugs, Proteins, Pathways, Diseases) instead
    # of a synthetic random graph.
    print("=" * 70)
    print("PHASE 2: Knowledge Graph Construction (Phase 1 → 2 bridge)")
    print("=" * 70)
    from drugos_graph.phase1_bridge import (
        run_phase1_to_phase2,
        RecordingGraphBuilder,
    )
    _builder = RecordingGraphBuilder()
    _bridge_result = run_phase1_to_phase2(
        phase1_processed_dir=args.phase1_dir,
        builder=_builder,
    )
    staged = _bridge_result["staged"]
    _bridge_summary = _bridge_result["summary"]
    print(
        f"Phase 2 COMPLETE: {_bridge_summary['nodes_staged']} nodes, "
        f"{_bridge_summary['edges_staged']} edges staged from REAL data.\n"
    )
    if _bridge_summary["nodes_staged"] == 0:
        logging.error("Phase 2 bridge staged ZERO nodes. Cannot proceed.")
        sys.exit(2)

    bridge = GTRLBridge(
        output_dir=output_dir,
        device="cpu",
        seed=42,
    )

    # ROOT FIX (X-03): default to STRICT mode (allow_invalid_output=False).
    # The bridge will RAISE RuntimeError if scientific validation fails,
    # making the failure LOUD instead of silently shipping garbage.
    if args.allow_invalid_output:
        logging.warning(
            "ROOT FIX (X-03): --allow-invalid-output flag is set. "
            "The scientific-validation safety net is DISABLED. The "
            "bridge will produce candidates even if GT AUC < 0.85, "
            "RL AUC < 0.5, or KP recovery < 20%. These candidates "
            "may be RANDOM — do NOT use them for pharma partner demos."
        )

    # v100 ROOT FIX (R-006): pass phase1_staged_data=staged so the GT
    # model trains on the REAL biomedical KG. The previous call passed
    # only num_drugs/num_diseases which triggered the synthetic
    # build_demo_graph fallback — Phase 1+2 were completely skipped.
    candidates_df, results = bridge.run_full_pipeline(
        allow_invalid_output=args.allow_invalid_output,
        phase1_staged_data=staged,
        gt_epochs=args.gt_epochs,
        rl_timesteps=args.rl_timesteps,
        rl_top_n=args.rl_top_n,
        # V31 ROOT FIX (P0-1): use a 3-LAYER model so the GT model can
        # capture the full drug→protein→pathway→disease (3-hop) pattern.
        gt_embedding_dim=32,
        gt_num_layers=3,
        gt_num_heads=4,
        gt_dropout=0.25,
    )

    # Print summary
    # v100 ROOT FIX (R-013): use .get() with safe defaults for ALL dict
    # accesses. The previous code used direct ``results['key']`` which
    # raised KeyError if the bridge omitted a field.
    print("\n" + "=" * 70)
    print("V100 REAL PIPELINE COMPLETE - SUMMARY (on REAL biomedical KG)")
    print("=" * 70)
    print(f"  GT Best Val AUC:        {results.get('gt_best_val_auc', 0):.4f}")
    print(f"  GT Test AUC:            {results.get('gt_test_auc', 0):.4f}")
    print(f"  GT Test AUC (verified): {results.get('gt_test_auc_verified', 'N/A')}")
    print(f"  GT Epochs Trained:      {results.get('gt_epochs_trained', 0)}")
    print(f"  RL Pairs Processed:     {results.get('rl_pairs_processed', 0)}")
    print(f"  RL Candidates Ranked:   {results.get('rl_ranked_high', 0)}")
    print(f"  RL Inference Latency:   {results.get('rl_inference_latency_ms', 0):.0f}ms")
    print(f"  Candidates Returned:    {results.get('n_candidates_returned', 0)}")
    print(f"  Output Directory:       {output_dir}")

    sv = results.get("scientific_validation", {})
    print()
    print("SCIENTIFIC VALIDATION (V100 honest metrics — REAL data):")
    # v100 ROOT FIX (R-014): the previous code used
    # ``sv.get('gt_test_auc', 'N/A'):.4f`` which raised ValueError when
    # the key was missing (formatting the string 'N/A' as float). Now
    # we default to 0.0 (a valid float) so :.4f always works.
    _gt_auc = sv.get('gt_test_auc', 0.0)
    print(f"  GT Test AUC:            {_gt_auc:.4f}  "
          f"pass={sv.get('gt_test_auc_pass', '?')}")
    _rl_auc = sv.get('rl_auc', 'N/A')
    print(f"  RL AUC:                 {_rl_auc}  "
          f"pass={sv.get('rl_auc_pass', '?')}")
    print(f"  KP Recovery Rate:       {sv.get('kp_recovery_rate', 0):.1%}  "
          f"pass={sv.get('kp_recovery_pass', '?')}")
    overall_pass = sv.get('overall_pass', False)
    print(f"  OVERALL:                "
          f"{'PASSED' if overall_pass else 'FAILED (honest — see metrics above)'}")
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
