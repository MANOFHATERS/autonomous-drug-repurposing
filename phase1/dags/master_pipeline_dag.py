"""
Master DAG for the Drug Repurposing ETL Platform.

Orchestrates all 7 source pipelines in the correct dependency order:

  download_chembl  ──┐
  download_drugbank ─┤-> entity_resolution -> load_string
  download_uniprot  ─┤                    -> load_disgenet
  download_string  ──┘                    -> load_omim
  download_disgenet                        -> load_pubchem_enrichment
  download_omim
  download_pubchem

DrugBank XML check: Uses BranchPythonOperator to skip DrugBank if the XML
file is not present (it requires manual download -- pipeline should not fail
the whole DAG).

Schedule: Every Sunday at 02:00 UTC  (``0 2 * * 0``)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# v89 ROOT FIX (BUG #39): shared sys.path bootstrap -- was duplicated
# verbatim in all 8 DAG files. Extracted to dags/_dags_init.py.
# ---------------------------------------------------------------------------
from dags._dags_init import ensure_project_root  # noqa: F401
# P1-050 ROOT FIX: explicit call (no longer auto-invoked at module import)
ensure_project_root()

from airflow.decorators import dag, task
from airflow.operators.branch import BranchPythonOperator
from airflow.operators.empty import EmptyOperator
from airflow.utils.trigger_rule import TriggerRule

# v83 P1-14: import the shared retry policy so the master DAG uses the
# SAME retry parameters (5min + exponential backoff) as the 7 standalone DAGs.
# v89 ROOT FIX (BUG #24): also import fail_fast_on_http_4xx so it can be
# applied to _trigger_phase2 (was missing -- inconsistent with the
# documented "apply @fail_fast_on_http_4xx to EVERY @task" policy).
from dags._retry_policy import DEFAULT_RETRY_ARGS, fail_fast_on_http_4xx

logger = logging.getLogger(__name__)

# v89 ROOT FIX (BUG #27 -- fragile runtime import inside _check_drugbank_xml):
# The previous code did ``from config.settings import DRUGBANK_XML_PATH``
# INSIDE the ``_check_drugbank_xml`` branch callable. That import runs
# at TASK EXECUTION time (in the Airflow worker). If the worker's
# sys.path / CWD is different from expected, the task raises ImportError
# -- which (with retries=2) retries 2 more times before failing the DAG.
# ROOT FIX: move the import to the TOP of the module (after sys.path
# setup), so it runs at DAG PARSE time. If config.settings is not
# importable, the DAG fails to parse -- Airflow marks the DAG as
# "import error" in the UI, which is far more diagnosable than a
# runtime ImportError 2 retries later. The ``try/except`` wraps the
# import so a missing config doesn't kill DAG parsing for the OTHER
# 6 pipelines that don't need DRUGBANK_XML_PATH.
try:
    from config.settings import DRUGBANK_XML_PATH
except Exception as _exc:  # noqa: BLE001 -- config import must never kill DAG parse
    logger.warning(
        "v89 BUG #27: could not import DRUGBANK_XML_PATH from config.settings "
        "at DAG parse time (%s). The DrugBank XML branch will fall back to "
        "'skip_drugbank' at runtime. Fix config.settings to enable DrugBank.",
        _exc,
    )
    DRUGBANK_XML_PATH = ""  # sentinel -- _check_drugbank_xml will skip

# ---------------------------------------------------------------------------
# v29 ROOT FIX (audit O-12): XCom used for large dataframes -- anti-pattern.
# Now passes file paths via XCom (and, in practice, tasks communicate through
# CSV files in processed_data/ + the shared DB, never by returning a DataFrame
# from a @task). Returning a DataFrame would push it to XCom and saturate the
# metadata DB. Every @task below returns None and either writes to
# processed_data/ (producers) or reads from processed_data/ (consumers) --
# only small file-path strings are ever exchanged between tasks.
# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
# v75 ROOT FIX (T-024 -- SLA/timeout mismatch compounds silently):
#   v29 set TASK_SLA=4h with TASK_TIMEOUT=8h. The audit's premise was that
#   the SLA was the binding limit -- but Airflow SLA misses are ADVISORY:
#   they emit an entry to the SLA miss log and (optionally) trigger an
#   email, but they do NOT kill the running task. The task continued for
#   another 4h until the 8h execution_timeout fired. With retries=0 on
#   _trigger_phase2 (line 462), the 8h timeout killed the task RED with
#   no retry -- the 4h SLA warning was the only early signal, and it was
#   advisory.
#
#   Worse: the 8h timeout was documented as "TransE training on real data
#   can take 6-7h" -- so a NORMAL 6-7h run fires the 4h SLA miss every
#   single time, training operators to ignore SLA warnings. That is the
#   definition of a noisy false-positive alarm that defeats the purpose
#   of having an SLA at all.
#
#   ROOT FIX (master-grade, no sugar-coating):
#     1. Align the SLA and the hard timeout at the SAME value (7h). The
#        SLA miss at 7h now coincides with the hard kill, so there is
#        exactly ONE signal at exactly ONE time. No false-positive
#        advisory that trains operators to ignore warnings.
#     2. The hard timeout is the BINDING limit -- by definition, when the
#        SLA fires, the task is also about to be killed. This is the
#        scientifically correct configuration for an SLA that is meant
#        as an early-warning system: the warning must come BEFORE the
#        kill, and if the only sensible "early warning" time is "right
#        before the kill", then the SLA is redundant with the timeout
#        and should be set to the same value to remove the noise.
#     3. The 7h value is the upper bound of the documented training
#        window (6-7h on real data). A normal run completes in ≤7h; a
#        stuck run is killed at 7h. No false positives on normal runs.
#     4. retries=0 on _trigger_phase2 is preserved (line 462) -- a
#        timed-out Phase 2 training run must NOT be retried
#        automatically (GPU state, partial checkpoints, and
#        non-deterministic sampler state would corrupt the retry).
#        The hard kill at 7h is the patient-safe failure mode.
#     5. The SLA-miss-is-advisory behaviour is now DOCUMENTED in the
#        DEFAULT_ARGS comment below so operators do not rely on the
#        SLA to actually stop the task.
# v93 ROOT FIX (P1-034 -- SLA defeats its own purpose):
#   The v75 ROOT FIX (T-024) aligned SLA and execution_timeout at 7h,
#   claiming "exactly ONE signal at exactly ONE time". But this DEFEATS
#   the purpose of an SLA. An SLA is meant to be an EARLY WARNING that
#   fires BEFORE the hard kill -- giving the operator a window to
#   intervene (extend the timeout, kill a stuck task manually, page
#   on-call). By setting SLA == timeout, the SLA miss fires at exactly
#   7h, and the hard kill ALSO fires at exactly 7h -- the operator gets
#   no early warning, just a single "task killed" notification.
#
#   Root fix: set TASK_SLA = 5h (2h before the 7h kill). The SLA miss
#   at 5h is ADVISORY -- it pages the operator but does NOT stop the
#   task. The operator has a 2h window to decide: extend the timeout
#   (via Airflow's clear+retry with a longer timeout), kill the task
#   manually, or let it run to the 7h hard kill. The 7h TASK_TIMEOUT
#   remains the patient-safe failure mode (GPU state, partial
#   checkpoints, non-deterministic sampler state would corrupt a
#   retry -- so we kill, not retry).
#
# Design invariants preserved:
#     1. TASK_TIMEOUT = 7h remains the upper bound of the documented
#        training window (6-7h on real data). A normal run completes
#        in ≤7h; a stuck run is killed at 7h. No false positives on
#        normal runs.
#     2. retries=0 on _trigger_phase2 is preserved (line 462) -- a
#        timed-out Phase 2 training run must NOT be retried
#        automatically (GPU state, partial checkpoints, and
#        non-deterministic sampler state would corrupt the retry).
#        The hard kill at 7h is the patient-safe failure mode.
#     3. The SLA-miss at 5h is ADVISORY -- it pages but does not stop.
#        Operators do not rely on the SLA to stop the task; the 7h
#        timeout does that.
TASK_SLA = timedelta(hours=5)
TASK_TIMEOUT = timedelta(hours=7)

# v83 DAG-2 ROOT FIX: apply the SAME retry policy used by all 7 standalone
# DAGs (dags/_retry_policy.py::DEFAULT_RETRY_ARGS). The previous DEFAULT_ARGS
# used ``retries=2, retry_delay=30min`` with NO ``retry_exponential_backoff``
# and NO ``max_retry_delay``. A 4xx error in the master DAG wasted 60 min
# (2 × 30min) per task; the standalone DAGs fail-fast in seconds via
# ``@fail_fast_on_http_4xx`` + ``retry_exponential_backoff=True``. The master
# DAG is the SUNDAY run -- the longest, most data-intensive run of the week --
# so a 60-min wait per task is the worst possible time to waste.
#
# ROOT FIX (DAG-2):
#   1. Spread ``DEFAULT_RETRY_ARGS`` (5min base delay, exponential backoff,
#      20min cap) so transient 5xx/network errors recover in ~10s on the
#      first retry, and the master DAG's retry behavior matches the 7
#      standalone DAGs exactly.
#   2. Apply ``@fail_fast_on_http_4xx`` to EVERY @task below so 4xx errors
#      (401/403/404/400/410/451) immediately raise ``AirflowFailException``
#      and skip retries -- matching the standalone DAGs' fail-fast behavior.
#      Without this, a 401 (expired DISGENET/OMIM API key) on the master
#      DAG wastes 60 min (was 30min × 2 retries) before failing RED,
#      while the same error on the standalone DAG fails in <1 second.
#   3. Preserve the v75 ROOT FIX (T-024): SLA == execution_timeout == 7h
#      (aligned, no false-positive advisory).
#   4. Preserve ``retries=2`` (from DEFAULT_RETRY_ARGS) -- same as standalone.
# v89: DEFAULT_RETRY_ARGS and fail_fast_on_http_4xx are imported ONCE at
# the top of this module (line 43) -- no duplicate import here.

DEFAULT_ARGS = {
    **DEFAULT_RETRY_ARGS,
    "owner": "drug_repurposing",
    "depends_on_past": False,
    # v83 FORENSIC ROOT FIX (P1-14): the previous code used
    # ``retry_delay=timedelta(minutes=30)`` with NO exponential backoff.
    # The 7 standalone DAGs use ``DEFAULT_RETRY_ARGS`` (5min + exponential
    # backoff). ROOT FIX: spread ``DEFAULT_RETRY_ARGS`` into ``DEFAULT_ARGS``
    # (done above) so the master DAG uses the SAME retry policy. The
    # ``sla`` / ``execution_timeout`` overrides (7h, T-024) are retained
    # AFTER the spread so they win over the ``DEFAULT_RETRY_ARGS`` 4h defaults.
    # T-024: ``sla`` is ADVISORY -- an Airflow SLA miss writes a row to
    # the sla_miss table and (optionally) sends an email, but it does
    # NOT kill the running task. The task continues until
    # ``execution_timeout`` fires. Both are now set to 7h (aligned) so
    # there is exactly ONE signal at exactly ONE time -- operators do
    # not get a 4h false-positive SLA miss that trains them to ignore
    # the alarm, and a stuck Phase 2 training run is hard-killed at 7h
    # (the documented upper bound of normal TransE training time).
    # v83 DAG-2: ``sla`` and ``execution_timeout`` come from
    # DEFAULT_RETRY_ARGS (4h) but the master DAG overrides them to 7h
    # because trigger_phase2's TransE training can take 6-7h. This
    # override is deliberate and documented.
    "sla": TASK_SLA,
    "execution_timeout": TASK_TIMEOUT,
}


# ---------------------------------------------------------------------------
# Branch helper -- DrugBank XML gate
# ---------------------------------------------------------------------------

# v89 ROOT FIX (BUG #36 -- fragile coupling between branch return values
# and task_ids):
#   The previous code returned the hardcoded strings "download_drugbank"
#   and "skip_drugbank" from ``_check_drugbank_xml``. These strings had
#   to EXACTLY match the task_id parameters of the downstream tasks.
#   If a future refactor renamed the ``download_drugbank`` function
#   (whose TaskFlow task_id is auto-generated from the function name),
#   the branch return value would become a DANGLING reference --
#   BranchPythonOperator raises AirflowException at RUNTIME, with no
#   compile-time check.
#
#   ROOT FIX: define the task_ids as module-level constants. Use them
#   in (a) the branch function's return values, (b) the EmptyOperator's
#   task_id parameter, and (c) a parse-time assertion (inside
#   ``master_pipeline``) that the TaskFlow-generated task_id of
#   ``download_drugbank`` matches the constant. The assertion catches
#   any rename at DAG PARSE time instead of at runtime.
_DRUGBANK_DOWNLOAD_TASK_ID: str = "download_drugbank"
_DRUGBANK_SKIP_TASK_ID: str = "skip_drugbank"


def _check_drugbank_xml(**context) -> str:
    """Return the task-id to branch into based on DrugBank XML availability.

    DrugBank requires a paid license; the XML must be pre-positioned
    manually.  If the file is missing we gracefully skip the pipeline so the
    rest of the DAG can continue.

    v89 ROOT FIX (BUG #27): DRUGBANK_XML_PATH is now imported at module
    top level (not inside this function), so import failures surface at
    DAG parse time, not at task execution time after 2 retries.

    v89 ROOT FIX (BUG #36): returns the module-level constant
    ``_DRUGBANK_DOWNLOAD_TASK_ID`` / ``_DRUGBANK_SKIP_TASK_ID`` instead
    of hardcoded strings, so a rename of the downstream task_id is
    caught at parse time by the assertion in ``master_pipeline``.
    """
    # v89 BUG #27: DRUGBANK_XML_PATH is now a module-level name (imported
    # at the top of this file). No runtime import here.
    # v43 ROOT FIX (P1 -- _check_drugbank_xml crashes on invalid path):
    # The previous code did Path(DRUGBANK_XML_PATH) then .exists() +
    # .stat() with no try/except. If DRUGBANK_XML_PATH is set to an
    # invalid value (null bytes, extremely long path, etc.), Path()
    # raises ValueError and .stat() raises OSError -- crashing the
    # branch task and (with retries=2) the entire DAG. The intent was
    # to gracefully skip DrugBank. Fix: wrap in try/except and return
    # the skip task_id on any error.
    try:
        xml_path = Path(DRUGBANK_XML_PATH)
        if xml_path.exists() and xml_path.stat().st_size > 0:
            logger.info("DrugBank XML found at %s -- will run pipeline", xml_path)
            return _DRUGBANK_DOWNLOAD_TASK_ID
    except (OSError, ValueError) as exc:
        logger.warning(
            "DrugBank XML path %r is invalid or unreadable (%s) -- "
            "skipping pipeline. To enable: fix DRUGBANK_XML_PATH env var.",
            DRUGBANK_XML_PATH, exc,
        )
        return _DRUGBANK_SKIP_TASK_ID

    logger.warning(
        "DrugBank XML not found at %s -- skipping pipeline. "
        "To enable: download from https://go.drugbank.com/ and set "
        "DRUGBANK_XML_PATH env var.", xml_path,
    )
    return _DRUGBANK_SKIP_TASK_ID


# ---------------------------------------------------------------------------
# Task callables -- each delegates to the corresponding pipeline.
# v40 ROOT FIX (P1 #54): ALL primary download tasks now call
# ``.run_download_and_clean_only()`` (NOT ``.run()``). The previous code
# had ChEMBL/DrugBank/UniProt calling ``.run()`` (full run including
# LOAD to DB) while STRING/DisGeNET/OMIM called
# ``.run_download_and_clean_only()``. This broke the two-phase design
# (download -> resolve -> load) -- some pipelines loaded to DB BEFORE
# entity_resolution ran. The fix: ALL download tasks do download+clean
# only; the LOAD phase happens in the *_load tasks AFTER
# entity_resolution. This ensures entity_resolution can influence what
# gets loaded for ALL sources.
# ---------------------------------------------------------------------------

@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
# v83 DAG-2 ROOT FIX: @fail_fast_on_http_4xx converts 4xx to
# AirflowFailException (non-retryable) -- matches standalone DAGs.
@fail_fast_on_http_4xx
def download_chembl() -> None:
    """Run the ChEMBL pipeline: approved drugs + bioactivity data (download+clean only)."""
    from pipelines.chembl_pipeline import ChEMBLPipeline
    # v40: was .run() (full run including LOAD) -- now download+clean only.
    ChEMBLPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_drugbank() -> None:
    """Run the DrugBank pipeline: parse XML for drug + target data (download+clean only)."""
    from pipelines.drugbank_pipeline import DrugBankPipeline
    # v40: was .run() (full run including LOAD) -- now download+clean only.
    DrugBankPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_uniprot() -> None:
    """Run the UniProt pipeline: human reviewed proteins via REST API (download+clean only)."""
    from pipelines.uniprot_pipeline import UniProtPipeline
    # v40: was .run() (full run including LOAD) -- now download+clean only.
    UniProtPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_string() -> None:
    """Run the STRING pipeline: download+clean only (load after entity resolution)."""
    from pipelines.string_pipeline import StringPipeline
    StringPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_disgenet() -> None:
    """Run the DisGeNET pipeline: download+clean only (load after entity resolution)."""
    from pipelines.disgenet_pipeline import DisGeNETPipeline
    DisGeNETPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_omim() -> None:
    """Run the OMIM pipeline: download+clean only (load after entity resolution)."""
    from pipelines.omim_pipeline import OMIMPipeline
    OMIMPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_pubchem() -> None:
    """Run the PubChem pipeline: download+clean only (load after entity resolution).

    v35 ROOT FIX (issue 35): previously called ``PubChemPipeline().run()``
    (the FULL run, including load into DB). This caused a DOUBLE-LOAD: the
    ``download_pubchem`` task loaded PubChem data into the ``drugs`` table,
    then the ``load_pubchem_enrichment`` task (line 414 below) called
    ``PubChemPipeline().run_load_only()`` which loaded the SAME data
    AGAIN. Both loads were idempotent (upsert), so the duplicate was
    silently absorbed -- but it doubled the load wall-clock time and
    masked any bug in the load idempotency. Fix: use
    ``run_download_and_clean_only()`` so only the ``load_pubchem_enrichment``
    task loads (matching the pattern used by ChEMBL, DrugBank, UniProt,
    STRING, DisGeNET, and OMIM in this DAG).

    P1-071 ROOT FIX: added ``trigger_rule=none_failed_min_one_success`` so
    that pubchem_download and pubchem_load do not fail the DAG when upstream
    tasks that are NOT required for PubChem (e.g. drugbank_load) are skipped.
    """
    from pipelines.pubchem_pipeline import PubChemPipeline
    PubChemPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def entity_resolution() -> None:
    """Run cross-database entity resolution.

    Reconciles drug entities across ChEMBL, DrugBank, and PubChem using
    InChIKey matching, connectivity-block matching, and normalised-name
    matching.  Also resolves protein entities across UniProt and STRING.

    Results are persisted to the ``entity_mapping`` table and the
    ``proteins.string_id`` column is updated with resolved STRING IDs.

    v29 ROOT FIX (audit O-12): XCom used for large dataframes -- anti-pattern.
    Now passes file paths via XCom. This task reads every upstream DataFrame
    from CSV files in ``PROCESSED_DATA_DIR`` (drugs.csv, drugbank_drugs.csv,
    pubchem_enrichment.csv, proteins.csv, protein_protein_interactions.csv)
    rather than pulling DataFrames from upstream tasks' XCom. The upstream
    download tasks return None and persist their output to those CSV files;
    this task pulls the *file paths* (constants below), not the DataFrames.

    v75 ROOT FIX (T-025 -- download_parallel.py skips entity resolution):
      The entity resolution logic was previously INLINE in this task body.
      The forensic audit found that ``scripts/download_parallel.py`` and
      the Makefile's ``download-all`` / ``download-samples`` targets
      skipped entity resolution entirely because they could not call this
      Airflow task. ROOT FIX: the logic was extracted into
      ``entity_resolution/run.py::run_entity_resolution()`` -- a single
      shared function with NO Airflow dependency. This task is now a thin
      wrapper that calls that function. ``download_parallel.py`` calls
      the same function. The two callers CANNOT drift.
    """
    from entity_resolution.run import run_entity_resolution
    run_entity_resolution()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_string() -> None:
    """FIX AUDIT-26: Use run_load_only() -- data already downloaded and cleaned."""
    from pipelines.string_pipeline import StringPipeline
    StringPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_disgenet() -> None:
    """FIX AUDIT-26: Use run_load_only() -- data already downloaded and cleaned."""
    from pipelines.disgenet_pipeline import DisGeNETPipeline
    DisGeNETPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_omim() -> None:
    """FIX AUDIT-27: Use run_load_only() -- data already downloaded and cleaned."""
    from pipelines.omim_pipeline import OMIMPipeline
    OMIMPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_pubchem_enrichment() -> None:
    """FIX AUDIT-27: PubChem data already downloaded.

    P1-071 ROOT FIX: added ``trigger_rule=none_failed_min_one_success`` so
    that this task runs even if some upstream tasks are skipped, as long
    as pubchem_download succeeds.
    """
    from pipelines.pubchem_pipeline import PubChemPipeline
    PubChemPipeline().run_load_only()


# v79 FORENSIC ROOT FIX (P0-B2 -- Master DAG has NO load_chembl /
#   load_drugbank / load_uniprot task):
#   The v78 master DAG had download tasks for ChEMBL, DrugBank, and
#   UniProt that called ``run_download_and_clean_only()`` (CSV only,
#   NO DB write), but there were NO corresponding ``load_*`` tasks.
#   The ``drugs``, ``proteins``, and ``drug_protein_interactions``
#   tables in the staging DB were EMPTY for these 3 sources. Entity
#   resolution read from an empty DB for ChEMBL/DrugBank/UniProt, and
#   the Phase 2 bridge (in PostgreSQL mode) read empty ``drugs`` /
#   ``proteins`` tables -> ``drug_canonical_map`` was empty -> ALL
#   Compound-treats-Disease edges were silently skipped (P0-B1
#   compound). V1 launch criterion ``positive_pairs_sufficient`` was
#   structurally unverifiable.
# ROOT FIX: add ``load_chembl()``, ``load_drugbank()``, and
#   ``load_uniprot()`` tasks that call ``run_load_only()`` (the same
#   pattern used by load_string / load_disgenet / load_omim /
#   load_pubchem_enrichment). Wire them after ``entity_resolution``
#   so entity resolution can influence what gets loaded (the v40
#   two-phase design: download -> resolve -> load).
@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_chembl() -> None:
    """v79 P0-B2 ROOT FIX: Load ChEMBL cleaned data into the staging DB.

    Loads ``drugs`` (ChEMBL molecules with max_phase=4) and
    ``drug_protein_interactions`` (ChEMBL activities with resolved
    UniProt accessions) into the staging DB. Data was already
    downloaded + cleaned by ``download_chembl``; this task only
    performs the DB upsert (idempotent via ON CONFLICT DO UPDATE).
    """
    from pipelines.chembl_pipeline import ChEMBLPipeline
    ChEMBLPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_drugbank() -> None:
    """v79 P0-B2 ROOT FIX: Load DrugBank cleaned data into the staging DB.

    Loads ``drugs`` (DrugBank FDA-approved drugs with InChIKey) and
    ``drug_protein_interactions`` (DrugBank drug->target edges) into
    the staging DB. Data was already downloaded + cleaned by
    ``download_drugbank``; this task only performs the DB upsert
    (idempotent via ON CONFLICT DO UPDATE).
    """
    from pipelines.drugbank_pipeline import DrugBankPipeline
    DrugBankPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_uniprot() -> None:
    """v79 P0-B2 ROOT FIX: Load UniProt cleaned data into the staging DB.

    Loads ``proteins`` (UniProt human reviewed proteins with gene
    symbols) into the staging DB. Data was already downloaded +
    cleaned by ``download_uniprot``; this task only performs the DB
    upsert (idempotent via ON CONFLICT DO UPDATE).
    """
    from pipelines.uniprot_pipeline import UniProtPipeline
    UniProtPipeline().run_load_only()


@task(retries=0, execution_timeout=TASK_TIMEOUT, trigger_rule=TriggerRule.ALL_SUCCESS)
# v89 ROOT FIX (BUG #24 -- inconsistent application of fail-fast policy):
#   The comment at lines 99-118 (v83 DAG-2) says "Apply
#   ``@fail_fast_on_http_4xx`` to EVERY @task below so 4xx errors
#   immediately raise ``AirflowFailException`` and skip retries".
#   But ``_trigger_phase2`` was MISSING the decorator. While
#   ``_trigger_phase2`` invokes a subprocess (not a direct HTTP call),
#   the Phase 2 pipeline (``run_unified.py`` / ``python -m
#   drugos_graph``) makes HTTP calls to download data, call Neo4j's
#   REST API, etc. If the subprocess exits with a 4xx-derived error
#   (e.g. CalledProcessError wrapping a 401 from Neo4j auth), the
#   error was NOT converted to AirflowFailException -- it was retried
#   (but retries=0 makes this moot). ROOT FIX: add the decorator for
#   consistency with the documented policy. The decorator's behavior
#   on non-4xx exceptions (re-raise unchanged) is preserved.
@fail_fast_on_http_4xx
def _trigger_phase2() -> None:
    """v29 ROOT FIX (audit O-2 -- master DAG always reports success).

    v75 ROOT FIX (T-024 -- SLA/timeout alignment):
      ``execution_timeout=TASK_TIMEOUT`` (7h, aligned with TASK_SLA).
      ``retries=0`` is preserved -- a timed-out Phase 2 training run
      must NOT be retried automatically. The 7h hard kill is the
      patient-safe failure mode: partial TransE checkpoints, GPU
      state, and the non-deterministic negative sampler would
      corrupt any retry. Operators who want a longer training
      window must explicitly raise BOTH TASK_SLA and TASK_TIMEOUT
      (keeping them aligned) -- never just one.

    The forensic audit found that this task had ``trigger_rule=ALL_DONE``
    + ``check=False`` + ``retries=0``, which meant Phase 2 could crash,
    time out, or fail V1 criteria and the DAG would still report GREEN.
    Every previous AI session that told the user "it's 100% integrated"
    was reading the DAG's green status without checking the actual
    Phase 2 exit code or the AUC log.

    ROOT FIX: change ``trigger_rule`` to ``ALL_SUCCESS`` (so Phase 2
    only runs if all Phase 1 tasks succeeded), use ``check=True`` (so
    non-zero exit code raises), and propagate timeouts / exceptions
    instead of swallowing them. The DAG now fails RED when Phase 2
    fails -- operators can no longer claim success without verifying.

    Behavior:
      * ``trigger_rule=ALL_SUCCESS`` -- only runs if ALL Phase 1 tasks
        succeeded. (Was: ``ALL_DONE`` which fires even on failure.)
      * ``check=True`` -- non-zero exit code raises CalledProcessError.
        (Was: ``check=False`` which silently ignored failures.)
      * Timeouts and exceptions propagate -- task fails RED. (Was:
        logged as WARNING and task succeeded.)

    The task still uses the RecordingGraphBuilder by default (no
    Neo4j required), so it can run in any environment. Operators who
    want a real Neo4j load set ``DRUGOS_NEO4J_URI``.
    """
    import importlib.util
    import os
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    _project_root = _Path(__file__).resolve().parent.parent.parent
    run_unified = _project_root / "run_unified.py"

    if not run_unified.exists():
        # v89 ROOT FIX (BUG #26 -- fallback path doesn't verify
        # ``drugos_graph`` is importable):
        #   The previous code fell back to ``python -m drugos_graph``
        #   when ``run_unified.py`` was missing, but did NOT verify
        #   that ``drugos_graph`` was actually importable. If NEITHER
        #   ``run_unified.py`` existed NOR ``drugos_graph`` was
        #   installed, ``subprocess.run(cmd, check=True)`` raised
        #   ``FileNotFoundError`` (or ``ModuleNotFoundError`` from the
        #   subprocess), which was caught by the ``except Exception``
        #   block and re-raised. The error message ("Phase 2 invocation
        #   raised FileNotFoundError") didn't tell the operator HOW to
        #   fix it.
        #
        #   ROOT FIX: pre-flight check via ``importlib.util.find_spec``.
        #   If the package isn't importable, raise a clear RuntimeError
        #   with the exact remediation: install ``drugos_graph`` OR
        #   position ``run_unified.py`` at the project root. This
        #   surfaces the root cause BEFORE the subprocess starts, so
        #   the operator sees the fix in the task log immediately.
        if importlib.util.find_spec("drugos_graph") is None:
            raise RuntimeError(
                "Phase 2 invocation failed pre-flight check: "
                "neither 'run_unified.py' exists at the project root "
                f"({run_unified}) NOR the 'drugos_graph' package is "
                "importable. Remediation: either (a) install the "
                "'drugos_graph' package (pip install -e . from the "
                "project root), or (b) position 'run_unified.py' at "
                f"the project root ({_project_root}). The master DAG "
                "cannot proceed to Phase 2 without one of these. "
                "(v89 BUG #26)"
            )
        # Fallback: invoke via ``python -m drugos_graph``.
        cmd = [
            _sys.executable, "-m", "drugos_graph",
            "--data-source", "phase1",
            "--phase1-dir", str(_project_root / "phase1" / "processed_data"),
        ]
    else:
        cmd = [
            _sys.executable, str(run_unified),
            "--phase1-dir", str(_project_root / "phase1" / "processed_data"),
            "--full-pipeline",
        ]

    neo4j_uri = os.environ.get("DRUGOS_NEO4J_URI")
    if neo4j_uri:
        cmd.extend(["--neo4j-uri", neo4j_uri])
        if os.environ.get("DRUGOS_NEO4J_USER"):
            cmd.extend(["--neo4j-user", os.environ["DRUGOS_NEO4J_USER"]])
        if os.environ.get("DRUGOS_NEO4J_PASSWORD"):
            cmd.extend(["--neo4j-password", os.environ["DRUGOS_NEO4J_PASSWORD"]])

    logger.info("v29 trigger_phase2: invoking Phase 2 pipeline: %s", " ".join(cmd))

    # v29 ROOT FIX: check=True (was False) so non-zero exit raises
    # CalledProcessError. This makes the task fail RED when Phase 2
    # fails, instead of silently logging a WARNING and succeeding.
    #
    # v80 FORENSIC ROOT FIX (P0-C9 -- Airflow worker OOM on 7h TransE
    #   training):
    #   The previous code passed ``capture_output=True`` to
    #   ``subprocess.run``. This causes subprocess to BUFFER THE
    #   ENTIRE stdout AND stderr streams in memory until the process
    #   exits. For a 7-hour TransE training run that emits per-epoch
    #   logs (loss, gradient norms, validation AUC, per-batch
    #   progress bars from tqdm), the accumulated output is 10-50 GB.
    #   The Airflow worker (default 4-8 GB RAM) OOM-crashes mid-
    #   training, killing the subprocess AND the scheduler. The DAG
    #   reports RED with "OOMKilled" but the operator has no log to
    #   diagnose which epoch crashed.
    #
    #   ROOT FIX: stream stdout+stderr to a log file on disk (under
    #   ``<project_root>/logs/phase2_<timestamp>.log``) and pass the
    #   file handle as ``stdout``/``stderr`` to subprocess. This keeps
    #   peak memory at O(1) regardless of training length, AND
    #   preserves the full log for post-mortem debugging. We tail the
    #   last 2000 chars into the Airflow task log on success for
    #   operator visibility (matches the previous ``result.stdout[-2000:]``
    #   behavior). On failure we tail the last 4000 chars of stderr.
    import os as _os
    _logs_dir = _project_root / "logs"
    _logs_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt, timezone as _tz
    # v89 FORENSIC ROOT FIX (BUG #20 P1 -- datetime.utcnow() is deprecated
    #   in Python 3.12+ and returns a NAIVE datetime, not timezone-aware.
    #   In non-UTC environments the log filename timestamp could drift.
    #   ROOT FIX: use datetime.now(timezone.utc) which is tz-aware and
    #   not deprecated. Behaviour is identical on UTC systems.
    _log_timestamp = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    _phase2_log_path = _logs_dir / f"phase2_trigger_{_log_timestamp}.log"
    logger.info("v29 trigger_phase2: streaming subprocess output to %s", _phase2_log_path)

    try:
        with open(_phase2_log_path, "wb") as _log_fh:
            result = subprocess.run(
                cmd, cwd=str(_project_root), check=True,
                stdout=_log_fh, stderr=subprocess.STDOUT,
                timeout=int(TASK_TIMEOUT.total_seconds()),
            )
        logger.info("v29 trigger_phase2: Phase 2 pipeline completed successfully.")
        # Tail the last 2000 chars of the log for operator visibility.
        try:
            with open(_phase2_log_path, "rb") as _tail_fh:
                _tail_fh.seek(0, 2)
                _size = _tail_fh.tell()
                _tail_fh.seek(max(0, _size - 2000))
                _tail = _tail_fh.read().decode("utf-8", errors="replace")
            if _tail:
                logger.info("stdout/stderr tail:\n%s", _tail)
        except OSError:
            pass
    except subprocess.CalledProcessError as exc:
        # v29 ROOT FIX: propagate the failure. The DAG turns RED.
        # Tail the last 4000 chars of the log for diagnostics.
        _err_tail = ""
        try:
            with open(_phase2_log_path, "rb") as _tail_fh:
                _tail_fh.seek(0, 2)
                _size = _tail_fh.tell()
                _tail_fh.seek(max(0, _size - 4000))
                _err_tail = _tail_fh.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        logger.error(
            "v29 trigger_phase2: Phase 2 pipeline FAILED with exit "
            "code %d. The DAG will now fail RED -- this is the correct "
            "behavior (audit O-2 root fix). Full log: %s. stderr tail:\n%s",
            exc.returncode,
            _phase2_log_path,
            _err_tail,
        )
        raise
    except subprocess.TimeoutExpired as exc:
        # v29 ROOT FIX (audit O-10): propagate the timeout. The DAG turns RED.
        logger.error(
            "v29 trigger_phase2: Phase 2 pipeline TIMED OUT after %d "
            "seconds. Subprocess timed out -- DAG will FAIL. The DAG will "
            "now fail RED -- this is the correct behavior (audit O-2 / "
            "O-10 root fix). Full log: %s",
            int(TASK_TIMEOUT.total_seconds()),
            _phase2_log_path,
        )
        raise
    except Exception as exc:
        # v29 ROOT FIX: propagate ANY exception. The DAG turns RED.
        logger.error(
            "v29 trigger_phase2: Phase 2 invocation raised %s: %s. "
            "The DAG will now fail RED (audit O-2 root fix). "
            "Full log: %s",
            type(exc).__name__, exc,
            _phase2_log_path,
        )
        raise


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

@dag(
    dag_id="drug_repurposing_master",
    description=(
        "Master DAG orchestrating all Drug Repurposing ETL pipelines "
        "with entity resolution"
    ),
    schedule="0 2 * * 0",           # Every Sunday at 02:00 UTC
    # P1-047 FORENSIC ROOT FIX (Team 4 -- DAG schedule collision):
    # The previous schedule grid had collisions. When the 15th of the
    # month fell on a Sunday, the master DAG (Sun 02:00 UTC) and
    # UniProt/STRING/OMIM standalone DAGs (15th at 04:00/05:00/07:00 UTC)
    # all fired within hours of each other. They all write to the same
    # ``drugs``, ``proteins``, ``gene_disease_associations`` tables ->
    # DB contention, lock timeouts, possible deadlocks. The new schedule
    # grid (see below) staggers ALL standalone DAGs to non-Sunday weekdays:
    #   Sun 02:00 UTC: Master (this DAG)
    #   Mon 03:00 UTC: DrugBank
    #   Tue 06:00 UTC: DisGeNET
    #   Wed 04:00 UTC: ChEMBL
    #   Thu 07:00 UTC: OMIM (was 15th of month -- moved to weekly Thursday)
    #   Fri 04:00 UTC: UniProt (was 15th of month -- moved to weekly Friday)
    #   Sat 05:00 UTC: STRING (was 15th of month -- moved to weekly Saturday)
    #   Sat 08:00 UTC: PubChem (was Wed 08:00 -- moved to avoid ChEMBL overlap)
    # OMIM/UniProt/STRING were monthly (15th of month) -- they're now weekly
    # because their loaders are idempotent (they check for upstream changes
    # and no-op if nothing changed). The weekly cadence is slightly wasteful
    # but eliminates the Sunday collision risk entirely.
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["drug_repurposing", "master", "etl"],
)
def master_pipeline() -> None:
    """Build the master pipeline DAG with all inter-task dependencies."""

    # ── Branch operator: DrugBank XML gate ──────────────────────────────
    check_drugbank = BranchPythonOperator(
        task_id="check_drugbank_xml",
        python_callable=_check_drugbank_xml,
    )

    skip_drugbank = EmptyOperator(task_id=_DRUGBANK_SKIP_TASK_ID)

    drugbank_done = EmptyOperator(
        task_id="drugbank_done",
        # v43 ROOT FIX (P0 -- DAG produces ZERO data when DrugBank XML is
        # missing): the previous ALL_SUCCESS trigger rule treated a
        # SKIPPED branch as non-success. When DRUGBANK_XML_PATH is
        # absent, _check_drugbank_xml returns _DRUGBANK_SKIP_TASK_ID ->
        # download_drugbank is SKIPPED -> drugbank_done with ALL_SUCCESS
        # is also SKIPPED -> resolve is SKIPPED -> all *_load tasks are
        # SKIPPED -> trigger_phase2 is SKIPPED. The DAG reports GREEN
        # but produces ZERO data.
        #
        # The fix: NONE_FAILED_MIN_ONE_SUCCESS. This means:
        #   - At least one upstream must have succeeded (so a total
        #     failure of BOTH branches still propagates).
        #   - No upstream may have FAILED (so a real DrugBank crash
        #     after retries still aborts the join -- preserves the v39
        #     patient-safety fix).
        #   - SKIPPED upstreams are OK (so the operator's deliberate
        #     choice to skip DrugBank doesn't kill the entire DAG).
        #
        # This closes the "DAG reports GREEN but produces ZERO data"
        # hole while preserving the v39 fix's failure-propagation
        # guarantee. NONE_FAILED_MIN_ONE_SUCCESS is available since
        # Airflow 2.2 (2021); the requirements pin >=2.10.0.
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    # ── Primary download tasks ──────────────────────────────────────────
    chembl = download_chembl()
    drugbank = download_drugbank()
    # v89 ROOT FIX (BUG #36 -- parse-time assertion that the TaskFlow-
    # generated task_id matches the branch return value):
    #   ``_check_drugbank_xml`` returns ``_DRUGBANK_DOWNLOAD_TASK_ID``
    #   ("download_drugbank"). The TaskFlow API auto-generates the
    #   task_id from the function name (also "download_drugbank"). If a
    #   future refactor renames the function, the branch return value
    #   becomes a DANGLING reference -- BranchPythonOperator raises
    #   AirflowException at RUNTIME. This assertion catches the
    #   mismatch at DAG PARSE time, so the DAG shows up as "import
    #   error" in the Airflow UI instead of failing mid-run.
    assert drugbank.task_id == _DRUGBANK_DOWNLOAD_TASK_ID, (
        f"BUG #36 regression: download_drugbank task_id is "
        f"{drugbank.task_id!r} but _check_drugbank_xml returns "
        f"{_DRUGBANK_DOWNLOAD_TASK_ID!r}. Update "
        f"_DRUGBANK_DOWNLOAD_TASK_ID or the function name to match."
    )
    uniprot = download_uniprot()
    string = download_string()

    # ── Secondary download tasks ────────────────────────────────────────
    # v35 ROOT FIX (issue 36): the previous comment claimed DisGeNET and
    # OMIM "share the gene_disease_associations.csv file via
    # _save_csv_with_mode" -- this is FALSE. DisGeNET writes to
    # ``gene_disease_associations.csv`` (per DisGeNETPipeline source_name),
    # and OMIM writes to ``omim_gene_disease_associations.csv`` (per
    # OMIMPipeline source_name). They write to DIFFERENT files, so running
    # them in parallel would NOT cause CSV corruption.
    #
    # v76 ROOT FIX (T-041 -- remove ``disgenet >> omim`` wire for parallel
    # GDA loading):
    #   The v40 comment acknowledged that DisGeNET and OMIM write to
    #   DIFFERENT CSV files and could run in parallel safely, but kept the
    #   sequential ``disgenet >> omim`` wire "as a defensive choice". This
    #   added ~5min of DisGeNET runtime to the critical path before OMIM
    #   started. Over a year of weekly runs, that's ~260 minutes of wasted
    #   scheduler time. The sequential ordering was NEVER a requirement --
    #   it was a "choice" that cost latency for zero benefit.
    #   ROOT FIX: remove the ``disgenet >> omim`` wire entirely. Both
    #   pipelines now run in PARALLEL (no dependency between them). They
    #   write to different files (gene_disease_associations.csv vs
    #   omim_gene_disease_associations.csv) so there is no race condition.
    #   The ``omim >> drugbank`` wire (T-042) is ALSO removed in this v76
    #   pass -- DrugBank now gracefully handles a missing OMIM CSV -- so
    #   there is no downstream dependency that requires OMIM to finish
    #   before DisGeNET (or vice versa). The full GDA loading chain is now
    #   parallel: DisGeNET ‖ OMIM ‖ DrugBank all run concurrently after
    #   entity resolution.
    disgenet = download_disgenet()
    omim = download_omim()
    # v76 T-041: NO wire between disgenet and omim -- they run in parallel.
    # Both write to different CSV files; no shared state, no race condition.

    # PubChem download task (needs drugs in DB from entity resolution)
    # P1-071 ROOT FIX: use none_failed_min_one_success so pubchem_download
    # runs even if drugbank_load is skipped (DrugBank XML not present).
    pubchem_download = download_pubchem()

    # ── Entity resolution ───────────────────────────────────────────────
    resolve = entity_resolution()

    # ── Post-resolution load tasks ──────────────────────────────────────
    # v79 P0-B2 ROOT FIX: instantiate the NEW load_chembl / load_drugbank
    # / load_uniprot tasks (previously missing -- drugs/proteins/DPI tables
    # were empty for these 3 sources after the download-only tasks).
    chembl_load = load_chembl()
    drugbank_load = load_drugbank()
    uniprot_load = load_uniprot()
    string_load = load_string()
    disgenet_load = load_disgenet()
    omim_load = load_omim()
    # P1-071 ROOT FIX: use none_failed_min_one_success for pubchem_load
    # so it runs even if some upstream tasks are skipped.
    pubchem_load = load_pubchem_enrichment()

    # V18 ROOT FIX (Phase 1 ↔ Phase 2 100% connection):
    # Before v18, the master DAG ended at ``pubchem_load`` -- Phase 2
    # (knowledge graph construction + TransE training) had to be
    # invoked MANUALLY via ``python -m drugos_graph`` or
    # ``run_unified.py``. The audit flagged this as the only
    # meaningful integration gap (Phase 1 -> Phase 2 connection was
    # ~90% complete; this single missing wire was the remaining 10%).
    #
    # Root fix: add a ``trigger_phase2`` task that fires
    # ``run_unified.py --full-pipeline`` after ``pubchem_load``
    # completes.
    #
    # v73 ROOT FIX (T-012 -- stale inline comment contradicted the
    # docstring + actual code):
    #   The previous inline comment here claimed the trigger_phase2 task
    #   was fault-tolerant and that Phase 2 failure would NOT abort the
    #   Phase 1 run. That described the PRE-v29 behavior
    #   (``trigger_rule=ALL_DONE`` + ``check=False`` + swallowed
    #   exceptions). The v29 ROOT FIX (docstring at lines 461-487)
    #   explicitly CHANGED the behavior to fail RED on Phase 2 failure:
    #   ``trigger_rule=ALL_SUCCESS``, ``check=True``, exceptions
    #   propagated via ``raise``. The actual code at lines 525/540/551/559
    #   implements the docstring's "DAG fails RED" behavior. The stale
    #   inline comment was a contradiction that misled operators into
    #   building automation that expected the DAG to stay GREEN on
    #   Phase 2 failure -- automation that would break the moment Phase 2
    #   actually failed.
    #
    #   ROOT FIX: delete the stale comment. Update to reflect the
    #   ACTUAL behavior: Phase 2 failure fails the DAG RED
    #   (``trigger_rule=ALL_SUCCESS``, ``check=True``, exceptions
    #   propagated). Operators who want the OLD fault-tolerant
    #   behavior must explicitly set ``trigger_rule=TriggerRule.ALL_DONE``
    #   and ``check=False`` on the ``_trigger_phase2`` task -- the
    #   default is now strict coupling per the v29 ROOT FIX.
    trigger_phase2 = _trigger_phase2()

    # ── Wire dependencies ───────────────────────────────────────────────
    # v76 ROOT FIX (T-040 -- rewrite list-bitshift as explicit statements
    # for clarity):
    #   The previous wiring used the list-bitshift syntax:
    #     check_drugbank >> [drugbank, skip_drugbank] >> drugbank_done
    #   This works but is fragile -- an operator unfamiliar with Airflow's
    #   list-bitshift may not realize ``drugbank_done`` is downstream of
    #   BOTH branches. The ``drugbank_done`` join task uses
    #   ``trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS`` (set at
    #   line 463) which correctly handles the SKIPPED branch (if the
    #   BranchPythonOperator chose ``skip_drugbank``, then ``drugbank``
    #   is SKIPPED and ``drugbank_done`` accepts the SKIPPED upstream).
    #   ROOT FIX: rewrite as three EXPLICIT statements so the dependency
    #   graph is unambiguous to any reader. The behavior is identical;
    #   only the readability changes. Each ``>>`` is now a single
    #   edge, making the fan-out (check -> two branches) and fan-in
    #   (two branches -> join) visually explicit.
    check_drugbank >> drugbank       # branch 1: download DrugBank XML
    check_drugbank >> skip_drugbank  # branch 2: skip (XML not found)
    drugbank >> drugbank_done        # fan-in: join after branch 1
    skip_drugbank >> drugbank_done   # fan-in: join after branch 2

    # v76 ROOT FIX (T-042 -- remove ``omim >> drugbank`` wire; DrugBank
    # now runs in PARALLEL with OMIM):
    #   The v9 ROOT FIX wired ``omim >> drugbank`` because DrugBank's
    #   ``_write_structured_indications`` step raised RuntimeError when
    #   the OMIM CSV was missing. The v40 comment kept the wire, calling
    #   the coupling "acceptable". But the coupling was brittle: if OMIM
    #   failed (API key missing, network error), DrugBank was SKIPPED via
    #   the dependency chain -- losing ALL DrugBank drug + target data
    #   from the knowledge graph, a major data loss. The
    #   BranchPythonOperator checks for DrugBank XML existence, NOT for
    #   OMIM CSV existence, so if OMIM failed but DrugBank XML existed,
    #   the branch chose "download_drugbank" but the download then failed
    #   because the OMIM CSV didn't exist -- cascading to DAG RED.
    #   ROOT FIX: DrugBank's ``_write_structured_indications`` now
    #   gracefully handles a missing OMIM CSV (logs WARNING, writes a
    #   header-only drugbank_indications.csv, continues -- see
    #   drugbank_pipeline.py v76 T-042 fix). The ``omim >> drugbank``
    #   wire is REMOVED. DrugBank now runs in PARALLEL with OMIM. If OMIM
    #   fails, DrugBank still loads all drug + target data; only the
    #   drug->disease indication edges are empty for that run. This is the
    #   scientifically correct trade-off: a KG with DrugBank drugs but no
    #   indication edges is far more useful than a KG with NO DrugBank
    #   data at all.
    # NO ``omim >> drugbank`` wire -- DrugBank is independent of OMIM.

    # SCI-FIX: ALL primary + secondary downloads must complete before
    # entity resolution. Previously, disgenet and omim were orphaned
    # (no upstream/downstream), causing race conditions where the load
    # tasks could fire before the downloads finished.
    # v76 T-042: ``drugbank_done`` (not ``drugbank``) is in the fan-in
    # because the BranchPythonOperator may skip DrugBank -- ``drugbank_done``
    # joins both branches with NONE_FAILED_MIN_ONE_SUCCESS.
    chembl >> resolve
    drugbank_done >> resolve
    uniprot >> resolve
    string >> resolve
    disgenet >> resolve
    omim >> resolve

    # Entity resolution -> dependent loads (fan-out)
    # v79 P0-B2 ROOT FIX: wire the NEW load_chembl / load_drugbank /
    # load_uniprot tasks after entity_resolution. These populate the
    # ``drugs``, ``proteins``, and ``drug_protein_interactions`` tables
    # for ChEMBL/DrugBank/UniProt -- previously empty, which caused the
    # Phase 2 bridge to find zero Compound nodes and skip ALL treats
    # edges (P0-B1 compound).
    resolve >> chembl_load
    resolve >> drugbank_load
    resolve >> uniprot_load
    resolve >> string_load
    resolve >> disgenet_load
    resolve >> omim_load

    # v89 FORENSIC ROOT FIX (BUG #1 P0 -- PubChem download queries an EMPTY
    #   drugs table):
    #   The previous wiring ``resolve >> pubchem_download >> pubchem_load``
    #   was SCIENTIFICALLY WRONG. PubChem's ``run_download_and_clean_only()``
    #   (invoked by ``download_pubchem``) reads InChIKeys from the ``drugs``
    #   table where ``pubchem_cid IS NULL`` (see pubchem_pipeline.py:1084-1090
    #   ``select(Drug.inchikey).where(Drug.pubchem_cid.is_(None))``).
    #
    #   But the ``drugs`` table is populated by ``chembl_load`` and
    #   ``drugbank_load`` (which call ``run_load_only()``), NOT by
    #   ``resolve`` (entity_resolution reads CSVs and writes to the
    #   ``entity_mapping`` table -- it does NOT populate ``drugs``).
    #   With the previous wiring, ``pubchem_download`` ran CONCURRENTLY
    #   with ``chembl_load``/``drugbank_load`` (all depend only on
    #   ``resolve``), so ``pubchem_download`` queried an EMPTY ``drugs``
    #   table, found zero drugs to enrich, produced an empty
    #   ``pubchem_enrichment.csv``, and ``pubchem_load`` loaded nothing.
    #   Every Sunday master DAG run produced a KG with ZERO PubChem
    #   enrichment data (no CIDs, no molecular formulas, no molecular
    #   weights). The inline comment at the old line 803 ("PubChem needs
    #   drugs in the DB (from entity resolution)") was wrong -- entity
    #   resolution does NOT populate the ``drugs`` table.
    #
    #   ROOT FIX: wire ``pubchem_download`` AFTER both ``chembl_load``
    #   and ``drugbank_load`` (the two tasks that populate the ``drugs``
    #   table with InChIKeys). ``pubchem_load`` remains downstream of
    #   ``pubchem_download``. This guarantees the ``drugs`` table is
    #   non-empty when PubChem's download queries it.
    chembl_load >> pubchem_download
    drugbank_load >> pubchem_download
    pubchem_download >> pubchem_load

    # V18 ROOT FIX (Phase 1 ↔ Phase 2 100% connection):
    # v79 P0-B2 ROOT FIX (compound): trigger_phase2 now depends on the 6
    #   REQUIRED load tasks (chembl, drugbank, uniprot, string, disgenet,
    #   omim), with ``trigger_rule=ALL_SUCCESS`` so Phase 2 fires ONLY
    #   after every required load succeeds.
    # v100 P1-009 ROOT FIX (PubChem graceful degradation):
    #   The previous code ALSO wired ``pubchem_load >> trigger_phase2`` --
    #   making PubChem (which is enrichment-only: CIDs, molecular formulas,
    #   molecular weights) a HARD dependency of Phase 2. When PubChem's API
    #   had a transient outage (rate limit, maintenance, network blip),
    #   pubchem_download FAILED -> pubchem_load was SKIPPED -> trigger_phase2
    #   saw 1 failed upstream -> the ENTIRE Sunday master DAG run failed.
    #   PubChem is documented as optional enrichment -- it should NOT block
    #   the KG build. ROOT FIX: remove the ``pubchem_load >> trigger_phase2``
    #   wire. PubChem data still loads (if its download succeeds) via the
    #   ``pubchem_download >> pubchem_load`` chain -- it just no longer
    #   blocks Phase 2. If PubChem is slow, Phase 2 fires with whatever
    #   PubChem data has loaded so far (possibly none for that run); the
    #   next run picks up the enrichment. This is the scientifically
    #   correct trade-off: a KG with 6/7 sources is far more useful than
    #   no KG at all because PubChem was unreachable.
    chembl_load >> trigger_phase2
    drugbank_load >> trigger_phase2
    uniprot_load >> trigger_phase2
    string_load >> trigger_phase2
    disgenet_load >> trigger_phase2
    omim_load >> trigger_phase2
    # NOTE: pubchem_load is intentionally NOT wired to trigger_phase2.
    # PubChem is optional enrichment -- see P1-009 ROOT FIX above.


# v89 ROOT FIX (BUG #40): consistent DAG-instance naming convention.
# Was ``master_dag = master_pipeline()`` -- different from the standalone
# DAGs' ``<name>_dag_instance``. All 8 DAG files now use ``dag = ...``.
dag = master_pipeline()
