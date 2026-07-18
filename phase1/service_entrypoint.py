#!/usr/bin/env python3
"""phase1/service_entrypoint.py — Phase 1 Dataset Service launcher.

v122 FORENSIC ROOT FIX (Teammate 15 — hostile-auditor, BUG-7):
    The v116 docker-compose.yml phase1-service command was:

        command: >
          bash -lc "cd /opt/phase1 && python -c 'from service import app;
          import uvicorn; uvicorn.run(app, host=\"0.0.0.0\", port=8000)'"

    This is the SAME 4-level escaping pattern (YAML `>`, bash `-lc`,
    python `-c`, Python string literal) that IN-091 was created to fix
    for the phase2-kg-builder service. The phase2 fix was to create a
    dedicated Python entrypoint (phase2/drugos_graph/run_bridge.py) and
    invoke it with `command: ["python", "/opt/repo/phase2/drugos_graph/
    run_bridge.py", "--no-prefer-postgres"]` — no escaping, no shell,
    no folding.

    ROOT FIX: this script is the Phase 1 equivalent. The docker-compose
    command becomes:

        command: ["python", "/opt/phase1/service_entrypoint.py"]

    Standard argparse handles arguments. The script is also independently
    runnable for dev/CI:

        python phase1/service_entrypoint.py --host 0.0.0.0 --port 8000

Usage:
    python phase1/service_entrypoint.py [OPTIONS]

Options:
    --host HOST       Bind host (default: 0.0.0.0)
    --port PORT       Bind port (default: 8000, or $PHASE1_SERVICE_PORT)
    --reload          Enable uvicorn auto-reload (dev only)

Exit codes:
    0 — server shut down cleanly
    1 — configuration or import error
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main() -> int:
    """Launch the Phase 1 Dataset Service."""
    parser = argparse.ArgumentParser(
        description="Phase 1 Dataset Service launcher.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--host", type=str,
        default=os.environ.get("PHASE1_SERVICE_HOST", "0.0.0.0"),
        help="Bind host (default: 0.0.0.0 or $PHASE1_SERVICE_HOST).",
    )
    parser.add_argument(
        "--port", type=int,
        default=int(os.environ.get("PHASE1_SERVICE_PORT", "8000")),
        help="Bind port (default: 8000 or $PHASE1_SERVICE_PORT).",
    )
    parser.add_argument(
        "--reload", action="store_true", default=False,
        help="Enable uvicorn auto-reload (DEV ONLY — do not use in production).",
    )
    args = parser.parse_args()

    # Ensure the phase1/ directory is on sys.path so `from service import app`
    # works regardless of CWD. The docker-compose service mounts `./phase1`
    # at `/opt/phase1` and sets PYTHONPATH=/opt/phase1:/opt/repo — this
    # script lives at /opt/phase1/service_entrypoint.py, so its parent
    # (/opt/phase1) is the phase1 package root.
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))

    print(
        f"[phase1-service] Starting Phase 1 Dataset Service on "
        f"{args.host}:{args.port} (reload={args.reload})",
        flush=True,
    )

    try:
        import uvicorn
        from service import app  # type: ignore[import-not-found]
    except ImportError as exc:
        print(
            f"[phase1-service] ERROR: failed to import 'service.app' or "
            f"uvicorn: {exc}\n"
            f"  PYTHONPATH={os.environ.get('PYTHONPATH', '<unset>')}\n"
            f"  sys.path[:5]={sys.path[:5]}\n"
            f"  Ensure the phase1/ directory (containing service.py) is on "
            f"PYTHONPATH and that fastapi/uvicorn are installed.",
            file=sys.stderr,
        )
        return 1

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=os.environ.get("PHASE1_LOG_LEVEL", "info"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
