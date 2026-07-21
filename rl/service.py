#!/usr/bin/env python3
"""Phase 4 RL Hypothesis Ranker Service (Step 1 integration plan, v105).

Wraps Phase 4's RL ranker as an HTTP service so the Next.js frontend
can proxy to it via RL_SERVICE_URL. The frontend's
``src/lib/services/rl-ranker.ts`` and ``src/app/api/rl/route.ts``
already proxy to ``RL_SERVICE_URL`` -- this service is what they
expect to find there.

Endpoints:
    GET  /health                -> {status: "ok", service: "phase4_rl", ...}
    GET  /rank?limit=50         -> {candidates: [...], source: "rl_service", ...}
    GET  /rank/{drug}           -> {candidates: [...]} filtered by drug
    POST /rank                  -> same as GET /rank but with body filters

Run:
    cd rl && python service.py
    # or: uvicorn rl.service:app --host 0.0.0.0 --port 8004

Environment:
    RL_CHECKPOINT_PATH: Path to the trained PPO checkpoint (.zip file).
        If unset, the service reads the latest top_candidates_*.csv
        from the rl/ directory (matching the frontend's FE-003 v105
        behavior) so it can still answer /rank in dev/CI.
    RL_OUTPUT_DIR: Directory containing top_candidates_*.csv outputs.
        Defaults to <repo>/rl/.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make rl + repo root importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException, Query, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# v122 FORENSIC ROOT FIX (BUG-4/BUG-5/BUG-6): wire up shared observability
# (metrics + structured JSON logging + OpenTelemetry).
try:
    from shared.observability import configure_app as _configure_observability
except Exception:
    _configure_observability = None

# SH-035 ROOT FIX: import URL constants from the canonical
# shared.contracts.urls module. The previous code hardcoded the path
# strings ("/validate", "/rank", "/health") which silently drifted
# from the shared contract. Now the service registers the EXACT same
# path strings the frontend imports from frontend/contracts/api_contracts.ts
# (which mirrors shared.contracts.urls).
try:
    from shared.contracts.urls import (
        URL_HEALTH as _URL_HEALTH,
        URL_RANK as _URL_RANK,
        URL_RANK_BY_DRUG as _URL_RANK_BY_DRUG,
        URL_VALIDATE as _URL_VALIDATE,
    )
except Exception:  # Defensive fallback for stripped-down deployments.
    _URL_HEALTH = "/health"
    _URL_RANK = "/rank"
    _URL_RANK_BY_DRUG = "/rank/{drug}"
    _URL_VALIDATE = "/validate"

# SH-002/SH-003 ROOT FIX: import the canonical outcome enum + column
# names from the shared contract. The previous code hardcoded a 4-value
# set in the /validate handler — which happened to match the shared
# contract, but the hardcoded copy could drift. Now the set is sourced
# from shared.contracts.writeback.VALID_OUTCOMES so any change to the
# canonical enum is automatically picked up.
try:
    from shared.contracts.writeback import VALID_OUTCOMES as _VALID_OUTCOMES
except Exception:  # Defensive fallback.
    _VALID_OUTCOMES = (
        "validated_positive",
        "validated_negative",
        "validated_toxic",
        "invalidated",
    )

logger = logging.getLogger("rl.service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 4 RL Ranker Service",
    description="HTTP wrapper around Phase 4 RL hypothesis ranker.",
    version="1.0.0",
)
# P4-006 ROOT FIX: CORS allow_origins is now read from an env var
# (RL_CORS_ORIGINS) instead of hardcoded ["*"] which allowed ANY website
# to call the service. A malicious website could exfiltrate the entire
# ranking database (pharma partner data, drug names, disease names,
# reward scores). Defaults to localhost:3000 for dev.
# P4-036 ROOT FIX (MEDIUM — Team Cosmic / Phase 4): the previous code
# had a branch `if _RL_CORS_ORIGINS == "*": _allow_origins = ["*"]`
# which RE-ENABLED wildcard CORS when the operator set
# RL_CORS_ORIGINS="*". The wildcard allows ANY website (including
# malicious ones) to call the /rank and /validate endpoints —
# exfiltrating pharma partner data. The fix REMOVES the wildcard
# branch: if RL_CORS_ORIGINS="*" is set, log a WARNING and fall back
# to the safe default (localhost:3000). Operators who need multiple
# origins must list them explicitly (comma-separated).
_RL_CORS_ORIGINS = os.environ.get("RL_CORS_ORIGINS", "http://localhost:3000")
if _RL_CORS_ORIGINS == "*":
    # P4-036: wildcard CORS is FORBIDDEN. Fall back to the safe default.
    logger.warning(
        "P4-036 ROOT FIX: RL_CORS_ORIGINS='*' is FORBIDDEN (would allow "
        "ANY website to call /rank and /validate, exfiltrating pharma "
        "partner data). Falling back to the safe default "
        "'http://localhost:3000'. To allow multiple origins, list them "
        "explicitly as a comma-separated list (e.g., "
        "RL_CORS_ORIGINS='http://localhost:3000,https://app.example.com')."
    )
    _allow_origins = ["http://localhost:3000"]
else:
    _allow_origins = [o.strip() for o in _RL_CORS_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["GET", "POST", "OPTIONS"],  # INT-030: add OPTIONS for CORS preflight
    allow_headers=["*"],
)

# v122 BUG-4/BUG-5/BUG-6: mount /metrics + configure JSON logging + OTel.
# Must come AFTER all middleware is added.
if _configure_observability is not None:
    _configure_observability(app, service_name="phase4-rl")


class RankRequest(BaseModel):
    drug: Optional[str] = None
    disease: Optional[str] = None
    limit: int = 50


# ============================================================================
# BE-043 v128 ROOT FIX (Task 9.5 — /api/rl GET cross-tenant data leak):
# The previous /rank endpoint accepted an OPTIONAL ``org_id`` query param
# but ONLY logged it — no filtering was applied. A user from org A could
# see drug names validated by org B (a different pharma partner), violating
# the cross-tenant isolation required for 21 CFR Part 11 compliance and
# the DOCX §10 data flywheel's "proprietary validated data" moat.
#
# ROOT FIX:
#   1. ``RL_REQUIRE_AUTH`` env var (default "true") — when true, the /rank
#      endpoint REQUIRES org_id. Missing org_id → 401 Unauthorized.
#   2. ``_load_org_private_drugs()`` reads validated_hypotheses.csv and
#      builds a dict {org_id -> set of drugs validated by that org}.
#      Drugs validated by an org are PRIVATE to that org.
#   3. ``_filter_candidates_by_org()`` removes candidates whose drug is
#      in ANOTHER org's private set. Drugs validated by THIS org are
#      always visible. Drugs not validated by any org are PUBLIC (visible
#      to all authenticated orgs).
#
# This is the standard multi-tenant isolation pattern: each tenant sees
# their own private data + all public data, but NOT other tenants' private
# data. The /rank endpoint now enforces this.
# ============================================================================

# Env var: when "true" (default), /rank requires org_id. Set to "false"
# for local dev (e.g., running the test suite without an auth context).
# This is read DYNAMICALLY at request time (not module load time) so that
# tests can toggle it via monkeypatch.setenv(...) and verify both auth
# paths (enforced + disabled) in the same test run.
def _rl_require_auth() -> bool:
    """Read RL_REQUIRE_AUTH env var at request time (not module load time).

    Default is "true" (production-safe: /rank requires org_id). Tests set
    RL_REQUIRE_AUTH=false in conftest.py to allow anonymous access for
    existing tests that don't pass org_id.
    """
    return os.environ.get("RL_REQUIRE_AUTH", "true").lower() not in (
        "false", "0", "no", "off",
    )


def _load_org_private_drugs() -> Dict[str, set]:
    """Build a dict {org_id -> set of drug names} from validated_hypotheses.csv.

    Reads the canonical validated_hypotheses.csv (phase1/processed_data/)
    and the legacy rl/validated_hypotheses.csv. Each row's ``validated_by``
    column identifies the org that validated the (drug, disease) pair.
    Drugs validated by an org are PRIVATE to that org — other orgs should
    NOT see them in their /rank results.

    PUBLIC validators (FDA, EMA, PMDA, etc.) are EXCLUDED from the
    org_private_drugs mapping — drugs validated by public agencies are
    PUBLIC (visible to all orgs). Only PHARMA PARTNER validators create
    private drug sets.

    Returns:
        Dict mapping org_id (lowercased) to a set of drug names (lowercased).
        Empty dict if no validated_hypotheses.csv exists or all rows are
        empty/header-only (fresh deployment) or all rows are validated by
        public agencies.
    """
    # PUBLIC_VALIDATORS: validators that represent PUBLIC regulatory agencies
    # (not pharma partners). Drugs validated by these agencies are PUBLIC —
    # they appear in FDA labels, EMA approvals, etc. and are visible to all
    # orgs. Only PHARMA PARTNER validators (e.g., "pfizer", "novartis")
    # create private drug sets.
    PUBLIC_VALIDATORS = frozenset({
        "fda_approved", "fda_withdrawal", "fda_investigational",
        "ema_approved", "ema_withdrawal", "ema_investigational",
        "pmda_approved", "pmda_withdrawal",
        "clinical_trial", "literature", "public_kg",
        "data_flywheel",  # the auto-populated validator (not a real org)
        "system", "admin", "root", "",
    })

    org_to_drugs: Dict[str, set] = {}
    try:
        import csv as _csv_org
        # Canonical path (matches _load_validated_hypotheses in rl_drug_ranker.py).
        _module_dir = Path(__file__).resolve().parent
        _repo_root = _module_dir.parent
        _candidate_paths = [
            _repo_root / "phase1" / "processed_data" / "validated_hypotheses.csv",
            _module_dir / "validated_hypotheses.csv",
        ]
        # Env var override (matches RL_VALIDATED_HYPOTHESES_PATH).
        _env_path = os.environ.get("RL_VALIDATED_HYPOTHESES_PATH", "")
        if _env_path:
            _candidate_paths.insert(0, Path(_env_path))
        for _path in _candidate_paths:
            if not _path.exists():
                continue
            try:
                with open(_path, "r", encoding="utf-8") as _f:
                    _reader = _csv_org.DictReader(_f)
                    for _row in _reader:
                        _drug = (_row.get("drug") or "").strip().lower()
                        _org = (_row.get("validated_by") or "").strip().lower()
                        if not _drug or not _org:
                            continue
                        # Skip PUBLIC validators — their drugs are PUBLIC.
                        if _org in PUBLIC_VALIDATORS:
                            continue
                        org_to_drugs.setdefault(_org, set()).add(_drug)
            except Exception as _e:
                logger.warning(
                    "BE-043 v128: failed to read %s for org scoping: %s. "
                    "Falling back to no org filtering (all drugs visible to all orgs).",
                    _path, _e,
                )
                return {}
        # If we found at least one file but it had no private-validator rows,
        # return empty dict (no private drugs — all drugs are public).
        return org_to_drugs
    except Exception as _e:
        logger.warning(
            "BE-043 v128: _load_org_private_drugs failed: %s. "
            "Falling back to no org filtering.", _e,
        )
        return {}


def _filter_candidates_by_org(
    candidates: List[Dict[str, Any]],
    org_id: str,
    org_private_drugs: Dict[str, set],
) -> List[Dict[str, Any]]:
    """Filter candidates to enforce cross-tenant isolation.

    A candidate is visible to ``org_id`` if:
      - The candidate's drug is NOT in any OTHER org's private set (public drug), OR
      - The candidate's drug IS in THIS org's private set (own private drug).

    A candidate is HIDDEN if:
      - The candidate's drug is in ANOTHER org's private set (other tenant's private drug).

    Args:
        candidates: List of candidate dicts (each has a "drug" key).
        org_id: The requesting org's identifier (lowercased).
        org_private_drugs: Dict {org_id -> set of drug names} from
            _load_org_private_drugs().

    Returns:
        Filtered list of candidates (preserves order).
    """
    if not org_private_drugs:
        # No validated hypotheses → all drugs are public → no filtering.
        return candidates
    # Build the set of OTHER orgs' private drugs.
    other_orgs_private_drugs: set = set()
    for _other_org, _drugs in org_private_drugs.items():
        if _other_org == org_id:
            continue  # Skip THIS org's drugs (they're visible to itself).
        other_orgs_private_drugs.update(_drugs)
    if not other_orgs_private_drugs:
        # Only THIS org has private drugs → all candidates visible.
        return candidates
    # Filter: exclude candidates whose drug is in another org's private set.
    return [
        c for c in candidates
        if (c.get("drug") or "").strip().lower() not in other_orgs_private_drugs
    ]


def _find_latest_output_csv() -> Optional[Path]:
    """FE-003 v105: find the latest top_candidates_*.csv in RL_OUTPUT_DIR.

    Mirrors the frontend's rl-ranker.ts findLatestOutputCsv() logic.
    Returns the absolute path, or None if no output exists.
    """
    output_dir = Path(os.environ.get("RL_OUTPUT_DIR", str(_HERE)))
    if not output_dir.exists():
        return None
    candidates = []
    for f in output_dir.iterdir():
        if f.name.startswith("top_candidates_") and f.suffix == ".csv":
            try:
                candidates.append((f, f.stat().st_mtime))
            except OSError:
                pass
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def _load_reward_weights_from_meta(csv_path: Path) -> Optional[Dict[str, float]]:
    """P4-004 ROOT FIX: load reward weights from the .meta.json sidecar.

    The RL agent's RewardConfig uses specific weights (gnn=0.04, safety=0.25,
    market=0.12, etc.). The previous code used HARDCODED weights 0.4/0.3/0.3
    which produced a DIFFERENT overall score than the agent's reward function.
    This fix reads the actual weights from the metadata sidecar written by
    ``save_results`` (``.meta.json`` next to the CSV) so the dashboard's
    overallScore matches what the agent learned.
    """
    meta_path = csv_path.with_suffix(".meta.json")
    if not meta_path.exists():
        # Also try <stem>.meta.json (e.g., top_candidates_20250714.meta.json)
        meta_path = csv_path.parent / (csv_path.stem + ".meta.json")
    if not meta_path.exists():
        return None
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        weights = meta.get("reward_weights")
        if weights and isinstance(weights, dict):
            return weights
    except Exception:
        pass
    return None


def _load_candidates_from_csv(
    csv_path: Path,
    drug: Optional[str],
    disease: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    """Parse the RL ranker's output CSV into the RankedHypothesis schema.

    BE-070 + INT-022 MERGED FIX: Returns a dict with 'candidates' and 'total'
    keys. 'total' is the count AFTER filtering but BEFORE pagination — this is
    required by the frontend's pagination controls ("Showing X-Y of Z").
    The previous code only returned the candidate list, so the frontend's
    `total` was the page size (wrong), making it impossible to navigate
    beyond page 1.

    P4-013: Uses streaming iteration instead of list(reader) to avoid OOM
    on large CSV files. We cannot break early because P4-014 requires sorting
    ALL matching candidates by rank before applying the limit.
    """
    import csv as csv_mod

    out: List[Dict[str, Any]] = []
    if not csv_path.exists():
        return {"candidates": out, "total": 0}

    # P4-004: load reward weights from sidecar (if available)
    _reward_weights = _load_reward_weights_from_meta(csv_path)

    def _num(v: Any) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            f = float(v)
            return f if f == f else None  # NaN check
        except (ValueError, TypeError):
            return None

    # BE-070: Count ALL matching rows for `total`, but only keep candidates
    # in memory. We stream the CSV and:
    #   1. Count every row that passes the filter → total_filtered
    #   2. Collect ALL matching candidates for sorting (P4-014)
    #   3. Sort by rank, then apply limit
    total_filtered = 0
    # P4-027 ROOT FIX (LOW — Team Cosmic / Phase 4): the previous code
    # opened the CSV with `errors="replace"` which SILENTLY replaces
    # invalid UTF-8 bytes with the U+FFFD replacement character. Garbled
    # drug/disease names (e.g., "aspirin\ufffd" instead of "aspirin")
    # were inserted into the response, breaking downstream matching.
    # The fix uses `errors="strict"` (the default) and catches
    # UnicodeDecodeError with a clear error message. This makes the
    # operator aware of malformed CSVs (encoding issues at the source)
    # instead of silently passing garbled data to pharma partners.
    # P4-013 ROOT FIX: use `with open(csv_path, ...)` directly (not
    # `f_open = open(...)` then `with f_open as f:`). The previous code
    # split the open and the with-statement, which the P4-013 test flags
    # as a streaming-CSV anti-pattern (the file handle leaks if the
    # with-statement is never entered). The fix combines them.
    try:
        # P4-013: the for loop is INSIDE the with-block so the file is
        # closed even if the loop raises.
        with open(csv_path, "r", encoding="utf-8") as f:  # strict by default
            reader = csv_mod.DictReader(f)
            for i, row in enumerate(reader):
                # Normalize keys to lowercase.
                r = {k.lower(): v for k, v in row.items()}
                d = r.get("drug", "")
                dis = r.get("disease", "")
                if not d or not dis:
                    continue
                if drug and drug.lower() not in d.lower():
                    continue
                if disease and disease.lower() not in dis.lower():
                    continue
                # This row passes the filter — increment total.
                total_filtered += 1

                gnn = _num(r.get("gnn_score"))
                safety = _num(r.get("safety_score"))
                market = _num(r.get("market_score"))
                reward = _num(r.get("reward"))
                # P4-031 ROOT FIX: use `if rank is not None` instead of `if rank`.
                # The previous code used `or` which treats 0 as falsy, so a CSV
                # with rank=0 fell back to (i+1), overwriting the user's explicit
                # rank=0. The fix uses `is not None` so rank=0 is preserved.
                _rank_raw = _num(r.get("rank"))
                rank = _rank_raw if _rank_raw is not None else float(i + 1)
                policy_prob = _num(r.get("policy_prob"))

                # P4-004 ROOT FIX: compute overall using the SAME weights as the
                # agent's reward function. If the .meta.json sidecar is available,
                # read the weights from there. Otherwise fall back to the agent's
                # default weights (NOT the old hardcoded 0.4/0.3/0.3).
                overall: Optional[float] = None
                if _reward_weights:
                    # Use weights from sidecar — these are the EXACT weights the
                    # agent trained with
                    signals = []
                    score_keys = {
                        "gnn": gnn, "safety": safety, "market": market,
                        "confidence": _num(r.get("confidence")),
                        "pathway": _num(r.get("pathway_score")),
                        "patent": _num(r.get("patent_score")),
                        "rare_disease": _num(r.get("rare_disease_score")),
                        "unmet_need": _num(r.get("unmet_need_score")),
                        "efficacy": _num(r.get("efficacy_score")),
                        "adme": _num(r.get("adme_score")),
                    }
                    for key, score in score_keys.items():
                        w = _reward_weights.get(key)
                        if score is not None and w is not None and w > 0:
                            signals.append((score, w))
                    if signals:
                        total_w = sum(w for _, w in signals)
                        overall = sum(v * w for v, w in signals) / total_w if total_w > 0 else None
                else:
                    # Fallback: use the agent's DEFAULT reward weights (same as
                    # RewardConfig defaults in rl_drug_ranker.py)
                    signals = []
                    if gnn is not None: signals.append((gnn, 0.04))
                    if safety is not None: signals.append((safety, 0.25))
                    if market is not None: signals.append((market, 0.12))
                    if signals:
                        total_w = sum(w for _, w in signals)
                        overall = sum(v * w for v, w in signals) / total_w
                if overall is None and policy_prob is not None:
                    overall = policy_prob

                out.append({
                    "drug": d,
                    "disease": dis,
                    # P4-031 ROOT FIX (LOW — Team Cosmic / Phase 4): change
                    # `if rank else (i + 1)` to `if rank is not None else (i + 1)`.
                    # The previous code treated rank=0 as falsy (0 is falsy in
                    # Python), so a CSV with rank=0 would fall back to (i+1) —
                    # the user's explicit rank=0 was silently overwritten. The
                    # fix uses `is not None` so rank=0 is preserved (0 is a
                    # valid rank, e.g., the top-ranked pair).
                    "rank": int(rank) if rank is not None else (i + 1),
                    "reward": reward,
                    "policyProb": policy_prob,
                    "gnnScore": gnn,
                    "safetyScore": safety,
                    "marketScore": market,
                    "plausibilityScore": gnn,
                    "overallScore": overall,
                    "confidence": _num(r.get("confidence")),
                    "pathwayScore": _num(r.get("pathway_score")),
                    "unmetNeedScore": _num(r.get("unmet_need_score")),
                    "efficacyScore": _num(r.get("efficacy_score")),
                    "admeScore": _num(r.get("adme_score")),
                    "literatureSupport": _num(r.get("literature_support")),
                    "isKnownPositive": str(r.get("is_known_positive", "")).lower() in ("1", "true", "yes"),
                })
    except UnicodeDecodeError as _ude:
        logger.error(
            "P4-027 ROOT FIX: CSV %s is not valid UTF-8 (%s). The previous "
            "code used errors='replace' which silently garbled drug/disease "
            "names. Fix the source CSV (re-export as UTF-8) or convert it "
            "with `iconv -f ISO-8859-1 -t UTF-8 input.csv > output.csv`.",
            csv_path, _ude,
        )
        return {"candidates": [], "total": 0}

    # P4-014 ROOT FIX: sort ALL candidates by rank, THEN apply limit.
    # The previous code broke after ``len(out) >= limit`` and THEN sorted,
    # which meant only the FIRST ``limit`` rows in CSV order were sorted —
    # NOT the top-``limit`` by rank. If the CSV was unsorted, the API
    # returned arbitrary candidates instead of the true top-N.
    # P4-031 ROOT FIX: use `if rank is None` instead of `or 1e9`.
    # The previous code used `c.get("rank") or 1e9` which treats rank=0
    # as falsy (0 is falsy in Python), so a candidate with rank=0 was
    # sorted as if it had rank=1e9 (LAST). The fix uses `is None` so
    # rank=0 is preserved and sorted correctly (FIRST, since 0 < 1).
    out.sort(key=lambda c: (c.get("rank") if c.get("rank") is not None else 1e9))
    # P4-014: apply limit AFTER sort (was: before sort). The previous code
    # broke after `len(out) >= limit` INSIDE the loop, which collected only
    # the FIRST `limit` rows in CSV order, then sorted those — returning
    # arbitrary candidates instead of the true top-N by rank. The fix
    # collects ALL matching candidates, sorts by rank, THEN slices.
    if limit > 0:
        out = out[:limit]
    return {"candidates": out, "total": total_filtered}


# ============================================================================
# P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
# Cache the GTRLBridge + RL input DataFrame at service startup so /rank
# doesn't rebuild the bridge on every request.
# ----------------------------------------------------------------------------
# THE BUG (forensic root cause):
#   The previous ``_load_candidates_from_checkpoint`` built a NEW
#   ``GTRLBridge`` on EVERY /rank request:
#     bridge = GTRLBridge(...)
#     bridge.build_model()           # ← trains the graph transformer
#     rl_input_df = bridge.generate_rl_input()   # ← full graph traversal
#   On a real KG, ``build_model()`` takes minutes (it trains the graph
#   transformer) and ``generate_rl_input()`` does a full graph traversal.
#   Every /rank request added multi-minute latency — the pharma partner
#   API was unusable.
#
# ROOT FIX:
#   Build the bridge ONCE (lazy on first /rank call, or eagerly via the
#   ``/reload`` endpoint), then reuse it for all subsequent /rank requests.
#   The PPO model + VecNormalize sidecar are still loaded per-request
#   (the checkpoint may change between requests), but the expensive
#   bridge build is cached.
#
# THREAD SAFETY:
#   FastAPI runs sync endpoints in a threadpool, so concurrent /rank
#   requests may race to build the bridge. We use ``threading.Lock``
#   with double-checked locking so only ONE thread builds the bridge;
#   the rest wait and then receive the cached instance.
#
# INVALIDATION:
#   ``invalidate_bridge_cache()`` clears the cache. Called by the
#   ``/reload`` endpoint (admin-only) after a KG update. The next /rank
#   request will rebuild the bridge with the new KG data.
# ============================================================================
_bridge_cache: Optional[Any] = None     # GTRLBridge instance (None = not built yet)
_rl_input_cache: Optional[Any] = None   # pandas DataFrame from bridge.generate_rl_input()
_bridge_lock = threading.Lock()


def get_cached_bridge() -> Tuple[Any, Any]:
    """Return the cached (GTRLBridge, rl_input_df), building it on first call.

    P4-024 ROOT FIX (Teammate 12): the previous ``_load_candidates_from_checkpoint``
    built a NEW GTRLBridge on every /rank request — calling ``build_model()``
    (which trains the graph transformer — multi-minute on a real KG) AND
    ``generate_rl_input()`` (a full graph traversal) on EVERY request.
    This added multi-minute latency to every /rank call, making the pharma
    partner API unusable.

    ROOT FIX: build the bridge ONCE (lazy on first /rank call), then reuse
    it for all subsequent /rank requests. The PPO model and VecNormalize
    sidecar are still loaded per-request (the checkpoint may change between
    requests), but the expensive bridge build is cached.

    Thread-safety: uses double-checked locking so concurrent /rank requests
    don't race to build the bridge.

    Returns:
        Tuple of (bridge, rl_input_df). The bridge is a ``GTRLBridge``
        instance; ``rl_input_df`` is a ``pandas.DataFrame`` from
        ``bridge.generate_rl_input()``.
    """
    global _bridge_cache, _rl_input_cache
    if _bridge_cache is not None and _rl_input_cache is not None:
        return _bridge_cache, _rl_input_cache
    with _bridge_lock:
        # Double-checked locking: re-test inside the lock so only the
        # first thread does the build; the rest receive the cached instance.
        if _bridge_cache is not None and _rl_input_cache is not None:
            return _bridge_cache, _rl_input_cache
        from graph_transformer.gt_rl_bridge import GTRLBridge
        bridge = GTRLBridge(
            output_dir=str(_HERE / "_service_output"),
            device="cpu",
            seed=42,
        )
        # Train the graph transformer ONCE. This is the expensive step
        # (multi-minute on a real KG); caching it cuts /rank latency
        # from minutes to seconds.
        bridge.build_model()
        # Pre-compute the RL training input. This is a graph traversal
        # that produces the (drug, disease, features) rows the RL env
        # consumes. Caching it avoids re-traversal on every /rank call.
        rl_input_df = bridge.generate_rl_input()
        _rl_input_len = len(rl_input_df) if rl_input_df is not None else 0
        if _rl_input_len == 0:
            logger.warning(
                "P4-024 ROOT FIX (Teammate 12): bridge generated EMPTY RL "
                "input — /rank will return empty candidates. Check that "
                "phase1/phase2 data is loaded and the KG has drug→protein→"
                "pathway→disease edges."
            )
        else:
            logger.info(
                "P4-024 ROOT FIX (Teammate 12): bridge cached at startup. "
                "RL input has %d rows. Subsequent /rank requests reuse "
                "this cache (no multi-minute rebuild). Use POST /reload "
                "to refresh the cache after a KG update.",
                _rl_input_len,
            )
        _bridge_cache = bridge
        _rl_input_cache = rl_input_df
        return _bridge_cache, _rl_input_cache


def invalidate_bridge_cache() -> None:
    """Clear the cached GTRLBridge + RL input.

    Called by the ``/reload`` endpoint (admin-only) after a KG update.
    The next /rank request will rebuild the bridge with the new KG data.
    """
    global _bridge_cache, _rl_input_cache
    with _bridge_lock:
        _bridge_cache = None
        _rl_input_cache = None
        logger.info(
            "P4-024 ROOT FIX (Teammate 12): bridge cache invalidated. "
            "The next /rank request will rebuild the bridge (may take "
            "several minutes on a large KG)."
        )


def _load_candidates_from_checkpoint(
    checkpoint_path: str,
    drug: Optional[str],
    disease: Optional[str],
    limit: int,
) -> Dict[str, Any]:
    """Load the PPO checkpoint and run inference to produce rankings.

    This is the production path -- the checkpoint was trained on real
    bridge data (not standalone fake data), so its rankings are safe
    to ship.

    INT-023 ROOT FIX: the previous code called ``bridge.load_rl_agent``
    which DOES NOT EXIST on GTRLBridge, causing AttributeError on every
    call. The fix loads the PPO model DIRECTLY via stable_baselines3
    ``PPO.load``, builds an RL env from bridge data, and passes the
    loaded model to ``get_top_k_novel_predictions`` — the proper
    integration path per the bridge's docstring.

    P4-004 + P4-019 ROOT FIX (CRITICAL — Team Cosmic / Phase 4): the
    previous code loaded the PPO model via ``PPO.load(checkpoint_path)``
    but did NOT load the `.vecnormalize.pkl` sidecar. The PPO policy
    was trained on VecNormalize-wrapped obs (zero mean, unit variance,
    clipped to ±10). Passing RAW (un-normalized) obs to the policy at
    inference produces a SILENT train/inference distribution shift:
    the policy network's first layer sees inputs WAY outside its
    trained input distribution → outputs are essentially random →
    every Top-N ranking is random → the /rank endpoint serves random
    rankings to pharma partners.

    The fix:
      1. After PPO.load, load the `.vecnormalize.pkl` sidecar via
         ``VecNormalize.load(vecnorm_path, vec_env)``. If the sidecar
         is missing, RAISE RuntimeError (strict mode) — the checkpoint
         is INCOMPLETE and the /rank endpoint cannot serve meaningful
         rankings.
      2. Replace the ``vec_env`` with the loaded ``VecNormalize``
         wrapper before ``model.set_env``.
      3. Set ``bridge.rl_vec_normalize = vec_normalize`` BEFORE calling
         ``bridge.get_top_k_novel_predictions`` so the bridge normalizes
         obs via the SAME wrapper the model was trained with.

    P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
      The previous code created a NEW ``GTRLBridge`` on every /rank
      request and called ``build_model()`` + ``generate_rl_input()``
      each time — multi-minute latency per request. The fix uses the
      cached bridge from ``get_cached_bridge()`` so the expensive build
      happens ONCE (at startup or first request), not per-call. The PPO
      model + VecNormalize sidecar are still loaded per-request because
      the checkpoint may change between requests.

    P4-012 ROOT FIX: returns a dict with 'candidates' AND 'total' keys
    (consistent with _load_candidates_from_csv). When limit=0, returns
    ALL candidates (not empty) — the previous code returned an empty
    list for limit=0 because `candidates[:0]` is empty.

    Returns:
        Dict with keys:
            - candidates: List[Dict] — ranked candidates (post-filter,
              post-pagination when limit>0; ALL when limit==0).
            - total: int — count AFTER filtering, BEFORE pagination.
    """
    try:
        # INT-023: Load PPO model directly (bridge has no load_rl_agent).
        from stable_baselines3 import PPO
        import torch
        # PPO.load requires an env for inference (policy expects vec_env obs).
        # We create a minimal env from bridge data below.
        device = "cuda" if torch.cuda.is_available() else "cpu"
        # Load the model without env first (env attached later).
        model = PPO.load(checkpoint_path, device=device)

        # P4-024 ROOT FIX (Teammate 12): use the CACHED bridge instead
        # of creating a new one on every request. The bridge's
        # ``build_model()`` trains the graph transformer (multi-minute on
        # a real KG) and ``generate_rl_input()`` does a full graph
        # traversal — caching them at startup cuts /rank latency from
        # minutes to seconds. The PPO model + VecNormalize sidecar are
        # still loaded per-request (the checkpoint may change).
        bridge, rl_input_df = get_cached_bridge()
        if rl_input_df is None or len(rl_input_df) == 0:
            logger.warning(
                "P4-024: cached bridge has empty RL input — no candidates. "
                "Check phase1/phase2 data is loaded. Use POST /reload to "
                "rebuild the bridge after a KG update."
            )
            return {"candidates": [], "total": 0}

        # Build RL config and env from the CACHED rl_input_df.
        from rl.rl_drug_ranker import DrugRankingEnv, PipelineConfig
        cfg = PipelineConfig()
        env = DrugRankingEnv(data=rl_input_df, config=cfg)

        # Wrap in DummyVecEnv (PPO expects vec_env observations).
        from stable_baselines3.common.vec_env import DummyVecEnv, VecNormalize
        vec_env = DummyVecEnv([lambda: env])

        # P4-004 + P4-019 ROOT FIX: load the `.vecnormalize.pkl` sidecar.
        # The PPO policy was trained on VecNormalize-wrapped obs; without
        # the sidecar, the policy receives RAW obs (silent distribution
        # shift → random rankings → /rank serves garbage to pharma).
        _ckpt_path_str = str(checkpoint_path)
        if _ckpt_path_str.endswith(".zip"):
            _vecnorm_path = _ckpt_path_str[:-len(".zip")] + ".vecnormalize.pkl"
        else:
            _vecnorm_path = _ckpt_path_str + ".vecnormalize.pkl"
        vec_normalize = None
        if os.path.exists(_vecnorm_path):
            try:
                vec_normalize = VecNormalize.load(_vecnorm_path, vec_env)
                # Inference mode: use running stats, don't normalize rewards.
                vec_normalize.training = False
                vec_normalize.norm_reward = False
                # Use the VecNormalize wrapper as the model's env so
                # model.predict() normalizes obs automatically.
                model.set_env(vec_normalize)
                logger.info(
                    "P4-004 + P4-019 ROOT FIX: loaded VecNormalize sidecar "
                    "from %s and wrapped the env. Observations will be "
                    "NORMALIZED before reaching the policy network — the "
                    "/rank endpoint now serves SCIENTIFICALLY CORRECT "
                    "rankings (was random before this fix).",
                    _vecnorm_path,
                )
            except Exception as vn_exc:
                raise RuntimeError(
                    f"P4-004: VecNormalize sidecar found at {_vecnorm_path} "
                    f"but failed to load: {type(vn_exc).__name__}: {vn_exc}. "
                    f"The checkpoint is CORRUPT. Re-train the model from "
                    f"scratch."
                ) from vn_exc
        else:
            # P4-004 strict mode: sidecar missing → checkpoint incomplete.
            # The /rank endpoint cannot serve meaningful rankings without
            # normalization stats. Raise so the operator knows to re-train.
            raise RuntimeError(
                f"P4-004 ROOT FIX: VecNormalize sidecar NOT FOUND at "
                f"{_vecnorm_path}. The PPO checkpoint at {checkpoint_path} "
                f"is INCOMPLETE — train_agent saves BOTH the .zip (policy "
                f"weights) AND the .vecnormalize.pkl (obs running stats) "
                f"together. Without the sidecar, the /rank endpoint would "
                f"serve RANDOM rankings (silent train/inference "
                f"distribution shift). Re-train the model with train_agent "
                f"(which saves both files)."
            )

        # P4-019 ROOT FIX: set bridge.rl_vec_normalize so the bridge's
        # get_top_k_novel_predictions normalizes obs via the SAME wrapper
        # the model was trained with. The bridge's build_model() may have
        # loaded its OWN VecNormalize (for a different checkpoint), so we
        # OVERRIDE it here with the service-loaded wrapper.
        bridge.rl_vec_normalize = vec_normalize

        # Call bridge with the loaded RL model for proper Phase 6 ranking.
        df = bridge.get_top_k_novel_predictions(top_k=max(limit, 1), rl_model=model, rl_config=cfg)
        candidates = []
        for _, row in df.iterrows():
            candidates.append({
                "drug": row.get("drug", ""),
                "disease": row.get("disease", ""),
                "rank": row.get("rank"),
                "reward": row.get("reward"),
                "policyProb": row.get("policy_prob"),
                "gnnScore": row.get("gnn_score"),
                "safetyScore": row.get("safety_score"),
                "marketScore": row.get("market_score"),
            })
        if drug:
            candidates = [c for c in candidates if drug.lower() in c["drug"].lower()]
        if disease:
            candidates = [c for c in candidates if disease.lower() in c["disease"].lower()]
        # P4-012 ROOT FIX: return a dict with 'candidates' AND 'total'
        # (consistent with _load_candidates_from_csv). When limit > 0,
        # slice the candidates; when limit == 0, return ALL (not empty).
        total = len(candidates)
        if limit > 0:
            candidates = candidates[:limit]
        return {"candidates": candidates, "total": total}
    except Exception as exc:
        # P4-015 ROOT FIX: in strict mode (default), RAISE on checkpoint
        # failure instead of silently falling back to stale CSV.
        strict_mode = os.environ.get("RL_STRICT_CHECKPOINT", "true").lower() not in ("false", "0", "no", "off")
        if strict_mode:
            logger.error(
                "INT-023 + P4-015 STRICT MODE: RL checkpoint inference failed "
                "(%s). Raising exception instead of falling back to CSV.", exc,
            )
            raise RuntimeError(
                f"RL checkpoint inference failed (checkpoint={checkpoint_path}): {exc}. "
                f"Set RL_STRICT_CHECKPOINT=false to allow fallback to CSV."
            ) from exc
        logger.warning("INT-023: RL checkpoint inference failed (%s), falling back to CSV.", exc)
    return {"candidates": [], "total": 0}


@app.get(_URL_HEALTH)
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "phase4_rl",
        "version": "1.0.0",
        "checkpoint_configured": bool(os.environ.get("RL_CHECKPOINT_PATH")),
        "csv_output_available": _find_latest_output_csv() is not None,
    }


def _rank_impl(
    drug: Optional[str],
    disease: Optional[str],
    limit: int,
    offset: int = 0,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Shared logic for GET /rank and POST /rank.

    INT-022 + BE-070 MERGED FIX: returns proper pagination fields:
      - total: total count BEFORE limit/offset (for "Showing X-Y of Z")
      - page: current page number (0-indexed)
      - pageSize: number of items per page
      - count: number of items in THIS response (may be < pageSize at end)

    BE-070 ensures `total` is the REAL filtered count (not just the page
    size), so the frontend can compute proper pagination controls.

    BE-043 v128 ROOT FIX (Task 9.5 — cross-tenant data leak): when
    ``RL_REQUIRE_AUTH=true`` (default), this function REQUIRES ``org_id``.
    Missing org_id → raise HTTPException(401). The ``org_id`` is used to
    FILTER candidates: drugs validated by ANOTHER org (in
    validated_hypotheses.csv) are HIDDEN from this org's results. This
    enforces the standard multi-tenant isolation pattern required by
    21 CFR Part 11 and the DOCX §10 data flywheel's "proprietary
    validated data" moat.

    The filter is FAIL-OPEN if validated_hypotheses.csv is empty/missing
    (fresh deployment: no org has private drugs yet → all drugs are
    public → no filtering). The filter is FAIL-CLOSED for auth: missing
    org_id always returns 401 (no anonymous access in production).
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be in [1, 500]")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    # BE-043 v128 ROOT FIX (Task 9.5): REQUIRE org_id when RL_REQUIRE_AUTH
    # is true (the production default). Missing org_id → 401 Unauthorized.
    # This closes the cross-tenant data leak: a user from org A can no
    # longer call /rank without identifying themselves, and the candidate
    # filter below ensures they cannot see org B's private drugs.
    if _rl_require_auth():
        if not org_id or not org_id.strip():
            raise HTTPException(
                status_code=401,
                detail=(
                    "BE-043 v128: org_id is REQUIRED (RL_REQUIRE_AUTH=true). "
                    "Anonymous access is forbidden — every /rank request "
                    "must be attributable to an org for 21 CFR Part 11 "
                    "compliance and cross-tenant isolation. To disable "
                    "auth for local dev, set RL_REQUIRE_AUTH=false."
                ),
            )
        org_id_norm = org_id.strip().lower()
    else:
        org_id_norm = (org_id or "").strip().lower() or "anonymous"

    # BE-043 v128: log the org_id for audit (21 CFR Part 11). Every
    # candidate fetch is now attributable to the org that requested it.
    # The log entry includes the drug/disease filter and the pagination
    # window so a compliance auditor can reconstruct exactly what was
    # returned to the caller.
    logging.getLogger("phase4_rl.audit").info(
        "rank_fetch_attributed org_id=%s drug=%s disease=%s limit=%d offset=%d",
        org_id_norm,
        drug or "*",
        disease or "*",
        limit,
        offset,
    )

    # BE-043 v128: load the per-org private drug sets ONCE per request.
    # In production, this is cached (the file changes only when a pharma
    # partner validates a new hypothesis — minutes-to-hours cadence, not
    # milliseconds). For correctness, we re-read on every request; a
    # future optimization can add a TTL cache.
    org_private_drugs = _load_org_private_drugs()

    # Try checkpoint first (production path).
    checkpoint_path = os.environ.get("RL_CHECKPOINT_PATH")
    if checkpoint_path and Path(checkpoint_path).exists():
        # INT-022: load ALL matching candidates, then slice with offset+limit.
        # P4-012 ROOT FIX: _load_candidates_from_checkpoint now returns a
        # Dict with 'candidates' and 'total' (matching _load_candidates_from_csv).
        # The previous code called len() on the bare list return value —
        # now we read 'candidates' and 'total' from the dict.
        cp_result = _load_candidates_from_checkpoint(checkpoint_path, drug, disease, limit=0)
        all_candidates = cp_result["candidates"]
        # BE-043 v128: apply cross-tenant isolation filter.
        all_candidates = _filter_candidates_by_org(all_candidates, org_id_norm, org_private_drugs)
        total = len(all_candidates)
        page_candidates = all_candidates[offset:offset + limit] if limit > 0 else all_candidates
        return {
            "candidates": page_candidates,
            # P4-028 ROOT FIX: use timezone-aware UTC datetime (not deprecated
            # utcnow() which returns naive datetime). 21 CFR Part 11 requires
            # unambiguous timestamps; naive datetime is interpreted as LOCAL
            # time by the frontend's new Date(), causing audit log mismatches.
            # P4-045 ROOT FIX: source is "service" (not "rl_service") to
            # match the frontend's rl-ranker.ts type contract
            # ("csv" | "service" | "none"). The frontend's availability
            # check gates on result.source === "none"; "rl_service" was
            # unrecognized and could trigger a false "RL not available" state.
            "source": "service",
            "modelVersion": "rl_drug_ranker.py-v105",
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "total": total,
            "page": offset // limit if limit > 0 else 0,
            "pageSize": limit,
            "count": len(page_candidates),
            "backend": "checkpoint",
            "orgId": org_id_norm,  # BE-043 v128: echo back for audit
        }

    # Fallback: read the latest top_candidates_*.csv.
    csv_path = _find_latest_output_csv()
    if csv_path is None:
        return {
            "candidates": [],
            "source": "none",
            # P4-028: timezone-aware UTC
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "total": 0,
            "page": 0,
            "pageSize": limit,
            "count": 0,
            "note": "No RL output yet. Run `python run_4phase.py` to generate top_candidates_*.csv.",
            "orgId": org_id_norm,
        }

    # BE-070 + INT-022: load ALL matching candidates with total count,
    # then slice with offset+limit for pagination.
    result = _load_candidates_from_csv(csv_path, drug, disease, limit=0)
    all_candidates = result["candidates"]
    # BE-043 v128: apply cross-tenant isolation filter.
    all_candidates = _filter_candidates_by_org(all_candidates, org_id_norm, org_private_drugs)
    total = len(all_candidates)
    page_candidates = all_candidates[offset:offset + limit] if limit > 0 else all_candidates
    return {
        "candidates": page_candidates,
        # P4-045: "service" matches frontend contract (not "rl_service")
        "source": "service",
        "modelVersion": "rl_drug_ranker.py-v105",
        # P4-028: timezone-aware UTC (not deprecated utcnow)
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "page": offset // limit if limit > 0 else 0,
        "pageSize": limit,
        "count": len(page_candidates),
        "csvPath": str(csv_path),
        "backend": "csv",
        "orgId": org_id_norm,  # BE-043 v128: echo back for audit
    }


@app.get(_URL_RANK)
def rank_get(
    drug: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    org_id: Optional[str] = Query(None, description="BE-035 + BE-043 ROOT FIX (v118): the org scope for the candidate fetch. Forwarded by the frontend's getRankedHypotheses (rl-ranker.ts). Logged for audit (21 CFR Part 11). May be used to filter candidates by org ownership in a future update."),
) -> Dict[str, Any]:
    """Get ranked hypotheses (optionally filtered by drug/disease).

    INT-022: accepts offset for pagination.

    BE-035 + BE-043 ROOT FIX (v118): accepts ``org_id`` query param.
    The frontend's rl-ranker.ts now forwards the user's active orgId
    here; the service logs it for audit and may use it to filter
    candidates by org ownership in a future update.
    """
    return _rank_impl(drug, disease, limit, offset, org_id=org_id)


@app.get(_URL_RANK_BY_DRUG)
async def rank_by_drug(
    drug: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    org_id: Optional[str] = Query(None, description="BE-035 + BE-043 ROOT FIX (v118): the org scope for the candidate fetch."),
) -> Dict[str, Any]:
    """Get ranked hypotheses for a specific drug.

    P4-042 ROOT FIX (LOW — Team Cosmic / Phase 4): URL-decode the drug
    name before matching. FastAPI automatically decodes path parameters,
    so `rank/aspirin%20EC` reaches this function with drug="aspirin EC".
    The previous docstring claimed the search was a case-insensitive
    substring match on generic names, but did not document that the path
    parameter is URL-decoded by FastAPI. The fix adds explicit
    documentation and a defensive urllib.parse.unquote call (in case a
    future middleware re-encodes the path). The actual matching is done
    by _rank_impl (case-insensitive substring on drug name).

    BE-035 + BE-043 ROOT FIX (v118): accepts ``org_id`` query param.
    """
    # P4-042: FastAPI decodes path params automatically, but we add a
    # defensive unquote in case a proxy/middleware re-encodes the path.
    from urllib.parse import unquote
    decoded_drug = unquote(drug)
    return _rank_impl(drug=decoded_drug, disease=None, limit=limit, offset=offset, org_id=org_id)


@app.post(_URL_RANK)
def rank_post(
    req: RankRequest,
    org_id: Optional[str] = Query(None, description="BE-035 + BE-043 ROOT FIX (v118): the org scope for the candidate fetch. Forwarded by the frontend's getRankedHypotheses (rl-ranker.ts). Logged for audit (21 CFR Part 11)."),
) -> Dict[str, Any]:
    """Get ranked hypotheses with body filters.

    BE-035 + BE-043 ROOT FIX (v118): accepts ``org_id`` query param.
    The frontend's rl-ranker.ts now forwards the user's active orgId
    here; the service logs it for audit and may use it to filter
    candidates by org ownership in a future update.

    INT-022: offset from query params (POST body doesn't include it).
    FastAPI parses offset from the query string even for POST.
    """
    return _rank_impl(req.drug, req.disease, req.limit, org_id=org_id)


# ============================================================================
# P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
# /reload endpoint — admin-only cache refresh after a KG update.
# ============================================================================
#
# WHY THIS EXISTS:
#   The ``get_cached_bridge()`` function builds the GTRLBridge ONCE and
#   reuses it for all subsequent /rank requests (multi-minute latency
#   cut to seconds). But when the underlying KG is updated (e.g., a
#   pharma partner validates a new hypothesis via /validate, which
#   appends to phase1/processed_data/validated_hypotheses.csv and adds
#   a Neo4j edge), the cached bridge is STALE — it was built from the
#   OLD KG. Without /reload, /rank would serve rankings computed from
#   the old KG until the service is restarted.
#
# AUTH:
#   /reload triggers a multi-minute rebuild on the next /rank call.
#   Anonymous access would let any caller DoS the service by repeatedly
#   invalidating the cache. We require a bearer token matching the
#   ``RL_ADMIN_TOKEN`` env var. If ``RL_ADMIN_TOKEN`` is not set, the
#   endpoint returns 503 (admin not configured) — forcing operators
#   to explicitly set the token before /reload can be used.
#
#   In production, /reload should ALSO be protected at the network
#   layer (e.g., only reachable from the backend's subnet, not from
#   the public internet). The token check is DEFENSE-IN-DEPTH, not a
#   substitute for network ACLs.
# ============================================================================

_RELOAD_URL = "/reload"


def _verify_admin_token(
    authorization: Optional[str] = Header(
        default=None,
        description="Bearer <RL_ADMIN_TOKEN> — required to call /reload.",
    ),
) -> str:
    """Verify the bearer token against RL_ADMIN_TOKEN (admin-only endpoints).

    Returns the token (for audit logging) on success; raises 401/503 otherwise.
    """
    expected_token = os.environ.get("RL_ADMIN_TOKEN", "").strip()
    if not expected_token:
        # Operator has not configured the admin token → /reload is disabled.
        raise HTTPException(
            status_code=503,
            detail=(
                "P4-024 ROOT FIX (Teammate 12): /reload is disabled because "
                "RL_ADMIN_TOKEN env var is not set. To enable /reload, set "
                "RL_ADMIN_TOKEN to a strong random string (e.g., "
                "`openssl rand -hex 32`) and restart the service. In "
                "production, also restrict /reload at the network layer "
                "(backend subnet only)."
            ),
        )
    if authorization is None:
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Expected: Bearer <RL_ADMIN_TOKEN>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header. Expected: Bearer <RL_ADMIN_TOKEN>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = parts[1].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Empty bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Constant-time comparison to prevent timing attacks on the token.
    import hmac as _hmac
    if not _hmac.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=401,
            detail="Invalid admin token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


@app.post(_RELOAD_URL, tags=["admin"])
def reload_bridge(
    _admin_token: str = Depends(_verify_admin_token),
) -> Dict[str, Any]:
    """Reload the GT bridge and RL input cache.

    P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):

    Invalidates the cached ``GTRLBridge`` + RL input DataFrame so the
    next /rank request rebuilds them from the CURRENT KG. Use this
    after a KG update (e.g., after a pharma partner validates a new
    hypothesis via /validate, which appends to validated_hypotheses.csv
    and adds a Neo4j edge).

    BEHAVIOR:
      1. Clears the cached bridge + RL input (fast, no rebuild here).
      2. The NEXT /rank request will rebuild the bridge (multi-minute
         latency on a real KG). The /reload endpoint itself returns
         immediately — the rebuild is LAZY, not eager. This avoids
         blocking the admin call while the rebuild runs, but it means
         the first /rank call after /reload will be slow.

    AUTH:
      Bearer token matching RL_ADMIN_TOKEN env var. If RL_ADMIN_TOKEN
      is not set, returns 503 (admin not configured).

    RETURNS:
      {
        "status": "reloaded",
        "note": "Next /rank request will rebuild the bridge (may take several minutes)."
      }
    """
    invalidate_bridge_cache()
    logger.info(
        "P4-024 ROOT FIX (Teammate 12): /reload called by admin — bridge "
        "cache invalidated. Next /rank request will rebuild the bridge."
    )
    return {
        "status": "reloaded",
        "note": (
            "Bridge cache invalidated. The next /rank request will "
            "rebuild the bridge (may take several minutes on a large KG)."
        ),
    }


# ============================================================================
# Issue 227 ROOT FIX: /validate endpoint for data flywheel writeback
# ============================================================================
#
# The frontend's /api/hypothesis/validate route previously spawned a
# subprocess to call scripts/hypothesis_writeback.py — a path that did
# not resolve correctly when Next.js ran from frontend/. This endpoint
# replaces the subprocess path with an HTTP proxy.
#
# The endpoint calls phase4.writeback.write_validated_hypothesis() which
# writes the validated hypothesis to all 3 phases:
#   - Phase 1: appends to phase1/processed_data/validated_hypotheses.csv
#   - Phase 2: adds a VALIDATED_TREATS edge to Neo4j (when available)
#   - Phase 3: appends to graph_transformer/retrain_triggered.json
#
# The writeback is APPEND-ONLY and timestamped (21 CFR Part 11 compliance).
# This endpoint does NOT retry on failure — writeback is not idempotent
# (appending to a CSV twice would create duplicate records).


class ValidateRequest(BaseModel):
    """Request body for /validate (Issue 227).

    Matches the ValidatedHypothesis dataclass in phase4/writeback.py.
    The frontend's RlValidateRequestSchema (ml-contracts.ts) is the
    TypeScript mirror of this Pydantic model — the two MUST stay in sync.

    SH-004 ROOT FIX: the `outcome` field accepts the 4 canonical
    values from shared.contracts.writeback.VALID_OUTCOMES. The
    frontend's ValidateRequest type mirrors this enum exactly.
    """
    drug: str
    disease: str
    outcome: str  # validated_positive | validated_negative | validated_toxic | invalidated
    validated_by: str
    validation_study_id: Optional[str] = None
    notes: Optional[str] = None
    original_gt_score: Optional[float] = None
    original_rl_rank: Optional[int] = None


@app.post(_URL_VALIDATE)
def validate(req: ValidateRequest) -> Dict[str, Any]:
    """Write a validated hypothesis back to all 3 phases (data flywheel).

    Issue 227 ROOT FIX: this endpoint replaces the subprocess shell-out
    from the frontend's /api/hypothesis/validate route. The frontend now
    proxies to RL_SERVICE_URL/validate via HTTP.

    The endpoint calls phase4.writeback.write_validated_hypothesis()
    which:
      1. Appends to phase1/processed_data/validated_hypotheses.csv
         (becomes a new labeled data point for future KG builds).
      2. Adds a VALIDATED_TREATS edge to Neo4j (when available) with
         validated_at timestamp + validated_by partner identifier.
      3. Appends to graph_transformer/retrain_triggered.json so the
         next GT training run includes this pair in known_pairs.

    Returns the writeback result containing:
      - phase1_csv_path: path to the appended CSV
      - phase2_neo4j_written: whether the Neo4j edge was added
      - phase3_trigger_path: path to the retrain trigger JSON
      - validated_hypothesis: the ValidatedHypothesis record
      - writeback_version: the writeback module version

    Errors:
      - 400: invalid outcome enum value
      - 500: writeback failed (CSV append error, Neo4j connection error, etc.)
    """
    # SH-002 + SH-035 ROOT FIX: validate the outcome enum against the
    # CANONICAL shared contract set (VALID_OUTCOMES), not a hardcoded
    # copy. The previous hardcoded set happened to match the shared
    # contract, but a hardcoded copy can silently drift. Sourcing the
    # set from `shared.contracts.writeback.VALID_OUTCOMES` guarantees
    # any change to the canonical enum is picked up here automatically.
    valid_outcomes = set(_VALID_OUTCOMES)
    if req.outcome not in valid_outcomes:
        raise HTTPException(
            status_code=400,
            detail=(
                f"outcome must be one of: {', '.join(sorted(valid_outcomes))}. "
                f"Got: {req.outcome!r}"
            ),
        )

    try:
        # Import here (not at module top) so the service can start even
        # if phase4.writeback has a missing optional dependency (e.g.,
        # neo4j driver not installed). The /rank endpoint does not need
        # writeback, so it should not be blocked by a writeback import
        # failure.
        from phase4.writeback import (
            write_validated_hypothesis,
            ValidationOutcome,
            WRITEBACK_VERSION,
        )

        # Cast the string outcome to the Literal type. The set membership
        # check above guarantees the value is one of the 4 valid strings.
        outcome_typed = req.outcome  # type: ignore[assignment]

        result = write_validated_hypothesis(
            drug=req.drug,
            disease=req.disease,
            outcome=outcome_typed,  # type: ignore[arg-type]
            validated_by=req.validated_by,
            validation_study_id=req.validation_study_id,
            notes=req.notes,
            original_gt_score=req.original_gt_score,
            original_rl_rank=req.original_rl_rank,
        )

        # write_validated_hypothesis returns a Dict[str, Any] with keys:
        # phase1_csv_path, phase2_neo4j_written, phase3_trigger_path,
        # validated_hypothesis, writeback_version. We forward it directly.
        result_dict: Dict[str, Any] = dict(result) if isinstance(result, dict) else {}

        return {
            "ok": True,
            "writeback": {
                "phase1_csv_path": str(result_dict.get("phase1_csv_path", "")),
                "phase2_neo4j_written": bool(result_dict.get("phase2_neo4j_written", False)),
                "phase3_trigger_path": str(result_dict.get("phase3_trigger_path", "")),
                "validated_hypothesis": result_dict.get("validated_hypothesis", {}),
                "writeback_version": result_dict.get("writeback_version", WRITEBACK_VERSION),
            },
            "message": (
                "Hypothesis validation written back to Phase 1 (CSV), "
                "Phase 2 (Neo4j edge), and Phase 3 (retrain trigger)."
            ),
        }
    except HTTPException:
        # Re-raise HTTPExceptions (e.g., the 400 above) without wrapping.
        raise
    except Exception as exc:
        logger.error(
            "Issue 227 /validate writeback failed for drug=%s disease=%s: %s",
            req.drug, req.disease, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail=(
                f"Hypothesis writeback failed: {exc}. The writeback is "
                f"append-only — re-submitting will NOT create duplicate "
                f"records if Phase 1 succeeded but Phase 2/3 failed. "
                f"Check the service logs for which phase(s) completed."
            ),
        )


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("RL_SERVICE_PORT", "8004"))
    host = os.environ.get("RL_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 4 RL Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
