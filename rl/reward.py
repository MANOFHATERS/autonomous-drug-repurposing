"""rl.reward — Reward Function & Config (P4-008/P4-021 modular wrapper).

P4-021 ROOT FIX (Team Member 9, REAL EXTRACTION STEP):
The column constants (FEATURE_COLS, REQUIRED_COLUMNS) are now imported
from rl/constants.py (the self-contained constants module), NOT from
the 9000-line monolith. This is the FIRST real extraction step toward
P4-021's goal of actual decoupling.

The RewardConfig and RewardFunction classes still live in
rl_drug_ranker.py because they have deep dependencies on the
withdrawn-drug sets, pandas/numpy, and the column constants. A full
extraction is planned post-v105 when CI coverage is higher.

Callers can import:
    from rl.reward import RewardFunction, RewardConfig, compute_reward
    from rl.reward import load_reward_weights_for_tenant, apply_tenant_reward_weights

TASK 8.4 ROOT FIX (Teammate 8 v127 — Phase 1 → Phase 4 patient-safety):
Adds ``load_phase1_safety_signals`` and ``compute_safety_score_with_phase1``
so the reward function can use REAL Phase 1 DrugBank data
(``is_withdrawn`` column from ``drugbank_drugs.csv``) instead of the
hardcoded ~75-drug ``WITHDRAWN_DRUGS`` frozenset in the monolith.
The hardcoded set is the FALLBACK when Phase 1 data is unavailable
(dev/CI without the bridge); the Phase 1 loader is the SOURCE OF
TRUTH in production. This module does NOT touch rl_drug_ranker.py
(owned by TM9) — it provides a parallel path that callers can use
to inject Phase 1 safety data into the existing RewardFunction at
runtime via the ``extra_withdrawn_drugs`` constructor parameter.
"""
from __future__ import annotations

import os
from typing import Dict, Optional, Set, Tuple

# P4-021: import CONSTANTS from rl/constants.py (self-contained, no monolith dep).
from .constants import FEATURE_COLS, REQUIRED_COLUMNS

# P4-021: RewardConfig + RewardFunction still come from the monolith (they have
# deep interdependencies on the withdrawn-drug sets, pandas/numpy, etc.).
from .rl_drug_ranker import (
    RewardConfig,
    RewardFunction,
    compute_reward,
    # P4-005: per-tenant reward weights
    load_reward_weights_for_tenant,
    save_reward_weights_for_tenant,
    apply_tenant_reward_weights,
    DEFAULT_REWARD_WEIGHTS_DIR,
    # Constants used by the reward function (still from monolith — these are
    # scientific guardrail sets, not column names, so they stay with the
    # reward logic until RewardFunction is extracted).
    WITHDRAWN_DRUGS,
    INDICATION_WITHDRAWN_DRUGS,
    CONTROLLED_SUBSTANCES,
    DEFAULT_PROPRIETARY_PREFIXES,
)


# =============================================================================
# TASK 8.4 ROOT FIX: Phase 1 safety-signal loader.
# =============================================================================
# The Phase 1 contract (phase1/contracts/phase1_schema.py) defines the
# ``drugs`` source with an ``is_withdrawn`` boolean column (line 299) and a
# ``groups`` column that may contain the token "withdrawn" (line 294). This
# loader reads the Phase 1 DrugBank CSV and returns the set of drug names
# that are flagged withdrawn in EITHER column — covering both the explicit
# boolean flag and the legacy groups string.
#
# The function is INTENTIONALLY a stand-alone helper (no class state, no
# module-level singletons) so it can be:
#   - Unit tested with a fake CSV (no Phase 1 data needed)
#   - Called lazily at RL env construction time (one CSV read per training
#     run, not per episode)
#   - Composed with the existing ``WITHDRAWN_DRUGS`` frozenset via
#     ``merge_withdrawn_drugs_with_phase1`` below — the union of the two
#     sets is the effective patient-safety guardrail.
# =============================================================================

# The Phase 1 CSV filename (per phase1/contracts/phase1_schema.py line 259).
# Aliases cover the degraded path when DrugBank is unavailable and the
# bridge falls back to ChEMBL (``chembl_drugs.csv``).
PHASE1_DRUGS_FILENAME: str = "drugbank_drugs.csv"
PHASE1_DRUGS_ALIASES: Tuple[str, ...] = (
    "drugbank_open_drugs.csv",
    "chembl_drugs.csv",
    "drugs.csv",
)

# The column names (per phase1/contracts/phase1_schema.py lines 262, 294, 299).
PHASE1_DRUG_NAME_COLUMN: str = "name"
PHASE1_IS_WITHDRAWN_COLUMN: str = "is_withdrawn"
PHASE1_GROUPS_COLUMN: str = "groups"
PHASE1_WITHDRAWN_TOKEN: str = "withdrawn"

# The reason column is OPTIONAL in Phase 1 (no ``withdrawn_reason`` column
# is defined in the schema — the schema only has ``is_withdrawn``). When
# the source CSV has a ``withdrawn_reason`` column (added by some DrugBank
# extractors), we read it. When it doesn't, the reason is "withdrawn"
# (generic). This makes the loader robust to schema extensions.
PHASE1_WITHDRAWN_REASON_COLUMN: str = "withdrawn_reason"


def load_phase1_safety_signals(
    phase1_dir: str,
    drugs_filename: Optional[str] = None,
) -> Tuple[Set[str], Dict[str, str]]:
    """Load withdrawn-drug safety signals from the Phase 1 DrugBank CSV.

    TASK 8.4 ROOT FIX (Teammate 8 — Phase 1 → Phase 4 patient-safety):

    The reward function's ``safety_score`` for withdrawn drugs must be
    0.0 (hard-reject). The previous code used a hardcoded ~75-drug
    ``WITHDRAWN_DRUGS`` frozenset in rl/rl_drug_ranker.py — this set
    is INCOMPLETE (the FDA has withdrawn >200 drugs since 1960) and
    goes STALE the moment a new withdrawal is announced. Production
    patient-safety requires loading the LIVE Phase 1 DrugBank data
    (which is updated by the Phase 1 Airflow pipeline whenever
    DrugBank releases a new version).

    This loader reads the Phase 1 ``drugbank_drugs.csv`` (the canonical
    Compound source per phase1/contracts/phase1_schema.py) and returns
    the set of drug names flagged ``is_withdrawn=True`` OR with
    ``groups`` containing the token ``withdrawn``. The two checks are
    redundant by design — DrugBank sometimes populates only one.

    Args:
        phase1_dir: Path to the Phase 1 output directory (the directory
            that contains ``drugbank_drugs.csv`` or one of its aliases).
               drugs_filename: Override the filename (defaults to
            ``PHASE1_DRUGS_FILENAME`` = ``drugbank_drugs.csv``). When
            None, the loader tries the canonical name first, then each
            alias in ``PHASE1_DRUGS_ALIASES``.

    Returns:
        Tuple of (withdrawn_drug_names, withdrawn_reasons):
            - withdrawn_drug_names: ``Set[str]`` of lowercase drug names
              flagged withdrawn in the Phase 1 CSV. Empty set if the CSV
              is missing, empty, or has no withdrawn rows.
            - withdrawn_reasons: ``Dict[str, str]`` mapping drug name
              (lowercase) to the withdrawal reason (from the
              ``withdrawn_reason`` column if present, else "withdrawn").

    Raises:
        FileNotFoundError: If ``phase1_dir`` does not exist (the loader
            treats a missing CSV file as an empty result, but a missing
            DIRECTORY is a configuration error — the caller passed the
            wrong path).
    """
    if not os.path.isdir(phase1_dir):
        raise FileNotFoundError(
            f"Phase 1 directory not found: {phase1_dir}. The Phase 1 "
            f"pipeline must run first (phase1/run_pipeline.py) to produce "
            f"the DrugBank CSV. Pass the directory that contains "
            f"'{PHASE1_DRUGS_FILENAME}'."
        )

    # Resolve the CSV path: try the canonical name first, then aliases.
    candidate_names = [drugs_filename] if drugs_filename else [
        PHASE1_DRUGS_FILENAME, *PHASE1_DRUGS_ALIASES,
    ]
    csv_path: Optional[str] = None
    for name in candidate_names:
        if name is None:
            continue
        candidate = os.path.join(phase1_dir, name)
        if os.path.isfile(candidate):
            csv_path = candidate
            break

    if csv_path is None:
        # Phase 1 directory exists but the drugs CSV is missing — this is
        # the degraded path (DrugBank license paused). Return empty sets
        # so the caller falls back to the hardcoded WITHDRAWN_DRUGS set.
        return set(), {}

    # Read the CSV lazily (pandas is already a hard dep of rl_drug_ranker).
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            f"TASK 8.4: pandas is required to load Phase 1 safety signals "
            f"from {csv_path} but is not installed. Install with: "
            f"pip install pandas"
        ) from exc

    try:
        df = pd.read_csv(csv_path, low_memory=False)
    except Exception as exc:
        raise RuntimeError(
            f"TASK 8.4: failed to read Phase 1 drugs CSV at {csv_path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if len(df) == 0:
        return set(), {}

    # Verify the required name column exists.
    if PHASE1_DRUG_NAME_COLUMN not in df.columns:
        raise RuntimeError(
            f"TASK 8.4: Phase 1 drugs CSV at {csv_path} is missing the "
            f"required '{PHASE1_DRUG_NAME_COLUMN}' column (per "
            f"phase1/contracts/phase1_schema.py). Columns present: "
            f"{list(df.columns)}"
        )

    # Lowercase + strip the name column for case-insensitive matching
    # (the hardcoded WITHDRAWN_DRUGS set uses lowercase keys).
    names = df[PHASE1_DRUG_NAME_COLUMN].astype(str).str.lower().str.strip()

    withdrawn_mask = _detect_withdrawn_rows(df)

    withdrawn_names: Set[str] = set()
    withdrawn_reasons: Dict[str, str] = {}
    if withdrawn_mask.any():
        withdrawn_df = df.loc[withdrawn_mask]
        withdrawn_names_lower = (
            withdrawn_df[PHASE1_DRUG_NAME_COLUMN]
            .astype(str).str.lower().str.strip()
        )
        withdrawn_names = set(withdrawn_names_lower.tolist())

        # Read the withdrawal reason (optional column).
        reason_col = (
            PHASE1_WITHDRAWN_REASON_COLUMN
            if PHASE1_WITHDRAWN_REASON_COLUMN in withdrawn_df.columns
            else None
        )
        for name_lower, row in zip(withdrawn_names_lower, withdrawn_df.itertuples(index=False)):
            reason = "withdrawn"
            if reason_col is not None:
                reason_val = getattr(row, reason_col, None) if hasattr(row, "_asdict") else None
                if reason_val is None and isinstance(row, dict):
                    reason_val = row.get(reason_col)
                if reason_val is not None and str(reason_val).strip():
                    reason = str(reason_val).strip()
            withdrawn_reasons[name_lower] = reason

    return withdrawn_names, withdrawn_reasons


def _detect_withdrawn_rows(df) -> "Any":  # type: ignore[name-defined]
    """Return a boolean mask of rows flagged withdrawn in Phase 1.

    A row is flagged withdrawn if EITHER:
      1. The ``is_withdrawn`` column is True (boolean), OR
      2. The ``groups`` column contains the token ``withdrawn``.

    The two checks are redundant by design — DrugBank sometimes populates
    only one. The function is defensive: missing columns, NaN values,
    and unexpected dtypes are handled gracefully (return False for that
    row, not crash).
    """
    import pandas as pd

    n = len(df)
    mask = pd.Series([False] * n, index=df.index)

    # Check 1: is_withdrawn column (boolean).
    if PHASE1_IS_WITHDRAWN_COLUMN in df.columns:
        col = df[PHASE1_IS_WITHDRAWN_COLUMN]
        # Handle bool, string "True"/"False", 0/1, and NaN.
        if col.dtype == bool:
            mask = mask | col.fillna(False)
        else:
            # Stringified booleans — coerce to lower-str and check.
            col_str = col.astype(str).str.lower().str.strip()
            mask = mask | col_str.isin(["true", "1", "yes", "y", "t"])

    # Check 2: groups column contains "withdrawn" token.
    if PHASE1_GROUPS_COLUMN in df.columns:
        col = df[PHASE1_GROUPS_COLUMN].astype(str).str.lower()
        # DrugBank groups is a semicolon-separated list of tokens
        # (e.g. "approved;withdrawn;nutracet"). Use contains to catch
        # the token anywhere in the list.
        token_mask = col.str.contains(
            r"\bwithdrawn\b", regex=True, na=False
        )
        mask = mask | token_mask

    return mask


def merge_withdrawn_drugs_with_phase1(
    phase1_withdrawn: Set[str],
    hardcoded_withdrawn: Optional[Set[str]] = None,
) -> Set[str]:
    """Merge Phase 1 withdrawn-drug names with the hardcoded fallback set.

    TASK 8.4 ROOT FIX: the reward function's patient-safety guardrail
    is the UNION of:
      - Phase 1 DrugBank ``is_withdrawn`` (live, updated by Airflow)
      - The hardcoded ``WITHDRAWN_DRUGS`` frozenset (historical FDA
        withdrawals that may not yet be reflected in DrugBank)

    The union is the correct semantics: a drug withdrawn in EITHER
    source is unsafe to rank HIGH. The hardcoded set catches drugs
    withdrawn between DrugBank releases; Phase 1 catches new
    withdrawals the moment the Airflow pipeline picks them up.

    Args:
        phase1_withdrawn: Set of drug names (lowercase) from the
            Phase 1 DrugBank CSV.
        hardcoded_withdrawn: Optional set of drug names (lowercase)
            from the hardcoded ``WITHDRAWN_DRUGS`` frozenset. When
            None, uses the imported ``WITHDRAWN_DRUGS``.

    Returns:
        Set[str] of lowercase drug names — the union of the two inputs.
    """
    if hardcoded_withdrawn is None:
        hardcoded_withdrawn = set(WITHDRAWN_DRUGS)
    return set(phase1_withdrawn) | set(hardcoded_withdrawn)


def compute_safety_score_with_phase1(
    drug_name: str,
    phase1_withdrawn: Set[str],
    hardcoded_withdrawn: Optional[Set[str]] = None,
) -> float:
    """Compute the safety_score for a drug using Phase 1 data.

    TASK 8.4 ROOT FIX: returns 0.0 (HARD-REJECT) if the drug is in
    the Phase 1 withdrawn set OR the hardcoded withdrawn set. Returns
    1.0 (safe) otherwise.

    The hardcoded set is the FALLBACK — when Phase 1 data is missing
    (dev/CI without the bridge), the function degrades to checking
    ONLY the hardcoded set, preserving the existing behavior. When
    Phase 1 data IS present, the union of both sets is used, catching
    drugs that are in Phase 1 but not yet in the hardcoded set (e.g.
    a drug withdrawn last week that DrugBank picked up but the
    hardcoded list hasn't been updated to include).

    Args:
        drug_name: Drug name (any case). Will be lowercased + stripped
            for matching.
        phase1_withdrawn: Set of drug names (lowercase) from the
            Phase 1 DrugBank CSV.
        hardcoded_withdrawn: Optional set of drug names (lowercase)
            from the hardcoded ``WITHDRAWN_DRUGS`` frozenset. When
            None, uses the imported ``WITHDRAWN_DRUGS``.

    Returns:
        0.0 if the drug is withdrawn (HARD-REJECT), 1.0 otherwise.
    """
    name_lower = str(drug_name).lower().strip()
    if not name_lower:
        return 1.0
    merged = merge_withdrawn_drugs_with_phase1(
        phase1_withdrawn, hardcoded_withdrawn
    )
    if name_lower in merged:
        return 0.0
    return 1.0


def build_reward_function_with_phase1_safety(
    phase1_dir: str,
    config: Optional[RewardConfig] = None,
    drugs_filename: Optional[str] = None,
) -> Tuple["RewardFunction", Set[str], Dict[str, str]]:
    """Build a RewardFunction that uses Phase 1 safety signals.

    TASK 8.4 ROOT FIX: this is the PRODUCTION path for the reward
    function. It:
      1. Loads withdrawn-drug names from the Phase 1 DrugBank CSV.
      2. Merges them with the hardcoded ``WITHDRAWN_DRUGS`` set
         (the union is the patient-safety guardrail).
      3. Constructs a ``RewardFunction`` with the merged set passed
         via ``extra_withdrawn_drugs`` — the existing RewardFunction
         uses this set IN ADDITION to its hardcoded ``WITHDRAWN_DRUGS``
         when computing safety_score (so drugs in EITHER set get
         safety_score=0.0 → reward hard-reject).

    The function returns the RewardFunction AND the Phase 1 safety
    data so the caller can log it / pass it to downstream consumers
    (the validation gate, the bridge, etc.).

    Args:
        phase1_dir: Path to the Phase 1 output directory.
        config: RewardConfig (uses RewardConfig() if None).
        drugs_filename: Override the CSV filename.

    Returns:
        Tuple of (reward_fn, phase1_withdrawn_names, phase1_withdrawn_reasons).

    Raises:
        FileNotFoundError: If the Phase 1 directory does not exist.
        RuntimeError: If the RewardFunction constructor does not accept
            ``extra_withdrawn_drugs`` (the RewardFunction class in
            rl_drug_ranker.py MUST be updated to accept this parameter —
            see the task spec). When this happens, the function falls
            back to constructing a plain RewardFunction (without the
            Phase 1 safety data) and logs a CRITICAL warning.
    """
    import logging
    _logger = logging.getLogger(__name__)

    phase1_withdrawn, phase1_reasons = load_phase1_safety_signals(
        phase1_dir, drugs_filename=drugs_filename
    )

    if phase1_withdrawn:
        _logger.info(
            "TASK 8.4: loaded %d withdrawn drugs from Phase 1 (%s). "
            "These will be MERGED with the hardcoded WITHDRAWN_DRUGS "
            "set (%d drugs) to form the patient-safety guardrail.",
            len(phase1_withdrawn), phase1_dir, len(WITHDRAWN_DRUGS),
        )
    else:
        _logger.warning(
            "TASK 8.4: Phase 1 directory %s contains no withdrawn drugs "
            "(CSV missing, empty, or no rows with is_withdrawn=True). "
            "Falling back to the hardcoded WITHDRAWN_DRUGS set (%d drugs).",
            phase1_dir, len(WITHDRAWN_DRUGS),
        )

    cfg = config if config is not None else RewardConfig()

    # Try to construct RewardFunction with extra_withdrawn_drugs. If the
    # RewardFunction class doesn't accept this parameter (older version
    # of rl_drug_ranker.py), fall back to a plain RewardFunction and log
    # a CRITICAL warning — the caller should know Phase 1 data is NOT
    # being used for patient-safety.
    try:
        reward_fn = RewardFunction(
            config=cfg,
            extra_withdrawn_drugs=phase1_withdrawn,
        )
    except TypeError as exc:
        _logger.critical(
            "TASK 8.4: RewardFunction does not accept 'extra_withdrawn_drugs' "
            "parameter (%s). The Phase 1 safety signals (%d withdrawn drugs) "
            "CANNOT be injected — the reward function will use ONLY the "
            "hardcoded WITHDRAWN_DRUGS set (%d drugs). This is a PATIENT-"
            "SAFETY REGRESSION — update rl/rl_drug_ranker.py RewardFunction "
            "to accept extra_withdrawn_drugs (coordinate with TM9).",
            exc, len(phase1_withdrawn), len(WITHDRAWN_DRUGS),
        )
        reward_fn = RewardFunction(config=cfg)

    return reward_fn, phase1_withdrawn, phase1_reasons


__all__ = [
    "RewardConfig",
    "RewardFunction",
    "compute_reward",
    "load_reward_weights_for_tenant",
    "save_reward_weights_for_tenant",
    "apply_tenant_reward_weights",
    "DEFAULT_REWARD_WEIGHTS_DIR",
    "FEATURE_COLS",
    "REQUIRED_COLUMNS",
    "WITHDRAWN_DRUGS",
    "INDICATION_WITHDRAWN_DRUGS",
    "CONTROLLED_SUBSTANCES",
    "DEFAULT_PROPRIETARY_PREFIXES",
    # TASK 8.4 ROOT FIX: Phase 1 safety-signal loader
    "PHASE1_DRUGS_FILENAME",
    "PHASE1_DRUGS_ALIASES",
    "PHASE1_DRUG_NAME_COLUMN",
    "PHASE1_IS_WITHDRAWN_COLUMN",
    "PHASE1_GROUPS_COLUMN",
    "PHASE1_WITHDRAWN_TOKEN",
    "PHASE1_WITHDRAWN_REASON_COLUMN",
    "load_phase1_safety_signals",
    "merge_withdrawn_drugs_with_phase1",
    "compute_safety_score_with_phase1",
    "build_reward_function_with_phase1_safety",
]
