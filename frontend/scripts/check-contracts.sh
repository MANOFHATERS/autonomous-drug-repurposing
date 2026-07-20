#!/usr/bin/env bash
# frontend/scripts/check-contracts.sh
# ─────────────────────────────────────────────────────────────────
# CI check: verify frontend/contracts/api_contracts.ts is in sync
# with the Python services' OpenAPI schemas.
#
# Task 13.4 (SH-006) ROOT FIX (Teammate 13, v129):
#   This script regenerates api_contracts.ts in a temp location and
#   compares it to the committed file. If they differ, the check fails
#   with instructions on how to regenerate. This prevents the contract
#   drift that the audit flagged — a Python service change WITHOUT a
#   corresponding api_contracts.ts regeneration will now block CI.
#
# Usage:
#   bash frontend/scripts/check-contracts.sh
#
# Exit codes:
#   0 — contracts are in sync
#   1 — contracts are out of sync (regenerate and commit)
#   2 — environment error (Python deps missing, etc.)
# ─────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

OPENAPI_JSON="frontend/contracts/openapi.json"
CONTRACTS_TS="frontend/contracts/api_contracts.ts"
TMP_OPENAPI="$(mktemp)"
TMP_CONTRACTS="$(mktemp)"
trap 'rm -f "$TMP_OPENAPI" "$TMP_CONTRACTS"' EXIT

echo "[check-contracts] Extracting OpenAPI schemas from Python services…"
if ! python3 frontend/scripts/extract_openapi.py "$TMP_OPENAPI" >/dev/null 2>&1; then
  echo "ERROR: extract_openapi.py failed. See above for details." >&2
  exit 2
fi

echo "[check-contracts] Generating TypeScript contracts…"
if ! python3 frontend/scripts/generate_api_contracts.py "$TMP_OPENAPI" "$TMP_CONTRACTS" >/dev/null 2>&1; then
  echo "ERROR: generate_api_contracts.py failed. See above for details." >&2
  exit 2
fi

# The generated file embeds a "Generation timestamp" line that changes
# every run — strip it before comparing so the check is deterministic.
# Also strip the openapi.json source line (in case the temp path leaked).
strip_volatile() {
  local file="$1"
  # Remove the timestamp line and any line that mentions the volatile
  # openapi.json temp path. Keep everything else.
  grep -v -E '^\s*\* Generation timestamp:' "$file" \
    | grep -v -E '^\s*\* OpenAPI schema source:' || true
}

if ! diff -q \
    <(strip_volatile "$CONTRACTS_TS") \
    <(strip_volatile "$TMP_CONTRACTS") >/dev/null; then
  echo "ERROR: frontend/contracts/api_contracts.ts is out of sync with the Python services." >&2
  echo "" >&2
  echo "To fix, run:" >&2
  echo "    python3 frontend/scripts/extract_openapi.py" >&2
  echo "    python3 frontend/scripts/generate_api_contracts.py" >&2
  echo "    git add frontend/contracts/openapi.json frontend/contracts/api_contracts.ts" >&2
  echo "    git commit -m 'contracts: regenerate api_contracts.ts from Python OpenAPI'" >&2
  echo "" >&2
  echo "Diff (committed ← regenerated):" >&2
  diff -u \
    <(strip_volatile "$CONTRACTS_TS") \
    <(strip_volatile "$TMP_CONTRACTS") | head -80 >&2 || true
  exit 1
fi

echo "[check-contracts] ✓ frontend/contracts/api_contracts.ts is in sync."
exit 0
