"""Shared scientific-validation thresholds for the Autonomous Drug Repurposing Platform.

P4-013 ROOT FIX (HIGH — Team Member 12 / Phase 4): the previous code had
TWO independent definitions of the KP recovery threshold:

  1. ``rl/rl_drug_ranker.py`` used ``config.min_kp_recovery_rate``
     (default 0.2) at the scientific_validation gate.
  2. ``graph_transformer/gt_rl_bridge.py`` used
     ``max(rl_config_threshold, 0.5)`` (effectively 0.5) at its own
     scientific_validation gate.

A run with ``kp_recovery_rate = 0.4`` would PASS the ranker's gate
(0.4 >= 0.2) but FAIL the bridge's gate (0.4 < 0.5). The two components
disagreed on whether the run was scientifically valid, leaving the ops
team unable to determine if the run succeeded. The bridge writes its
CSV; the ranker refuses to; the pipeline state is inconsistent.

The fix defines a SINGLE constant — ``KP_RECOVERY_THRESHOLD`` — in this
shared module. Both ``rl_drug_ranker.py`` and ``gt_rl_bridge.py``
import it, so the threshold can NEVER drift between the two components.
A CI test (``tests/test_team12_p4_012_to_018.py::test_p4_013_*``)
verifies both files import and use the same constant.

The threshold value is 0.5 (50%), matching the V1 launch criterion
implied by the bridge's existing ``max(rl_config_threshold, 0.5)``
logic. The DOCX §8 V1 launch criteria do not specify a numeric KP
recovery threshold, but the bridge's existing 0.5 is the stricter
value and was clearly intended as the production bar (the ranker's
0.2 was a developer-friendly default that the bridge intentionally
overrode). Standardizing on 0.5 means a run must recover at least
half of the known positives in the test set to be considered
scientifically valid — a meaningful bar for a drug-repurposing
platform where known positives are the ground truth.

This module is INTENTIONALLY minimal — it contains only the shared
thresholds. It has no dependencies on torch, pandas, or any other
heavy import, so it can be imported from both the RL ranker (which
runs in CI without torch) and the GT bridge (which requires torch).
"""
from __future__ import annotations

# P4-013: the single source of truth for the KP recovery threshold.
# Both rl_drug_ranker.py and gt_rl_bridge.py import this constant.
# Do NOT define a separate threshold anywhere else.
KP_RECOVERY_THRESHOLD: float = 0.5
"""Minimum fraction of known positives that must be recovered in the
test set for the scientific_validation gate to pass. The V1 launch
criterion (DOCX §8) requires the RL agent to produce "consistent,
non-random rankings" — recovering ≥50% of known positives in the
held-out test set is the operationalization of that criterion.

A run with ``kp_recovery_rate < KP_RECOVERY_THRESHOLD`` FAILS the
scientific_validation gate and the pipeline refuses to write its
output CSV (no ``--allow-invalid-output`` bypass — see P4-014).
"""

# P4-013: the minimum number of literature-supported predictions
# required by the V1 launch criterion (DOCX §8: "At least 5 top
# predictions are supported by published literature"). This is
# already defined inline in rl_drug_ranker.py, but we expose it here
# so downstream consumers (bridge, dashboard, CI) can import a
# single constant instead of hardcoding 5.
MIN_LITERATURE_SUPPORTED: int = 5
"""Minimum number of literature-supported predictions for the V1 launch
criterion (DOCX §8). The scientific_validation gate checks
``n_literature_supported >= MIN_LITERATURE_SUPPORTED``.
"""

# P4-013: the GT test AUC threshold for the V1 launch criterion
# (DOCX §8: "Graph Transformer achieves >0.85 AUC on held-out
# drug-disease pairs"). This is already defined as
# ``config.gt_test_auc_threshold`` in rl_drug_ranker.py (default 0.85),
# but we expose the canonical value here so the bridge can import it
# without duplicating the magic number.
GT_TEST_AUC_THRESHOLD: float = 0.85
"""Minimum GT test AUC for the V1 launch criterion (DOCX §8). The
scientific_validation gate checks ``gt_test_auc > GT_TEST_AUC_THRESHOLD``.
"""

# P4-013: the RL AUC threshold. The DOCX §8 V1 launch criterion
# requires "RL agent produces consistent, non-random rankings" — an
# AUC > 0.5 (better than random) is the operationalization.
RL_AUC_THRESHOLD: float = 0.5
"""Minimum RL AUC for the V1 launch criterion. AUC <= 0.5 means the
RL agent is no better than random ranking — the scientific_validation
gate fails.
"""


__all__ = [
    "KP_RECOVERY_THRESHOLD",
    "MIN_LITERATURE_SUPPORTED",
    "GT_TEST_AUC_THRESHOLD",
    "RL_AUC_THRESHOLD",
]
