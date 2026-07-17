#!/bin/sh
# =============================================================================
# backup.sh — IN-005 ROOT FIX: daily backup sidecar.
# =============================================================================
# Runs inside the pg-backup container (postgres:16-alpine image). Performs:
#   1. pg_dump of the DrugOS Postgres DB → /backups/postgres-YYYYMMDD-HHMMSS.sql.gz
#   2. neo4j-admin dump of the Neo4j KG → /backups/neo4j-YYYYMMDD-HHMMSS.dump
#      (via cypher-shell EXPORT — the neo4j-admin binary is not in this image,
#       so we use cypher-shell to run `CALL apoc.export.cypher.all` if APOC
#       is available, otherwise skip with a warning).
#   3. Retains only the last 7 daily backups (deletes older files).
#
# The script sleeps 24h between backups (cron-in-a-container pattern) so the
# pg-backup service stays alive and restarts the loop on container restart.
# =============================================================================
set -euo pipefail

BACKUP_DIR="/backups"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

mkdir -p "$BACKUP_DIR"

while true; do
    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Starting backup cycle..."

    # ─── PostgreSQL backup ──────────────────────────────────────────────
    PG_FILE="$BACKUP_DIR/postgres-${TIMESTAMP}.sql.gz"
    if PGPASSWORD="${POSTGRES_PASSWORD}" pg_dump \
        -h "${POSTGRES_HOST}" \
        -U "${POSTGRES_USER}" \
        -d "${POSTGRES_DB}" \
        --no-owner --no-privileges \
        | gzip > "$PG_FILE"; then
        echo "  ✓ Postgres backup: $PG_FILE ($(du -h "$PG_FILE" | cut -f1))"
    else
        echo "  ✗ Postgres backup FAILED" >&2
        rm -f "$PG_FILE"
    fi

    # ─── Neo4j backup (via cypher-shell + APOC export) ──────────────────
    NEO4J_FILE="$BACKUP_DIR/neo4j-${TIMESTAMP}.cypher.gz"
    if command -v cypher-shell >/dev/null 2>&1; then
        if echo "CALL apoc.export.cypher.all(null, {format: 'plain'});" \
            | cypher-shell -a "bolt://${NEO4J_HOST}:7687" -u neo4j -p "${NEO4J_PASSWORD}" --format plain \
            | gzip > "$NEO4J_FILE"; then
            echo "  ✓ Neo4j backup: $NEO4J_FILE ($(du -h "$NEO4J_FILE" | cut -f1))"
        else
            echo "  ✗ Neo4j backup FAILED (cypher-shell returned non-zero)" >&2
            rm -f "$NEO4J_FILE"
        fi
    else
        echo "  ⚠ Neo4j backup SKIPPED (cypher-shell not in this image — install neo4j client or use a neo4j image for backups)"
    fi

    # ─── Retention: delete backups older than RETENTION_DAYS ────────────
    find "$BACKUP_DIR" -name "postgres-*.sql.gz" -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true
    find "$BACKUP_DIR" -name "neo4j-*.cypher.gz" -mtime +"${RETENTION_DAYS}" -delete 2>/dev/null || true
    echo "  ✓ Retention: kept last ${RETENTION_DAYS} days of backups"

    echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] Backup cycle complete. Sleeping 24h..."
    sleep 86400
done
