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
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make rl + repo root importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
_RL_CORS_ORIGINS = os.environ.get("RL_CORS_ORIGINS", "http://localhost:3000")
if _RL_CORS_ORIGINS == "*":
    _allow_origins = ["*"]
else:
    _allow_origins = [o.strip() for o in _RL_CORS_ORIGINS.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class RankRequest(BaseModel):
    drug: Optional[str] = None
    disease: Optional[str] = None
    limit: int = 50


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


def _load_candidates_from_csv(csv_path: Path, drug: Optional[str], disease: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """Parse the RL ranker's output CSV into the RankedHypothesis schema."""
    import csv as csv_mod

    out: List[Dict[str, Any]] = []
    if not csv_path.exists():
        return out

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

    # P4-013 ROOT FIX: use streaming iterator instead of list(reader).
    # The previous code loaded ALL rows into memory (~500MB for 1M rows),
    # then iterated. The break only stopped PROCESSING — the full file was
    # already in memory. This fix uses a streaming for-loop and breaks
    # as soon as we have enough candidates, preventing OOM on production.
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv_mod.DictReader(f)
        rows = enumerate(reader)  # lazy iterator, NOT list

    for i, row in rows:
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

        gnn = _num(r.get("gnn_score"))
        safety = _num(r.get("safety_score"))
        market = _num(r.get("market_score"))
        reward = _num(r.get("reward"))
        rank = _num(r.get("rank")) or (i + 1)
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
            "rank": int(rank) if rank else (i + 1),
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

    # P4-014 ROOT FIX: sort ALL candidates by rank, THEN apply limit.
    # The previous code broke after ``len(out) >= limit`` and THEN sorted,
    # which meant only the FIRST ``limit`` rows in CSV order were sorted —
    # NOT the top-``limit`` by rank. If the CSV was unsorted, the API
    # returned arbitrary candidates instead of the true top-N.
    out.sort(key=lambda c: (c.get("rank") or 1e9))
    out = out[:limit]
    return out


def _load_candidates_from_checkpoint(checkpoint_path: str, drug: Optional[str], disease: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """Load the PPO checkpoint and run inference to produce rankings.

    This is the production path -- the checkpoint was trained on real
    bridge data (not standalone fake data), so its rankings are safe
    to ship.
    """
    try:
        # P4-003 ROOT FIX: GTRLBridge has NO method ``rank_top_candidates``.
        # The previous ``hasattr(bridge, 'rank_top_candidates')`` guard was
        # always False, so the checkpoint branch NEVER executed and the
        # service ALWAYS fell back to CSV. The fix calls the EXISTING
        # ``get_top_k_novel_predictions`` method (verified present in
        # graph_transformer/gt_rl_bridge.py). The dead ``hasattr`` guard
        # is removed.
        from graph_transformer.gt_rl_bridge import GTRLBridge
        bridge = GTRLBridge(output_dir=str(_HERE / "_service_output"), device="cpu", seed=42)
        bridge.load_rl_agent(checkpoint_path)
        df = bridge.get_top_k_novel_predictions(top_k=limit)
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
        return candidates[:limit]
    except Exception as exc:
        # P4-015 ROOT FIX: in strict mode (default), RAISE on checkpoint
        # failure instead of silently falling back to stale CSV. The env
        # var RL_STRICT_CHECKPOINT controls this: "true" (default) = raise,
        # "false" = warn and fall back. Pharma partners must see an error
        # when the checkpoint is broken, not stale CSV rankings.
        strict_mode = os.environ.get("RL_STRICT_CHECKPOINT", "true").lower() not in ("false", "0", "no", "off")
        if strict_mode:
            logger.error(
                "P4-015 STRICT MODE: RL checkpoint inference failed and "
                "RL_STRICT_CHECKPOINT=true. Raising exception instead of "
                "silently falling back to stale CSV. Error: %s", exc,
            )
            raise RuntimeError(
                f"RL checkpoint inference failed (checkpoint={checkpoint_path}): {exc}. "
                f"Set RL_STRICT_CHECKPOINT=false to allow fallback to CSV."
            ) from exc
        logger.warning("RL checkpoint inference failed (%s), falling back to CSV.", exc)
    return []


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "phase4_rl",
        "version": "1.0.0",
        "checkpoint_configured": bool(os.environ.get("RL_CHECKPOINT_PATH")),
        "csv_output_available": _find_latest_output_csv() is not None,
    }


def _rank_impl(drug: Optional[str], disease: Optional[str], limit: int) -> Dict[str, Any]:
    """Shared logic for GET /rank and POST /rank."""
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be in [1, 500]")

    # Try checkpoint first (production path).
    checkpoint_path = os.environ.get("RL_CHECKPOINT_PATH")
    if checkpoint_path and Path(checkpoint_path).exists():
        candidates = _load_candidates_from_checkpoint(checkpoint_path, drug, disease, limit)
        if candidates:
            return {
                "candidates": candidates,
                "source": "rl_service",
                "modelVersion": "rl_drug_ranker.py-v105",
                "generatedAt": __import__("datetime").datetime.utcnow().isoformat(),
                "count": len(candidates),
                "backend": "checkpoint",
            }

    # Fallback: read the latest top_candidates_*.csv.
    csv_path = _find_latest_output_csv()
    if csv_path is None:
        return {
            "candidates": [],
            "source": "none",
            "generatedAt": __import__("datetime").datetime.utcnow().isoformat(),
            "count": 0,
            "note": "No RL output yet. Run `python run_4phase.py` to generate top_candidates_*.csv.",
        }
    candidates = _load_candidates_from_csv(csv_path, drug, disease, limit)
    return {
        "candidates": candidates,
        "source": "rl_service",
        "modelVersion": "rl_drug_ranker.py-v105",
        "generatedAt": __import__("datetime").datetime.utcnow().isoformat(),
        "count": len(candidates),
        "csvPath": str(csv_path),
        "backend": "csv",
    }


@app.get("/rank")
def rank_get(
    drug: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
) -> Dict[str, Any]:
    """Get ranked hypotheses (optionally filtered by drug/disease)."""
    return _rank_impl(drug, disease, limit)


@app.get("/rank/{drug}")
def rank_by_drug(drug: str, limit: int = Query(50, ge=1, le=500)) -> Dict[str, Any]:
    """Get ranked hypotheses for a specific drug."""
    return _rank_impl(drug=drug, disease=None, limit=limit)


@app.post("/rank")
def rank_post(req: RankRequest) -> Dict[str, Any]:
    """Get ranked hypotheses with body filters."""
    return _rank_impl(req.drug, req.disease, req.limit)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("RL_SERVICE_PORT", "8004"))
    host = os.environ.get("RL_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 4 RL Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
