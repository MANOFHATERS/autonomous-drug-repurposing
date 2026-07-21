"""Conftest for backend integration tests (TEAMMATE-11 acceptance tests).

Sets the JWT_SECRET env var to a deterministic test value so
``create_test_jwt`` can mint tokens that pass ``verify_jwt``. The secret
is >=32 chars (the production requirement enforced by verify_jwt).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure the repo root is on sys.path so `from backend.api.main import app`
# works when pytest is invoked from any cwd.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Deterministic test secret (>=32 chars). NEVER use this in production.
os.environ.setdefault(
    "JWT_SECRET",
    "test-secret-for-integration-tests-only-not-for-production-use-32chars",
)
# Disable the rate limiter for integration tests so the test client's
# rapid-fire requests don't get 429'd.
os.environ.setdefault("DRUGOS_DISABLE_RATE_LIMIT", "1")
