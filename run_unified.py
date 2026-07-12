#!/usr/bin/env python3
"""
Unified Platform Runner -- Phase 1 + Phase 2 in one command
==========================================================

This is the SINGLE top-level entry point for the unified Autonomous Drug
Repurposing Platform. It chains:

  Phase 1  ->  Bridge  ->  Phase 2
  ───────────────────────────────
  Phase 1 (data ingestion):
    Reads the processed_data CSVs that Phase 1's pipelines have already
    produced (DrugBank drugs, DrugBank interactions, OMIM GDA). If you
    want to re-run Phase 1 pipelines from scratch, see
    ``phase1/README.md`` and ``phase1/Makefile``.

  Bridge (phase1_bridge):
    Converts Phase 1 CSVs into Phase 2 node/edge dicts with full lineage.
    See ``phase2/drugos_graph/phase1_bridge.py``.

  Phase 2 (knowledge graph):
    Loads the staged dicts into a graph builder. By default the
    RecordingGraphBuilder is used (in-memory, no Neo4j) so the runner
    works out of the box. To target a real Neo4j, set the
    DRUGOS_NEO4J_URI / DRUGOS_NEO4J_USER / DRUGOS_NEO4J_PASSWORD env vars
    OR pass --neo4j-uri on the CLI.

USAGE
-----
  # Dry run (in-memory, no Neo4j, no side effects):
  python run_unified.py

  # Dry run with verbose JSON report:
  python run_unified.py --json

  # Real Neo4j load:
  python run_unified.py --neo4j-uri bolt://localhost:7687 \\
      --neo4j-user neo4j --neo4j-password secret

  # Override Phase 1 processed_data dir:
  python run_unified.py --phase1-dir /custom/path/to/processed_data

EXIT CODES
----------
  0  -- Success (data loaded, no errors)
  1  -- Bridge produced zero nodes (Phase 1 outputs likely missing)
  2  -- Bridge produced zero edges (interactions or OMIM CSV likely empty)
  3  -- Neo4j connection failed in production (v75 T-032: applies whenever
       the runner is in production mode AND no Neo4j is reachable, whether
       --neo4j-uri was explicitly supplied OR auto-detected from
       DRUGOS_NEO4J_URI env var OR the default bolt://localhost:7687
       fallback. Set DRUGOS_ALLOW_NO_NEO4J=1 to acknowledge and continue
       with the in-memory RecordingGraphBuilder.)
  4  -- V1 launch criteria not met (only when --full-pipeline)
  5  -- Full pipeline raised an unexpected exception
  6  -- --require-full-data set but Phase 1 sample mode would be used (T-011)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess  # R-001/R-INT-007: top-level import so subprocess.SubprocessError resolves in except clauses
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
PHASE1_ROOT = HERE / "phase1"
PHASE2_ROOT = HERE / "phase2"
PHASE1_PROCESSED_DEFAULT = PHASE1_ROOT / "processed_data"

for p in (str(PHASE2_ROOT), str(PHASE1_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)


# R-025 root fix: the previous code called set_global_seed(42) at
# module import time. phase2/drugos_graph/run_pipeline.py:run_full_pipeline
# ALSO calls set_global_seed(42) as its first action. That double
# seed-setting is harmless but redundant and confused readers about
# which call actually mattered. The import-time call has been removed;
# run_full_pipeline's own seed-setting is authoritative. The flag below
# is retained for any downstream code that introspected it.
_GLOBAL_SEED_SET: bool = False
try:
    from drugos_graph.config import set_global_seed as _set_global_seed  # noqa: F401
    _GLOBAL_SEED_SET = True
except ImportError as _seed_import_exc:
    import logging as _logging
    _logging.getLogger("unified").error(
        "Cannot import drugos_graph.config.set_global_seed (%s). "
        "The phase2 package is missing or broken. Pipeline will run but "
        "model init is NON-DETERMINISTIC. Ensure phase2/drugos_graph/ "
        "is on sys.path.",
        _seed_import_exc,
    )


# v20 Compound-2/8 ROOT FIX -- Production escape-hatch guard (run_unified side).
# The same guard exists in run_pipeline.py, but run_pipeline.py is only
# imported when --full-pipeline is on. For --no-full-pipeline (bridge-only)
# runs, this guard ensures escape hatches are still refused in production.
#
# v75 ROOT FIX (T-031 -- escape-hatch guard runs at import time):
#   The v74 code called ``_check_production_escape_hatches_unified()`` at
#   MODULE IMPORT TIME (top-level call at line 151). This ran BEFORE
#   ``main()``, BEFORE argparse processed ``--help``, and BEFORE any
#   logging was set up. If DRUGOS_ENVIRONMENT=prod and an escape-hatch
#   flag was set, the module raised ``SystemExit(1)`` -- so
#   ``python run_unified.py --help`` exited 1 with NO help output,
#   confusing operators.
#
#   ROOT FIX: the call is now made INSIDE ``main()`` AFTER
#   ``_setup_logging(args.verbose)`` (see the call site below). This
#   ensures:
#     (1) ``--help`` works regardless of escape-hatch state (argparse
#         processes --help before main() runs the guard).
#     (2) The guard's SystemExit (if raised) is logged via the configured
#         logger instead of being a bare stderr write.
#     (3) The guard runs ONCE per process (main() is called once), not
#         on every import (the v74 module-level call ran on every
#         import -- including by the Airflow _trigger_phase2 subprocess
#         which imports run_unified indirectly).
def _check_production_escape_hatches_unified() -> None:
    env = os.environ.get("DRUGOS_ENVIRONMENT", "dev").lower()
    if env in ("prod", "production"):
        offenders: List[str] = []
        for flag in (
            "DRUGOS_ALLOW_NO_SAMPLER",
            "DRUGOS_ALLOW_PERMISSIVE_KG",
            "DRUGOS_ALLOW_PERMISSIVE_DPI",
            "DRUGOS_ALLOW_LAUNCH_FAIL",
        ):
            if os.environ.get(flag, "") == "1":
                offenders.append(flag)
        if offenders:
            raise SystemExit(
                f"REFUSING TO RUN: production environment detected "
                f"(DRUGOS_ENVIRONMENT={env}) but escape-hatch flag(s) "
                f"are set: {', '.join(offenders)}. These flags re-activate "
                "patient-safety-critical compound destruction chains "
                "(Compound-1, Compound-2, Compound-5, Compound-8). "
                "Unset the flag(s) or change DRUGOS_ENVIRONMENT to 'dev'."
            )


# v75 ROOT FIX (T-031): the call is now made inside main() -- see the
# call site after _setup_logging(args.verbose).


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_real_neo4j(uri: str, user: str, password: str):
    """Construct and connect a real DrugOSGraphBuilder to a Neo4j instance."""
    from drugos_graph import DrugOSGraphBuilder, Neo4jConfig

    cfg = Neo4jConfig(uri=uri, user=user, password=password)
    builder = DrugOSGraphBuilder(cfg)
    builder.connect()
    try:
        builder.create_constraints()
    except (OSError, ValueError) as exc:
        logging.warning("create_constraints() failed (continuing): %s", exc)
    return builder


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_unified.py",
        description="Run the unified Phase 1 -> Phase 2 pipeline.",
    )
    parser.add_argument(
        "--phase1-dir",
        type=Path,
        default=PHASE1_PROCESSED_DEFAULT,
        help="Phase 1 processed_data directory (default: phase1/processed_data)",
    )
    parser.add_argument("--neo4j-uri", default=None,
                        help="Neo4j bolt:// URI. If omitted, dry-run mode is used.")
    parser.add_argument("--neo4j-user", default=None)
    parser.add_argument("--neo4j-password", default=None)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--json", action="store_true",
                        help="Emit the full summary as JSON to stdout")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--full-pipeline",
        action="store_true",
        default=True,
        help=(
            "v15 ROOT FIX (REM-25): after the bridge stages data, also "
            "run the FULL Phase 2 pipeline (entity resolution -> PyG "
            "HeteroData build -> training data construction -> TransE "
            "training -> validation -> V1 launch criteria check). "
            "v20 ROOT FIX (Phase1↔Phase2 connection): the previous "
            "default was False -- operators had to explicitly pass "
            "--full-pipeline to get an AUC. Most users never did, "
            "leading to the audit's complaint that the runner exits 0 "
            "but produces no model. Default is now True; pass "
            "--no-full-pipeline to stop at the bridge (dev/test only)."
        ),
    )
    parser.add_argument(
        "--no-full-pipeline",
        dest="full_pipeline",
        action="store_false",
        help=(
            "v20: opt OUT of the full pipeline. Stops at the bridge -- "
            "no TransE training, no AUC, no V1 launch criteria check. "
            "Useful for quick smoke-tests in dev."
        ),
    )
    # ORCH-002 ROOT FIX: --full-pipeline was misleadingly named — it runs
    # only the Phase 2 INTERNAL full pipeline (TransE training, PyG
    # HeteroData, V1 launch criteria), NOT Phase 3 (Graph Transformer)
    # or Phase 4 (RL ranker). An operator running `python run_unified.py`
    # expected 4-phase output but got ONLY Phase 1+2. The new --run-gt-rl
    # flag (default False for backward-compat; --run-gt-rl to enable)
    # chains Phase 3+4 via GTRLBridge.run_full_pipeline AFTER Phase 2
    # completes. This makes the meaning of each flag explicit:
    #   --full-pipeline : Phase 2 full internal pipeline (TransE etc.)
    #   --run-gt-rl     : Phase 3 (GT) + Phase 4 (RL) on top of Phase 2
    # Use BOTH for a true 4-phase run from this entry point. For the
    # canonical 4-phase runner, see run_4phase.py (which is the source
    # of truth for the Phase 1→2→3→4 chain).
    parser.add_argument(
        "--run-gt-rl",
        action="store_true",
        default=False,
        help=(
            "ORCH-002 ROOT FIX: after Phase 2, also run Phase 3 (Graph "
            "Transformer training) + Phase 4 (RL ranker) via "
            "GTRLBridge.run_full_pipeline. Without this flag, "
            "run_unified.py stops after Phase 2 (or after the Phase 2 "
            "internal full pipeline if --full-pipeline is set). For the "
            "canonical 4-phase runner, use run_4phase.py."
        ),
    )
    parser.add_argument(
        "--gt-epochs", type=int, default=80,
        help=(
            "ORCH-002: Graph Transformer training epochs (only used "
            "when --run-gt-rl is set). Default 80 (demo); 500 for "
            "production-scale training."
        ),
    )
    parser.add_argument(
        "--rl-timesteps", type=int, default=5000,
        help=(
            "ORCH-002: RL training timesteps (only used when --run-gt-rl "
            "is set). Default 5000; 50000 for production-scale RL."
        ),
    )
    parser.add_argument(
        "--rl-top-n", type=int, default=10,
        help="ORCH-002: Number of top RL candidates to return (default 10).",
    )
    parser.add_argument(
        "--gt-rl-output-dir", type=str, default=None,
        help=(
            "ORCH-002: Output directory for GT/RL artifacts. Defaults to "
            "<cwd>/output_unified_gt_rl."
        ),
    )
    # v21 ROOT FIX (Audit Chain 1 / Chain 12): the previous declaration
    # used ``action='store_true', default=True`` with NO inverse flag.
    # That made ``--skip-download`` a no-op (it was already True) AND
    # locked the operator out of ever enabling downloads from this
    # entry point -- the audit's #1 P0 blocker. ``BooleanOptionalAction``
    # exposes BOTH ``--skip-download`` AND ``--no-skip-download`` so the
    # user can choose. Default stays True (Phase 1 CSVs are the
    # authoritative data source per the build doc), but operators can
    # now opt in to live downloads without editing source code.
    parser.add_argument(
        "--skip-download",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip network downloads in step7 (use Phase 1 CSVs only). "
             "Default True -- the bridge is the authoritative data source. "
             "Pass --no-skip-download to enable live downloads of "
             "STRING/UniProt/ChEMBL/DrugBank/SIDER/etc.",
    )
    # v63 ROOT FIX (P2C-003+016 -- ChEMBERTa silent-disable cascade):
    # The audit required a --no-chemberta CLI flag for dev environments
    # where the ChEMBERTa checkpoint download is too slow or HF_TOKEN is
    # unavailable. Without this flag, dev runs either (a) raised
    # FeatureFailureError (DRUGOS_STRICT_FEATURES=1 default) blocking
    # all dev work, or (b) required setting DRUGOS_USE_CHEMBERTA=0 env
    # var which is undocumented and easy to forget. The flag sets the
    # env var BEFORE step9 runs, so the dev path is explicit and
    # auditable. In production (DRUGOS_ENVIRONMENT=prod), the flag is
    # IGNORED -- ChEMBERTa is mandatory and failure is fatal.
    parser.add_argument(
        "--no-chemberta",
        action="store_true",
        default=False,
        help="v63: Disable ChEMBERTa SMILES feature encoding in step9 "
             "(dev convenience flag). Sets DRUGOS_USE_CHEMBERTA=0 and "
             "DRUGOS_STRICT_FEATURES=0 so the pipeline falls back to "
             "random Xavier features for Compound nodes WITHOUT raising. "
             "The Graph Transformer will NOT learn molecular structure -- "
             "AUC reflects transductive memorisation only. "
             "IGNORED in production (DRUGOS_ENVIRONMENT=prod) where "
             "ChEMBERTa is mandatory.",
    )
    # v73 ROOT FIX (T-011 -- auto-invoked Phase 1 sample mode produces
    # sample-sized KG silently):
    #   The previous runner auto-invoked Phase 1 in SAMPLE mode
    #   (50-200 records per source) when ``phase1/processed_data/`` did
    #   not exist. The operator saw "UNIFIED RUN COMPLETE -- N nodes, M
    #   edges loaded" with no indication that N and M were sample-scale
    #   (tens) instead of production-scale (millions). The runner exited
    #   0. The staged_graph.json persisted at the sample scale. Phase 2
    #   training ran on the sample graph and reported an AUC that looked
    #   valid but was meaningless. The V1 launch criteria check could
    #   PASS on the sample graph -- operators could declare V1 launch on
    #   a sample-sized KG. ``make dry-run`` auto-invoked sample-mode
    #   Phase 1 and reported success; CI that ran ``make dry-run`` and
    #   checked exit code 0 saw green even though no real data was
    #   loaded.
    #
    #   ROOT FIX: add a ``--require-full-data`` flag that exits non-zero
    #   (exit code 6) if sample mode would be used. When the flag is NOT
    #   set (default dev behaviour), emit a LOUD multi-line warning so
    #   the operator CANNOT miss that the KG is sample-scale. The
    #   warning is repeated in the final summary log so the sample-scale
    #   state is visible at every stage of the run.
    parser.add_argument(
        "--require-full-data",
        action="store_true",
        default=False,
        help=(
            "v73 ROOT FIX (T-011): exit non-zero (code 6) if Phase 1 "
            "sample mode would be auto-invoked. Use in CI, production "
            "deployments, and any context where a sample-sized KG "
            "(tens of nodes) would be misleading. When this flag is "
            "NOT set, a LOUD warning is printed to stderr indicating "
            "the KG is sample-scale -- but the run continues (dev mode)."
        ),
    )
    args = parser.parse_args(argv)

    # v63 ROOT FIX (P2C-003+016): honour --no-chemberta in dev only.
    if args.no_chemberta:
        _env = os.environ.get("DRUGOS_ENVIRONMENT", "dev").lower()
        if _env in ("prod", "production"):
            print(
                "ERROR: --no-chemberta cannot be used in production "
                "(DRUGOS_ENVIRONMENT=prod). ChEMBERTa molecular features "
                "are mandatory for V1 launch sign-off. The Graph "
                "Transformer must learn molecular structure, not "
                "transductive node identity.",
                file=sys.stderr,
            )
            return 1
        os.environ["DRUGOS_USE_CHEMBERTA"] = "0"
        os.environ["DRUGOS_STRICT_FEATURES"] = "0"
        print(
            "[v63] --no-chemberta: ChEMBERTa DISABLED for this run "
            "(dev mode). DRUGOS_USE_CHEMBERTA=0, DRUGOS_STRICT_FEATURES=0. "
            "Compound nodes will use random Xavier features. The Graph "
            "Transformer will NOT learn molecular structure.",
            file=sys.stderr,
        )

    _setup_logging(args.verbose)
    log = logging.getLogger("unified")

    # v75 ROOT FIX (T-031 -- escape-hatch guard runs at import time):
    # Call the production escape-hatch guard HERE, after logging is set
    # up and after argparse has processed --help. The v74 module-level
    # call (line 151) ran before main() and broke --help in production
    # environments with escape hatches set. Now --help works regardless
    # of escape-hatch state, and the guard's SystemExit (if raised) is
    # logged via the configured logger.
    _check_production_escape_hatches_unified()

    # ─── 1. Phase 1 outputs sanity check ──────────────────────────────────
    # v29 ROOT FIX (audit O-1 -- "run_unified.py does NOT run Phase 1"):
    # The audit found that a fresh ``python run_unified.py`` exits 1
    # immediately because Phase 1's processed_data/ doesn't exist on a
    # fresh clone. The v28 code just said "run Phase 1 first" and gave
    # up. ROOT FIX: actually invoke Phase 1 here, so the unified runner
    # is truly unified. We try the Phase 1 master pipeline; if it
    # fails (e.g. no DrugBank license, no network), we fall back to the
    # embedded samples (no API calls, no DB writes -- biologically valid
    # real InChIKeys/UniProt IDs/DOIDs) so the platform ALWAYS produces
    # a graph and an AUC even on a fresh laptop with no credentials.
    #
    # v73 ROOT FIX (T-011 -- sample-mode KG is silent):
    #   Track whether the run ended up in sample mode (Tier 2 fallback)
    #   so the final summary log can emit a LOUD, multi-line warning.
    #   The previous code logged a single WARNING line that was easy to
    #   miss in a long log scroll. Now we print a banner to BOTH the
    #   log AND stderr so the operator cannot miss it. If
    #   ``--require-full-data`` was passed, we exit non-zero (code 6)
    #   BEFORE auto-invoking sample mode -- CI gates can catch this.
    _sample_mode_used = False
    if not args.phase1_dir.exists():
        # v73 ROOT FIX (T-011): if --require-full-data was passed, refuse
        # to auto-invoke Phase 1 sample mode. Exit code 6 is documented
        # in the EXIT CODES section above. This is the CI / production
        # guard -- operators who want the dev fallback simply omit the
        # flag and the existing layered Tier 1 -> Tier 2 -> Tier 3 path
        # runs as before.
        if args.require_full_data:
            print(
                "ERROR: --require-full-data is set but Phase 1 "
                "processed_data/ does not exist at "
                f"{args.phase1_dir}. Refusing to auto-invoke Phase 1 "
                "sample mode (T-011 fix). Run Phase 1 with full data "
                "first: `cd phase1 && python -m pipelines all` with "
                "proper API credentials (ChEMBL, DrugBank license, "
                "DISGENET_API_KEY, OMIM_API_KEY).",
                file=sys.stderr,
            )
            return 6
        log.warning(
            "Phase 1 processed_data dir not found: %s -- attempting to "
            "run Phase 1 master pipeline now (v49 root fix).", args.phase1_dir,
        )
        # v73 ROOT FIX (T-011): LOUD multi-line banner to BOTH log and
        # stderr so the operator CANNOT miss that the KG will be
        # sample-scale. This fires BEFORE the auto-invocation so the
        # operator sees it at the very start of the run.
        _sample_banner = (
            "\n"
            + "=" * 78 + "\n"
            + "!!! PHASE 1 SAMPLE MODE WARNING (T-011) !!!\n"
            + "=" * 78 + "\n"
            + "Phase 1 processed_data/ does not exist. The unified runner\n"
            + "will auto-invoke Phase 1 in SAMPLE MODE (50-200 records per\n"
            + "source). The resulting Knowledge Graph will be SAMPLE-SCALE\n"
            + "(tens of nodes) -- NOT production-scale (millions of nodes).\n"
            + "\n"
            + "This is suitable for:\n"
            + "  * dev smoke-tests of the bridge + Phase 2 pipeline\n"
            + "  * verifying the unified runner wires Phase 1 -> Phase 2\n"
            + "\n"
            + "This is NOT suitable for:\n"
            + "  * V1 launch sign-off (the AUC reflects transductive\n"
            + "    memorisation on a tiny graph, NOT generalisation)\n"
            + "  * production deployment\n"
            + "  * CI gates that check exit code 0 as a proxy for\n"
            + "    'real data was loaded'\n"
            + "\n"
            + "To require full production data, re-run with:\n"
            + "  python run_unified.py --require-full-data\n"
            + "This exits non-zero (code 6) if sample mode would be used.\n"
            + "\n"
            + "To load full data, run Phase 1 with API credentials:\n"
            + "  cd phase1 && python -m pipelines all\n"
            + "(requires DRUGBANK_XML_PATH, DISGENET_API_KEY, OMIM_API_KEY)\n"
            + "=" * 78 + "\n"
        )
        log.warning(_sample_banner)
        # Also print to stderr so it shows even when logging is
        # redirected to a file. The operator CANNOT miss this.
        print(_sample_banner, file=sys.stderr)
        _sample_mode_used = True
        # v61 ROOT FIX (silent break point #3 -- forensic deep fix):
        # The v49 code tried `python -m pipelines` (full sample-mode run
        # which makes API calls to ChEMBL/UniProt/STRING/DisGeNET/OMIM/
        # PubChem). When ANY API was unreachable (no network, rate-limit,
        # missing API keys, DrugBank academic license paused), the entire
        # Phase 1 master pipeline FAILED and run_unified.py exited 1 --
        # the user saw "Phase 1 auto-invocation failed" with NO fallback.
        # ROOT FIX: layered fallback strategy.
        #   Tier 1: try `python -m pipelines all` (full sample mode with
        #     API calls -- produces the richest dataset when network +
        #     credentials are available).
        #   Tier 2: if Tier 1 fails, try `python -m pipelines samples`
        #     (embedded sample CSVs -- NO API calls, NO DB writes,
        #     biologically valid real IDs). This ALWAYS succeeds as long
        #     as the phase1 package imports cleanly.
        #   Tier 3: if even Tier 2 fails (phase1 package broken), give
        #     the user a clear actionable error message.
        phase1_succeeded = False
        # --- Tier 1: full sample-mode run with API calls ---
        try:
            _phase1_root = str(HERE / "phase1")
            log.info(
                "Tier 1: invoking `python -m pipelines all` "
                "(DRUGOS_DOWNLOAD_MODE=sample, makes API calls to all "
                "7 sources -- needs network + API keys)."
            )
            _env = dict(os.environ)
            _env["DRUGOS_DOWNLOAD_MODE"] = _env.get(
                "DRUGOS_DOWNLOAD_MODE", "sample"
            )
            # R-001: subprocess is imported at module level (top-level), so
            # subprocess.SubprocessError in the except clause resolves correctly.
            # R-033: 60s GUARANTEED Tier 1 would fail on any real hardware --
            # `python -m pipelines all` makes API calls to 7 external sources
            # and easily exceeds 60s. 600s (10 min) is long enough for a real
            # attempt but short enough that operators do not wait hours for
            # the fallback.
            _proc = subprocess.run(
                [sys.executable, "-m", "pipelines", "all"],
                cwd=_phase1_root,
                capture_output=True, text=True, timeout=600,
                env=_env,
            )
            if _proc.returncode == 0 and args.phase1_dir.exists():
                phase1_succeeded = True
                log.info(
                    "Tier 1 succeeded: Phase 1 master pipeline completed "
                    "-- processed_data available at %s", args.phase1_dir,
                )
            else:
                log.warning(
                    "Tier 1 failed (rc=%d) -- falling back to Tier 2 "
                    "(embedded samples, no API calls). stderr tail: %s",
                    _proc.returncode, (_proc.stderr or "")[-500:],
                )
        # R-001/R-INT-007: subprocess is imported at module level, so
        # subprocess.SubprocessError resolves correctly in this except clause.
        except (subprocess.SubprocessError, OSError, ValueError) as _tier1_exc:
            log.warning(
                "Tier 1 exception: %s -- falling back to Tier 2.",
                _tier1_exc,
            )

        # --- Tier 2: embedded samples (no API calls) ---
        if not phase1_succeeded:
            try:
                _phase1_root = str(HERE / "phase1")
                log.info(
                    "Tier 2: invoking `python -m pipelines samples` "
                    "(embedded CSVs -- no API calls, no DB writes, "
                    "biologically valid real IDs). This ALWAYS succeeds "
                    "if the phase1 package imports cleanly."
                )
                _proc = subprocess.run(
                    [sys.executable, "-m", "pipelines", "samples"],
                    cwd=_phase1_root,
                    capture_output=True, text=True, timeout=300,
                )
                if _proc.returncode == 0 and args.phase1_dir.exists():
                    phase1_succeeded = True
                    log.info(
                        "Tier 2 succeeded: embedded sample CSVs written "
                        "to %s. The platform will run end-to-end on "
                        "these samples -- biologically valid (real "
                        "InChIKeys, UniProt IDs, DOIDs) but small (~70 "
                        "nodes). For the full 10K-drug KG, run Tier 1 "
                        "with proper API credentials.", args.phase1_dir,
                    )
                else:
                    log.error(
                        "Tier 2 FAILED (rc=%d). stdout: %s | stderr: %s",
                        _proc.returncode,
                        (_proc.stdout or "")[-500:],
                        (_proc.stderr or "")[-500:],
                    )
            except (subprocess.SubprocessError, OSError, ValueError, ImportError) as _tier2_exc:
                log.error(
                    "Tier 2 exception: %s", _tier2_exc,
                )

        # --- Tier 3: total failure -- actionable error ---
        if not phase1_succeeded:
            log.error(
                "All Phase 1 invocation tiers failed. Manual options: "
                "(1) cd phase1 && python -m pipelines samples   "
                "(2) get a DrugBank license and set DRUGBANK_XML_PATH  "
                "(3) set DISGENET_API_KEY and OMIM_API_KEY env vars  "
                "(4) run individual Phase 1 pipelines (chembl, drugbank, "
                "uniprot, string, disgenet, omim, pubchem) one at a "
                "time. See phase1/README.md."
            )
            return 1
    else:
        log.info("=" * 70)
        log.info("UNIFIED RUNNER -- Phase 1 -> Bridge -> Phase 2")
        log.info("=" * 70)
        log.info("Phase 1 processed_data: %s", args.phase1_dir)

    # ─── 2. Build or select the graph builder ─────────────────────────────
    # v36 ROOT FIX (Neo4j persistence -- user's #1 complaint):
    # "All data lives in RecordingGraphBuilder (in-memory). Nothing
    # persists. No Neo4j writes." The previous code defaulted to
    # RecordingGraphBuilder when --neo4j-uri was omitted, which meant
    # a fresh ``python run_unified.py`` produced NO persistent KG --
    # the 67 nodes / 66 edges were dropped on process exit unless the
    # operator explicitly passed --neo4j-uri.
    #
    # The fix: AUTO-DETECT Neo4j from env vars (DRUGOS_NEO4J_URI /
    # DRUGOS_NEO4J_USER / DRUGOS_NEO4J_PASSWORD) and CLI args. If
    # neither is set, attempt a connection to the default
    # ``bolt://localhost:7687`` with credentials ``neo4j / neo4j``
    # (the Neo4j default). Only if that fails do we fall back to the
    # RecordingGraphBuilder AND persist the staged graph to disk as
    # ``staged_graph.json`` (the v34 fallback) AND emit a loud warning
    # that the KG is NOT in Neo4j.
    builder = None
    neo4j_uri = (
        args.neo4j_uri
        or os.environ.get("DRUGOS_NEO4J_URI")
        or os.environ.get("NEO4J_URI")
    )
    neo4j_user = (
        args.neo4j_user
        or os.environ.get("DRUGOS_NEO4J_USER")
        or os.environ.get("NEO4J_USER")
        or "neo4j"
    )
    neo4j_password = (
        args.neo4j_password
        or os.environ.get("DRUGOS_NEO4J_PASSWORD")
        or os.environ.get("NEO4J_PASSWORD")
        or "neo4j"
    )
    # R-020 root fix: the previous "auto-detect" was theater -- on a fresh
    # laptop without Neo4j it ALWAYS tried bolt://localhost:7687 first,
    # ALWAYS failed (ConnectionError), ALWAYS fell back to RecordingGraphBuilder.
    # The 5-second connection timeout added latency to every run. Now: if no
    # URI is provided, go STRAIGHT to RecordingGraphBuilder with a clear log.
    neo4j_connected = False
    builder = None
    if neo4j_uri:
        try:
            log.info("Neo4j mode: connecting to %s", neo4j_uri)
            builder = _build_real_neo4j(neo4j_uri, neo4j_user, neo4j_password)
            neo4j_connected = True
            log.info("Neo4j connection ESTABLISHED -- KG will be persisted.")
        except (OSError, ValueError, ConnectionError) as exc:
            log.warning(
                "Neo4j connection to %s failed: %s. Falling back to "
                "RecordingGraphBuilder (in-memory). To enable Neo4j: "
                "start Neo4j locally and pass --neo4j-uri or set DRUGOS_NEO4J_URI.",
                neo4j_uri, exc,
            )
    else:
        log.info(
            "No --neo4j-uri or DRUGOS_NEO4J_URI set -- using "
            "RecordingGraphBuilder (in-memory). The staged graph WILL "
            "be persisted to disk as staged_graph.json. To use Neo4j, "
            "pass --neo4j-uri bolt://localhost:7687."
        )

    # R-031: use the package-level re-export instead of the deep submodule path.
    if builder is None:
        from drugos_graph import RecordingGraphBuilder
        builder = RecordingGraphBuilder()
        # In production, missing Neo4j is a launch-blocking condition (T-032).
        _env = os.environ.get("DRUGOS_ENVIRONMENT", "dev").lower()
        if _env in ("prod", "production"):
            log.error(
                "PRODUCTION BLOCKER: DRUGOS_ENVIRONMENT=%s but no Neo4j "
                "connection is available (URI=%s). The KG will NOT be "
                "persisted to Neo4j. Either start Neo4j or set "
                "DRUGOS_ALLOW_NO_NEO4J=1 to acknowledge.",
                _env, neo4j_uri or "(none)",
            )
            if os.environ.get("DRUGOS_ALLOW_NO_NEO4J") != "1":
                return 3

    # ─── 3. Run the bridge ────────────────────────────────────────────────
    from drugos_graph.phase1_bridge import run_phase1_to_phase2

    log.info("Running Phase 1 -> Phase 2 bridge...")
    result = run_phase1_to_phase2(
        phase1_processed_dir=args.phase1_dir,
        builder=builder,
        batch_size=args.batch_size,
    )

    summary: Dict[str, Any] = result["summary"]

    # R-032: trimmed 15-line comment block down to its essence.
    # Persist the staged graph to disk so it survives process exit.
    _persist_path = None  # explicit None init before try
    try:
        from drugos_graph.phase1_bridge import Phase1StagedData
        staged_obj: Phase1StagedData = result["staged"]
        _persist_dir = PHASE2_ROOT / "data" / "processed"
        _persist_dir.mkdir(parents=True, exist_ok=True)
        _persist_path = _persist_dir / "staged_graph.json"
        _persist_payload = {
            "bridge_version": summary["bridge_version"],
            "nodes_staged": summary["nodes_staged"],
            "edges_staged": summary["edges_staged"],
            "nodes_loaded": summary["nodes_loaded"],
            "edges_loaded": summary["edges_loaded"],
            "edge_types_present": list(summary["edge_types_present"]),
            "warnings": list(summary.get("warnings", [])),
            "errors": list(summary.get("errors", [])),
            "node_counts_by_type": {},
            "edge_counts_by_type": {},
            "nodes": {},
            "edges": {},
        }
        # Persist nodes/edges by type for downstream consumers.
        _node_collections = {
            "Compound": getattr(staged_obj, "compound_nodes", []),
            "Protein": getattr(staged_obj, "protein_nodes", []),
            "Gene": getattr(staged_obj, "gene_nodes", []),
            "Disease": getattr(staged_obj, "disease_nodes", []),
            "ClinicalOutcome": getattr(staged_obj, "clinical_outcome_nodes", []),
            "Pathway": getattr(staged_obj, "pathway_nodes", []),
        }
        for ntype, nodes in _node_collections.items():
            if nodes:
                _persist_payload["nodes"][ntype] = nodes[:50]  # cap for readability
                _persist_payload["node_counts_by_type"][ntype] = len(nodes)
        for (src, rel, dst), edges in staged_obj.edges.items():
            _key = f"{src}->{rel}->{dst}"
            _persist_payload["edges"][_key] = edges[:50]
            _persist_payload["edge_counts_by_type"][_key] = len(edges)
        with open(_persist_path, "w") as _f:
            json.dump(_persist_payload, _f, indent=2, default=str)
        log.info(
            "Staged graph PERSISTED to %s (dry-run artifact -- Neo4j is "
            "the production store when --neo4j-uri is set).",
            _persist_path,
        )
    except OSError as _persist_os_exc:
        # v54 ROOT FIX (ROOT-4 -- staged_graph.json bare except):
        # Split the bare `except Exception` into specific clauses.
        # OSError (disk full, permission denied, path too long) is
        # ERROR-level -- the operator needs to know the graph was NOT
        # persisted, because downstream consumers (Phase 3, RL ranker)
        # will not find the file.
        # v75 ROOT FIX (T-033): replaced the fragile
        # ``_persist_path if '_persist_path' in dir() else 'staged_graph.json'``
        # check with an explicit ``_persist_path is not None`` check.
        # ``_persist_path`` is now initialized to ``None`` BEFORE the
        # try block (line 699), so the except handler can reliably
        # detect whether the assignment at line 705 ever ran. The
        # placeholder ``'staged_graph.json'`` (the default filename the
        # try block WOULD have used) is now a clear fallback string
        # only -- no ``dir()`` introspection, no scope ambiguity.
        _path_for_msg = str(_persist_path) if _persist_path is not None else "staged_graph.json (default -- assignment did not run)"
        log.error(
            "FAILED to persist staged graph to disk (OSError): %s. "
            "The staged graph was loaded into the builder but NOT "
            "written to %s. Downstream consumers (Phase 3, RL ranker) "
            "will NOT find this file. Check disk space and permissions. "
            "(v54 ROOT-4 fix -- was silently dropped in v48; v75 T-033 "
            "fix -- replaced dir() check with explicit None init).",
            _persist_os_exc,
            _path_for_msg,
        )
    except (TypeError, ValueError) as _persist_json_exc:
        # JSON serialization errors (non-serializable objects in payload)
        # -- ERROR-level because this indicates a code bug in the payload
        # construction.
        log.error(
            "FAILED to persist staged graph to disk (JSON serialization "
            "error): %s. The staged graph payload contains non-serializable "
            "objects. This is a code bug in run_unified.py's persistence "
            "block -- report to the development team. (v54 ROOT-4 fix).",
            _persist_json_exc,
        )
    except (OSError, PermissionError) as _persist_exc:
        # Catch-all for I/O errors -- WARNING (non-fatal) but
        # with full traceback so the operator can diagnose.
        log.warning(
            "Failed to persist staged graph to disk (unexpected I/O error, "
            "non-fatal): %s",
            _persist_exc,
            exc_info=True,
        )

    # ─── 4. Report ────────────────────────────────────────────────────────
    log.info("-" * 70)
    log.info("BRIDGE SUMMARY")
    log.info("-" * 70)
    log.info("Bridge version:       %s", summary["bridge_version"])
    log.info("Sources read:         %s", summary["sources_read"])
    log.info("Nodes staged:         %d", summary["nodes_staged"])
    log.info("Edges staged:         %d", summary["edges_staged"])
    log.info("Nodes loaded:         %d", summary["nodes_loaded"])
    log.info("Edges loaded:         %d", summary["edges_loaded"])
    log.info("Edge types present:")
    for et in summary["edge_types_present"]:
        log.info("  - %s", et)
    if summary["warnings"]:
        log.info("Warnings:")
        for w in summary["warnings"]:
            log.info("  ! %s", w)
    if summary["errors"]:
        log.error("Errors:")
        for e in summary["errors"]:
            log.error("  X %s", e)

    if args.json:
        # Make summary JSON-serializable (Path objects etc.)
        print(json.dumps(summary, indent=2, default=str))

    # ─── 5. Exit-code contract ───────────────────────────────────────────
    if summary["nodes_loaded"] == 0:
        log.error("Zero nodes loaded -- Phase 1 outputs likely missing or empty.")
        return 1
    if summary["edges_loaded"] == 0:
        log.error("Zero edges loaded -- interactions or OMIM CSV likely empty.")
        return 2

    log.info("=" * 70)
    log.info("UNIFIED RUN COMPLETE -- %d nodes, %d edges loaded",
             summary["nodes_loaded"], summary["edges_loaded"])
    log.info("=" * 70)

    # v73 ROOT FIX (T-011): if sample mode was used to populate Phase 1,
    # emit a FINAL LOUD warning so the operator cannot miss the
    # sample-scale state at the end of the run. This catches operators
    # who scroll only to the bottom of the log.
    if _sample_mode_used:
        _final_sample_warning = (
            "\n"
            + "!" * 78 + "\n"
            + "!!! SAMPLE-MODE KG -- NOT PRODUCTION-SCALE (T-011) !!!\n"
            + "!" * 78 + "\n"
            + f"The Knowledge Graph just loaded ({summary['nodes_loaded']} "
            + f"nodes, {summary['edges_loaded']} edges) was built from\n"
            + "EMBEDDED SAMPLE CSVs, NOT real biomedical data. The AUC\n"
            + "and V1 launch criteria reported by Phase 2 are NOT valid\n"
            + "for production sign-off -- they reflect transductive\n"
            + "memorisation on a tiny graph, NOT generalisation.\n"
            + "\n"
            + "DO NOT report this run as a V1 launch candidate.\n"
            + "\n"
            + "To load full production data, run Phase 1 with API\n"
            + "credentials (DRUGBANK_XML_PATH, DISGENET_API_KEY,\n"
            + "OMIM_API_KEY) and re-run this script -- OR pass\n"
            + "--require-full-data to fail-fast in CI.\n"
            + "!" * 78 + "\n"
        )
        log.warning(_final_sample_warning)
        print(_final_sample_warning, file=sys.stderr)

    # ─── 6. v15 ROOT FIX (REM-25): optionally run the FULL Phase 2 pipeline ─
    # v14's run_unified.py stopped at the bridge -- it never trained TransE,
    # never built PyG HeteroData, never validated, never checked V1 launch
    # criteria. The "unified runner" was therefore theater: it loaded nodes
    # and edges into a RecordingGraphBuilder and exited 0, but the project's
    # headline deliverable (the >0.85 AUC) was never computed by THIS entry
    # point. Operators had to manually invoke `python -m drugos_graph` --
    # which most users never did, leading to the user's complaint that "every
    # session every AI tells its 100 percent integrated but see the reality."
    # Fix: when --full-pipeline is passed, chain directly into
    # run_pipeline.run_full_pipeline(data_source="phase1") so the unified
    # runner actually produces a model, an AUC, and a launch verdict.
    if args.full_pipeline:
        log.info("-" * 70)
        log.info("FULL PIPELINE -- Step 8 (entity_resolution) -> Step 9 (PyG build) "
                 "-> Step 10 (training data) -> Step 11 (TransE train) -> "
                 "Step 12 (validation) -> V1 launch criteria")
        log.info("-" * 70)
        try:
            from drugos_graph.run_pipeline import run_full_pipeline
            # v73 ROOT FIX (T-010 -- env-var Neo4j path caused double-load):
            #   The previous predicate checked ONLY the CLI arg for Neo4j
            #   URI presence. If the operator set the DRUGOS_NEO4J_URI env
            #   var (the recommended production setup per the docstring at
            #   lines 24-26) but did NOT pass --neo4j-uri on the CLI, the
            #   predicate evaluated to False (skip_neo4j=False). But the
            #   bridge at line 473 had ALREADY connected to Neo4j using
            #   the env-var-resolved URI (line 441-445) and loaded the
            #   staged graph. run_full_pipeline then opened a SECOND Neo4j
            #   session and re-loaded the same graph -- duplicate nodes,
            #   duplicate edges, upsert collisions. The duplicate load
            #   doubled write latency and corrupted edge counts.
            #
            #   ROOT FIX: use ``neo4j_connected`` (set at line 474 ONLY
            #   when ``_build_real_neo4j`` succeeded) as the predicate.
            #   ``neo4j_connected=True`` means the bridge already loaded
            #   the graph into Neo4j, so ``run_full_pipeline`` MUST skip
            #   its own Neo4j load (use the in-memory / RecordingGraphBuilder
            #   path internally for the PyG/TransE stages). When
            #   ``neo4j_connected=False`` (env var unset AND localhost
            #   connection failed -> RecordingGraphBuilder fallback),
            #   ``skip_neo4j=False`` is harmless -- there is no Neo4j to
            #   skip, and ``run_full_pipeline`` falls back to its own
            #   in-memory builder internally. This single-flag predicate
            #   correctly handles ALL three Neo4j modes:
            #     (a) --neo4j-uri CLI arg -> neo4j_connected=True
            #         -> skip_neo4j=True ✓
            #     (b) DRUGOS_NEO4J_URI env var -> neo4j_connected=True
            #         -> skip_neo4j=True ✓ (was the bug)
            #     (c) No Neo4j available -> neo4j_connected=False
            #         -> skip_neo4j=False ✓ (harmless, no-op)
            pipeline_result = run_full_pipeline(
                data_source="phase1",
                skip_neo4j=neo4j_connected,
                skip_download=args.skip_download,
                phase1_processed_dir=args.phase1_dir,
            )
            log.info("-" * 70)
            log.info("PIPELINE RESULT")
            log.info("-" * 70)
            # Pipeline result is a dict; pretty-print the key fields.
            for k, v in pipeline_result.items():
                if k == "v1_criteria":
                    log.info("  V1 launch criteria: %s", v)
                elif isinstance(v, dict):
                    # Summarize each step's dict result.
                    short = {sk: sv for sk, sv in v.items()
                             if sk in ("skipped", "reason", "held_out_auc",
                                       "best_val_auc", "model_saved",
                                       "passed", "n_nodes", "n_edges",
                                       "n_triples", "elapsed_s")}
                    log.info("  %s: %s", k, short)
                else:
                    log.info("  %s: %s", k, v)
            # If V1 launch criteria returned a verdict, reflect it in exit.
            v1 = pipeline_result.get("v1_criteria") or {}
            if isinstance(v1, dict) and v1.get("passed") is False:
                log.error("V1 LAUNCH CRITERIA NOT MET -- see report above.")
                return 4
            log.info("=" * 70)
            log.info("FULL PIPELINE COMPLETE -- V1 criteria satisfied")
            log.info("=" * 70)
        except SystemExit as exc:
            # v21 ROOT FIX (Audit Chain 12): run_pipeline.py previously
            # called sys.exit(1) directly when V1 launch criteria fail.
            # The previous ``except Exception`` clause did NOT catch
            # SystemExit (SystemExit derives from BaseException, not
            # Exception), so the exit code propagated through
            # run_unified.py and crashed any parent orchestrator
            # (Airflow/Celery/K8s Job). The documented contract said
            # exit code 4 = V1 launch criteria not met -- but that
            # contract was DEAD because sys.exit(1) hijacked the exit.
            # Now run_pipeline raises V1LaunchCriteriaFailed instead
            # (caught below). This SystemExit catch is defensive -- it
            # handles any OTHER sys.exit() that might still leak from
            # deep library code (e.g. argparse on bad CLI).
            code = int(exc.code) if isinstance(exc.code, int) else 1
            if code == 1:
                log.error(
                    "V1 launch criteria not met (sys.exit(1) from "
                    "run_pipeline). Returning documented exit code 4."
                )
                return 4
            log.error("Pipeline raised SystemExit(%d).", code)
            return code
        except (RuntimeError, OSError, ValueError, ConnectionError) as exc:
            # v21 ROOT FIX: catch V1LaunchCriteriaFailed (our typed
            # exception from run_pipeline, derived from RuntimeError) and
            # translate to exit code 4. Other specific runtime/env errors
            # also map to exit code 5. Programming bugs (TypeError,
            # AttributeError, etc.) are NOT caught -- they must propagate.
            exc_name = type(exc).__name__
            if exc_name == "V1LaunchCriteriaFailed":
                log.error(
                    "V1 launch criteria not met. Returning documented "
                    "exit code 4. Failure detail: %s",
                    getattr(exc, "criteria", {}),
                )
                return 4
            log.exception("Full pipeline failed: %s", exc)
            return 5

    # ─── 7. ORCH-002 ROOT FIX: optional Phase 3 (GT) + Phase 4 (RL) ───────
    # The previous run_unified.py stopped after Phase 2 (or after the
    # Phase 2 internal full pipeline if --full-pipeline). Phase 3 (Graph
    # Transformer) and Phase 4 (RL ranker) were NEVER invoked from this
    # entry point, even though the docstring implied a "unified" run.
    # The new --run-gt-rl flag chains Phase 3+4 via the same
    # GTRLBridge.run_full_pipeline that run_4phase.py uses, so an
    # operator can run the full 4-phase chain from a single command.
    if getattr(args, "run_gt_rl", False):
        log.info("=" * 70)
        log.info(
            "PHASE 3 + 4 (ORCH-002): Graph Transformer training + "
            "RL ranking via GTRLBridge.run_full_pipeline"
        )
        log.info("=" * 70)
        try:
            # Phase 2 → Phase 3 schema adapter: convert the in-memory
            # RecordingGraphBuilder (or Neo4j-backed builder) into the
            # (node_features, edge_indices, node_maps, known_pairs) tuple
            # that GTRLBridge expects. This is the SAME adapter that
            # run_4phase.py uses — we deliberately reuse it so the two
            # entry points cannot drift.
            from graph_transformer.data.phase2_adapter import (
                adapt_phase2_to_phase3,
            )

            # ``builder`` is set earlier in main() by the Phase 2 bridge.
            # If the bridge was skipped (e.g. dev smoke-test), we cannot
            # run Phase 3+4 — fail loud, not silent.
            if "builder" not in locals() or builder is None:
                log.error(
                    "ORCH-002: --run-gt-rl was set but the Phase 2 "
                    "builder is not available. Phase 3+4 requires the "
                    "Phase 2 graph. Run without --no-full-pipeline."
                )
                return 5

            graph_data = adapt_phase2_to_phase3(builder, seed=42)
            node_features, edge_indices, node_maps, known_pairs = graph_data
            n_drugs = len(node_maps.get("drug", {}))
            n_diseases = len(node_maps.get("disease", {}))
            log.info(
                "Phase 2→3 adapter: %d drugs, %d diseases, %d known pairs.",
                n_drugs, n_diseases, len(known_pairs),
            )
            if n_drugs == 0 or n_diseases == 0:
                log.error(
                    "ORCH-002: schema adapter produced 0 drug or 0 disease "
                    "nodes. Cannot train Graph Transformer on an empty "
                    "graph. Aborting Phase 3+4."
                )
                return 5

            from graph_transformer.gt_rl_bridge import GTRLBridge

            gt_rl_output_dir = args.gt_rl_output_dir or str(
                Path.cwd() / "output_unified_gt_rl"
            )
            Path(gt_rl_output_dir).mkdir(parents=True, exist_ok=True)

            bridge = GTRLBridge(
                output_dir=gt_rl_output_dir,
                device="cpu",
                seed=42,
            )
            candidates_df, results = bridge.run_full_pipeline(
                gt_epochs=args.gt_epochs,
                rl_timesteps=args.rl_timesteps,
                rl_top_n=args.rl_top_n,
                allow_invalid_output=False,
                graph_data=graph_data,
            )

            log.info("=" * 70)
            log.info("PHASE 3 + 4 COMPLETE — GT/RL RESULTS")
            log.info("=" * 70)
            log.info("  GT Best Val AUC:        %s", results.get("gt_best_val_auc", "N/A"))
            log.info("  GT Test AUC (verified): %s", results.get("gt_test_auc_verified", "N/A"))
            log.info("  GT Epochs Trained:      %s", results.get("gt_epochs_trained", 0))
            log.info("  RL Candidates Ranked:   %s", results.get("rl_ranked_high", 0))
            log.info("  Candidates Returned:    %s", results.get("n_candidates_returned", 0))
            log.info("  Output Directory:       %s", gt_rl_output_dir)
            sv = results.get("scientific_validation", {})
            if sv:
                log.info(
                    "  Scientific validation:  overall_pass=%s",
                    sv.get("overall_pass", False),
                )
            if len(candidates_df) > 0:
                log.info("Top RL-ranked candidates:")
                cols = [c for c in ["drug", "disease", "reward", "rank"]
                        if c in candidates_df.columns]
                log.info("\n%s", candidates_df[cols].to_string(index=False))
        except RuntimeError as exc:
            log.exception("ORCH-002: Phase 3+4 failed: %s", exc)
            return 4
        except Exception as exc:
            # Programming bugs must propagate — do NOT swallow.
            log.exception("ORCH-002: unexpected Phase 3+4 error: %s", exc)
            raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
