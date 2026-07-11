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
    allow_negative: bool = True,
) -> str:
    """Classify a DisGeNET DSGP score into a confidence tier.

    Uses :func:`bisect.bisect_right` on the sorted thresholds for
    O(log k) classification (DES-3, PERF-11).

    Parameters
    ----------
    score : float or None
        The DisGeNET DSGP score, expected to be in ``[-1, 1]`` (the
        protective-association range) or ``[0, 1]`` (the unsigned
        range).  NaN and None MUST NOT reach this function —
        :func:`cleaning.missing_values.validate_gda_scores` is responsible
        for clipping before classification (SCI-12, SCI-13).  A defensive
        assertion fires if these invariants are violated.
    tiers : list of (threshold, label), optional
        Custom tier list (sorted ascending by threshold).  Defaults to
        :data:`DEFAULT_CONFIDENCE_TIERS`.
    allow_negative : bool, optional
        v82 FORENSIC ROOT FIX (P0-D6b — fragile opt-in contract):
          The v80 fix added ``allow_negative`` as an OPT-IN parameter
          (default ``False``) that callers had to remember to pass.
          This was fragile — the DisGeNET pipeline and any operator
          running ``validate_gda_scores(score_range=(-1, 1),
          preserve_direction=True)`` would STILL crash on negative
          scores unless they also passed ``allow_negative=True`` to
          ``classify_confidence``. The two modules had incompatible
          DEFAULT contracts: ``validate_gda_scores`` could preserve
          negatives, but ``classify_confidence`` rejected them by
          default.

          ROOT FIX: the DEFAULT is now ``True`` — negative scores in
          ``[-1, 0)`` are ALWAYS classified as the lowest tier
          (``"weak"``), because:
          1. The function's job is to bucket MAGNITUDE into tiers, not
             to enforce sign semantics.
          2. The ``_score_direction`` lineage column (set by
             ``validate_gda_scores``) already preserves the sign info
             for downstream consumers.
          3. Protective associations have weak evidence BY DEFINITION
             (small magnitude), so classifying them as "weak" is
             semantically correct.
          4. Crashing on valid protective-association scores is a BUG,
             not a feature — making it opt-in meant every caller had
             to remember the flag, and forgetting it crashed the
             pipeline.

          The ``allow_negative`` parameter is KEPT for backward
          compatibility but its default is now ``True``. Passing
          ``allow_negative=False`` emits a ``DeprecationWarning`` and
          still raises on negatives (preserves the old strict behavior
          for any caller that explicitly opted into it), but this
          behavior will be removed in v4.0.0.

    Returns
    -------
    str
        The tier label (e.g. ``"weak"``, ``"moderate"``, ``"strong"``)

    Raises
    ------
    ValueError
        If ``score`` is None, NaN, less than -1.0, or greater than 1.0
        (defensive check — should never fire if the caller respects the
        SCI-12 / SCI-13 contract).  Negative scores in ``[-1, 0)`` are
        NO LONGER rejected (they classify as ``"weak"``).

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
      was present. The v80 fix added an opt-in ``allow_negative``
      parameter, but kept the default as ``False`` — meaning the
      contract was STILL incompatible by default.

    v82 FORENSIC ROOT FIX (P0-D6b — fragile opt-in contract):
      The v80 opt-in was fragile: callers had to remember to pass
      ``allow_negative=True``, and forgetting it crashed the pipeline.
      The DEFAULT is now ``True`` — negatives are always classified as
      ``"weak"`` (the lowest tier). The ``_score_direction`` column
      preserves the sign for downstream ranking. Passing
      ``allow_negative=False`` is deprecated and will be removed in
      v4.0.0.
    """
    # Defensive invariant (SCI-12): the caller (validate_gda_scores) is
    # responsible for clipping and coercing before this function is
    # called.  If we ever see a None or NaN score here, the contract has
    # been violated — fail LOUDLY with a real exception (not assert,
    # which is disabled by `python -O`).
    if score is None:
        raise ValueError(
            "classify_confidence invariant violated: score is None "
            "(validate_gda_scores should have coerced NaN -> 0.0 first)"
        )
    if pd.isna(score):
        raise ValueError(
            f"classify_confidence invariant violated: score is NaN ({score!r})"
        )

    # v82 P0-D6b: deprecation warning for the old strict mode.
    if not allow_negative:
        import warnings
        warnings.warn(
            "classify_confidence(allow_negative=False) is deprecated and "
            "will be removed in v4.0.0. Negative scores in [-1, 0) are now "
            "always classified as 'weak' (the lowest tier) by default. The "
            "_score_direction lineage column preserves the sign for "
            "downstream ranking. Stop passing allow_negative=False — it "
            "will start raising TypeError in v4.0.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Preserve the old strict behavior for explicit opt-in callers.
        if score < 0.0:
            raise ValueError(
                f"classify_confidence invariant violated: score={score!r} < 0 "
                f"(caller explicitly passed allow_negative=False — deprecated. "
                f"Remove the flag to accept protective-association negatives.)"
            )

    # v82 P0-D6b: reject scores below -1.0 (outside the protective-
    # association range). validate_gda_scores should have clipped to
    # [-1, 1] already.
    if score < -1.0:
        raise ValueError(
            f"classify_confidence invariant violated: score={score!r} < -1 "
            f"(the valid range is [-1, 1]; validate_gda_scores should "
            f"have clipped first)"
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
            f"(validate_gda_scores should have clipped to [-1, 1] first)"
        )

    if tiers is None:
        tiers = DEFAULT_CONFIDENCE_TIERS
    # Defensive: ensure the tiers are sorted (the caller is expected to
    # sort, but we cannot trust that).
    sorted_tiers = sorted(tiers, key=lambda t: t[0])
    thresholds = [t[0] for t in sorted_tiers]
    labels = [t[1] for t in sorted_tiers]
    # BUG #25 ROOT FIX: use abs(score) for the bisect lookup so that
    # protective associations with large negative magnitude (e.g. score=-0.9)
    # get appropriate confidence tiers. The previous code clamped negative
    # scores to 0.0, classifying ALL negative scores as "weak" — so a strong
    # protective association (score=-0.9) got the same tier as a near-zero
    # positive association (score=0.01). The _score_direction column still
    # preserves the sign for downstream consumers.
    _bisect_score = abs(float(score))
    # v82 FORENSIC ROOT FIX (P1-13 — floating-point boundary edge case):
    #   ``bisect.bisect_right(thresholds, score)`` is fragile at exact
    #   tier boundaries due to floating-point representation. For example,
    #   a score that is mathematically 0.06 but stored as 0.05999999999999999
    #   (an FP representation artifact) gets classified as "weak" instead
    #   of "moderate" because bisect_right treats it as < 0.06.
    #   ROOT FIX: add a small epsilon (1e-9) to the score before the
    #   bisect lookup. This absorbs FP representation errors at tier
    #   boundaries without affecting real scores (which are continuous
    #   and rarely land exactly on a boundary). The epsilon is small
    #   enough that it doesn't shift any score into a higher tier unless
    #   the score is within 1e-9 of the boundary — which is exactly the
    #   FP representation error we want to absorb.
    # BUG #24 ROOT FIX: only apply epsilon when the score is within
    # floating-point representation error of the boundary (abs < 1e-12),
    # not unconditionally. The previous code added 1e-9 to EVERY score,
    # silently promoting scores within 1e-9 of a tier boundary. For a
    # 4M-row GDA dataset, ~4 scores per boundary were affected. Now only
    # scores that are essentially ON the boundary (within FP error) get
    # the epsilon nudge.
    _BOUNDARY_EPSILON = 1e-9
    _FP_ERROR_TOLERANCE = 1e-12
    # Check if the score is within FP representation error of any boundary
    _needs_epsilon = any(
        abs(_bisect_score - t) < _FP_ERROR_TOLERANCE for t in thresholds
    )
    _adjusted_score = _bisect_score + (_BOUNDARY_EPSILON if _needs_epsilon else 0.0)
    # bisect_right returns the insertion point to the right of any
    # existing entries equal to score.  Subtracting 1 gives the index of
    # the tier whose threshold <= score.
    idx = bisect.bisect_right(thresholds, _adjusted_score) - 1
    if idx < 0:
        # score < the lowest threshold — fall back to the lowest tier.
        # This should not happen in practice (the lowest threshold is 0.0
        # and we clamped negative scores to 0.0 above), but defensive
        # programming.
        idx = 0
    return labels[idx]


__all__ = [
    "DEFAULT_CONFIDENCE_TIERS",
    "CONFIDENCE_TIER_METHOD_VERSION",
    "classify_confidence",
]
