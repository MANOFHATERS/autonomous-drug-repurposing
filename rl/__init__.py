"""
RL-Driven Hypothesis Ranker package -- Team Cosmic (Phase 4).

V4 ROOT FIX (B-F9): ``rl/`` is now a proper installable Python package,
not a single-file module imported via ``sys.path.insert`` hackery.

This eliminates:
  - The bridge's ``sys.path.insert(0, rl_dir)`` mutation (a global
    side-effect that could shadow other ``rl_drug_ranker.py`` files
    on the user's system).
  - The inability to ``pip install`` the RL module.
  - The inability to type-check across module boundaries with mypy.

Phase 3 (``graph_transformer``) and Phase 4 (``rl``) are now
structurally symmetric: both are proper packages, both are imported
via ordinary Python import statements, and the bridge imports Phase 4
exactly the same way it imports Phase 3 -- ``from rl.rl_drug_ranker
import ...``.

This is the structural foundation for "Phase 3 <-> Phase 4 100%
connected": the two phases now share a single import graph with no
path manipulation, no module shadowing, and no implicit global state.
"""
from __future__ import annotations

# ROOT FIX (FORENSIC-AUDIT-I37): aligned version with graph_transformer package.
# Both packages are now versioned together as "4.1.0".
__version__ = "4.1.0"
__schema_version__ = "4.1.0"

# Re-export the most-used symbols so callers can do
#   from rl import PipelineConfig, run_pipeline, KNOWN_POSITIVES
# without having to know the internal layout.
from .rl_drug_ranker import (
    # Configuration
    RewardConfig,
    PipelineConfig,
    DEFAULT_CONFIG,
    # P0 fix: scientific failure exception
    ScientificFailureError,
    # Constants
    KNOWN_POSITIVES,
    VALIDATED_HYPOTHESES,  # V30 (10.25): separate from KNOWN_POSITIVES to prevent circular leakage
    WITHDRAWN_DRUGS,
    CONTROLLED_SUBSTANCES,
    REQUIRED_COLUMNS,
    FEATURE_COLS,
    # Core classes
    RewardFunction,
    DrugRankingEnv,
    RankedCandidate,
    PipelineMetrics,
    # Functions
    compute_reward,
    validate_input_schema,
    preprocess_data,
    generate_fake_data,
    generate_data_quality_report,
    train_agent,
    evaluate_agent,
    compute_auc,
    extract_policy_prob_high,
    literature_crosscheck,
    check_known_positive_recovery,
    save_results,
    run_pipeline,
    # Data dictionary + schema
    DATA_DICTIONARY,
    INPUT_SCHEMA,
    OUTPUT_SCHEMA,
)

__all__ = [
    "RewardConfig",
    "PipelineConfig",
    "DEFAULT_CONFIG",
    "ScientificFailureError",
    "KNOWN_POSITIVES",
    "VALIDATED_HYPOTHESES",  # V30 (10.25)
    "WITHDRAWN_DRUGS",
    "CONTROLLED_SUBSTANCES",
    "REQUIRED_COLUMNS",
    "FEATURE_COLS",
    "RewardFunction",
    "DrugRankingEnv",
    "RankedCandidate",
    "PipelineMetrics",
    "compute_reward",
    "validate_input_schema",
    "preprocess_data",
    "generate_fake_data",
    "generate_data_quality_report",
    "train_agent",
    "evaluate_agent",
    "compute_auc",
    "extract_policy_prob_high",
    "literature_crosscheck",
    "check_known_positive_recovery",
    "save_results",
    "run_pipeline",
    "DATA_DICTIONARY",
    "INPUT_SCHEMA",
    "OUTPUT_SCHEMA",
    "__version__",
    "__schema_version__",
]
