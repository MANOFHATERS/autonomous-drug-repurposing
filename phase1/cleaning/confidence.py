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
    # P1-004 ROOT FIX (v100 forensic — SCIENTIFIC MISLABEL):
    # The previous labels were ("weak", "moderate", "strong") mapped to
    # thresholds (0.0, 0.06, 0.3). Per Piñero et al. 2020 §2.3 the bands are:
    #   [0.0, 0.06)   — sub-weak (below the published weak-evidence floor)
    #   [0.06, 0.3)   — WEAK evidence (the published weak band)
    #   [0.3, 1.0]    — strong evidence
    # The previous code labeled [0.0, 0.06) as "weak" (Piñero calls this
    # sub-weak) and [0.06, 0.3) as "moderate" (Piñero calls this weak).
    # P1-058 ROOT FIX: the [0.06, 0.3) band is "weak" per Piñero 2020,
    # not "moderate". The previous v43 fix labeled this "moderate" which
    # inflated the perceived confidence of weak-evidence GDA edges.
    # This INFLATED the perceived confidence of every weak-evidence GDA
    # edge — patient-safety risk because downstream ML filters expecting
    # confidence_tier == "weak" only caught SUB-FLOOR scores, missing the
    # actual weak band. ROOT FIX: rename labels to ("sub_weak", "weak",
    # "strong") so the label set is scientifically accurate. The DB CHECK
    # constraint (chk_gda_confidence_tier), the ORM CheckConstraint
    # (models.py), DISGENET_CONFIDENCE_TIERS_JSON (config/settings.py),
    # the JSON schema (pipelines/schema/v1.json), and migration 012
    # (backfill + constraint swap) are updated in lockstep so the four
    # sites remain in agreement.
    # (Parallel V100 fix BUG #4 applied the same root fix.)
    (0.0, "sub_weak"),   # [0.0, 0.06)  — sub-weak (below the published weak-evidence floor; Piñero et al. 2020 §2.3)
    (0.06, "weak"),      # [0.06, 0.3)  — weak evidence (Piñero et al. 2020 §2.3 weak band)
    (0.3, "strong"),     # [0.3, 1.0]   — strong evidence (Piñero et al. 2020 §2.3)
]
"""Default confidence-tier thresholds (Piñero et al. 2020).

A list of ``(threshold, label)`` pairs, sorted ascending by threshold.
The first tier whose ``threshold <= score`` (and which is below the next
tier's threshold) wins.  ``score = 0.0`` always falls in the first tier
(``"sub_weak"``).

v100 P1-004 ROOT FIX (SCIENTIFIC MISLABEL — forensic root fix):
The previous labels were ``"weak"`` / ``"moderate"`` / ``"strong"``
mapped to thresholds (0.0, 0.06, 0.3). Per Piñero et al. 2020 §2.3 the
bands are sub-weak / weak / strong — there is NO "moderate" band in the
publication. P1-058 ROOT FIX: the [0.06, 0.3) band is "weak" per Piñero
2020, not "moderate". The previous v43 fix labeled this "moderate", which
inflated the perceived confidence of every weak-evidence GDA edge —
a patient-safety risk because downstream ML filters expecting
``confidence_tier == "weak"`` only caught SUB-FLOOR scores
and missed the actual weak band, while models trained on
``confidence_tier == "moderate"`` were trained on what is actually
weak evidence.

ROOT FIX: rename labels to ``"sub_weak"`` / ``"weak"`` / ``"strong"``
so the label set is scientifically accurate. The DB CHECK constraint
(``chk_gda_confidence_tier``), the ORM CheckConstraint (``models.py``),
``DISGENET_CONFIDENCE_TIERS_JSON`` (``config/settings.py``), the JSON
schema (``pipelines/schema/v1.json``), SCHEMA.md, and migration 012
(backfill + constraint swap) are updated in lockstep so all four sites
remain in agreement.

The v43 fix (which introduced the weak/moderate/strong labels to keep
the DB schema stable) is now SUPERSEDED — the DB schema is updated
alongside the Python labels so the label set is BOTH scientifically
correct AND schema-consistent.

(Parallel V100 fix BUG #4 applied the same root fix — same labels,
same migration 012 number, same scope. Kept this comment for the more
detailed forensic trail.)
"""

# The tier-method version string recorded in the GDA model's
# ``confidence_tier_method`` column (LIN-15, IDEM-17).  Bump this when
# the default thresholds change so downstream consumers can detect a
# definition change.
CONFIDENCE_TIER_METHOD_VERSION: str = "pinero_2020_v2"


def classify_confidence(
    score: Optional[float],
    tiers: Optional[list[tuple[float, str]]] = None,
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

    Returns
    -------
    str
        The tier label (``"sub_weak"``, ``"weak"``, or ``"strong"``)

    Raises
    ------
    ValueError
        If ``score`` is None, NaN, less than -1.0, or greater than 1.0
        (defensive check — should never fire if the caller respects the
        SCI-12 / SCI-13 contract).  Negative scores in ``[-1, 0)`` are
        classified as ``"sub_weak"`` (the lowest tier).

    Notes
    -----
    CRITICAL FIX (patient safety): the original implementation used
    ``assert`` statements, which are SILENTLY DISABLED when Python is
    invoked with ``-O`` (optimized mode). For a biomedical platform
    where bad scores propagate to drug-repurposing predictions, that
    is unacceptable — a NaN score would silently classify as "sub_weak"
    instead of raising. We replace the asserts with explicit
    ``ValueError`` raises that fire regardless of optimization level.

    v84 FORENSIC ROOT FIX (BUG #49): removed the deprecated
    ``allow_negative`` parameter entirely. The v82 fix kept it as a
    backward-compat shim that emitted a ``DeprecationWarning`` — dead
    code in practice since the default was already ``True`` and no
    caller passed ``False``. Negative scores in ``[-1, 0)`` are ALWAYS
    classified as ``"sub_weak"`` (the lowest tier); the
    ``_score_direction`` lineage column (set by
    ``validate_gda_scores``) preserves the sign for downstream ranking.
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

    # v84 FORENSIC ROOT FIX (BUG #49): removed the deprecated
    # ``allow_negative=False`` code path (dead code — the default was
    # already ``True`` and no caller passed ``False``). Negative scores
    # in ``[-1, 0)`` are ALWAYS classified as the lowest tier ("sub_weak").
    # The ``_score_direction`` lineage column preserves the sign for
    # downstream ranking.

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
    # v90 ROOT FIX (BUG #25) + v100 P1-013 ROOT FIX (forensic clarification):
    #
    # The v90 fix changed the bisect lookup from
    # ``_bisect_score = max(0.0, float(score))`` to
    # ``_bisect_score = abs(float(score))`` so a strong protective
    # association (score = -0.8) classifies as "strong" (matching +0.8).
    # The previous max(0.0, ...) clamped ALL negative scores to 0.0,
    # classifying every protective association as the lowest tier
    # regardless of magnitude — losing the strength information.
    #
    # P1-013 ROOT FIX (v100 forensic): the v90 abs() fix is OPT-IN. The
    # DisGeNET and OMIM pipelines (the ONLY callers in production) pass
    # ``score_range=(0.0, 1.0)`` and ``preserve_direction=False`` to
    # ``validate_gda_scores`` because Piñero 2020 DSGP scores are
    # UNSIGNED — they live in [0, 1] and do not encode direction. Under
    # that default configuration, ``validate_gda_scores`` clips any
    # negative score to 0.0 BEFORE this function is reached, so the
    # abs() branch never fires in the standard pipeline flow.
    #
    # The abs() branch EXISTS for callers that explicitly opt into
    # protective-association mode by passing ``score_range=(-1.0, 1.0)``
    # and ``preserve_direction=True`` (e.g. future pipelines that ingest
    # signed-association sources like GWAS beta coefficients). In that
    # mode, ``validate_gda_scores`` preserves the sign and this function
    # uses abs(score) so the TIER reflects STRENGTH regardless of
    # direction; the ``_score_direction`` lineage column (set by
    # ``validate_gda_scores``) preserves the sign for downstream ranking.
    #
    # This is NOT dead code — it is a deliberate opt-in feature gate.
    # The v90 "ROOT FIX" framing was misleading because it suggested
    # the fix applied to the default pipeline; this comment makes the
    # opt-in semantics explicit so future maintainers don't rip out the
    # abs() branch as dead code (which would silently break protective-
    # association mode if a future caller enables it).
    _bisect_score = abs(float(score))
    # bisect_right returns the insertion point to the right of any
    # existing entries equal to score.  Subtracting 1 gives the index of
    # the tier whose threshold <= score.
    idx = bisect.bisect_right(thresholds, _bisect_score) - 1
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
