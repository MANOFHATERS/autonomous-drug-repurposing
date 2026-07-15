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
import threading
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
# P3-042 ROOT FIX (v107): lock around _MODEL_STATE check-and-build.
# The previous code checked ``if _MODEL_STATE: return _MODEL_STATE``
# without a lock. Under concurrent load (V1 contract: 100 concurrent
# requests), two requests could both see _MODEL_STATE as empty, both
# load the checkpoint, both write to _MODEL_STATE. The lock serializes
# the check-and-load so exactly ONE load happens.
_MODEL_STATE_LOCK = threading.Lock()


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

    P3-042 ROOT FIX (v107): the check-and-load is now serialized by
    ``_MODEL_STATE_LOCK`` to prevent concurrent duplicate loads.
    """
    with _MODEL_STATE_LOCK:
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
    path produce IDENTICAL predictions.

    FORENSIC ROOT FIX (audit Issue 124): the model class is now
    dispatched via the checkpoint's ``model_class_name`` field. The
    previous code ALWAYS constructed ``DrugRepurposingGraphTransformer``
    regardless of what class the trainer actually saved. If a future
    model variant is trained (e.g., a HGT-class model), the service
    would silently construct the WRONG class and crash on
    ``load_state_dict`` with a shape-mismatch error. The fix looks up
    the class by qualified name (defaulting to
    ``DrugRepurposingGraphTransformer`` for pre-fix checkpoints) and
    instantiates it with the saved ``hyperparams``.

    FORENSIC ROOT FIX (audit Issue 139): the checkpoint is now
    SELF-CONTAINED -- it carries node_features, edge_indices, node_maps,
    drug_names, disease_names, known_pairs alongside the model_state_dict
    and hyperparams. We load EVERYTHING from the single .pt file. For
    backward compatibility with pre-fix checkpoints (which saved only
    the model_state_dict + schema), we fall back to the legacy
    ``graph_state.pt`` sidecar if the new fields are missing.
    """
    import torch

    ckpt_path = Path(checkpoint_path)
    if not ckpt_path.exists():
        raise FileNotFoundError(f"GT checkpoint not found: {ckpt_path}")

    # P3-004 ROOT FIX: weights_only=True (with feature detection)
    ckpt = _torch_load_safe(str(ckpt_path))

    # FORENSIC ROOT FIX (audit Issue 139): try to load graph data from
    # the SELF-CONTAINED checkpoint first. Fall back to the legacy
    # graph_state.pt sidecar for pre-fix checkpoints.
    node_features = ckpt.get("node_features")
    edge_indices = ckpt.get("edge_indices")
    node_maps = ckpt.get("node_maps")
    drug_names = ckpt.get("drug_names")
    disease_names = ckpt.get("disease_names")
    known_pairs = ckpt.get("known_pairs")
    hyperparams = ckpt.get("hyperparams")
    model_class_name = ckpt.get("model_class_name")

    used_sidecar = False
    if node_features is None or edge_indices is None:
        # Pre-fix checkpoint: load graph data from graph_state.pt sidecar.
        graph_state_path = ckpt_path.parent / "graph_state.pt"
        if not graph_state_path.exists():
            candidates = list(ckpt_path.parent.glob("*graph_state*.pt")) + \
                         list(ckpt_path.parent.glob("*graph*.pt"))
            candidates = [c for c in candidates if c != ckpt_path]
            if not candidates:
                raise FileNotFoundError(
                    f"Checkpoint {ckpt_path} is a PRE-FIX checkpoint "
                    f"(no embedded graph data) and no graph_state.pt "
                    f"sidecar was found next to it. Expected: "
                    f"{graph_state_path}. Either re-train with the "
                    f"forensic-fix trainer (which saves a self-contained "
                    f"checkpoint) or restore the missing sidecar. "
                    f"(audit Issue 139)"
                )
            graph_state_path = candidates[0]
        graph_state = _torch_load_safe(str(graph_state_path))
        node_features = node_features or graph_state.get("node_features")
        edge_indices = edge_indices or graph_state.get("edge_indices")
        node_maps = node_maps or graph_state.get("node_maps")
        drug_names = drug_names or graph_state.get("drug_names", [])
        disease_names = disease_names or graph_state.get("disease_names", [])
        known_pairs = known_pairs or graph_state.get("known_pairs", [])
        # Legacy checkpoints didn't save model_class_name or hyperparams;
        # rebuild hyperparams from graph_state.model_config (best effort).
        if hyperparams is None:
            legacy_cfg = graph_state.get("model_config", {})
            legacy_dims = graph_state.get("node_features_dims") or graph_state.get(
                "feature_dims", {}
            )
            hyperparams = {
                "feature_dims": legacy_dims,
                "embedding_dim": legacy_cfg.get("embedding_dim", 32),
                "num_layers": legacy_cfg.get("num_layers", 3),
                "num_heads": legacy_cfg.get("num_heads", 2),
                "dropout": legacy_cfg.get("dropout", 0.2),
                "attention_dropout": legacy_cfg.get("attention_dropout", 0.2),
                "link_predictor_hidden_dims": legacy_cfg.get(
                    "link_predictor_hidden_dims", [64, 32]
                ),
            }
        used_sidecar = True

    # FORENSIC ROOT FIX (audit Issue 124): dispatch the model class via
    # the saved ``model_class_name``. Defaults to
    # ``DrugRepurposingGraphTransformer`` for pre-fix checkpoints (which
    # don't save the class name).
    model = _construct_model_from_class_name(
        model_class_name, hyperparams
    )
    model_state_dict = ckpt.get("model_state_dict", ckpt.get("model", ckpt))
    model.load_state_dict(model_state_dict)
    model.eval()

    _MODEL_STATE.update({
        "model": model,
        "node_features": node_features,
        "edge_indices": edge_indices,
        "node_maps": node_maps or {},
        "drug_names": drug_names or [],
        "disease_names": disease_names or [],
        "known_pairs": known_pairs or [],
        "hyperparams": hyperparams or {},
        "model_class_name": model_class_name or type(model).__name__,
        "embedding_dim": (hyperparams or {}).get("embedding_dim", 32),
        "backend": "checkpoint",
        "checkpoint_path": checkpoint_path,
        "used_graph_state_sidecar": used_sidecar,
    })
    logger.info(
        "GT checkpoint loaded from %s (class=%s, sidecar=%s)",
        checkpoint_path,
        _MODEL_STATE["model_class_name"],
        used_sidecar,
    )
    return _MODEL_STATE


def _construct_model_from_class_name(
    model_class_name: Optional[str],
    hyperparams: Optional[Dict[str, Any]],
):
    """FORENSIC ROOT FIX (audit Issue 124): dispatch the model class.

    Looks up the class by its qualified name (e.g.,
    ``graph_transformer.models.graph_transformer.DrugRepurposingGraphTransformer``)
    and instantiates it with the saved ``hyperparams``. Falls back to
    ``DrugRepurposingGraphTransformer`` (the only production class today)
    if the class name is missing or unresolvable -- this preserves
    backward compatibility with pre-fix checkpoints.

    Raises a clear ``ValueError`` if the saved class name resolves to a
    class that is NOT a ``torch.nn.Module`` (catches corrupted or
    hostile checkpoints early).
    """
    import torch.nn as nn

    DEFAULT_CLASS = "graph_transformer.models.graph_transformer.DrugRepurposingGraphTransformer"
    target_name = model_class_name or DEFAULT_CLASS
    # Strip any module-alias prefixes that could collide with the import
    # system (e.g., "graph_transformer.models.graph_transformer.GraphTransformerModel"
    # is the V89 alias for the same class).
    cls = _resolve_class_by_name(target_name)
    if cls is None:
        # Fall back to the default class with a warning -- this preserves
        # backward compatibility. A future PR can make this strict once
        # all production checkpoints carry the correct class name.
        logger.warning(
            "Could not resolve model_class_name=%r. Falling back to "
            "DrugRepurposingGraphTransformer (the only production class "
            "today). Re-train with the forensic-fix trainer to embed "
            "the correct class name in the checkpoint. (audit Issue 124)",
            target_name,
        )
        from graph_transformer.models.graph_transformer import (
            DrugRepurposingGraphTransformer as _FallbackCls,
        )
        cls = _FallbackCls

    if not (isinstance(cls, type) and issubclass(cls, nn.Module)):
        raise ValueError(
            f"Resolved model_class_name={target_name!r} to {cls!r}, which "
            f"is NOT a torch.nn.Module subclass. The checkpoint may be "
            f"corrupted or hostile. Refusing to instantiate. "
            f"(audit Issue 124)"
        )

    # Filter the hyperparams to only those the class's __init__ accepts.
    # This makes the loader robust to extra/missing keys as the model
    # constructor evolves (e.g., a new hyperparam added in a future
    # version is silently dropped when loading an old checkpoint).
    import inspect
    sig = inspect.signature(cls.__init__)
    accepted = {
        k for k in sig.parameters.keys() if k != "self"
    }
    safe_hyperparams = {
        k: v for k, v in (hyperparams or {}).items() if k in accepted
    }
    dropped = set((hyperparams or {}).keys()) - accepted
    if dropped:
        logger.warning(
            "Dropping hyperparams not accepted by %s.__init__: %s. "
            "This is expected when loading a checkpoint saved by a "
            "newer trainer version into an older model class. "
            "(audit Issue 124)",
            cls.__name__, sorted(dropped),
        )

    # Ensure edge_types and node_types are tuples (JSON-serialized
    # checkpoints store them as lists; the model's __init__ expects
    # tuples for hashing).
    if "edge_types" in safe_hyperparams and safe_hyperparams["edge_types"] is not None:
        safe_hyperparams["edge_types"] = [
            tuple(et) if isinstance(et, (list, tuple)) else et
            for et in safe_hyperparams["edge_types"]
        ]
    if "exclude_edges" in safe_hyperparams and safe_hyperparams["exclude_edges"] is not None:
        safe_hyperparams["exclude_edges"] = set(
            tuple(e) if isinstance(e, (list, tuple)) else e
            for e in safe_hyperparams["exclude_edges"]
        )

    return cls(**safe_hyperparams)


def _resolve_class_by_name(qualified_name: str):
    """Resolve a ``module.path.ClassName`` string to the class object.

    Returns ``None`` if the module cannot be imported or the class
    cannot be found. The caller decides whether to fall back or raise.
    """
    if not qualified_name or "." not in qualified_name:
        # Bare class name -- assume it's in the canonical model module.
        from graph_transformer.models import graph_transformer as _gt_mod
        return getattr(_gt_mod, qualified_name, None)
    module_path, _, class_name = qualified_name.rpartition(".")
    try:
        import importlib
        module = importlib.import_module(module_path)
    except (ImportError, ModuleNotFoundError) as exc:
        logger.warning(
            "Could not import module %r for model_class_name %r: %s",
            module_path, qualified_name, exc,
        )
        return None
    cls = getattr(module, class_name, None)
    return cls


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
