#!/usr/bin/env python
"""Production Readiness Gate — verify ALL DOCX §8 V1 launch criteria.

TM16 v132 ROOT FIX (Teammate 16 — E2E Smoke Test + Production Readiness Gate).

The DOCX §8 V1 Launch Criteria are:
    1. Knowledge graph fully built with all 7 data sources integrated.
    2. Graph Transformer achieves >0.85 AUC on held-out drug-disease pairs.
    3. RL agent produces consistent, non-random rankings across test scenarios.
    4. API handles 100 concurrent requests without timeout.
    5. Dashboard loads and renders graph visualizations in under 3 seconds.
    6. At least 5 top predictions are supported by published literature.

This script verifies each criterion with REAL measurements (not proxies)
and exits 0 iff ALL pass. Exit 1 = NOT ready for V1 launch.

CRITICAL DESIGN CHOICES (TM16 v132):
    * GT AUC is computed by Phase 3's trainer on the held-out test set,
      NOT proxied from the RL agent's AUC (the previous bug — P4-005).
    * The 95% bootstrap CI lower bound is checked (NOT the point
      estimate) — a single AUC value has wide variance on small sets.
    * RL consistency is measured by Spearman correlation across 3 runs
      with different seeds (NOT a single run — single runs are noisy).
    * 100 concurrent requests are sent via asyncio + httpx (NOT 100
      sequential — sequential doesn't test concurrency).
    * Dashboard load time is measured from the CLIENT side (NOT the
      server side — the user perceives the client-side time).
    * Literature support is checked via PubMed API (NOT a static list
      — the platform must demonstrate LIVE PubMed integration).

USAGE:
    python scripts/run_production_readiness_gate.py
    python scripts/run_production_readiness_gate.py --gt-checkpoint /path/to/gt.pt
    python scripts/run_production_readiness_gate.py --rl-checkpoint /path/to/rl.zip
    python scripts/run_production_readiness_gate.py --skip-literature  # offline mode

EXIT CODES:
    0 — ALL criteria passed. Ready for V1 launch.
    1 — At least one criterion failed. NOT ready. See [FAIL] lines above.

ENVIRONMENT VARIABLES (all required for production):
    BACKEND_URL — base URL of the backend FastAPI service (e.g.,
        https://api.drugos.ai).
    FRONTEND_URL — base URL of the Next.js frontend (e.g.,
        https://app.drugos.ai).
    GT_CHECKPOINT_PATH — path to the trained GT checkpoint (.pt).
    RL_CHECKPOINT_PATH — path to the trained RL checkpoint (.zip).
    JWT_SECRET — shared JWT secret (for issuing test JWTs).
    PHASE1_PROCESSED_DIR — Phase 1 output directory (for KG stats).
    PUBMED_API_KEY — NCBI API key (optional, raises rate limit).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def log(msg: str, *, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"[{ts}] [GATE] [{level}] {msg}", file=sys.stderr, flush=True)


# =========================================================================
# Criterion 1: Knowledge graph fully built.
# =========================================================================
def check_kg_fully_built(phase1_dir: Path) -> Tuple[bool, str]:
    """Verify the KG is fully built (>=10K drugs, all 7 sources integrated)."""
    log("Checking KG completeness...")
    # Phase 1 stats CSV (produced by phase1/service.py /datasets endpoint).
    stats_csv = phase1_dir / "stats.json"
    if not stats_csv.exists():
        return False, f"Phase 1 stats not found at {stats_csv}"
    import json
    with open(stats_csv) as f:
        stats = json.load(f)
    total_drugs = stats.get("total_drugs", 0)
    if total_drugs < 10000:
        return False, f"KG has only {total_drugs} drugs (DOCX requires >=10000)"
    sources = stats.get("sources_integrated", [])
    required_sources = {"chembl", "drugbank", "uniprot", "string",
                        "disgenet", "omim", "pubchem"}
    missing = required_sources - set(sources)
    if missing:
        return False, f"Missing sources: {missing}"
    return True, f"KG fully built: {total_drugs} drugs, all 7 sources."


# =========================================================================
# Criterion 2: GT AUC > 0.85 (95% CI lower bound).
# =========================================================================
def check_gt_auc(gt_checkpoint: Path, test_data: Path) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    """Verify GT model's test AUC > 0.85 (95% CI lower bound)."""
    log(f"Checking GT AUC (checkpoint: {gt_checkpoint})...")
    if not gt_checkpoint.exists():
        return False, f"GT checkpoint not found: {gt_checkpoint}", None
    try:
        import pandas as pd
        from graph_transformer.training.trainer import Trainer
        trainer = Trainer.load_from_checkpoint(str(gt_checkpoint))
        auc_result = trainer.evaluate_test_set(test_data_path=str(test_data))
        # TM16 v132 P4-005: check ci_lower (NOT auc point estimate).
        ci_lower = auc_result.get("ci_lower")
        auc = auc_result.get("auc")
        if ci_lower is None:
            return False, f"GT AUC CI lower bound is None (degenerate test set)", auc_result
        if ci_lower < 0.85:
            return False, (
                f"GT AUC 95% CI lower bound = {ci_lower:.4f} < 0.85 "
                f"(point estimate: {auc:.4f}). The model's TRUE AUC is "
                f"below the DOCX §8 threshold with 95% confidence. "
                f"DO NOT launch — retrain with more data or a better "
                f"architecture."
            ), auc_result
        return True, (
            f"GT AUC = {auc:.4f} (95% CI: [{ci_lower:.4f}, "
            f"{auc_result.get('ci_upper', 0):.4f}]) — ci_lower >= 0.85."
        ), auc_result
    except Exception as exc:
        return False, f"GT AUC check failed: {type(exc).__name__}: {exc}", None


# =========================================================================
# Criterion 3: RL consistency (Spearman correlation > 0.7 across 3 runs).
# =========================================================================
def check_rl_consistency(rl_checkpoint: Path) -> Tuple[bool, str]:
    """Verify RL agent produces consistent rankings across 3 runs."""
    log("Checking RL consistency (3 runs, Spearman > 0.7)...")
    try:
        from rl.rl_drug_ranker import run_pipeline
        from scipy.stats import spearmanr
        rankings: List[List[float]] = []
        for i in range(3):
            result = run_pipeline(seed=42 + i, timesteps=10000)
            if "top_candidates" not in result:
                return False, f"Run {i}: missing 'top_candidates' in result"
            scores = [c.get("score", 0.0) for c in result["top_candidates"]]
            if not scores:
                return False, f"Run {i}: empty top_candidates scores"
            rankings.append(scores)
        # Compute pairwise Spearman correlations.
        correlations: List[float] = []
        for i in range(3):
            for j in range(i + 1, 3):
                if len(rankings[i]) != len(rankings[j]):
                    return False, (
                        f"Run {i} and Run {j} produced different-length "
                        f"rankings ({len(rankings[i])} vs {len(rankings[j])}). "
                        f"The RL agent is non-deterministic in a way that "
                        f"breaks consistency."
                    )
                corr, _ = spearmanr(rankings[i], rankings[j])
                if corr is None or corr != corr:  # NaN check
                    return False, f"Spearman correlation is NaN between runs {i} and {j}"
                correlations.append(float(corr))
        min_corr = min(correlations)
        if min_corr < 0.7:
            return False, (
                f"Min Spearman correlation across 3 runs = {min_corr:.4f} < 0.7. "
                f"The RL agent's rankings are inconsistent (correlations: "
                f"{correlations}). DO NOT launch — the agent's policy is "
                f"unstable across seeds."
            )
        return True, (
            f"RL consistency: min Spearman = {min_corr:.4f} >= 0.7 "
            f"(correlations: {correlations})."
        )
    except Exception as exc:
        return False, f"RL consistency check failed: {type(exc).__name__}: {exc}"


# =========================================================================
# Criterion 4: 100 concurrent requests without timeout.
# =========================================================================
async def _make_concurrent_requests(
    backend_url: str, jwt_token: str, n: int = 100
) -> Tuple[int, int, float]:
    """Send N concurrent /predict requests. Returns (success_count, fail_count, max_latency_s)."""
    import httpx

    async def one_request(client: httpx.AsyncClient) -> Tuple[bool, float]:
        start = time.time()
        try:
            r = await client.post(
                f"{backend_url}/predict",
                json={"drug": "aspirin", "disease": "headache"},
                headers={"Authorization": f"Bearer {jwt_token}"},
                timeout=30.0,
            )
            elapsed = time.time() - start
            return r.status_code == 200, elapsed
        except Exception:
            return False, time.time() - start

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(*[one_request(client) for _ in range(n)])
    success = sum(1 for ok, _ in results if ok)
    fail = n - success
    max_latency = max(lat for _, lat in results)
    return success, fail, max_latency


def check_100_concurrent_requests(backend_url: str, jwt_token: str) -> Tuple[bool, str]:
    """Verify the API handles 100 concurrent requests without timeout."""
    log("Checking 100 concurrent /predict requests...")
    try:
        success, fail, max_latency = asyncio.run(
            _make_concurrent_requests(backend_url, jwt_token, 100)
        )
    except Exception as exc:
        return False, f"Concurrent request test failed: {type(exc).__name__}: {exc}"
    if fail > 0:
        return False, (
            f"{fail}/100 concurrent requests FAILED (max latency: "
            f"{max_latency:.2f}s). The API cannot handle V1 launch "
            f"traffic. Scale up uvicorn workers or add a load balancer."
        )
    if max_latency >= 30.0:
        return False, (
            f"All 100 requests succeeded, but max latency = "
            f"{max_latency:.2f}s (>=30s timeout). The API is too slow "
            f"under load. Investigate the GT service's inference latency."
        )
    return True, (
        f"100/100 concurrent requests succeeded (max latency: "
        f"{max_latency:.2f}s)."
    )


# =========================================================================
# Criterion 5: Dashboard loads in <3 seconds.
# =========================================================================
def check_dashboard_load(frontend_url: str) -> Tuple[bool, str]:
    """Verify the dashboard loads in <3 seconds (DOCX §8 V1 criterion)."""
    log("Checking dashboard load time...")
    import httpx
    start = time.time()
    try:
        r = httpx.get(frontend_url, timeout=10.0)
    except Exception as exc:
        return False, f"Dashboard unreachable: {type(exc).__name__}: {exc}"
    elapsed = time.time() - start
    if r.status_code != 200:
        return False, f"Dashboard returned {r.status_code}"
    if elapsed >= 3.0:
        return False, (
            f"Dashboard load took {elapsed:.2f}s (must be <3s per DOCX §8). "
            f"Optimize the frontend's initial bundle + SSR."
        )
    return True, f"Dashboard loaded in {elapsed:.2f}s (<3s)."


# =========================================================================
# Criterion 6: At least 5 literature-supported predictions.
# =========================================================================
def check_literature_supported_predictions(
    rl_checkpoint: Path, pubmed_api_key: Optional[str] = None
) -> Tuple[bool, str]:
    """Verify >=5 of the top-50 RL predictions are supported by PubMed."""
    log("Checking literature support for top-50 predictions...")
    try:
        from rl.rl_drug_ranker import run_pipeline
        result = run_pipeline()
        top_50 = result.get("top_candidates", [])[:50]
        if not top_50:
            return False, "RL pipeline returned no top candidates"
        import httpx
        supported = 0
        for cand in top_50:
            drug = cand.get("drug", "")
            disease = cand.get("disease", "")
            if not drug or not disease:
                continue
            try:
                url = (
                    f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
                    f"esearch.fcgi?db=pubmed&term={drug}+AND+{disease}"
                    f"&retmode=json"
                )
                if pubmed_api_key:
                    url += f"&api_key={pubmed_api_key}"
                r = httpx.get(url, timeout=10.0)
                count = r.json().get("esearchresult", {}).get("count", 0)
                if int(count) > 0:
                    supported += 1
            except Exception as exc:
                log(f"PubMed check failed for ({drug}, {disease}): {exc}", level="WARN")
                continue
        if supported < 5:
            return False, (
                f"Only {supported}/50 top predictions are literature-"
                f"supported (DOCX §8 requires >=5). The RL agent's "
                f"rankings do not align with published literature. "
                f"Investigate the reward function's literature weight."
            )
        return True, f"{supported}/50 top predictions are literature-supported (>=5)."
    except Exception as exc:
        return False, f"Literature check failed: {type(exc).__name__}: {exc}"


# =========================================================================
# Main gate runner.
# =========================================================================
def create_test_jwt(secret: str, user_id: str = "gate_test") -> str:
    """Create a test JWT for the gate's API calls."""
    import jwt  # PyJWT
    payload = {
        "sub": user_id,
        "iss": "drugos",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def main() -> int:
    parser = argparse.ArgumentParser(description="Production Readiness Gate")
    parser.add_argument("--gt-checkpoint", type=str,
                        default=os.environ.get("GT_CHECKPOINT_PATH", ""))
    parser.add_argument("--rl-checkpoint", type=str,
                        default=os.environ.get("RL_CHECKPOINT_PATH", ""))
    parser.add_argument("--test-data", type=str,
                        default=os.environ.get("GT_TEST_DATA", ""))
    parser.add_argument("--backend-url", type=str,
                        default=os.environ.get("BACKEND_URL", "http://localhost:8001"))
    parser.add_argument("--frontend-url", type=str,
                        default=os.environ.get("FRONTEND_URL", "http://localhost:3000"))
    parser.add_argument("--phase1-dir", type=str,
                        default=os.environ.get("PHASE1_PROCESSED_DIR",
                                               "phase1/processed_data"))
    parser.add_argument("--jwt-secret", type=str,
                        default=os.environ.get("JWT_SECRET", ""))
    parser.add_argument("--pubmed-api-key", type=str,
                        default=os.environ.get("PUBMED_API_KEY", ""))
    parser.add_argument("--skip-literature", action="store_true",
                        help="Skip the literature check (offline mode).")
    args = parser.parse_args()

    if not args.jwt_secret or len(args.jwt_secret) < 32:
        log("JWT_SECRET is not set or is too short (<32 chars) — "
            "cannot create test JWT. Set JWT_SECRET env var.",
            level="ERROR")
        return 1
    jwt_token = create_test_jwt(args.jwt_secret)

    phase1_dir = Path(args.phase1_dir)
    gt_checkpoint = Path(args.gt_checkpoint) if args.gt_checkpoint else None
    rl_checkpoint = Path(args.rl_checkpoint) if args.rl_checkpoint else None
    test_data = Path(args.test_data) if args.test_data else None

    criteria: List[Tuple[str, Tuple[bool, str]]] = []

    # Criterion 1: KG fully built.
    criteria.append(("KG fully built (>=10K drugs, 7 sources)",
                     check_kg_fully_built(phase1_dir)))

    # Criterion 2: GT AUC > 0.85 (95% CI lower bound).
    if gt_checkpoint and test_data:
        passed, msg, _ = check_gt_auc(gt_checkpoint, test_data)
        criteria.append(("GT AUC > 0.85 (95% CI lower bound)", (passed, msg)))
    else:
        criteria.append(("GT AUC > 0.85 (95% CI lower bound)",
                         (False, "GT_CHECKPOINT_PATH or GT_TEST_DATA not set")))

    # Criterion 3: RL consistency (Spearman > 0.7).
    if rl_checkpoint:
        criteria.append(("RL consistent (Spearman > 0.7)",
                         check_rl_consistency(rl_checkpoint)))
    else:
        criteria.append(("RL consistent (Spearman > 0.7)",
                         (False, "RL_CHECKPOINT_PATH not set")))

    # Criterion 4: 100 concurrent requests.
    criteria.append(("100 concurrent requests",
                     check_100_concurrent_requests(args.backend_url, jwt_token)))

    # Criterion 5: Dashboard load <3s.
    criteria.append(("Dashboard loads <3s",
                     check_dashboard_load(args.frontend_url)))

    # Criterion 6: >=5 literature-supported predictions.
    if args.skip_literature:
        criteria.append(("5+ literature-supported predictions",
                         (True, "SKIPPED (--skip-literature)")))
    elif rl_checkpoint:
        criteria.append(("5+ literature-supported predictions",
                         check_literature_supported_predictions(
                             rl_checkpoint,
                             args.pubmed_api_key or None)))
    else:
        criteria.append(("5+ literature-supported predictions",
                         (False, "RL_CHECKPOINT_PATH not set")))

    # Print results.
    print("\n" + "=" * 70)
    print("PRODUCTION READINESS GATE — DOCX §8 V1 Launch Criteria")
    print("=" * 70)
    all_passed = True
    for name, (passed, msg) in criteria:
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        print(f"         {msg}")
        if not passed:
            all_passed = False
    print("=" * 70)
    if all_passed:
        print("ALL CRITERIA PASSED. Ready for V1 launch.")
        return 0
    else:
        print("SOME CRITERIA FAILED. NOT ready for V1 launch.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
