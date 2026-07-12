"""
PubChem DAG -- standalone pipeline for PubChem drug enrichment.

Reads InChIKeys from the ``drugs`` table where ``pubchem_cid`` IS NULL,
batch-queries the PubChem PUG REST API for properties, and bulk-updates
the ``drugs`` table with retrieved molecular data.

Can be triggered independently or as part of the master pipeline.
Schedule: every Wednesday at 08:00 UTC (cron ``0 8 * * 3``).
v49 ROOT FIX (Compound-4 -- Sunday Morning Pile-Up): was previously
``0 8 * * 0`` (Sunday 08:00 UTC) which overlapped the master DAG
window (Sunday 02:00 UTC, 8h timeout). Moved to Wednesday to
eliminate the per-pipeline filelock conflict with the master.
PubChem updates compound properties continuously; the standalone DAG
runs every Wednesday so ad-hoc / per-source refreshes work without
requiring the master DAG.
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
# PubChemPipeline persists its output to processed_data/ (pubchem_enrichment.csv).
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
def run_pubchem() -> None:
    """Execute the full PubChem pipeline: download -> clean -> load."""
    from pipelines.pubchem_pipeline import PubChemPipeline
    PubChemPipeline().run()


@dag(
    dag_id="pubchem_pipeline",
    description="PubChem ETL pipeline: drug enrichment via PUG REST API",
    # v49 ROOT FIX (Compound-4 -- Sunday Morning Pile-Up):
    # Was "0 8 * * 0" (Sunday 08:00 UTC) -- overlapped master DAG window.
    # Moved to Wednesday 08:00 UTC to eliminate the filelock conflict.
    #
    # P1-047 FORENSIC ROOT FIX (Team 4 -- ChEMBL/PubChem Wednesday overlap):
    # The previous schedule was ``0 8 * * 3`` (Wednesday 08:00 UTC), which
    # overlapped with ChEMBL's ``0 4 * * 3`` (Wednesday 04:00 UTC) -- both
    # write to the ``drugs`` table, causing DB contention. ROOT FIX: move
    # PubChem to Saturday 08:00 UTC (3 hours after STRING at Sat 05:00 --
    # no overlap). Saturday NEVER collides with the master's Sunday window.
    schedule="0 8 * * 6",  # Every Saturday at 08:00 UTC (P1-047 root fix)
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["drug_repurposing", "pubchem", "etl"],
)
def pubchem_dag() -> None:
    """Build the PubChem pipeline DAG."""
    run_pubchem()


# v89 ROOT FIX (BUG #40): consistent DAG-instance naming convention.
dag = pubchem_dag()
