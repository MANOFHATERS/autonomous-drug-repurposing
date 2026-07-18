#!/usr/bin/env bash
# Run all backend tests + integration tests + E2E tests.
# Usage: bash scripts/run-all-tests.sh
#
# IN-034 ROOT FIX (Teammate 13, HIGH): the previous version used `set -e`
# but piped every test command through `tail` / `grep | tail`. In bash, the
# exit code of a pipeline is the exit code of the LAST command (`tail`),
# and `tail` almost always exits 0 — so `set -e` never triggered. The
# script printed "All tests complete." even when every test failed, and
# `npm test` always exited 0. This made the test suite a false-positive
# gate: broken code shipped because the runner lied.
#
# ROOT FIX:
#   1. `set -euo pipefail` — pipefail makes a pipeline's exit code reflect
#      the FIRST failing command (the test runner), not the trailing `tail`.
#   2. Each stage's exit code is captured via `${PIPESTATUS[0]}` (the test
#      command's code, NOT tail's) and accumulated.
#   3. The script exits with a non-zero code if ANY stage failed, and the
#      final message reports the real outcome (PASS/FAIL), never a blanket
#      "All tests complete".
#   4. `set -e` is intentionally NOT enabled for the test stages. The
#      script starts with `set -uo pipefail` (no `-e`), so a failed test
#      command does NOT abort the script — we capture its exit code via
#      PIPESTATUS[0] and continue to the next stage. The previous version
#      toggled `set +e` / `set -e` around each stage; this was unnecessary
#      noise AND introduced a subtle bug (between stages `-e` was active,
#      so any stray non-test command failure would have aborted the run
#      early and hidden later-stage failures). The clean pattern is:
#      never enable `-e`, rely on `pipefail` + explicit PIPESTATUS capture.

set -uo pipefail

cd "$(dirname "$0")/.."

echo "==================================================="
echo "DrugOS Full Test Suite"
echo "==================================================="

# Accumulate failures across all stages. 0 = all passed, non-zero = at
# least one stage failed. We OR each stage's code in so the final exit
# code is non-zero if ANY stage failed.
OVERALL_RC=0

# Ensure no leftover dev servers from previous runs
pkill -9 -f "next dev" 2>/dev/null || true
sleep 2
rm -f .next/dev/lock

echo ""
echo "=== 1. Backend unit tests (Jest) ==="
echo ""
rm -f db/test.db
# IN-035 ROOT FIX: use `npx jest` (npm is the canonical package manager —
# the Dockerfile uses `npm ci`). `npx` resolves to the locally-installed
# jest (node_modules/.bin/jest), no network fetch. (Previous `bun x jest`
# required bun to be installed globally, which is NOT in package.json.)
#
# IN-034 ROOT FIX: `set -e` is intentionally NOT enabled at the top of
# this script (line 26 uses `set -uo pipefail` only). This means a failed
# test command does NOT abort the script — we capture its exit code via
# PIPESTATUS[0] and continue to the next stage, then OR the codes together
# so the final exit reflects whether ANY stage failed. The previous
# version toggled `set +e` / `set -e` around each stage, which was
# unnecessary noise AND introduced a subtle bug: between stages `set -e`
# was active, so any stray non-test command failure (e.g. a future
# `pkill` without `|| true`) would abort the run early and hide later
# stage failures. The clean pattern is: never enable `-e` for the test
# stages, rely on `pipefail` + explicit PIPESTATUS capture.
npx jest src/lib/services/__tests__/ --no-coverage --runInBand --forceExit 2>&1 | tail -40
# PIPESTATUS[0] is the jest exit code (the first command in the pipeline),
# NOT tail's exit code. This is the critical fix for IN-034.
JEST_RC=${PIPESTATUS[0]}
echo "Jest exit code: ${JEST_RC}"
OVERALL_RC=$((OVERALL_RC | JEST_RC))

echo ""
echo "=== 2. Integration tests (Node script) ==="
echo ""
pkill -9 -f "next dev" 2>/dev/null || true
sleep 2
rm -f .next/dev/lock
node scripts/run-integration-tests.js 2>&1 | tail -40
INTEGRATION_RC=${PIPESTATUS[0]}
echo "Integration exit code: ${INTEGRATION_RC}"
OVERALL_RC=$((OVERALL_RC | INTEGRATION_RC))

echo ""
echo "=== 3. E2E tests (Playwright) ==="
echo ""
pkill -9 -f "next dev" 2>/dev/null || true
sleep 2
rm -f .next/dev/lock
node scripts/run-e2e-tests.js 2>&1 | tail -40
E2E_RC=${PIPESTATUS[0]}
echo "E2E exit code: ${E2E_RC}"
OVERALL_RC=$((OVERALL_RC | E2E_RC))

echo ""
echo "==================================================="
if [ "${OVERALL_RC}" -eq 0 ]; then
  echo "RESULT: ALL TEST STAGES PASSED"
else
  echo "RESULT: TEST FAILURES DETECTED (jest=${JEST_RC}, integration=${INTEGRATION_RC}, e2e=${E2E_RC})"
fi
echo "==================================================="

# IN-034: exit with the real combined result so CI/release gates work.
exit "${OVERALL_RC}"
