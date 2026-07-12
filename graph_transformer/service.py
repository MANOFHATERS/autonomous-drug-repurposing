#!/usr/bin/env python3
"""Phase 3 Graph Transformer Service (Step 1 integration plan, v105).

Wraps Phase 3's GT model inference as an HTTP service so the Next.js
frontend can proxy to it via GT_SERVICE_URL. The frontend's new
``/api/predict`` route (added in Step 2) proxies to this service.

Endpoints:
    GET  /health                -> {status: "ok", service: "phase3_gt", ...}
    POST /predict               -> {scores: [{drug, disease, score, confidence}, ...]}
    GET  /top-k?k=10            -> {candidates: [{drug, disease, score}, ...]}

Run:
    cd graph_transformer && python service.py
    # or: uvicorn graph_transformer.service:app --host 0.0.0.0 --port 8003

Environment:
    GT_CHECKPOINT_PATH: Path to the trained GT checkpoint (.pt file).
        If unset, the service builds the demo graph and trains a tiny
        model in-memory so it can still answer /predict in dev/CI.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make graph_transformer + repo root importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# Phase 1/2 imports needed for the demo-graph fallback.
_PHASE1 = _REPO_ROOT / "phase1"
_PHASE2 = _REPO_ROOT / "phase2"
for _p in (str(_PHASE1), str(_PHASE2)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger("graph_transformer.service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 3 Graph Transformer Service",
    description="HTTP wrapper around Phase 3 GT model inference.",
    version="1.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


class PredictRequest(BaseModel):
    """Request body for /predict.

    Either provide an explicit list of pairs, or leave pairs empty to
    score all drug-disease pairs in the graph.
    """
    pairs: Optional[List[Dict[str, str]]] = None  # [{drug, disease}, ...]
    drug: Optional[str] = None  # score this drug against ALL diseases
    disease: Optional[str] = None  # score this disease against ALL drugs
    limit: int = 50


# Global model state -- loaded once at startup, reused across requests.
_MODEL_STATE: Dict[str, Any] = {}


def _load_or_build_model() -> Dict[str, Any]:
    """Load the trained GT checkpoint, or build+train a tiny demo model.

    This function is idempotent -- it caches the result in _MODEL_STATE.
    """
    if _MODEL_STATE:
        return _MODEL_STATE

    checkpoint_path = os.environ.get("GT_CHECKPOINT_PATH")
    if checkpoint_path and Path(checkpoint_path).exists():
        try:
            return _load_checkpoint(checkpoint_path)
        except Exception as exc:
            logger.warning("GT checkpoint load failed (%s), falling back to demo model.", exc)

    return _build_demo_model()


def _load_checkpoint(checkpoint_path: str) -> Dict[str, Any]:
    """Load a trained GT checkpoint + rebuild the graph it was trained on."""
    import torch
    from graph_transformer.models.graph_transformer import GraphTransformerModel
    from graph_transformer.models.link_predictor import LinkPredictor

    # The checkpoint bundle includes the model state dicts + node_maps.
    bundle = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model_state = bundle.get("model_state_dict") or bundle.get("model")
    link_state = bundle.get("link_predictor_state_dict") or bundle.get("link_predictor")
    node_maps = bundle.get("node_maps", {})
    embedding_dim = bundle.get("embedding_dim", 64)
    hidden_dims = bundle.get("link_predictor_hidden_dims", [64, 32])

    model = GraphTransformerModel(embedding_dim=embedding_dim)
    model.load_state_dict(model_state)
    link_predictor = LinkPredictor(embedding_dim=embedding_dim, hidden_dims=hidden_dims)
    if link_state:
        link_predictor.load_state_dict(link_state)
    model.eval()
    link_predictor.eval()

    _MODEL_STATE.update({
        "model": model,
        "link_predictor": link_predictor,
        "node_maps": node_maps,
        "embedding_dim": embedding_dim,
        "backend": "checkpoint",
        "checkpoint_path": checkpoint_path,
    })
    return _MODEL_STATE


def _build_demo_model() -> Dict[str, Any]:
    """Build the demo graph + train a tiny GT model in-memory (dev fallback)."""
    try:
        from graph_transformer.gt_rl_bridge import GTRLBridge
        bridge = GTRLBridge(output_dir=str(_HERE / "_service_demo_output"), device="cpu", seed=42)
        # Train for very few epochs -- this is just for dev/CI to have
        # *some* predictions. Production uses a real checkpoint.
        bridge.run_full_pipeline(
            gt_epochs=10,
            rl_timesteps=0,  # skip RL for the demo model
            rl_top_n=0,
            allow_invalid_output=True,  # dev-only
        )
        _MODEL_STATE.update({
            "model": getattr(bridge, "model", None),
            "link_predictor": getattr(getattr(bridge, "model", None), "link_predictor", None),
            "node_maps": getattr(bridge, "node_maps", {}),
            "drug_names": getattr(bridge, "drug_names", []),
            "disease_names": getattr(bridge, "disease_names", []),
            "embedding_dim": 64,
            "backend": "demo_model",
        })
        return _MODEL_STATE
    except Exception as exc:
        logger.error("GT demo model build failed: %s", exc, exc_info=True)
        _MODEL_STATE.update({"backend": "error", "error": str(exc)})
        return _MODEL_STATE


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "phase3_gt",
        "version": "1.0.0",
        "checkpoint_configured": bool(os.environ.get("GT_CHECKPOINT_PATH")),
    }


@app.post("/predict")
def predict(req: PredictRequest) -> Dict[str, Any]:
    """Score drug-disease pairs with the GT model."""
    state = _load_or_build_model()
    if state.get("backend") == "error":
        raise HTTPException(status_code=503, detail=f"GT model unavailable: {state.get('error')}")

    model = state.get("model")
    link_predictor = state.get("link_predictor")
    node_maps = state.get("node_maps", {})
    if model is None or link_predictor is None:
        raise HTTPException(status_code=503, detail="GT model not loaded.")

    import torch

    # Determine the pairs to score.
    pairs: List[Dict[str, str]] = []
    if req.pairs:
        pairs = req.pairs
    elif req.drug:
        # Score this drug against all diseases in the graph.
        drug_map = node_maps.get("drug", {})
        disease_map = node_maps.get("disease", {})
        if req.drug not in drug_map:
            raise HTTPException(status_code=404, detail=f"Drug '{req.drug}' not in graph.")
        pairs = [{"drug": req.drug, "disease": d} for d in disease_map.keys()]
    elif req.disease:
        drug_map = node_maps.get("drug", {})
        disease_map = node_maps.get("disease", {})
        if req.disease not in disease_map:
            raise HTTPException(status_code=404, detail=f"Disease '{req.disease}' not in graph.")
        pairs = [{"drug": d, "disease": req.disease} for d in drug_map.keys()]
    else:
        raise HTTPException(status_code=400, detail="Provide pairs, drug, or disease.")

    # Score each pair.
    scores: List[Dict[str, Any]] = []
    drug_map = node_maps.get("drug", {})
    disease_map = node_maps.get("disease", {})
    with torch.no_grad():
        # Encode all nodes once.
        # The model's encode() returns per-type embeddings.
        try:
            embeddings = model.encode() if hasattr(model, "encode") else None
        except Exception:
            embeddings = None

        for pair in pairs[: req.limit]:
            drug = pair.get("drug", "")
            disease = pair.get("disease", "")
            if drug not in drug_map or disease not in disease_map:
                scores.append({
                    "drug": drug, "disease": disease,
                    "score": 0.0, "confidence": 0.0,
                    "note": "drug or disease not in graph",
                })
                continue
            try:
                # Use the model's predict helper if available.
                if hasattr(model, "predict_probability"):
                    prob = float(model.predict_probability(drug, disease))
                elif hasattr(link_predictor, "predict_probability") and embeddings is not None:
                    drug_emb = embeddings["drug"][drug_map[drug]]
                    disease_emb = embeddings["disease"][disease_map[disease]]
                    prob = float(link_predictor.predict_probability(drug_emb.unsqueeze(0), disease_emb.unsqueeze(0)).item())
                else:
                    prob = 0.5
                scores.append({
                    "drug": drug,
                    "disease": disease,
                    "score": prob,
                    "confidence": min(1.0, prob * 2) if prob < 0.5 else min(1.0, (1 - prob) * 2),
                })
            except Exception as exc:
                scores.append({
                    "drug": drug, "disease": disease,
                    "score": 0.0, "confidence": 0.0,
                    "note": f"scoring error: {exc}",
                })

    return {
        "scores": scores,
        "backend": state.get("backend"),
        "model_version": "gt_v105",
        "count": len(scores),
    }


@app.get("/top-k")
def top_k(k: int = 10) -> Dict[str, Any]:
    """Return the top-k novel predictions from the GT model."""
    if k < 1 or k > 500:
        raise HTTPException(status_code=400, detail="k must be in [1, 500]")
    state = _load_or_build_model()
    if state.get("backend") == "error":
        raise HTTPException(status_code=503, detail=f"GT model unavailable: {state.get('error')}")

    # Try to use the bridge's top_k_novel_predictions if available.
    try:
        from graph_transformer.gt_rl_bridge import GTRLBridge
        # The cached bridge would be ideal, but we don't keep it cached.
        # Fall back to /predict over all pairs.
        pass
    except Exception:
        pass

    # Fall back: enumerate all pairs and score them.
    node_maps = state.get("node_maps", {})
    drug_map = node_maps.get("drug", {})
    disease_map = node_maps.get("disease", {})
    pairs = []
    for d in list(drug_map.keys())[:50]:  # cap to avoid explosion
        for dis in list(disease_map.keys())[:50]:
            pairs.append({"drug": d, "disease": dis})
    req = PredictRequest(pairs=pairs, limit=len(pairs))
    result = predict(req)
    scores = result.get("scores", [])
    scores.sort(key=lambda s: s.get("score", 0), reverse=True)
    return {
        "candidates": scores[:k],
        "backend": state.get("backend"),
        "count": min(k, len(scores)),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("GT_SERVICE_PORT", "8003"))
    host = os.environ.get("GT_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 3 GT Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
