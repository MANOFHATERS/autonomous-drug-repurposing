#!/bin/bash
# Persistent npm install runner — retries up to 3 times with exponential
# backoff until success.
#
# IN-033 ROOT FIX (Teammate 13, MEDIUM): the previous version hardcoded
# `cd /home/z/my-project` — a path that exists only on the original
# developer's machine. On any other machine (CI runner, another dev's
# laptop, a Docker container) the `cd` failed with "No such file or
# directory" and `npm install` then ran in the WRONG directory (or failed
# outright). It also passed `--legacy-peer-deps`, which masks real peer-
# dependency conflicts that should be fixed in package.json rather than
# silently overridden, and retried 10 times with a flat 5s sleep — a
# band-aid for flaky networks / a broken dep tree, not a fix.
#
# ROOT FIX:
#   1. `cd "$(dirname "$0")/.."` — standard portable pattern: cd to the
#      frontend dir (parent of scripts/), regardless of where the script
#      is invoked from. Works on every machine.
#   2. `set -euo pipefail` — fail fast on errors, undefined vars, and
#      pipeline failures.
#   3. Removed `--legacy-peer-deps`. If peer-dep conflicts surface, they
#      must be resolved in package.json (correct version pins), not hidden.
#   4. Reduced retries to 3 with exponential backoff (10s, 20s, 40s) —
#      enough to ride out a transient network blip, not enough to hide a
#      permanently broken dep tree. The script exits non-zero if all 3
#      attempts fail, so CI catches it.

set -euo pipefail

# Portable: cd to the frontend directory (parent of this script's dir).
cd "$(dirname "$0")/.."

LOG=/tmp/npm-install-loop.log
echo "=== Starting install loop at $(date) in $(pwd) ===" > "$LOG"

MAX_ATTEMPTS=3
attempt=0
while [ "$attempt" -lt "$MAX_ATTEMPTS" ]; do
  attempt=$((attempt + 1))
  backoff=$((10 * (2 ** (attempt - 1))))
  echo "--- Attempt $attempt/$MAX_ATTEMPTS at $(date) ---" >> "$LOG"
  # No --legacy-peer-deps: real peer-dep conflicts must surface, not be hidden.
  if npm install --no-audit --no-fund --omit=optional --prefer-offline --loglevel=error >> "$LOG" 2>&1; then
    echo "SUCCESS on attempt $attempt" >> "$LOG"
    echo "npm install succeeded on attempt $attempt (see $LOG)"
    exit 0
  fi
  echo "Attempt $attempt failed" >> "$LOG"
  if [ "$attempt" -lt "$MAX_ATTEMPTS" ]; then
    echo "Waiting ${backoff}s before retry..." >> "$LOG"
    sleep "$backoff"
  fi
done

echo "=== Install loop FAILED after $MAX_ATTEMPTS attempts at $(date) ===" >> "$LOG"
ls node_modules 2>/dev/null | wc -l >> "$LOG" || true
echo "ERROR: npm install failed after $MAX_ATTEMPTS attempts. See $LOG." >&2
exit 1
