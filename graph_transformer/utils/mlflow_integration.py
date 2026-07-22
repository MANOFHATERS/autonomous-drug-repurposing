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
        # P3-010: keep the ActiveRun object alive so the run is not
        # ended prematurely by garbage collection (in current mlflow
        # versions, ActiveRun.__exit__ ends the run).
        self._active_run: Optional[Any] = None
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
            # P3-010 ROOT FIX (Teammate 7 v142, hostile-auditor RED TEAM):
            # The previous code stored the ActiveRun OBJECT returned by
            # ``mlflow.start_run()`` into ``self._run_id``. The attribute
            # name ``_run_id`` strongly implies a STRING, and downstream
            # callers (notably ``register_model`` below, after the P3-010
            # fix) need the string run ID to build a ``runs:/<run_id>/...``
            # URI. Storing the ActiveRun object meant any string
            # formatting like ``f"runs:/{self._run_id}/artifacts/..."``
            # would produce ``runs:/<ActiveRun ...>/artifacts/...`` -- a
            # malformed URI that MLflow rejects with a confusing 400.
            #
            # ROOT FIX: capture the ActiveRun object, extract its
            # ``.info.run_id`` string, and store the STRING in
            # ``self._run_id``. Keep the ActiveRun object alive in
            # ``self._active_run`` so the run is not ended prematurely
            # (in current mlflow versions ActiveRun.__exit__ ends the
            # run, but storing the reference prevents garbage collection).
            active_run = _mlflow.start_run(run_name=run_name)  # type: ignore[union-attr]
            self._active_run = active_run
            # ActiveRun.info.run_id is the canonical string run ID.
            if active_run is not None and hasattr(active_run, "info") and active_run.info is not None:
                self._run_id = str(active_run.info.run_id)
            else:
                # Defensive fallback: query mlflow.active_run() if the
                # returned object lacks .info (rare, but seen in some
                # mlflow client wrappers).
                _active = _mlflow.active_run()  # type: ignore[union-attr]
                if _active is not None and hasattr(_active, "info") and _active.info is not None:
                    self._run_id = str(_active.info.run_id)
                else:
                    logger.warning(
                        "MLflow start_run returned no usable run_id; "
                        "register_model will fall back to file:// URI."
                    )
                    self._run_id = None
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

        P3-010 ROOT FIX (Teammate 7 v142, hostile-auditor RED TEAM):
        The previous implementation had TWO compound bugs:

          1. The ``stage`` parameter was accepted by the method signature
             but NEVER passed to ``mlflow.register_model``. MLflow's
             ``register_model`` API does not accept a ``stage`` argument
             directly (stages are set via ``transition_model_version_stage``
             AFTER registration), but the previous log line printed
             ``stage=%s`` as if it were applied. The trainer's
             ``save_checkpoint`` calls ``register_model(stage="Staging")``
             expecting the model to be in Staging -- it was registered
             with NO stage. The team lead's workflow "transition Staging
             -> Production after verifying AUC > 0.85" could not fire
             because the model never entered Staging.

          2. ``model_uri=f"file://{local_checkpoint_path}"`` used a
             ``file://`` URI. MLflow's ``register_model`` expects an
             ``mlflow-artifact:/`` URI (from a prior ``log_artifact``
             call) or a ``runs:/<run_id>/artifacts/<filename>`` URI. A
             bare ``file://`` URI only works if the MLflow tracking
             server has direct filesystem access to the local path --
             true only in single-node dev deployments. In distributed
             production (MLflow server on a different host than the
             Airflow worker), ``register_model`` fails with "file not
             found".

        ROOT FIX:
          1. Build the model URI as ``runs:/{run_id}/artifacts/{basename}``
             using the run_id captured by ``start_run`` (the P3-010 fix
             to ``start_run`` stores the STRING run_id, not the ActiveRun
             object). This URI works in distributed deployments because
             it points to the MLflow run's artifact store, NOT the local
             filesystem.
          2. Capture the ``ModelVersion`` object returned by
             ``mlflow.register_model`` and call
             ``mlflow.transition_model_version_stage(name, version, stage)``
             to actually move the newly-registered version into the
             requested stage. The transition is best-effort: if it fails
             (e.g., the stage name is invalid or the registry backend
             does not support stages), the model is still registered
             (with no stage) and a WARNING is logged -- this is the
             correct trade-off because a registered-but-unstaged model
             is still usable; a failed registration is not.
          3. Fall back to the legacy ``file://`` URI ONLY when no run_id
             is available (e.g., ``start_run`` failed or was never
             called). This preserves backward compatibility with
             pre-P3-010 callers that did not call ``start_run`` first,
             while making the unsafe path EXPLICIT and AUDITABLE in the
             logs.

        Args:
            local_checkpoint_path: Local filesystem path to the .pt
                checkpoint. The caller MUST have called ``log_artifact``
                with this path first, so the artifact is available in
                the MLflow run's artifact store at ``artifacts/<basename>``.
            model_name: Registered model name in the MLflow Model
                Registry (e.g., "drugos_gt").
            stage: Target stage for the newly-registered version. One
                of "Staging", "Production", "Archived". The stage is
                applied via ``transition_model_version_stage`` AFTER
                registration.
            tags: Optional tags to set on the model version (e.g.,
                {"val_auc": "0.87", "best_epoch": "12"}).
        """
        if not self._active:
            return
        # P3-010: build the model URI. Prefer the runs:/ URI (works in
        # distributed deployments); fall back to file:// ONLY when no
        # run_id is available (legacy callers).
        #
        # MLflow URI format: ``runs:/<run_id>/<artifact_path>`` where
        # <artifact_path> is the path WITHIN the run's artifact store.
        # ``mlflow.log_artifact(local_path)`` (with no artifact_path
        # argument) puts the file at the ROOT of the artifact store, so
        # the URI is ``runs:/<run_id>/<basename>`` (NOT
        # ``runs:/<run_id>/artifacts/<basename>`` -- the ``artifacts/``
        # prefix is WRONG and MLflow rejects it with "Unable to find a
        # logged_model with artifact_path artifacts/<basename>").
        checkpoint_basename = os.path.basename(local_checkpoint_path)
        if self._run_id:
            model_uri = f"runs:/{self._run_id}/{checkpoint_basename}"
        else:
            logger.warning(
                "MLflow register_model: no run_id available (start_run "
                "not called or failed). Falling back to file:// URI for "
                "checkpoint %s. This URI ONLY works when the MLflow "
                "tracking server has direct filesystem access to the "
                "local path -- distributed deployments will fail with "
                "'file not found'. Call start_run() before register_model() "
                "to use the production-safe runs:/ URI.",
                local_checkpoint_path,
            )
            model_uri = f"file://{local_checkpoint_path}"
        try:
            # P3-010: capture the ModelVersion object so we can extract
            # its .version integer and call transition_model_version_stage.
            model_version = _mlflow.register_model(  # type: ignore[union-attr]
                model_uri=model_uri,
                name=model_name,
                tags=tags,
            )
            # Extract the version integer. MLflow's register_model returns
            # a ModelVersion object with a .version attribute (string in
            # some versions, int in others -- coerce to string for the
            # transition API which accepts both).
            version_str: Optional[str] = None
            if model_version is not None:
                if hasattr(model_version, "version") and model_version.version is not None:
                    version_str = str(model_version.version)
                elif hasattr(model_version, "version_info") and model_version.version_info is not None:
                    # Newer MLflow versions return a ModelVersionInfo with
                    # version_info instead of version. Be defensive.
                    _vi = model_version.version_info
                    if hasattr(_vi, "version") and _vi.version is not None:
                        version_str = str(_vi.version)
            # P3-010: actually transition the newly-registered version
            # to the requested stage. This is the fix for the compound
            # bug where ``stage`` was accepted but never applied.
            #
            # P3-010 v142.1 (hostile-auditor follow-up): the original v142
            # fix called ``_mlflow.transition_model_version_stage(...)`` --
            # but this top-level function was REMOVED in MLflow 3.x. In
            # MLflow 3.x, the transition API is only on ``MlflowClient``:
            #     from mlflow.tracking import MlflowClient
            #     MlflowClient().transition_model_version_stage(name, version, stage)
            # The original v122 fix claimed to apply the stage but never
            # did (it accepted the parameter and only logged it). The
            # v142 fix tried to call the top-level function -- which
            # works on MLflow < 3.0 but raises AttributeError on 3.x.
            # The v142.1 fix tries the top-level function first (for
            # backward compat with MLflow < 3.0) and falls back to
            # MlflowClient on AttributeError or ImportError. This makes
            # the fix work across ALL MLflow versions (1.x, 2.x, 3.x).
            if version_str is not None:
                try:
                    _transitioned = False
                    # Path 1: top-level mlflow.transition_model_version_stage
                    # (MLflow < 3.0). Use getattr to avoid AttributeError
                    # on MLflow 3.x where the function does not exist.
                    _top_level_transition = getattr(
                        _mlflow, "transition_model_version_stage", None
                    )
                    if callable(_top_level_transition):
                        _top_level_transition(
                            name=model_name,
                            version=version_str,
                            stage=stage,
                            # archive_existing_versions=True ensures only ONE
                            # version is in the target stage at a time. This
                            # matches the team lead's workflow: "the latest
                            # Staging version replaces the previous one."
                            # Older versions are auto-Archived.
                            archive_existing_versions=True,
                        )
                        _transitioned = True
                    else:
                        # Path 2: MlflowClient.transition_model_version_stage
                        # (MLflow 3.x+). The function was moved from the
                        # top-level mlflow module to the MlflowClient class
                        # in MLflow 3.0. We instantiate a client and call
                        # the method on it.
                        try:
                            from mlflow.tracking import MlflowClient as _MlflowClient
                            _client = _MlflowClient()
                            _client.transition_model_version_stage(
                                name=model_name,
                                version=version_str,
                                stage=stage,
                                archive_existing_versions=True,
                            )
                            _transitioned = True
                        except ImportError as ie:
                            logger.warning(
                                "MLflow: could not import MlflowClient (%s). "
                                "The stage transition API is unavailable. "
                                "MLflow version: %s.",
                                ie, getattr(_mlflow, "__version__", "unknown"),
                            )
                    if _transitioned:
                        logger.info(
                            "MLflow: registered model %s version %s and "
                            "transitioned to stage=%s (tags=%s, uri=%s).",
                            model_name, version_str, stage, tags, model_uri,
                        )
                except Exception as stage_exc:
                    # The transition failed -- the model is registered
                    # (with no stage) but not transitioned. Log a WARNING
                    # so operators see the model needs manual staging in
                    # the MLflow UI. Do NOT re-raise: a registered-but-
                    # unstaged model is still usable; failing the whole
                    # training run because of a staging transition error
                    # would be a worse outcome.
                    logger.warning(
                        "MLflow: registered model %s version %s but "
                        "FAILED to transition to stage=%s (%s). The model "
                        "is in the Model Registry with NO stage. An "
                        "operator must manually transition it to %s in "
                        "the MLflow UI.",
                        model_name, version_str, stage, stage_exc, stage,
                    )
            else:
                # register_model returned without a version (should not
                # happen for a successful call, but be defensive). Log
                # at INFO so the operator knows the stage was not applied.
                logger.info(
                    "MLflow: registered model %s (stage=%s requested but "
                    "register_model returned no version; cannot transition). "
                    "tags=%s, uri=%s.",
                    model_name, stage, tags, model_uri,
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
            # P3-010: also release the ActiveRun reference so the run can
            # be cleanly ended (without this, the ActiveRun object is kept
            # alive in self._active_run, which may prevent mlflow's
            # internal cleanup from finalizing the run's status).
            self._active_run = None


def get_git_commit() -> str:
    """Public accessor for the git commit hash (used by the bridge too)."""
    return _git_commit()
