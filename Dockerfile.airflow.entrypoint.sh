#!/usr/bin/env bash
# =============================================================================
# airflow-entrypoint.sh — Fernet key + webserver secret validation entrypoint.
# =============================================================================
# v118 ROOT FIX (Teammate 15 — Infrastructure, issues IN-049 + IN-077):
#   The previous entrypoint read `AIRFLOW__CORE__FERNET_KEY` directly, but
#   docker-compose.yml (v117) sources the key from a Docker Compose `secrets:`
#   block, which mounts the key at `/run/secrets/airflow_fernet_key` and sets
#   the env var `AIRFLOW__CORE__FERNET_KEY_FILE` (NOT `AIRFLOW__CORE__FERNET_KEY`).
#   The previous entrypoint exited with "AIRFLOW__CORE__FERNET_KEY is not set"
#   even when the secret file was correctly mounted — the Airflow container
#   never started. This was a CRITICAL bug masked by the v117 "ROOT FIX"
#   comment claiming `_FILE` was supported "via the entrypoint wrapper".
#
# ROOT FIX: this entrypoint now:
#   1. If `AIRFLOW__CORE__FERNET_KEY_FILE` is set, read the file and export
#      its contents as `AIRFLOW__CORE__FERNET_KEY` (so Airflow itself + the
#      validation below both see the actual key value).
#   2. Same for `AIRFLOW__WEBSERVER__SECRET_KEY_FILE` → `AIRFLOW__WEBSERVER__SECRET_KEY`.
#   3. Same for `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_FILE` → `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`.
#   4. Validate the Fernet key by attempting to construct a Fernet instance.
#      An invalid key (e.g. the old "dev_fernet_key_replace_in_production"
#      placeholder, which is 36 chars of ASCII — NOT a valid 32-byte URL-safe
#      base64 key) exits with a clear error before Airflow starts.
#   5. Hand off to the original command (e.g., "airflow scheduler").
#
# This entrypoint is the SECOND line of defense. The FIRST line is
# docker-compose's `${VAR:?ERROR}` interpolation, which fails BEFORE the
# container starts if `.env` is missing the required var. This entrypoint
# catches operators who set the var to an INVALID value in `.env`.
#
# Backward compatibility: operators who set `AIRFLOW__CORE__FERNET_KEY`
# directly (no `_FILE`) still work — the `_FILE` branch is skipped.
# =============================================================================
set -euo pipefail

# ─── 1. Translate *_FILE env vars to their bare equivalents ─────────────────
# Airflow 2.10 DOES support `_FILE` suffix natively, but only for a small
# set of config keys. To keep the validation below simple AND to support
# older Airflow versions (and any config key), we read the file ourselves
# and export the bare env var. This is the same pattern used by the
# official postgres, grafana, and mlflow Docker images.
_load_file_env() {
    local file_var="$1"
    local bare_var="$2"
    local file_path
    file_path="${!file_var:-}"
    if [ -n "$file_path" ]; then
        if [ ! -f "$file_path" ]; then
            echo "ERROR: $file_var points to '$file_path' but the file does not exist." >&2
            echo "       Check that the Docker Compose secrets: block is correctly configured." >&2
            exit 1
        fi
        if [ ! -r "$file_path" ]; then
            echo "ERROR: $file_var points to '$file_path' but the file is not readable." >&2
            echo "       Check file permissions (uid/gid of the airflow user)." >&2
            exit 1
        fi
        # Read file contents, strip trailing newline. Use printf (not echo) to
        # avoid interpreting backslash escapes in the key value.
        local value
        value="$(printf '%s' "$(cat "$file_path")")"
        # Strip a single trailing newline if present (common when operators
        # use `echo "key" > file` instead of `echo -n "key" > file`).
        value="${value%$'\n'}"
        export "$bare_var=$value"
    fi
}

_load_file_env AIRFLOW__CORE__FERNET_KEY_FILE             AIRFLOW__CORE__FERNET_KEY
_load_file_env AIRFLOW__WEBSERVER__SECRET_KEY_FILE       AIRFLOW__WEBSERVER__SECRET_KEY
_load_file_env AIRFLOW__DATABASE__SQL_ALCHEMY_CONN_FILE  AIRFLOW__DATABASE__SQL_ALCHEMY_CONN

# ─── 2. Validate the Fernet key BEFORE starting Airflow ─────────────────────
# A valid Fernet key is a 32-byte URL-safe base64-encoded string (44 chars
# total including the trailing '='). The old placeholder
# "dev_fernet_key_replace_in_production" is 36 chars of ASCII — NOT a valid
# Fernet key. Airflow would crash at startup with
# `cryptography.fernet.InvalidToken` (visible failure) OR silently fall back
# to plaintext Connection storage (invisible failure where every Airflow
# Connection password is stored unencrypted — a FDA 21 CFR Part 11 finding).
if [ -z "${AIRFLOW__CORE__FERNET_KEY:-}" ]; then
    echo "ERROR: AIRFLOW__CORE__FERNET_KEY is not set." >&2
    echo "       Generate one with:" >&2
    echo '         python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"' >&2
    echo "       Then either:" >&2
    echo "         (a) set AIRFLOW_FERNET_KEY=<key> in .env, OR" >&2
    echo "         (b) write the key to secrets/airflow_fernet_key.txt (no trailing newline)." >&2
    exit 1
fi

if ! python3 -c "
import os, sys
from cryptography.fernet import Fernet
key = os.environ.get('AIRFLOW__CORE__FERNET_KEY', '')
try:
    Fernet(key.encode() if isinstance(key, str) else key)
except Exception as exc:
    print(f'ERROR: AIRFLOW__CORE__FERNET_KEY is not a valid Fernet key: {exc}', file=sys.stderr)
    print('       A valid Fernet key is a 32-byte URL-safe base64-encoded string (44 chars incl trailing =).', file=sys.stderr)
    print('       Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"', file=sys.stderr)
    sys.exit(1)
"; then
    exit 1
fi

# ─── 3. Validate the webserver secret key is set (IN-010) ───────────────────
# Airflow's webserver uses this to sign session cookies. If unset, Airflow
# generates a random one on each restart, invalidating all active sessions
# (every operator gets logged out on every restart — confusing).
if [ -z "${AIRFLOW__WEBSERVER__SECRET_KEY:-}" ]; then
    echo "ERROR: AIRFLOW__WEBSERVER__SECRET_KEY is not set." >&2
    echo "       Generate one with:" >&2
    echo '         python -c "import secrets; print(secrets.token_urlsafe(32))"' >&2
    echo "       Then either:" >&2
    echo "         (a) set AIRFLOW_WEBSERVER_SECRET_KEY=<key> in .env, OR" >&2
    echo "         (b) write the key to secrets/airflow_webserver_secret_key.txt (no trailing newline)." >&2
    exit 1
fi

# ─── 4. Hand off to the original command (e.g., "airflow scheduler") ───────
exec "$@"
