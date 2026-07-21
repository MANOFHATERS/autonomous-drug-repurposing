#!/usr/bin/env python3
"""Airflow DAG: retrain_on_validated.

TM15 v132 ROOT FIX (Teammate 15 — Data Flywheel Integration, requirement #5).

WHY THIS DAG EXISTS
===================
The project docx (Section 10 — "The Data Flywheel") mandates that when
a pharma partner validates a hypothesis, the validation result is fed
back into the platform as a new labeled data point. The next model
retraining run picks it up — predictions improve — more partners
validate — repeat.

Without this DAG, validated hypotheses were stored (CSV + DB + Neo4j
edge) but NEVER fed back into model retraining. An operator had to
manually trigger Phase 1 → 2 → 3 → 4 retraining. The flywheel was
aspirational, not actual.

WHAT THIS DAG DOES
==================
Every 6 hours, a sensor checks if 10+ new validated hypotheses have
been added to ``phase1/processed_data/validated_hypotheses.csv`` since
the last successful run. If yes, it triggers a full retraining chain:

    Phase 2 (KG build) → Phase 3 (GT training) → Phase 4 (RL training)

The threshold of 10 is intentionally conservative:
  * Retraining the GT model on <10 new pairs produces a negligible AUC
    improvement (within the bootstrap CI noise — see compute_auc's
    bootstrap CI computation). The compute cost (GPU hours, MLflow
    runs, Airflow task slots) outweighs the benefit.
  * Pharma partners typically validate in batches (5–20 hypotheses
    per wet-lab campaign). 10 is the typical minimum batch size.
  * The threshold is configurable via the ``RETRAIN_VALIDATED_THRESHOLD``
    Airflow Variable (default 10).

The sensor stores its last-seen count in Airflow's XCom (key
``last_count``) so the next run knows the baseline. If the XCom is
missing (first run, or XCom purge), the sensor treats ALL validated
hypotheses as new — but only triggers retraining if there are at least
``RETRAIN_VALIDATED_THRESHOLD`` of them.

CONTRACT
========
Sensor (check_new_validated_hypotheses):
    Returns True iff 10+ new validated hypotheses have been added
    since the last successful run.
    Pushes XCom keys: ``last_count`` (current total), ``new_count``
    (delta since last run).

Phase 2 task (trigger_phase2_retrain):
    Runs the Phase 2 KG construction pipeline with the
    ``--include-validated-hypotheses`` flag so validated pairs become
    :VALIDATED_TREATS edges in Neo4j.

Phase 3 task (trigger_phase3_retrain):
    Runs the Phase 3 GT trainer with ``--checkpoint gt_retrain_latest.pt``.
    The trainer reads the validated_hypotheses.csv via
    ``load_validated_hypotheses`` and incorporates the pairs into the
    training set (this is the canonical path — the demo builder's
    ``add_edge("drug", "treats", "disease", ...)`` injection was REMOVED
    by the P3-008 root fix).

Phase 4 task (trigger_phase4_retrain):
    Runs the Phase 4 RL trainer with ``--checkpoint rl_retrain_latest.zip``
    and ``--timesteps 50000``. The RL trainer reads the same
    validated_hypotheses.csv via ``load_validated_hypotheses`` and
    passes the set to ``reward_fn.set_validated_hypotheses()`` so the
    +0.1 validated_bonus fires during training.

RUNNING MANUALLY
================
    # Trigger a one-off retrain (skips the sensor):
    airflow dags trigger retrain_on_validated

    # Check the last run's XCom:
    airflow tasks list retrain_on_validated
    airflow tasks test retrain_on_validated check_new_validated_hypotheses 2026-01-01

ROLLBACK
========
To disable the DAG temporarily:
    airflow dags pause retrain_on_validated
Validated hypotheses continue to accumulate in the CSV; the next unpause
will trigger a single retrain run if 10+ have accumulated.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Airflow is an OPTIONAL dependency at import time. The DAG module must NOT
# crash when imported in an environment without Airflow (e.g. CI that only
# tests the Phase 1 cleaning module). The DAG factory pattern below defers
# all Airflow imports to call time, so the module is import-safe.
# -----------------------------------------------------------------------------
try:
    from airflow import DAG
    from airflow.operators.empty import EmptyOperator
    from airflow.operators.python import PythonOperator, PythonSensor
    from airflow.utils.context import Context

    _AIRFLOW_AVAILABLE = True
except ImportError:  # pragma: no cover — Airflow is an optional dep
    _AIRFLOW_AVAILABLE = False
    DAG = None  # type: ignore[assignment, misc]
    EmptyOperator = None  # type: ignore[assignment, misc]
    PythonOperator = None  # type: ignore[assignment, misc]
    PythonSensor = None  # type: ignore[assignment, misc]
    Context = Any  # type: ignore[assignment, misc]


# -----------------------------------------------------------------------------
# Configuration (Airflow Variables override these at runtime).
# -----------------------------------------------------------------------------
DEFAULT_RETRAIN_THRESHOLD = 10
"""Minimum number of NEW validated hypotheses to trigger a retrain."""

DEFAULT_SCHEDULE_INTERVAL = "0 */6 * * *"  # every 6 hours
DEFAULT_START_DATE = datetime(2026, 1, 1, tzinfo=timezone.utc)
DEFAULT_SENSOR_TIMEOUT_SECONDS = 60 * 60  # 1 hour
DEFAULT_SENSOR_POKE_INTERVAL_SECONDS = 60 * 10  # 10 minutes
DEFAULT_SENSOR_MODE = "reschedule"

DAG_ID = "retrain_on_validated"


# -----------------------------------------------------------------------------
# Canonical CSV path — uses the SAME constant as phase4.writeback and the
# shared contract. This guarantees the sensor reads the EXACT file the
# writeback module writes to.
# -----------------------------------------------------------------------------
def _get_validated_csv_path() -> Path:
    """Return the canonical validated_hypotheses.csv path.

    Reads PHASE1_VALIDATED_CSV and VALIDATED_HYPOTHESES_CSV env vars at
    CALL TIME (not import time) so tests and runtime config can override
    without reloading the module. Falls back to the shared contract's
    canonical path (phase1/processed_data/validated_hypotheses.csv).
    """
    env_path = (
        os.environ.get("PHASE1_VALIDATED_CSV")
        or os.environ.get("VALIDATED_HYPOTHESES_CSV")
    )
    if env_path:
        return Path(env_path)
    # Fall back to the canonical shared-contract path. Import lazily so
    # this module imports cleanly even when shared/ is not on the path
    # (e.g. in a stripped-down CI container that only tests DAG wiring).
    try:
        import sys as _sys
        _here = Path(__file__).resolve()
        _repo_root = _here.parents[2]  # phase1/dags/X.py -> repo root
        if str(_repo_root) not in _sys.path:
            _sys.path.insert(0, str(_repo_root))
        from shared.contracts.writeback import get_validated_csv_path as _get_path
        return Path(_get_path())
    except Exception:
        # Last-resort fallback: hardcode the canonical relative path.
        # This matches the shared contract's default.
        return Path("phase1/processed_data/validated_hypotheses.csv")


def _count_validated_hypotheses(csv_path: Path) -> int:
    """Count the number of validated hypotheses rows in the CSV.

    Returns 0 if the file does not exist or is empty (header only).
    Propagates parse errors (the sensor should NOT silently treat a
    corrupt CSV as "0 validated" — the operator must investigate).
    """
    if not csv_path.exists():
        return 0
    import csv as _csv
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = _csv.reader(f)
        try:
            next(reader)  # skip header
        except StopIteration:
            return 0
        return sum(1 for _ in reader)


def check_new_validated_hypotheses(**context: Any) -> bool:
    """Airflow sensor: check if 10+ new validated hypotheses since last run.

    Pushes XCom:
        ``last_count`` — total count of validated hypotheses now in the CSV.
        ``new_count``  — delta since the last successful run's ``last_count``.

    Returns True iff ``new_count >= RETRAIN_VALIDATED_THRESHOLD``.
    """
    # Read threshold from Airflow Variable (overridable at runtime without
    # redeploying the DAG). Fall back to the module default if the Variable
    # is unset or unparseable.
    threshold = DEFAULT_RETRAIN_THRESHOLD
    try:
        from airflow.models import Variable
        v = Variable.get("RETRAIN_VALIDATED_THRESHOLD", default_var=None)
        if v is not None:
            threshold = int(v)
            if threshold < 1:
                logger.warning(
                    "RETRAIN_VALIDATED_THRESHOLD=%d is <1; falling back to "
                    "default %d.", threshold, DEFAULT_RETRAIN_THRESHOLD,
                )
                threshold = DEFAULT_RETRAIN_THRESHOLD
    except Exception as exc:
        logger.warning(
            "Could not read RETRAIN_VALIDATED_THRESHOLD Airflow Variable "
            "(%s); using default %d.", exc, DEFAULT_RETRAIN_THRESHOLD,
        )

    csv_path = _get_validated_csv_path()
    current_count = _count_validated_hypotheses(csv_path)

    # Pull the last_count from XCom (None on first run or after XCom purge).
    ti = context.get("ti") or context.get("task_instance")
    last_count = 0
    if ti is not None:
        try:
            last_count = ti.xcom_pull(
                task_ids="check_new_validated_hypotheses",
                key="last_count",
            ) or 0
        except Exception:
            last_count = 0

    new_count = max(0, current_count - last_count)
    logger.info(
        "retrain_on_validated sensor: csv=%s current_count=%d "
        "last_count=%d new_count=%d threshold=%d",
        csv_path, current_count, last_count, new_count, threshold,
    )

    if new_count >= threshold:
        if ti is not None:
            ti.xcom_push(key="last_count", value=current_count)
            ti.xcom_push(key="new_count", value=new_count)
            ti.xcom_push(key="threshold", value=threshold)
        logger.info(
            "retrain_on_validated sensor: TRIGGERING retrain "
            "(%d new >= %d threshold).",
            new_count, threshold,
        )
        return True
    logger.info(
        "retrain_on_validated sensor: NOT triggering retrain "
        "(%d new < %d threshold).",
        new_count, threshold,
    )
    return False


def trigger_phase2_retrain(**context: Any) -> int:
    """Trigger the Phase 2 KG construction pipeline.

    Runs ``python -m phase2.drugos_graph.run_pipeline`` with the
    ``--include-validated-hypotheses`` flag so validated pairs become
    :VALIDATED_TREATS edges in Neo4j (in addition to the edges the
    writeback module already adds via direct Neo4j MERGE).

    Returns the subprocess exit code (0 = success). Raises
    ``subprocess.CalledProcessError`` on non-zero exit (Airflow will
    retry per the DAG's retry policy).
    """
    import subprocess
    import sys
    new_count = 0
    ti = context.get("ti") or context.get("task_instance")
    if ti is not None:
        new_count = ti.xcom_pull(
            task_ids="check_new_validated_hypotheses",
            key="new_count",
        ) or 0
    provenance = f"retrain_{datetime.now(timezone.utc).isoformat()}"
    logger.info(
        "trigger_phase2_retrain: starting KG build (new_count=%d, "
        "provenance=%s)", new_count, provenance,
    )
    cmd = [
        sys.executable, "-m", "phase2.drugos_graph.run_pipeline",
        "--provenance", provenance,
        "--include-validated-hypotheses",
    ]
    # Capture output so Airflow can display it in the task instance UI.
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error(
            "trigger_phase2_retrain FAILED (exit %d).\n"
            "STDOUT:\n%s\nSTDERR:\n%s",
            result.returncode, result.stdout, result.stderr,
        )
        raise RuntimeError(
            f"Phase 2 retrain failed (exit {result.returncode}): "
            f"{result.stderr[-2000:]}"
        )
    logger.info(
        "trigger_phase2_retrain: SUCCESS. KG built with %d new validated "
        "pairs incorporated as :VALIDATED_TREATS edges.", new_count,
    )
    return result.returncode


def trigger_phase3_retrain(**context: Any) -> int:
    """Trigger the Phase 3 GT trainer.

    Runs ``python -m graph_transformer.training.trainer`` with a
    ``--checkpoint gt_retrain_latest.pt`` flag. The trainer reads
    validated_hypotheses.csv via ``load_validated_hypotheses`` and
    incorporates the pairs into the training set (positive if outcome
    is ``validated_positive``, negative if ``validated_negative`` or
    ``validated_toxic``). This is the CANONICAL path by which validated
    pairs enter the GT training set — NOT the demo builder's
    ``add_edge("drug", "treats", "disease", ...)`` (which was REMOVED
    by the P3-008 root fix).
    """
    import subprocess
    import sys
    logger.info("trigger_phase3_retrain: starting GT trainer")
    cmd = [
        sys.executable, "-m", "graph_transformer.training.trainer",
        "--checkpoint", "gt_retrain_latest.pt",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error(
            "trigger_phase3_retrain FAILED (exit %d).\n"
            "STDOUT:\n%s\nSTDERR:\n%s",
            result.returncode, result.stdout, result.stderr,
        )
        raise RuntimeError(
            f"Phase 3 retrain failed (exit {result.returncode}): "
            f"{result.stderr[-2000:]}"
        )
    logger.info("trigger_phase3_retrain: SUCCESS. GT model retrained.")
    return result.returncode


def trigger_phase4_retrain(**context: Any) -> int:
    """Trigger the Phase 4 RL trainer.

    Runs ``python -m rl.rl_drug_ranker train`` with 50000 timesteps and
    ``--checkpoint rl_retrain_latest.zip``. The trainer reads
    validated_hypotheses.csv via ``load_validated_hypotheses`` and
    passes the set to ``reward_fn.set_validated_hypotheses()`` so the
    +0.1 validated_bonus fires during training.
    """
    import subprocess
    import sys
    logger.info("trigger_phase4_retrain: starting RL trainer (50K timesteps)")
    cmd = [
        sys.executable, "-m", "rl.rl_drug_ranker", "train",
        "--timesteps", "50000",
        "--checkpoint", "rl_retrain_latest.zip",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error(
            "trigger_phase4_retrain FAILED (exit %d).\n"
            "STDOUT:\n%s\nSTDERR:\n%s",
            result.returncode, result.stdout, result.stderr,
        )
        raise RuntimeError(
            f"Phase 4 retrain failed (exit {result.returncode}): "
            f"{result.stderr[-2000:]}"
        )
    logger.info("trigger_phase4_retrain: SUCCESS. RL agent retrained.")
    return result.returncode


# -----------------------------------------------------------------------------
# DAG factory — called at module import time IF Airflow is available.
# -----------------------------------------------------------------------------
default_args = {
    "owner": "cosmic",
    "depends_on_past": False,
    "start_date": DEFAULT_START_DATE,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
}

dag: Any = None
if _AIRFLOW_AVAILABLE:
    dag = DAG(
        dag_id=DAG_ID,
        default_args=default_args,
        description=(
            "TM15 v132: trigger full Phase 2 -> 3 -> 4 retraining when "
            "10+ new validated hypotheses accumulate (data flywheel)."
        ),
        schedule_interval=DEFAULT_SCHEDULE_INTERVAL,
        catchup=False,
        is_paused_upon_creation=False,
        tags=["data-flywheel", "tm15", "retrain"],
        default_view="graph",
    )

    with dag:
        sensor = PythonSensor(
            task_id="check_new_validated_hypotheses",
            python_callable=check_new_validated_hypotheses,
            timeout=DEFAULT_SENSOR_TIMEOUT_SECONDS,
            poke_interval=DEFAULT_SENSOR_POKE_INTERVAL_SECONDS,
            mode=DEFAULT_SENSOR_MODE,
            dag=dag,
        )

        phase2 = PythonOperator(
            task_id="trigger_phase2_retrain",
            python_callable=trigger_phase2_retrain,
            dag=dag,
        )

        phase3 = PythonOperator(
            task_id="trigger_phase3_retrain",
            python_callable=trigger_phase3_retrain,
            dag=dag,
        )

        phase4 = PythonOperator(
            task_id="trigger_phase4_retrain",
            python_callable=trigger_phase4_retrain,
            dag=dag,
        )

        # Dependency chain: sensor → Phase 2 → Phase 3 → Phase 4.
        # Each phase depends on the previous one succeeding. If any phase
        # fails, Airflow's retry policy (1 retry, 5 min delay) kicks in;
        # if the retry also fails, downstream tasks are skipped.
        sensor >> phase2 >> phase3 >> phase4


__all__ = [
    "DAG_ID",
    "DEFAULT_RETRAIN_THRESHOLD",
    "DEFAULT_SCHEDULE_INTERVAL",
    "check_new_validated_hypotheses",
    "trigger_phase2_retrain",
    "trigger_phase3_retrain",
    "trigger_phase4_retrain",
    "_get_validated_csv_path",
    "_count_validated_hypotheses",
    "dag",
]
