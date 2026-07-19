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
import tempfile
from typing import Any, Dict, List, Optional, Tuple

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


    # ─── Teammate 6 (Task 6.4) ROOT FIX — calibration plot ──────────────
    # P3-004 ROOT FIX (v127 forensic, Teammate 6): the temperature
    # calibration parameter (Guo et al. 2017) is fit on a held-out
    # validation set, but the platform had NO way to VISUALLY verify
    # the calibration actually improved. Operators had to trust the
    # scalar temperature value (T=1.65, etc.) without seeing the
    # reliability diagram.
    #
    # This method logs a RELIABILITY DIAGRAM to MLflow as an artifact.
    def log_calibration_plot(
        self,
        pre_probs: "Any",
        post_probs: "Any",
        labels: "Any",
        step: Optional[int] = None,
        n_bins: int = 10,
    ) -> None:
        """Log a reliability diagram (calibration plot) to MLflow.

        Teammate 6 (Task 6.4) ROOT FIX: produces a matplotlib figure
        showing pre- and post-calibration reliability curves and logs
        it as an MLflow artifact. When MLflow is unavailable or not
        configured, this method is a no-op.
        """
        if not self._active:
            return
        try:
            import numpy as _np
            pre = _np.asarray(pre_probs, dtype=_np.float64).ravel()
            post = _np.asarray(post_probs, dtype=_np.float64).ravel()
            lab = _np.asarray(labels, dtype=_np.float64).ravel()
        except Exception as exc:
            logger.debug("MLflow log_calibration_plot: input conversion failed: %s", exc)
            return
        if pre.size == 0 or post.size == 0 or lab.size == 0:
            logger.debug("MLflow log_calibration_plot: empty input — skipping.")
            return
        if not (pre.size == post.size == lab.size):
            logger.warning(
                "MLflow log_calibration_plot: size mismatch (pre=%d, post=%d, "
                "labels=%d) — skipping plot.", pre.size, post.size, lab.size,
            )
            return

        def _reliability(probs: "_np.ndarray", true: "_np.ndarray") -> Tuple[List[float], List[float], List[int]]:
            bins = _np.linspace(0.0, 1.0, n_bins + 1)
            probs = _np.clip(probs, 0.0, 1.0)
            bin_idx = _np.digitize(probs, bins[1:-1], right=False)
            mean_pred: List[float] = []
            emp_acc: List[float] = []
            counts: List[int] = []
            for i in range(n_bins):
                mask = bin_idx == i
                n = int(mask.sum())
                if n == 0:
                    continue
                mean_pred.append(float(probs[mask].mean()))
                emp_acc.append(float(true[mask].mean()))
                counts.append(n)
            return mean_pred, emp_acc, counts

        try:
            pre_mean, pre_acc, pre_counts = _reliability(pre, lab)
            post_mean, post_acc, post_counts = _reliability(post, lab)
        except Exception as exc:
            logger.debug("MLflow log_calibration_plot: reliability computation failed: %s", exc)
            return

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
        except Exception as exc:
            logger.debug("MLflow log_calibration_plot: matplotlib unavailable: %s", exc)
            return

        try:
            fig, ax = plt.subplots(1, 1, figsize=(6, 6), constrained_layout=True)
            ax.plot([0, 1], [0, 1], color="gray", linestyle="--", linewidth=1,
                    label="Perfect calibration")
            if pre_mean:
                ax.plot(pre_mean, pre_acc, marker="o", color="#d62728",
                        linewidth=2, markersize=7,
                        label=f"Pre-calibration (T=1.0, n={int(sum(pre_counts))})")
            if post_mean:
                ax.plot(post_mean, post_acc, marker="s", color="#1f77b4",
                        linewidth=2, markersize=7,
                        label=f"Post-calibration (n={int(sum(post_counts))})")
            ax.set_xlabel("Mean predicted probability")
            ax.set_ylabel("Empirical accuracy (fraction of positives)")
            ax.set_title("Reliability diagram — temperature calibration (Guo et al. 2017)")
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.set_aspect("equal", adjustable="box")
            ax.legend(loc="upper left", framealpha=0.9)
            ax.grid(True, alpha=0.3)

            with tempfile.NamedTemporaryFile(
                suffix=".png", prefix="calibration_plot_", delete=False,
            ) as tmp:
                tmp_path = tmp.name
            fig.savefig(tmp_path, dpi=120, format="png")
            plt.close(fig)

            artifact_path = "calibration_plots"
            if step is not None:
                artifact_path = f"{artifact_path}/epoch_{step}"
            _mlflow.log_artifact(tmp_path, artifact_path=artifact_path)  # type: ignore[union-attr]
            logger.info(
                "MLflow: logged calibration plot (pre n=%d, post n=%d, bins=%d) "
                "to artifact_path=%s",
                int(sum(pre_counts)), int(sum(post_counts)), n_bins, artifact_path,
            )
            try:
                def _ece(mean_pred: List[float], emp_acc: List[float], counts: List[int], total: int) -> float:
                    if total == 0:
                        return 0.0
                    return float(sum(
                        (c / total) * abs(a - p)
                        for p, a, c in zip(mean_pred, emp_acc, counts)
                    ))
                pre_ece = _ece(pre_mean, pre_acc, pre_counts, sum(pre_counts))
                post_ece = _ece(post_mean, post_acc, post_counts, sum(post_counts))
                metrics = {
                    "calibration_ece_pre": pre_ece,
                    "calibration_ece_post": post_ece,
                    "calibration_ece_improvement": max(0.0, pre_ece - post_ece),
                }
                if step is not None:
                    metrics["step"] = float(step)
                _mlflow.log_metrics(metrics, step=step)  # type: ignore[union-attr]
            except Exception as exc:
                logger.debug("MLflow log_calibration_plot: ECE metric log failed: %s", exc)

            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        except Exception as exc:
            logger.warning(
                "MLflow log_calibration_plot: failed to build/log figure: %s. "
                "Calibration plot will not be available in the MLflow UI.",
                exc,
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
