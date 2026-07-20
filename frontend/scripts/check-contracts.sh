#!/usr/bin/env bash
# frontend/scripts/check-contracts.sh
# ─────────────────────────────────────────────────────────────────
# CI check: verify frontend/contracts/api_contracts.ts is in sync
# with the Python services' OpenAPI schemas.
#
# Task 13.4 (SH-006) v131 ROOT FIX (Teammate 13):
#   This script regenerates api_contracts.ts to a TEMP location and
#   compares it to the committed file. If they differ, the check fails
#   with instructions on how to regenerate. This prevents the contract
#   drift that the audit flagged — a Python service change WITHOUT a
#   corresponding api_contracts.ts regeneration will now block CI.
#
#   v131 switched from the custom Python generator to openapi-typescript
#   (the canonical tool the task required). openapi-typescript v7.13.0
#   works now that the project uses TypeScript 5.9.3 (the version the
#   JS/TS ecosystem supports). The v129 custom Python generator was a
#   workaround for openapi-typescript's TypeScript 7 incompatibility.
#
# IMPORTANT: this script does NOT modify the working tree. It generates
#   the regenerated file to a temp path and diffs against the committed
#   file. The developer's uncommitted changes are safe.
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
  echo "       Common causes:" >&2
  echo "         - Missing Python deps (torch, gymnasium) — install with:" >&2
  echo "             pip install torch --index-url https://download.pytorch.org/whl/cpu" >&2
  echo "             pip install gymnasium" >&2
  echo "         - Python service import error (check the service's __init__)" >&2
  exit 2
fi

echo "[check-contracts] Assembling TypeScript contracts via openapi-typescript…"
# Generate the regenerated file to TMP_CONTRACTS (NOT the working tree).
# assemble-contracts.mjs reads openapi.json — we pass the temp openapi via
# a temp copy in the expected location, then restore.
cp "$OPENAPI_JSON" "${OPENAPI_JSON}.check-bak"
trap 'rm -f "$TMP_OPENAPI" "$TMP_CONTRACTS"; mv -f "${OPENAPI_JSON}.check-bak" "$OPENAPI_JSON" 2>/dev/null || true' EXIT
cp "$TMP_OPENAPI" "$OPENAPI_JSON"

# Pass the temp output path as an argument so assemble-contracts.mjs writes
# there instead of overwriting the committed file.
if ! node frontend/scripts/assemble-contracts.mjs "$TMP_CONTRACTS" >/dev/null 2>&1; then
  echo "ERROR: assemble-contracts.mjs failed. See above for details." >&2
  echo "       Common causes:" >&2
  echo "         - openapi-typescript not installed (npm install --legacy-peer-deps)" >&2
  echo "         - openapi.json is malformed" >&2
  exit 2
fi

# Restore the original openapi.json (the trap also does this, but do it
# explicitly so the diff below uses the committed openapi.json state).
mv -f "${OPENAPI_JSON}.check-bak" "$OPENAPI_JSON"
trap 'rm -f "$TMP_OPENAPI" "$TMP_CONTRACTS"' EXIT

# Compare the committed CONTRACTS_TS to the regenerated TMP_CONTRACTS.
# No volatile fields to strip — openapi-typescript output is deterministic
# (sorted by path, stable schema ordering). The preamble has no timestamps.
if ! diff -q "$CONTRACTS_TS" "$TMP_CONTRACTS" >/dev/null; then
  echo "ERROR: frontend/contracts/api_contracts.ts is out of sync with the Python services." >&2
  echo "" >&2
  echo "To fix, run:" >&2
  echo "    npm run gen:contracts" >&2
  echo "    git add frontend/contracts/openapi.json frontend/contracts/api_contracts.ts" >&2
  echo "    git commit -m 'contracts: regenerate api_contracts.ts from Python OpenAPI'" >&2
  echo "" >&2
  echo "Diff (committed ← regenerated):" >&2
  diff -u "$CONTRACTS_TS" "$TMP_CONTRACTS" | head -80 >&2 || true
  exit 1
fi

echo "[check-contracts] ✓ frontend/contracts/api_contracts.ts is in sync."
exit 0
