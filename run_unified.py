#!/usr/bin/env python3
"""
Unified Platform Runner — Phase 1 + Phase 2 in one command
==========================================================

This is the SINGLE top-level entry point for the unified Autonomous Drug
Repurposing Platform. It chains:

  Phase 1  →  Bridge  →  Phase 2
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
  0  — Success (data loaded, no errors)
  1  — Bridge produced zero nodes (Phase 1 outputs likely missing)
  2  — Bridge produced zero edges (interactions or OMIM CSV likely empty)
  3  — Neo4j connection failed in production (v75 T-032: applies whenever
       the runner is in production mode AND no Neo4j is reachable, whether
       --neo4j-uri was explicitly supplied OR auto-detected from
       DRUGOS_NEO4J_URI env var OR the default bolt://localhost:7687
       fallback. Set DRUGOS_ALLOW_NO_NEO4J=1 to acknowledge and continue
       with the in-memory RecordingGraphBuilder.)
  4  — V1 launch criteria not met (only when --full-pipeline)
  5  — Full pipeline raised an unexpected exception
  6  — --require-full-data set but Phase 1 sample mode would be used (T-011)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
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


# FIX TOP-14 (FIX-CFG-ML audit): set the global RNG seed as the FIRST
# importable side-effect of run_unified.py. The Phase 2 config defines
# SEED=42 and propagates it to TransEConfig.seed / EvaluationConfig.seed /
# PyGConfig.seed, but until set_global_seed() is actually CALLED, the
# global ``random`` / ``numpy.random`` / ``torch`` RNG state at process
# start is whatever Python seeded it with — non-deterministic. This made
# model init non-deterministic (PyTorch ``nn.Embedding`` init consumes
# the global RNG), so two ``python run_unified.py`` runs with the same
# config could produce different held-out AUCs. Calling set_global_seed()
# here at import time (before any model is constructed) makes the entire
# pipeline deterministic given the same CONFIG_HASH. Synchronized with
# phase2/drugos_graph/run_pipeline.py:run_full_pipeline (which also calls
# set_global_seed as its first line) — DO NOT diverge (audit TOP-14).
# v54 ROOT FIX (ROOT-3 — set_global_seed bare except):
# The v48 code used `except Exception` which catches BOTH ImportError
# (config module missing — CRITICAL) AND RuntimeError (seed failed —
# WARNING). This conflated two different failure modes. ROOT FIX:
# split into two except clauses:
#   1. ImportError → ERROR (the phase2 package is broken/inaccessible)
#   2. Exception → WARNING (seed-setting is best-effort, non-blocking)
# Also: if the seed is NOT set, set a module-level flag so downstream
# code can detect non-deterministic mode.
_GLOBAL_SEED_SET: bool = False
try:
    from drugos_graph.config import set_global_seed as _set_global_seed

    _set_global_seed(42)
    _GLOBAL_SEED_SET = True
except ImportError as _seed_import_exc:
    import logging as _logging

    _logging.getLogger("unified").error(
        "set_global_seed(42) FAILED — cannot import drugos_graph.config "
        "(%s). The phase2 package is missing or broken. Pipeline will "
        "run but model init is NON-DETERMINISTIC. This is a CRITICAL "
        "regression: ensure phase2/drugos_graph/ is on sys.path "
        "(audit TOP-14, v54 ROOT-3 fix).",
        _seed_import_exc,
    )
except Exception as _seed_exc:  # noqa: BLE001 — best-effort, do not block
    import logging as _logging

    _logging.getLogger("unified").warning(
        "set_global_seed(42) failed (%s) — pipeline will run but model "
        "init is non-deterministic. This is a regression: phase2/drugos_"
        "graph/config.py must define set_global_seed (audit TOP-14).",
        _seed_exc,
    )


# v20 Compound-2/8 ROOT FIX — Production escape-hatch guard (run_unified side).
# The same guard exists in run_pipeline.py, but run_pipeline.py is only
# imported when --full-pipeline is on. For --no-full-pipeline (bridge-only)
# runs, this guard ensures escape hatches are still refused in production.
#
# v75 ROOT FIX (T-031 — escape-hatch guard runs at import time):
#   The v74 code called ``_check_production_escape_hatches_unified()`` at
#   MODULE IMPORT TIME (top-level call at line 151). This ran BEFORE
#   ``main()``, BEFORE argparse processed ``--help``, and BEFORE any
#   logging was set up. If DRUGOS_ENVIRONMENT=prod and an escape-hatch
#   flag was set, the module raised ``SystemExit(1)`` — so
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
#         import — including by the Airflow _trigger_phase2 subprocess
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


# v75 ROOT FIX (T-031): the call is now made inside main() — see the
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
    except Exception as exc:
        logging.warning("create_constraints() failed (continuing): %s", exc)
    return builder


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_unified.py",
        description="Run the unified Phase 1 → Phase 2 pipeline.",
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
            "run the FULL Phase 2 pipeline (entity resolution → PyG "
            "HeteroData build → training data construction → TransE "
            "training → validation → V1 launch criteria check). "
            "v20 ROOT FIX (Phase1↔Phase2 connection): the previous "
            "default was False — operators had to explicitly pass "
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
            "v20: opt OUT of the full pipeline. Stops at the bridge — "
            "no TransE training, no AUC, no V1 launch criteria check. "
            "Useful for quick smoke-tests in dev."
        ),
    )
    # v21 ROOT FIX (Audit Chain 1 / Chain 12): the previous declaration
    # used ``action='store_true', default=True`` with NO inverse flag.
    # That made ``--skip-download`` a no-op (it was already True) AND
    # locked the operator out of ever enabling downloads from this
    # entry point — the audit's #1 P0 blocker. ``BooleanOptionalAction``
    # exposes BOTH ``--skip-download`` AND ``--no-skip-download`` so the
    # user can choose. Default stays True (Phase 1 CSVs are the
    # authoritative data source per the build doc), but operators can
    # now opt in to live downloads without editing source code.
    parser.add_argument(
        "--skip-download",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip network downloads in step7 (use Phase 1 CSVs only). "
             "Default True — the bridge is the authoritative data source. "
             "Pass --no-skip-download to enable live downloads of "
             "STRING/UniProt/ChEMBL/DrugBank/SIDER/etc.",
    )
    # v63 ROOT FIX (P2C-003+016 — ChEMBERTa silent-disable cascade):
    # The audit required a --no-chemberta CLI flag for dev environments
    # where the ChEMBERTa checkpoint download is too slow or HF_TOKEN is
    # unavailable. Without this flag, dev runs either (a) raised
    # FeatureFailureError (DRUGOS_STRICT_FEATURES=1 default) blocking
    # all dev work, or (b) required setting DRUGOS_USE_CHEMBERTA=0 env
    # var which is undocumented and easy to forget. The flag sets the
    # env var BEFORE step9 runs, so the dev path is explicit and
    # auditable. In production (DRUGOS_ENVIRONMENT=prod), the flag is
    # IGNORED — ChEMBERTa is mandatory and failure is fatal.
    parser.add_argument(
        "--no-chemberta",
        action="store_true",
        default=False,
        help="v63: Disable ChEMBERTa SMILES feature encoding in step9 "
             "(dev convenience flag). Sets DRUGOS_USE_CHEMBERTA=0 and "
             "DRUGOS_STRICT_FEATURES=0 so the pipeline falls back to "
             "random Xavier features for Compound nodes WITHOUT raising. "
             "The Graph Transformer will NOT learn molecular structure — "
             "AUC reflects transductive memorisation only. "
             "IGNORED in production (DRUGOS_ENVIRONMENT=prod) where "
             "ChEMBERTa is mandatory.",
    )
    # v73 ROOT FIX (T-011 — auto-invoked Phase 1 sample mode produces
    # sample-sized KG silently):
    #   The previous runner auto-invoked Phase 1 in SAMPLE mode
    #   (50-200 records per source) when ``phase1/processed_data/`` did
    #   not exist. The operator saw "UNIFIED RUN COMPLETE — N nodes, M
    #   edges loaded" with no indication that N and M were sample-scale
    #   (tens) instead of production-scale (millions). The runner exited
    #   0. The staged_graph.json persisted at the sample scale. Phase 2
    #   training ran on the sample graph and reported an AUC that looked
    #   valid but was meaningless. The V1 launch criteria check could
    #   PASS on the sample graph — operators could declare V1 launch on
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
            "the KG is sample-scale — but the run continues (dev mode)."
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

    # v75 ROOT FIX (T-031 — escape-hatch guard runs at import time):
    # Call the production escape-hatch guard HERE, after logging is set
    # up and after argparse has processed --help. The v74 module-level
    # call (line 151) ran before main() and broke --help in production
    # environments with escape hatches set. Now --help works regardless
    # of escape-hatch state, and the guard's SystemExit (if raised) is
    # logged via the configured logger.
    _check_production_escape_hatches_unified()

    # ─── 1. Phase 1 outputs sanity check ──────────────────────────────────
    # v29 ROOT FIX (audit O-1 — "run_unified.py does NOT run Phase 1"):
    # The audit found that a fresh ``python run_unified.py`` exits 1
    # immediately because Phase 1's processed_data/ doesn't exist on a
    # fresh clone. The v28 code just said "run Phase 1 first" and gave
    # up. ROOT FIX: actually invoke Phase 1 here, so the unified runner
    # is truly unified. We try the Phase 1 master pipeline; if it
    # fails (e.g. no DrugBank license, no network), we fall back to the
    # embedded samples (no API calls, no DB writes — biologically valid
    # real InChIKeys/UniProt IDs/DOIDs) so the platform ALWAYS produces
    # a graph and an AUC even on a fresh laptop with no credentials.
    #
    # v73 ROOT FIX (T-011 — sample-mode KG is silent):
    #   Track whether the run ended up in sample mode (Tier 2 fallback)
    #   so the final summary log can emit a LOUD, multi-line warning.
    #   The previous code logged a single WARNING line that was easy to
    #   miss in a long log scroll. Now we print a banner to BOTH the
    #   log AND stderr so the operator cannot miss it. If
    #   ``--require-full-data`` was passed, we exit non-zero (code 6)
    #   BEFORE auto-invoking sample mode — CI gates can catch this.
    _sample_mode_used = False
    if not args.phase1_dir.exists():
        # v73 ROOT FIX (T-011): if --require-full-data was passed, refuse
        # to auto-invoke Phase 1 sample mode. Exit code 6 is documented
        # in the EXIT CODES section above. This is the CI / production
        # guard — operators who want the dev fallback simply omit the
        # flag and the existing layered Tier 1 → Tier 2 → Tier 3 path
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
            "Phase 1 processed_data dir not found: %s — attempting to "
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
            + "(tens of nodes) — NOT production-scale (millions of nodes).\n"
            + "\n"
            + "This is suitable for:\n"
            + "  * dev smoke-tests of the bridge + Phase 2 pipeline\n"
            + "  * verifying the unified runner wires Phase 1 → Phase 2\n"
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
        # v61 ROOT FIX (silent break point #3 — forensic deep fix):
        # The v49 code tried `python -m pipelines` (full sample-mode run
        # which makes API calls to ChEMBL/UniProt/STRING/DisGeNET/OMIM/
        # PubChem). When ANY API was unreachable (no network, rate-limit,
        # missing API keys, DrugBank academic license paused), the entire
        # Phase 1 master pipeline FAILED and run_unified.py exited 1 —
        # the user saw "Phase 1 auto-invocation failed" with NO fallback.
        # ROOT FIX: layered fallback strategy.
        #   Tier 1: try `python -m pipelines all` (full sample mode with
        #     API calls — produces the richest dataset when network +
        #     credentials are available).
        #   Tier 2: if Tier 1 fails, try `python -m pipelines samples`
        #     (embedded sample CSVs — NO API calls, NO DB writes,
        #     biologically valid real IDs). This ALWAYS succeeds as long
        #     as the phase1 package imports cleanly.
        #   Tier 3: if even Tier 2 fails (phase1 package broken), give
        #     the user a clear actionable error message.
        phase1_succeeded = False
        # --- Tier 1: full sample-mode run with API calls ---
        try:
            import subprocess as _sp
            import sys as _sys
            _phase1_root = str(HERE / "phase1")
            log.info(
                "Tier 1: invoking `python -m pipelines all` "
                "(DRUGOS_DOWNLOAD_MODE=sample, makes API calls to all "
                "7 sources — needs network + API keys)."
            )
            _env = dict(os.environ)
            _env["DRUGOS_DOWNLOAD_MODE"] = _env.get(
                "DRUGOS_DOWNLOAD_MODE", "sample"
            )
            # v61 ROOT FIX: Tier 1 has a SHORT timeout (60s) so it fails
            # fast and Tier 2 (embedded samples) kicks in quickly. The
            # full 7200s timeout was making run_unified.py hang for 2
            # hours when API calls were slow/unreachable. Tier 1 is a
            # "best effort" — if it can't complete in 60s, Tier 2 takes
            # over (embedded samples always succeed in <5s).
            _proc = _sp.run(
                [_sys.executable, "-m", "pipelines", "all"],
                cwd=_phase1_root,
                capture_output=True, text=True, timeout=60,
                env=_env,
            )
            if _proc.returncode == 0 and args.phase1_dir.exists():
                phase1_succeeded = True
                log.info(
                    "Tier 1 succeeded: Phase 1 master pipeline completed "
                    "— processed_data available at %s", args.phase1_dir,
                )
            else:
                log.warning(
                    "Tier 1 failed (rc=%d) — falling back to Tier 2 "
                    "(embedded samples, no API calls). stderr tail: %s",
                    _proc.returncode, (_proc.stderr or "")[-500:],
                )
        except Exception as _tier1_exc:
            log.warning(
                "Tier 1 exception: %s — falling back to Tier 2.",
                _tier1_exc,
            )

        # --- Tier 2: embedded samples (no API calls) ---
        if not phase1_succeeded:
            try:
                import subprocess as _sp
                import sys as _sys
                _phase1_root = str(HERE / "phase1")
                log.info(
                    "Tier 2: invoking `python -m pipelines samples` "
                    "(embedded CSVs — no API calls, no DB writes, "
                    "biologically valid real IDs). This ALWAYS succeeds "
                    "if the phase1 package imports cleanly."
                )
                _proc = _sp.run(
                    [_sys.executable, "-m", "pipelines", "samples"],
                    cwd=_phase1_root,
                    capture_output=True, text=True, timeout=300,
                )
                if _proc.returncode == 0 and args.phase1_dir.exists():
                    phase1_succeeded = True
                    log.info(
                        "Tier 2 succeeded: embedded sample CSVs written "
                        "to %s. The platform will run end-to-end on "
                        "these samples — biologically valid (real "
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
            except Exception as _tier2_exc:
                log.error(
                    "Tier 2 exception: %s", _tier2_exc,
                )

        # --- Tier 3: total failure — actionable error ---
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
        log.info("UNIFIED RUNNER — Phase 1 → Bridge → Phase 2")
        log.info("=" * 70)
        log.info("Phase 1 processed_data: %s", args.phase1_dir)

    # ─── 2. Build or select the graph builder ─────────────────────────────
    # v36 ROOT FIX (Neo4j persistence — user's #1 complaint):
    # "All data lives in RecordingGraphBuilder (in-memory). Nothing
    # persists. No Neo4j writes." The previous code defaulted to
    # RecordingGraphBuilder when --neo4j-uri was omitted, which meant
    # a fresh ``python run_unified.py`` produced NO persistent KG —
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
    # v36: if no URI was given on CLI or env, try the default localhost
    # address. This makes ``python run_unified.py`` "just work" if the
    # operator has Neo4j running locally with default credentials.
    if not neo4j_uri:
        neo4j_uri = "bolt://localhost:7687"
        log.info(
            "v36 Neo4j auto-detect: no --neo4j-uri or DRUGOS_NEO4J_URI "
            "set — trying default %s (user=%s). Set DRUGOS_NEO4J_URI to "
            "override.",
            neo4j_uri, neo4j_user,
        )

    neo4j_connected = False
    try:
        log.info("Neo4j mode: connecting to %s", neo4j_uri)
        builder = _build_real_neo4j(neo4j_uri, neo4j_user, neo4j_password)
        neo4j_connected = True
        log.info("Neo4j connection ESTABLISHED — KG will be persisted.")
    except Exception as exc:
        log.warning(
            "Neo4j connection to %s failed: %s. Falling back to "
            "RecordingGraphBuilder (in-memory). The staged graph WILL "
            "be persisted to disk as staged_graph.json (v34 fallback) "
            "but will NOT be in Neo4j. To enable Neo4j persistence: "
            "(1) start Neo4j locally (``docker run -p 7687:7687 -e "
            "NEO4J_AUTH=neo4j/password neo4j``), (2) set "
            "DRUGOS_NEO4J_URI=bolt://localhost:7687, (3) set "
            "DRUGOS_NEO4J_USER=neo4j, (4) set "
            "DRUGOS_NEO4J_PASSWORD=password. (v36 Neo4j persistence fix)",
            neo4j_uri, exc,
        )
        from drugos_graph.phase1_bridge import RecordingGraphBuilder
        builder = RecordingGraphBuilder()
        # In production, this is a launch-blocking condition.
        # v75 ROOT FIX (T-032 — exit code 3 contract mismatch):
        #   The exit code contract (lines 43-56) was updated to document
        #   that exit code 3 fires whenever the runner is in production
        #   mode AND no Neo4j is reachable — whether --neo4j-uri was
        #   explicitly supplied OR auto-detected from DRUGOS_NEO4J_URI
        #   env var OR the default bolt://localhost:7687 fallback. The
        #   v74 docstring's stale qualifier (which restricted exit 3 to
        #   only the explicit-CLI-arg case) was inaccurate: the v36
        #   ROOT FIX auto-detect path (line 566) meant the connection
        #   attempt ALWAYS happens, so the explicit-CLI-arg qualifier
        #   never matched reality. The inline log message below is also
        #   updated to reflect that the failure can happen on ANY of
        #   the three Neo4j URI sources (CLI arg, env var, default
        #   localhost).
        _env = os.environ.get("DRUGOS_ENVIRONMENT", "dev").lower()
        if _env in ("prod", "production"):
            log.error(
                "!!! PRODUCTION BLOCKER (v36 Neo4j persistence, v75 T-032) !!! "
                "DRUGOS_ENVIRONMENT=%s but Neo4j connection failed (URI=%s). "
                "The KG will NOT be persisted to Neo4j — only the "
                "in-memory RecordingGraphBuilder will be used. This "
                "is a launch-blocking condition for production. "
                "Either start Neo4j or set DRUGOS_ALLOW_NO_NEO4J=1 "
                "to acknowledge. Exit code 3 will be returned (per the "
                "updated EXIT CODES contract at lines 43-56 — applies "
                "to CLI-supplied, env-var-auto-detected, AND default-"
                "localhost Neo4j URIs alike).",
                _env, neo4j_uri,
            )
            if os.environ.get("DRUGOS_ALLOW_NO_NEO4J") != "1":
                return 3

    # ─── 3. Run the bridge ────────────────────────────────────────────────
    from drugos_graph.phase1_bridge import run_phase1_to_phase2

    log.info("Running Phase 1 → Phase 2 bridge...")
    result = run_phase1_to_phase2(
        phase1_processed_dir=args.phase1_dir,
        builder=builder,
        batch_size=args.batch_size,
    )

    summary: Dict[str, Any] = result["summary"]

    # v34 ROOT FIX (NEO4J PERSISTENCE): the previous code used
    # RecordingGraphBuilder (in-memory) by default and NEVER persisted
    # the staged graph to disk. On process exit, all 67 nodes and 68
    # edges were lost. The user explicitly complained: "All data lives
    # in RecordingGraphBuilder (in-memory). Nothing persists. No Neo4j
    # writes." The fix: ALWAYS persist the staged graph to disk as a
    # JSON file (phase2/data/processed/staged_graph.json) so the data
    # survives process exit. This is NOT a replacement for Neo4j — it's
    # a fallback for dry-run mode + a debug artifact for production.
    # When --neo4j-uri is set, the bridge ALSO writes to Neo4j (above).
    #
    # v75 ROOT FIX (T-033 — ``'_persist_path' in dir()`` unreliable):
    #   The v74 except handler at line 692 used
    #   ``_persist_path if '_persist_path' in dir() else 'staged_graph.json'``
    #   to fall back to a placeholder filename when the exception fired
    #   before ``_persist_path`` was assigned. ``dir()`` with no args
    #   returns the names in the CURRENT scope (local, then enclosing,
    #   then global, then builtin) — it WORKS in this case but is
    #   unusual and fragile (a future refactor that moves the except
    #   handler into a different scope could break it). The Pythonic
    #   pattern is to initialize ``_persist_path = None`` BEFORE the
    #   try block and check ``if _persist_path is not None``. This
    #   makes the intent explicit and survives refactoring.
    _persist_path = None  # v75 T-033: explicit None init before try
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
            "Staged graph PERSISTED to %s (dry-run artifact — Neo4j is "
            "the production store when --neo4j-uri is set).",
            _persist_path,
        )
    except OSError as _persist_os_exc:
        # v54 ROOT FIX (ROOT-4 — staged_graph.json bare except):
        # Split the bare `except Exception` into specific clauses.
        # OSError (disk full, permission denied, path too long) is
        # ERROR-level — the operator needs to know the graph was NOT
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
        # only — no ``dir()`` introspection, no scope ambiguity.
        _path_for_msg = str(_persist_path) if _persist_path is not None else "staged_graph.json (default — assignment did not run)"
        log.error(
            "FAILED to persist staged graph to disk (OSError): %s. "
            "The staged graph was loaded into the builder but NOT "
            "written to %s. Downstream consumers (Phase 3, RL ranker) "
            "will NOT find this file. Check disk space and permissions. "
            "(v54 ROOT-4 fix — was silently dropped in v48; v75 T-033 "
            "fix — replaced dir() check with explicit None init).",
            _persist_os_exc,
            _path_for_msg,
        )
    except (TypeError, ValueError) as _persist_json_exc:
        # JSON serialization errors (non-serializable objects in payload)
        # — ERROR-level because this indicates a code bug in the payload
        # construction.
        log.error(
            "FAILED to persist staged graph to disk (JSON serialization "
            "error): %s. The staged graph payload contains non-serializable "
            "objects. This is a code bug in run_unified.py's persistence "
            "block — report to the development team. (v54 ROOT-4 fix).",
            _persist_json_exc,
        )
    except Exception as _persist_exc:
        # Catch-all for unexpected errors — WARNING (non-fatal) but
        # with full traceback so the operator can diagnose.
        log.warning(
            "Failed to persist staged graph to disk (unexpected error, "
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
        log.error("Zero nodes loaded — Phase 1 outputs likely missing or empty.")
        return 1
    if summary["edges_loaded"] == 0:
        log.error("Zero edges loaded — interactions or OMIM CSV likely empty.")
        return 2

    log.info("=" * 70)
    log.info("UNIFIED RUN COMPLETE — %d nodes, %d edges loaded",
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
            + "!!! SAMPLE-MODE KG — NOT PRODUCTION-SCALE (T-011) !!!\n"
            + "!" * 78 + "\n"
            + f"The Knowledge Graph just loaded ({summary['nodes_loaded']} "
            + f"nodes, {summary['edges_loaded']} edges) was built from\n"
            + "EMBEDDED SAMPLE CSVs, NOT real biomedical data. The AUC\n"
            + "and V1 launch criteria reported by Phase 2 are NOT valid\n"
            + "for production sign-off — they reflect transductive\n"
            + "memorisation on a tiny graph, NOT generalisation.\n"
            + "\n"
            + "DO NOT report this run as a V1 launch candidate.\n"
            + "\n"
            + "To load full production data, run Phase 1 with API\n"
            + "credentials (DRUGBANK_XML_PATH, DISGENET_API_KEY,\n"
            + "OMIM_API_KEY) and re-run this script — OR pass\n"
            + "--require-full-data to fail-fast in CI.\n"
            + "!" * 78 + "\n"
        )
        log.warning(_final_sample_warning)
        print(_final_sample_warning, file=sys.stderr)

    # ─── 6. v15 ROOT FIX (REM-25): optionally run the FULL Phase 2 pipeline ─
    # v14's run_unified.py stopped at the bridge — it never trained TransE,
    # never built PyG HeteroData, never validated, never checked V1 launch
    # criteria. The "unified runner" was therefore theater: it loaded nodes
    # and edges into a RecordingGraphBuilder and exited 0, but the project's
    # headline deliverable (the >0.85 AUC) was never computed by THIS entry
    # point. Operators had to manually invoke `python -m drugos_graph` —
    # which most users never did, leading to the user's complaint that "every
    # session every AI tells its 100 percent integrated but see the reality."
    # Fix: when --full-pipeline is passed, chain directly into
    # run_pipeline.run_full_pipeline(data_source="phase1") so the unified
    # runner actually produces a model, an AUC, and a launch verdict.
    if args.full_pipeline:
        log.info("-" * 70)
        log.info("FULL PIPELINE — Step 8 (entity_resolution) → Step 9 (PyG build) "
                 "→ Step 10 (training data) → Step 11 (TransE train) → "
                 "Step 12 (validation) → V1 launch criteria")
        log.info("-" * 70)
        try:
            from drugos_graph.run_pipeline import run_full_pipeline
            # v73 ROOT FIX (T-010 — env-var Neo4j path caused double-load):
            #   The previous predicate checked ONLY the CLI arg for Neo4j
            #   URI presence. If the operator set the DRUGOS_NEO4J_URI env
            #   var (the recommended production setup per the docstring at
            #   lines 24-26) but did NOT pass --neo4j-uri on the CLI, the
            #   predicate evaluated to False (skip_neo4j=False). But the
            #   bridge at line 473 had ALREADY connected to Neo4j using
            #   the env-var-resolved URI (line 441-445) and loaded the
            #   staged graph. run_full_pipeline then opened a SECOND Neo4j
            #   session and re-loaded the same graph — duplicate nodes,
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
            #   connection failed → RecordingGraphBuilder fallback),
            #   ``skip_neo4j=False`` is harmless — there is no Neo4j to
            #   skip, and ``run_full_pipeline`` falls back to its own
            #   in-memory builder internally. This single-flag predicate
            #   correctly handles ALL three Neo4j modes:
            #     (a) --neo4j-uri CLI arg → neo4j_connected=True
            #         → skip_neo4j=True ✓
            #     (b) DRUGOS_NEO4J_URI env var → neo4j_connected=True
            #         → skip_neo4j=True ✓ (was the bug)
            #     (c) No Neo4j available → neo4j_connected=False
            #         → skip_neo4j=False ✓ (harmless, no-op)
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
                log.error("V1 LAUNCH CRITERIA NOT MET — see report above.")
                return 4
            log.info("=" * 70)
            log.info("FULL PIPELINE COMPLETE — V1 criteria satisfied")
            log.info("=" * 70)
        except SystemExit as exc:
            # v21 ROOT FIX (Audit Chain 12): run_pipeline.py previously
            # called sys.exit(1) directly when V1 launch criteria fail.
            # The previous ``except Exception`` clause did NOT catch
            # SystemExit (SystemExit derives from BaseException, not
            # Exception), so the exit code propagated through
            # run_unified.py and crashed any parent orchestrator
            # (Airflow/Celery/K8s Job). The documented contract said
            # exit code 4 = V1 launch criteria not met — but that
            # contract was DEAD because sys.exit(1) hijacked the exit.
            # Now run_pipeline raises V1LaunchCriteriaFailed instead
            # (caught below). This SystemExit catch is defensive — it
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
        except Exception as exc:
            # v21 ROOT FIX: catch V1LaunchCriteriaFailed (our typed
            # exception from run_pipeline) and translate to exit code 4.
            # All other Exceptions get exit code 5.
            exc_module = type(exc).__module__
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
