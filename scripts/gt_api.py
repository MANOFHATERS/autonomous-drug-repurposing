#!/usr/bin/env python3
"""Phase 3 Graph Transformer HTTP API (FastAPI service).

Task 356 ROOT FIX: ``docker-compose.yml`` referenced
``scripts.gt_api:app`` but the file did not exist, so the
``phase3-trainer`` container could not start. ``uvicorn`` would emit
``ModuleNotFoundError: No module named 'scripts.gt_api'`` and crash on
boot, leaving the frontend's ``/api/predict`` and ``/api/top-k`` routes
dead in production.

This module wraps the existing ``scripts.gt_inference`` CLI helper into
a FastAPI service so the frontend can call it via HTTP instead of
spawning a Python subprocess per request (RT-006 design goal).

Endpoints
---------
    GET  /healthz
        Container healthcheck. Returns ``{"status":"ok"}``.

    GET  /health
        Rich metadata (model path, device, version).

    POST /predict
        Predict interaction scores for an explicit list of drug-disease
        pairs. Body: ``{"pairs": [{"drug":"aspirin","disease":"migraine"}]}``.
        Returns: ``{"predictions":[{"drug":...,"disease":...,"score":0.87}]}``.

    POST /top-k
        Rank the top-K drug-disease pairs by predicted score. Body:
        ``{"top_k": 50, "drug_filter": null}``. Returns the K highest-
        scoring pairs the model has not seen during training.

Environment
-----------
    GT_CHECKPOINT_DIR: directory containing ``best_model.pt`` and
        ``graph_state.pt`` (written by ``run_4phase.py``). Defaults to
        ``./output_v100/checkpoints``.
    GT_DEVICE: ``cpu`` or ``cuda``. Defaults to ``cpu`` (Docker image
        ships CPU-only PyTorch per task 368).

Implementation notes
--------------------
The model + graph_state are loaded ONCE at startup (``_load_model``),
then cached on the ``app.state`` object. Re-loading a PyTorch checkpoint
on every request would add ~2s latency per call and exhaust GPU memory.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make repo root importable so ``from graph_transformer...`` works
# regardless of where uvicorn set cwd inside the container.
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "phase1"), str(_REPO_ROOT / "phase2")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

logger = logging.getLogger("scripts.gt_api")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 3 GT Service",
    description="HTTP wrapper around the Graph Transformer inference engine.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("GT_CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ─────────────────────── Request / response schemas ─────────────────────


class DrugDiseasePair(BaseModel):
    drug: str = Field(..., description="Drug name or ChEMBL ID")
    disease: str = Field(..., description="Disease name or MONDO ID")


class PredictRequest(BaseModel):
    pairs: List[DrugDiseasePair] = Field(..., min_length=1, max_length=500)


class Prediction(BaseModel):
    drug: str
    disease: str
    score: float


class PredictResponse(BaseModel):
    predictions: List[Prediction]
    model_version: str
    n_pairs: int


class TopKRequest(BaseModel):
    top_k: int = Field(10, ge=1, le=500)
    drug_filter: Optional[List[str]] = None


class TopKResponse(BaseModel):
    predictions: List[Prediction]
    model_version: str


# ─────────────────────── Model cache (loaded once) ──────────────────────


_model_lock = threading.Lock()


def _resolve_checkpoint_dir() -> Path:
    """Find the directory containing ``best_model.pt`` + ``graph_state.pt``."""
    env_dir = os.environ.get("GT_CHECKPOINT_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir():
            return p
    # Default: <repo>/output_v100/checkpoints
    candidates = [
        _REPO_ROOT / "output_v100" / "checkpoints",
        _REPO_ROOT / "output_v100",
        Path("/opt/ml_artifacts/checkpoints"),
        Path("/opt/ml_artifacts"),
    ]
    for c in candidates:
        if c.is_dir() and (c / "best_model.pt").exists():
            return c
    return candidates[0] if candidates else _REPO_ROOT


def _load_model() -> Dict[str, Any]:
    """Lazily load the GT model + graph_state. Cached on app.state."""
    if getattr(app.state, "gt_model", None) is not None:
        return app.state.gt_model

    with _model_lock:
        if getattr(app.state, "gt_model", None) is not None:
            return app.state.gt_model

        ckpt_dir = _resolve_checkpoint_dir()
        ckpt_path = ckpt_dir / "best_model.pt"
        if not ckpt_path.exists():
            app.state.gt_model = None
            app.state.gt_error = f"checkpoint not found: {ckpt_path}"
            logger.warning(app.state.gt_error)
            return app.state.gt_model or {}

        # Delegate the heavy lifting to the existing gt_inference helper.
        # Re-using scripts.gt_inference avoids duplicating the model
        # reconstruction logic (which handles model_config, graph_state,
        # known_pairs, etc.).
        try:
            import scripts.gt_inference as gti  # type: ignore
            # The helper exposes _load_checkpoint(checkpoint_path) which
            # returns (model, node_features, edge_indices, node_maps,
            # drug_names, disease_names, known_pairs).
            loaded = gti._load_checkpoint(str(ckpt_path))
            app.state.gt_model = {
                "model": loaded[0],
                "node_features": loaded[1],
                "edge_indices": loaded[2],
                "node_maps": loaded[3],
                "drug_names": loaded[4],
                "disease_names": loaded[5],
                "known_pairs": loaded[6],
                "checkpoint_path": str(ckpt_path),
            }
            app.state.gt_error = None
            logger.info("GT model loaded from %s", ckpt_path)
        except Exception as exc:  # noqa: BLE001
            app.state.gt_model = None
            app.state.gt_error = f"{type(exc).__name__}: {exc}"
            logger.exception("Failed to load GT model")

        return app.state.gt_model or {}


# ─────────────────────── Endpoints ──────────────────────────────────────


@app.get("/healthz", tags=["health"])
def healthz() -> Dict[str, str]:
    """Docker-native healthcheck (tiny 200-OK body)."""
    return {"status": "ok", "service": "phase3-gt"}


@app.get("/health", tags=["health"])
def health() -> Dict[str, Any]:
    """Rich health: model path, load status, device."""
    model = _load_model()
    return {
        "status": "ok" if model else "degraded",
        "service": "phase3-gt",
        "model_loaded": bool(model),
        "checkpoint": model.get("checkpoint_path") if model else None,
        "error": getattr(app.state, "gt_error", None),
        "device": os.environ.get("GT_DEVICE", "cpu"),
    }


@app.post("/predict", response_model=PredictResponse, tags=["inference"])
def predict(req: PredictRequest) -> PredictResponse:
    """Predict interaction scores for explicit drug-disease pairs."""
    model = _load_model()
    if not model:
        raise HTTPException(status_code=503, detail=getattr(app.state, "gt_error", "model not loaded"))

    try:
        import scripts.gt_inference as gti  # type: ignore
        pairs_dicts = [{"drug": p.drug, "disease": p.disease} for p in req.pairs]
        # gti._predict_pairs signature: (model, node_features, edge_indices,
        # node_maps, drug_names, disease_names, pairs) — all but `pairs`
        # come from the cached model dict.
        preds = gti._predict_pairs(
            model["model"],
            model["node_features"],
            model["edge_indices"],
            model["node_maps"],
            model["drug_names"],
            model["disease_names"],
            pairs_dicts,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("predict failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    return PredictResponse(
        predictions=[Prediction(**p) for p in preds],
        model_version=os.environ.get("GT_MODEL_VERSION", "v100"),
        n_pairs=len(preds),
    )


@app.post("/top-k", response_model=TopKResponse, tags=["inference"])
def top_k(req: TopKRequest) -> TopKResponse:
    """Return the top-K highest-scoring drug-disease pairs."""
    model = _load_model()
    if not model:
        raise HTTPException(status_code=503, detail=getattr(app.state, "gt_error", "model not loaded"))

    try:
        import scripts.gt_inference as gti  # type: ignore
        # gti._top_k_novel signature: (model, node_features, edge_indices,
        # drug_names, disease_names, known_pairs, top_k).
        preds = gti._top_k_novel(
            model["model"],
            model["node_features"],
            model["edge_indices"],
            model["drug_names"],
            model["disease_names"],
            model["known_pairs"],
            req.top_k,
        )
        # Apply optional drug filter (case-insensitive) — the underlying
        # helper does not natively filter, so we slice after ranking.
        if req.drug_filter:
            allowed = {d.lower() for d in req.drug_filter}
            preds = [p for p in preds if p["drug"].lower() in allowed][: req.top_k]
    except Exception as exc:  # noqa: BLE001
        logger.exception("top_k failed")
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    return TopKResponse(
        predictions=[Prediction(**p) for p in preds],
        model_version=os.environ.get("GT_MODEL_VERSION", "v100"),
    )


@app.on_event("startup")
def _startup() -> None:
    """Pre-warm the model cache so the first request is not slow."""
    _load_model()


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("GT_SERVICE_PORT", "8002"))
    uvicorn.run(
        "scripts.gt_api:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("GT_LOG_LEVEL", "info"),
    )
