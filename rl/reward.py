"""rl.reward — Reward Function & Config (P4-008/P4-021 modular wrapper).

TEAMMATE 3 — P1→P4 SAFETY WIRING ROOT FIX (v131, hostile-auditor pass):

This module is the SINGLE SOURCE OF TRUTH for loading Phase 1 DrugBank
withdrawal data and merging it with the hardcoded ``WITHDRAWN_DRUGS``
frozenset (which is the FALLBACK, not the primary source). The previous
version of this module defined ``load_phase1_safety_signals`` and
``build_reward_function_with_phase1_safety`` but they were NEVER CALLED
from production code (``run_pipeline`` line 10207 used the plain
``RewardFunction(config.reward)`` constructor). Even worse, the previous
``RewardFunction.__init__`` did NOT accept the ``extra_withdrawn_drugs``
parameter, so the previous ``build_reward_function_with_phase1_safety``
silently raised ``TypeError`` and fell back to a plain ``RewardFunction``
WITHOUT the Phase 1 data — meaning the Phase 1 safety wiring was 100%
dead code from a patient-safety standpoint.

ROOT FIX (this version):
  1. ``load_phase1_safety_signals`` now:
       - Returns 4 values: (withdrawn_names, withdrawn_reasons,
         withdrawn_countries, withdrawn_years) — the previous 2-value
         return discarded countries and years.
       - Handles ``.csv.gz`` files (tries ``drugbank_drugs.csv`` first,
         then ``drugbank_drugs.csv.gz``).
       - Raises ``FileNotFoundError`` when the CSV is missing (the
         previous behavior returned empty sets, which silently disabled
         the safety guardrail).
       - Reads ``withdrawn_reason``, ``withdrawn_country``,
         ``withdrawn_year`` columns when present.
  2. ``build_reward_function_with_phase1_safety`` now:
       - Returns a SINGLE ``RewardFunction`` (not a 3-tuple).
       - Accepts ``treat_unknown_as_withdrawn: bool = True`` — when
         True (the conservative default), a drug with
         ``is_withdrawn=None`` is treated as WITHDRAWN (fail-CLOSED).
       - Sets all 6 safety attributes on the returned ``RewardFunction``:
           ``_withdrawn_drugs`` (frozenset, merged Phase 1 + hardcoded)
           ``_withdrawn_reasons`` (dict[name -> reason])
           ``_withdrawn_countries`` (dict[name -> country])
           ``_withdrawn_years`` (dict[name -> year])
           ``_treat_unknown_as_withdrawn`` (bool)
           ``_safety_source`` (literal 'phase1' | 'hardcoded' | 'merged')
  3. ``RewardFunction.__init__`` in ``rl_drug_ranker.py`` now accepts
     ``extra_withdrawn_drugs`` (see fix #3 in rl_drug_ranker.py).
  4. ``run_pipeline`` in ``rl_drug_ranker.py`` now calls
     ``build_reward_function_with_phase1_safety`` when
     ``PHASE1_PROCESSED_DIR`` is set (see fix #5 in rl_drug_ranker.py).
  5. ``_check_withdrawn`` helper in ``rl_drug_ranker.py`` implements
     fail-CLOSED semantics for ``is_withdrawn=None`` (see fix #4).

The hardcoded ``WITHDRAWN_DRUGS`` frozenset remains as a defense-in-
depth backstop — it catches drugs that were withdrawn between DrugBank
releases. The Phase 1 data is the PRIMARY source; the hardcoded set is
the FALLBACK.
"""
from __future__ import annotations

import gzip
import logging
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional, Set, Tuple

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

logger = logging.getLogger(__name__)


# =============================================================================
# TEAMMATE 3 — Phase 1 safety-signal loader (ROOT FIX v131).
# =============================================================================
# The Phase 1 contract (phase1/contracts/phase1_schema.py) defines the
# ``drugs`` source with an ``is_withdrawn`` boolean column (line 299) and a
# ``groups`` column that may contain the token "withdrawn" (line 294). The
# DrugBank pipeline (phase1/pipelines/drugbank_pipeline.py) ALSO emits the
# structured withdrawal fields ``withdrawn_reason``, ``withdrawn_country``,
# ``withdrawn_year`` (lines 2448-2456) when the DrugBank XML contains a
# ``<withdrawn-notice>`` element.
#
# This loader reads the Phase 1 DrugBank CSV and returns the FULL structured
# safety signal: names + reasons + countries + years. The previous version
# discarded countries and years — they were loaded by the DrugBank pipeline
# but never consumed by the reward function. This root fix makes them
# available to the reward function so that the safety_score can use them for
# structured scoring (e.g., a drug withdrawn in the US gets a higher penalty
# than a drug withdrawn only in a single small market).
# =============================================================================

# The Phase 1 CSV filenames (per phase1/contracts/phase1_schema.py line 259).
# The ``.gz`` variant is the gzip-compressed form (used when the Phase 1
# pipeline runs in disk-constrained environments).
PHASE1_DRUGS_FILENAME: str = "drugbank_drugs.csv"
PHASE1_DRUGS_FILENAME_GZ: str = "drugbank_drugs.csv.gz"
PHASE1_DRUGS_ALIASES: Tuple[str, ...] = (
    "drugbank_open_drugs.csv",
    "drugbank_open_drugs.csv.gz",
    "chembl_drugs.csv",
    "chembl_drugs.csv.gz",
    "drugs.csv",
    "drugs.csv.gz",
)

# The column names (per phase1/contracts/phase1_schema.py lines 262, 294, 299).
PHASE1_DRUG_NAME_COLUMN: str = "name"
PHASE1_IS_WITHDRAWN_COLUMN: str = "is_withdrawn"
PHASE1_GROUPS_COLUMN: str = "groups"
PHASE1_WITHDRAWN_TOKEN: str = "withdrawn"

# Structured withdrawal columns (emitted by phase1/pipelines/drugbank_pipeline.py
# lines 2454-2456 when the DrugBank XML contains a <withdrawn-notice> element).
PHASE1_WITHDRAWN_REASON_COLUMN: str = "withdrawn_reason"
PHASE1_WITHDRAWN_COUNTRY_COLUMN: str = "withdrawn_country"
PHASE1_WITHDRAWN_YEAR_COLUMN: str = "withdrawn_year"


def _is_withdrawn_truthy(value: Any) -> bool:
    """Return True iff ``value`` represents an affirmative withdrawal flag.

    Handles bool, string "True"/"False"/"1"/"yes"/"y", int 0/1, and NaN/None
    (returns False for NaN/None — the unknown case is handled separately by
    the caller, NOT by this helper).
    """
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        v = value.strip().lower()
        return v in ("true", "1", "yes", "y", "t")
    if isinstance(value, (int, float)):
        # NaN-safe comparison (NaN != NaN).
        try:
            if value != value:  # NaN
                return False
        except Exception:
            return False
        return bool(value)
    return False


def _is_withdrawn_unknown(value: Any) -> bool:
    """Return True iff ``value`` represents an UNKNOWN withdrawal status.

    UNKNOWN means: the column is present but the value is None, empty string,
    or the literal strings 'none'/'null'/'nan'. The conservative default
    (fail-CLOSED) treats unknown as WITHDRAWN.
    """
    if value is None:
        return True
    if isinstance(value, str):
        v = value.strip().lower()
        return v in ("", "none", "null", "nan", "na", "n/a")
    if isinstance(value, float):
        try:
            if value != value:  # NaN
                return True
        except Exception:
            pass
    return False


def load_phase1_safety_signals(
    phase1_dir: str,
    drugs_filename: Optional[str] = None,
) -> Tuple[Set[str], Dict[str, str], Dict[str, str], Dict[str, Optional[int]]]:
    """Load withdrawn-drug safety signals from the Phase 1 DrugBank CSV.

    TEAMMATE 3 ROOT FIX (v131): returns the FULL structured safety signal
    (names + reasons + countries + years), handles ``.csv.gz`` files, and
    raises ``FileNotFoundError`` when the CSV is missing.

    A row is flagged withdrawn if EITHER:
      1. The ``is_withdrawn`` column is True (boolean), OR
      2. The ``groups`` column contains the token ``withdrawn``.

    The two checks are redundant by design — DrugBank sometimes populates
    only one. The function is defensive: missing columns, NaN values, and
    unexpected dtypes are handled gracefully.

    Args:
        phase1_dir: Path to the Phase 1 output directory (the directory
            that contains ``drugbank_drugs.csv`` or one of its aliases).
        drugs_filename: Override the filename (defaults to trying
            ``drugbank_drugs.csv`` first, then ``drugbank_drugs.csv.gz``,
            then each alias). When provided, ONLY this filename is tried
            (plus the ``.gz`` variant if the base name doesn't already
            end in ``.gz``).

    Returns:
        Tuple of (withdrawn_names, withdrawn_reasons, withdrawn_countries,
        withdrawn_years):
            - withdrawn_names: ``Set[str]`` of lowercase drug names flagged
              withdrawn in the Phase 1 CSV.
            - withdrawn_reasons: ``Dict[str, str]`` mapping drug name
              (lowercase) to the withdrawal reason (from the
              ``withdrawn_reason`` column if present, else "withdrawn").
            - withdrawn_countries: ``Dict[str, str]`` mapping drug name
              (lowercase) to the withdrawal country (from the
              ``withdrawn_country`` column if present, else "").
            - withdrawn_years: ``Dict[str, Optional[int]]`` mapping drug
              name (lowercase) to the withdrawal year (from the
              ``withdrawn_year`` column if present, else None).

    Raises:
        FileNotFoundError: If ``phase1_dir`` does not exist, OR if the
            drugs CSV is not found in ``phase1_dir`` (after trying all
            candidates). The previous behavior returned empty sets when
            the CSV was missing — this silently disabled the safety
            guardrail. The root fix raises so the caller can decide
            whether to fall back to the hardcoded set or fail loudly.
    """
    if not os.path.isdir(phase1_dir):
        raise FileNotFoundError(
            f"Phase 1 directory not found: {phase1_dir}. The Phase 1 "
            f"pipeline must run first (phase1/run_pipeline.py) to produce "
            f"the DrugBank CSV. Pass the directory that contains "
            f"'{PHASE1_DRUGS_FILENAME}' or '{PHASE1_DRUGS_FILENAME_GZ}'."
        )

    # Resolve the CSV path. When the caller overrides ``drugs_filename``,
    # we try ONLY that filename (plus the .gz variant if applicable).
    # When ``drugs_filename`` is None, we try the canonical name, its
    # .gz variant, then each alias (and each alias's .gz variant).
    candidate_names: list[str]
    if drugs_filename is not None:
        candidate_names = [drugs_filename]
        # If the override doesn't already end in .gz, also try the .gz variant.
        if not drugs_filename.endswith(".gz"):
            candidate_names.append(drugs_filename + ".gz")
    else:
        candidate_names = [PHASE1_DRUGS_FILENAME, PHASE1_DRUGS_FILENAME_GZ]
        candidate_names.extend(PHASE1_DRUGS_ALIASES)

    csv_path: Optional[str] = None
    for name in candidate_names:
        if name is None:
            continue
        candidate = os.path.join(phase1_dir, name)
        if os.path.isfile(candidate):
            csv_path = candidate
            break

    if csv_path is None:
        # ROOT FIX v131: raise FileNotFoundError instead of returning empty
        # sets. The previous behavior silently disabled the safety
        # guardrail when the CSV was missing — a patient-safety hazard.
        raise FileNotFoundError(
            f"DrugBank drugs CSV not found in {phase1_dir}. Tried: "
            f"{candidate_names}. The Phase 1 pipeline must run first to "
            f"produce the CSV, or the caller must fall back to the "
            f"hardcoded WITHDRAWN_DRUGS frozenset."
        )

    # Read the CSV (handle .gz transparently).
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            f"TEAMMATE-3: pandas is required to load Phase 1 safety signals "
            f"from {csv_path} but is not installed. Install with: "
            f"pip install pandas"
        ) from exc

    try:
        if csv_path.endswith(".gz"):
            with gzip.open(csv_path, "rt", encoding="utf-8-sig") as f:
                df = pd.read_csv(f, low_memory=False)
        else:
            df = pd.read_csv(csv_path, encoding="utf-8-sig", low_memory=False)
    except Exception as exc:
        raise RuntimeError(
            f"TEAMMATE-3: failed to read Phase 1 drugs CSV at {csv_path}: "
            f"{type(exc).__name__}: {exc}"
        ) from exc

    if len(df) == 0:
        logger.warning(
            "TEAMMATE-3: Phase 1 drugs CSV %s is empty (0 rows). Returning "
            "empty safety signal — caller should fall back to hardcoded set.",
            csv_path,
        )
        return set(), {}, {}, {}

    # Verify the required name column exists.
    if PHASE1_DRUG_NAME_COLUMN not in df.columns:
        raise RuntimeError(
            f"TEAMMATE-3: Phase 1 drugs CSV at {csv_path} is missing the "
            f"required '{PHASE1_DRUG_NAME_COLUMN}' column (per "
            f"phase1/contracts/phase1_schema.py). Columns present: "
            f"{list(df.columns)}"
        )

    # Lowercase + strip the name column for case-insensitive matching.
    names_lower = (
        df[PHASE1_DRUG_NAME_COLUMN].astype(str).str.lower().str.strip()
    )

    withdrawn_mask = _detect_withdrawn_rows(df)

    withdrawn_names: Set[str] = set()
    withdrawn_reasons: Dict[str, str] = {}
    withdrawn_countries: Dict[str, str] = {}
    withdrawn_years: Dict[str, Optional[int]] = {}

    if withdrawn_mask.any():
        withdrawn_df = df.loc[withdrawn_mask]
        withdrawn_names_lower = (
            withdrawn_df[PHASE1_DRUG_NAME_COLUMN]
            .astype(str).str.lower().str.strip()
            .tolist()
        )
        withdrawn_names = set(withdrawn_names_lower)

        # Read the structured withdrawal columns (optional but emitted by
        # phase1/pipelines/drugbank_pipeline.py when <withdrawn-notice> exists).
        reason_col = (
            PHASE1_WITHDRAWN_REASON_COLUMN
            if PHASE1_WITHDRAWN_REASON_COLUMN in withdrawn_df.columns
            else None
        )
        country_col = (
            PHASE1_WITHDRAWN_COUNTRY_COLUMN
            if PHASE1_WITHDRAWN_COUNTRY_COLUMN in withdrawn_df.columns
            else None
        )
        year_col = (
            PHASE1_WITHDRAWN_YEAR_COLUMN
            if PHASE1_WITHDRAWN_YEAR_COLUMN in withdrawn_df.columns
            else None
        )

        for name_lower, (_, row) in zip(
            withdrawn_names_lower, withdrawn_df.iterrows()
        ):
            # Reason.
            reason = "withdrawn"
            if reason_col is not None:
                reason_val = row.get(reason_col)
                if reason_val is not None and str(reason_val).strip():
                    reason = str(reason_val).strip()
            withdrawn_reasons[name_lower] = reason

            # Country.
            country = ""
            if country_col is not None:
                country_val = row.get(country_col)
                if country_val is not None and str(country_val).strip():
                    country = str(country_val).strip()
            withdrawn_countries[name_lower] = country

            # Year (int or None).
            year: Optional[int] = None
            if year_col is not None:
                year_val = row.get(year_col)
                if year_val is not None:
                    try:
                        if isinstance(year_val, float) and year_val != year_val:
                            # NaN
                            year = None
                        else:
                            year = int(year_val)
                    except (ValueError, TypeError):
                        # Try parsing as string then int.
                        try:
                            year = int(str(year_val).strip())
                        except (ValueError, TypeError):
                            year = None
            withdrawn_years[name_lower] = year

    logger.info(
        "TEAMMATE-3: Loaded %d withdrawn drugs from %s "
        "(reasons: %d, countries: %d, years: %d)",
        len(withdrawn_names), os.path.basename(csv_path),
        sum(1 for v in withdrawn_reasons.values() if v and v != "withdrawn"),
        sum(1 for v in withdrawn_countries.values() if v),
        sum(1 for v in withdrawn_years.values() if v is not None),
    )

    return withdrawn_names, withdrawn_reasons, withdrawn_countries, withdrawn_years


def _detect_withdrawn_rows(df: "Any") -> "Any":  # type: ignore[name-defined]
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
) -> FrozenSet[str]:
    """Merge Phase 1 withdrawn-drug names with the hardcoded fallback set.

    ROOT FIX: the reward function's patient-safety guardrail is the UNION of:
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
        ``FrozenSet[str]`` of lowercase drug names — the union of the
        two inputs. Returned as a frozenset so it can be assigned
        directly to ``RewardFunction._withdrawn_drugs``.
    """
    if hardcoded_withdrawn is None:
        hardcoded_withdrawn = set(WITHDRAWN_DRUGS)
    return frozenset(set(phase1_withdrawn) | set(hardcoded_withdrawn))


def compute_safety_score_with_phase1(
    drug_name: str,
    phase1_withdrawn: Set[str],
    hardcoded_withdrawn: Optional[Set[str]] = None,
) -> float:
    """Compute the safety_score for a drug using Phase 1 data.

    Returns 0.0 (HARD-REJECT) if the drug is in the Phase 1 withdrawn
    set OR the hardcoded withdrawn set. Returns 1.0 (safe) otherwise.

    The hardcoded set is the FALLBACK — when Phase 1 data is missing
    (dev/CI without the bridge), the function degrades to checking
    ONLY the hardcoded set, preserving the existing behavior. When
    Phase 1 data IS present, the union of both sets is used.

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
    treat_unknown_as_withdrawn: bool = True,
) -> "RewardFunction":
    """Build a RewardFunction that uses Phase 1 safety signals.

    TEAMMATE 3 ROOT FIX (v131): this is the PRODUCTION path for the
    reward function. It:

      1. Loads withdrawn-drug names + reasons + countries + years from
         the Phase 1 DrugBank CSV (handling ``.csv.gz`` transparently).
      2. Merges the Phase 1 withdrawn set with the hardcoded
         ``WITHDRAWN_DRUGS`` frozenset (the union is the patient-
         safety guardrail — drugs in EITHER set are hard-rejected).
      3. Constructs a ``RewardFunction`` and sets the following
         safety attributes on it:
           ``_withdrawn_drugs`` (frozenset, merged Phase 1 + hardcoded)
           ``_withdrawn_reasons`` (dict[name -> reason])
           ``_withdrawn_countries`` (dict[name -> country])
           ``_withdrawn_years`` (dict[name -> year])
           ``_treat_unknown_as_withdrawn`` (bool — conservative default True)
           ``_safety_source`` (literal 'phase1' | 'hardcoded' | 'merged')

    When the Phase 1 CSV is missing (``FileNotFoundError``), the function
    falls back to a plain ``RewardFunction`` configured with ONLY the
    hardcoded ``WITHDRAWN_DRUGS`` set, and sets ``_safety_source =
    'hardcoded'``. A CRITICAL warning is logged so the operator knows
    the patient-safety guardrail is running in degraded mode.

    Args:
        phase1_dir: Path to the Phase 1 output directory.
        config: RewardConfig (uses ``RewardConfig()`` if None).
        drugs_filename: Override the CSV filename.
        treat_unknown_as_withdrawn: When True (the conservative default),
            a drug with ``is_withdrawn=None`` is treated as WITHDRAWN
            (fail-CLOSED). When False, ``is_withdrawn=None`` is treated
            as SAFE (fail-OPEN — use only for dev/debug, NEVER in
            production).

    Returns:
        ``RewardFunction`` instance with the safety attributes set.

    Raises:
        Nothing — falls back to hardcoded set on FileNotFoundError.
        Other exceptions (RuntimeError, ImportError) propagate.
    """
    cfg = config if config is not None else RewardConfig()

    try:
        phase1_withdrawn, reasons, countries, years = load_phase1_safety_signals(
            phase1_dir, drugs_filename=drugs_filename
        )
    except FileNotFoundError as exc:
        logger.warning(
            "TEAMMATE-3: %s. Falling back to hardcoded WITHDRAWN_DRUGS "
            "frozenset (%d drugs). The patient-safety guardrail is "
            "running in DEGRADED mode — Phase 1 live data is NOT being "
            "used. Set PHASE1_PROCESSED_DIR to the Phase 1 output "
            "directory to enable live safety signals.",
            exc, len(WITHDRAWN_DRUGS),
        )
        reward_fn = RewardFunction(config=cfg)
        reward_fn._withdrawn_drugs = frozenset(WITHDRAWN_DRUGS)
        reward_fn._withdrawn_reasons = {}
        reward_fn._withdrawn_countries = {}
        reward_fn._withdrawn_years = {}
        reward_fn._treat_unknown_as_withdrawn = treat_unknown_as_withdrawn
        reward_fn._safety_source = "hardcoded"
        return reward_fn

    # Merge Phase 1 withdrawn set with the hardcoded fallback.
    merged_withdrawn = merge_withdrawn_drugs_with_phase1(phase1_withdrawn)

    if phase1_withdrawn:
        logger.info(
            "TEAMMATE-3: Merged safety signals: %d from Phase 1 + %d "
            "hardcoded = %d total (union). treat_unknown_as_withdrawn=%s.",
            len(phase1_withdrawn), len(WITHDRAWN_DRUGS), len(merged_withdrawn),
            treat_unknown_as_withdrawn,
        )
        safety_source = "merged"
    else:
        logger.warning(
            "TEAMMATE-3: Phase 1 directory %s contains 0 withdrawn drugs "
            "(CSV present but no rows with is_withdrawn=True). Using ONLY "
            "the hardcoded WITHDRAWN_DRUGS set (%d drugs).",
            phase1_dir, len(WITHDRAWN_DRUGS),
        )
        safety_source = "hardcoded"

    # Construct RewardFunction. The class now accepts ``extra_withdrawn_drugs``
    # (see fix #3 in rl_drug_ranker.py) — this is the production path.
    # We ALSO set the merged frozenset as the ``_withdrawn_drugs`` attribute
    # so ``_check_withdrawn`` can read it directly (defense-in-depth: even
    # if the RewardFunction's internal ``compute()`` method forgets to
    # consult ``extra_withdrawn_drugs``, the ``_check_withdrawn`` helper
    # will still catch withdrawn drugs via ``_withdrawn_drugs``).
    reward_fn = RewardFunction(
        config=cfg,
        extra_withdrawn_drugs=set(phase1_withdrawn),
    )
    reward_fn._withdrawn_drugs = merged_withdrawn
    reward_fn._withdrawn_reasons = reasons
    reward_fn._withdrawn_countries = countries
    reward_fn._withdrawn_years = years
    reward_fn._treat_unknown_as_withdrawn = treat_unknown_as_withdrawn
    reward_fn._safety_source = safety_source

    return reward_fn


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
    # Phase 1 safety-signal loader (TEAMMATE-3 ROOT FIX v131)
    "PHASE1_DRUGS_FILENAME",
    "PHASE1_DRUGS_FILENAME_GZ",
    "PHASE1_DRUGS_ALIASES",
    "PHASE1_DRUG_NAME_COLUMN",
    "PHASE1_IS_WITHDRAWN_COLUMN",
    "PHASE1_GROUPS_COLUMN",
    "PHASE1_WITHDRAWN_TOKEN",
    "PHASE1_WITHDRAWN_REASON_COLUMN",
    "PHASE1_WITHDRAWN_COUNTRY_COLUMN",
    "PHASE1_WITHDRAWN_YEAR_COLUMN",
    "load_phase1_safety_signals",
    "merge_withdrawn_drugs_with_phase1",
    "compute_safety_score_with_phase1",
    "build_reward_function_with_phase1_safety",
]
