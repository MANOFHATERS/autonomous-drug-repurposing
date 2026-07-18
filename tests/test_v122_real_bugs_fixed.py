#!/usr/bin/env python3
"""v122 FORENSIC ROOT FIX verification — Teammate 15 hostile-auditor pass.

This script verifies that the 7 REAL bugs found by reading the actual code
(comments claimed "ROOT FIX" but the code was still broken) are NOW actually
fixed. Each test reads the actual code/file and asserts the fix is in place.

Run:
    python3 tests/test_v122_real_bugs_fixed.py
"""
from __future__ import annotations

import sys
import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PASS = 0
FAIL = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        print(f"  PASS: {label}")
        PASS += 1
    else:
        print(f"  FAIL: {label} {detail}")
        FAIL += 1


def section(title: str) -> None:
    print()
    print(f"=== {title} ===")


# ─── BUG-1: Dockerfile.python-ml must use python:3.12-slim (not 3.14) ────────
section("BUG-1: Dockerfile.python-ml base image")
dockerfile_pyml_lines = (REPO_ROOT / "Dockerfile.python-ml").read_text().splitlines()
# Find the actual FROM line (not commented)
from_lines = [l for l in dockerfile_pyml_lines if l.startswith("FROM ")]
check(
    "Dockerfile.python-ml has exactly one FROM line",
    len(from_lines) == 1,
    detail=f"got {len(from_lines)} FROM lines: {from_lines}",
)
check(
    "Dockerfile.python-ml FROM uses python:3.12-slim (not 3.14)",
    from_lines and "python:3.12-slim" in from_lines[0],
    detail=f"got: {from_lines}",
)
check(
    "Dockerfile.python-ml FROM does NOT use python:3.14-slim (broken — no torch wheels)",
    from_lines and "python:3.14-slim" not in from_lines[0],
)


# ─── BUG-2: Dockerfile.gpu must not use --prefix=/install in single-stage ──
section("BUG-2: Dockerfile.gpu single-stage build")
dockerfile_gpu_lines = (REPO_ROOT / "Dockerfile.gpu").read_text().splitlines()
# Find actual RUN pip install lines (not commented)
pip_install_lines = [
    l for l in dockerfile_gpu_lines
    if l.strip().startswith("RUN pip install") or l.strip().startswith("pip install")
]
prefix_install_count = sum(1 for l in pip_install_lines if "--prefix=/install" in l)
check(
    "Dockerfile.gpu does NOT use --prefix=/install in RUN pip install lines",
    prefix_install_count == 0,
    detail=f"found {prefix_install_count} RUN pip install lines with --prefix=/install",
)
# Verify source directories are COPYed (IN-092 fix applied)
dockerfile_gpu = (REPO_ROOT / "Dockerfile.gpu").read_text()
check(
    "Dockerfile.gpu COPYs phase1/ source",
    "COPY --chown=drugos:drugos phase1/ ./phase1/" in dockerfile_gpu,
)
check(
    "Dockerfile.gpu COPYs phase2/ source",
    "COPY --chown=drugos:drugos phase2/ ./phase2/" in dockerfile_gpu,
)
check(
    "Dockerfile.gpu COPYs scripts/ source",
    "COPY --chown=drugos:drugos scripts/ ./scripts/" in dockerfile_gpu,
)
check(
    "Dockerfile.gpu COPYs run_4phase.py",
    "COPY --chown=drugos:drugos run_4phase.py ./" in dockerfile_gpu,
)
check(
    "Dockerfile.gpu uses Python 3.12 (matches Dockerfile.ml)",
    "python3.12" in dockerfile_gpu,
)


# ─── BUG-3: backup.sh has TIMESTAMP inside loop + _FILE secret support ─────
section("BUG-3: observability/backup.sh")
backup_sh = (REPO_ROOT / "observability" / "backup.sh").read_text()
# TIMESTAMP must be computed INSIDE the loop (after "while true; do")
loop_start = backup_sh.find("while true; do")
assert loop_start > 0, "while loop not found in backup.sh"
loop_section = backup_sh[loop_start:]
check(
    "backup.sh computes TIMESTAMP inside the loop (not at script start)",
    "TIMESTAMP=" in loop_section,
    detail="TIMESTAMP= must be inside the while loop",
)
check(
    "backup.sh does NOT compute TIMESTAMP at script-start scope",
    "TIMESTAMP=\"" not in backup_sh[:loop_start],
    detail="TIMESTAMP was found before the while loop",
)
check(
    "backup.sh reads POSTGRES_PASSWORD_FILE (Docker secret pattern)",
    "_read_secret POSTGRES_PASSWORD_FILE POSTGRES_PASSWORD" in backup_sh,
)
check(
    "backup.sh reads NEO4J_PASSWORD_FILE (Docker secret pattern)",
    "_read_secret NEO4J_PASSWORD_FILE NEO4J_PASSWORD" in backup_sh,
)
check(
    "backup.sh defines the _read_secret helper function",
    "_read_secret()" in backup_sh,
)
# Validate shell syntax
result = subprocess.run(
    ["sh", "-n", str(REPO_ROOT / "observability" / "backup.sh")],
    capture_output=True, text=True,
)
check(
    "backup.sh passes 'sh -n' syntax check",
    result.returncode == 0,
    detail=f"stderr: {result.stderr}",
)

# Also verify the docker-compose.yml pg-backup service mounts the neo4j_password secret
compose = (REPO_ROOT / "docker-compose.yml").read_text()
pg_backup_section = compose[compose.find("pg-backup:"):]
check(
    "docker-compose.yml pg-backup service mounts neo4j_password secret",
    "- neo4j_password" in pg_backup_section[:pg_backup_section.find("networks:")],
)
check(
    "docker-compose.yml pg-backup service sets NEO4J_PASSWORD_FILE env var",
    "NEO4J_PASSWORD_FILE: /run/secrets/neo4j_password" in pg_backup_section,
)


# ─── BUG-4: /metrics endpoint exposed on every FastAPI service ─────────────
section("BUG-4: /metrics endpoint on FastAPI services")

# Verify each service FILE imports shared.observability.configure_app
# (We test the file content rather than importing because importing requires
# all deps installed; the file-level check is sufficient evidence of wiring.)
for service_file, service_name in [
    ("phase1/service.py", "phase1-dataset"),
    ("phase2/service.py", "phase2-kg-api"),
    ("scripts/gt_api.py", "phase3-gt-api"),
    ("rl/service.py", "phase4-rl"),
]:
    fpath = REPO_ROOT / service_file
    content = fpath.read_text()
    check(
        f"{service_file} imports shared.observability.configure_app",
        "from shared.observability import configure_app" in content,
    )
    check(
        f"{service_file} calls configure_app(app, service_name='{service_name}')",
        f"service_name=\"{service_name}\"" in content or f"service_name='{service_name}'" in content,
    )

# Verify shared/observability/__init__.py exists and exports configure_app
obs = (REPO_ROOT / "shared" / "observability" / "__init__.py").read_text()
check(
    "shared/observability/__init__.py defines configure_app()",
    "def configure_app(" in obs,
)
check(
    "shared/observability/__init__.py mounts /metrics endpoint",
    'app.mount("/metrics"' in obs,
)
check(
    "shared/observability/__init__.py uses prometheus_client.make_asgi_app",
    "make_asgi_app" in obs,
)


# ─── BUG-5: structured JSON logging at application level ───────────────────
section("BUG-5: structured JSON logging")
check(
    "shared/observability defines a JSON formatter class",
    "class _JsonFormatter" in obs,
)
check(
    "JSON formatter serializes log records as JSON",
    "json.dumps(log_obj" in obs,
)
check(
    "shared/observability configures logging on the root logger",
    "_configure_logging" in obs and "root = logging.getLogger()" in obs,
)
# Verify each service imports the observability module (already checked in BUG-4)


# ─── BUG-6: OpenTelemetry instrumentation ──────────────────────────────────
section("BUG-6: OpenTelemetry instrumentation")
check(
    "shared/observability instruments FastAPI with OTel",
    "FastAPIInstrumentor.instrument_app(app)" in obs,
)
check(
    "shared/observability reads OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_ENDPOINT" in obs,
)
# Verify requirements.txt has opentelemetry deps
reqs = (REPO_ROOT / "requirements.txt").read_text()
check(
    "requirements.txt includes opentelemetry-instrumentation-fastapi",
    "opentelemetry-instrumentation-fastapi" in reqs,
)
check(
    "requirements.txt includes opentelemetry-exporter-otlp",
    "opentelemetry-exporter-otlp" in reqs,
)
check(
    "requirements.txt includes opentelemetry-sdk",
    "opentelemetry-sdk" in reqs,
)


# ─── BUG-7: phase1-service uses dedicated entrypoint (no fragile bash -lc) ─
section("BUG-7: phase1-service entrypoint")
# Verify phase1/service_entrypoint.py exists
entrypoint = REPO_ROOT / "phase1" / "service_entrypoint.py"
check(
    "phase1/service_entrypoint.py exists",
    entrypoint.exists(),
)
if entrypoint.exists():
    ep_content = entrypoint.read_text()
    check(
        "entrypoint uses argparse (not bash -lc)",
        "argparse.ArgumentParser" in ep_content,
    )
    check(
        "entrypoint imports uvicorn",
        "import uvicorn" in ep_content,
    )
    check(
        "entrypoint imports 'from service import app'",
        "from service import app" in ep_content,
    )
# Verify docker-compose.yml uses the entrypoint (not the fragile bash -lc)
phase1_service_section = compose[compose.find("  phase1-service:"):]
phase1_service_section = phase1_service_section[:phase1_service_section.find("\n  #", 10)]
check(
    "docker-compose.yml phase1-service uses ['python', '/opt/phase1/service_entrypoint.py']",
    '["python", "/opt/phase1/service_entrypoint.py"]' in phase1_service_section
    or 'command: ["python", "/opt/phase1/service_entrypoint.py"]' in phase1_service_section,
    detail="phase1-service command must be the dedicated entrypoint, not bash -lc",
)
check(
    "docker-compose.yml phase1-service does NOT use bash -lc with python -c",
    "bash -lc \"cd /opt/phase1 && python -c" not in phase1_service_section,
)


# ─── Final summary ─────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"PASS: {PASS}")
print(f"FAIL: {FAIL}")
print("=" * 60)
sys.exit(0 if FAIL == 0 else 1)
