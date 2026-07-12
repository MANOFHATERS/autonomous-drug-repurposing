"""
DrugBank DAG -- standalone pipeline for DrugBank XML drug and target data.

Parses the DrugBank full-database XML file (requires manual download due
to licensing).  Extracts drug metadata and target interactions, normalises
InChIKeys, deduplicates, and bulk-upserts into the ``drugs`` and
``drug_protein_interactions`` tables.

If the DrugBank XML file is not present the pipeline will raise a clear
``FileNotFoundError`` with download instructions.

Can be triggered independently or as part of the master pipeline.
Schedule: every Monday at 03:00 UTC (cron ``0 3 * * 1``).
v49 ROOT FIX (Compound-4 -- Sunday Morning Pile-Up): was previously
``0 3 * * 0`` (Sunday 03:00 UTC) which overlapped the master DAG
window (Sunday 02:00 UTC, 8h timeout). Moved to Monday to eliminate
the per-pipeline filelock conflict with the master. DrugBank XML is
manually positioned; the weekly standalone run picks up any newly-
positioned XML without requiring the master DAG.
"""

from __future__ import annotations

from datetime import datetime

# v89 ROOT FIX (BUG #39): shared sys.path bootstrap (see dags/_dags_init.py).
from dags._dags_init import ensure_project_root  # noqa: F401
# P1-050 ROOT FIX: explicit call (no longer auto-invoked at module import)
ensure_project_root()

from airflow.decorators import dag, task

# v74 ROOT FIX (T-023 -- retries on 4xx HTTP errors waste 60 min):
# Use the shared retry policy: exponential backoff (5min -> 10min -> 20min
# cap) AND a fail-fast decorator that converts HTTP 4xx (401 Unauthorized,
# 403 Forbidden, 404 Not Found, etc.) to AirflowFailException so the task
# is NOT retried. Retrying a 401 (bad API key) or 404 (wrong endpoint)
# never succeeds -- the original error is non-transient.
from dags._retry_policy import DEFAULT_RETRY_ARGS, fail_fast_on_http_4xx

# v29 ROOT FIX (audit O-12): XCom used for large dataframes -- anti-pattern.
# Now passes file paths via XCom. The single @task below returns None and the
# DrugBankPipeline persists its output to processed_data/ (drugbank_drugs.csv).
# Downstream DAGs (master pipeline) read that CSV by path -- no DataFrame is
# ever pushed to / pulled from XCom.

DEFAULT_ARGS = {
    **DEFAULT_RETRY_ARGS,
    "owner": "drug_repurposing",
    "depends_on_past": False,
}


# v89 ROOT FIX (BUG #25 / BUG #38): bare ``@task`` -- retry params
# inherited from DEFAULT_ARGS (spread from DEFAULT_RETRY_ARGS).
@task
@fail_fast_on_http_4xx
def run_drugbank() -> None:
    """Execute the full DrugBank pipeline: download (verify XML) -> clean -> load."""
    from pipelines.drugbank_pipeline import DrugBankPipeline
    DrugBankPipeline().run()


@dag(
    dag_id="drugbank_pipeline",
    description="DrugBank ETL pipeline: drug and target data from XML",
    # v49 ROOT FIX (Compound-4 -- Sunday Morning Pile-Up):
    # The v29 schedule was "0 3 * * 0" (Sunday 03:00 UTC) -- this overlaps
    # the master DAG (Sunday 02:00 UTC, 8h timeout) and causes per-pipeline
    # filelock conflicts every week. ROOT FIX: move standalone DrugBank
    # to Monday 03:00 UTC. The master DAG remains the primary
    # orchestrator; this standalone DAG only fires for ad-hoc refreshes
    # and no longer conflicts with the master's window.
    schedule="0 3 * * 1",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["drug_repurposing", "drugbank", "etl"],
)
def drugbank_dag() -> None:
    """Build the DrugBank pipeline DAG."""
    run_drugbank()


# v89 ROOT FIX (BUG #40): consistent DAG-instance naming convention.
dag = drugbank_dag()
