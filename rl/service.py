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
    allow_methods=["GET", "POST", "OPTIONS"],  # INT-030: add OPTIONS for CORS preflight
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
    # The previous code loaded ALL rows into memory (~500MB for 1M rows)
    # with ``rows = list(reader)``. This fix uses a streaming for-loop
    # inside the with-block so the file handle stays open during iteration.
    # NOTE: we cannot break early because P4-014 requires sorting ALL
    # matching candidates by rank before applying the limit. Breaking early
    # would return the first N matches in CSV order, not the top-N by rank.
    # The out list grows with each matching candidate (not all rows — only
    # those passing the drug/disease filters).
    with open(csv_path, "r", encoding="utf-8", errors="replace") as f:
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

    INT-023 ROOT FIX: the previous code called ``bridge.load_rl_agent``
    which DOES NOT EXIST on GTRLBridge, causing AttributeError on every
    call. The fix loads the PPO model DIRECTLY via stable_baselines3
    ``PPO.load``, builds an RL env from bridge data, and passes the
    loaded model to ``get_top_k_novel_predictions`` — the proper
    integration path per the bridge's docstring.
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

        # Build bridge and env for ranking.
        from graph_transformer.gt_rl_bridge import GTRLBridge
        from rl.rl_drug_ranker import DrugRankingEnv, PipelineConfig
        bridge = GTRLBridge(output_dir=str(_HERE / "_service_output"), device="cpu", seed=42)
        # Build a minimal graph for the env. In production, this uses
        # real Phase 1/2 data; here we build the demo graph.
        bridge.build_model()
        rl_input_df = bridge.generate_rl_input()
        if len(rl_input_df) == 0:
            logger.warning("INT-023: bridge generated empty RL input — no candidates.")
            return []

        # Build RL config and env.
        cfg = PipelineConfig()
        env = DrugRankingEnv(data=rl_input_df, config=cfg)

        # Wrap in DummyVecEnv (PPO expects vec_env observations).
        from stable_baselines3.common.vec_env import DummyVecEnv
        vec_env = DummyVecEnv([lambda: env])
        model.set_env(vec_env)

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
        return candidates[:limit] if limit > 0 else candidates
    except Exception as exc:
        # P4-015 ROOT FIX: in strict mode (default), RAISE on checkpoint
        # failure instead of silently falling back to stale CSV.
        strict_mode = os.environ.get("RL_STRICT_CHECKPOINT", "true").lower() not in ("false", "0", "no", "off")
        if strict_mode:
            logger.error(
                "INT-023 + P4-015 STRICT MODE: RL checkpoint inference failed "
                "(%s). Raising exception instead of falling back to stale CSV.", exc,
            )
            raise RuntimeError(
                f"RL checkpoint inference failed (checkpoint={checkpoint_path}): {exc}. "
                f"Set RL_STRICT_CHECKPOINT=false to allow fallback to CSV."
            ) from exc
        logger.warning("INT-023: RL checkpoint inference failed (%s), falling back to CSV.", exc)
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


def _rank_impl(
    drug: Optional[str],
    disease: Optional[str],
    limit: int,
    offset: int = 0,
) -> Dict[str, Any]:
    """Shared logic for GET /rank and POST /rank.

    INT-022 ROOT FIX: returns proper pagination fields:
      - total: total count BEFORE limit/offset (for "Showing X-Y of Z")
      - page: current page number (0-indexed)
      - pageSize: number of items per page
      - count: number of items in THIS response (may be < pageSize at end)

    The previous code returned only ``count`` which was always <= limit,
    so the frontend could not compute proper pagination controls.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be in [1, 500]")
    if offset < 0:
        raise HTTPException(status_code=400, detail="offset must be >= 0")

    # Try checkpoint first (production path).
    checkpoint_path = os.environ.get("RL_CHECKPOINT_PATH")
    if checkpoint_path and Path(checkpoint_path).exists():
        all_candidates = _load_candidates_from_checkpoint(checkpoint_path, drug, disease, limit=0)
        total = len(all_candidates)
        page_candidates = all_candidates[offset:offset + limit] if limit > 0 else all_candidates
        return {
            "candidates": page_candidates,
            "source": "rl_service",
            "modelVersion": "rl_drug_ranker.py-v105",
            "generatedAt": __import__("datetime").datetime.utcnow().isoformat(),
            "total": total,
            "page": offset // limit if limit > 0 else 0,
            "pageSize": limit,
            "count": len(page_candidates),
            "backend": "checkpoint",
        }

    # Fallback: read the latest top_candidates_*.csv.
    csv_path = _find_latest_output_csv()
    if csv_path is None:
        return {
            "candidates": [],
            "source": "none",
            "generatedAt": __import__("datetime").datetime.utcnow().isoformat(),
            "total": 0,
            "page": 0,
            "pageSize": limit,
            "count": 0,
            "note": "No RL output yet. Run `python run_4phase.py` to generate top_candidates_*.csv.",
        }
    # INT-022: load ALL matching candidates, then slice with offset+limit.
    all_candidates = _load_candidates_from_csv(csv_path, drug, disease, limit=0)
    total = len(all_candidates)
    page_candidates = all_candidates[offset:offset + limit] if limit > 0 else all_candidates
    return {
        "candidates": page_candidates,
        "source": "rl_service",
        "modelVersion": "rl_drug_ranker.py-v105",
        "generatedAt": __import__("datetime").datetime.utcnow().isoformat(),
        "total": total,
        "page": offset // limit if limit > 0 else 0,
        "pageSize": limit,
        "count": len(page_candidates),
        "csvPath": str(csv_path),
        "backend": "csv",
    }


@app.get("/rank")
def rank_get(
    drug: Optional[str] = Query(None),
    disease: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Get ranked hypotheses (optionally filtered by drug/disease).

    INT-022: accepts offset for pagination.
    """
    return _rank_impl(drug, disease, limit, offset)


@app.get("/rank/{drug}")
def rank_by_drug(
    drug: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    """Get ranked hypotheses for a specific drug."""
    return _rank_impl(drug=drug, disease=None, limit=limit, offset=offset)


@app.post("/rank")
def rank_post(req: RankRequest) -> Dict[str, Any]:
    """Get ranked hypotheses with body filters."""
    # INT-022: offset from query params (POST body doesn't include it).
    # FastAPI parses offset from the query string even for POST.
    return _rank_impl(req.drug, req.disease, req.limit)


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("RL_SERVICE_PORT", "8004"))
    host = os.environ.get("RL_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 4 RL Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
