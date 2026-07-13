#!/usr/bin/env python3
"""Phase 3 Graph Transformer Service — HTTP wrapper around GT inference.

P3-001/P3-002/P3-003/P3-004/P3-012 ROOT FIX (forensic, root-level):

  P3-001 (MockData): the previous ``_build_demo_model()`` trained a tiny GT
    model in-memory on a synthetic demo graph and served its predictions as
    real. The checkpoint path was ALSO broken: ``model.encode()`` was called
    with NO arguments (requires ``node_features`` + ``edge_indices``),
    raising ``TypeError`` that was silently caught; ``model.predict_probability``
    was called with STRING names but the model has no such method; every pair
    fell to ``prob = 0.5``. FIX: load checkpoint + ``graph_state.pt`` (the
    same loading path ``scripts/gt_inference.py`` uses), pass real
    ``node_features`` + ``edge_indices`` to ``model.encode()``, index
    embeddings by node map, and call ``link_predictor.predict_probability(
    drug_emb, disease_emb)``. Remove the demo-model fallback entirely — if
    no checkpoint exists, return HTTP 503 with a clear message.

  P3-002 (ContractViolation/DeadCode): the frontend (``predict/route.ts``,
    ``top-k/route.ts``) spawns ``scripts/gt_inference.py`` via subprocess and
    expects ``{predictions, source, modelVersion, generatedAt, count,
    checkpointPath}``. The old ``service.py`` returned ``{scores, backend,
    model_version}`` — a DIFFERENT shape. FIX: align the response shape with
    the frontend contract so ``service.py`` is a drop-in HTTP replacement
    for the subprocess path. The frontend can now use ``GT_SERVICE_URL`` to
    proxy to this service for high-concurrency deployments (V1 contract:
    100 concurrent requests).

  P3-003 (Inverted confidence): the old formula
    ``min(1.0, prob * 2) if prob < 0.5 else min(1.0, (1 - prob) * 2)``
    returned 1.0 at prob=0.5 (least confident) and 0.0 at prob=0.0/1.0
    (most confident) — exactly backwards. FIX: ``1.0 - 2.0 * abs(prob - 0.5)``
    which is 0.0 at prob=0.5 (least confident) and 1.0 at prob=0.0/1.0
    (most confident).

  P3-004 (Security): CORS ``allow_origins=["*"]`` allowed ANY origin.
    ``torch.load(weights_only=False)`` allowed arbitrary code execution from
    a malicious checkpoint. FIX: ``allow_origins`` reads from
    ``GT_CORS_ORIGINS`` env var (comma-separated; defaults to localhost
    origins for dev). ``torch.load`` uses ``weights_only=True`` with feature
    detection for older PyTorch (the trainer does the same).

  P3-012 (SilentFailure): per-pair scoring errors were swallowed with no
    log/metric/alert. FIX: log at ERROR level, aggregate error counts,
    surface ``error_count`` + ``error_rate`` in the response, and return
    HTTP 500 if >10% of pairs fail.

Endpoints (response shape aligned with frontend contract):
    GET  /health     -> {status, service, version, checkpoint_configured, checkpoint_loaded}
    POST /predict    -> {predictions, source, modelVersion, generatedAt, count, checkpointPath, error_count, error_rate}
    GET  /top-k      -> {predictions, source, modelVersion, generatedAt, count, checkpointPath}

Run:
    cd graph_transformer && python service.py
    # or: uvicorn graph_transformer.service:app --host 0.0.0.0 --port 8003

Environment:
    GT_CHECKPOINT_PATH: Path to the trained GT checkpoint (.pt file).
        REQUIRED — if unset or missing, /predict and /top-k return 503.
    GT_CORS_ORIGINS: Comma-separated allowed origins (default:
        http://localhost:3000,http://localhost:3001).
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Make graph_transformer + repo root importable.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_REPO_ROOT = _HERE.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
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

# P3-004 ROOT FIX: explicit CORS origins from env, NOT wildcard.
_DEFAULT_CORS = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000"
_cors_env = os.environ.get("GT_CORS_ORIGINS", _DEFAULT_CORS)
ALLOWED_ORIGINS: List[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]
logger.info("CORS allowed origins: %s", ALLOWED_ORIGINS)

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 3 Graph Transformer Service",
    description="HTTP wrapper around Phase 3 GT model inference (response shape aligned with frontend).",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
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


def _torch_load_safe(path: str) -> Dict[str, Any]:
    """P3-004 ROOT FIX: load a checkpoint with weights_only=True.

    Uses feature detection for older PyTorch (<2.0 doesn't support
    weights_only). This prevents arbitrary code execution from a
    malicious checkpoint file.
    """
    import torch
    try:
        return torch.load(path, map_location="cpu", weights_only=True)
    except TypeError:
        # Older PyTorch doesn't have weights_only parameter — fall back
        # to default loading with a logged warning. This is the SAME
        # feature-detection pattern the trainer uses.
        logger.warning(
            "PyTorch <2.0 detected: torch.load(weights_only=True) not "
            "supported. Falling back to default load. UPGRADE PyTorch to "
            ">=2.0 for full checkpoint security."
        )
        return torch.load(path, map_location="cpu")


def _load_or_build_model() -> Dict[str, Any]:
    """Load the trained GT checkpoint + graph state.

    P3-001 ROOT FIX: NO demo-model fallback. If no checkpoint is
    configured or the checkpoint cannot be loaded, return an error state.
    The frontend route (gt-inference.ts) already returns ``source: "none"``
    when no checkpoint exists — this service must do the SAME, not serve
    fake predictions from a demo model.
    """
    if _MODEL_STATE:
        return _MODEL_STATE

    checkpoint_path = os.environ.get("GT_CHECKPOINT_PATH")
    if not checkpoint_path or not Path(checkpoint_path).exists():
        _MODEL_STATE.update({
            "backend": "no_checkpoint",
            "error": f"GT_CHECKPOINT_PATH not set or file not found: {checkpoint_path}",
        })
        return _MODEL_STATE

    try:
        return _load_checkpoint(checkpoint_path)
    except Exception as exc:
        logger.error("GT checkpoint load failed: %s", exc, exc_info=True)
        _MODEL_STATE.update({"backend": "error", "error": str(exc)})
        return _MODEL_STATE


def _load_checkpoint(checkpoint_path: str) -> Dict[str, Any]:
    """Load a trained GT checkpoint + graph_state.pt.

    P3-001 ROOT FIX: this mirrors the loading path in
    ``scripts/gt_inference.py`` so the HTTP service and the subprocess
    path produce IDENTICAL predictions. The checkpoint bundle includes
    the model state dict; ``graph_state.pt`` (written alongside by the
    bridge) includes ``node_features``, ``edge_indices``, ``node_maps``,
    ``drug_names``, ``disease_names``, and ``known_pairs``.
    """
    import torch
    from graph_transformer.models.graph_transformer import (
        DrugRepurposingGraphTransformer,
    )

    ckpt_path = Path(checkpoint_path)
    if not ckpt_path.exists():
        raise FileNotFoundError(f"GT checkpoint not found: {ckpt_path}")

    # Look for graph_state.pt in the same directory
    graph_state_path = ckpt_path.parent / "graph_state.pt"
    if not graph_state_path.exists():
        candidates = list(ckpt_path.parent.glob("*graph_state*.pt")) + \
                     list(ckpt_path.parent.glob("*graph*.pt"))
        candidates = [c for c in candidates if c != ckpt_path]
        if not candidates:
            raise FileNotFoundError(
                f"Graph state file not found next to checkpoint {ckpt_path}. "
                f"Expected: {graph_state_path}. The bridge must write this "
                f"file alongside the model checkpoint so inference can "
                f"reproduce the exact graph topology the model was trained on."
            )
        graph_state_path = candidates[0]

    # P3-004 ROOT FIX: weights_only=True (with feature detection)
    ckpt = _torch_load_safe(str(ckpt_path))
    graph_state = _torch_load_safe(str(graph_state_path))

    # Reconstruct model from saved config
    model_config = ckpt.get("model_config", graph_state.get("model_config", {}))
    node_features_dims = graph_state.get("node_features_dims") or graph_state.get(
        "feature_dims", {}
    )
    model = DrugRepurposingGraphTransformer(
        node_features_dims=node_features_dims,
        embedding_dim=model_config.get("embedding_dim", 32),
        num_layers=model_config.get("num_layers", 3),
        num_heads=model_config.get("num_heads", 2),
        dropout=model_config.get("dropout", 0.2),
        attention_dropout=model_config.get("attention_dropout", 0.2),
        link_predictor_hidden_dims=model_config.get("link_predictor_hidden_dims", [64, 32]),
    )
    model_state_dict = ckpt.get("model_state_dict", ckpt.get("model", ckpt))
    model.load_state_dict(model_state_dict)
    model.eval()

    _MODEL_STATE.update({
        "model": model,
        "node_features": graph_state["node_features"],
        "edge_indices": graph_state["edge_indices"],
        "node_maps": graph_state["node_maps"],
        "drug_names": graph_state["drug_names"],
        "disease_names": graph_state["disease_names"],
        "known_pairs": graph_state.get("known_pairs", []),
        "embedding_dim": model_config.get("embedding_dim", 32),
        "backend": "checkpoint",
        "checkpoint_path": checkpoint_path,
    })
    logger.info("GT checkpoint loaded from %s", checkpoint_path)
    return _MODEL_STATE


def _compute_confidence(prob: float) -> float:
    """P3-003 ROOT FIX: correct confidence formula.

    The old formula ``min(1.0, prob * 2) if prob < 0.5 else min(1.0, (1 - prob) * 2)``
    was INVERTED — it returned 1.0 at prob=0.5 (least confident) and 0.0 at
    prob=0.0/1.0 (most confident).

    NOTE: the audit's suggested fix ``1.0 - 2.0 * abs(prob - 0.5)`` is
    MATHEMATICALLY IDENTICAL to the old formula (both return 1.0 at prob=0.5
    and 0.0 at prob=0.0/1.0). The audit itself had a math error.

    The CORRECT formula is ``2.0 * abs(prob - 0.5)``:
      - prob=0.5 (least confident) -> confidence=0.0  (model is unsure)
      - prob=0.0 or 1.0 (most confident) -> confidence=1.0  (model is sure)
      - prob=0.7 -> confidence=0.4  (model leans yes, moderate confidence)

    This is the standard "confidence" interpretation: how far the prediction
    is from the decision boundary (0.5), normalized to [0, 1].
    """
    return max(0.0, min(1.0, 2.0 * abs(prob - 0.5)))


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "phase3_gt",
        "version": "2.0.0",
        "checkpoint_configured": bool(os.environ.get("GT_CHECKPOINT_PATH")),
        "checkpoint_loaded": _MODEL_STATE.get("backend") == "checkpoint",
    }


@app.post("/predict")
def predict(req: PredictRequest) -> Dict[str, Any]:
    """Score drug-disease pairs with the GT model.

    P3-002 ROOT FIX: response shape aligned with frontend contract
    (``{predictions, source, modelVersion, generatedAt, count,
    checkpointPath}``) so this service is a drop-in HTTP replacement
    for the subprocess path (``scripts/gt_inference.py``).
    """
    state = _load_or_build_model()
    if state.get("backend") in ("no_checkpoint", "error"):
        raise HTTPException(
            status_code=503,
            detail=f"GT model unavailable: {state.get('error')}",
        )

    model = state["model"]
    node_features = state["node_features"]
    edge_indices = state["edge_indices"]
    node_maps = state["node_maps"]
    drug_names = state["drug_names"]
    disease_names = state["disease_names"]

    import torch
    from graph_transformer.data import LABEL_LEAKING_EDGES

    # Build name -> index lookup (case-insensitive)
    drug_to_idx = {n.lower(): i for i, n in enumerate(drug_names)}
    disease_to_idx = {n.lower(): i for i, n in enumerate(disease_names)}

    # Determine the pairs to score
    pairs: List[Dict[str, str]] = []
    if req.pairs:
        pairs = req.pairs
    elif req.drug:
        if req.drug.lower() not in drug_to_idx:
            raise HTTPException(status_code=404, detail=f"Drug '{req.drug}' not in graph.")
        pairs = [{"drug": req.drug, "disease": d} for d in disease_names]
    elif req.disease:
        if req.disease.lower() not in disease_to_idx:
            raise HTTPException(status_code=404, detail=f"Disease '{req.disease}' not in graph.")
        pairs = [{"drug": d, "disease": req.disease} for d in drug_names]
    else:
        raise HTTPException(status_code=400, detail="Provide pairs, drug, or disease.")

    pairs = pairs[: req.limit]

    # P3-001 ROOT FIX: encode the graph ONCE with real node_features +
    # edge_indices (not model.encode() with no args). Then index embeddings
    # by node map and call link_predictor.predict_probability(drug_emb,
    # disease_emb) — the CORRECT inference path.
    drug_map = node_maps.get("drug", {})
    disease_map = node_maps.get("disease", {})

    predictions: List[Dict[str, Any]] = []
    error_count = 0

    prior_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            # Encode the graph ONCE (exclude label-leaking edges)
            embeddings = model.encode(
                node_features, edge_indices,
                exclude_edges_override=set(LABEL_LEAKING_EDGES),
            )
            drug_emb_all = embeddings["drug"]
            disease_emb_all = embeddings["disease"]

            for pair in pairs:
                drug = pair.get("drug", "")
                disease = pair.get("disease", "")
                d_idx = drug_to_idx.get(drug.lower())
                ds_idx = disease_to_idx.get(disease.lower())

                if d_idx is None or ds_idx is None:
                    # P3-012 ROOT FIX: log at ERROR level (not swallow silently)
                    logger.error(
                        "Predict: drug='%s' or disease='%s' not in graph "
                        "(d_idx=%s, ds_idx=%s)", drug, disease, d_idx, ds_idx,
                    )
                    error_count += 1
                    predictions.append({
                        "drug": drug, "disease": disease,
                        "score": 0.0, "confidence": 0.0,
                        "note": "drug or disease not in graph",
                    })
                    continue

                try:
                    drug_emb = drug_emb_all[d_idx].unsqueeze(0)
                    disease_emb = disease_emb_all[ds_idx].unsqueeze(0)
                    prob = float(
                        model.link_predictor.predict_probability(
                            drug_emb, disease_emb, apply_temperature=True,
                        ).item()
                    )
                    predictions.append({
                        "drug": drug,
                        "disease": disease,
                        "score": prob,
                        "confidence": _compute_confidence(prob),
                    })
                except Exception as exc:
                    # P3-012 ROOT FIX: log at ERROR level, count errors
                    logger.error(
                        "Predict: scoring error for (%s, %s): %s",
                        drug, disease, exc, exc_info=True,
                    )
                    error_count += 1
                    predictions.append({
                        "drug": drug, "disease": disease,
                        "score": 0.0, "confidence": 0.0,
                        "note": f"scoring error: {exc}",
                    })
    finally:
        # P3-017 ROOT FIX: restore prior training mode (consistent with
        # predict_drug_disease_scores and predict_all_pairs)
        model.train(prior_training)

    # P3-012 ROOT FIX: surface error count + rate. If >10% of pairs failed,
    # return HTTP 500 (the model is not serving real predictions).
    total = len(predictions)
    error_rate = (error_count / total) if total > 0 else 0.0
    if total > 0 and error_rate > 0.10:
        raise HTTPException(
            status_code=500,
            detail=(
                f"GT scoring failed for {error_count}/{total} pairs "
                f"({error_rate:.1%}). Model may be misconfigured. "
                f"Check logs for details."
            ),
        )

    # P3-002 ROOT FIX: aligned response shape
    return {
        "predictions": predictions,
        "source": "gt_checkpoint",
        "modelVersion": "gt_v110",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(predictions),
        "checkpointPath": state.get("checkpoint_path"),
        "error_count": error_count,
        "error_rate": round(error_rate, 4),
    }


@app.get("/top-k")
def top_k(k: int = 10) -> Dict[str, Any]:
    """Return the top-k novel predictions from the GT model.

    P3-002 ROOT FIX: response shape aligned with frontend contract.
    Uses ``top_k_novel_predictions`` from ``graph_transformer.inference``
    (the SAME function the subprocess path uses) so HTTP and subprocess
    produce identical results.
    """
    if k < 1 or k > 500:
        raise HTTPException(status_code=400, detail="k must be in [1, 500]")
    state = _load_or_build_model()
    if state.get("backend") in ("no_checkpoint", "error"):
        raise HTTPException(
            status_code=503,
            detail=f"GT model unavailable: {state.get('error')}",
        )

    from graph_transformer.inference import top_k_novel_predictions

    model = state["model"]
    node_features = state["node_features"]
    edge_indices = state["edge_indices"]
    drug_names = state["drug_names"]
    disease_names = state["disease_names"]
    known_pairs = state["known_pairs"]

    prior_training = model.training
    model.eval()
    try:
        raw = top_k_novel_predictions(
            model=model,
            node_features=node_features,
            edge_indices=edge_indices,
            drug_names=drug_names,
            disease_names=disease_names,
            known_pairs=known_pairs,
            top_k=k,
            device="cpu",
        )
    finally:
        model.train(prior_training)

    predictions = [
        {"drug": d, "disease": v, "score": float(s)}
        for (d, v, s) in raw
    ]

    return {
        "predictions": predictions,
        "source": "gt_checkpoint",
        "modelVersion": "gt_v110",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(predictions),
        "checkpointPath": state.get("checkpoint_path"),
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("GT_SERVICE_PORT", "8003"))
    host = os.environ.get("GT_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 3 GT Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
