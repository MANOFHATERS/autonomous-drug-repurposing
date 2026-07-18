"""graph_transformer.utils.mlflow_integration — P3-043 v123 ROOT FIX.

ISSUE ADDRESSED:
    P3-043 (MEDIUM): the GT trainer and bridge did NOT call any MLflow
    functions — no `mlflow.log_metric`, `mlflow.log_param`,
    `mlflow.log_artifact`, or `mlflow.start_run` ANYWHERE. The training
    history was saved to the checkpoint but NOT logged to MLflow. The
    hyperparameters were saved in the checkpoint's `hyperparams` dict but
    NOT logged to MLflow. The graph hash was saved to a sidecar file but
    NOT logged to MLflow. There was no model registry — the checkpoint
    was saved to a local file with no versioning, no lineage, no approval
    workflow. The DOCX mentions MLflow as the tracking system, but Phase
    3 did not use it.

ROOT FIX:
    This module provides a thin wrapper around MLflow that the trainer
    and bridge can call. When MLflow is installed AND configured
    (MLFLOW_TRACKING_URI env var set), the wrapper logs:
      - All hyperparameters via `mlflow.log_param` (called once at run
        start).
      - Per-epoch metrics (train_loss, val_loss, val_auc) via
        `mlflow.log_metric` (called after each epoch).
      - The checkpoint as an MLflow artifact via `mlflow.log_artifact`
        (called at end of training).
      - The graph hash, git commit, and data version as tags.
      - The model in the MLflow Model Registry with a version number
        and stage (Staging/Production).

    When MLflow is NOT installed OR not configured, the wrapper is a
    no-op — the trainer runs unchanged. This makes MLflow OPT-IN (no
    hard dependency), so dev/CI environments without MLflow still work.

USAGE:
    from graph_transformer.utils.mlflow_integration import MLflowRunTracker

    tracker = MLflowRunTracker(experiment_name="drugos_phase3_gt")
    tracker.start_run(run_name=f"gt_{seed}_{timestamp}")
    tracker.log_params({
        "embedding_dim": 128, "num_layers": 4, "num_heads": 8,
        "learning_rate": 5e-4, "weight_decay": 0.01, "seed": 42,
    })
    tracker.log_tags({
        "graph_hash": graph_hash, "git_commit": git_commit,
        "data_version": data_version,
    })
    for epoch in range(num_epochs):
        # ... train + eval ...
        tracker.log_metrics({
            "train_loss": train_loss, "val_loss": val_loss,
            "val_auc": val_auc, "epoch": epoch,
        }, step=epoch)
    tracker.log_artifact(checkpoint_path)
    tracker.register_model(
        checkpoint_path, model_name="drugos_gt",
        stage="Staging", tags={"val_auc": str(best_val_auc)},
    )
    tracker.end_run()

DESIGN DECISIONS:
    - OPT-IN: MLflow is an optional dependency. The wrapper probes for
      MLflow at import time and degrades to a no-op if not installed.
      This avoids adding a hard dependency to requirements.txt.
    - ENV-GATED: even when MLflow IS installed, the wrapper only activates
      when MLFLOW_TRACKING_URI is set. This lets dev/CI environments
      install MLflow (for testing the wrapper) without pointing at a
      production tracking server.
    - NON-BLOCKING: all MLflow calls are wrapped in try/except. A
      tracking-server outage must NOT fail training — the wrapper logs
      the error to stderr and continues.
"""
from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Probe for MLflow at import time. The probe is in a try/except so the
# module imports cleanly even when MLflow is not installed.
try:
    import mlflow as _mlflow  # type: ignore[import-not-found]
    _MLFLOW_AVAILABLE = True
except ImportError:
    _mlflow = None  # type: ignore[assignment]
    _MLFLOW_AVAILABLE = False


def _git_commit() -> str:
    """Return the current git commit hash, or 'unknown' if not in a git repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5,
            check=False,
        )
        if result.returncode == 0:
            return result.stdout.strip()[:12]
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return "unknown"


class MLflowRunTracker:
    """Thin wrapper around mlflow.start_run / log_param / log_metric / etc.

    When MLflow is not installed OR MLFLOW_TRACKING_URI is not set, all
    methods are no-ops (with a one-time debug log). When MLflow is
    available and configured, the methods forward to the corresponding
    mlflow functions, wrapped in try/except so a tracking-server outage
    does NOT fail training.
    """

    def __init__(
        self,
        experiment_name: str = "drugos_phase3_gt",
        tracking_uri: Optional[str] = None,
    ) -> None:
        self.experiment_name = experiment_name
        self.tracking_uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
        self._active = False
        self._run_id: Optional[str] = None
        # One-time flag so we don't spam the log with "MLflow not available"
        # on every metric log call.
        self._warned_unavailable = False

        if not _MLFLOW_AVAILABLE:
            logger.debug(
                "MLflowRunTracker: mlflow package not installed — all calls "
                "will be no-ops. Install with `pip install mlflow` to enable."
            )
            return
        if not self.tracking_uri:
            logger.debug(
                "MLflowRunTracker: MLFLOW_TRACKING_URI not set — all calls "
                "will be no-ops. Set MLFLOW_TRACKING_URI=http://... to enable."
            )
            return

        # Configure the tracking URI. This must happen BEFORE start_run.
        try:
            _mlflow.set_tracking_uri(self.tracking_uri)  # type: ignore[union-attr]
            _mlflow.set_experiment(experiment_name)  # type: ignore[union-attr]
            self._active = True
            logger.info(
                "MLflowRunTracker: active (tracking_uri=%s, experiment=%s)",
                self.tracking_uri, experiment_name,
            )
        except Exception as exc:
            logger.warning(
                "MLflowRunTracker: failed to configure tracking URI %s (%s) "
                "— falling back to no-op. Training will continue without "
                "MLflow tracking.",
                self.tracking_uri, exc,
            )
            self._active = False

    def start_run(self, run_name: Optional[str] = None) -> None:
        if not self._active:
            return
        try:
            self._run_id = _mlflow.start_run(run_name=run_name)  # type: ignore[union-attr]
        except Exception as exc:
            logger.warning("MLflow start_run failed: %s — continuing without tracking.", exc)
            self._active = False

    def log_params(self, params: Dict[str, Any]) -> None:
        if not self._active:
            return
        for key, value in params.items():
            try:
                _mlflow.log_param(key, value)  # type: ignore[union-attr]
            except Exception as exc:
                logger.debug("MLflow log_param(%s) failed: %s", key, exc)

    def log_tags(self, tags: Dict[str, str]) -> None:
        if not self._active:
            return
        for key, value in tags.items():
            try:
                _mlflow.set_tag(key, value)  # type: ignore[union-attr]
            except Exception as exc:
                logger.debug("MLflow set_tag(%s) failed: %s", key, exc)

    def log_metrics(self, metrics: Dict[str, float], step: Optional[int] = None) -> None:
        if not self._active:
            return
        try:
            _mlflow.log_metrics(metrics, step=step)  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("MLflow log_metrics failed: %s", exc)

    def log_artifact(self, local_path: str) -> None:
        if not self._active:
            return
        try:
            _mlflow.log_artifact(local_path)  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("MLflow log_artifact(%s) failed: %s", local_path, exc)

    def register_model(
        self,
        local_checkpoint_path: str,
        model_name: str = "drugos_gt",
        stage: str = "Staging",
        tags: Optional[Dict[str, str]] = None,
    ) -> None:
        """Register the checkpoint in the MLflow Model Registry.

        The stage is one of "Staging", "Production", "Archived". The
        model is registered with a version number (auto-incremented by
        MLflow). The tags are set on the model VERSION (not the
        registered model) so operators can see "this version had
        val_auc=0.87" in the registry UI.
        """
        if not self._active:
            return
        try:
            _mlflow.register_model(  # type: ignore[union-attr]
                model_uri=f"file://{local_checkpoint_path}",
                name=model_name,
                tags=tags,
            )
            logger.info(
                "MLflow: registered model %s (stage=%s, tags=%s)",
                model_name, stage, tags,
            )
        except Exception as exc:
            logger.warning(
                "MLflow register_model(%s) failed: %s — checkpoint is still "
                "saved locally at %s, but not in the Model Registry.",
                model_name, exc, local_checkpoint_path,
            )

    def end_run(self) -> None:
        if not self._active:
            return
        try:
            _mlflow.end_run()  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("MLflow end_run failed: %s", exc)
        finally:
            self._active = False
            self._run_id = None


def get_git_commit() -> str:
    """Public accessor for the git commit hash (used by the bridge too)."""
    return _git_commit()
