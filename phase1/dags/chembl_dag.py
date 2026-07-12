"""
ChEMBL DAG -- standalone pipeline for ChEMBL drug and bioactivity data.

Downloads FDA-approved molecules and bioactivity data from the ChEMBL REST
API, cleans / normalises InChIKeys, deduplicates, and bulk-upserts into
the ``drugs`` and ``drug_protein_interactions`` tables.

Can be triggered independently or as part of the master pipeline.
Schedule: every Wednesday at 04:00 UTC (cron ``0 4 * * 3``).
v43 ROOT FIX (P1 -- schedule overlap with master DAG): the previous
schedule ``0 2 * * 0`` (Sunday 02:00 UTC) collided with the master
DAG's identical schedule. Both DAGs invoked ChEMBLPipeline().run()
simultaneously, causing file-lock contention and spurious "Could not
acquire run lock" failures. Fix: stagger to Wednesday 04:00 UTC so
standalone and master never overlap. The master DAG keeps Sunday
02:00 UTC. ChEMBL releases a new dump weekly; the standalone DAG
runs every Wednesday so ad-hoc / per-source refreshes work without
requiring the master DAG.
"""

from __future__ import annotations

from datetime import datetime

# v89 ROOT FIX (BUG #39): shared sys.path bootstrap -- was duplicated
# verbatim in all 8 DAG files. Extracted to dags/_dags_init.py so the
# path-setup logic lives in ONE place.
from dags._dags_init import ensure_project_root  # noqa: F401
# P1-050 ROOT FIX: explicit call (no longer auto-invoked at module import)
ensure_project_root()

from airflow.decorators import dag, task

# v29 ROOT FIX (audit O-12): XCom used for large dataframes -- anti-pattern.
# Now passes file paths via XCom. The single @task below returns None and the
# ChEMBLPipeline persists its output to processed_data/ (drugs.csv,
# drug_protein_interactions.csv). Downstream DAGs (master pipeline) read those
# CSVs by path -- no DataFrame is ever pushed to / pulled from XCom.

# v74 ROOT FIX (T-023 -- retries on 4xx HTTP errors waste 60 min):
# Use the shared retry policy: exponential backoff (5min -> 10min -> 20min
# cap) AND a fail-fast decorator that converts HTTP 4xx (401 Unauthorized,
# 403 Forbidden, 404 Not Found, etc.) to AirflowFailException so the task
# is NOT retried. Retrying a 401 (bad API key) or 404 (wrong endpoint)
# never succeeds -- the original error is non-transient.
from dags._retry_policy import DEFAULT_RETRY_ARGS, fail_fast_on_http_4xx

DEFAULT_ARGS = {
    **DEFAULT_RETRY_ARGS,
    "owner": "drug_repurposing",
    "depends_on_past": False,
}


# v89 ROOT FIX (BUG #25 / BUG #38): use bare ``@task`` -- all retry /
# timeout / backoff params are ALREADY in ``DEFAULT_RETRY_ARGS`` (spread
# into ``DEFAULT_ARGS`` above). The previous redundant overrides were a
# maintenance trap: if ``DEFAULT_RETRY_ARGS`` changed, the standalone
# DAGs didn't follow. The OMIM DAG already used bare ``@task`` (v83);
# the other 6 DAGs are now aligned for consistency (DRY).
@task
@fail_fast_on_http_4xx
def run_chembl() -> None:
    """Execute the full ChEMBL pipeline: download -> clean -> load."""
    from pipelines.chembl_pipeline import ChEMBLPipeline
    ChEMBLPipeline().run()


@dag(
    dag_id="chembl_pipeline",
    description="ChEMBL ETL pipeline: approved drugs and bioactivity data",
    # v29 ROOT FIX (audit O-11): was schedule=None (dead). Now scheduled.
    # ChEMBL releases a new dump every Sunday; standalone DAG runs weekly.
    # v43 ROOT FIX (P1 -- schedule overlap with master DAG): the previous
    # schedule "0 2 * * 0" (Sunday 02:00 UTC) collided with the master
    # DAG's identical schedule. Both DAGs invoke ChEMBLPipeline().run()
    # simultaneously -> file-lock contention -> spurious "Could not
    # acquire run lock" failures. Fix: stagger to Wednesday 04:00 UTC
    # so standalone and master never overlap. The master DAG keeps
    # Sunday 02:00 UTC.
    schedule="0 4 * * 3",  # Wednesday 04:00 UTC (staggered from master)
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["drug_repurposing", "chembl", "etl"],
)
def chembl_dag() -> None:
    """Build the ChEMBL pipeline DAG."""
    run_chembl()


# v89 ROOT FIX (BUG #40): consistent DAG-instance naming convention.
# Was ``chembl_dag_instance`` here and ``master_dag`` in the master DAG
# -- a future maintainer searching for ``dag_instance`` would miss most
# of them. All 8 DAG files now use ``dag = <dag_factory>()``.
dag = chembl_dag()
