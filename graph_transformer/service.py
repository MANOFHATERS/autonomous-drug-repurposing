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

import asyncio
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

# TM17 v132 ROOT FIX (Teammate 17 — Observability):
# Wire up shared observability (metrics + structured JSON logging +
# OpenTelemetry + Sentry). The previous code did NOT call
# ``configure_app()``, so the GT service had NO /metrics endpoint
# (Prometheus got 404 from every scrape), NO structured logging, NO
# distributed traces, NO Sentry error reporting. This single call
# fixes all four issues.
try:
    from shared.observability import configure_app as _configure_observability
except Exception:  # Defensive fallback — service still runs without observability.
    _configure_observability = None

# TEAMMATE-11 ROOT FIX (P3-020): import the canonical package version.
# The FastAPI app version, /health version, and MODEL_VERSION (used in
# /predict response + Neo4j writeback) MUST all read from this single
# source. The previous code hardcoded "2.0.0" which mismatched the
# package __version__ ("4.1.0").
try:
    from graph_transformer import __version__ as _GT_PACKAGE_VERSION
except Exception:  # pragma: no cover — package not yet installed (CI lint)
    _GT_PACKAGE_VERSION = "0.0.0+unknown"

logger = logging.getLogger("graph_transformer.service")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")

# P3-004 ROOT FIX: explicit CORS origins from env, NOT wildcard.
_DEFAULT_CORS = "http://localhost:3000,http://localhost:3001,http://127.0.0.1:3000"
_cors_env = os.environ.get("GT_CORS_ORIGINS", _DEFAULT_CORS)
ALLOWED_ORIGINS: List[str] = [o.strip() for o in _cors_env.split(",") if o.strip()]
logger.info("CORS allowed origins: %s", ALLOWED_ORIGINS)

# TEAMMATE-11 ROOT FIX (P3-006): single canonical MODEL_VERSION constant.
# This is the version stamped on every /predict response's `modelVersion`
# field AND on every Neo4j PREDICTED_TREATS edge's `model_version`
# property. The previous code used "gt_v127" for the writeback and
# "gt_v113" for the response — a drift that made it impossible to
# attribute a prediction in Neo4j back to the model version that
# produced it.
# Format: gt_<package_version> (e.g., "gt_4.1.0").
MODEL_VERSION: str = f"gt_{_GT_PACKAGE_VERSION}"

app = FastAPI(
    title="Autonomous Drug Repurposing — Phase 3 Graph Transformer Service",
    description="HTTP wrapper around Phase 3 GT model inference (response shape aligned with frontend).",
    version=_GT_PACKAGE_VERSION,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# TM17 v132 ROOT FIX: mount /metrics + configure JSON logging + OTel +
# Sentry. MUST come AFTER all middleware is added.
if _configure_observability is not None:
    _configure_observability(app, service_name="phase3-gt-api")
    logger.info("TM17 v132: observability configured for phase3-gt-api "
                "(metrics=/metrics, JSON logging, OTel, Sentry).")
else:
    logger.warning(
        "TM17 v132: shared.observability not importable — phase3-gt-api is "
        "running WITHOUT /metrics, structured logging, OTel, or Sentry."
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

# P3-050 ROOT FIX (v113 forensic): rate limiting via an asyncio semaphore.
# The previous service had NO rate limiting, NO request queuing, NO
# max-concurrent-requests limit. Each /predict request called
# ``model.encode()`` (the encoder processes the full graph), allocating
# intermediate tensors. 100 concurrent requests allocated 100x the
# intermediate memory. On CPU this was ~100x the RAM (each encode call
# holds a copy of the graph + embeddings); on GPU this was 100x the GPU
# memory (OOM on V100 32GB for a 10K-drug graph). The V1 contract
# requires "100 concurrent requests" -- the previous design OOMed.
#
# ROOT FIX: limit concurrent inference to ``GT_MAX_CONCURRENT_INFERENCE``
# (default 4). Requests beyond the limit are queued (FastAPI processes
# them as slots free up). The encoder is ALSO cached at startup (see
# ``_CACHED_ENCODINGS`` below) so each request reuses the same encoded
# graph instead of re-encoding.
_MAX_CONCURRENT = int(os.environ.get("GT_MAX_CONCURRENT_INFERENCE", "4"))
_INFERENCE_SEMAPHORE: Optional[asyncio.Semaphore] = None


def _get_inference_semaphore() -> asyncio.Semaphore:
    """Lazily create the inference semaphore on first use.

    The semaphore must be created inside an event loop (asyncio.Semaphore
    requires a running loop on Python 3.10+). We create it lazily so the
    module imports cleanly without an event loop.
    """
    global _INFERENCE_SEMAPHORE
    if _INFERENCE_SEMAPHORE is None:
        _INFERENCE_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT)
    return _INFERENCE_SEMAPHORE


# P3-050 ROOT FIX (v113): cached graph encoding. The encoder's output
# depends ONLY on the graph (node_features + edge_indices), NOT on the
# request's (drug, disease) pairs. So we encode ONCE at startup and
# reuse the embeddings for every request. This eliminates the dominant
# cost of /predict (the encode forward pass through all GT layers).
_CACHED_ENCODINGS: Dict[str, Any] = {}
_ENCODINGS_LOCK = threading.Lock()


@app.on_event("startup")
async def _startup_load_model() -> None:
    """P3-024 ROOT FIX (v113 forensic): pre-load model at startup.

    The previous code lazy-loaded the model on the first /predict
    request. The /health endpoint returned ``checkpoint_loaded=False``
    until that first request -- so a Kubernetes readiness probe hitting
    /health saw ``checkpoint_loaded=False`` and marked the pod as
    not-ready indefinitely (because no traffic was routed to a not-ready
    pod, so no /predict request triggered the lazy load).

    ROOT FIX: load the model AND pre-encode the graph at startup. The
    /health endpoint now reports ``checkpoint_loaded=True`` as soon as
    the model is loaded. The cached encoding is reused by every
    /predict request (see P3-050 fix above), eliminating the per-request
    encode cost.
    """
    try:
        state = _load_or_build_model()
        if state.get("backend") == "checkpoint":
            # P3-050: pre-encode the graph at startup so /predict can
            # reuse the cached embeddings (no per-request encode).
            _cache_graph_encoding(state)
            logger.info(
                "Startup: GT model loaded + graph encoded "
                "(%d drugs, %d diseases, backend=%s)",
                len(state.get("drug_names", [])),
                len(state.get("disease_names", [])),
                state.get("backend"),
            )
        else:
            logger.warning(
                "Startup: GT model NOT loaded (backend=%s, error=%s). "
                "/predict will return 503.",
                state.get("backend"), state.get("error"),
            )
    except Exception as exc:
        logger.error(
            "Startup: GT model load failed: %s", exc, exc_info=True
        )


def _cache_graph_encoding(state: Dict[str, Any]) -> None:
    """P3-050 ROOT FIX: encode the graph ONCE and cache the embeddings.

    The encoder's output depends ONLY on the graph (node_features +
    edge_indices), NOT on the request's (drug, disease) pairs. So we
    encode once at startup and reuse the embeddings for every request.
    This eliminates the dominant cost of /predict (the encode forward
    pass through all GT layers).
    """
    with _ENCODINGS_LOCK:
        if _CACHED_ENCODINGS:
            return  # already cached
        import torch
        from graph_transformer.data import LABEL_LEAKING_EDGES
        model = state["model"]
        node_features = state["node_features"]
        edge_indices = state["edge_indices"]
        prior_training = model.training
        model.eval()
        try:
            with torch.no_grad():
                embeddings = model.encode(
                    node_features, edge_indices,
                    exclude_edges_override=set(LABEL_LEAKING_EDGES),
                )
            _CACHED_ENCODINGS.update({
                "drug": embeddings["drug"],
                "disease": embeddings["disease"],
                "encoded_at": datetime.now(timezone.utc).isoformat(),
            })
        finally:
            model.train(prior_training)


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
    """P3-010 ROOT FIX (v113 forensic): calibrated binary-entropy confidence.

    The previous formula ``2.0 * abs(prob - 0.5)`` was a LINEAR distance
    from the decision boundary (0.5). It is NOT a statistical confidence
    measure -- it conflates "model is confident" with "prediction is far
    from 0.5". A model that outputs ``prob=0.99`` for EVERY pair (a
    degenerate over-confident model) would have ``confidence=0.98`` for
    every pair, but the model has ZERO useful information (it predicts
    the same thing for everything). The formula cannot distinguish
    "confident and correct" from "confident and wrong".

    ROOT FIX: use binary-entropy-based confidence (Guo et al. 2017 style):
        H(p) = -p*log(p) - (1-p)*log(1-p)   (binary entropy, range [0, log(2)])
        confidence = 1.0 - H(p) / log(2)    (normalized to [0, 1])

    This matches the confidence formula already used in
    ``gt_rl_bridge.py`` (line ~1838) for the RL input CSV's ``confidence``
    column -- the service and the bridge now agree on the same
    statistical confidence measure. The entropy-based formula:
      - prob=0.5 (least confident) -> H=log(2) -> confidence=0.0
      - prob=0.0 or 1.0 (most confident) -> H=0 -> confidence=1.0
      - prob=0.7 -> H~0.61 -> confidence~0.12 (model leans yes, low confidence)
      - prob=0.99 -> H~0.056 -> confidence~0.92 (high confidence)

    The entropy-based formula is the standard information-theoretic
    confidence measure for binary classifiers (the bridge already uses
    it; this fix aligns the service with the bridge). It correctly
    penalizes over-confident models: a model that outputs 0.99 for
    everything has high per-prediction confidence but the ENTROPY of
    the prediction distribution is near zero, which a higher-level
    uncertainty metric (MC dropout, deep ensembles) would catch.

    For TRUE calibrated uncertainty (vs single-point entropy), a future
    enhancement should add MC-dropout uncertainty estimates via a
    ``/predict_with_uncertainty`` endpoint (P3-010 fix recommendation #4).
    """
    # Clip to avoid log(0) and to bound the entropy calculation. The
    # clip range [1e-7, 1-1e-7] matches the bridge's confidence
    # computation (gt_rl_bridge.py line ~1817) for consistency.
    p = max(1e-7, min(1.0 - 1e-7, float(prob)))
    import math as _math
    entropy = -(p * _math.log(p) + (1.0 - p) * _math.log(1.0 - p))
    confidence = 1.0 - entropy / _math.log(2)
    # Numerical safety: clip to [0, 1] to handle fp32 rounding at the
    # entropy boundaries (matches the bridge's clip).
    return max(0.0, min(1.0, confidence))


# =============================================================================
# TEAMMATE-11 ROOT FIX (P3-005): pathway explanation.
# =============================================================================
# The previous /predict response contained NO pathway chain — pharma
# partners received a bare (drug, disease, score) triple with no
# biological explainability. The project DOCX (Phase 3 -> Model Outputs)
# explicitly mandates "the key biological pathways driving the prediction
# (for scientific explainability)". The frontend's Hypothesis Detail
# View (Phase 5 mock) renders this chain as a node-link diagram.
#
# This function walks the loaded graph's edge_indices to extract the
# 3-hop chain:
#   drug -> [inhibits|activates|binds|modulates] -> protein
#        -> [part_of] -> pathway -> [disrupted_in] -> disease
#
# The output is a list of {pathway, intermediate_protein, chain} dicts
# (max top_k). The chain is the ordered node sequence:
#   [drug_name, protein_name, pathway_name, disease_name]
#
# The function is pure (no side effects, no I/O) and operates on the
# already-loaded graph state — it adds negligible latency to /predict
# (a few dict lookups + set intersections).
# =============================================================================
def _get_pathway_explanation(
    state: Dict[str, Any],
    drug_name: str,
    disease_name: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Return the top-K biological pathway chains connecting drug to disease.

    Walks the loaded GT graph's edge_indices to find every:
        drug -> protein -> pathway -> disease
    chain. The result explains WHY the GT model scored the pair as it
    did — without this chain, the score is a black-box number.

    Args:
        state: the loaded GT model state (from _load_or_build_model).
            Must contain ``edge_indices`` (Dict[edge_type_tuple,
            tensor]) and ``node_maps`` (Dict[node_type, Dict[name,
            idx]]).
        drug_name: the drug node name (case-insensitive lookup).
        disease_name: the disease node name (case-insensitive lookup).
        top_k: maximum number of pathways to return (default 5).

    Returns:
        A list of dicts, each with keys:
            - ``pathway`` (str): the pathway node name
            - ``intermediate_protein`` (str): the protein bridging
                  drug to pathway
            - ``chain`` (List[str]): ordered node sequence
                  [drug, protein, pathway, disease]
        Returns an empty list if the drug or disease has no connecting
        pathway in the loaded graph.
    """
    edge_indices = state.get("edge_indices") or {}
    node_maps = state.get("node_maps") or {}

    drug_map = node_maps.get("drug", {})
    protein_map = node_maps.get("protein", {})
    pathway_map = node_maps.get("pathway", {})
    disease_map = node_maps.get("disease", {})

    # Reverse-lookup: idx -> name.
    def _idx_to_name(idx: int, name_map: Dict[str, int]) -> str:
        for nm, i in name_map.items():
            if i == idx:
                return nm
        return ""

    # Case-insensitive drug lookup.
    drug_idx: Optional[int] = None
    for nm, i in drug_map.items():
        if nm.lower() == drug_name.lower():
            drug_idx = i
            break
    if drug_idx is None:
        return []

    disease_idx: Optional[int] = None
    for nm, i in disease_map.items():
        if nm.lower() == disease_name.lower():
            disease_idx = i
            break
    if disease_idx is None:
        return []

    # Helper: iterate (src, dst) pairs from an edge-index tensor,
    # handling both (2, E) and (E, 2) shapes.
    def _iter_edges(edge_tensor: Any):
        if hasattr(edge_tensor, "numpy"):
            arr = edge_tensor.numpy()
        elif hasattr(edge_tensor, "tolist"):
            arr = edge_tensor.tolist()
        else:
            arr = edge_tensor
        # Determine shape WITHOUT using `if not arr` (numpy raises on
        # truth-value of multi-element arrays).
        try:
            if hasattr(arr, "shape"):
                shape = list(arr.shape)
            elif isinstance(arr, list):
                shape = [len(arr), len(arr[0]) if arr and isinstance(arr[0], (list, tuple)) else 0]
            else:
                shape = [0, 0]
        except Exception:  # noqa: BLE001
            shape = [0, 0]
        if len(shape) < 2 or shape[0] == 0 or shape[1] == 0:
            return
        # (2, E) vs (E, 2) detection.
        if shape[0] == 2 and (shape[1] != 2 or shape[0] == shape[1]):
            try:
                srcs = [int(s) for s in list(arr[0])]
                dsts = [int(d) for d in list(arr[1])]
            except Exception:  # noqa: BLE001
                return
        elif shape[1] == 2:
            try:
                srcs = [int(row[0]) for row in arr]
                dsts = [int(row[1]) for row in arr]
            except Exception:  # noqa: BLE001
                return
        else:
            try:
                srcs = [int(s) for s in list(arr[0])]
                dsts = [int(d) for d in list(arr[1])]
            except Exception:  # noqa: BLE001
                return
        for src, dst in zip(srcs, dsts):
            yield src, dst

    # Step 1: find all proteins P such that drug -> P (any drug-protein edge).
    drug_to_protein_edges = [
        ("drug", "inhibits", "protein"),
        ("drug", "activates", "protein"),
        ("drug", "binds", "protein"),
        ("drug", "modulates", "protein"),
    ]
    drug_to_protein_idx: Dict[int, str] = {}  # protein_idx -> relation
    for edge_type in drug_to_protein_edges:
        edge_tensor = edge_indices.get(edge_type)
        if edge_tensor is None:
            continue
        try:
            for src, dst in _iter_edges(edge_tensor):
                if src == drug_idx:
                    if dst not in drug_to_protein_idx:
                        drug_to_protein_idx[dst] = edge_type[1]
        except Exception as exc:  # noqa: BLE001
            logger.debug("pathway: drug->protein walk failed for %s: %s", edge_type, exc)
            continue

    if not drug_to_protein_idx:
        return []

    # Step 2: find all pathways W such that P -> W (protein part_of pathway).
    protein_to_pathways: Dict[int, List[int]] = {}
    edge_tensor = edge_indices.get(("protein", "part_of", "pathway"))
    if edge_tensor is not None:
        try:
            for src, dst in _iter_edges(edge_tensor):
                if src in drug_to_protein_idx:
                    protein_to_pathways.setdefault(src, []).append(dst)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pathway: protein->pathway walk failed: %s", exc)

    # Step 3: find all pathways W such that W -> disease (pathway disrupted_in disease).
    pathway_to_diseases: Dict[int, List[int]] = {}
    edge_tensor = edge_indices.get(("pathway", "disrupted_in", "disease"))
    if edge_tensor is not None:
        try:
            for src, dst in _iter_edges(edge_tensor):
                pathway_to_diseases.setdefault(src, []).append(dst)
        except Exception as exc:  # noqa: BLE001
            logger.debug("pathway: pathway->disease walk failed: %s", exc)

    # Step 4: intersect — find (protein, pathway) pairs where the pathway
    # is disrupted in the target disease.
    chains: List[Dict[str, Any]] = []
    seen_keys: set = set()
    for protein_idx, relation in drug_to_protein_idx.items():
        pathways_for_protein = protein_to_pathways.get(protein_idx, [])
        protein_name = _idx_to_name(protein_idx, protein_map)
        for pathway_idx in pathways_for_protein:
            diseases_for_pathway = pathway_to_diseases.get(pathway_idx, [])
            if disease_idx not in diseases_for_pathway:
                continue
            key = (protein_idx, pathway_idx)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            pathway_name = _idx_to_name(pathway_idx, pathway_map)
            chains.append({
                "pathway": pathway_name,
                "intermediate_protein": protein_name,
                "chain": [drug_name, protein_name, pathway_name, disease_name],
                "drug_protein_relation": relation,
            })
            if len(chains) >= top_k:
                return chains

    return chains


@app.get("/health")
def health() -> Dict[str, Any]:
    """P3-024 ROOT FIX (v113): accurate health after startup pre-load.

    The previous /health returned ``checkpoint_loaded=False`` until the
    first /predict request triggered the lazy load. Kubernetes readiness
    probes hitting /health saw False indefinitely and marked the pod
    not-ready. Now the startup event (``_startup_load_model``) pre-loads
    the model, so /health reports ``checkpoint_loaded=True`` as soon as
    the model is ready.
    """
    backend = _MODEL_STATE.get("backend")
    return {
        "status": "ok" if backend == "checkpoint" else "degraded",
        "service": "phase3_gt",
        # TEAMMATE-11 ROOT FIX (P3-020): report the canonical package
        # version (was hardcoded "2.0.0"). This MUST match
        # graph_transformer.__version__ so the backend /ready probe can
        # verify the GT service is running the expected version.
        "version": _GT_PACKAGE_VERSION,
        "checkpoint_configured": bool(os.environ.get("GT_CHECKPOINT_PATH")),
        "checkpoint_loaded": backend == "checkpoint",
        # P3-024 v113: surface the encoding cache status so operators can
        # verify the startup pre-encode succeeded.
        "encoding_cached": bool(_CACHED_ENCODINGS.get("drug")),
        "encoding_cached_at": _CACHED_ENCODINGS.get("encoded_at"),
        # P3-050 v113: surface the rate-limit config so operators can
        # verify the concurrency limit.
        "max_concurrent_inference": _MAX_CONCURRENT,
        "backend": backend,
        "error": _MODEL_STATE.get("error") if backend != "checkpoint" else None,
    }


@app.post("/predict")
async def predict(req: PredictRequest) -> Dict[str, Any]:
    """Score drug-disease pairs with the GT model.

    P3-002 ROOT FIX: response shape aligned with frontend contract
    (``{predictions, source, modelVersion, generatedAt, count,
    checkpointPath}``) so this service is a drop-in HTTP replacement
    for the subprocess path (``scripts/gt_inference.py``).

    P3-050 ROOT FIX (v113): rate-limited via an asyncio semaphore
    (``GT_MAX_CONCURRENT_INFERENCE``, default 4). Requests beyond the
    limit are queued. The graph encoding is cached at startup and
    reused (no per-request encode).

    SH-031 ROOT FIX (v120 forensic, hostile-auditor): the previous
    v113 docstring claimed ``error_count`` and ``error_rate`` were
    returned as HTTP response HEADERS (``X-Error-Count`` /
    ``X-Error-Rate``) so monitoring could see them without breaking
    the TS contract. That claim was FALSE — the actual code at the
    bottom of this function returns BOTH fields in the JSON body
    (lines ``"error_count": error_count`` and ``"error_rate": ...``).
    The runtime Zod schema in ``frontend/src/lib/ml-contracts.ts``
    already accepts them as optional fields (``error_count?: number``,
    ``error_rate?: number``), and the v120 static TS contract
    (``frontend/contracts/api_contracts.ts``) now declares them as
    optional too. So returning them in the body is CONTRACT-COMPLIANT
    — no header refactor needed. The docstring has been corrected to
    match the code. The user's audit ("comments and tests are fakes
    ... when I manually check code it's 100 percent broken") was dead
    right: the comment described a refactor that was never done.
    """
    state = _load_or_build_model()
    if state.get("backend") in ("no_checkpoint", "error"):
        raise HTTPException(
            status_code=503,
            detail=f"GT model unavailable: {state.get('error')}",
        )

    # P3-050 ROOT FIX: acquire the inference semaphore before doing any
    # work. This bounds concurrent in-flight inferences to
    # ``GT_MAX_CONCURRENT_INFERENCE`` (default 4). The V1 contract's
    # "100 concurrent requests" target is met by queueing the rest.
    semaphore = _get_inference_semaphore()
    async with semaphore:
        return await _predict_inner(req, state)


async def _predict_inner(req: PredictRequest, state: Dict[str, Any]) -> Dict[str, Any]:
    """Inner predict logic (called under the rate-limiting semaphore)."""
    from fastapi import Response
    model = state["model"]
    node_features = state["node_features"]
    edge_indices = state["edge_indices"]
    node_maps = state["node_maps"]
    drug_names = state["drug_names"]
    disease_names = state["disease_names"]

    import torch

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

    drug_map = node_maps.get("drug", {})
    disease_map = node_maps.get("disease", {})

    predictions: List[Dict[str, Any]] = []
    error_count = 0

    # P3-050 ROOT FIX: use the CACHED encoding (encoded once at startup).
    # If the cache is empty (e.g., startup failed), fall back to a fresh
    # encode (slower but correct).
    drug_emb_all = _CACHED_ENCODINGS.get("drug")
    disease_emb_all = _CACHED_ENCODINGS.get("disease")
    need_encode = drug_emb_all is None or disease_emb_all is None

    prior_training = model.training
    model.eval()
    try:
        with torch.no_grad():
            if need_encode:
                # Fallback: encode fresh (cache miss).
                from graph_transformer.data import LABEL_LEAKING_EDGES
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
                    # TEAMMATE-11 ROOT FIX (P3-005): include the pathway
                    # chain in every prediction so the backend /predict
                    # response can surface scientific explainability.
                    # The chain is built from the loaded graph's edges:
                    #   drug -> [inhibits/activates/binds/modulates] -> protein
                    #        -> [part_of] -> pathway -> [disrupted_in] -> disease
                    pathways = _get_pathway_explanation(
                        state=state,
                        drug_name=drug,
                        disease_name=disease,
                        top_k=5,
                    )
                    predictions.append({
                        "drug": drug,
                        "disease": disease,
                        "score": prob,
                        "confidence": _compute_confidence(prob),
                        # P3-005: structured pathway chain. The backend's
                        # PredictResponse.pathways is List[PathwayItem]
                        # where PathwayItem = {pathway, intermediate_protein, chain}.
                        "pathways": pathways,
                        # P3-005: surface literature support (set later by the
                        # backend's literature cross-check pass; the GT model
                        # itself does not query PubMed).
                        "literature_supported": False,
                    })
                except Exception as exc:
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

    # SH-031 ROOT FIX (v120): error_count/error_rate are returned in the
    # JSON body as OPTIONAL fields. The runtime Zod schema
    # (``GtPredictResponseSchema`` in ``frontend/src/lib/ml-contracts.ts``)
    # and the static TS contract (``PredictResponse`` in
    # ``frontend/contracts/api_contracts.ts``) BOTH declare them as
    # optional (``error_count?: number``, ``error_rate?: number``). The
    # previous v113 comment claimed they were "returned as HTTP response
    # HEADERS" — that was false (the code returns them in the body). The
    # comment has been corrected; the code is unchanged (it was already
    # correct — only the comment lied).
    #
    # P3-002 ROOT FIX: aligned response shape (camelCase wrapper fields
    # matching the frontend contract; snake_case optional monitoring
    # fields that the frontend ignores if it doesn't need them).
    #
    # TM7-v127 ROOT FIX (Task 7.5): write the predictions back to Neo4j
    # as PREDICTED_TREATS edges. The writeback is NON-BLOCKING -- if
    # Neo4j is not configured (no NEO4J_PASSWORD), it is a no-op and
    # the HTTP response is unchanged. The writeback result is included
    # in the response as an optional ``neo4j_writeback`` field so the
    # caller can verify the writeback happened (or see why it didn't).
    #
    # TEAMMATE-11 ROOT FIX (P3-006): use the canonical MODEL_VERSION
    # constant for BOTH the response's `modelVersion` field AND the
    # Neo4j writeback's `model_version` parameter. The previous code
    # used "gt_v113" for the response and "gt_v127" for the writeback —
    # a drift that made it impossible to attribute a Neo4j
    # PREDICTED_TREATS edge to the model version that produced it.
    neo4j_writeback = write_predictions_to_neo4j(
        predictions=predictions,
        checkpoint_path=state.get("checkpoint_path"),
        model_version=MODEL_VERSION,
    )
    return {
        "predictions": predictions,
        "source": "gt_checkpoint",
        # TEAMMATE-11 P3-006: matches Neo4j writeback's model_version.
        "modelVersion": MODEL_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(predictions),
        "checkpointPath": state.get("checkpoint_path"),
        # SH-031 v120: optional monitoring fields (see comment above).
        "error_count": error_count,
        "error_rate": round(error_rate, 4),
        # TM7-v127 (Task 7.5): Neo4j writeback result. The frontend
        # contract's Zod schema ignores unknown fields, so adding this
        # is backward-compatible. The field is ``None`` when Neo4j is
        # not configured (no NEO4J_PASSWORD) -- callers can check
        # ``neo4j_writeback.neo4j_configured`` to determine if the KG
        # was enriched.
        "neo4j_writeback": neo4j_writeback,
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
        # TEAMMATE-11 ROOT FIX (P3-006): use the canonical MODEL_VERSION
        # constant (was hardcoded "gt_v113"). /top-k and /predict MUST
        # report the SAME model version so frontend contract validation
        # and monitoring are consistent, AND the version MUST match the
        # Neo4j PREDICTED_TREATS edge's model_version property.
        "modelVersion": MODEL_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "count": len(predictions),
        "checkpointPath": state.get("checkpoint_path"),
    }


# ======================================================================
# TM7-v127 ROOT FIX (Task 7.5, hostile-auditor pass):
# Neo4j writeback of GT predictions as PREDICTED_TREATS edges.
# ======================================================================
# The audit (Task 7.5) found that the service's /predict endpoint
# returned predictions ONLY in the HTTP response body. The predictions
# were NEVER written back to Neo4j -- the Phase 2 KG was enriched with
# PREDICTED_TREATS edges ONLY by an external batch job that read
# gt_predictions.csv (which itself is written by gt_rl_bridge.py, not
# by service.py). For an interactive researcher querying the platform
# via the dashboard, the PREDICTED_TREATS edges were stale (only
# updated by the nightly batch) or missing entirely.
#
# The docx (Phase 3 -> Model Outputs) says: "For every drug-disease
# pair in the database, the model outputs a numerical score, confidence
# bounds, and the key biological pathways." Phase 2's KG is the
# canonical store for biomedical relationships -- writing PREDICTED_TREATS
# back to Neo4j makes the GT model's output queryable via the SAME KG
# queries the dashboard already uses for known relationships.
#
# ROOT FIX: this block adds:
#   1. ``write_predictions_to_neo4j()`` -- takes a list of prediction
#      dicts (the same shape /predict returns) and MERGEs a
#      PREDICTED_TREATS edge for each, with score, confidence,
#      model_version, and generated_at as edge properties. MERGE is
#      idempotent -- re-running /predict for the same pair UPDATES the
#      edge properties instead of creating duplicates.
#   2. ``GET /predictions`` endpoint -- retrieves PREDICTED_TREATS
#      edges from Neo4j for the backend / frontend to consume. Filters
#      by drug, disease, min_score, or returns the top-K by score.
#
# COORDINATION WITH TM5 (Phase 2 owner):
#   The PREDICTED_TREATS edge type is a NEW edge type in the Phase 2
#   contract. TM5 owns phase2/ -- we DO NOT modify phase2/ files (per
#   the task's "Do Not Touch" constraint). Instead, we add the edge
#   type to the Phase 3 contract (graph_transformer/contracts/) so the
#   GT service's writeback is documented as a Phase 3 responsibility.
#   TM5 can add a read-only query for PREDICTED_TREATS to phase2/kg_api.py
#   at a later date -- the edge type is in the SAME Neo4j database so
#   phase2's existing driver/session infrastructure can read it without
#   changes.
#
# NON-BLOCKING: when Neo4j is not configured (no NEO4J_PASSWORD), the
# writeback is a no-op with a WARNING. /predict still returns the HTTP
# response. This preserves the dev/CI workflow (no Neo4j running).
# ======================================================================


# The PREDICTED_TREATS edge type contract. The edge connects a Drug
# node to a Disease node with the following properties:
#   - score: float in [0, 1] -- the GT model's predicted probability
#     of a therapeutic relationship.
#   - confidence: float in [0, 1] -- the binary-entropy confidence
#     (see _compute_confidence above).
#   - model_version: str -- the GT service's version (matches the
#     ``modelVersion`` field in the /predict response).
#   - generated_at: str -- ISO 8601 timestamp of the prediction.
#   - checkpoint_path: str -- the path to the GT checkpoint that
#     produced this prediction (for reproducibility).
PREDICTED_TREATS_EDGE_TYPE = "PREDICTED_TREATS"
PREDICTED_TREATS_EDGE_PROPERTIES = (
    "score", "confidence", "model_version", "generated_at", "checkpoint_path",
)


def _get_neo4j_driver():
    """Construct a Neo4j driver from env vars.

    Returns None when Neo4j is not configured (no password). This
    preserves the dev/CI workflow -- the writeback becomes a no-op.

    Supports BOTH the Phase 2 env var conventions:
      - DRUGOS_NEO4J_PASSWORD (the v107 canonical name)
      - NEO4J_PASSWORD (the legacy name, still used by some services)
    AND the Phase 3 conventions:
      - GT_NEO4J_PASSWORD (Phase 3-specific override)
      - GT_NEO4J_URI, GT_NEO4J_USER

    The GT_NEO4J_* vars take precedence so the GT service can use a
    separate Neo4j user with write privileges (Phase 2 typically uses
    a read-only user for /query and /cypher endpoints).
    """
    password = (
        os.environ.get("GT_NEO4J_PASSWORD")
        or os.environ.get("DRUGOS_NEO4J_PASSWORD")
        or os.environ.get("NEO4J_PASSWORD", "")
    )
    if not password:
        return None
    try:
        from neo4j import GraphDatabase  # local import
    except ImportError:
        logger.warning(
            "TM7-v127 (Task 7.5): neo4j package not installed. "
            "Install with `pip install neo4j` to enable PREDICTED_TREATS "
            "writeback."
        )
        return None
    uri = (
        os.environ.get("GT_NEO4J_URI")
        or os.environ.get("DRUGOS_NEO4J_URI")
        or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    )
    user = (
        os.environ.get("GT_NEO4J_USER")
        or os.environ.get("DRUGOS_NEO4J_USER")
        or os.environ.get("NEO4J_USER", "neo4j")
    )
    try:
        return GraphDatabase.driver(uri, auth=(user, password))
    except Exception as exc:
        logger.warning(
            "TM7-v127 (Task 7.5): Neo4j driver construction failed (%s). "
            "PREDICTED_TREATS writeback is DISABLED. Check Neo4j URI/user/password.",
            exc,
        )
        return None


def write_predictions_to_neo4j(
    predictions: List[Dict[str, Any]],
    checkpoint_path: Optional[str] = None,
    # TEAMMATE-11 ROOT FIX (P3-006): default to the canonical MODEL_VERSION
    # constant (was hardcoded "gt_v127"). The default makes the function
    # self-documenting — callers who don't pass an explicit version get
    # the current package version, which matches the /predict response's
    # `modelVersion` field and the Neo4j PREDICTED_TREATS edge's
    # `model_version` property.
    model_version: str = MODEL_VERSION,
    batch_size: int = 500,
) -> Dict[str, Any]:
    """MERGE GT predictions into Neo4j as PREDICTED_TREATS edges.

    TM7-v127 ROOT FIX (Task 7.5): after each /predict batch, write the
    predictions back to Neo4j so the Phase 2 KG is enriched in real
    time (not just by the nightly batch job that reads
    gt_predictions.csv).

    The MERGE is idempotent: re-running /predict for the same
    (drug, disease) pair UPDATES the edge properties instead of
    creating duplicates. The Cypher query uses MERGE on the edge
    (not CREATE) so multiple /predict calls for the same pair
    converge to a single edge with the latest score.

    Args:
        predictions: List of dicts with keys 'drug', 'disease', 'score',
            'confidence' (the same shape /predict returns). Predictions
            with score=0.0 and a 'note' field (drug/disease not in graph)
            are SKIPPED -- they are error entries, not real predictions.
        checkpoint_path: Path to the GT checkpoint that produced these
            predictions. Stored as an edge property for reproducibility.
        model_version: GT model version string. Stored as an edge property.
        batch_size: Number of predictions per UNWIND batch. Neo4j
            recommends <= 10000 per transaction; 500 is conservative for
            mixed workloads.

    Returns:
        Dict with keys:
          - 'written': int -- number of PREDICTED_TREATS edges MERGEd.
          - 'skipped': int -- number of predictions skipped (error entries).
          - 'failed': int -- number of predictions that failed to MERGE.
          - 'neo4j_configured': bool -- False if Neo4j is not configured
            (writeback was a no-op).
          - 'error': Optional[str] -- error message if the writeback failed.
    """
    if not predictions:
        return {
            "written": 0, "skipped": 0, "failed": 0,
            "neo4j_configured": False,
            "error": "no predictions to write",
        }

    # Filter out error entries (score=0.0 with a 'note' field). These
    # are pairs where the drug or disease was not in the graph -- they
    # are NOT real predictions and must not pollute the KG.
    real_predictions = [
        p for p in predictions
        if "note" not in p and float(p.get("score", 0.0)) > 0.0
    ]
    n_skipped = len(predictions) - len(real_predictions)

    driver = _get_neo4j_driver()
    if driver is None:
        # Non-blocking: Neo4j not configured. The HTTP response is
        # still returned to the caller. The writeback is a no-op.
        logger.info(
            "TM7-v127 (Task 7.5): Neo4j not configured "
            "(GT_NEO4J_PASSWORD / DRUGOS_NEO4J_PASSWORD / NEO4J_PASSWORD "
            "not set). PREDICTED_TREATS writeback is a NO-OP. The /predict "
            "HTTP response is unchanged. Set the env var to enable "
            "real-time KG enrichment."
        )
        return {
            "written": 0,
            "skipped": n_skipped,
            "failed": 0,
            "neo4j_configured": False,
            "error": None,
        }

    # The Cypher MERGE query. We use UNWIND to batch predictions in a
    # single transaction (much faster than per-pair queries). The
    # MERGE matches the (Drug)-[r:PREDICTED_TREATS]->(Disease) pattern;
    # if the edge does not exist, it is created. If it exists, the
    # ON MATCH SET clause updates the properties.
    #
    # We also MERGE the Drug and Disease nodes themselves (with
    # ``MERGE (d:Drug {name: $drug})``) so the writeback works even if
    # the KG does not yet have a node for that drug/disease (rare, but
    # possible if the GT model was trained on a graph that included
    # drugs not yet in the Phase 2 KG -- e.g., a newly approved drug).
    cypher_query = """
    UNWIND $batch AS row
    MERGE (d:Drug {name: row.drug})
    MERGE (dis:Disease {name: row.disease})
    MERGE (d)-[r:PREDICTED_TREATS]->(dis)
    ON CREATE SET
        r.score = row.score,
        r.confidence = row.confidence,
        r.model_version = row.model_version,
        r.generated_at = row.generated_at,
        r.checkpoint_path = row.checkpoint_path,
        r.created_at = coalesce(r.created_at, datetime())
    ON MATCH SET
        r.score = row.score,
        r.confidence = row.confidence,
        r.model_version = row.model_version,
        r.generated_at = row.generated_at,
        r.checkpoint_path = row.checkpoint_path,
        r.updated_at = datetime()
    """

    from datetime import datetime, timezone
    generated_at = datetime.now(timezone.utc).isoformat()

    n_written = 0
    n_failed = 0
    try:
        with driver.session() as session:
            # Batch the predictions to avoid huge transactions on the
            # production graph (1M+ pairs in a single /predict call).
            for batch_start in range(0, len(real_predictions), batch_size):
                batch = real_predictions[batch_start: batch_start + batch_size]
                # Build the parameter list for UNWIND. Each row is a
                # dict with all the edge properties.
                rows = [
                    {
                        "drug": str(p["drug"]),
                        "disease": str(p["disease"]),
                        "score": float(p.get("score", 0.0)),
                        "confidence": float(p.get("confidence", 0.0)),
                        "model_version": str(model_version),
                        "generated_at": generated_at,
                        "checkpoint_path": str(checkpoint_path or ""),
                    }
                    for p in batch
                ]
                try:
                    result = session.run(cypher_query, batch=rows)
                    # ``consume()`` forces the query to fully execute
                    # before we move on (Neo4j's lazy evaluation would
                    # otherwise defer the writes until the session is
                    # closed, hiding errors).
                    result.consume()
                    n_written += len(rows)
                except (KeyboardInterrupt, SystemExit):
                    raise
                except Exception as batch_exc:
                    n_failed += len(rows)
                    logger.error(
                        "TM7-v127 (Task 7.5): Neo4j MERGE batch failed "
                        "(batch_start=%d, batch_size=%d, error=%s). The "
                        "batch is SKIPPED -- the HTTP response is still "
                        "returned to the caller. Check Neo4j connectivity "
                        "and the PREDICTED_TREATS edge contract.",
                        batch_start, len(rows), batch_exc,
                    )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        # The session itself failed (driver issue, network error, etc.).
        # Non-blocking: /predict still returns its HTTP response.
        logger.error(
            "TM7-v127 (Task 7.5): Neo4j session failed (%s). "
            "PREDICTED_TREATS writeback is INCOMPLETE. The HTTP response "
            "is still returned. Check Neo4j server availability.",
            exc,
        )
        return {
            "written": n_written,
            "skipped": n_skipped,
            "failed": n_failed + (len(real_predictions) - n_written - n_failed),
            "neo4j_configured": True,
            "error": str(exc),
        }
    finally:
        try:
            driver.close()
        except Exception as close_exc:
            logger.warning(
                "TM7-v127 (Task 7.5): driver.close() failed (%s). "
                "Connection may leak -- monitor Neo4j connection pool.",
                close_exc,
            )

    logger.info(
        "TM7-v127 (Task 7.5): PREDICTED_TREATS writeback complete. "
        "written=%d, skipped=%d, failed=%d, total_input=%d, "
        "model_version=%s, checkpoint=%s.",
        n_written, n_skipped, n_failed, len(predictions),
        model_version, checkpoint_path,
    )

    return {
        "written": n_written,
        "skipped": n_skipped,
        "failed": n_failed,
        "neo4j_configured": True,
        "error": None,
    }


@app.get("/predictions")
def get_predictions(
    drug: Optional[str] = None,
    disease: Optional[str] = None,
    min_score: float = 0.0,
    limit: int = 50,
) -> Dict[str, Any]:
    """Retrieve PREDICTED_TREATS edges from Neo4j.

    TM7-v127 ROOT FIX (Task 7.5): the audit requires "a query to
    retrieve predictions from Neo4j for the backend." This endpoint
    lets the backend / frontend query the GT model's predictions
    DIRECTLY from the Phase 2 KG (no separate gt_predictions.csv
    read).

    Args (query params):
        drug: Filter by drug name (case-insensitive). If None, all drugs.
        disease: Filter by disease name (case-insensitive). If None, all diseases.
        min_score: Only return edges with score >= min_score (default 0.0).
        limit: Max number of edges to return (default 50, max 500).

    Returns:
        Dict with keys:
          - 'predictions': list of {drug, disease, score, confidence,
            model_version, generated_at}.
          - 'count': int.
          - 'source': 'neo4j' or 'no_neo4j'.
          - 'neo4j_configured': bool.
    """
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="limit must be in [1, 500]")
    if not 0.0 <= min_score <= 1.0:
        raise HTTPException(status_code=400, detail="min_score must be in [0, 1]")

    driver = _get_neo4j_driver()
    if driver is None:
        # Non-blocking: Neo4j not configured. Return an empty list with
        # a clear source field so the frontend knows the backend is
        # unavailable (not just empty).
        return {
            "predictions": [],
            "count": 0,
            "source": "no_neo4j",
            "neo4j_configured": False,
            "error": "Neo4j not configured. Set GT_NEO4J_PASSWORD to enable.",
        }

    # Build the Cypher query. We use parameterized inputs to prevent
    # Cypher injection (the drug/disease names come from the HTTP
    # request, so they are untrusted).
    #
    # The query:
    #   1. MATCHes the PREDICTED_TREATS edge pattern.
    #   2. Filters by drug name (if provided, case-insensitive).
    #   3. Filters by disease name (if provided, case-insensitive).
    #   4. Filters by min_score.
    #   5. Orders by score descending (top predictions first).
    #   6. Limits to ``limit``.
    #
    # The toLower() call makes the match case-insensitive (the GT
    # service stores drug/disease names as the model's training-graph
    # names, which are typically lowercase).
    cypher_query = """
    MATCH (d:Drug)-[r:PREDICTED_TREATS]->(dis:Disease)
    WHERE ($drug IS NULL OR toLower(d.name) = toLower($drug))
      AND ($disease IS NULL OR toLower(dis.name) = toLower($disease))
      AND r.score >= $min_score
    RETURN d.name AS drug, dis.name AS disease, r.score AS score,
           r.confidence AS confidence, r.model_version AS model_version,
           r.generated_at AS generated_at
    ORDER BY r.score DESC
    LIMIT $limit
    """
    try:
        with driver.session() as session:
            result = session.run(
                cypher_query,
                drug=drug,
                disease=disease,
                min_score=float(min_score),
                limit=int(limit),
            )
            predictions = []
            for record in result:
                predictions.append({
                    "drug": record["drug"],
                    "disease": record["disease"],
                    "score": float(record["score"]),
                    "confidence": float(record["confidence"]),
                    "model_version": record["model_version"],
                    "generated_at": record["generated_at"],
                })
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.error(
            "TM7-v127 (Task 7.5): /predictions Neo4j query failed (%s). "
            "Returning 502 to the caller. Check Neo4j server availability.",
            exc,
        )
        raise HTTPException(
            status_code=502,
            detail=f"Neo4j query failed: {exc}",
        )
    finally:
        try:
            driver.close()
        except Exception as close_exc:
            logger.warning(
                "TM7-v127 (Task 7.5): driver.close() failed after /predictions (%s).",
                close_exc,
            )

    return {
        "predictions": predictions,
        "count": len(predictions),
        "source": "neo4j",
        "neo4j_configured": True,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("GT_SERVICE_PORT", "8003"))
    host = os.environ.get("GT_SERVICE_HOST", "0.0.0.0")
    logger.info("Starting Phase 3 GT Service on %s:%d", host, port)
    uvicorn.run(app, host=host, port=port)
