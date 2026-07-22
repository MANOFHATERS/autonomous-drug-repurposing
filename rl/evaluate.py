"""rl.evaluate — Evaluation & AUC (P4-008 modular wrapper + audit #199).

P4-008 ROOT FIX: modular wrapper around the evaluation functions:
evaluate_agent, compute_auc, extract_policy_prob_high,
check_known_positive_recovery, literature_crosscheck, display_top_candidates,
split_data. See rl/env.py for the full P4-008 rationale.

Audit #199 ROOT FIX: also exports ``produce_evaluation_report`` which
produces a complete JSON-serializable evaluation report with:
  - AUC (from compute_auc)
  - KP recovery (from check_known_positive_recovery)
  - Top-N candidates with per-candidate feature values
  - Model info (class, device, policy arch)
  - Timestamp (timezone-aware UTC)

The previous ``evaluate_agent`` only returned a ``List[RankedCandidate]``.
Most callers just printed AUC and discarded the rest. The DOCX §8 V1
launch contract requires all of: AUC, KP recovery, top-N candidates,
AND per-candidate feature values (for the dashboard's Hypothesis Detail
View). ``produce_evaluation_report`` assembles all of that in one call.

P4-021 v142 FORENSIC ROOT FIX (deprecation of pure re-export shims):
This module is a PURE RE-EXPORT SHIM (no actual logic). NEW CODE
should import directly from rl.rl_drug_ranker. The shim remains
functional for backward compat with existing callers. Set the env
var RL_WARN_ON_SHIM_IMPORT=1 to surface a DeprecationWarning on import
(for migration tracking). This shim will be REMOVED in v5.0.

Callers can now import (DEPRECATED — use rl.rl_drug_ranker directly):
    from rl.evaluate import evaluate_agent, compute_auc, produce_evaluation_report
"""
from __future__ import annotations

import os as _os
import warnings as _warnings

# P4-021 v142: emit DeprecationWarning ONLY when RL_WARN_ON_SHIM_IMPORT=1
# (avoids breaking existing tests; lets operators opt-in to migration tracking).
if _os.environ.get("RL_WARN_ON_SHIM_IMPORT", "0") == "1":
    _warnings.warn(
        "rl.evaluate is a PURE RE-EXPORT SHIM (P4-021 v142). Import from "
        "rl.rl_drug_ranker directly instead. This shim will be REMOVED in v5.0.",
        DeprecationWarning,
        stacklevel=2,
    )

from .rl_drug_ranker import (
    evaluate_agent,
    compute_auc,
    extract_policy_prob_high,
    check_known_positive_recovery,
    literature_crosscheck,
    display_top_candidates,
    split_data,
    # Audit #199: full JSON evaluation report
    produce_evaluation_report,
)

__all__ = [
    "evaluate_agent",
    "compute_auc",
    "extract_policy_prob_high",
    "check_known_positive_recovery",
    "literature_crosscheck",
    "display_top_candidates",
    "split_data",
    # Audit #199
    "produce_evaluation_report",
]
