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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def _load_candidates_from_csv(csv_path: Path, drug: Optional[str], disease: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """Parse the RL ranker's output CSV into the RankedHypothesis schema."""
    import csv as csv_mod

    out: List[Dict[str, Any]] = []
    if not csv_path.exists():
        return out

    def _num(v: Any) -> Optional[float]:
        if v is None or v == "":
            return None
        try:
            f = float(v)
            return f if f == f else None  # NaN check
        except (ValueError, TypeError):
            return None

    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv_mod.DictReader(f)
        rows = list(reader)

    for i, row in enumerate(rows):
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

        # Compute overall score if missing (same formula as frontend).
        overall: Optional[float] = None
        signals = []
        if gnn is not None: signals.append((gnn, 0.4))
        if safety is not None: signals.append((safety, 0.3))
        if market is not None: signals.append((market, 0.3))
        if signals:
            total_w = sum(w for _, w in signals)
            overall = sum(v * w for v, w in signals) / total_w
        elif policy_prob is not None:
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
        if len(out) >= limit:
            break

    # Sort by rank (or reward desc if no rank).
    out.sort(key=lambda c: (c.get("rank") or 1e9))
    return out


def _load_candidates_from_checkpoint(checkpoint_path: str, drug: Optional[str], disease: Optional[str], limit: int) -> List[Dict[str, Any]]:
    """Load the PPO checkpoint and run inference to produce rankings.

    This is the production path -- the checkpoint was trained on real
    bridge data (not standalone fake data), so its rankings are safe
    to ship.
    """
    try:
        # The bridge's GTRLBridge loads the checkpoint and runs inference.
        # We delegate to it.
        from graph_transformer.gt_rl_bridge import GTRLBridge
        bridge = GTRLBridge(output_dir=str(_HERE / "_service_output"), device="cpu", seed=42)
        # The bridge's load_rl_agent + rank_top_candidates methods.
        # We don't actually retrain -- just load + infer.
        if hasattr(bridge, "load_rl_agent"):
            bridge.load_rl_agent(checkpoint_path)
        if hasattr(bridge, "rank_top_candidates"):
            df = bridge.rank_top_candidates(top_n=limit)
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
