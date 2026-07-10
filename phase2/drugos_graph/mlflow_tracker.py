"""DrugOS Graph Module — MLflow Experiment Tracker
====================================================
Logs training metrics, model parameters, and artifacts to MLflow
for experiment tracking and reproducibility.
"""

import logging
from typing import Any, Dict, Optional

from .config import ensure_dirs

logger = logging.getLogger(__name__)


class MLflowTracker:
    """Tracks experiments using MLflow.

    Falls back to local file logging if MLflow is not installed.
    """

    def __init__(self, experiment_name: str = "DrugOS_Week2", tracking_uri: Optional[str] = None):
        self.experiment_name = experiment_name
        self.tracking_uri = tracking_uri
        self.mlflow = None
        self.run = None
        self._local_log = []

        try:
            import mlflow
            self.mlflow = mlflow
            if tracking_uri:
                mlflow.set_tracking_uri(tracking_uri)
            mlflow.set_experiment(experiment_name)
            logger.info(f"MLflow initialized: experiment={experiment_name}")
        except ImportError:
            logger.warning(
                "mlflow not installed — using local file logging. "
                "Install with: pip install mlflow"
            )
        except Exception as e:
            logger.warning(f"MLflow initialization failed: {e}")
            self.mlflow = None

    def start_run(self, run_name: str = "default") -> "MLflowTracker":
        """Start a new MLflow run.

        v40 ROOT FIX (P2 #4): the previous ``start_run`` method was NOT
        paired with ``end_run`` if the caller used it outside the context
        manager (``with MLflowTracker() as t: ...``). If the body raised
        before ``end_run`` was called, the MLflow run was left dangling
        (orphaned run in the MLflow tracking server). The fix:
        ``start_run`` now returns ``self`` so it can be used as a context
        manager directly: ``with tracker.start_run("name"): ...``. The
        ``__exit__`` method calls ``end_run`` regardless of whether an
        exception was raised, ensuring the run is always properly closed.

        FIX-P2-P2-8: correct the return type annotation from ``None`` to
        ``"MLflowTracker"`` so static analysers and IDEs match the
        ``return self`` statement and the documented context-manager
        usage.
        """
        if self.mlflow:
            self.run = self.mlflow.start_run(run_name=run_name)
        logger.info(f"Started experiment run: {run_name}")
        return self  # v40: enable ``with tracker.start_run() as t:`` usage

    def log_params(self, params: Dict[str, Any]) -> None:
        """Log hyperparameters."""
        # v35 ROOT FIX (L-1): the previous code appended to
        # ``self._local_log`` UNCONDITIONALLY — even when MLflow was
        # active and had already logged the params. For long runs
        # (100+ epochs × 5+ params), this caused ``_local_log`` to grow
        # unbounded (500+ entries). The fix only appends when MLflow is
        # NOT active (i.e., the local log IS the canonical record).
        if self.mlflow and self.run:
            self.mlflow.log_params(params)
        else:
            self._local_log.append({"type": "params", "data": params})
        logger.info(f"Logged params: {params}")

    def log_metrics(self, metrics: Dict[str, float], step: int = 0) -> None:
        """Log training metrics."""
        # v35 ROOT FIX (L-1): see ``log_params`` — only append to the
        # local log when MLflow is NOT active.
        if self.mlflow and self.run:
            self.mlflow.log_metrics(metrics, step=step)
        else:
            self._local_log.append({"type": "metrics", "data": metrics, "step": step})

    def log_artifact(self, path: str) -> None:
        """Log a file artifact."""
        # v35 ROOT FIX (L-1): see ``log_params`` — only append to the
        # local log when MLflow is NOT active.
        if self.mlflow and self.run:
            self.mlflow.log_artifact(path)
        else:
            self._local_log.append({"type": "artifact", "path": path})

    def set_tag(self, key: str, value: Any) -> None:
        """Set a tag on the current MLflow run.

        v63 ROOT FIX (P2C-003+016 — ChEMBERTa silent-disable cascade):
        the audit required that when ChEMBERTa is disabled (any of the
        3 fallback layers fires), the pipeline MUST log
        ``CHEMBERTA_DISABLED=true`` to MLflow so operators monitoring
        the MLflow dashboard can immediately see that the Graph
        Transformer trained on random Xavier features (not molecular
        structure). Without this tag, the only signal was a buried
        WARNING log that production dashboards filtered out — exactly
        the silent-degradation the audit named.
        """
        if self.mlflow and self.run:
            self.mlflow.set_tag(key, str(value))
        else:
            self._local_log.append({"type": "tag", "key": key, "value": value})
        logger.info(f"Set tag: {key}={value}")

    def end_run(self) -> None:
        """End the current MLflow run."""
        if self.mlflow and self.run:
            self.mlflow.end_run()
            self.run = None
        logger.info("Experiment run ended")

    def get_local_log(self) -> list:
        """Return local log (for fallback when MLflow is unavailable)."""
        return self._local_log

    def __enter__(self):
        # v40 ROOT FIX (P2 #4): start_run now returns self, so __enter__
        # can delegate to it. This ensures the run is always started
        # before the body executes, and __exit__ always ends it.
        return self.start_run()

    def __exit__(self, exc_type, exc_val, exc_tb):
        # v40 ROOT FIX (P2 #4): always call end_run, even on exception.
        # The previous code was correct (it always called end_run), but
        # the issue was that start_run could be called OUTSIDE the
        # context manager (``tracker.start_run()`` without ``with``),
        # leaving the run dangling if an exception occurred before
        # end_run was manually called. The fix makes start_run return
        # self so it can be used as a context manager directly.
        self.end_run()

    # v43 ROOT FIX (P2 — no __del__ for dangling runs): if the caller
    # uses start_run() without a context manager AND forgets to call
    # end_run(), the MLflow run is left dangling. __del__ is a safety
    # net that calls end_run() when the tracker is garbage-collected.
    def __del__(self):
        try:
            if self.mlflow and self.run:
                self.end_run()
        except Exception:
            pass  # __del__ must never raise
        # v72 ROOT FIX (P2C-019): removed ``return False``. __del__ is a
        # destructor whose return value is ignored by the Python runtime.
        # The previous ``return False`` was dead code that could mislead a
        # maintainer into thinking the return value mattered. The
        # convention is to return None (implicitly) from __del__.
