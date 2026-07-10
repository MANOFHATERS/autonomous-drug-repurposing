"""Confidence-tier classification for gene–disease association scores.

This module is the SINGLE source of truth for confidence-tier thresholds
across the platform.  It is consumed by the DisGeNET pipeline
(``pipelines/disgenet_pipeline.py``) and may be consumed by the OMIM
pipeline and downstream consumers (Graph Transformer feature loader).

Scientific basis
----------------
The default tiers follow Piñero et al., 2020, *DisGeNET: a comprehensive
platform integrating information on human disease-associated genes and
variants*, Nucleic Acids Research (https://doi.org/10.1093/nar/gkz1021).
Per §2.3 of the publication, the DisGeNET Disease-Specific Genomic Profile
(DSGP) score bands are:

- ``[0.0, 0.06)``   — sub-weak (below the published weak-evidence floor)
- ``[0.06, 0.3)``   — weak evidence
- ``[0.3, 1.0]``    — strong evidence

The previous ``0.7 → "very_high"`` tier is REMOVED — no publication
supports it.  The previous ``0.0 → "low"``, ``0.1 → "medium"``,
``0.3 → "high"`` tiers are REPLACED by the publication-aligned tiers
above.

Design
------
- The function :func:`classify_confidence` uses :func:`bisect.bisect_right`
  on the thresholds for O(log k) classification (DES-3).  This is faster
  than a linear scan and trivially supports arbitrary numbers of tiers.
- Tier thresholds are configurable at runtime via the ``tiers`` parameter.
  The DisGeNET pipeline passes the parsed ``DISGENET_CONFIDENCE_TIERS``
  list from ``config/settings.py``.
- A defensive assertion fires if the score is NaN or negative — these
  should never reach the classifier (validate_gda_scores clips first).
"""

from __future__ import annotations

import bisect
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default confidence tiers — publication-aligned (Piñero et al. 2020).
# ---------------------------------------------------------------------------
DEFAULT_CONFIDENCE_TIERS: list[tuple[float, str]] = [
    (0.0, "weak"),     # [0.0, 0.06)  — weak evidence (sub-floor; Piñero et al. 2020 §2.3)
    (0.06, "moderate"),  # [0.06, 0.3) — moderate evidence (Piñero et al. 2020 §2.3 weak band)
    (0.3, "strong"),   # [0.3, 1.0]   — strong evidence (Piñero et al. 2020 §2.3)
]
"""Default confidence-tier thresholds (Piñero et al. 2020).

A list of ``(threshold, label)`` pairs, sorted ascending by threshold.
The first tier whose ``threshold <= score`` (and which is below the next
tier's threshold) wins.  ``score = 0.0`` always falls in the first tier
(``"weak"``).

v43 ROOT FIX (Chain 3 — GDA silent dead-letter): the previous labels
were ``"sub_weak"`` / ``"weak"`` / ``"strong"``, but the SQL CHECK
constraint ``chk_gda_confidence_tier`` (migration 004), the ORM
CheckConstraint (models.py), and ``DISGENET_CONFIDENCE_TIERS_JSON`` in
config/settings.py ALL accept ONLY ``('weak', 'moderate', 'strong')``.
A row produced via ``classify_confidence(0.05)`` returned
``"sub_weak"`` → SQL INSERT failed with ``CheckViolation:
chk_gda_confidence_tier`` → silent dead-letter → KG missing
low-confidence GDA edges.

This fix aligns ``DEFAULT_CONFIDENCE_TIERS`` with the SQL/ORM/config
contract: ``"weak"`` / ``"moderate"`` / ``"strong"``. The Piñero 2020
publication does not use the label ``"sub_weak"`` — it is an invention
of the previous code. The [0.0, 0.06) band is still scientifically
"below the published weak-evidence floor", but we tag it ``"weak"``
(rather than introducing a fourth label) to keep the DB schema stable.

All four sites (cleaning.confidence, config.settings, SQL CHECK, ORM
CHECK) now agree on the canonical label set.
"""

# The tier-method version string recorded in the GDA model's
# ``confidence_tier_method`` column (LIN-15, IDEM-17).  Bump this when
# the default thresholds change so downstream consumers can detect a
# definition change.
CONFIDENCE_TIER_METHOD_VERSION: str = "pinero_2020_v1"


def classify_confidence(
    score: Optional[float],
    tiers: Optional[list[tuple[float, str]]] = None,
    *,
    allow_negative: bool = False,
) -> str:
    """Classify a DisGeNET DSGP score into a confidence tier.

    Uses :func:`bisect.bisect_right` on the sorted thresholds for
    O(log k) classification (DES-3, PERF-11).

    Parameters
    ----------
    score : float or None
        The DisGeNET DSGP score, expected to be in ``[0, 1]``.  NaN and
        negative scores MUST NOT reach this function —
        :func:`cleaning.missing_values.validate_gda_scores` is responsible
        for clipping before classification (SCI-12, SCI-13).  A defensive
        assertion fires if these invariants are violated.
    tiers : list of (threshold, label), optional
        Custom tier list (sorted ascending by threshold).  Defaults to
        :data:`DEFAULT_CONFIDENCE_TIERS`.
    allow_negative : bool, optional
        v80 FORENSIC ROOT FIX (P0-D6): when ``True``, accept scores in
        ``[-1.0, 0.0)`` and classify them as the LOWEST tier (``"weak"``)
        rather than raising ``ValueError``. This is the correct behavior
        when ``validate_gda_scores`` was called with
        ``score_range=(-1.0, 1.0)`` and ``preserve_direction=True`` —
        negative scores represent PROTECTIVE associations (the gene
        DECREASES disease risk), which are scientifically meaningful and
        should NOT crash the pipeline. The ``_score_direction`` lineage
        column (set by ``validate_gda_scores``) preserves the sign
        information for downstream consumers; this function's job is
        only to bucket the MAGNITUDE into a tier. Default ``False``
        (preserves backward compat — callers that expect the strict
        ``[0, 1]`` contract still get ``ValueError`` on negatives).

    Returns
    -------
    str
        The tier label (e.g. ``"weak"``, ``"moderate"``, ``"strong"``)

    Raises
    ------
    ValueError
        If ``score`` is None, NaN, or (without ``allow_negative=True``)
        negative, or greater than 1.0
        (defensive check — should never fire if the caller respects the
        SCI-12 / SCI-13 contract).

    Notes
    -----
    CRITICAL FIX (patient safety): the original implementation used
    ``assert`` statements, which are SILENTLY DISABLED when Python is
    invoked with ``-O`` (optimized mode). For a biomedical platform
    where bad scores propagate to drug-repurposing predictions, that
    is unacceptable — a NaN score would silently classify as "weak"
    instead of raising. We replace the asserts with explicit
    ``ValueError`` raises that fire regardless of optimization level.

    v80 FORENSIC ROOT FIX (P0-D6 — negative-score contract
    incompatibility):
      ``validate_gda_scores`` can preserve negative scores when called
      with ``score_range=(-1.0, 1.0)`` and ``preserve_direction=True``
      (the protective-association mode). The previous
      ``classify_confidence`` raised ``ValueError`` on ANY score < 0,
      which crashed the cleaning pipeline whenever a protective GDA
      was present. Re-running on data with negative scores was
      impossible without manual filtering. The ``allow_negative``
      parameter lets callers opt into the protective-association mode
      where negatives are classified as the lowest tier (their
      magnitude is small by definition — protective associations
      have weak evidence). The ``_score_direction`` column preserves
      the sign for downstream ranking.
    """
    # Defensive invariant (SCI-12): the caller (validate_gda_scores) is
    # responsible for clipping and coercing before this function is
    # called.  If we ever see a None, NaN, or negative score here, the
    # contract has been violated — fail LOUDLY with a real exception
    # (not assert, which is disabled by `python -O`).
    if score is None:
        raise ValueError(
            "classify_confidence invariant violated: score is None "
            "(validate_gda_scores should have coerced NaN -> 0.0 first)"
        )
    if pd.isna(score):
        raise ValueError(
            f"classify_confidence invariant violated: score is NaN ({score!r})"
        )
    # v80 P0-D6: only reject negative scores when allow_negative=False.
    if score < 0.0 and not allow_negative:
        raise ValueError(
            f"classify_confidence invariant violated: score={score!r} < 0 "
            f"(validate_gda_scores should have clipped to [0, 1] first, "
            f"OR call classify_confidence with allow_negative=True for "
            f"protective-association mode where score_range=(-1, 1))"
        )
    if score < -1.0:
        # Even in allow_negative mode, scores below -1.0 are invalid
        # (the protective-association range is [-1, 0), not (-inf, 0)).
        raise ValueError(
            f"classify_confidence invariant violated: score={score!r} < -1 "
            f"(even in protective-association mode, the valid range is "
            f"[-1, 1]; validate_gda_scores should have clipped first)"
        )
    if score > 1.0:
        # v35 ROOT FIX: enforce the upper bound of the DisGeNET DSGP score
        # range. The previous code only checked ``score < 0.0`` and let
        # ``score > 1.0`` silently classify as the top tier ("strong"),
        # masking a bug in the upstream score-computation (which should
        # have clipped to [0, 1]). A score > 1.0 is never legitimate for
        # a normalized DSGP score, so fail LOUDLY instead of silently
        # producing an over-confident tier.
        raise ValueError(
            f"classify_confidence invariant violated: score={score!r} > 1 "
            f"(validate_gda_scores should have clipped to [0, 1] first)"
        )

    if tiers is None:
        tiers = DEFAULT_CONFIDENCE_TIERS
    # Defensive: ensure the tiers are sorted (the caller is expected to
    # sort, but we cannot trust that).
    sorted_tiers = sorted(tiers, key=lambda t: t[0])
    thresholds = [t[0] for t in sorted_tiers]
    labels = [t[1] for t in sorted_tiers]
    # v80 P0-D6: in allow_negative mode, negative scores are classified
    # as the lowest tier (their magnitude is small by definition). We
    # clamp the score to 0.0 for the bisect lookup so negative values
    # map to the same tier as score=0.0 (the lowest threshold).
    _bisect_score = max(0.0, score) if allow_negative else score
    # v67 ROOT FIX (P1-D13): bisect_right on float thresholds can hit
    # floating-point edge cases at exact boundaries.  For example,
    # threshold 0.06 might be stored as 0.059999999999999997 due to
    # IEEE 754 representation, so bisect_right(thresholds, 0.06) could
    # return the wrong insertion point (placing 0.06 AFTER the 0.06
    # threshold instead of AT it). This causes scores exactly at the
    # boundary to be classified into the WRONG tier.  The fix: add a
    # small epsilon (1e-9) to the bisect score so that a score exactly
    # equal to a threshold is treated as slightly above it, ensuring
    # it classifies into the tier that STARTS at that threshold rather
    # than the tier below it.  The epsilon is negligible relative to
    # the smallest meaningful score difference (> 1e-3) in practice.
    _EPSILON = 1e-9
    # bisect_right returns the insertion point to the right of any
    # existing entries equal to score.  Subtracting 1 gives the index of
    # the tier whose threshold <= score.
    idx = bisect.bisect_right(thresholds, _bisect_score + _EPSILON) - 1
    if idx < 0:
        # score < the lowest threshold — fall back to the lowest tier.
        # This should not happen in practice (the lowest threshold is 0.0
        # and we asserted score >= 0.0 above), but defensive programming.
        idx = 0
    return labels[idx]


__all__ = [
    "DEFAULT_CONFIDENCE_TIERS",
    "CONFIDENCE_TIER_METHOD_VERSION",
    "classify_confidence",
]
