"""
UniProt DAG — standalone pipeline for human reviewed (Swiss-Prot) protein data.

Downloads human reviewed proteins from the UniProt REST API using
cursor-based pagination, cleans and normalises records, and bulk-upserts
into the ``proteins`` table.

Can be triggered independently or as part of the master pipeline.
Schedule: 15th of every month at 04:00 UTC (cron ``0 4 15 * *``). UniProt's
Swiss-Prot human reviewed set updates monthly; the standalone DAG runs
on the 15th of every month so ad-hoc / per-source refreshes work without
requiring the master DAG.

v89 FORENSIC ROOT FIX (BUG #8 P1 — Sunday Morning Pile-Up):
  Moved from ``0 4 1 * *`` (1st of month) to ``0 4 15 * *`` (15th of
  month). The 1st could fall on a Sunday, colliding with the master DAG
  (Sunday 02:00 UTC, up to 7h runtime). See omim_dag.py for full fix
  rationale. The 15th never systematically collides with Sunday.
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
# UniProtPipeline persists its output to processed_data/ (proteins.csv).
# Downstream DAGs (master pipeline) read that CSV by path — no DataFrame is
# ever pushed to / pulled from XCom.

DEFAULT_ARGS = {
    **DEFAULT_RETRY_ARGS,
    "owner": "drug_repurposing",
    "depends_on_past": False,
}


@task(retries=2, execution_timeout=timedelta(hours=4),
      retry_exponential_backoff=True, retry_delay=timedelta(minutes=5))
@fail_fast_on_http_4xx
def run_uniprot() -> None:
    """Execute the full UniProt pipeline: download → clean → load."""
    from pipelines.uniprot_pipeline import UniProtPipeline
    UniProtPipeline().run()


@dag(
    dag_id="uniprot_pipeline",
    description="UniProt ETL pipeline: human reviewed protein data",
    # v29 ROOT FIX (audit O-11): was schedule=None (dead). Now scheduled.
    # UniProt's Swiss-Prot human reviewed set updates monthly; standalone DAG
    # runs on the 15th of every month at 04:00 UTC so ad-hoc / per-source
    # refreshes work without requiring the master DAG.
    # v89 BUG #8: moved from 1st to 15th to avoid Sunday collisions.
    schedule="0 4 15 * *",
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["drug_repurposing", "uniprot", "etl"],
)
def uniprot_dag() -> None:
    """Build the UniProt pipeline DAG."""
    run_uniprot()


uniprot_dag_instance = uniprot_dag()
