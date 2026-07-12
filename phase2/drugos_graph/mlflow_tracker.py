"""DrugOS Graph Module — MLflow Experiment Tracker
====================================================
Logs training metrics, model parameters, and artifacts to MLflow
for experiment tracking and reproducibility.

P2-014 ROOT FIX (deterministic shutdown via atexit):
    The previous ``__del__`` called ``end_run()`` at garbage-
    collection time, which is NON-DETERMINISTIC. For long-lived
    tracker instances held by pipeline singletons, ``__del__`` may
    not fire until interpreter shutdown — by then, the MLflow
    tracking server may be unreachable (network torn down, HTTP
    client closed), and ``end_run`` silently fails (caught by the
    ``except`` clause). The run is left dangling in the MLflow UI
    as "ACTIVE" forever.

    ROOT FIX: register ``close()`` with ``atexit`` during
    ``__init__``. ``atexit`` runs BEFORE the network is torn down
    (during normal interpreter shutdown), so the MLflow run is
    ended deterministically. ``__exit__`` now calls ``close()``
    (not ``end_run``) so the defensive try/except applies.
    ``__del__`` remains as a last-resort safety net but is
    documented as best-effort only — operators should use
    ``with MLflowTracker() as t:`` or call ``close()`` explicitly.
"""

import atexit
import logging
from typing import Any, Dict, Optional

from .config import ensure_dirs

logger = logging.getLogger(__name__)


class MLflowTracker:
    """Tracks experiments using MLflow.

    Falls back to local file logging if MLflow is not installed.

    P2-014: callers should use ``with MLflowTracker() as t:`` or
    call ``close()`` explicitly. ``__del__`` is a best-effort
    safety net only; ``atexit`` handles the deterministic shutdown.
    """

    def __init__(self, experiment_name: str = "DrugOS_Week2", tracking_uri: Optional[str] = None):
        self.experiment_name = experiment_name
        self.tracking_uri = tracking_uri
        self.mlflow = None
        self.run = None
        self._local_log = []
        # P2-014 ROOT FIX: register close() with atexit so the
        # MLflow run is ended deterministically at interpreter
        # shutdown, BEFORE the network is torn down. ``atexit``
        # runs in reverse-registration order, so this fires after
        # any upstream atexit handlers that may still need the
        # tracker. ``close()`` is idempotent (safe to call multiple
        # times — see the ``self.run = None`` reset in ``end_run``).
        self._closed: bool = False
        atexit.register(self._atexit_close)

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

    def _atexit_close(self) -> None:
        """P2-014: atexit-registered close handler.

        Swallows ALL exceptions so interpreter shutdown never
        raises (atexit handlers that raise produce noisy
        tracebacks at shutdown and can mask the real exit code).
        """
        try:
            self.close()
        except Exception:
            pass

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
        # P2-014 ROOT FIX: call close() (not end_run) so the
        # defensive try/except in close() applies AND the
        # idempotency guard (_closed) prevents double-end. The
        # previous code called end_run() directly which (a) had no
        # try/except, so an exception during end_run would propagate
        # out of the with-block (masking the original exception),
        # and (b) could be re-invoked by atexit / __del__, producing
        # "Run not active" warnings in the MLflow UI.
        self.close()

    # v100 ROOT FIX (BUG P2-038 — dangling MLflow run via __del__):
    # The previous __del__ called end_run() at garbage-collection time,
    # which is NON-DETERMINISTIC. If the MLflowTracker was held by a
    # long-lived object (e.g. the pipeline's singleton logger), __del__
    # might not fire until interpreter shutdown — by then, the MLflow
    # tracking server may be unreachable, and end_run silently fails,
    # leaving the run dangling in the MLflow UI.
    #
    # P2-014 ROOT FIX (deterministic shutdown): the atexit handler
    # registered in __init__ now handles deterministic shutdown.
    # close() is idempotent (``_closed`` flag) so the atexit handler
    # + explicit close() + __del__ can all fire without producing
    # "Run not active" warnings. ``__del__`` remains as a last-resort
    # safety net for callers that forget to use the context manager
    # AND somehow bypass atexit (e.g. os._exit).
    def close(self) -> None:
        """Deterministically end the MLflow run.

        v100 ROOT FIX (BUG P2-038): callers should invoke close() in a
        try/finally block (or register with atexit) to ensure the MLflow
        run is ended BEFORE the tracking server becomes unreachable.
        __del__ is a fallback for callers that forget — but __del__ is
        non-deterministic (fires at GC time, which may be after the
        tracking server is gone). close() is the only path that
        GUARANTEES end_run succeeds.

        P2-014 ROOT FIX: ``close()`` is now IDEMPOTENT — the
        ``_closed`` flag prevents double-end. The atexit handler
        registered in ``__init__`` calls ``close()`` at interpreter
        shutdown, so callers using the context manager
        (``with MLflowTracker() as t:``) AND the atexit handler can
        BOTH fire without producing "Run not active" warnings.
        """
        # P2-014: idempotency guard. ``end_run`` resets ``self.run``
        # to None, but if multiple shutdown paths fire (atexit +
        # explicit close + __del__), the second call would call
        # ``mlflow.end_run()`` with no active run, producing a
        # noisy "Run not active" warning in the MLflow UI. The
        # ``_closed`` flag is set on the FIRST close call; subsequent
        # calls are no-ops.
        if self._closed:
            return
        self._closed = True
        try:
            self.end_run()
        except Exception:
            pass

    # v43 ROOT FIX (P2 — no __del__ for dangling runs): if the caller
    # uses start_run() without a context manager AND forgets to call
    # end_run() or close(), the MLflow run is left dangling. __del__ is
    # a last-resort safety net that calls end_run() when the tracker is
    # garbage-collected. v100 P2-038: callers should prefer close() —
    # __del__ is non-deterministic and may fire after the tracking
    # server is unreachable.
    # P2-014 ROOT FIX: ``__del__`` is now a thin wrapper around the
    # idempotent ``close()`` (which checks ``_closed``). This means
    # if atexit / explicit close() already fired, ``__del__`` is a
    # no-op (no "Run not active" warning). If neither fired (caller
    # forgot AND somehow bypassed atexit), ``__del__`` still ends
    # the run as a last resort — best-effort, may fail silently if
    # the tracking server is gone (the exception is swallowed).
    def __del__(self):
        try:
            # P2-014: delegate to close() which has the idempotency
            # guard. Directly calling end_run() here would bypass
            # the ``_closed`` check and could produce "Run not
            # active" warnings when atexit / explicit close() have
            # already fired.
            if not self._closed:
                self.close()
        except Exception:
            pass  # __del__ must never raise
        # v72 ROOT FIX (P2C-019): removed ``return False``. __del__ is a
        # destructor whose return value is ignored by the Python runtime.
        # The previous ``return False`` was dead code that could mislead a
        # maintainer into thinking the return value mattered. The
        # convention is to return None (implicitly) from __del__.
