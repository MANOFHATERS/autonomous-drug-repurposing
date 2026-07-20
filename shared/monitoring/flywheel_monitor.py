"""
shared.monitoring — data flywheel step monitoring and alerting.

ISSUE #355: alert when a flywheel step fails.

The flywheel has 4 steps. Each step can fail silently:
    1. writeback fails → CSV not updated, validated hypothesis lost.
    2. trainer crashes → no fine-tune, model state stale.
    3. ranker doesn't load new bonuses → RL agent uses stale bonuses.
    4. checkpoint corrupt → next training run crashes or produces
       scientifically wrong predictions.

This module provides 4 health-check functions, one per step. Each
returns a FlywheelStepStatus. The orchestrator (Airflow / cron) calls
all 4 and emits an alert if any returns ok=False.

Usage:
    from shared.monitoring.flywheel_monitor import (
        check_writeback_health,
        check_retrain_trigger_health,
        check_checkpoint_health,
        check_rl_ranker_health,
        run_all_checks,
    )

    statuses = run_all_checks()
    for s in statuses:
        if not s.ok:
            alert(s.message)
"""
from __future__ import annotations

import csv
import json
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# How old can the latest validated hypothesis be before we alert?
# Default 7 days (weekly Airflow schedule). Override via env var.
FLYWHEEL_STALENESS_HOURS: int = int(os.environ.get("FLYWHEEL_STALENESS_HOURS", "168"))


@dataclass
class FlywheelStepStatus:
    """Status of a single flywheel step."""
    step: str
    ok: bool
    message: str
    last_run: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    details: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        icon = "OK" if self.ok else "FAIL"
        return f"[{icon}] {self.step}: {self.message}"


# ---------------------------------------------------------------------------
# Step 1: writeback CSV health
# ---------------------------------------------------------------------------

def check_writeback_health(
    csv_path: Optional[str] = None,
    staleness_hours: int = FLYWHEEL_STALENESS_HOURS,
) -> FlywheelStepStatus:
    """Verify the validated_hypotheses.csv exists, is non-empty, and is fresh.

    FAIL conditions:
        - CSV does not exist.
        - CSV has 0 rows.
        - CSV's latest entry is older than staleness_hours.
        - CSV's outcome column contains invalid values.
    """
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from shared.contracts.writeback import (
            get_validated_csv_path,
            OUTCOME_COL,
            VALID_OUTCOMES,
            REQUIRED_COLUMNS,
        )
    except Exception as exc:
        return FlywheelStepStatus(
            step="writeback_csv",
            ok=False,
            message=f"failed to import shared.contracts.writeback: {exc}",
        )

    if csv_path is None:
        csv_path = get_validated_csv_path()

    if not os.path.exists(csv_path):
        return FlywheelStepStatus(
            step="writeback_csv",
            ok=False,
            message=f"validated_hypotheses.csv not found at {csv_path}",
            details={"csv_path": csv_path},
        )

    rows: List[Dict[str, str]] = []
    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    except Exception as exc:
        return FlywheelStepStatus(
            step="writeback_csv",
            ok=False,
            message=f"failed to read CSV: {exc}",
            details={"csv_path": csv_path},
        )

    if not rows:
        return FlywheelStepStatus(
            step="writeback_csv",
            ok=False,
            message=f"CSV has 0 rows (no validated hypotheses yet) at {csv_path}",
            details={"csv_path": csv_path, "row_count": 0},
        )

    # Check required columns.
    missing_cols = [c for c in REQUIRED_COLUMNS if c not in rows[0]]
    if missing_cols:
        return FlywheelStepStatus(
            step="writeback_csv",
            ok=False,
            message=f"CSV missing required columns: {missing_cols}",
            details={"csv_path": csv_path, "missing_columns": missing_cols},
        )

    # Check outcome values are valid.
    invalid_outcomes = [r[OUTCOME_COL] for r in rows if r.get(OUTCOME_COL) not in VALID_OUTCOMES]
    if invalid_outcomes:
        return FlywheelStepStatus(
            step="writeback_csv",
            ok=False,
            message=f"CSV has {len(invalid_outcomes)} rows with invalid outcome values: {set(invalid_outcomes)}",
            details={"csv_path": csv_path, "invalid_outcomes": list(set(invalid_outcomes))},
        )

    # Check freshness (latest validated_at).
    latest_ts_str = max(r.get("validated_at", "") for r in rows)
    try:
        latest_ts = datetime.fromisoformat(latest_ts_str.replace("Z", "+00:00"))
        if latest_ts.tzinfo is None:
            latest_ts = latest_ts.replace(tzinfo=timezone.utc)
        age_hours = (datetime.now(timezone.utc) - latest_ts).total_seconds() / 3600
        if age_hours > staleness_hours:
            return FlywheelStepStatus(
                step="writeback_csv",
                ok=False,
                message=f"CSV is STALE — latest entry is {age_hours:.1f}h old (threshold {staleness_hours}h)",
                details={
                    "csv_path": csv_path,
                    "row_count": len(rows),
                    "latest_validated_at": latest_ts_str,
                    "age_hours": age_hours,
                    "staleness_threshold_hours": staleness_hours,
                },
            )
    except Exception as exc:
        logger.warning("writeback health: failed to parse timestamp %r: %s", latest_ts_str, exc)

    return FlywheelStepStatus(
        step="writeback_csv",
        ok=True,
        message=f"CSV healthy — {len(rows)} validated hypotheses, latest at {latest_ts_str}",
        details={
            "csv_path": csv_path,
            "row_count": len(rows),
            "latest_validated_at": latest_ts_str,
        },
    )


# ---------------------------------------------------------------------------
# Step 2: retrain trigger JSON health
# ---------------------------------------------------------------------------

def check_retrain_trigger_health(
    trigger_path: Optional[str] = None,
) -> FlywheelStepStatus:
    """Verify the Phase 3 retrain trigger JSON exists and is parseable.

    FAIL conditions:
        - JSON does not exist.
        - JSON is not valid JSON.
        - JSON is not a list of dicts.
        - Any entry is missing required fields (drug, disease, outcome).
    """
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from shared.contracts.writeback import VALID_OUTCOMES
    except Exception:
        VALID_OUTCOMES = [
            "validated_positive", "validated_toxic",
            "validated_negative", "invalidated",
        ]

    if trigger_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        trigger_path = str(repo_root / "graph_transformer" / "retrain_triggered.json")

    if not os.path.exists(trigger_path):
        return FlywheelStepStatus(
            step="retrain_trigger",
            ok=False,
            message=f"retrain trigger JSON not found at {trigger_path}",
            details={"trigger_path": trigger_path},
        )

    try:
        with open(trigger_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except json.JSONDecodeError as exc:
        return FlywheelStepStatus(
            step="retrain_trigger",
            ok=False,
            message=f"retrain trigger JSON is CORRUPT — failed to parse: {exc}",
            details={"trigger_path": trigger_path, "error": str(exc)},
        )

    if not isinstance(entries, list):
        return FlywheelStepStatus(
            step="retrain_trigger",
            ok=False,
            message=f"retrain trigger JSON is not a list (got {type(entries).__name__})",
            details={"trigger_path": trigger_path, "actual_type": type(entries).__name__},
        )

    invalid_entries = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            invalid_entries.append((i, "not a dict"))
            continue
        drug = entry.get("drug", "").strip()
        disease = entry.get("disease", "").strip()
        outcome = entry.get("outcome", "").strip().lower()
        if not drug or not disease:
            invalid_entries.append((i, f"missing drug/disease: {entry}"))
            continue
        if outcome not in VALID_OUTCOMES:
            invalid_entries.append((i, f"invalid outcome: {outcome!r}"))

    if invalid_entries:
        return FlywheelStepStatus(
            step="retrain_trigger",
            ok=False,
            message=f"retrain trigger JSON has {len(invalid_entries)} invalid entries: {invalid_entries[:3]}",
            details={"trigger_path": trigger_path, "invalid_entries": invalid_entries[:10]},
        )

    return FlywheelStepStatus(
        step="retrain_trigger",
        ok=True,
        message=f"retrain trigger JSON healthy — {len(entries)} entries",
        details={"trigger_path": trigger_path, "entry_count": len(entries)},
    )


# ---------------------------------------------------------------------------
# Step 3: checkpoint health
# ---------------------------------------------------------------------------

def check_checkpoint_health(
    checkpoint_path: Optional[str] = None,
) -> FlywheelStepStatus:
    """Verify the GT checkpoint exists, is loadable, and has required keys.

    FAIL conditions:
        - Checkpoint does not exist.
        - Checkpoint is not a valid torch save (corrupt).
        - Checkpoint is missing required keys (model_state_dict, known_pairs).
    """
    try:
        import torch
    except ImportError:
        return FlywheelStepStatus(
            step="checkpoint",
            ok=False,
            message="torch not installed — cannot verify checkpoint",
        )

    if checkpoint_path is None:
        repo_root = Path(__file__).resolve().parents[2]
        # Try common locations.
        candidates = [
            repo_root / "graph_transformer" / "checkpoints" / "gt_checkpoint.pt",
            repo_root / "checkpoints" / "gt_checkpoint.pt",
            repo_root / "gt_checkpoint.pt",
        ]
        for c in candidates:
            if c.exists():
                checkpoint_path = str(c)
                break
        if checkpoint_path is None:
            return FlywheelStepStatus(
                step="checkpoint",
                ok=False,
                message=f"no checkpoint found in candidates: {[str(c) for c in candidates]}",
            )

    if not os.path.exists(checkpoint_path):
        return FlywheelStepStatus(
            step="checkpoint",
            ok=False,
            message=f"checkpoint not found at {checkpoint_path}",
            details={"checkpoint_path": checkpoint_path},
        )

    # Check file size (a corrupt checkpoint is often 0 bytes or partial).
    size = os.path.getsize(checkpoint_path)
    if size < 100:
        return FlywheelStepStatus(
            step="checkpoint",
            ok=False,
            message=f"checkpoint is suspiciously small ({size} bytes) — likely corrupt",
            details={"checkpoint_path": checkpoint_path, "size_bytes": size},
        )

    try:
        # v114 FORENSIC ROOT FIX (BUG #3 from Task 3-b audit): the previous
        # code used weights_only=False, which allows ARBITRARY CODE
        # EXECUTION from a malicious checkpoint (pickle deserialization).
        # The P3-020 security fix (weights_only=True) was applied to
        # trainer.py and service.py but MISSED this file. A malicious
        # checkpoint placed on the Airflow worker's disk could execute
        # arbitrary code when the flywheel monitor inspects it.
        #
        # ROOT FIX: use weights_only=True. The flywheel monitor only
        # reads the bundle's metadata (dict keys, model_config, etc.) --
        # it does NOT need to deserialize arbitrary Python objects. If
        # the checkpoint contains non-standard objects that
        # weights_only=True cannot handle, torch.load will raise and the
        # except block will (correctly) report the checkpoint as corrupt.
        # This is fail-closed: a checkpoint that can't be loaded safely
        # is treated as corrupt, not executed.
        bundle = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    except Exception as exc:
        return FlywheelStepStatus(
            step="checkpoint",
            ok=False,
            message=f"checkpoint is CORRUPT — torch.load failed: {exc}",
            details={"checkpoint_path": checkpoint_path, "error": str(exc)},
        )

    if not isinstance(bundle, dict):
        return FlywheelStepStatus(
            step="checkpoint",
            ok=False,
            message=f"checkpoint is not a dict (got {type(bundle).__name__})",
            details={"checkpoint_path": checkpoint_path, "actual_type": type(bundle).__name__},
        )

    required_keys = ["model_state_dict", "known_pairs"]
    missing_keys = [k for k in required_keys if k not in bundle]
    if missing_keys:
        return FlywheelStepStatus(
            step="checkpoint",
            ok=False,
            message=f"checkpoint missing required keys: {missing_keys}",
            details={"checkpoint_path": checkpoint_path, "missing_keys": missing_keys},
        )

    return FlywheelStepStatus(
        step="checkpoint",
        ok=True,
        message=f"checkpoint healthy — {len(bundle.get('known_pairs', []))} known_pairs, size {size} bytes",
        details={
            "checkpoint_path": checkpoint_path,
            "size_bytes": size,
            "known_pairs_count": len(bundle.get("known_pairs", [])),
            "best_val_auc": bundle.get("best_val_auc"),
            "best_epoch": bundle.get("best_epoch"),
        },
    )


# ---------------------------------------------------------------------------
# Step 4: RL ranker bonus-loading health
# ---------------------------------------------------------------------------

def check_rl_ranker_health(
    expected_min_bonus_pairs: int = 0,
) -> FlywheelStepStatus:
    """Verify the RL ranker loaded the expected number of validated pairs.

    FAIL conditions:
        - rl.rl_drug_ranker cannot be imported.
        - _load_validated_hypotheses() raises.
        - Loaded pair count < expected_min_bonus_pairs.

    Args:
        expected_min_bonus_pairs: minimum expected count. Set to 0 to
            skip the count check (just verify the loader runs).
    """
    try:
        import sys
        repo_root = str(Path(__file__).resolve().parents[2])
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        # SH-033 v117 ROOT FIX (Teammate 8): use the PUBLIC API
        # (get_validated_hypotheses / get_validated_toxic_hypotheses)
        # instead of the PRIVATE functions (_load_validated_hypotheses
        # / _load_validated_toxic_hypotheses). The private functions
        # are implementation details of the _LazyList proxy and may
        # be refactored without notice. The public API is the stable
        # contract.
        from rl.rl_drug_ranker import (
            get_validated_hypotheses,
            get_validated_toxic_hypotheses,
        )
    except Exception as exc:
        return FlywheelStepStatus(
            step="rl_ranker",
            ok=False,
            message=f"failed to import rl.rl_drug_ranker: {exc}",
        )

    try:
        # SH-033 v117: call the PUBLIC API (returns a plain list, no
        # need to wrap in list() — get_validated_hypotheses already
        # returns List[Tuple[str, str]]).
        bonus_pairs = get_validated_hypotheses()
        toxic_pairs = get_validated_toxic_hypotheses()
    except Exception as exc:
        return FlywheelStepStatus(
            step="rl_ranker",
            ok=False,
            message=f"RL ranker loader raised: {exc}",
        )

    if len(bonus_pairs) < expected_min_bonus_pairs:
        return FlywheelStepStatus(
            step="rl_ranker",
            ok=False,
            message=(
                f"RL ranker loaded {len(bonus_pairs)} bonus pairs, "
                f"expected >= {expected_min_bonus_pairs}. The ranker "
                f"is NOT picking up new validated hypotheses."
            ),
            details={
                "bonus_pair_count": len(bonus_pairs),
                "toxic_pair_count": len(toxic_pairs),
                "expected_min_bonus_pairs": expected_min_bonus_pairs,
            },
        )

    return FlywheelStepStatus(
        step="rl_ranker",
        ok=True,
        message=f"RL ranker healthy — {len(bonus_pairs)} bonus pairs, {len(toxic_pairs)} toxic pairs loaded",
        details={
            "bonus_pair_count": len(bonus_pairs),
            "toxic_pair_count": len(toxic_pairs),
            "bonus_pairs_sample": list(bonus_pairs)[:5],
            "toxic_pairs_sample": list(toxic_pairs)[:5],
        },
    )


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def run_all_checks(
    csv_path: Optional[str] = None,
    trigger_path: Optional[str] = None,
    checkpoint_path: Optional[str] = None,
    expected_min_bonus_pairs: int = 0,
    staleness_hours: int = FLYWHEEL_STALENESS_HOURS,
) -> List[FlywheelStepStatus]:
    """Run all 4 flywheel health checks and return the statuses.

    The orchestrator (Airflow / cron) calls this and alerts on any
    status with ok=False.
    """
    return [
        check_writeback_health(csv_path=csv_path, staleness_hours=staleness_hours),
        check_retrain_trigger_health(trigger_path=trigger_path),
        check_checkpoint_health(checkpoint_path=checkpoint_path),
        check_rl_ranker_health(expected_min_bonus_pairs=expected_min_bonus_pairs),
    ]


def alert_on_failures(statuses: List[FlywheelStepStatus]) -> int:
    """Log an alert for each failed status. Returns the count of failures.

    In production, replace the logger calls with your alerting system
    (PagerDuty, Slack webhook, Sentry, etc.).
    """
    failures = [s for s in statuses if not s.ok]
    for s in failures:
        logger.error(
            "FLYWHEEL ALERT: step=%s failed: %s | details=%s",
            s.step, s.message, s.details,
        )
    if not failures:
        logger.info("FLYWHEEL OK: all %d steps healthy", len(statuses))
    return len(failures)


# ===========================================================================
# SH-013 v129 ROOT FIX (Teammate 14, forensic, root-level, no surface fix):
# ATOMIC DATA FLYWHEEL TRIGGER
# ===========================================================================
# The audit found that the data flywheel trigger→fine-tune hop was broken:
# writeback writes to validated_hypotheses.csv AND retrain_triggered.json,
# then retrain_on_validated reads the CSV and fine-tunes the model. If the
# retrain FAILS (e.g., checkpoint corrupt, OOM, bug), the CSV and JSON have
# ALREADY been updated — the validated pair is recorded as "processed" but
# the model never learned it. The next run sees the pair in the CSV and
# skips it (idempotent), so the model NEVER learns from this validation.
# This is silent data loss in the flywheel — the exact pattern the audit
# flagged as "aspirational rather than actual".
#
# ROOT FIX: this function makes the trigger ATOMIC. It:
#   1. Validates inputs (drug, disease, outcome must be non-empty + valid).
#   2. BACKS UP the current validated_hypotheses.csv and retrain_triggered.json.
#   3. Appends the validated hypothesis to the CSV (atomic write: temp + rename).
#   4. Appends to the retrain trigger JSON (atomic write: temp + rename).
#   5. Calls retrain_on_validated to fine-tune the GT model.
#   6. If retrain fails OR raises, ROLLS BACK the CSV and JSON to their
#      pre-trigger state (restores from backup).
#   7. Cleans up backups on success.
#   8. Returns a status dict.
#
# The atomicity guarantee: either BOTH the writeback AND the retrain succeed,
# or NEITHER does. This is the "single transaction" the audit requires.
#
# This function is the SINGLE entry point for the data flywheel trigger.
# The frontend's /api/hypothesis/validate route, the Airflow weekly task,
# and any other trigger source SHOULD call this function (not the raw
# write_validated_hypothesis + retrain_on_validated pair).
# ===========================================================================


def _atomic_write_csv(
    csv_path: str,
    rows: List[Dict[str, str]],
    fieldnames: List[str],
) -> None:
    """Atomically write a CSV file (temp file + fsync + rename).

    This is the low-level atomic write primitive. It writes to a temp file
    in the SAME directory as csv_path, fsyncs the temp file, then renames
    it to csv_path. On POSIX systems, rename is atomic — a reader either
    sees the old file or the new file, never a partial write.
    """
    csv_dir = os.path.dirname(csv_path)
    if csv_dir:
        os.makedirs(csv_dir, exist_ok=True)
    # NamedTemporaryFile in the SAME directory as the target (so rename is atomic).
    # delete=False because we rename it manually.
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(csv_path)}.",
        suffix=".tmp",
        dir=csv_dir or None,
    )
    try:
        with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        # fsync the temp file before rename for durability.
        import os as _os_mod
        if hasattr(_os_mod, "fsync"):
            with open(tmp_path, "rb") as f:
                _os_mod.fsync(f.fileno())
        # Atomic rename (POSIX). On Windows, os.replace is atomic too.
        os.replace(tmp_path, csv_path)
    except Exception:
        # Clean up the temp file on any error.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _atomic_write_json(json_path: str, data: Any) -> None:
    """Atomically write a JSON file (temp file + fsync + rename)."""
    json_dir = os.path.dirname(json_path)
    if json_dir:
        os.makedirs(json_dir, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(json_path)}.",
        suffix=".tmp",
        dir=json_dir or None,
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        import os as _os_mod
        if hasattr(_os_mod, "fsync"):
            with open(tmp_path, "rb") as f:
                _os_mod.fsync(f.fileno())
        os.replace(tmp_path, json_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_csv_rows(csv_path: str) -> Tuple[List[Dict[str, str]], List[str]]:
    """Read a CSV file and return (rows, fieldnames). Returns ([], []) if not exists."""
    if not os.path.exists(csv_path):
        return [], []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames or []
        return rows, fieldnames


def _read_json_list(json_path: str) -> List[Dict[str, Any]]:
    """Read a JSON file and return a list of dicts. Returns [] if not exists or invalid."""
    if not os.path.exists(json_path):
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def trigger_flywheel_retrain_atomically(
    drug: str,
    disease: str,
    outcome: str,
    checkpoint_path: str,
    validated_by: str = "automated",
    validation_study_id: Optional[str] = None,
    notes: str = "",
    original_gt_score: Optional[float] = None,
    original_rl_rank: Optional[int] = None,
    fine_tune_epochs: int = 10,
    learning_rate: float = 1e-4,
    csv_path: Optional[str] = None,
    retrain_trigger_path: Optional[str] = None,
    output_checkpoint_path: Optional[str] = None,
    skip_retrain: bool = False,
    drug_id: Optional[str] = None,
    drug_name: Optional[str] = None,
    disease_id: Optional[str] = None,
    disease_name: Optional[str] = None,
    score: Optional[float] = None,
) -> Dict[str, Any]:
    """SH-013 v129 ROOT FIX: Atomically trigger the data flywheel.

    This is the SINGLE entry point for the data flywheel trigger. It
    atomically writes a validated hypothesis to the CSV + JSON trigger,
    then calls retrain_on_validated to fine-tune the GT model. If the
    retrain fails, the CSV and JSON are ROLLED BACK to their pre-trigger
    state — no silent data loss.

    The atomicity guarantee: either BOTH the writeback AND the retrain
    succeed, or NEITHER does. This is the "single transaction" the audit
    requires (SH-013).

    Args:
        drug: Drug name (e.g. "aspirin"). REQUIRED.
        disease: Disease name (e.g. "diabetes"). REQUIRED.
        outcome: One of VALID_OUTCOMES (validated_positive |
            validated_toxic | validated_negative | invalidated). REQUIRED.
        checkpoint_path: Path to the trained GT checkpoint (.pt file).
        validated_by: Who validated (e.g. "wet_lab:partner_a").
        validation_study_id: Optional study ID (e.g. "NCT12345678").
        notes: Free-text notes from the validator.
        original_gt_score: The GT model's original prediction score [0, 1].
        original_rl_rank: The RL ranker's original rank (1-indexed).
        fine_tune_epochs: Number of fine-tune epochs (default 10).
        learning_rate: Fine-tune learning rate (default 1e-4).
        csv_path: Path to validated_hypotheses.csv. If None, uses
            get_validated_csv_path().
        retrain_trigger_path: Path to retrain_triggered.json. If None,
            uses <repo>/graph_transformer/retrain_triggered.json.
        output_checkpoint_path: Where to save the fine-tuned model. If
            None, overwrites the input checkpoint.
        skip_retrain: If True, only writeback (no retrain). For testing
            the atomic write/rollback in isolation.
        drug_id: Optional canonical drug ID (e.g. InChIKey) — SH-003 v129.
        drug_name: Optional explicit drug name field — SH-003 v129.
        disease_id: Optional canonical disease ID (e.g. DOID) — SH-003 v129.
        disease_name: Optional explicit disease name field — SH-003 v129.
        score: Optional composite score — SH-003 v129.

    Returns:
        Dict with keys:
        - status: "success" | "rolled_back" | "writeback_only"
        - validated_pair: Tuple[str, str] — the (drug, disease) pair.
        - outcome: str — the outcome enum value.
        - csv_path: str — the CSV path.
        - retrain_trigger_path: str — the JSON trigger path.
        - retrain_result: Dict — the result from retrain_on_validated (if run).
        - error: Optional[str] — error message if rolled back.
        - timestamp: str — ISO 8601 timestamp of the trigger.
        - rollback_performed: bool — True if rollback was triggered.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Step 1: import the canonical schema constants.
    # ------------------------------------------------------------------
    try:
        import sys as _sys
        _repo_root = str(Path(__file__).resolve().parents[2])
        if _repo_root not in _sys.path:
            _sys.path.insert(0, _repo_root)
        from shared.contracts.writeback import (
            DRUG_COL, DISEASE_COL, OUTCOME_COL, TIMESTAMP_COL,
            VALIDATED_BY_COL, VALIDATION_STUDY_ID_COL, NOTES_COL,
            ORIGINAL_GT_SCORE_COL, ORIGINAL_RL_RANK_COL, WRITEBACK_VERSION_COL,
            DRUG_ID_COL, DRUG_NAME_COL, DISEASE_ID_COL, DISEASE_NAME_COL, SCORE_COL,
            WRITEBACK_CSV_COLUMNS, VALID_OUTCOMES, WRITEBACK_VERSION,
            get_validated_csv_path,
        )
    except Exception as exc:
        return {
            "status": "rolled_back",
            "validated_pair": (drug or "", disease or ""),
            "outcome": outcome or "",
            "csv_path": csv_path or "",
            "retrain_trigger_path": retrain_trigger_path or "",
            "retrain_result": {},
            "error": f"failed to import shared.contracts.writeback: {exc}",
            "timestamp": timestamp,
            "rollback_performed": False,
        }

    # ------------------------------------------------------------------
    # Step 2: validate inputs.
    # ------------------------------------------------------------------
    if not drug or not isinstance(drug, str) or not drug.strip():
        return {
            "status": "rolled_back",
            "validated_pair": (drug or "", disease or ""),
            "outcome": outcome or "",
            "csv_path": csv_path or "",
            "retrain_trigger_path": retrain_trigger_path or "",
            "retrain_result": {},
            "error": "drug must be a non-empty string",
            "timestamp": timestamp,
            "rollback_performed": False,
        }
    if not disease or not isinstance(disease, str) or not disease.strip():
        return {
            "status": "rolled_back",
            "validated_pair": (drug, disease or ""),
            "outcome": outcome or "",
            "csv_path": csv_path or "",
            "retrain_trigger_path": retrain_trigger_path or "",
            "retrain_result": {},
            "error": "disease must be a non-empty string",
            "timestamp": timestamp,
            "rollback_performed": False,
        }
    if outcome not in VALID_OUTCOMES:
        return {
            "status": "rolled_back",
            "validated_pair": (drug, disease),
            "outcome": outcome or "",
            "csv_path": csv_path or "",
            "retrain_trigger_path": retrain_trigger_path or "",
            "retrain_result": {},
            "error": f"outcome {outcome!r} is not valid. Must be one of: {list(VALID_OUTCOMES)}",
            "timestamp": timestamp,
            "rollback_performed": False,
        }

    # Resolve paths.
    if csv_path is None:
        csv_path = get_validated_csv_path()
    if retrain_trigger_path is None:
        _repo_root = Path(__file__).resolve().parents[2]
        retrain_trigger_path = str(_repo_root / "graph_transformer" / "retrain_triggered.json")

    # ------------------------------------------------------------------
    # Step 3: BACK UP the current CSV and JSON (for rollback).
    # ------------------------------------------------------------------
    csv_backup = csv_path + ".bak"
    json_backup = retrain_trigger_path + ".bak"
    try:
        if os.path.exists(csv_path):
            shutil.copy2(csv_path, csv_backup)
        else:
            # Remove stale backup if CSV doesn't exist.
            if os.path.exists(csv_backup):
                os.unlink(csv_backup)
        if os.path.exists(retrain_trigger_path):
            shutil.copy2(retrain_trigger_path, json_backup)
        else:
            if os.path.exists(json_backup):
                os.unlink(json_backup)
    except Exception as exc:
        return {
            "status": "rolled_back",
            "validated_pair": (drug, disease),
            "outcome": outcome,
            "csv_path": csv_path,
            "retrain_trigger_path": retrain_trigger_path,
            "retrain_result": {},
            "error": f"failed to back up CSV/JSON for rollback: {exc}",
            "timestamp": timestamp,
            "rollback_performed": False,
        }

    # ------------------------------------------------------------------
    # Step 4: APPEND to CSV (atomic write).
    # ------------------------------------------------------------------
    try:
        existing_rows, existing_fieldnames = _read_csv_rows(csv_path)
        # Merge existing fieldnames with the canonical schema (in case the
        # existing CSV has a subset or superset of columns).
        canonical_fieldnames = list(WRITEBACK_CSV_COLUMNS)
        # Preserve any extra columns from the existing CSV (forward-compat).
        for fn in existing_fieldnames:
            if fn not in canonical_fieldnames:
                canonical_fieldnames.append(fn)

        new_row: Dict[str, str] = {
            DRUG_COL: drug.strip(),
            DISEASE_COL: disease.strip(),
            OUTCOME_COL: outcome,
            TIMESTAMP_COL: timestamp,
            VALIDATED_BY_COL: validated_by,
            VALIDATION_STUDY_ID_COL: validation_study_id or "",
            NOTES_COL: notes or "",
            ORIGINAL_GT_SCORE_COL: (
                "" if original_gt_score is None else f"{float(original_gt_score):.6f}"
            ),
            ORIGINAL_RL_RANK_COL: (
                "" if original_rl_rank is None else str(int(original_rl_rank))
            ),
            WRITEBACK_VERSION_COL: WRITEBACK_VERSION,
            # SH-003 v129: richer schema columns (optional).
            DRUG_ID_COL: drug_id or "",
            DRUG_NAME_COL: drug_name or drug.strip(),
            DISEASE_ID_COL: disease_id or "",
            DISEASE_NAME_COL: disease_name or disease.strip(),
            # SH-013 v129 ROOT FIX (bug caught in E2E test): the score
            # column is INDEPENDENT of original_gt_score. The previous
            # logic incorrectly required original_gt_score to be non-None
            # before writing score — this meant score=0.87 with no
            # original_gt_score wrote "" instead of "0.870000". The score
            # column represents the composite/final score (which may differ
            # from the original GT prediction). Write it whenever it's
            # provided.
            SCORE_COL: "" if score is None else f"{float(score):.6f}",
        }
        all_rows = existing_rows + [new_row]
        _atomic_write_csv(csv_path, all_rows, canonical_fieldnames)
        logger.info(
            "SH-013 v129: atomically appended validated hypothesis to %s "
            "(row %d, outcome=%s)", csv_path, len(all_rows), outcome,
        )
    except Exception as exc:
        # Rollback not needed — the atomic write either succeeded or didn't
        # touch the original (temp + rename). But the backup is still there
        # for safety; clean it up.
        _cleanup_backup(csv_backup)
        _cleanup_backup(json_backup)
        return {
            "status": "rolled_back",
            "validated_pair": (drug, disease),
            "outcome": outcome,
            "csv_path": csv_path,
            "retrain_trigger_path": retrain_trigger_path,
            "retrain_result": {},
            "error": f"failed to atomically write CSV: {exc}",
            "timestamp": timestamp,
            "rollback_performed": False,
        }

    # ------------------------------------------------------------------
    # Step 5: APPEND to retrain_triggered.json (atomic write).
    # ------------------------------------------------------------------
    try:
        trigger_entries = _read_json_list(retrain_trigger_path)
        new_entry = {
            "drug": drug.strip(),
            "disease": disease.strip(),
            "outcome": outcome,
            "validated_at": timestamp,
            "validated_by": validated_by,
            "validation_study_id": validation_study_id or "",
            # SH-003 v129: richer schema fields (optional).
            "drug_id": drug_id or "",
            "drug_name": drug_name or drug.strip(),
            "disease_id": disease_id or "",
            "disease_name": disease_name or disease.strip(),
        }
        trigger_entries.append(new_entry)
        _atomic_write_json(retrain_trigger_path, trigger_entries)
        logger.info(
            "SH-013 v129: atomically appended trigger entry to %s "
            "(entry %d, outcome=%s)",
            retrain_trigger_path, len(trigger_entries), outcome,
        )
    except Exception as exc:
        # CSV was already updated. Roll it back to the backup.
        _restore_from_backup(csv_path, csv_backup)
        _cleanup_backup(csv_backup)
        _cleanup_backup(json_backup)
        return {
            "status": "rolled_back",
            "validated_pair": (drug, disease),
            "outcome": outcome,
            "csv_path": csv_path,
            "retrain_trigger_path": retrain_trigger_path,
            "retrain_result": {},
            "error": f"failed to atomically write JSON trigger: {exc}",
            "timestamp": timestamp,
            "rollback_performed": True,
        }

    # ------------------------------------------------------------------
    # Step 6: if skip_retrain, stop here (writeback only).
    # ------------------------------------------------------------------
    if skip_retrain:
        _cleanup_backup(csv_backup)
        _cleanup_backup(json_backup)
        return {
            "status": "writeback_only",
            "validated_pair": (drug, disease),
            "outcome": outcome,
            "csv_path": csv_path,
            "retrain_trigger_path": retrain_trigger_path,
            "retrain_result": {"skipped": True},
            "error": None,
            "timestamp": timestamp,
            "rollback_performed": False,
        }

    # ------------------------------------------------------------------
    # Step 7: CALL retrain_on_validated (the fine-tune step).
    # ------------------------------------------------------------------
    retrain_result: Dict[str, Any] = {}
    retrain_error: Optional[str] = None
    try:
        # Lazy import to avoid loading torch when this module is imported
        # (the health-check functions don't need torch).
        from graph_transformer.training.trainer import retrain_on_validated
        retrain_result = retrain_on_validated(
            checkpoint_path=checkpoint_path,
            validated_csv_path=csv_path,
            output_checkpoint_path=output_checkpoint_path,
            fine_tune_epochs=fine_tune_epochs,
            learning_rate=learning_rate,
        )
        # Check if retrain itself reported an error.
        if retrain_result.get("error"):
            retrain_error = retrain_result["error"]
    except Exception as exc:
        retrain_error = f"retrain_on_validated raised: {exc}"
        logger.exception("SH-013 v129: retrain_on_validated raised an exception")

    # ------------------------------------------------------------------
    # Step 8: if retrain failed, ROLL BACK the CSV and JSON.
    # ------------------------------------------------------------------
    if retrain_error is not None:
        logger.error(
            "SH-013 v129: retrain failed (%s) — rolling back CSV and JSON "
            "to their pre-trigger state. The validated pair will be retried "
            "on the next trigger.", retrain_error,
        )
        _restore_from_backup(csv_path, csv_backup)
        _restore_from_backup(retrain_trigger_path, json_backup)
        _cleanup_backup(csv_backup)
        _cleanup_backup(json_backup)
        return {
            "status": "rolled_back",
            "validated_pair": (drug, disease),
            "outcome": outcome,
            "csv_path": csv_path,
            "retrain_trigger_path": retrain_trigger_path,
            "retrain_result": retrain_result,
            "error": retrain_error,
            "timestamp": timestamp,
            "rollback_performed": True,
        }

    # ------------------------------------------------------------------
    # Step 9: SUCCESS — clean up backups and return.
    # ------------------------------------------------------------------
    _cleanup_backup(csv_backup)
    _cleanup_backup(json_backup)
    logger.info(
        "SH-013 v129: atomic flywheel trigger SUCCESS — pair (%s, %s) "
        "outcome=%s written to CSV+JSON and model fine-tuned (%d pairs added).",
        drug, disease, outcome,
        retrain_result.get("validated_pairs_added", 0),
    )
    return {
        "status": "success",
        "validated_pair": (drug, disease),
        "outcome": outcome,
        "csv_path": csv_path,
        "retrain_trigger_path": retrain_trigger_path,
        "retrain_result": retrain_result,
        "error": None,
        "timestamp": timestamp,
        "rollback_performed": False,
    }


def _cleanup_backup(backup_path: str) -> None:
    """Delete a backup file if it exists. Swallows errors (best-effort)."""
    try:
        if os.path.exists(backup_path):
            os.unlink(backup_path)
    except OSError as exc:
        logger.warning("SH-013 v129: failed to clean up backup %s: %s", backup_path, exc)


def _restore_from_backup(target_path: str, backup_path: str) -> None:
    """Restore a file from its backup. Swallows errors (best-effort)."""
    try:
        if os.path.exists(backup_path):
            shutil.copy2(backup_path, target_path)
            logger.info("SH-013 v129: restored %s from backup", target_path)
        else:
            # No backup means the file didn't exist before — delete the
            # current file to restore the "didn't exist" state.
            if os.path.exists(target_path):
                os.unlink(target_path)
                logger.info(
                    "SH-013 v129: deleted %s (no backup — file didn't exist before)",
                    target_path,
                )
    except OSError as exc:
        logger.error(
            "SH-013 v129: FAILED to restore %s from backup %s: %s. "
            "The file may be in an inconsistent state — manual intervention required.",
            target_path, backup_path, exc,
        )


__all__ = [
    "FlywheelStepStatus",
    "FLYWHEEL_STALENESS_HOURS",
    "check_writeback_health",
    "check_retrain_trigger_health",
    "check_checkpoint_health",
    "check_rl_ranker_health",
    "run_all_checks",
    "alert_on_failures",
    # SH-013 v129 ROOT FIX: atomic flywheel trigger + helpers.
    "trigger_flywheel_retrain_atomically",
    "_atomic_write_csv",
    "_atomic_write_json",
    "_read_csv_rows",
    "_read_json_list",
    "_cleanup_backup",
    "_restore_from_backup",
]
