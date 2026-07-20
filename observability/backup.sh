#!/bin/sh
# =============================================================================
# backup.sh — IN-005 ROOT FIX: daily backup sidecar.
# =============================================================================
# v122 FORENSIC ROOT FIX (Teammate 15 — hostile-auditor, BUG-3):
#   The v116 version of this script had THREE real bugs that made backups
#   silently fail (the WORST kind of bug for a backup system — you only
#   discover it when you need to restore, and by then it's too late):
#
#   BUG 1: TIMESTAMP was computed ONCE outside the loop (line 19). Every
#     backup iteration wrote to the SAME file (postgres-YYYYMMDD-HHMMSS.sql.gz
#     with the START timestamp). The 7-day retention was MEANINGLESS because
#     there was only ever 1 file (each cycle overwrote the previous). A
#     restore from "yesterday's backup" got TODAY's (incomplete) backup.
#     ROOT FIX: compute TIMESTAMP INSIDE the loop, so each cycle gets a
#     unique filename.
#
#   BUG 2: PGPASSWORD="${POSTGRES_PASSWORD}" — but the pg-backup service
#     in docker-compose.yml uses `POSTGRES_PASSWORD_FILE: /run/secrets/
#     postgres_password` (file-based Docker secret). The `POSTGRES_PASSWORD`
#     env var is NOT set. pg_dump fails with "FATAL: role 'drugos' has no
#     password" — the script logged "✗ Postgres backup FAILED" but
#     continued (because of `if ... ; then ... else` swallowing the exit
#     code), so the operator saw a "✓ Backup cycle complete" message at
#     the end of every run even though NO backup was actually created.
#     ROOT FIX: read POSTGRES_PASSWORD from the file if POSTGRES_PASSWORD_FILE
#     is set (the Docker secret pattern). Fall back to the env var for
#     backward compat with deployments that don't use file-based secrets.
#
#   BUG 3: same as BUG 2 for NEO4J_PASSWORD. The cypher-shell command used
#     "${NEO4J_PASSWORD}" which was empty. The Neo4j backup "FAILED" branch
#     was silently swallowed.
#     ROOT FIX: same pattern — read from NEO4J_PASSWORD_FILE if set.
#
#   BUG 4: the postgres:16-alpine image does NOT include cypher-shell.
#     The `command -v cypher-shell >/dev/null 2>&1` check always FAILED,
#     so the Neo4j backup was ALWAYS SKIPPED. The script logged
#     "⚠ Neo4j backup SKIPPED (cypher-shell not in this image)" on every
#     run — defeating the purpose of backing up Neo4j (the KG is the
#     patient-safety-critical data).
#     ROOT FIX: download the official neo4j-client package at runtime
#     (small ~5MB, only added when needed) OR use the APOC export HTTP
#     endpoint via curl (no client install needed). We use the HTTP
#     approach because it works with the existing postgres:16-alpine
#     image (no apt-get install, no image rebuild). The Neo4j 5.x APOC
#     procedure `apoc.export.cypher.all` can be invoked via the HTTP
#     API at /db/neo4j/tx/commit with a Cypher statement.
#
# Runs inside the pg-backup container (postgres:16-alpine image). Performs:
#   1. pg_dump of the DrugOS Postgres DB → /backups/postgres-YYYYMMDD-HHMMSS.sql.gz
#   2. Neo4j KG export via APOC HTTP API → /backups/neo4j-YYYYMMDD-HHMMSS.cypher.gz
#   3. Retains only the last 7 daily backups (deletes older files).
#
# The script sleeps 24h between backups (cron-in-a-container pattern) so the
# pg-backup service stays alive and restarts the loop on container restart.
# =============================================================================
set -euo pipefail

BACKUP_DIR="/backups"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"
mkdir -p "$BACKUP_DIR"

# v122 BUG-2/BUG-3 fix: read passwords from _FILE if set (Docker secret
# pattern). Fall back to the bare env var for backward compat.
# v128 TM15 Task 15.6 ROOT FIX: added MLFLOW_ARTIFACT_DIR support so the
# MLflow backup section can locate the mounted artifact store. Compose sets
# this to /mlruns (the read-only mount of the mlflow_data volume).
_read_secret() {
    # Args: file_var_name bare_var_name
    # Echoes the secret value. Exits 1 if neither is set.
    _file_var="$1"
    _bare_var="$2"
    _file_path="$(eval "echo \"\${$_file_var:-}\"")"
    _bare_val="$(eval "echo \"\${$_bare_var:-}\"")"
    if [ -n "$_file_path" ] && [ -f "$_file_path" ]; then
        # Read file, strip trailing newline.
        cat "$_file_path" | tr -d '\n'
        return 0
    fi
    if [ -n "$_bare_val" ]; then
        echo "$_bare_val"
        return 0
    fi
    return 1
}

while true; do
    # v122 BUG-1 fix: compute TIMESTAMP INSIDE the loop so each cycle
    # gets a unique filename. The previous code computed TIMESTAMP once
    # at script start, so every cycle overwrote the same file.
    TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting backup cycle (run id: $TIMESTAMP)..."

    # ─── PostgreSQL backup ──────────────────────────────────────────────
    PG_FILE="$BACKUP_DIR/postgres-${TIMESTAMP}.sql.gz"
    # v122 BUG-2 fix: read password from POSTGRES_PASSWORD_FILE (Docker
    # secret) first, fall back to POSTGRES_PASSWORD env var.
    PG_PASSWORD="$(_read_secret POSTGRES_PASSWORD_FILE POSTGRES_PASSWORD 2>/dev/null || true)"
    if [ -z "$PG_PASSWORD" ]; then
        echo "  ✗ Postgres backup FAILED: no password available (neither POSTGRES_PASSWORD_FILE nor POSTGRES_PASSWORD is set)" >&2
    else
        if PGPASSWORD="$PG_PASSWORD" pg_dump \
            -h "${POSTGRES_HOST}" \
            -U "${POSTGRES_USER}" \
            -d "${POSTGRES_DB}" \
            --no-owner --no-privileges \
            | gzip > "$PG_FILE"; then
            echo "  ✓ Postgres backup: $PG_FILE ($(du -h "$PG_FILE" | cut -f1))"
        else
            echo "  ✗ Postgres backup FAILED (pg_dump returned non-zero)" >&2
            rm -f "$PG_FILE"
        fi
    fi

    # ─── Neo4j backup (via APOC HTTP export — no cypher-shell needed) ───
    # v122 BUG-4 fix: the postgres:16-alpine image does NOT include
    # cypher-shell. The previous code's `command -v cypher-shell` check
    # always failed, so Neo4j backup was ALWAYS SKIPPED. The Neo4j KG is
    # the patient-safety-critical data — skipping its backup is a P0
    # finding. ROOT FIX: use Neo4j's HTTP transactional API
    # (POST /db/neo4j/tx/commit) to invoke the APOC export procedure
    # `apoc.export.cypher.all(null, {format: 'plain'})`. This requires
    # only `curl` (already in the image) and APOC (enabled in the
    # docker-compose neo4j service via NEO4J_PLUGINS: '["apoc"]').
    NEO4J_FILE="$BACKUP_DIR/neo4j-${TIMESTAMP}.cypher.gz"
    NEO4J_PASSWORD="$(_read_secret NEO4J_PASSWORD_FILE NEO4J_PASSWORD 2>/dev/null || true)"
    if [ -z "$NEO4J_PASSWORD" ]; then
        echo "  ✗ Neo4j backup FAILED: no password available (neither NEO4J_PASSWORD_FILE nor NEO4J_PASSWORD is set)" >&2
    elif ! command -v curl >/dev/null 2>&1; then
        echo "  ✗ Neo4j backup FAILED: curl not found in image" >&2
    else
        # Build the JSON payload for the Neo4j HTTP transactional API.
        # The APOC procedure `apoc.export.cypher.all` writes the full KG
        # as Cypher CREATE statements. Passing `null` as the first arg
        # streams the output back as a JSON string (instead of writing
        # to a file inside the Neo4j container).
        NEO4J_HOST="${NEO4J_HOST:-neo4j}"
        NEO4J_HTTP_PORT="${NEO4J_HTTP_PORT:-7474}"
        NEO4J_URL="http://${NEO4J_HOST}:${NEO4J_HTTP_PORT}/db/neo4j/tx/commit"
        PAYLOAD='{"statements":[{"statement":"CALL apoc.export.cypher.all(null, {format: \x27plain\x27}) YIELD cypherStatements RETURN cypherStatements"}]}'

        # Use a temp file for the response (the response can be large —
        # the full KG as Cypher statements). Parse with python (also in
        # the image as a postgres dependency) to extract the cypherStatements
        # field. gzip the result.
        TMP_RESP="$(mktemp)"
        HTTP_CODE="$(curl -sS -o "$TMP_RESP" -w "%{http_code}" \
            -u "neo4j:${NEO4J_PASSWORD}" \
            -H "Content-Type: application/json" \
            -X POST "$NEO4J_URL" \
            -d "$PAYLOAD" 2>/dev/null || echo "000")"
        if [ "$HTTP_CODE" = "200" ]; then
            # Extract cypherStatements from the JSON response and gzip it.
            # Using python here because jq is not in the alpine image.
            if python3 -c "
import json, sys, gzip
with open('$TMP_RESP', 'r') as f:
    resp = json.load(f)
results = resp.get('results', [])
if not results:
    print('ERROR: no results in Neo4j response', file=sys.stderr)
    sys.exit(1)
cols = results[0].get('columns', [])
if 'cypherStatements' not in cols:
    print('ERROR: cypherStatements column missing in Neo4j response', file=sys.stderr)
    sys.exit(1)
data = results[0].get('data', [])
if not data:
    print('ERROR: no data rows in Neo4j response', file=sys.stderr)
    sys.exit(1)
row = data[0].get('row', [])
cypher_text = row[0] if row else ''
if not cypher_text:
    print('ERROR: empty cypherStatements in Neo4j response', file=sys.stderr)
    sys.exit(1)
with gzip.open('$NEO4J_FILE', 'wt', encoding='utf-8') as gz:
    gz.write(cypher_text)
print(f'OK: wrote {len(cypher_text)} bytes to $NEO4J_FILE')
" 2>&1; then
                echo "  ✓ Neo4j backup: $NEO4J_FILE ($(du -h "$NEO4J_FILE" | cut -f1))"
            else
                echo "  ✗ Neo4j backup FAILED (could not parse HTTP response or write gzip)" >&2
                rm -f "$NEO4J_FILE"
            fi
        else
            echo "  ✗ Neo4j backup FAILED (HTTP $HTTP_CODE from $NEO4J_URL)" >&2
            rm -f "$NEO4J_FILE"
        fi
        rm -f "$TMP_RESP"
    fi

    # ─── MLflow artifact backup (v128 TM15 Task 15.6 ROOT FIX) ────────
    # The audit (IN-005) required backups for Postgres, Neo4j, AND MLflow.
    # The previous backup.sh only covered Postgres + Neo4j. MLflow's backend
    # metadata (experiments, runs, params, metrics) is stored in Postgres
    # and is already covered by pg_dump above. But MLflow's ARTIFACT store
    # (model checkpoints, calibration plots, feature importance plots) is
    # stored on the filesystem at /mlruns — NOT in Postgres. Without this
    # tar.gz backup, a disk failure would lose every trained model
    # checkpoint (6+ hours of GPU training each).
    MLFLOW_FILE="$BACKUP_DIR/mlflow-${TIMESTAMP}.tar.gz"
    MLFLOW_ARTIFACT_DIR="${MLFLOW_ARTIFACT_DIR:-/mlruns}"
    if [ ! -d "$MLFLOW_ARTIFACT_DIR" ]; then
        echo "  ⚠ MLflow backup SKIPPED: artifact dir $MLFLOW_ARTIFACT_DIR not mounted (set MLFLOW_ARTIFACT_DIR or mount mlflow_data)" >&2
    else
        # Use tar with --exclude for .lock files (MLflow creates transient
        # .lock files during runs; tarring them produces a corrupt archive
        # if the lock is held mid-write). --warning=no-file-changed suppresses
        # the warning tar emits when a file changes mid-read.
        if tar --warning=no-file-changed --warning=no-file-removed \
                --exclude='*.lock' --exclude='*.tmp' \
                -czf "$MLFLOW_FILE" \
                -C "$(dirname "$MLFLOW_ARTIFACT_DIR")" \
                "$(basename "$MLFLOW_ARTIFACT_DIR")" 2>/dev/null; then
            echo "  ✓ MLflow backup: $MLFLOW_FILE ($(du -h "$MLFLOW_FILE" | cut -f1))"
        else
            # tar exits 1 on minor warnings (file changed) even when the
            # archive is valid. Check the archive integrity before declaring failure.
            if gzip -t "$MLFLOW_FILE" 2>/dev/null; then
                echo "  ✓ MLflow backup: $MLFLOW_FILE ($(du -h "$MLFLOW_FILE" | cut -f1)) (with non-fatal tar warnings)"
            else
                echo "  ✗ MLflow backup FAILED (tar returned non-zero and archive is corrupt)" >&2
                rm -f "$MLFLOW_FILE"
            fi
        fi
    fi

    # ─── Retention: delete backups older than RETENTION_DAYS ────────────
    find "$BACKUP_DIR" -name "postgres-*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true
    find "$BACKUP_DIR" -name "neo4j-*.cypher.gz" -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true
    # v128 TM15 Task 15.6: also clean up old MLflow backups.
    find "$BACKUP_DIR" -name "mlflow-*.tar.gz" -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true
    echo "  ✓ Retention: kept last ${RETENTION_DAYS} days of backups"

    # ─── Summary: count successful backups so the operator can verify ───
    PG_COUNT="$(find "$BACKUP_DIR" -name "postgres-*.sql.gz" 2>/dev/null | wc -l | tr -d ' ')"
    NEO4J_COUNT="$(find "$BACKUP_DIR" -name "neo4j-*.cypher.gz" 2>/dev/null | wc -l | tr -d ' ')"
    MLFLOW_COUNT="$(find "$BACKUP_DIR" -name "mlflow-*.tar.gz" 2>/dev/null | wc -l | tr -d ' ')"
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup cycle $TIMESTAMP complete. Postgres: $PG_COUNT, Neo4j: $NEO4J_COUNT, MLflow: $MLFLOW_COUNT. Sleeping 24h..."
    sleep 86400
done
