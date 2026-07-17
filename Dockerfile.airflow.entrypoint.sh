#!/usr/bin/env bash
# =============================================================================
# airflow-entrypoint.sh — Fernet key validation entrypoint (IN-077 + v116).
# =============================================================================
# Validates AIRFLOW__CORE__FERNET_KEY BEFORE starting the scheduler.
#
# A valid Fernet key is a 32-byte URL-safe base64-encoded string (44 chars
# total including the trailing '='). The old placeholder
# "dev_fernet_key_replace_in_production" is 36 chars of ASCII — NOT a valid
# Fernet key. Airflow would crash at startup with
# `cryptography.fernet.InvalidToken` (visible failure) OR silently fall back
# to plaintext Connection storage (invisible failure where every Airflow
# Connection password is stored unencrypted — a FDA 21 CFR Part 11 finding).
#
# v116: docker-compose.yml now sources the key from
#   ${AIRFLOW_FERNET_KEY:?ERROR: ...}
# so compose fails BEFORE the container starts if the var is unset. This
# entrypoint is the SECOND line of defense — it catches operators who set
# the var to an INVALID value in .env.
# =============================================================================
set -euo pipefail

if [ -z "${AIRFLOW__CORE__FERNET_KEY:-}" ]; then
    echo "ERROR: AIRFLOW__CORE__FERNET_KEY is not set. Generate one with:" >&2
    echo '  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"' >&2
    exit 1
fi

# Validate the Fernet key by attempting to construct a Fernet instance.
if ! python3 -c "
import os, sys
from cryptography.fernet import Fernet
key = os.environ.get('AIRFLOW__CORE__FERNET_KEY', '')
try:
    Fernet(key.encode() if isinstance(key, str) else key)
except Exception as exc:
    print(f'ERROR: AIRFLOW__CORE__FERNET_KEY is not a valid Fernet key: {exc}', file=sys.stderr)
    print('Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"', file=sys.stderr)
    sys.exit(1)
"; then
    exit 1
fi

# Hand off to the original command (e.g., "airflow scheduler").
exec "$@"
