"""
DisGeNET DAG — standalone pipeline for gene-disease associations.

Downloads the full gene-disease association dataset from DisGeNET,
filters by minimum score, normalises confidence tiers, and bulk-upserts
into the ``gene_disease_associations`` table.

If ``DISGENET_API_KEY`` is set the Bearer auth header is used for the
download; otherwise the public endpoint is attempted.

Can be triggered independently or as part of the master pipeline.
Schedule: every Tuesday at 06:00 UTC (cron ``0 6 * * 2``).
v49 ROOT FIX (Compound-4 — Sunday Morning Pile-Up): was previously
``0 6 * * 0`` (Sunday 06:00 UTC) which overlapped the master DAG
window (Sunday 02:00 UTC, 8h timeout). Moved to Tuesday to eliminate
the per-pipeline filelock conflict with the master. DisGeNET curates
weekly; the standalone DAG runs on Tuesday so per-source refreshes
work without requiring the master DAG.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from airflow.decorators import dag, task

# v74 ROOT FIX (T-023 — retries on 4xx HTTP errors waste 60 min):
# Use the shared retry policy: exponential backoff (5min → 10min → 20min
# cap) AND a fail-fast decorator that converts HTTP 4xx (401 Unauthorized,
# 403 Forbidden, 404 Not Found, etc.) to AirflowFailException so the task
# is NOT retried. Retrying a 401 (bad API key) or 404 (wrong endpoint)
# never succeeds — the original error is non-transient.
from dags._retry_policy import DEFAULT_RETRY_ARGS, fail_fast_on_http_4xx

# v29 ROOT FIX (audit O-12): XCom used for large dataframes — anti-pattern.
# Now passes file paths via XCom. The single @task below returns None and the
# DisGeNETPipeline persists its output to processed_data/
# (gene_disease_associations.csv). Downstream DAGs (master pipeline) read that
# CSV by path — no DataFrame is ever pushed to / pulled from XCom.

DEFAULT_ARGS = {
    **DEFAULT_RETRY_ARGS,
    "owner": "drug_repurposing",
    "depends_on_past": False,
}


@task(retries=2, execution_timeout=timedelta(hours=4),
      retry_exponential_backoff=True, retry_delay=timedelta(minutes=5))
@fail_fast_on_http_4xx
def run_disgenet() -> None:
    """Execute the full DisGeNET pipeline: download → clean → load."""
    from pipelines.disgenet_pipeline import DisGeNETPipeline
    DisGeNETPipeline().run()


@dag(
    dag_id="disgenet_pipeline",
    description="DisGeNET ETL pipeline: gene-disease associations",
    # v49 ROOT FIX (Compound-4 — Sunday Morning Pile-Up):
    # Was "0 6 * * 0" (Sunday 06:00 UTC) — overlapped master DAG window.
    # Moved to Tuesday 06:00 UTC to eliminate the filelock conflict.
    schedule="0 6 * * 2",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["drug_repurposing", "disgenet", "etl"],
)
def disgenet_dag() -> None:
    """Build the DisGeNET pipeline DAG."""
    run_disgenet()


disgenet_dag_instance = disgenet_dag()
