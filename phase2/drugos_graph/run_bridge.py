"""phase2/drugos_graph/run_bridge.py — Phase 1 → Phase 2 bridge entrypoint.

IN-091 v117 ROOT FIX (Teammate 8): the previous docker-compose.yml
phase2-kg-builder service used a fragile 4-level-escaping bash -lc
command:

    command: >
      bash -lc "python -c 'from phase2.drugos_graph.phase1_bridge
      import run_phase1_to_phase2; run_phase1_to_phase2(
      phase1_processed_dir=\"/opt/phase1/processed_data\",
      prefer_postgres=False)'"

YAML `>` folds newlines into spaces, bash `-lc "..."` uses double
quotes, Python `-c '...'` uses single quotes, and the Python string
argument `"/opt/phase1/processed_data"` uses escaped double quotes `\"`.
This is FOUR levels of escaping. A single missing backslash or a YAML
formatter breaking the folding silently breaks the command.

ROOT FIX: this script replaces the inline Python one-liner with a
dedicated Python entrypoint. The docker-compose command becomes:

    command: ["python", "/opt/repo/phase2/drugos_graph/run_bridge.py"]

No escaping, no shell, no folding. Standard Python argparse handles
arguments. The script is also independently runnable:

    python phase2/drugos_graph/run_bridge.py \\
        --phase1-processed-dir /opt/phase1/processed_data \\
        --no-prefer-postgres

Usage:
    python phase2/drugos_graph/run_bridge.py [OPTIONS]

Options:
    --phase1-processed-dir PATH   Path to Phase 1 processed_data directory
                                  (default: $PHASE1_PROCESSED_DIR or
                                  /opt/phase1/processed_data)
    --prefer-postgres             Read from Phase 1 Postgres DB instead of
                                  CSVs (default: False — use CSVs)
    --no-prefer-postgres          Explicitly use CSVs (the default)

Exit codes:
    0 — bridge ran successfully
    1 — bridge failed (missing data, KG builder error, etc.)
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from pathlib import Path


def main() -> int:
    """Run the Phase 1 → Phase 2 bridge and exit with status code."""
    parser = argparse.ArgumentParser(
        description="Phase 1 → Phase 2 bridge: load Phase 1 processed_data "
                    "into the Phase 2 knowledge graph.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--phase1-processed-dir", type=str,
        default=os.environ.get(
            "PHASE1_PROCESSED_DIR",
            "/opt/phase1/processed_data",
        ),
        help="Path to Phase 1 processed_data directory (default: "
             "$PHASE1_PROCESSED_DIR or /opt/phase1/processed_data).",
    )
    prefer_pg_group = parser.add_mutually_exclusive_group()
    prefer_pg_group.add_argument(
        "--prefer-postgres", action="store_true", default=False,
        help="Read from Phase 1 Postgres DB instead of CSVs.",
    )
    prefer_pg_group.add_argument(
        "--no-prefer-postgres", dest="prefer_postgres",
        action="store_false", default=False,
        help="Explicitly use CSVs (the default).",
    )
    args = parser.parse_args()

    # Ensure the repo root is on sys.path so `phase2.drugos_graph.*`
    # imports work even when this script is invoked directly (not via
    # `python -m phase2.drugos_graph.run_bridge`).
    repo_root = str(Path(__file__).resolve().parents[2])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    print(
        f"[run_bridge] Starting Phase 1 → Phase 2 bridge with:\n"
        f"  phase1_processed_dir: {args.phase1_processed_dir}\n"
        f"  prefer_postgres:      {args.prefer_postgres}\n"
        f"  repo_root on path:    {repo_root}",
        flush=True,
    )

    # Verify the Phase 1 directory exists before importing the bridge
    # (gives a clearer error than the bridge's own FileNotFoundError).
    phase1_dir = Path(args.phase1_processed_dir)
    if not phase1_dir.exists():
        print(
            f"[run_bridge] ERROR: Phase 1 processed_data directory does not "
            f"exist: {phase1_dir}. Ensure Phase 1 ETL has run (Airflow "
            f"phase1-airflow service or `python -m phase1.pipelines`), then "
            f"retry.",
            file=sys.stderr,
        )
        return 1

    try:
        # Import inside main() so argparse errors (--help) don't trigger
        # the bridge's heavy imports (neo4j, pandas, networkx).
        from phase2.drugos_graph.phase1_bridge import run_phase1_to_phase2
    except ImportError as exc:
        print(
            f"[run_bridge] ERROR: failed to import "
            f"phase2.drugos_graph.phase1_bridge.run_phase1_to_phase2: {exc}\n"
            f"  This usually means PYTHONPATH is misconfigured. Expected "
            f"phase2/ on PYTHONPATH (got: {sys.path[:5]}).",
            file=sys.stderr,
        )
        return 1

    try:
        result = run_phase1_to_phase2(
            phase1_processed_dir=args.phase1_processed_dir,
            prefer_postgres=args.prefer_postgres,
        )
        print(
            f"[run_bridge] Phase 1 → Phase 2 bridge completed successfully. "
            f"Result: {result}",
            flush=True,
        )
        return 0
    except Exception as exc:
        print(
            f"[run_bridge] ERROR: Phase 1 → Phase 2 bridge failed: {exc}\n"
            f"{traceback.format_exc()}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
