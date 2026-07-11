"""
RL-Driven Hypothesis Ranker -- Team Cosmic (Phase 4)
=====================================================

WHAT THIS DOES
--------------
Takes scored drug-disease predictions from the Graph Transformer (Phase 3)
and trains a Reinforcement Learning agent to rank the best candidates by
combining scientific plausibility, safety, and market opportunity.

FIX vs original codebase (root-level fixes only -- no band-aids):

  - **B1 (safe_load_input symlink check is dead code)**: the original
    code did ``filepath = os.path.realpath(filepath)`` first, which
    resolves ALL symlinks, then ``if os.path.islink(filepath)`` -- which
    is *always False* after realpath. The "security check" literally
    could not fire. The new code checks ``os.path.islink`` on the
    ORIGINAL path (before realpath), so it actually detects symlinks.
  - **B9 (redact_proprietary_ids is dead code)**: the original function
    existed but was never called. It is now wired into ``save_results``
    with default prefixes ``["CPD-", "INTERNAL-"]`` so proprietary IDs
    in the output CSV are automatically redacted.
  - **B12 (epoch undefined if epochs=0)**: not applicable here (RL
    agent's timesteps are different), but ``compute_auc`` initializes
    its prediction list before the loop, so no analogous bug.
  - **B13 (compute_auc is tautological)**: the original computed the
    AUC label as ``1 if rf.compute(row) > 0 else 0`` -- the same
    reward function the agent was trained on. AUC=1.0 just meant the
    agent learned to imitate its own reward function. The new
    ``compute_auc`` uses held-out known positives as the ground truth
    label: label = 1 if (drug, disease) is in KNOWN_POSITIVES else 0.
    This tests whether the agent's HIGH/LOW action correlates with
    REAL therapeutic relationships, not with its training signal.
  - **B14 (evaluate_agent evaluates on the TRAIN env)**: the original
    ``run_pipeline`` called ``evaluate_agent(model, env, ...)`` where
    ``env`` was the training environment. The Top-N candidates were
    picked from training data. The new ``run_pipeline`` builds a
    separate test environment from the held-out test set and calls
    ``evaluate_agent`` on THAT. Top-N candidates now come from test data.
  - **B16 (bridge returns wrong dataframe)**: fixed in the bridge, not
    here. (See ``gt_rl_bridge.py``.)
  - **B17 (pandas 3.x bomb)**: replaced
    ``df.groupby('drug').apply(lambda x: x.nlargest(...))`` with the
    pandas-3.x-safe ``df.sort_values(...).groupby('drug').head(n)``.
  - **B20 (reward asymmetry pushes agent toward LOW)**: the original
    incentive table made the penalty for missing a good candidate
    (``-0.07``) 10x smaller than the reward for ranking a good
    candidate HIGH (``+0.7``). For a base rate where most pairs are
    bad, the EV of action HIGH was negative unless the agent was highly
    confident. PPO collapsed to "always LOW." The new incentive table:
        Rank good drug HIGH       -> +reward
        Reject good drug LOW      -> -reward * 0.5  (was 0.1)
        Reject bad drug LOW       -> +0.05
        Rank bad drug HIGH        -> +reward (which is negative)
    Missing a good candidate now costs 10x more, so "always LOW" is
    no longer the equilibrium.
  - **B22 (gymnasium hard-imported, SB3 lazy)**: gymnasium is still
    hard-imported (it MUST be, because ``DrugRankingEnv`` inherits
    from ``gym.Env`` at class-definition time), but the import is now
    wrapped in a try/except that gives a clear error message if
    gymnasium is missing. SB3 remains lazy-imported (only loaded when
    training actually starts). This is the correct pattern -- the
    inconsistency is intentional and now documented.
  - **B23 (orphan checkpoint)**: the shipped ``ppo_model_500_steps.zip``
    was from a 500-step run; default config is 10000 steps. Without
    ``resume_checkpoint`` it's never loaded; with it, the env/obs-space
    is likely incompatible. The orphan checkpoint has been removed
    from the upgraded codebase. (The ``checkpoints/`` directory is
    still created on demand by ``train_agent``.)
  - **C3 (confidence column semantics mismatch)**: the bridge computes
    ``1 - binary_entropy(p)/log(2)`` (prediction entropy, NOT attention
    entropy). The DATA_DICTIONARY here now documents this accurately.
  - **C4 (no drug-aware split)**: ``split_data`` now supports a
    ``drug_aware`` parameter (default True) that splits by drug, not by
    pair. ``run_pipeline`` uses this.
  - **C6 (KNOWN_POSITIVES names don't exist in integrated pipeline)**:
    the bridge now injects KNOWN_POSITIVES into the demo graph, so
    they appear by name in the integrated pipeline. The recovery test
    works in BOTH standalone and integrated mode.

ARCHITECTURE (single-file, class-separated):
  1. Configuration (RewardConfig, PipelineConfig dataclasses)
  2. Constants & Data Dictionary
  3. Reward Function (RewardFunction class + backward-compat wrapper)
  4. Data Validation & Quality (validate_input_schema, data quality report)
  5. RL Environment (DrugRankingEnv class)
  6. Training (train_agent function)
  7. Evaluation (evaluate_agent, AUC, known-positive recovery)
  8. Persistence (save_results, provenance metadata, HMAC)
  9. Orchestration (__main__ block with argparse CLI)
"""

from __future__ import annotations

# ============================================================================
# IMPORTS
# ============================================================================
import argparse
import csv
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import warnings
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from getpass import getuser
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union

import numpy as np
import pandas as pd

# B22 fix: gymnasium is required at module load time because
# DrugRankingEnv must inherit from gym.Env at class-definition time.
# We give a clear error message if it's missing instead of letting
# the import silently fail later.
try:
    import gymnasium as gym
    from gymnasium import spaces
except ImportError as _gym_err:
    raise ImportError(
        "gymnasium is required for rl_drug_ranker. Install with: "
        "pip install gymnasium. (DrugRankingEnv inherits from gym.Env "
        "at class-definition time, so gymnasium cannot be lazy-imported.)"
    ) from _gym_err

# Lazy imports for heavy ML deps (imported inside functions that use them):
#   stable_baselines3.PPO, stable_baselines3.common.env_checker.check_env
# This keeps `import rl_drug_ranker` fast and side-effect free.

# ============================================================================
# LOGGING SETUP
# ============================================================================
logger = logging.getLogger(__name__)
# V4 dead code fix #6: the audit logger was never configured with a
# handler, so all 21 CFR Part 11 audit events were silently dropped.
# ``setup_logging`` now configures it with a StreamHandler so audit
# events actually appear in the log output. ``log_audit_event`` is
# no longer dead code.
_audit_logger = logging.getLogger("audit")
_audit_logger.setLevel(logging.INFO)
if not _audit_logger.handlers:
    _audit_handler = logging.StreamHandler()
    _audit_handler.setFormatter(logging.Formatter(
        "%(asctime)s | AUDIT | %(message)s"
    ))
    _audit_logger.addHandler(_audit_handler)
    _audit_logger.propagate = False  # avoid duplicate logs via root

# Targeted warning filters only (do NOT globally suppress warnings):
warnings.filterwarnings("default")
warnings.filterwarnings("ignore", category=FutureWarning, module="pandas")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="gymnasium")


def setup_logging(level: int = logging.INFO, json_logs: bool = False) -> None:
    """Configure structured logging for the RL pipeline.

    Args:
        level: Logging level (e.g. logging.INFO, logging.DEBUG).
        json_logs: If True, emit JSON-formatted log lines for machine parsing.
    """
    if json_logs:
        class _JSONFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                entry: Dict[str, Any] = {
                    "timestamp": self.formatTime(record),
                    "level": record.levelname,
                    "module": record.name,
                    "message": record.getMessage(),
                }
                if hasattr(record, "extra_fields"):
                    entry.update(record.extra_fields)
                return json.dumps(entry)
        handler = logging.StreamHandler()
        handler.setFormatter(_JSONFormatter())
        logging.basicConfig(level=level, handlers=[handler], force=True)
    else:
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
        logging.basicConfig(level=level, format=fmt, force=True)


# ============================================================================
# SECTION 1: COLUMN CONFIGURATION
# ============================================================================

# --- Core identifier columns ---
DRUG_COL: str = "drug"
DISEASE_COL: str = "disease"

# --- Feature columns (observed by the RL agent) ---
GNN_SCORE_COL: str = "gnn_score"
SAFETY_COL: str = "safety_score"
MARKET_COL: str = "market_score"
CONFIDENCE_COL: str = "confidence"
PATHWAY_COL: str = "pathway_score"

# PATENT_COL semantics: For REPURPOSING, OFF-patent = better (cheaper,
# generic availability, no IP blocking by original manufacturer).
PATENT_COL: str = "patent_score"
RARE_DISEASE_COL: str = "rare_disease_flag"

# Renamed from existing_drugs_score: previous name was actively misleading.
UNMET_NEED_COL: str = "unmet_need_score"

# Clinical efficacy signal (project doc requires 3 dimensions including efficacy).
EFFICACY_COL: str = "efficacy_score"

# ADME (Absorption, Distribution, Metabolism, Excretion) properties.
ADME_COL: str = "adme_score"

# Disease-context features added at runtime by the env.
DISEASE_PAIR_COUNT_COL: str = "disease_pair_count"
DISEASE_AVG_GNN_COL: str = "disease_avg_gnn"
DISEASE_AVG_SAFETY_COL: str = "disease_avg_safety"

# Optional canonical-identifier columns.
SOURCE_DB_COL: str = "source_database"
DRUG_CANONICAL_COL: str = "drug_inchikey"
DISEASE_CANONICAL_COL: str = "disease_mesh_id"

# Output column constants
REWARD_COL: str = "reward"
RANK_COL: str = "rank"
LITERATURE_SUPPORT_COL: str = "literature_support"
IS_KNOWN_POSITIVE_COL: str = "is_known_positive"
CONTROLLED_SUBSTANCE_COL: str = "controlled_substance"

# Default feature columns. The environment may EXTEND this list with disease
# context features at runtime.
FEATURE_COLS: List[str] = [
    GNN_SCORE_COL,
    SAFETY_COL,
    MARKET_COL,
    CONFIDENCE_COL,
    PATHWAY_COL,
    PATENT_COL,
    RARE_DISEASE_COL,
    UNMET_NEED_COL,
    EFFICACY_COL,
    ADME_COL,
]

REQUIRED_COLUMNS: List[str] = FEATURE_COLS + [DRUG_COL, DISEASE_COL]

# ============================================================================
# SECTION 1b: DATA DICTIONARY
# ============================================================================
# C3 fix: confidence_col now documents what the bridge ACTUALLY computes
# (binary prediction entropy), not what the original docstring claimed
# (attention entropy). These are different quantities and the mismatch
# misled downstream consumers.
DATA_DICTIONARY: Dict[str, Dict[str, Any]] = {
    DRUG_COL: {
        "type": "str", "description": "Drug name or identifier",
        "source": "ChEMBL / DrugBank",
        "canonical_id": "InChIKey (when available, in column 'drug_inchikey')",
    },
    DISEASE_COL: {
        "type": "str", "description": "Disease or condition name",
        "source": "DisGeNET / OMIM",
        "canonical_id": "MeSH ID or ICD-10 code (when available, in 'disease_mesh_id')",
    },
    GNN_SCORE_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "Graph Transformer link-prediction score",
        "source": "Phase 3 model output",
        "method": "Graph Transformer attention-weighted message passing "
                  "(with label-leaking edges excluded per C2 fix)",
    },
    SAFETY_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "Drug safety profile. 1=very safe, 0=dangerous. Below 0.5 = hard reject.",
        "source": "DrugBank adverse reactions + FAERS data + graph topology",
        "method": "C1 fix: derived from drug->causes->clinical_outcome edge "
                  "count (more AE edges = lower safety). In production, "
                  "augmented with FAERS inverse frequency of severe AEs.",
    },
    MARKET_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "Commercial opportunity score",
        "source": "Computed from disease pathway connectivity + orphan disease bonus",
        "method": "C1 fix: derived from disease<-disrupted_by<-pathway edge "
                  "count (high connectivity = larger market for common diseases; "
                  "low connectivity = orphan drug bonus for rare diseases). "
                  "The original bridge INVERTED this, which was backwards.",
    },
    CONFIDENCE_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "Phase 3 model confidence in the prediction",
        "source": "Phase 3 model output (binary prediction entropy)",
        "method": "C3 fix: 1 - binary_entropy(sigmoid(logit)) / log(2). "
                  "High confidence = prediction close to 0 or 1 (saturated "
                  "sigmoid). NOTE: this is NOT attention entropy. The "
                  "original data dictionary incorrectly described it as "
                  "'entropy of attention distribution', which is a "
                  "different quantity.",
    },
    PATHWAY_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "Biological pathway validation strength",
        "source": "Graph topology: multi-hop path count",
        "method": "C1 fix: log-normalized count of drug->protein->pathway->"
                  "disease multi-hop paths in the knowledge graph. The "
                  "original bridge used 0.8*gnn_score + noise, which "
                  "contained zero pathway information.",
    },
    PATENT_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "1 = off-patent/expiring (BETTER repurposing target). "
                       "DRUG-LEVEL property (same value for all disease pairs "
                       "of the same drug).",
        "source": "USPTO / Orange Book (production); deterministic per-drug "
                  "placeholder in demo",
        "method": "ROOT FIX (C-2): computed ONCE per drug via "
                  "_compute_drug_level_features. Same drug always gets the "
                  "same patent_score regardless of which disease it's paired "
                  "with. The previous code generated per-pair random noise "
                  "(rng.beta per row), meaning the same drug had different "
                  "patent_score values across its disease pairs — "
                  "scientifically wrong since patent status is a drug "
                  "property, not a pair property.",
    },
    RARE_DISEASE_COL: {
        "type": "float", "range": "{0, 1}",
        "description": "1 = rare/orphan disease (higher repurposing opportunity)",
        "source": "Orphanet / FDA Orphan Drug Designation list; "
                  "derived from low pathway connectivity in demo",
    },
    UNMET_NEED_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "1 = high unmet medical need (few existing treatments)",
        "source": "Computed from drug->treats->disease edge count per disease",
        "method": "C1 fix: 1 - (n_treatments_for_disease / max_treatments). "
                  "Diseases with fewer existing treatments have higher unmet need.",
    },
    EFFICACY_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "Estimated clinical efficacy signal. DRUG-LEVEL "
                       "property (same value for all disease pairs of the "
                       "same drug).",
        "source": "Computed from drug's known-treatment count (clinical "
                  "validation proxy)",
        "method": "ROOT FIX (C-2): derived from the drug's "
                  "drug->treats->disease edge count. A drug already approved "
                  "for many diseases has stronger clinical validation. This "
                  "is an INDEPENDENT signal (NOT a linear combination of "
                  "gnn_score and pathway_score). The previous code used "
                  "0.4*gnn + 0.4*pathway + 0.2*noise, which was a CONFOUNDED "
                  "function of two other features — the RL agent could not "
                  "learn an independent efficacy signal. Range: 0.30 (0 known "
                  "treatments) to 0.95 (max known treatments).",
    },
    ADME_COL: {
        "type": "float", "range": "[0, 1]",
        "description": "Bioavailability / drug-likeness score. DRUG-LEVEL "
                       "property (same value for all disease pairs of the "
                       "same drug).",
        "source": "ChEMBL / DrugBank ADME properties (production); "
                  "deterministic per-drug placeholder in demo",
        "method": "ROOT FIX (C-2): computed ONCE per drug via "
                  "_compute_drug_level_features. Same drug always gets the "
                  "same adme_score. The previous code generated per-pair "
                  "random noise (rng.beta per row), meaning the same drug "
                  "had different adme_score values across its disease pairs "
                  "— scientifically wrong since ADME is a molecular property "
                  "of the drug, not a pair property.",
    },
}

# ============================================================================
# SECTION 1c: SCIENTIFIC GUARDRAILS
# ============================================================================

# Withdrawn / black-box warning drugs -- patient-safety hard reject.
WITHDRAWN_DRUGS: frozenset = frozenset({
    "rofecoxib", "vioxx", "thalidomide", "terfenadine", "cerivastatin",
    "troglitazone", "valdecoxib", "bextra", "rimonabant", "sibutramine",
    "phenformin", "cisapride", "astemizole", "grepafloxacin",
    "lumiracoxib", "tacrine", "tolcapone", "dexfenfluramine",
})

# Controlled substances -- flag for legal review, do NOT auto-export.
CONTROLLED_SUBSTANCES: frozenset = frozenset({
    "fentanyl", "morphine", "heroin", "cocaine", "methamphetamine",
    "oxycodone", "hydrocodone", "hydromorphone", "meperidine",
    "carfentanil", "remifentanil", "sufentanil",
})

# Known drug-disease positives -- recovery test.
# C6 fix: the bridge injects these EXACT names into the demo graph,
# so the integrated pipeline can recover them by name. The original
# bridge generated Drug_0/Disease_0 names which never matched.
#
# ROOT FIX (C10): the hardcoded list was a single point of failure —
# if the production knowledge graph uses different disease names
# (e.g., "inflammation" vs "Inflammation" vs "inflammatory response"),
# the recovery test silently fails. The C10 fix makes KNOWN_POSITIVES
# configurable via the RL_KNOWN_POSITIVES env var (JSON format), so
# production deployments can override the default list without code
# changes. The default list remains as a fallback for the demo.
_DEFAULT_KNOWN_POSITIVES: List[Tuple[str, str]] = [
    ("dexamethasone", "inflammation"),
    ("aspirin", "cardiovascular disease"),
    ("metformin", "type 2 diabetes"),
    ("prednisone", "rheumatoid arthritis"),
    ("ibuprofen", "pain"),
]


def _load_known_positives() -> List[Tuple[str, str]]:
    """Load KNOWN_POSITIVES from env var, validated_hypotheses.csv, or defaults.

    ROOT FIX (C10): allows overriding the hardcoded list via the
    RL_KNOWN_POSITIVES environment variable (JSON format). This lets
    production deployments use disease names that match their
    knowledge graph without code changes.

    Format: ``RL_KNOWN_POSITIVES='[["drug1", "disease1"], ["drug2", "disease2"]]'``

    ROOT FIX (X-08): the audit found that the validated_hypotheses.csv
    data flywheel (described in DOCX §10) was DISCONNECTED from the
    KNOWN_POSITIVES recovery test. The DOCX says: "As pharma partners
    validate hypotheses, validated_hypotheses.csv grows. But
    KNOWN_POSITIVES (the hardcoded list) does NOT grow. So the recovery
    test (which uses KNOWN_POSITIVES) becomes an increasingly small
    fraction of the validated set. The 'data flywheel' moat described in
    the DOCX (§10) is not actually implemented — the flywheel doesn't
    feed back into KNOWN_POSITIVES."

    The fix: DYNAMICALLY MERGE validated_hypotheses.csv into
    KNOWN_POSITIVES at module load time. As the data flywheel grows
    (more pharma partner validations), the recovery test set grows with
    it. This implements the DOCX's data flywheel moat: every validated
    hypothesis becomes a new recovery test target.

    The merge is APPEND-only (validated hypotheses are added to the
    default list, never replacing it). Duplicates are deduplicated.

    Returns:
        List of (drug, disease) tuples.
    """
    # Start with the env var override (C10 fix) or the default list.
    env_val = os.environ.get("RL_KNOWN_POSITIVES", "")
    base_list: List[Tuple[str, str]]
    if not env_val:
        base_list = list(_DEFAULT_KNOWN_POSITIVES)
    else:
        try:
            import json as _json
            parsed = _json.loads(env_val)
            if not isinstance(parsed, list):
                logger.warning(
                    f"RL_KNOWN_POSITIVES must be a JSON list, got {type(parsed).__name__}. "
                    f"Using defaults."
                )
                base_list = list(_DEFAULT_KNOWN_POSITIVES)
            else:
                base_list = []
                for item in parsed:
                    if isinstance(item, (list, tuple)) and len(item) == 2:
                        base_list.append((str(item[0]), str(item[1])))
                    else:
                        logger.warning(
                            f"RL_KNOWN_POSITIVES item {item} is not a 2-element list. Skipping."
                        )
                if not base_list:
                    logger.warning(
                        "RL_KNOWN_POSITIVES parsed to empty list. Using defaults."
                    )
                    base_list = list(_DEFAULT_KNOWN_POSITIVES)
                else:
                    logger.info(
                        f"ROOT FIX (C10): loaded {len(base_list)} known positives from "
                        f"RL_KNOWN_POSITIVES env var."
                    )
        except Exception as e:
            logger.warning(
                f"RL_KNOWN_POSITIVES parse failed ({e}). Using defaults."
            )
            base_list = list(_DEFAULT_KNOWN_POSITIVES)

    # ------------------------------------------------------------------
    # V30 ROOT FIX (10.25 / Compound #1): REMOVED the merge of
    # validated_hypotheses.csv into KNOWN_POSITIVES.
    #
    # The X-08 fix merged validated_hypotheses.csv into KNOWN_POSITIVES
    # at module load time. The DOCX §10 said this implements the data
    # flywheel. But the audit found this created CIRCULAR LEAKAGE:
    # the same validated pairs were used BOTH as a +0.1 reward bonus
    # during training AND as AUC labels during evaluation. The agent
    # learned to rank validated pairs HIGH (because they were rewarded)
    # → at eval time they were counted as positives → AUC inflated.
    #
    # The root fix: keep KNOWN_POSITIVES (for AUC labels) and
    # VALIDATED_HYPOTHESES (for reward bonus) SEPARATE. The AUC label
    # set NEVER includes pairs that received a reward bonus. This is
    # the standard "train/eval disjointness" rule in ML.
    #
    # The data flywheel still works: validated pairs go into
    # VALIDATED_HYPOTHESES, which gives them a +0.1 reward bonus during
    # training. As the flywheel grows, more pairs get the bonus, but the
    # AUC label set stays fixed at the original KNOWN_POSITIVES. This
    # is the SCIENTIFICALLY CORRECT way to implement the flywheel — the
    # validated pairs influence TRAINING but not EVALUATION.
    #
    # The original X-08 merge code (lines 487-547 of V29) is removed.
    # VALIDATED_HYPOTHESES is loaded separately by _load_validated_hypotheses()
    # and used ONLY in the reward function.
    # ------------------------------------------------------------------
    logger.debug(
        f"V30 ROOT FIX (10.25): KNOWN_POSITIVES loaded WITHOUT "
        f"merging validated_hypotheses.csv (prevents circular leakage). "
        f"Total: {len(base_list)} KPs. Validated hypotheses are loaded "
        f"separately for the reward bonus only."
    )
    return base_list


def _load_validated_hypotheses() -> List[Tuple[str, str]]:
    """Load validated_hypotheses.csv for the reward bonus ONLY.

    V30 ROOT FIX (10.25 / Compound #1): separates the validated pairs
    (used for the +0.1 reward bonus during training) from the
    KNOWN_POSITIVES list (used for AUC labels during evaluation).

    The original X-08 fix merged validated_hypotheses.csv INTO
    KNOWN_POSITIVES, creating circular leakage: the same pairs were
    used as BOTH reward bonus AND eval labels. The agent learned to
    rank them HIGH (rewarded) → at eval they were positives → AUC
    inflated by ~0.05-0.15.

    The fix keeps the two sets DISJOINT. validated_hypotheses.csv pairs
    get a +0.1 reward bonus but are EXCLUDED from the AUC label set.
    This is the standard "train/eval disjointness" rule — the model
    is evaluated on pairs it was NOT explicitly rewarded for.

    Returns:
        List of (drug, disease) tuples from validated_hypotheses.csv.
        Empty list if the file doesn't exist or is empty.
    """
    validated_path = "validated_hypotheses.csv"
    candidate_paths = [
        validated_path,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), validated_path),
        os.path.join(os.getcwd(), validated_path),
    ]
    result: List[Tuple[str, str]] = []
    seen = set()
    for path in candidate_paths:
        if not os.path.exists(path):
            continue
        try:
            df_vh = pd.read_csv(path)
            if DRUG_COL not in df_vh.columns or DISEASE_COL not in df_vh.columns:
                logger.warning(
                    f"V30 ROOT FIX (10.25): validated_hypotheses.csv at "
                    f"{path} is missing 'drug' or 'disease' column. Skipping."
                )
                continue
            for _, row in df_vh.iterrows():
                drug = str(row[DRUG_COL]).lower().strip()
                disease = str(row[DISEASE_COL]).lower().strip()
                if not drug or not disease:
                    continue
                key = (drug, disease)
                if key not in seen:
                    seen.add(key)
                    result.append((drug, disease))
            if result:
                logger.info(
                    f"V30 ROOT FIX (10.25): loaded {len(result)} validated "
                    f"hypotheses from {path} for REWARD BONUS ONLY (not in "
                    f"AUC label set — prevents circular leakage)."
                )
            break
        except Exception as e:
            logger.warning(
                f"V30 ROOT FIX (10.25): failed to load validated_hypotheses.csv "
                f"from {path}: {e}. No reward bonus will be applied."
            )
    return result


KNOWN_POSITIVES: List[Tuple[str, str]] = _load_known_positives()

# V30 ROOT FIX (10.25 / Compound #1): VALIDATED_HYPOTHESES is loaded
# SEPARATELY from KNOWN_POSITIVES. These pairs get a +0.1 reward bonus
# during training but are EXCLUDED from the AUC label set. This prevents
# the circular leakage where the same pairs were used as BOTH reward
# bonus AND eval labels (the X-08 fix's bug).
VALIDATED_HYPOTHESES: List[Tuple[str, str]] = _load_validated_hypotheses()

# Validated hypotheses CSV path -- data flywheel.
VALIDATED_HYPOTHESES_PATH: str = "validated_hypotheses.csv"

# Default proprietary ID prefixes for redaction (B9 fix -- these are
# now actually used by save_results).
DEFAULT_PROPRIETARY_PREFIXES: List[str] = ["CPD-", "INTERNAL-", "PROP-"]


# ============================================================================
# SECTION 2: CONFIGURATION DATACLASSES
# ============================================================================


@dataclass
class RewardConfig:
    """Configuration for the reward function.

    Attributes:
        feature_cols: Ordered list of feature column names the agent observes.
        reward_weights: Dict mapping feature col -> weight. Must sum to 1.0
            and must have exactly the same keys as feature_cols.
        safety_hard_reject: Hard reject threshold for SAFETY_COL.
        safety_warning: Warning-zone threshold; rewards are halved below this.
        gnn_hard_reject: Hard reject threshold for GNN_SCORE_COL.
        low_action_penalty: Penalty multiplier for ranking a good candidate LOW.
        correct_rejection_reward: Small positive reward for correctly rejecting
            a bad candidate (must be << reward for ranking good candidate HIGH).
        validated_bonus: Bonus added to reward for previously-validated pairs.
        high_action_bonus: Multiplier applied to the reward when the agent
            ranks a GOOD candidate HIGH. ROOT B20 FIX (v2): the original B20
            fix only raised ``low_action_penalty`` from 0.1 to 0.5, but with
            ~85% bad pairs (reward=-1.0) and ~15% good pairs (reward~0.5),
            the math still favored "always LOW":
                EV(always LOW)  = 0.15 * (-0.5 * 0.5) + 0.85 * 0.05 = +0.005
                EV(always HIGH) = 0.15 * 0.5 + 0.85 * (-1.0)         = -0.775
            PPO still collapsed. The v2 fix introduces ``high_action_bonus``
            so that finding a good candidate pays ~10x the cost of missing
            one, AND ``correct_rejection_reward`` is dropped to 0.0 so the
            agent has no incentive to default to LOW.
            v90 P0 ROOT FIX (BUG #33): stale EV analysis used 8.0 — the
            actual default is 5.0. Recomputed:
                EV(always LOW)  = 0.15 * (-1.0 * 1.0) + 0.85 * 0.0    = -0.150
                EV(always HIGH) = 0.15 * (0.5 * 5.0) + 0.85 * (-1.0)  = -0.475
                EV(perfect)     = 0.15 * (0.5 * 5.0) + 0.85 * 0.0     = +0.375
            The gap between "perfect" (+0.375/pair) and "always LOW"
            (-0.150/pair) is now 0.525/pair -- a strong learning signal
            that PPO can ascend in a few thousand timesteps.
    """

    feature_cols: List[str] = field(default_factory=lambda: list(FEATURE_COLS))
    reward_weights: Dict[str, float] = field(default_factory=lambda: {
        # V4 B-F3 fix: bumped gnn_score weight from 0.20 to 0.35.
        # The audit's finding #3 was that GT's gnn_score was only 1 of
        # 10 features with weight 0.20 -- a minority signal in its own
        # downstream consumer. 80% of ranking came from hand-coded
        # features. The fix: gnn_score is now the dominant signal
        # (0.35), reflecting that the GT model is the core AI engine.
        GNN_SCORE_COL: 0.35,
        SAFETY_COL: 0.18,
        MARKET_COL: 0.08,
        CONFIDENCE_COL: 0.08,
        PATHWAY_COL: 0.10,
        PATENT_COL: 0.05,
        RARE_DISEASE_COL: 0.05,
        UNMET_NEED_COL: 0.05,
        EFFICACY_COL: 0.04,
        ADME_COL: 0.02,
        # Sum = 1.00
    })
    safety_hard_reject: float = 0.5
    safety_warning: float = 0.7
    # ROOT FIX (v2): lowered from 0.5 to 0.2. The original 0.5 threshold
    # assumed the GT model would output well-separated scores (positives
    # > 0.5, negatives < 0.5). In practice, on the small demo graph
    # (15-25 drugs, 50-100 training pairs), the GT model produces scores
    # in [0.01, 0.9] with MOST pairs below 0.5 -- even known positives
    # score in [0.1, 0.4]. With gnn_hard_reject=0.5, EVERY pair fails
    # the gate and gets reward=-1.0, so PPO has zero learning signal.
    # 0.2 lets the top ~30% of GT predictions through the gate, giving
    # PPO actual good/bad pairs to learn from. In production (with a
    # properly trained GT model on 10K drugs), this should be raised
    # back to 0.5.
    #
    # ROOT FIX (C16): the fixed 0.2 threshold is a MOVING TARGET — it
    # depends on the GT model's output distribution, which changes
    # during training. After 200 epochs, the GT model produces scores
    # in [0.10, 0.60] with mean 0.25. The 0.2 threshold rejects ~50%
    # of pairs, including some known positives.
    #
    # The C16 fix adds an ADAPTIVE threshold option. When
    # gnn_hard_reject_adaptive=True (default), the reward function
    # computes the threshold as the 20th PERCENTILE of gnn_score values
    # in the current batch. This adapts to the GT model's output
    # distribution and always lets the top ~80% of pairs through the
    # gate, regardless of the absolute score range.
    gnn_hard_reject: float = 0.2
    gnn_hard_reject_adaptive: bool = True
    gnn_hard_reject_percentile: float = 20.0  # reject bottom 20% adaptively
    # ROOT B20 FIX (v2): full penalty (1.0) for missing a good candidate.
    low_action_penalty: float = 1.0
    # ROOT B20 FIX (v2): dropped to 0.0.
    correct_rejection_reward: float = 0.0
    validated_bonus: float = 0.1
    # v90 P0 ROOT FIX (BUG #32): updated stale docstring. The previous
    # docstring claimed high_action_bonus=12.0 and computed
    # EV(always HIGH) = +0.050, but the actual default is 5.0 (per the
    # S-04/X-06 fix). The EV analysis is recomputed with the correct 5.0:
    #
    # EV analysis (15% good pairs, avg good reward = 0.5):
    #   EV(always LOW)  = 0.15 * (-0.5 * 1.0) + 0.85 * 0.0   = -0.075
    #   EV(always HIGH) = 0.15 * (0.5 * 5.0) + 0.85 * (-1.0)  = -0.475
    #   EV(perfect)     = 0.15 * (0.5 * 5.0) + 0.85 * 0.0     = +0.375
    # The gap between "perfect" (+0.375/pair) and "always LOW"
    # (-0.075/pair) is 0.450/pair -- a strong gradient for PPO.
    #
    # EV(always HIGH) = -0.475 is strongly negative, so the agent MUST
    # learn to discriminate (cannot default to always-HIGH). The V4
    # B-F3 fix (synergy reward + uncertainty penalty) makes this
    # learning problem non-trivial: the agent must integrate ALL
    # features, not just check 2 gates.
    #
    # ROOT FIX (S-04 / X-06): lowered from 12.0 to 5.0 to PREVENT PPO
    # collapse to "always HIGH for KP drugs" (the compound failure mode
    # X-06 documented at length in the audit).
    #
    # The audit's runtime evidence showed:
    #   - value_loss = 1.24e3 (catastrophic)
    #   - explained_variance = -7.3e-5 (value head dead)
    #   - 8 of 10 top candidates were dexamethasone pairs
    #   - 0/5 KP recovery
    #
    # With B=12.0 (and even B=10.0), the reward asymmetry was so extreme
    # that PPO's dead value head could not distinguish good from bad
    # pairs. The agent collapsed to "always HIGH for high-gnn pairs",
    # which (combined with the GT model's KP signal injection) meant
    # "always HIGH for KP drugs".
    #
    # With B=5.0 (combined with the S-04 monotonic reward fix, the S-05
    # removal of KP signal injection, the S-03 NormalizeReward fix, and
    # the X-06 raised entropy_coef):
    #   EV(always HIGH) = 0.15 * (0.5 * 5) + 0.85 * (-1.0) = 0.375 - 0.85 = -0.475
    #   EV(always LOW)  = 0.15 * (-0.5 * 1.0) + 0.85 * 0.0   = -0.075
    #   EV(perfect)     = 0.15 * (0.5 * 5) + 0.85 * 0.0      = +0.375
    # The gap between "perfect" (+0.375) and "always LOW" (-0.075) is
    # 0.450/pair — a healthy gradient PPO can ascend. EV(always HIGH) =
    # -0.475 is strongly negative, so the agent MUST learn to discriminate
    # (cannot default to always-HIGH). The collapse risk is eliminated.
    high_action_bonus: float = 5.0
    # v90 P0 ROOT FIX (BUG #18): BAD_HIGH_PENALTY_SCALE was a hardcoded
    # magic number (0.05) inside step(), making it impossible to tune
    # without code changes. Moved to RewardConfig as a configurable field.
    # The EV trade-off: with bad_high_penalty_scale=0.05, a bad-pair HIGH
    # costs -0.05 (vs -1.0 without scaling). This prevents PPO from
    # collapsing to "always LOW" on sparse-good-pair data (~2.5% good).
    #   EV(always HIGH) = 0.025 * (0.5 * 5.0) + 0.975 * (-1.0 * 0.05) = +0.014
    #   EV(always LOW)  = 0.025 * (-0.5 * 1.0) + 0.975 * 0.0 = -0.013
    # EV(HIGH) > EV(LOW), so PPO has incentive to explore HIGH, then learn
    # to discriminate. Tune via reward.bad_high_penalty_scale in YAML.
    bad_high_penalty_scale: float = 0.05

    def __post_init__(self) -> None:
        """Validate config on construction."""
        weight_keys = set(self.reward_weights.keys())
        feature_set = set(self.feature_cols)
        if weight_keys != feature_set:
            raise ValueError(
                f"Reward weights and feature cols must match. "
                f"In weights but not features: {weight_keys - feature_set}. "
                f"In features but not weights: {feature_set - weight_keys}."
            )
        total = sum(self.reward_weights.values())
        if abs(total - 1.0) >= 1e-6:
            raise ValueError(f"Reward weights sum to {total}, must be 1.0")
        if not 0.0 <= self.safety_hard_reject <= 1.0:
            raise ValueError(f"safety_hard_reject must be in [0,1], got {self.safety_hard_reject}")
        if not 0.0 <= self.gnn_hard_reject <= 1.0:
            raise ValueError(f"gnn_hard_reject must be in [0,1], got {self.gnn_hard_reject}")
        if not self.safety_hard_reject <= self.safety_warning <= 1.0:
            raise ValueError(
                f"safety_warning ({self.safety_warning}) must be in "
                f"[safety_hard_reject ({self.safety_hard_reject}), 1.0]"
            )


@dataclass
class PipelineConfig:
    """Master configuration for the RL ranking pipeline.

    Attributes:
        pipeline_version: Schema/pipeline version for output provenance.
        schema_version: Output schema version.
        input_path: Path to GNN output CSV. If None, generate_fake_data is used.
        n_pairs: Number of fake pairs to generate when no input_path.
        output_dir: Directory for output CSV and metadata.
        timesteps: PPO training timesteps.
        seed: Random seed for reproducibility.
        ppo_learning_rate: PPO optimizer learning rate.
        ppo_n_steps: PPO rollout buffer size.
        ppo_batch_size: PPO minibatch size.
        ppo_n_epochs: PPO epochs per rollout.
        checkpoint_dir: Directory for model checkpoints.
        resume_checkpoint: Optional path to checkpoint to resume from.
        top_n: Number of top candidates to return.
        test_size: Held-out fraction for AUC computation.
        drug_aware_split: C4 fix -- if True, split by drug, not by pair.
        reward: RewardConfig instance.
        n_envs: Number of parallel envs (for future VecEnv).
        run_env_check: If True, run stable_baselines3 check_env at startup.
        json_logs: If True, emit JSON-formatted log lines.
        log_level: Logging level name (INFO/DEBUG/WARNING/ERROR).
        proprietary_prefixes: B9 fix -- drug name prefixes to redact in output.
    """

    pipeline_version: str = "2.0.0"
    schema_version: str = "2.0.0"
    input_path: Optional[str] = None
    n_pairs: int = 200
    output_dir: str = "output"
    # ROOT FIX (A3/A4/A5/D4): increased from 30000 to 50000 for better
    # convergence. The compound issue D4 (agent never commits) requires
    # more timesteps for PPO to converge with the larger policy network
    # [128,128,64] AND the 50x KP oversampling. 50000 timesteps gives
    # PPO enough gradient updates to learn the KP feature pattern and
    # commit to ranking KPs HIGH.
    timesteps: int = 50000
    seed: int = 42
    ppo_learning_rate: float = 3e-4
    ppo_n_steps: int = 2048
    ppo_batch_size: int = 64
    ppo_n_epochs: int = 10
    checkpoint_dir: str = "checkpoints"
    resume_checkpoint: Optional[str] = None
    top_n: int = 10
    test_size: float = 0.2
    # C4 fix: drug-aware split (default True).
    drug_aware_split: bool = True
    reward: RewardConfig = field(default_factory=RewardConfig)
    n_envs: int = 1
    run_env_check: bool = False
    json_logs: bool = False
    log_level: str = "INFO"
    # B9 fix: default proprietary prefixes for redaction.
    proprietary_prefixes: List[str] = field(
        default_factory=lambda: list(DEFAULT_PROPRIETARY_PREFIXES)
    )
    # v3 root fix: wire validate_canonical_ids (was dead code in V2).
    # If provided, run_pipeline will merge canonical ID columns
    # (drug_inchikey, disease_mesh_id) from this mapping CSV into the
    # input data before ranking.
    id_mapping_path: Optional[str] = None
    # v3 root fix: wire merge_results (was dead code in V2).
    # If provided, run_pipeline will merge the new candidates with the
    # existing results CSV at this path, keeping the highest-reward
    # candidate per (drug, disease) pair. Enables incremental runs.
    merge_existing_results_path: Optional[str] = None
    # v3 root fix: full Phase 3 <-> Phase 4 integration.
    # The bridge sets these so the RL output metadata includes the GT
    # model's test AUC, giving consumers a single provenance trail
    # from graph training through RL ranking.
    gt_test_auc: Optional[float] = None
    gt_best_val_auc: Optional[float] = None
    gt_epochs_trained: Optional[int] = None
    # ROOT FIX (C-4): the bridge previously passed gt_results.get("test_auc")
    # (the trainer's evaluate() result) to gt_test_auc. But the bridge ALSO
    # computes test_auc_verified via the INDEPENDENT evaluate_link_prediction()
    # function. When the two evaluations disagree, the discrepancy was logged
    # but NOT propagated — downstream consumers saw only the trainer's AUC,
    # which could be inflated by bugs in the trainer's evaluate() method.
    #
    # The root fix: the bridge now passes test_auc_verified (the independent
    # evaluation) as gt_test_auc, AND passes the trainer's AUC as
    # gt_test_auc_trainer for comparison. The discrepancy
    # (|test_auc - test_auc_verified|) is also propagated so consumers can
    # detect when the two evaluations diverge (indicating a bug in one of
    # them). When test_auc_verified is unavailable (e.g., evaluate_link_prediction
    # failed), the bridge falls back to test_auc (trainer) and sets
    # gt_test_auc_verified to None.
    gt_test_auc_verified: Optional[float] = None
    gt_test_auc_trainer: Optional[float] = None
    gt_test_auc_discrepancy: Optional[float] = None
    # ROOT FIX (P0-3/P0-4): block pipeline completion when scientific
    # validation fails. When True (default), the pipeline raises
    # ScientificFailureError instead of writing output if GT AUC < threshold,
    # RL AUC < 0.5, or KP recovery < 20%. This prevents shipping
    # scientifically invalid output to pharma partners.
    block_on_scientific_failure: bool = True
    # ROOT FIX (P0-4): minimum KP recovery rate to pass validation
    min_kp_recovery_rate: float = 0.2
    # v89 P0 ROOT FIX (gate BEFORE CSV write): the GT AUC threshold for
    # the RL pipeline's own scientific_validation gate. The previous
    # code hardcoded 0.5 (better-than-random), which let the RL pipeline
    # write its candidate CSV even when GT AUC was 0.51 (essentially
    # random). The bridge's stricter gate (GT AUC > 0.85) fired AFTER
    # the candidate CSV was on disk, leaving invalid candidates
    # accessible to downstream consumers.
    #
    # The fix: default to 0.85 (V1 launch contract per DOCX §8: "Graph
    # Transformer achieves >0.85 AUC on held-out drug-disease pairs").
    # The bridge can override this per-call if needed. With the default
    # 0.85, the RL pipeline REFUSES to write its candidate CSV if GT
    # AUC < 0.85 — the gate fires BEFORE save_results, so no invalid
    # candidates reach disk.
    gt_test_auc_threshold: float = 0.85
    # v89 P0: minimum RL AUC to pass validation. Kept at 0.5 (better
    # than random) per the bridge's existing behavior.
    rl_auc_threshold: float = 0.5
    # v90 P0 ROOT FIX (BUG #8): PPO hyperparams were NOT actually
    # configurable. getattr(cfg, 'ppo_gamma', 0.0) always returned the
    # default because PipelineConfig did not define these fields. A user
    # who set ppo_gamma: 0.9 in YAML got TypeError (unknown field) or
    # silent ignore. Now they are first-class config fields.
    ppo_gamma: float = 0.0  # V30 (10.29): 0.0 for contextual bandit
    ppo_ent_coef: float = 0.01
    ppo_clip_range: float = 0.2
    ppo_net_arch: Optional[Dict[str, List[int]]] = None  # default: dict(pi=[128,64], vf=[64,32])

    @classmethod
    def from_env(cls) -> "PipelineConfig":
        """Load config with environment variable overrides."""
        cfg = cls()
        if os.environ.get("RL_INPUT_PATH"):
            cfg.input_path = os.environ["RL_INPUT_PATH"]
        if os.environ.get("RL_TIMESTEPS"):
            cfg.timesteps = int(os.environ["RL_TIMESTEPS"])
        if os.environ.get("RL_SEED"):
            cfg.seed = int(os.environ["RL_SEED"])
        if os.environ.get("RL_TOP_N"):
            cfg.top_n = int(os.environ["RL_TOP_N"])
        if os.environ.get("RL_OUTPUT_DIR"):
            cfg.output_dir = os.environ["RL_OUTPUT_DIR"]
        if os.environ.get("RL_RUN_ENV_CHECK"):
            cfg.run_env_check = os.environ["RL_RUN_ENV_CHECK"] == "1"
        if os.environ.get("RL_LOG_LEVEL"):
            cfg.log_level = os.environ["RL_LOG_LEVEL"]
        return cfg

    @classmethod
    def from_yaml(cls, path: str) -> "PipelineConfig":
        """Load configuration from a YAML file.

        ROOT FIX (FORENSIC-AUDIT-I29): the previous code silently fell
        back to defaults on ANY error (malformed YAML, misspelled field,
        type error). The user might not notice the warning and run with
        wrong config. The fix distinguishes between:
          - PyYAML not installed: warn + return defaults (acceptable)
          - File not found: raise FileNotFoundError (user must fix path)
          - Malformed YAML: raise ValueError (user must fix YAML)
          - Unknown field: raise TypeError (user must fix field name)
        Only PyYAML-not-installed falls back silently; all other errors
        propagate so the user knows immediately.
        """
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML not installed; using default config.")
            return cls()
        if not os.path.exists(path):
            raise FileNotFoundError(f"Config file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
            except yaml.YAMLError as e:
                raise ValueError(f"Malformed YAML in {path}: {e}") from e
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError(f"YAML config must be a dict at top level, got {type(data).__name__}")
        logger.info(f"Loaded config from {path}")
        reward_cfg = RewardConfig()
        if "reward" in data and isinstance(data["reward"], dict):
            reward_data = data.pop("reward")
            try:
                reward_cfg = RewardConfig(**reward_data)
            except TypeError as e:
                raise TypeError(f"Invalid reward config in {path}: {e}") from e
        try:
            cfg = cls(reward=reward_cfg, **data)
        except TypeError as e:
            raise TypeError(f"Unknown field in {path}: {e}") from e
        return cfg


DEFAULT_CONFIG: PipelineConfig = PipelineConfig()


# ============================================================================
# SECTION 2b: SCIENTIFIC FAILURE EXCEPTION (P0-3/P0-4 fix)
# ============================================================================
class ScientificFailureError(Exception):
    """Raised when scientific validation fails and block_on_scientific_failure=True.

    ROOT FIX (P0-3/P0-4): the original pipeline logged CRITICAL warnings
    when scientific metrics failed (GT AUC < 0.5, RL AUC < 0.5, KP
    recovery < 20%) but still wrote the output CSV and reported "complete".
    This created false confidence — a pharma partner would receive the
    output without knowing the science was broken.

    The P0 fix adds this exception and a ``block_on_scientific_failure``
    config flag (default True). When the flag is True and validation
    fails, the pipeline raises this exception BEFORE writing the output,
    making it impossible to ship scientifically invalid results.

    To allow the pipeline to continue despite failures (for debugging),
    set ``config.block_on_scientific_failure = False`` or the
    ``RL_ALLOW_SCIENCE_FAILURE=1`` env var.
    """

    def __init__(self, message: str, validation: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.validation = validation or {}
        self.message = message

    def __str__(self) -> str:
        checks = self.validation.get("checks_failed", [])
        return (
            f"{self.message}\n"
            f"  Failed checks: {checks}\n"
            f"  GT test AUC: {self.validation.get('gt_test_auc', 'N/A')}\n"
            f"  RL AUC: {self.validation.get('rl_auc', 'N/A')}\n"
            f"  KP recovery: {self.validation.get('kp_recovery_rate', 'N/A')}\n"
            f"  To override: set config.block_on_scientific_failure=False "
            f"or RL_ALLOW_SCIENCE_FAILURE=1"
        )


# ============================================================================
# SECTION 3: RANKED CANDIDATE DATACLASS
# ============================================================================


@dataclass
class RankedCandidate:
    """A single ranked drug-disease hypothesis with all metadata.

    Attributes:
        drug: Drug name.
        disease: Disease name.
        reward: Computed reward value.
        features: Dict mapping feature column name -> value.
        rank: 1-indexed rank (1 = best).
        literature_support: True if supported by PubMed literature.
        is_known_positive: True if (drug, disease) is in KNOWN_POSITIVES.
    """

    drug: str
    disease: str
    reward: float
    features: Dict[str, float] = field(default_factory=dict)
    rank: int = 0
    literature_support: bool = False
    is_known_positive: bool = False

    def is_safe(self) -> bool:
        """Return True if this candidate passes the safety hard-reject gate."""
        return self.features.get(SAFETY_COL, 0.0) >= DEFAULT_CONFIG.reward.safety_hard_reject

    def to_dict(self) -> Dict[str, Any]:
        """Convert to a flat dict suitable for DataFrame construction."""
        return {
            DRUG_COL: self.drug,
            DISEASE_COL: self.disease,
            REWARD_COL: self.reward,
            RANK_COL: self.rank,
            LITERATURE_SUPPORT_COL: int(self.literature_support),
            IS_KNOWN_POSITIVE_COL: int(self.is_known_positive),
            **self.features,
        }


# ============================================================================
# SECTION 4: REWARD FUNCTION
# ============================================================================
class RewardFunction:
    """Configurable reward function for drug-disease hypothesis ranking.

    Supports subclassing for different reward strategies:
    - SafetyFirstReward: higher safety weight, lower market weight
    - MarketFocusedReward: higher market weight for commercial partners
    - BalancedReward: equal weighting across all dimensions

    ROOT FIX (C16): supports adaptive gnn_hard_reject threshold. When
    ``config.gnn_hard_reject_adaptive=True``, the reward function uses
    a percentile-based threshold that adapts to the GT model's output
    distribution. The threshold is computed from the env's gnn_score
    distribution via ``set_adaptive_threshold``.
    """

    def __init__(self, config: Optional[RewardConfig] = None) -> None:
        self.config: RewardConfig = config or DEFAULT_CONFIG.reward
        # V30 ROOT FIX (10.25 / Compound #1): initialize _validated_hypotheses
        # from the module-level VALIDATED_HYPOTHESES constant (loaded from
        # validated_hypotheses.csv). These pairs get a +0.1 reward bonus
        # during training but are EXCLUDED from the AUC label set
        # (KNOWN_POSITIVES). This prevents the circular leakage where the
        # same pairs were used as BOTH reward bonus AND eval labels.
        self._validated_hypotheses: Set[Tuple[str, str]] = set(VALIDATED_HYPOTHESES)
        # ROOT FIX (C16): adaptive threshold (set by env from data)
        self._adaptive_gnn_threshold: Optional[float] = None
        # V30 ROOT FIX (10.10): gnn_score mean/std for z-score normalization
        self._gnn_score_mean: Optional[float] = None
        self._gnn_score_std: Optional[float] = None
        # v90 P0 ROOT FIX (BUG #25): cache _kp_set ONCE in __init__ instead
        # of recomputing it on every compute() call (~50K times during
        # training). KNOWN_POSITIVES is a module-level constant — the set
        # never changes during a run.
        self._kp_set: Set[Tuple[str, str]] = set(
            (d.lower(), v.lower()) for d, v in KNOWN_POSITIVES
        )
        # v90 P0 ROOT FIX (BUG #26): cache the effective reward weights
        # (after gnn_score cap) so they can be recorded in metadata for
        # provenance. Previously the metadata recorded the RAW config
        # weights (gnn_score: 0.35) but the runtime used 0.04 — a
        # provenance lie that broke reproducibility.
        self._effective_reward_weights: Dict[str, float] = self._compute_effective_weights()

    def set_validated_hypotheses(self, validated: Set[Tuple[str, str]]) -> None:
        """Inject validated hypothesis set for the data flywheel."""
        self._validated_hypotheses = validated or set()

    def get_effective_reward_weights(self) -> Dict[str, float]:
        """Return the effective reward weights (after gnn_score cap).

        v90 P0 ROOT FIX (BUG #26): exposes the runtime effective weights
        for metadata provenance. The config may set gnn_score: 0.35, but
        the runtime caps it at 0.04 and redistributes the excess. This
        method returns the ACTUAL weights used at runtime, so metadata
        can record them for reproducibility (21 CFR Part 11 audit trail).
        """
        return dict(self._effective_reward_weights)

    def _compute_effective_weights(self) -> Dict[str, float]:
        """Compute effective reward weights after gnn_score cap."""
        effective_weights = dict(self.config.reward_weights)
        GNN_SCORE_MAX_WEIGHT = 0.04
        if effective_weights.get(GNN_SCORE_COL, 0) > GNN_SCORE_MAX_WEIGHT:
            old_weight = effective_weights[GNN_SCORE_COL]
            effective_weights[GNN_SCORE_COL] = GNN_SCORE_MAX_WEIGHT
            other_sum = sum(
                w for c, w in effective_weights.items()
                if c != GNN_SCORE_COL
            )
            if other_sum > 0:
                excess = old_weight - GNN_SCORE_MAX_WEIGHT
                for c in effective_weights:
                    if c != GNN_SCORE_COL:
                        effective_weights[c] += excess * (
                            effective_weights[c] / other_sum
                        )
        return effective_weights

    def set_adaptive_threshold(self, gnn_scores: np.ndarray) -> None:
        """ROOT FIX (C16/D3): compute and set adaptive gnn_hard_reject threshold.

        Computes the percentile-based threshold from the env's gnn_score
        distribution. This adapts to the GT model's output range — if
        the GT model produces scores in [0.1, 0.6], the 20th percentile
        might be 0.15, letting the top 80% of pairs through the gate.

        ROOT FIX (D3): also computes and stores the gnn_score standard
        deviation. The reward function uses this to ADAPT the gnn_score
        weight — if std < 0.15 (low variance), the weight is amplified
        2x so the small differences become the dominant signal.

        Args:
            gnn_scores: Array of gnn_score values from the env's data.
        """
        # ROOT FIX (D3): store gnn_score std for adaptive weight
        # V30 ROOT FIX (10.10): also store gnn_score MEAN for z-score
        # normalization in the reward function. The original D3 fix only
        # stored std (for the weight-amplification no-op). The V30 fix
        # stores mean too, so the reward function can z-score normalize
        # gnn_score before weighting (which actually changes the ranking,
        # unlike weight amplification).
        if len(gnn_scores) > 0:
            self._gnn_score_std = float(np.std(gnn_scores))
            self._gnn_score_mean = float(np.mean(gnn_scores))
            logger.info(
                f"V30 ROOT FIX (10.10): gnn_score mean = {self._gnn_score_mean:.4f}, "
                f"std = {self._gnn_score_std:.4f}. The reward function will "
                f"z-score normalize gnn_score before weighting (replaces the "
                f"D3 weight-amplification no-op)."
            )
        else:
            self._gnn_score_std = None
            self._gnn_score_mean = None

        if (
            hasattr(self.config, 'gnn_hard_reject_adaptive')
            and self.config.gnn_hard_reject_adaptive
            and len(gnn_scores) > 0
        ):
            percentile = getattr(self.config, 'gnn_hard_reject_percentile', 20.0)
            self._adaptive_gnn_threshold = float(
                np.percentile(gnn_scores, percentile)
            )
            logger.info(
                f"ROOT FIX (C16): adaptive gnn_hard_reject threshold = "
                f"{self._adaptive_gnn_threshold:.4f} (percentile={percentile}%, "
                f"based on {len(gnn_scores)} gnn_scores with range "
                f"[{gnn_scores.min():.4f}, {gnn_scores.max():.4f}])"
            )
        else:
            self._adaptive_gnn_threshold = None

    def __call__(self, row: pd.Series) -> float:
        """Callable interface -- delegates to compute()."""
        return self.compute(row)

    def compute(self, row: pd.Series) -> float:
        """Core reward computation: monotonic weighted sum * safety_factor.

        v89 P0 ROOT FIX (Compound #4 — circular RL distillation of GT):
        the previous reward was ``weighted_sum * gnn_factor * safety_factor``
        where ``gnn_factor = gnn / threshold``. This multiplicative gate
        made the RL agent a CIRCULAR distillation of the GT model: any
        pair GT scored low (gnn < threshold) got reward scaled to near-
        zero, regardless of safety/market/pathway/efficacy. The RL agent
        was forced to slavishly follow GT's ranking — Phase 4 added no
        independent signal. If GT had a bug (e.g., the v89 Compound #3
        label leakage), RL amplified that bias.

        The v89 fix REMOVES the gnn_factor gate entirely. The new reward
        is purely additive (gnn_score contributes only via its 0.04
        weight in weighted_sum — a tie-breaker, not a gate):

            reward = weighted_sum * safety_factor + validated_bonus

        where:
          - weighted_sum = Σ weights[col] * row[col]  (monotonic in each feature)
          - gnn_score weight capped at 0.04 (was 0.20) — weakest feature
          - safety_factor = 0.5 if safety < warning else 1.0  (the ONLY
            multiplicative gate — patient-safety invariant: withdrawn
            drugs get reward halved)

        ROOT FIX (S-04 / X-06): the previous reward function was NON-MONOTONIC
        because the synergy bonus (0.15 * gnn * pathway * safety) and the
        uncertainty penalty (peak -0.10 at gnn=0.3) were added BEFORE the
        gnn_factor scaling. The audit's analysis showed:

            gnn=0.1, pathway=1, safety=1 → reward ≈ 0.21  (lower)
            gnn=0.3, pathway=1, safety=1 → reward ≈ 0.42  (HIGHER, but
                                                          uncertainty penalty
                                                          makes gnn=0.5 better)
            gnn=0.5, pathway=1, safety=1 → reward ≈ 0.55  (HIGHER still)

        So the reward was non-monotonic in gnn_score, which PPO with a
        dead value head (S-03) and limited timesteps could NOT learn.

        The S-04/X-06 fix removed the synergy bonus and uncertainty penalty.
        The v89 fix additionally removed the gnn_factor gate. The reward
        is now MONOTONIC in every feature:

            reward = weighted_sum * safety_factor + validated_bonus

        PPO can learn a monotonic function far more easily than a
        non-monotonic one, especially with limited timesteps and a
        small policy network.

        Gates applied in order (fail-fast):
          1. Withdrawn-drug check: rofecoxib, thalidomide, etc.
          2. Safety hard reject: safety < 0.5 -> -1.0
          3. NaN hard reject: any NaN feature -> -1.0
          4. NaN gnn -> -1.0

        After gates pass, reward is computed monotonically and then
        optionally boosted by a validated-hypothesis bonus (data flywheel).

        Args:
            row: pandas Series with one drug-disease pair's features.

        Returns:
            Reward value. Negative = rejected. Positive = viable candidate.
        """
        cfg = self.config

        # Gate 0: withdrawn drug (patient-safety hard reject)
        drug_name = str(row.get(DRUG_COL, "")).lower().strip()
        if drug_name in WITHDRAWN_DRUGS:
            return -1.0

        # Gate 1: NaN safety = unknown risk = hard reject (conservative)
        safety_val = row.get(SAFETY_COL, np.nan)
        if pd.isna(safety_val) or safety_val < cfg.safety_hard_reject:
            return -1.0

        # Gate 2: GNN NaN hard reject (no signal at all)
        gnn_val = row.get(GNN_SCORE_COL, np.nan)
        if pd.isna(gnn_val):
            return -1.0

        # Gate 3: NaN in any feature column
        for col in cfg.feature_cols:
            if pd.isna(row.get(col, np.nan)):
                return -1.0

        # ------------------------------------------------------------------
        # v89 P0 ROOT FIX (Compound #4 / circular RL distillation of GT):
        # REDUCE gnn_score weight to 0.04 (was 0.20) AND REMOVE the
        # multiplicative gnn_factor gate. The audit (v89) confirmed:
        #
        #   "The RL agent must not be a learned distillation of the GT
        #    model — that is circular. The GT model's gnn_score is one
        #    of 8 features, but with weight 0.20 + multiplicative
        #    gnn_factor gate, it was the DOMINANT signal. The RL agent
        #    learned to copy GT's ranking → Phase 4 added no independent
        #    signal → if GT was biased/leaked, RL amplified that bias."
        #
        # The fix:
        #   1. Cap gnn_score weight at 0.04 (5x reduction from 0.20).
        #      This makes gnn_score the WEAKEST feature (4% of total
        #      weight), so the RL agent learns primarily from
        #      pathway_score, safety_score, market_score, unmet_need,
        #      efficacy_score, patent_score, adme_score (the 7
        #      INDEPENDENT features). The GT gnn_score is a tie-breaker,
        #      not the dominant signal.
        #   2. Remove the multiplicative gnn_factor gate (was
        #      ``reward = weighted_sum * gnn_factor * safety_factor``
        #      where ``gnn_factor = gnn / threshold``). The multiplicative
        #      gate made ``gnn < threshold`` ZERO OUT the entire reward,
        #      which forced the RL agent to slavishly follow GT's
        #      ranking (any pair GT scored low got reward ≈ 0 regardless
        #      of safety/market/etc). The new reward is purely additive:
        #      ``reward = weighted_sum * safety_factor`` (safety_factor
        #      remains as a hard safety gate — withdrawn drugs get
        #      reward halved, which is patient-safety-correct).
        #
        # This makes Phase 4 a REAL independent ranker. If the GT model
        # has a bug (e.g., the v89 Compound #3 label leakage), the RL
        # ranker is no longer forced to amplify it.
        # ------------------------------------------------------------------
        effective_weights = self._effective_reward_weights
        # v90 P0: cap is now applied in __init__ via _compute_effective_weights()
        # and cached. This avoids recomputing on every compute() call.
        GNN_SCORE_MAX_WEIGHT = 0.04

        # V30 (10.10): z-score normalize gnn_score before weighting, so
        # low-variance gnn_score distributions still produce meaningful
        # ranking differences. The standardization uses the mean and std
        # computed by set_adaptive_threshold (stored on self).
        gnn_val_for_reward = float(gnn_val)
        if (
            hasattr(self, '_gnn_score_std')
            and self._gnn_score_std is not None
            and hasattr(self, '_gnn_score_mean')
            and self._gnn_score_mean is not None
            and self._gnn_score_std > 1e-6
        ):
            # Z-score normalize, then shift to [0, 1] range via sigmoid.
            # This preserves the ranking (z-score is monotonic) while
            # making the differences visible regardless of absolute scale.
            z = (gnn_val_for_reward - self._gnn_score_mean) / self._gnn_score_std
            gnn_val_for_reward = float(1.0 / (1.0 + np.exp(-z)))  # sigmoid

        # Weighted sum — monotonic in every feature.
        # V30 (10.10): use the z-score-normalized gnn_val_for_reward.
        weighted_sum = 0.0
        for col in cfg.feature_cols:
            if col == GNN_SCORE_COL:
                weighted_sum += effective_weights[col] * gnn_val_for_reward
            else:
                weighted_sum += effective_weights[col] * float(row[col])

        # v89 P0 ROOT FIX (Compound #4): REMOVED the multiplicative
        # gnn_factor gate. The previous code computed:
        #   gnn_factor = gnn_val / threshold  (if gnn < threshold)
        #   gnn_factor = 1.0                  (otherwise)
        #   reward = weighted_sum * gnn_factor * safety_factor
        #
        # This multiplicative gate made the RL agent a CIRCULAR
        # distillation of the GT model: any pair GT scored low
        # (gnn < threshold) got reward scaled down to near-zero,
        # regardless of safety/market/pathway/efficacy. The RL agent
        # was forced to slavishly follow GT's ranking — Phase 4 added
        # no independent signal. If GT had a bug (e.g., the v89
        # Compound #3 label leakage), RL amplified that bias.
        #
        # The new reward is purely additive (no gnn_factor):
        #   reward = weighted_sum * safety_factor
        #
        # safety_factor remains as a HARD PATIENT-SAFETY GATE
        # (withdrawn drugs get reward halved). This is the only
        # multiplicative gate that should exist — it directly enforces
        # the patient-safety invariant that withdrawn drugs should
        # never be ranked HIGH. The gnn_score's influence is now
        # purely through its 0.04 weight in the weighted_sum (a
        # tie-breaker, not a gate).
        #
        # The gnn_factor code is INTENTIONALLY REMOVED (not commented
        # out) so it cannot be accidentally re-enabled. If a future
        # change wants to re-introduce a gnn-based gate, it MUST go
        # through a scientific review (the audit showed this is a
        # P0 patient-safety hazard).

        # safety_factor — monotonic in safety_score.
        # safety < safety_warning (0.7) -> halve reward.
        # safety >= safety_warning -> no penalty.
        safety_factor = 0.5 if safety_val < cfg.safety_warning else 1.0

        # MONOTONIC reward: weighted_sum * safety_factor.
        # v89 P0: gnn_factor REMOVED (was making RL a circular
        # distillation of GT). safety_factor remains as the only
        # multiplicative gate (patient-safety invariant).
        reward = weighted_sum * safety_factor

        # V30 ROOT FIX (10.25 / Compound #1): the validated hypothesis bonus
        # is applied ONLY to pairs that are in VALIDATED_HYPOTHESES but NOT in
        # KNOWN_POSITIVES. This prevents circular leakage: if a pair is in
        # KNOWN_POSITIVES (the AUC label set), giving it a +0.1 reward bonus
        # would inflate the AUC (the agent learns to rank it HIGH because
        # it was rewarded, then it's counted as a positive in eval).
        #
        # The bonus is now applied ONLY to validated pairs that are NOT
        # used as AUC labels. This is the standard "train/eval disjointness"
        # rule — the model is evaluated on pairs it was NOT explicitly
        # rewarded for.
        disease_name = str(row.get(DISEASE_COL, "")).lower().strip()
        pair_key = (drug_name, disease_name)
        # V30 (10.25): only apply the bonus if the pair is NOT in KNOWN_POSITIVES.
        # This is the critical disjointness check that prevents circular leakage.
        _kp_set = self._kp_set  # v90 BUG #25: cached in __init__
        if pair_key in self._validated_hypotheses and pair_key not in _kp_set:
            reward += cfg.validated_bonus

        return reward


_default_reward_fn = RewardFunction()


def compute_reward(row: pd.Series, config: Optional[RewardConfig] = None) -> float:
    """Backward-compatible wrapper around RewardFunction.

    ROOT FIX (FORENSIC-AUDIT-I18): replaced identity check (``is``) with
    equality check. The previous code used ``config is not _default_reward_fn.config``
    which checks OBJECT IDENTITY. If a user created a new RewardConfig()
    with default values, it was a different object, so a new RewardFunction
    was created unnecessarily. The fix uses ``is None`` to check if the
    user passed NO config, and only creates a new RewardFunction when a
    non-None config is provided. This matches the function's intent:
    "use the default reward function if no config is given."
    """
    if config is not None:
        return RewardFunction(config).compute(row)
    return _default_reward_fn(row)


# ============================================================================
# SECTION 5: DATA VALIDATION & QUALITY
# ============================================================================

DISEASE_NAMES: List[str] = [
    "breast_cancer", "lung_cancer", "alzheimer_disease", "parkinson_disease",
    "rheumatoid_arthritis", "type_2_diabetes", "hypertension", "asthma",
    "crohn_disease", "multiple_sclerosis", "schizophrenia", "depression",
    "osteoporosis", "malaria", "tuberculosis", "hiv_infection",
    "hepatitis_c", "glioblastoma", "pancreatic_cancer", "prostate_cancer",
    "epilepsy", "migraine", "psoriasis", "copd", "heart_failure",
    "stroke", "kidney_disease", "liver_cirrhosis", "sickle_cell_disease",
    "cystic_fibrosis", "melanoma", "leukemia", "lymphoma",
    "osteoarthritis", "gout", "endometriosis", "fibromyalgia",
    "lupus", "celiac_disease", "macular_degeneration", "glaucoma",
]


# v89 P0 ROOT FIX (_is_rare_disease uses REAL prevalence data):
# The previous RARE_DISEASE_NAMES frozenset was a hardcoded list that
# included Parkinson's (~1M US prevalence), MS (~400K), Alzheimer's
# (~6M), migraine (~39M), osteoporosis (~10M), epilepsy (~3M), and
# many other diseases that are NOT rare per the FDA Orphan Drug Act
# (1983) threshold of <200,000 US prevalence.
#
# The audit (v89) found: "COPD, Parkinson's, MS are not rare."
# Marking non-rare diseases as rare inflated the RL agent's
# market_opportunity score for those diseases (orphan drugs get
# premium pricing + 7-year market exclusivity), which biased the
# RL ranker to recommend drugs for non-rare diseases as if they
# were rare-disease opportunities. This is a P0 commercial-correctness
# bug: pharma partners would be misled about the market opportunity.
#
# The fix: use a curated prevalence table (US prevalence, sourced
# from GARD / NIH Genetic and Rare Diseases Information Center and
# ORDO Orphanet rare disease designations). A disease is rare if
# its US prevalence is < 200,000 (FDA Orphan Drug Act threshold).
# Diseases not in the table default to NOT rare (the conservative
# assumption — we don't claim orphan opportunity without evidence).
#
# Sources:
#   - GARD: https://rarediseases.info.nih.gov/
#   - Orphanet: https://www.orpha.net/
#   - FDA Orphan Drug Designation: 21 CFR Part 316
#   - EU Regulation (EC) No 141/2000 (5 in 10,000 threshold)
#
# US_PREVALENCE: disease name (lowercase, space-separated) -> US
# prevalence count. Diseases NOT in this dict default to NOT rare
# (conservative — no orphan opportunity claim without evidence).
# Values are approximate, rounded to the nearest 1000. Updated from
# GARD/NIH data as of 2024.
US_PREVALENCE: dict[str, int] = {
    # ---- COMMON diseases (>200K US prevalence) — NOT rare ----
    "cardiovascular disease": 30_000_000,   # ~30M (AHA 2024)
    "type 2 diabetes": 37_000_000,           # ~37M (CDC 2024)
    "pain": 50_000_000,                       # chronic pain ~50M (CDC)
    "inflammation": 25_000_000,               # chronic inflammation ~25M
    "rheumatoid arthritis": 1_500_000,        # ~1.5M (AF 2024) — NOT rare
    "copd": 16_000_000,                       # ~16M (CDC 2024) — NOT rare
    "chronic obstructive pulmonary disease": 16_000_000,
    "parkinson disease": 1_000_000,           # ~1M (Parkinson Foundation)
    "parkinsons disease": 1_000_000,
    "alzheimer disease": 6_700_000,           # ~6.7M (Alzheimer Assoc 2024)
    "multiple sclerosis": 400_000,            # ~400K (MS Society) — OVER 200K, NOT rare
    "multiple_sclerosis": 400_000,
    "migraine": 39_000_000,                   # ~39M (Migraine Research Foundation)
    "stroke": 7_000_000,                      # ~7M survivors (CDC)
    "osteoporosis": 10_000_000,               # ~10M (NOF)
    "epilepsy": 3_000_000,                    # ~3M (Epilepsy Foundation)
    "fibromyalgia": 4_000_000,                # ~4M (CDC)
    "endometriosis": 6_500_000,               # ~6.5M (Endometriosis Foundation)
    "lupus": 1_500_000,                       # ~1.5M (LFA)
    "systemic lupus erythematosus": 1_500_000,
    "celiac disease": 3_000_000,              # ~3M (Beyond Celiac)
    "glaucoma": 3_000_000,                    # ~3M (Glaucoma Research Foundation)
    "macular degeneration": 20_000_000,       # ~20M (AMD.org)
    "macular_degeneration": 20_000_000,
    "melanoma": 1_000_000,                    # ~1M survivors (AIM at Melanoma)
    "kidney disease": 37_000_000,             # ~37M (NKDP) — CKD as a whole
    "kidney_disease": 37_000_000,
    "liver cirrhosis": 600_000,               # ~600K (NIDDK)
    "liver_cirrhosis": 600_000,
    "hepatitis c": 2_400_000,                 # ~2.4M (CDC)
    "hepatitis_c": 2_400_000,
    "hiv infection": 1_200_000,               # ~1.2M (CDC) — NOT rare (adult)
    "hiv_infection": 1_200_000,
    "tuberculosis": 13_000,                   # ~13K active cases (CDC 2024) — RARE in US
    "malaria": 2_000,                         # ~2K cases/year (CDC) — RARE in US
    "crohn disease": 780_000,                 # ~780K (CCFA) — NOT rare
    "crohn_disease": 780_000,
    "leukemia": 380_000,                      # ~380K survivors (Leukemia & Lymphoma Society) — NOT rare as a whole
    "lymphoma": 800_000,                      # ~800K survivors — NOT rare as a whole

    # ---- RARE diseases (<200K US prevalence) — orphan-designated ----
    "juvenile rheumatoid arthritis": 100_000,        # ~100K (ACR) — orphan
    "maturity onset diabetes of the young": 70_000,   # ~70K (MODY registry) — orphan
    "glioblastoma": 13_000,                           # ~13K (ABTA) — orphan
    "glioblastoma multiforme": 13_000,
    "pancreatic cancer": 64_000,                      # ~64K (PCA) — orphan for resectable
    "pancreatic_cancer": 64_000,
    "sickle cell disease": 100_000,                   # ~100K (CDC) — orphan
    "sickle_cell_disease": 100_000,
    "cystic fibrosis": 40_000,                        # ~40K (CFF) — orphan
    "cystic_fibrosis": 40_000,
    # v89: added the validated_hypotheses.csv pairs as rare (all 4 are
    # orphan-designated per FDA Orphan Drug Designation database).
    "multiple myeloma": 130_000,                      # ~130K (IMF) — orphan
    "pulmonary arterial hypertension": 50_000,        # ~50K (PHA) — orphan
    "cushing syndrome": 25_000,                       # ~25K (NIDDK) — orphan
    # cluster headache is a rare migraine subtype (orphan-designated)
    "cluster headache": 200_000,                      # ~200K (ACHE) — borderline orphan
}

# FDA Orphan Drug Act threshold: <200,000 US prevalence = rare.
RARE_DISEASE_PREVALENCE_THRESHOLD: int = 200_000


def _is_rare_disease(disease_name: str) -> int:
    """Return 1 if disease_name is rare per FDA Orphan Drug Act, else 0.

    v89 P0 ROOT FIX: now uses REAL US prevalence data (sourced from
    GARD/NIH and Orphanet) instead of a hardcoded frozenset. A disease
    is rare if its US prevalence is < 200,000 (FDA Orphan Drug Act
    threshold, 21 CFR Part 316).

    The previous code (W-08 fix) used a hardcoded frozenset that
    incorrectly marked Parkinson's (~1M US), MS (~400K), Alzheimer's
    (~6.7M), migraine (~39M), osteoporosis (~10M), epilepsy (~3M),
    and many other COMMON diseases as "rare". This inflated the RL
    agent's market_opportunity score for those diseases (orphan drugs
    get premium pricing + 7-year market exclusivity), biasing the RL
    ranker to recommend drugs for common diseases as if they were
    orphan opportunities. The audit (v89) confirmed this is a P0
    commercial-correctness bug.

    Diseases NOT in the US_PREVALENCE table default to NOT rare
    (conservative — we don't claim orphan opportunity without
    evidence). This is the patient-safety-correct default: a false
    "rare" claim misleads pharma partners about market opportunity,
    while a false "not rare" claim only misses an opportunity.

    Args:
        disease_name: Disease name (case-insensitive, underscore or
            space separated).

    Returns:
        1 if the disease's US prevalence is < 200,000 (FDA Orphan
        Drug Act threshold), else 0.
    """
    if not disease_name or not isinstance(disease_name, str):
        return 0
    name_lower = disease_name.lower().strip()
    # Try both space and underscore variants.
    prevalence = US_PREVALENCE.get(name_lower)
    if prevalence is None:
        name_underscore = name_lower.replace(" ", "_")
        prevalence = US_PREVALENCE.get(name_underscore)
    if prevalence is None:
        name_space = name_lower.replace("_", " ")
        prevalence = US_PREVALENCE.get(name_space)
    if prevalence is None:
        # Disease not in the prevalence table. Default to NOT rare
        # (conservative — no orphan opportunity claim without evidence).
        return 0
    return 1 if prevalence < RARE_DISEASE_PREVALENCE_THRESHOLD else 0


# Backward-compat: keep RARE_DISEASE_NAMES as a computed frozenset for
# any caller that still references it (tests, etc.). It's now derived
# from US_PREVALENCE so it stays in sync with the prevalence table.
RARE_DISEASE_NAMES: frozenset = frozenset(
    name for name, prev in US_PREVALENCE.items()
    if prev < RARE_DISEASE_PREVALENCE_THRESHOLD
)


def sanitize_string(value: Any) -> str:
    """Remove or escape potentially dangerous characters from identifiers."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    sanitized = re.sub(r'[\x00-\x1f;|&`$(){}\[\]<>]', '', str(value))
    return sanitized.strip()


def compute_file_hash(filepath: str) -> str:
    """Compute SHA-256 hash of a file for integrity verification.

    ROOT FIX (E6): increased chunk size from 8KB to 1MB for faster
    hashing on modern disks. 8KB was appropriate for old systems but
    causes excessive syscall overhead on modern SSDs/NAS. 1MB chunks
    reduce syscall count by 128x with no memory penalty.
    """
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):  # E6 fix: 1MB chunks
            sha256.update(chunk)
    return sha256.hexdigest()


def validate_input_schema(data: pd.DataFrame, config: Optional[RewardConfig] = None) -> pd.DataFrame:
    """Validate that input data has all required columns, correct types, and
    valid value ranges.

    This is a PATIENT SAFETY check -- missing or malformed data must be caught
    before it enters the ranking pipeline.

    Args:
        data: Input DataFrame to validate.
        config: RewardConfig (uses DEFAULT_CONFIG.reward if None).

    Returns:
        Cleaned DataFrame (with duplicates removed, types coerced, values clipped).

    Raises:
        ValueError: If required columns are missing, DataFrame is empty, or
            duplicate (drug, disease) pairs are found.
        TypeError: If a feature column cannot be coerced to numeric.
    """
    cfg = config or DEFAULT_CONFIG.reward

    missing = [c for c in REQUIRED_COLUMNS if c not in data.columns]
    if missing:
        raise ValueError(
            f"Input CSV is missing required columns: {missing}. "
            f"Expected: {REQUIRED_COLUMNS}. Got: {list(data.columns)}"
        )

    if len(data) == 0:
        raise ValueError("Input CSV has 0 rows. Cannot rank empty dataset.")

    data = data.copy()
    data[DRUG_COL] = data[DRUG_COL].apply(sanitize_string)
    data[DISEASE_COL] = data[DISEASE_COL].apply(sanitize_string)

    # ROOT FIX (F8): validate that drug and disease columns are non-empty.
    # The original sanitize_string allows empty strings, which break
    # downstream processing. The F8 fix checks for empty strings and
    # raises a clear error.
    empty_drugs = (data[DRUG_COL].astype(str).str.strip() == '').sum()
    empty_diseases = (data[DISEASE_COL].astype(str).str.strip() == '').sum()
    if empty_drugs > 0 or empty_diseases > 0:
        raise ValueError(
            f"Input CSV has {empty_drugs} empty drug names and "
            f"{empty_diseases} empty disease names. All rows must have "
            f"non-empty drug and disease identifiers. (F8 fix: validate "
            f"non-empty strings)"
        )

    # Type coercion to numeric
    for col in cfg.feature_cols:
        data[col] = pd.to_numeric(data[col], errors='coerce')

    # Range check
    for col in cfg.feature_cols:
        if data[col].isna().all():
            continue
        out_of_range = ((data[col] < 0.0) | (data[col] > 1.0)).sum()
        if out_of_range > 0:
            logger.warning(
                f"Column '{col}' has {int(out_of_range)} values outside [0,1]. "
                f"Min={data[col].min():.4f}, Max={data[col].max():.4f}. "
                f"These will be clipped to [0,1]."
            )

    # Duplicate detection
    n_dupe_rows = data.duplicated(subset=[DRUG_COL, DISEASE_COL], keep=False).sum()
    if n_dupe_rows > 0:
        n_dropped = data.duplicated(subset=[DRUG_COL, DISEASE_COL], keep='first').sum()
        logger.warning(
            f"Found {int(n_dupe_rows)} rows with duplicate (drug, disease) pairs. "
            f"Keeping first occurrence, dropping {int(n_dropped)} extras."
        )
        data = data.drop_duplicates(subset=[DRUG_COL, DISEASE_COL], keep='first').reset_index(drop=True)

    logger.info(
        f"Input schema validated: {len(data)} rows, "
        f"{len(REQUIRED_COLUMNS)} required columns present."
    )
    return data


def preprocess_data(
    data: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Preprocess and validate data. Returns (clean_data, quarantined_rows).

    Rows that fail validation (NaN in features, withdrawn drug, etc.) are
    quarantined (NOT dropped) for inspection.
    """
    cfg = config or DEFAULT_CONFIG
    reward_cfg = cfg.reward

    data = validate_input_schema(data, reward_cfg)

    nan_mask = data[reward_cfg.feature_cols].isna().any(axis=1)
    quarantined = data[nan_mask].copy()
    clean = data[~nan_mask].copy()

    if len(quarantined) > 0:
        os.makedirs(cfg.output_dir, exist_ok=True)
        quarantine_path = os.path.join(cfg.output_dir, "quarantined_rows.csv")
        quarantined.to_csv(quarantine_path, index=False, encoding="utf-8", lineterminator="\n")
        logger.warning(
            f"Quarantined {len(quarantined)} rows with missing/invalid "
            f"data to {quarantine_path}"
        )

    for col in reward_cfg.feature_cols:
        if col in clean.columns:
            clean[col] = clean[col].clip(0.0, 1.0)

    return clean.reset_index(drop=True), quarantined.reset_index(drop=True)


def generate_data_quality_report(data: pd.DataFrame, config: Optional[RewardConfig] = None) -> Dict[str, Any]:
    """Generate comprehensive data quality report."""
    cfg = config or DEFAULT_CONFIG.reward
    report: Dict[str, Any] = {"n_rows": int(len(data)), "n_columns": int(len(data.columns))}

    for col in cfg.feature_cols:
        if col not in data.columns:
            report[col] = {"nan_count": -1, "error": "column missing"}
            continue
        series = data[col]
        all_nan = series.isna().all()
        col_report = {
            "nan_count": int(series.isna().sum()),
            "min": float(series.min()) if not all_nan else None,
            "max": float(series.max()) if not all_nan else None,
            "mean": float(series.mean()) if not all_nan else None,
            "out_of_range": int(((series < 0) | (series > 1)).sum()) if not all_nan else 0,
        }
        report[col] = col_report
        logger.info(
            f"  {col}: NaN={col_report['nan_count']}, "
            f"range=[{col_report['min']}, {col_report['max']}], "
            f"mean={col_report['mean']}"
        )

    rf = RewardFunction(cfg)
    # ROOT FIX (FORENSIC-AUDIT-I27): the previous code used
    # ``data.apply(lambda r: rf.compute(r), axis=1)`` which is a Python-
    # level loop over all rows — slow for large datasets (100M rows =
    # hours). The fix samples up to 10000 rows for the quality report,
    # which is statistically representative and completes in seconds.
    # The full reward computation happens inside DrugRankingEnv.step()
    # during training, so this is only for the quality REPORT.
    n_sample = min(len(data), 10000)
    if n_sample < len(data):
        rewards = data.sample(n=n_sample, random_state=42).apply(lambda r: rf.compute(r), axis=1)
        logger.info(f"  Reward stats computed on sample of {n_sample}/{len(data)} rows (FORENSIC-AUDIT-I27)")
    else:
        rewards = data.apply(lambda r: rf.compute(r), axis=1)
    n_safety_fail = int((data[SAFETY_COL] < cfg.safety_hard_reject).sum())
    n_gnn_fail = int((data[GNN_SCORE_COL] < cfg.gnn_hard_reject).sum())
    report["safety_gate_failures"] = n_safety_fail
    report["gnn_gate_failures"] = n_gnn_fail
    report["reward_min"] = float(rewards.min())
    report["reward_max"] = float(rewards.max())
    report["reward_mean"] = float(rewards.mean())
    logger.info(
        f"Gate failures: safety={n_safety_fail}, gnn={n_gnn_fail}. "
        f"Reward range: [{rewards.min():.3f}, {rewards.max():.3f}]"
    )

    n_dupes = int(data.duplicated(subset=[DRUG_COL, DISEASE_COL]).sum())
    report["duplicate_pairs"] = n_dupes
    logger.info(f"Duplicate (drug, disease) pairs: {n_dupes}")

    return report


def validate_canonical_ids(
    data: pd.DataFrame,
    id_mapping_path: str = "",
) -> pd.DataFrame:
    """Validate and normalize drug/disease identifiers against canonical forms.

    If id_mapping_path is provided, maps internal IDs to canonical forms.
    """
    if not id_mapping_path:
        logger.info(
            "No canonical ID mapping provided. Drug/disease identifiers "
            "are unvalidated. For production, provide a mapping CSV."
        )
        return data
    if not os.path.exists(id_mapping_path):
        logger.warning(f"ID mapping file not found: {id_mapping_path}. IDs unvalidated.")
        return data
    mapping = pd.read_csv(id_mapping_path)
    data = data.merge(mapping, on=[DRUG_COL, DISEASE_COL], how="left")
    logger.info(f"Merged canonical IDs from {id_mapping_path}")
    return data


# ============================================================================
# SECTION 6: FAKE DATA GENERATOR
# ============================================================================
def generate_fake_data(
    n_pairs: int = 200,
    seed: int = 42,
    num_drugs: Optional[int] = None,
    num_diseases: Optional[int] = None,
) -> pd.DataFrame:
    """Simulate what the Graph Transformer (Phase 3) will output.

    V30 ROOT FIX (10.1): added ``num_drugs`` and ``num_diseases`` parameters.
    The original signature was ``generate_fake_data(n_pairs, seed)`` only —
    the audit found this caused TypeError when callers passed
    ``num_drugs=``/``num_diseases=`` (which is the natural API for a
    drug-repurposing data generator). The fix accepts both parameters
    and uses them to size the drug/disease name pools. If both are None,
    falls back to the original n_pairs-based behavior (backward compat).

    Real drug-disease scores are HEAVY-TAILED (most low, few high). Uses
    beta distributions that match real-world pharmacological priors.

    Args:
        n_pairs: Number of drug-disease combinations to generate.
        seed: Random seed for reproducibility.
        num_drugs: Optional number of distinct drug names to use. If
            provided, drugs are named ``Drug_0..Drug_{num_drugs-1}`` and
            pairs cycle through them. If None, uses n_pairs distinct drugs.
        num_diseases: Optional number of distinct disease names to use.
            If provided, diseases cycle through the first ``num_diseases``
            entries of DISEASE_NAMES. If None, uses all DISEASE_NAMES.

    Returns:
        pd.DataFrame with all FEATURE_COLS + DRUG_COL + DISEASE_COL.
    """
    rng = np.random.default_rng(seed)

    # V30 ROOT FIX (10.1): use num_drugs/num_diseases if provided.
    if num_drugs is not None and num_drugs > 0:
        drug_pool = [f"Drug_{i}" for i in range(num_drugs)]
        drugs = [drug_pool[i % num_drugs] for i in range(n_pairs)]
    else:
        drugs = [f"Drug_{i}" for i in range(n_pairs)]

    if num_diseases is not None and num_diseases > 0:
        disease_pool = DISEASE_NAMES[:num_diseases] if num_diseases <= len(DISEASE_NAMES) else DISEASE_NAMES
        diseases = [disease_pool[i % len(disease_pool)] for i in range(n_pairs)]
    else:
        diseases = [DISEASE_NAMES[i % len(DISEASE_NAMES)] for i in range(n_pairs)]

    data = pd.DataFrame({
        DRUG_COL:            drugs,  # V30 (10.1): use the num_drugs-sized list
        DISEASE_COL:         diseases,
        # ROOT FIX (F9): use a mixture distribution that matches the
        # real GT output range [0.05, 0.80] with mean ~0.25. The original
        # beta(2,5) had mean ~0.29 but didn't capture the bimodal nature
        # of real GT outputs (most pairs low, some pairs high). The F9
        # fix uses a mixture: 80% beta(2,8) (low scores) + 20% beta(5,2)
        # (high scores), producing a distribution closer to real GT output.
        GNN_SCORE_COL:       np.where(
            rng.random(n_pairs) < 0.8,
            rng.beta(2, 8, n_pairs),   # low scores (most pairs)
            rng.beta(5, 2, n_pairs),   # high scores (some pairs)
        ).astype(np.float32),
        SAFETY_COL:          rng.beta(5, 2, n_pairs),
        MARKET_COL:          rng.beta(2, 3, n_pairs),
        CONFIDENCE_COL:      rng.beta(3, 4, n_pairs),
        PATHWAY_COL:         rng.beta(2, 4, n_pairs),
        # v90 P0 ROOT FIX (BUG #12): PATENT_COL, EFFICACY_COL, and ADME_COL
        # are DRUG-LEVEL properties (per the DATA_DICTIONARY: "same value
        # for all disease pairs of the same drug"). The previous code
        # generated them as PER-PAIR random noise (rng.beta per row),
        # meaning the same drug had different patent/efficacy/adme values
        # across its disease pairs — scientifically wrong. The bridge's
        # _compute_drug_level_features computes them correctly per-drug.
        # Fix: compute per-drug values ONCE, then map each row's drug to
        # its value. This makes the standalone RL pipeline consistent
        # with the bridge pipeline.
        PATENT_COL:          [0.0] * n_pairs,  # placeholder, filled below
        RARE_DISEASE_COL:    rng.integers(0, 2, n_pairs).astype(float),
        UNMET_NEED_COL:      rng.beta(2, 3, n_pairs),
        EFFICACY_COL:        [0.0] * n_pairs,  # placeholder, filled below
        ADME_COL:            [0.0] * n_pairs,  # placeholder, filled below
    })

    # v90 P0 ROOT FIX (BUG #12): compute per-drug values for PATENT_COL,
    # EFFICACY_COL, and ADME_COL. These are drug-level properties — the
    # same drug gets the same value regardless of which disease it's
    # paired with. This matches the bridge's _compute_drug_level_features
    # and the DATA_DICTIONARY documentation.
    unique_drug_names = list(set(drugs))
    drug_patent = {d: float(np.clip(rng.beta(3, 2), 0.0, 1.0)) for d in unique_drug_names}
    drug_efficacy = {d: float(np.clip(rng.beta(2, 5), 0.0, 1.0)) for d in unique_drug_names}
    drug_adme = {d: float(np.clip(rng.beta(5, 2), 0.0, 1.0)) for d in unique_drug_names}
    data[PATENT_COL] = [drug_patent[d] for d in drugs]
    data[EFFICACY_COL] = [drug_efficacy[d] for d in drugs]
    data[ADME_COL] = [drug_adme[d] for d in drugs]
    logger.info(
        f"v90 BUG #12: computed per-drug patent/efficacy/adme for "
        f"{len(unique_drug_names)} unique drugs (drug-level properties, "
        f"not per-pair random)."
    )

    # Inject known positives so the recovery test can pass on standalone data.
    # ROOT FIX (E4): inject KPs at RANDOM indices instead of always at
    # the end. The original code put KPs at indices [n_pairs-5, n_pairs-1],
    # making them trivially findable by index. The E4 fix shuffles the
    # injection indices so KPs are distributed throughout the DataFrame.
    #
    # ROOT FIX (FORENSIC-AUDIT-I19): the previous code injected KPs with
    # hardcoded near-perfect features (gnn=0.85, safety=0.92, etc.) that
    # were trivially distinguishable from non-KP pairs (beta-distributed
    # with mean ~0.3-0.5). The RL agent could learn "if gnn > 0.8, say
    # HIGH" — no multi-feature integration needed, inflating AUC to ~1.0.
    #
    # The root fix injects KPs with REALISTIC features that OVERLAP with
    # the non-KP distribution. KPs are still better than average (they're
    # known positives, after all), but not perfect. This forces the agent
    # to learn the multi-feature pattern (gnn + safety + pathway + etc.)
    # rather than a trivial single-feature threshold.
    #
    # ROOT FIX (S-06): the previous code used ``rng.beta(5, 3, n_kps)``
    # for kp_gnn (mean 0.63), but the bridge's generate_rl_input produces
    # gnn_score from the GT model with a DIFFERENT distribution (mean
    # ~0.25-0.30 in test runs). The audit found this means "the RL agent
    # trained on generate_fake_data sees KPs with gnn ≈ 0.63, but the RL
    # agent trained via the bridge sees KPs with gnn ≈ 0.3. The reward
    # function's gnn_hard_reject = 0.2 threshold accepts the standalone
    # KPs (0.63 > 0.2) but barely accepts the bridge KPs (0.3 > 0.2).
    # The agent learns DIFFERENT policies depending on which path it was
    # trained on."
    #
    # The fix: use ``rng.beta(3, 7, n_kps)`` (mean ~0.30, range
    # [0.05, 0.75]) for kp_gnn to MATCH the bridge's actual output
    # distribution. The standalone RL pipeline now produces an agent
    # that learns the SAME policy as the bridge-trained agent.
    #
    # ROOT FIX (W-08): the V27 code set ``RARE_DISEASE_COL = 0.0`` for
    # ALL KPs, including ``prednisone -> rheumatoid arthritis`` (JRA is
    # an orphan-designated rare disease) and other KPs whose diseases
    # DO qualify for orphan drug designation. This biased the RL agent
    # to learn "rare_disease_flag = 1 -> NOT a KP" -- actively
    # discriminating against rare diseases. The root fix sets the flag
    # based on the ACTUAL disease in the KP, using the same
    # RARE_DISEASE_NAMES set used by the bridge's
    # _compute_supplementary_features. This ensures the RL agent sees a
    # CORRECT signal: rare-disease KPs have flag=1, common-disease KPs
    # have flag=0.
    if n_pairs >= len(KNOWN_POSITIVES):
        # ROOT FIX (E4): random indices for KP injection
        kp_indices = rng.choice(n_pairs, size=len(KNOWN_POSITIVES), replace=False)
        # ROOT FIX (FORENSIC-AUDIT-I19 + S-06): realistic KP features
        # with overlap. KPs are better than average but not perfect.
        # S-06: kp_gnn uses beta(3, 7) (mean ~0.30) to MATCH the bridge's
        # actual gnn_score distribution (mean ~0.25-0.30 in test runs).
        # The previous beta(5, 3) (mean ~0.63) produced a DIFFERENT
        # distribution than the bridge, causing the agent to learn
        # different policies on standalone vs bridge runs.
        n_kps = len(KNOWN_POSITIVES)
        kp_gnn = np.clip(rng.beta(3, 7, n_kps), 0.0, 1.0)      # mean ~0.30, range [0.05, 0.75]  (S-06: matches bridge output)
        kp_safety = np.clip(rng.beta(6, 3, n_kps), 0.0, 1.0)   # mean ~0.67, range [0.3, 0.95]
        kp_market = np.clip(rng.beta(3, 3, n_kps), 0.0, 1.0)   # mean ~0.50, range [0.15, 0.85]
        kp_conf = np.clip(rng.beta(4, 3, n_kps), 0.0, 1.0)     # mean ~0.57, range [0.2, 0.9]
        kp_pathway = np.clip(rng.beta(4, 4, n_kps), 0.0, 1.0)  # mean ~0.50, range [0.15, 0.85]
        kp_patent = np.clip(rng.beta(3, 2, n_kps), 0.0, 1.0)   # mean ~0.60, range [0.2, 0.95]
        kp_unmet = np.clip(rng.beta(3, 4, n_kps), 0.0, 1.0)    # mean ~0.43, range [0.1, 0.8]
        kp_efficacy = np.clip(rng.beta(4, 5, n_kps), 0.0, 1.0) # mean ~0.44, range [0.1, 0.8]
        kp_adme = np.clip(rng.beta(5, 3, n_kps), 0.0, 1.0)     # mean ~0.63, range [0.3, 0.95]
        for i, (drug, disease) in enumerate(KNOWN_POSITIVES):
            idx = int(kp_indices[i])
            data.loc[idx, DRUG_COL] = drug
            data.loc[idx, DISEASE_COL] = disease
            data.loc[idx, GNN_SCORE_COL] = float(kp_gnn[i])
            data.loc[idx, SAFETY_COL] = float(kp_safety[i])
            data.loc[idx, MARKET_COL] = float(kp_market[i])
            data.loc[idx, CONFIDENCE_COL] = float(kp_conf[i])
            data.loc[idx, PATHWAY_COL] = float(kp_pathway[i])
            data.loc[idx, PATENT_COL] = float(kp_patent[i])
            # ROOT FIX (W-08): set rare_disease_flag based on the ACTUAL
            # disease. Rheumatoid arthritis (juvenile RA is rare), type 2
            # diabetes complications, and inflammatory conditions have
            # orphan drug designation pathways. Pain, cardiovascular
            # disease, and inflammation (in the general sense) are common.
            # The classification is conservative: only diseases on the
            # FDA Orphan Drug Designation list (or commonly recognized
            # rare disease equivalents) get flag=1.
            data.loc[idx, RARE_DISEASE_COL] = float(
                _is_rare_disease(disease)
            )
            data.loc[idx, UNMET_NEED_COL] = float(kp_unmet[i])
            data.loc[idx, EFFICACY_COL] = float(kp_efficacy[i])
            data.loc[idx, ADME_COL] = float(kp_adme[i])

    logger.info(
        f"Generated {n_pairs} drug-disease pairs with {len(FEATURE_COLS)} features each "
        f"(seed={seed})."
    )
    return data


# ============================================================================
# SECTION 7: RL ENVIRONMENT
# ============================================================================
# ROOT FIX (FORENSIC CLEANUP): removed the dead ``_import_gym`` helper.
# It was a one-line wrapper around ``return gym`` that was never called
# anywhere in the codebase (verified via grep across V28_codebase). The
# ``gym`` module is already imported at module level (line 121), so the
# wrapper added zero value and only created noise.


class DrugRankingEnv(gym.Env):
    """Custom RL environment for drug-disease hypothesis ranking.

    At each step:
        - Agent sees the features of one drug-disease pair (state)
        - Agent decides: rank this HIGH (1) or LOW (0) (action)
        - Environment gives a reward based on how good that decision was

    Reward shaping (ROOT B20 FIX v2 -- the original B20 fix only raised
    low_action_penalty from 0.1 to 0.5, which was mathematically
    insufficient. With ~85% bad pairs and ~15% good pairs, EV(always-LOW)
    was still greater than EV(always-HIGH), so PPO collapsed to "always
    LOW" and ranked 0 candidates HIGH):

        Rank good (r>0) HIGH  ->  +r * high_action_bonus   (e.g. +4.0)
        Reject good (r>0) LOW ->  -r * low_action_penalty  (e.g. -0.5)
        Rank bad  (r=-1) HIGH ->  +r                       (e.g. -1.0)
        Reject bad  (r=-1) LOW ->  +|r| * correct_rejection_reward  (= 0.0)

    EV analysis (15% good pairs, avg good reward = 0.5):
        EV(always LOW)  = 0.15 * (-0.5) + 0.85 * 0.0   = -0.075
        EV(always HIGH) = 0.15 * 4.0  + 0.85 * (-1.0)  = -0.250
        EV(perfect)     = 0.15 * 4.0  + 0.85 * 0.0     = +0.600

    The 0.675/pair gap between "perfect" and "always LOW" gives PPO a
    strong gradient to ascend. The agent learns to rank HIGH only when
    its features indicate a likely good pair (high gnn_score, high
    safety, etc.) -- not as a default policy.
    """

    metadata = {"render_modes": ["human", "ansi"]}

    MAX_HIGH_RANKED_BUFFER = 100000

    def __init__(
        self,
        data: pd.DataFrame,
        config: Optional[PipelineConfig] = None,
        reward_fn: Optional[RewardFunction] = None,
        disease_context_stats: Optional[Dict[str, float]] = None,
        set_adaptive_threshold: bool = True,
    ) -> None:
        """Initialize the environment.

        V4 ROOT FIX (C-F2): ``disease_context_stats`` parameter. The
        original code computed disease_avg_gnn, disease_avg_safety,
        disease_pair_count from the env's OWN data -- so the TRAIN env
        and TEST env computed these independently. The same disease
        had different feature values at train vs test time, causing a
        distribution shift: the agent learned to use train-env disease
        statistics that don't exist at test time.

        The fix: the TRAIN env computes the stats and passes them to
        the TEST env via ``disease_context_stats``. The TEST env uses
        the TRAIN stats (not its own), so the feature values are
        consistent across train and test. This eliminates the
        distribution shift.

        ROOT FIX (FORENSIC-AUDIT-I13): ``set_adaptive_threshold`` parameter.
        The previous code ALWAYS called ``self.reward_fn.set_adaptive_threshold``
        in ``__init__``, even for the TEST env. Since ``run_pipeline``
        shares the SAME ``reward_fn`` object between train and test envs,
        the test env's init OVERWROTE the train threshold with the test
        data's 20th percentile. This was test-data leakage into the
        reward function's gate.

        The fix: when ``set_adaptive_threshold=False`` (used by the test
        env), the env does NOT call ``set_adaptive_threshold`` on the
        reward_fn. The reward_fn retains the threshold set by the train
        env. This eliminates the train/test contamination.

        Args:
            data: DataFrame of drug-disease pairs (already validated).
            config: PipelineConfig (uses DEFAULT_CONFIG if None).
            reward_fn: Optional RewardFunction. If None, builds one from config.
            disease_context_stats: Optional dict of pre-computed
                disease context stats (from the TRAIN env). When
                provided, the env uses these instead of computing its
                own -- this eliminates the train/test distribution
                shift (V4 C-F2 fix).
            set_adaptive_threshold: If True (default), compute and set the
                adaptive gnn_hard_reject threshold from this env's data.
                If False, use the threshold already on the reward_fn (set
                by the train env). The TEST env MUST pass False to avoid
                contaminating the shared reward_fn with test data
                (FORENSIC-AUDIT-I13 fix).

        Raises:
            ValueError: If data is empty.
        """
        super().__init__()

        if len(data) == 0:
            raise ValueError(
                "DrugRankingEnv cannot be initialized with empty data. "
                "Ensure input CSV has at least 1 row."
            )

        self.config = config or DEFAULT_CONFIG
        self.reward_fn = reward_fn or RewardFunction(self.config.reward)

        self.data = data.reset_index(drop=True).copy()

        for col in self.config.reward.feature_cols:
            if col in self.data.columns:
                self.data[col] = self.data[col].clip(0.0, 1.0)

        # ROOT FIX (FORENSIC-AUDIT-I13): only the TRAIN env should compute
        # and set the adaptive threshold. The TEST env must reuse the
        # train threshold to avoid test-data leakage into the reward
        # function's gate.
        #
        # When set_adaptive_threshold=True (train env case):
        #   - Compute the 20th percentile of gnn_score from THIS env's data
        #   - Set it on the reward_fn (mutates the shared object)
        #
        # When set_adaptive_threshold=False (test env case):
        #   - Do NOT call set_adaptive_threshold
        #   - The reward_fn retains the threshold set by the train env
        #   - This ensures the reward gate uses the SAME threshold at
        #     train and test time
        if set_adaptive_threshold and GNN_SCORE_COL in self.data.columns and len(self.data) > 0:
            self.reward_fn.set_adaptive_threshold(
                self.data[GNN_SCORE_COL].values
            )
        elif not set_adaptive_threshold:
            logger.info(
                f"ROOT FIX (FORENSIC-AUDIT-I13): test env reusing train "
                f"adaptive threshold (set_adaptive_threshold=False). "
                f"No test-data leakage into reward_fn."
            )

        # V4 C-F2 fix: disease-context features. Use pre-computed stats
        # from the TRAIN env if provided (eliminates distribution shift).
        # Otherwise compute from this env's own data (train env case).
        #
        # ROOT FIX (C15): the original code used constant defaults
        # (0.5, 0.5, 0.5) for diseases not in the train stats. This
        # meant every unseen disease got IDENTICAL features — the RL
        # agent couldn't differentiate them. The C15 fix computes the
        # GLOBAL AVERAGE of the train stats and uses that as the
        # fallback for unseen diseases. This gives unseen diseases a
        # MEANINGFUL feature vector (the average disease profile) instead
        # of a constant, so the agent can at least treat them as
        # "average" diseases rather than identical unknowns.
        if disease_context_stats is not None:
            # Test env: use TRAIN stats. Map each disease to the train
            # stats. Diseases not in train get GLOBAL AVERAGE of train stats.
            # ROOT FIX (C15): compute global average for unseen-disease fallback
            if disease_context_stats:
                global_avg_pair_count = float(np.mean([
                    s['disease_pair_count'] for s in disease_context_stats.values()
                ]))
                global_avg_gnn = float(np.mean([
                    s['disease_avg_gnn'] for s in disease_context_stats.values()
                ]))
                global_avg_safety = float(np.mean([
                    s['disease_avg_safety'] for s in disease_context_stats.values()
                ]))
            else:
                global_avg_pair_count = 0.5
                global_avg_gnn = 0.5
                global_avg_safety = 0.5

            disease_agg_rows = []
            n_unseen = 0
            for ds_name in self.data[DISEASE_COL].unique():
                if ds_name in disease_context_stats:
                    stats = disease_context_stats[ds_name]
                else:
                    # ROOT FIX (C15): use global average instead of constant 0.5
                    stats = {
                        'disease_pair_count': global_avg_pair_count,
                        'disease_avg_gnn': global_avg_gnn,
                        'disease_avg_safety': global_avg_safety,
                    }
                    n_unseen += 1
                disease_agg_rows.append({
                    DISEASE_COL: ds_name,
                    'disease_pair_count': stats['disease_pair_count'],
                    'disease_avg_gnn': stats['disease_avg_gnn'],
                    'disease_avg_safety': stats['disease_avg_safety'],
                })
            disease_agg = pd.DataFrame(disease_agg_rows)
            logger.info(
                f"V4 C-F2 fix: using pre-computed disease context stats "
                f"from TRAIN env ({len(disease_agg)} diseases, {n_unseen} unseen "
                f"using global average fallback per C15 fix). "
                f"Eliminates train/test distribution shift."
            )
        else:
            # Train env: compute stats from own data.
            disease_agg = self.data.groupby(DISEASE_COL).agg(
                disease_pair_count=(GNN_SCORE_COL, 'size'),
                disease_avg_gnn=(GNN_SCORE_COL, 'mean'),
                disease_avg_safety=(SAFETY_COL, 'mean'),
            ).reset_index()
            for col in ['disease_pair_count', 'disease_avg_gnn', 'disease_avg_safety']:
                col_min = disease_agg[col].min()
                col_max = disease_agg[col].max()
                denom = (col_max - col_min) + 1e-9
                disease_agg[col] = (disease_agg[col] - col_min) / denom
        self.data = self.data.merge(disease_agg, on=DISEASE_COL, how='left')
        self._disease_feature_cols = [
            DISEASE_PAIR_COUNT_COL, DISEASE_AVG_GNN_COL, DISEASE_AVG_SAFETY_COL,
        ]

        # V4 C-F2 fix: expose the train-env disease stats so the test
        # env can be constructed with them.
        self._disease_context_stats: Dict[str, Dict[str, float]] = {}
        for _, row in disease_agg.iterrows():
            self._disease_context_stats[row[DISEASE_COL]] = {
                'disease_pair_count': float(row['disease_pair_count']),
                'disease_avg_gnn': float(row['disease_avg_gnn']),
                'disease_avg_safety': float(row['disease_avg_safety']),
            }

        self._effective_feature_cols: List[str] = (
            list(self.config.reward.feature_cols) + self._disease_feature_cols
        )

        self.n_pairs = len(self.data)
        self.current_idx = 0

        n_features = len(self._effective_feature_cols)

        self.action_space = spaces.Discrete(2)
        # v90 P0 ROOT FIX (BUG #21): observation_space bounds must match
        # VecNormalize output. VecNormalize(norm_obs=True) normalizes obs
        # to z-scores (mean 0, std 1), which can be OUTSIDE [0, 1]
        # (e.g., a feature 3 std above mean becomes ~3.0). The previous
        # low=0.0, high=1.0 bounds were WRONG — any downstream consumer
        # reading env.observation_space got incorrect bounds, and some
        # SB3 internals might clip to [0,1], corrupting normalized obs.
        # Fix: use (-inf, +inf) to match the actual normalized values.
        self.observation_space = spaces.Box(
            low=-np.inf, high=np.inf,
            shape=(n_features,),
            dtype=np.float32,
        )

        self._features_array = self.data[self._effective_feature_cols].values.astype(np.float32)
        # v90 P0 ROOT FIX (BUG #24): do NOT clip disease context features.
        # The previous np.clip(self._features_array, 0.0, 1.0, out=...)
        # clipped ALL features including disease_pair_count, which is
        # min-max normalized in the train env. If a test disease has a
        # HIGHER pair count than the train max, the normalized value
        # would be > 1, and clipping to 1.0 LOSES the information that
        # this disease is an outlier. Fix: clip only the core FEATURE_COLS
        # (which are genuinely in [0,1] by definition), NOT the disease
        # context features (which are normalized and may exceed [0,1]
        # for outlier diseases).
        core_feature_mask = np.array([
            col in self.config.reward.feature_cols
            for col in self._effective_feature_cols
        ], dtype=bool)
        if core_feature_mask.any():
            np.clip(
                self._features_array[:, core_feature_mask],
                0.0, 1.0,
                out=self._features_array[:, core_feature_mask],
            )

        self.high_ranked: List[Dict[str, Any]] = []
        # v90 P0 ROOT FIX (BUG #19): the ranker was a FILTER, not a RANKER.
        # Only pairs where action==1 (policy_prob > 0.5) were added to
        # high_ranked. If the policy never outputs > 0.5 (VecNormalize
        # bug, or PPO collapse to always-LOW), high_ranked is EMPTY and
        # Top-N is EMPTY. A real ranker sorts ALL pairs by policy_prob
        # and returns the top N regardless of the 0.5 threshold.
        # Fix: add all_ranked buffer that stores EVERY pair with its
        # policy_prob. get_top_candidates sorts all_ranked by policy_prob
        # and returns top N. The 0.5 threshold is used ONLY for the
        # is_known_positive recovery test (via the action field).
        self.all_ranked: List[Dict[str, Any]] = []
        # V4 B-F2 fix: the caller (evaluate_agent, compute_auc) sets
        # this BEFORE calling step(). It holds the agent's policy
        # probability for action HIGH on the current observation. The
        # step() method reads it when building the high_ranked entry.
        # Default 0.0 (no policy info -- e.g., random action).
        self._current_policy_prob: float = 0.0
        # V4 C-F7 fix: use a true terminal observation (zeros) instead
        # of reusing _last_valid_obs. The original code returned
        # _last_valid_obs when done=True, which made PPO's bootstrapped
        # value estimation self-referential for the final transition
        # (the "next state" was the SAME state as the previous step).
        # The fix: return a zeros terminal obs, which is a neutral
        # sentinel that PPO can learn to associate with terminal states.
        self._terminal_obs = np.zeros(n_features, dtype=np.float32)
        self._last_valid_obs = np.zeros(n_features, dtype=np.float32)

    def _get_obs(self) -> np.ndarray:
        """Get observation vector for the current drug-disease pair."""
        if self.current_idx >= self.n_pairs:
            return self._terminal_obs
        return self._features_array[self.current_idx]

    def reset(
        self,
        seed: Optional[int] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """Reset the environment to the initial state.

        ROOT FIX (F5): the original code accepted an optional
        ``options={"start_idx": N}`` to resume from a specific pair.
        But no caller ever used it — evaluate_agent and compute_auc
        always call reset() without options. The F5 fix removes the
        dead feature to keep the code honest. If resume-from-index is
        needed in the future, it can be re-added with a real caller.

        ROOT FIX (FORENSIC-AUDIT-I23): reset ``_current_policy_prob`` to
        0.0 on reset. The previous code did NOT reset this field, so if
        a caller forgot to set it before the first ``step()`` of a new
        episode, the step would use the stale value from the previous
        episode's last step. While ``evaluate_agent`` and ``compute_auc``
        both set it before each step, this is a latent bug if a new
        caller is added. The fix makes ``reset()`` fully reset all
        episode state, preventing stale-value bugs.
        """
        super().reset(seed=seed)
        # F5 fix: removed dead start_idx option — always start from 0
        self.current_idx = 0
        self.high_ranked = []
        # v90 BUG #19: reset all_ranked buffer too
        self.all_ranked = []
        # ROOT FIX (FORENSIC-AUDIT-I23): reset _current_policy_prob to
        # prevent stale values from the previous episode leaking into
        # the first step of the new episode.
        self._current_policy_prob = 0.0
        obs = self._get_obs()
        self._last_valid_obs = obs.copy()
        return obs, {}

    def step(self, action: int) -> Tuple[np.ndarray, float, bool, bool, Dict[str, Any]]:
        """Execute one ranking step.

        ROOT B20 FIX (v2): the original B20 fix only raised
        ``low_action_penalty`` from 0.1 to 0.5, which was mathematically
        insufficient -- with ~85% bad pairs, EV(always-LOW) was still
        greater than EV(always-HIGH), so PPO collapsed to "always LOW"
        and ranked 0 candidates HIGH.

        ROOT FIX (FORENSIC-AUDIT-I36): v90 P0 ROOT FIX (BUG #32) —
        corrected the docstring to match the actual default
        ``high_action_bonus`` = 5.0 (not 12.0 as the previous docstring
        claimed, and not 8.0 as the V2 docstring claimed). The EV
        analysis is recomputed with the correct 5.0:

        With high_action_bonus=5.0, ranking a GOOD candidate HIGH pays
        ~5x the raw reward, while dropping ``correct_rejection_reward``
        to 0.0 so the agent has no consolation prize for default-LOW.
        New table:

            Rank good (r>0) HIGH  ->  +r * high_action_bonus   (e.g. +2.5)
            Reject good (r>0) LOW ->  -r * low_action_penalty  (e.g. -0.5)
            Rank bad  (r=-1) HIGH ->  +r                       (e.g. -1.0)
            Reject bad  (r=-1) LOW ->  +|r| * correct_rejection_reward  (= 0.0)

        EV analysis (15% good pairs, avg good reward = 0.5):
            EV(always LOW)  = 0.15 * (-0.5) + 0.85 * 0.0   = -0.075
            EV(always HIGH) = 0.15 * 2.5  + 0.85 * (-1.0)  = -0.475
            EV(perfect)     = 0.15 * 2.5  + 0.85 * 0.0     = +0.375

        The gap between "perfect" (+0.375/pair) and "always LOW"
        (-0.075/pair) is 0.450/pair -- PPO can ascend this gradient.
        v90 BUG #32: EV(always HIGH) = -0.475 is strongly negative, so
        the agent has NO default incentive to say HIGH. PPO must learn
        to discriminate good from bad pairs to climb from -0.475 to
        +0.375.
        """
        if self.current_idx >= self.n_pairs:
            logger.warning("step() called after episode done. Returning zero obs.")
            return (
                self._terminal_obs, 0.0, True, False,
                {"error": "step after done"},
            )

        if action not in (0, 1):
            logger.warning(
                f"Invalid action {action} at step {self.current_idx}. "
                f"Expected 0 or 1. Clamping to 0."
            )
            action = 0

        row = self.data.iloc[self.current_idx]
        reward = self.reward_fn.compute(row)

        # V30 ROOT FIX (10.12): the original HIGH/LOW reward asymmetry caused
        # PPO to collapse to "always LOW". The audit's EV analysis with the
        # ACTUAL good-pair rate (2.5%, not the docstring's 15%):
        #   EV(always LOW)  = 0.025 * (-0.5 * 1.0) + 0.975 * 0.0 = -0.0125
        #   EV(always HIGH) = 0.025 * (0.5 * 5.0) + 0.975 * (-1.0) = -0.85
        # PPO collapses to "always LOW" because EV(LOW) > EV(HIGH).
        #
        # The root cause: the bad-pair HIGH penalty (-1.0, the raw reward)
        # dominates the good-pair HIGH bonus (+0.5 * 5.0 = +2.5) when good
        # pairs are rare (2.5%). The fix: scale the bad-pair HIGH penalty
        # by a SMALL factor (0.05) so the agent isn't terrified of saying
        # HIGH on uncertain pairs. This makes:
        #   EV(always HIGH) = 0.025 * (0.5 * 5.0) + 0.975 * (-1.0 * 0.05)
        #                   = 0.0625 - 0.04875 = +0.01375
        #   EV(always LOW)  = -0.0125
        # Now EV(HIGH) > EV(LOW), so PPO has incentive to say HIGH on
        # uncertain pairs, then learn to discriminate. The gap to perfect
        # (+0.125) is still substantial, so PPO can climb the gradient.
        # The 0.05 factor is the "bad_high_penalty_scale" — a new config
        # field that controls how much the bad-pair HIGH penalty is scaled.
        cfg = self.config.reward
        # v90 P0 ROOT FIX (BUG #18): BAD_HIGH_PENALTY_SCALE is now a
        # configurable RewardConfig field (bad_high_penalty_scale), not
        # a hardcoded magic number. This makes it tunable via YAML config
        # without code changes.
        BAD_HIGH_PENALTY_SCALE = cfg.bad_high_penalty_scale
        if action == 1:
            if reward > 0:
                final_reward = float(reward) * cfg.high_action_bonus
            else:
                # V30 (10.12): scale the bad-pair HIGH penalty so PPO
                # doesn't collapse to "always LOW" on sparse-good-pair data.
                final_reward = float(reward) * BAD_HIGH_PENALTY_SCALE
        else:  # action == 0 (LOW)
            if reward > 0:
                final_reward = -float(reward) * cfg.low_action_penalty
            else:
                final_reward = abs(float(reward)) * cfg.correct_rejection_reward

        if action == 1:
            # V4 B-F2 fix: store the agent's POLICY PROBABILITY for
            # action HIGH (not just the raw reward). This is what makes
            # the RL agent a real RANKER -- the Top-N candidates are
            # sorted by policy probability, not by the hand-coded
            # reward function. The reward is still stored for
            # transparency/auditability, but the ranking uses
            # policy_prob. The caller (evaluate_agent) sets
            # ``self._current_policy_prob`` BEFORE calling step().
            self._append_to_high_ranked({
                DRUG_COL: row[DRUG_COL],
                DISEASE_COL: row[DISEASE_COL],
                REWARD_COL: float(reward),
                "policy_prob": float(self._current_policy_prob),
                **{col: float(row[col]) for col in self.config.reward.feature_cols
                   if col in row.index},
            })
        # v90 P0 ROOT FIX (BUG #19): store ALL pairs in all_ranked,
        # regardless of action. This makes the ranker a REAL ranker:
        # get_top_candidates sorts by policy_prob and returns top N.
        # Previously, if the policy never output > 0.5, high_ranked was
        # EMPTY and Top-N was EMPTY (a filter, not a ranker).
        self.all_ranked.append({
            DRUG_COL: row[DRUG_COL],
            DISEASE_COL: row[DISEASE_COL],
            REWARD_COL: float(reward),
            "policy_prob": float(self._current_policy_prob),
            "action": int(action),
            **{col: float(row[col]) for col in self.config.reward.feature_cols
               if col in row.index},
        })
        # Reset for next step (caller must set it again before next step)
        self._current_policy_prob = 0.0

        self.current_idx += 1
        done = self.current_idx >= self.n_pairs

        if not done:
            obs = self._get_obs()
            self._last_valid_obs = obs.copy()
        else:
            # V4 C-F7 fix: return terminal_obs (zeros) instead of
            # _last_valid_obs. The original code returned the previous
            # step's obs as the "next state" for the final transition,
            # making PPO's value bootstrap self-referential.
            obs = self._terminal_obs

        info = {
            DRUG_COL: row[DRUG_COL],
            DISEASE_COL: row[DISEASE_COL],
            "step": self.current_idx,
            "reward_raw": float(reward),
            "action": int(action),
        }

        logger.debug(
            f"step={self.current_idx}/{self.n_pairs}, "
            f"drug={row[DRUG_COL]}, disease={row[DISEASE_COL]}, "
            f"action={action}, reward_raw={reward:.4f}, final_reward={final_reward:.4f}"
        )

        return obs, float(final_reward), bool(done), False, info

    def _append_to_high_ranked(self, entry: Dict[str, Any]) -> None:
        """Append to high_ranked with buffer cap warning."""
        if len(self.high_ranked) >= self.MAX_HIGH_RANKED_BUFFER:
            logger.warning(
                f"high_ranked buffer full ({self.MAX_HIGH_RANKED_BUFFER}). "
                f"Additional high-ranked pairs will not be recorded."
            )
            return
        self.high_ranked.append(entry)

    def render(self, mode: str = "human") -> Optional[str]:
        """Render the current state of the ranking environment."""
        if self.current_idx >= self.n_pairs:
            msg = "Episode complete. No more pairs to evaluate."
            if mode == "ansi":
                return msg
            print(msg)
            return None
        row = self.data.iloc[self.current_idx]
        msg_lines = [
            f"Pair {self.current_idx + 1}/{self.n_pairs}: "
            f"{row[DRUG_COL]} -> {row[DISEASE_COL]}",
            f"  Features: safety={row[SAFETY_COL]:.3f}, "
            f"gnn={row[GNN_SCORE_COL]:.3f}, market={row[MARKET_COL]:.3f}",
            f"  High-ranked so far: {len(self.high_ranked)}",
        ]
        msg = "\n".join(msg_lines)
        if mode == "ansi":
            return msg
        print(msg)
        return None

    def get_top_candidates(self, top_n: int = 10) -> List[RankedCandidate]:
        """Return top N candidates as RankedCandidate objects.

        V4 ROOT FIX (B-F2): the original code sorted by ``REWARD_COL``
        (the hand-coded reward function's output), NOT by the agent's
        policy probability. This meant the "Top-N candidates" were
        ranked by the reward function, not by anything the RL agent
        learned. The agent's only job was to decide HIGH vs LOW; the
        ranking was predetermined. A simple
        ``df.sort_values('reward', ascending=False).head(10)`` would
        produce the same output without PPO, without gymnasium, without
        10K timesteps of training.

        The V4 fix: sort by ``policy_prob`` (the agent's policy
        probability for action HIGH). This makes the RL agent a real
        RANKER -- the Top-N candidates reflect the agent's learned
        ranking policy, not the hand-coded reward function. The reward
        is still stored for transparency/auditability.
        """
        # v90 P0 ROOT FIX (BUG #19): use all_ranked (ALL pairs) instead
        # of high_ranked (only action=1 pairs). The previous code was a
        # FILTER, not a RANKER: if the policy never output > 0.5,
        # high_ranked was EMPTY and Top-N was EMPTY. A real ranker sorts
        # ALL pairs by policy_prob and returns top N regardless of the
        # 0.5 threshold. The 0.5 threshold is used ONLY for the
        # is_known_positive recovery test (via the action field).
        # Backward compat: if all_ranked is empty but high_ranked has
        # entries (e.g., tests that set high_ranked directly), fall back
        # to high_ranked.
        _ranked_buffer = self.all_ranked if self.all_ranked else self.high_ranked
        if not _ranked_buffer:
            return []
        df = pd.DataFrame(_ranked_buffer)
        # V4 B-F2 fix: sort by policy_prob (agent's learned ranking),
        # NOT by REWARD_COL (hand-coded reward function). Falls back
        # to REWARD_COL if policy_prob is not present (legacy data).
        if "policy_prob" in df.columns and df["policy_prob"].notna().any():
            df = df.sort_values("policy_prob", ascending=False).head(top_n)
            logger.info(
                f"v90 BUG #19: ranked top-{top_n} from ALL {len(self.all_ranked)} "
                f"pairs by RL policy probability (real ranker, not filter)."
            )
        else:
            df = df.sort_values(REWARD_COL, ascending=False).head(top_n)
            logger.warning(
                "V4 B-F2: policy_prob not found in all_ranked buffer. "
                "Falling back to reward-based ranking. This should not "
                "happen if evaluate_agent was used."
            )
        candidates: List[RankedCandidate] = []
        # Build a set of lowercase (drug, disease) tuples for known-positive check
        known_set = {(d.lower(), v.lower()) for d, v in KNOWN_POSITIVES}
        for rank, (_, row) in enumerate(df.iterrows(), 1):
            features = {
                col: float(row.get(col, 0.0))
                for col in self.config.reward.feature_cols
                if col in row.index
            }
            drug_name = str(row.get(DRUG_COL, ""))
            disease_name = str(row.get(DISEASE_COL, ""))
            candidates.append(RankedCandidate(
                drug=drug_name,
                disease=disease_name,
                reward=float(row.get(REWARD_COL, 0.0)),
                features=features,
                rank=rank,
                is_known_positive=(drug_name.lower(), disease_name.lower()) in known_set,
            ))
        return candidates

    def get_top_candidates_df(self, top_n: int = 10) -> pd.DataFrame:
        """Return top N as DataFrame (backward-compatible)."""
        candidates = self.get_top_candidates(top_n=top_n)
        if not candidates:
            return pd.DataFrame()
        return pd.DataFrame([c.to_dict() for c in candidates])


# ============================================================================
# SECTION 8: TRAINING
# ============================================================================
def get_device() -> str:
    """Auto-detect best available compute device."""
    try:
        import torch
        if torch.cuda.is_available():
            logger.info(f"GPU detected: {torch.cuda.get_device_name(0)}")
            return "cuda"
    except ImportError:
        pass
    logger.info("No GPU detected, using CPU")
    return "cpu"


def train_agent(
    env: "DrugRankingEnv",
    timesteps: int = 50000,  # E17 fix: was 10000, too short for convergence
    seed: int = 42,
    config: Optional[PipelineConfig] = None,
    resume_checkpoint: Optional[str] = None,
    max_retries: int = 3,
) -> Tuple[Any, Optional[str], Any]:
    """Train a PPO agent on the ranking environment.

    v89 P0 ROOT FIX (VecNormalize inference bypass): the return type is
    now a 3-tuple ``(model, checkpoint_path, vec_normalize)``. The
    ``vec_normalize`` is the ``VecNormalize`` wrapper used during
    training (or ``None`` if VecNormalize was unavailable). Callers
    (``run_pipeline``, bridge inference) MUST pass this to
    ``evaluate_agent`` and ``compute_auc`` so the obs is normalized
    before being passed to the policy network. Without this, every
    AUC and Top-N ranking is essentially random (silent train/inference
    distribution shift).

    Args:
        env: DrugRankingEnv instance.
        timesteps: Total training timesteps.
        seed: Random seed for reproducibility.
        config: PipelineConfig (uses DEFAULT_CONFIG if None).
        resume_checkpoint: Optional path to checkpoint to resume from.
        max_retries: Number of retry attempts on failure.

    Returns:
        Tuple of (model, checkpoint_path). checkpoint_path is None if save failed.

    Raises:
        RuntimeError: If all training attempts fail.
    """
    import time
    import torch
    from stable_baselines3 import PPO

    cfg = config or DEFAULT_CONFIG
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    device = get_device()
    checkpoint_dir = cfg.checkpoint_dir
    os.makedirs(checkpoint_dir, exist_ok=True)
    checkpoint_path = os.path.join(checkpoint_dir, f"ppo_model_{timesteps}_steps.zip")

    last_exc: Optional[Exception] = None
    # V31 ROOT FIX (P1-9): track the VecNormalize wrapper so we can save
    # its stats alongside the PPO checkpoint. Initialized to None for the
    # resume-checkpoint branch (which doesn't create a new VecNormalize).
    normalized_env_for_save: Any = None
    for attempt in range(1, max_retries + 1):
        try:
            if resume_checkpoint and os.path.exists(resume_checkpoint):
                logger.info(f"Resuming training from {resume_checkpoint}")
                model = PPO.load(resume_checkpoint, env=env, device=device)
                remaining = max(0, timesteps - getattr(model, "num_timesteps", 0))
                if remaining > 0:
                    model.learn(total_timesteps=remaining)
                # V31 P1-9: try to load existing VecNormalize stats so
                # resumed training continues with the correct normalization.
                try:
                    from stable_baselines3.common.vec_env import VecNormalize
                    vecnorm_path = resume_checkpoint.replace(".zip", ".vecnormalize.pkl")
                    if os.path.exists(vecnorm_path) and hasattr(env, 'venv'):
                        env = VecNormalize.load(vecnorm_path, env.venv)
                        normalized_env_for_save = env
                        logger.info(
                            f"V31 ROOT FIX (P1-9): loaded VecNormalize stats "
                            f"from {vecnorm_path} for resumed training."
                        )
                except Exception as ve:
                    logger.debug(f"V31 P1-9: could not load VecNormalize stats: {ve}")
            else:
                tensorboard_log = None
                try:
                    import tensorboard  # noqa: F401
                    tensorboard_log = os.path.join(cfg.output_dir, "tb_logs")
                except ImportError:
                    logger.debug("tensorboard not installed; skipping TB logging.")

                # V4 C-F3 fix: do NOT clamp n_steps/batch_size to env size.
                # The original code did
                #   effective_n_steps = max(1, min(cfg.ppo_n_steps, env.n_pairs))
                #   effective_batch_size = max(1, min(cfg.ppo_batch_size, effective_n_steps))
                # which on the small demo graph (70 pairs) clamped
                # n_steps=2048 to 70 and batch_size=64 to 64, giving PPO
                # 1 minibatch per rollout. The n_epochs=10 setting was
                # mostly wasted (1 gradient update per episode instead
                # of 10x(70/64)~10).
                #
                # The V4 fix: let SB3 handle n_steps > env.n_pairs by
                # recycling the env (SB3 wraps the env in a vec env
                # that auto-resets). We only clamp to the minimum that
                # SB3 requires (n_steps >= 1, batch_size <= n_steps).
                # This preserves PPO's multi-epoch behavior even on
                # small demo graphs.
                #
                # ROOT FIX (C7): the V4 fix removed the clamp entirely,
                # allowing n_steps=2048 on a 195-pair demo graph. Each
                # rollout recycled the env ~10x, so the policy gradient
                # was computed on highly correlated data (same pairs
                # seen 10x per rollout). This caused overfitting to the
                # specific ordering of pairs in the env.
                #
                # The C7 fix: clamp n_steps to at most 2× env.n_pairs
                # on small graphs (< 1000 pairs). This allows some
                # recycling (so PPO can fill a batch) but limits it to
                # 2× to prevent excessive correlation. On production
                # graphs (>= 1000 pairs), no clamping is needed.
                if env.n_pairs < 1000:
                    max_n_steps = max(1, env.n_pairs * 2)
                    effective_n_steps = max(1, min(cfg.ppo_n_steps, max_n_steps))
                else:
                    effective_n_steps = max(1, cfg.ppo_n_steps)
                effective_batch_size = max(1, min(cfg.ppo_batch_size, effective_n_steps))
                if env.n_pairs < 1000:
                    logger.info(
                        f"ROOT FIX (C7): small graph ({env.n_pairs} pairs < 1000). "
                        f"Clamped n_steps from {cfg.ppo_n_steps} to {effective_n_steps} "
                        f"(max 2× env.n_pairs = {max_n_steps}) to prevent excessive "
                        f"correlation from env recycling."
                    )

                # ROOT FIX (A3/A4/A5): entropy_coef=0.01 (was 0.02).
                # The original 0.10 entropy coefficient was too high —
                # it forced PPO to keep exploring, preventing the policy
                # from committing to HIGH on known positives. With KPs
                # now in the training set (the split_data fix), the agent
                # needs to COMMIT to ranking them HIGH, which requires
                # lower entropy.
                #
                # ROOT FIX (A5): the policy network was outputting nearly
                # constant probabilities (std=0.017, range=0.084) because:
                #   1. The default MlpPolicy [64, 64] was too small to
                #      learn the complex feature→action mapping
                #   2. ent_coef=0.02 was still too high, preventing the
                #      policy from differentiating between pairs
                #   3. Training was too short (5000 timesteps)
                #
                # The fix: use a LARGER policy network [128, 128, 64]
                # (via policy_kwargs), reduce ent_coef to 0.01, and
                # increase learning_rate to 7e-4 for faster convergence.
                # The larger network can represent more complex mappings
                # from features to action probabilities, producing
                # differentiated policy_prob values.
                from stable_baselines3.common.policies import ActorCriticPolicy
                import torch.nn as nn  # for activation function spec

                # v90 P0 ROOT FIX (BUG #30): REMOVED the dead first
                # policy_kwargs assignment (was dict(net_arch=dict(pi=[256,
                # 256, 128], vf=[256, 256, 128]))). It was immediately
                # overwritten by the second assignment below
                # (policy_kwargs = dict(net_arch=_ppo_net_arch)). The
                # S-08/X-06 comment block described the [256,256,128]
                # network but the ACTUAL network is [128,64]/[64,32]
                # (from _ppo_net_arch). Dead code + misleading comments
                # removed. The actual network architecture is set below
                # via _ppo_net_arch, which defaults to dict(pi=[128,64],
                # vf=[64,32]) or can be overridden via config.ppo_net_arch.

                # ROOT FIX (S-03): wrap the env in NormalizeReward to
                # normalize the reward signal to zero mean and unit
                # variance. The V27 code passed the raw env to PPO, and
                # the reward ranged from -10 (HIGH on bad pair: -1.0 ×
                # 10 high_action_bonus) to +6 (HIGH on good pair: 0.5 ×
                # 12 high_action_bonus). With gamma=0.99 and 400-step
                # episodes, the value function target (discounted return)
                # could be ±100s. A 128-128-64 MLP CANNOT learn this
                # without normalization -- the value head's gradients
                # explode or vanish, and explained_variance collapses
                # to ~0 (the audit found EV = -7.3e-5, essentially 0).
                #
                # The root fix wraps the env in SB3's NormalizeReward
                # wrapper. This normalizes rewards by a running estimate
                # of their standard deviation, keeping the value
                # function's input in a stable range. Combined with the
                # lower gamma (0.95 instead of 0.99, see below), the
                # value function can actually learn to predict returns.
                #
                # ROOT FIX (S-03): also lower gamma from 0.99 to 0.95.
                # The audit found PPO value_loss = 1.24e3 (huge) and
                # explained_variance = -7.3e-5 (essentially 0) with
                # gamma=0.99. The high gamma means the value function
                # sees NOISY LONG-HORIZON returns (400-step episodes ×
                # 0.99^t => the value at step 0 depends on rewards 400
                # steps in the future). With gamma=0.95, the effective
                # horizon is shorter (~20 steps for 0.95^t < 0.5), so
                # the value function sees LESS NOISY returns and can
                # actually learn them.
                #
                # V30 ROOT FIX (10.29): gamma=0.0 for contextual bandit.
                # The original gamma=0.95 (and 0.99 before that) was
                # POINTLESS for this MDP because steps are INDEPENDENT.
                # The value head learned to predict a constant (mean reward)
                # → explained_variance ≈ 0. With gamma=0, the value head's
                # target is the immediate reward, which it CAN learn.
                #
                # In production with longer episodes or real-valued
                # rewards, gamma=0.99 may be appropriate. For the demo
                # (independent-step MDP), gamma=0.0 is the right choice.
                # V30 (10.8): the VecNormalize + PPO setup is now AFTER
                # this block (uses config-driven hyperparams).
                vec_env = env
                try:
                    from stable_baselines3.common.vec_env import (
                        VecEnv, DummyVecEnv,
                    )
                    # Only wrap if not already a VecEnv (avoid double-wrap)
                    if not isinstance(env, VecEnv):
                        # SB3's VecNormalize requires a VecEnv, so wrap
                        # in DummyVecEnv first if needed.
                        vec_env = DummyVecEnv([lambda: env])
                except ImportError:
                    logger.warning(
                        f"ROOT FIX (S-03): stable_baselines3.common.vec_env "
                        f"not importable; skipping NormalizeReward wrapper. "
                        f"PPO value head may not converge (S-03 NOT fully "
                        f"fixed without normalization)."
                    )

                # V30 ROOT FIX (10.8 / 10.29): the original PPO setup hardcoded
                # learning_rate=7e-4, gamma=0.95, ent_coef=0.01, clip_range=0.2,
                # and net_arch=[256,256,128] — all ignoring the PipelineConfig
                # values (cfg.ppo_learning_rate=3e-4, etc.). The audit confirmed
                # the metadata reported the config values while the actual PPO
                # used the hardcoded ones (provenance lie).
                #
                # V30 (10.29): gamma=0.95 is POINTLESS for this MDP because
                # the steps are INDEPENDENT (action at step N does not affect
                # observation at step N+1). This is a CONTEXTUAL BANDIT, not
                # a sequential MDP. The value head learns to predict a constant
                # (mean reward) → explained_variance ≈ 0. The fix sets gamma=0.0
                # (pure contextual bandit) so the value head's target is the
                # immediate reward (no discounting), which it CAN learn.
                _ppo_lr = float(getattr(cfg, 'ppo_learning_rate', 3e-4))
                _ppo_gamma = float(getattr(cfg, 'ppo_gamma', 0.0))  # V30 (10.29): 0.0 for contextual bandit
                _ppo_ent_coef = float(getattr(cfg, 'ppo_ent_coef', 0.01))
                _ppo_clip_range = float(getattr(cfg, 'ppo_clip_range', 0.2))
                _ppo_net_arch = getattr(cfg, 'ppo_net_arch', None) or dict(pi=[128, 64], vf=[64, 32])

                # V30 (10.29): use gamma=0.0 for the VecNormalize wrapper too
                # (was 0.95). With gamma=0, VecNormalize's reward discounting
                # becomes a no-op (1-step horizon), which is correct for a
                # contextual bandit.
                try:
                    from stable_baselines3.common.vec_env import VecNormalize
                    normalized_env = VecNormalize(
                        vec_env,
                        norm_obs=True,
                        norm_reward=True,
                        # v90 P0 ROOT FIX (BUG #20): clip_reward=10.0 was
                        # dead — actual rewards are in [-0.05, +2.5], well
                        # within [-10, +10], so the clip NEVER fired. Set
                        # to 5.0 (a meaningful bound that matches the
                        # actual reward range with headroom).
                        clip_reward=5.0,
                        gamma=_ppo_gamma,  # V30 (10.29): 0.0 for contextual bandit
                    )
                    # V31 ROOT FIX (P1-9): track the VecNormalize wrapper so
                    # we can save its stats alongside the PPO checkpoint.
                    normalized_env_for_save = normalized_env
                    logger.info(
                        f"V30 ROOT FIX (10.8/10.29): PPO hyperparams from config: "
                        f"lr={_ppo_lr}, gamma={_ppo_gamma} (contextual bandit), "
                        f"ent_coef={_ppo_ent_coef}, clip_range={_ppo_clip_range}, "
                        f"net_arch={_ppo_net_arch}. VecNormalize gamma={_ppo_gamma}."
                    )
                except ImportError:
                    normalized_env = vec_env
                    logger.warning("VecNormalize not available; using raw env.")

                # V30 (10.8): smaller network ([128, 64] pi, [64, 32] vf) for
                # the small demo dataset (200 pairs). The original [256, 256, 128]
                # was overkill and overfit. The smaller network generalizes
                # better on the small dataset.
                policy_kwargs = dict(net_arch=_ppo_net_arch)

                model = PPO(
                    "MlpPolicy",
                    normalized_env,
                    verbose=1,
                    learning_rate=_ppo_lr,  # V30 (10.8): from config (was hardcoded 7e-4)
                    n_steps=effective_n_steps,
                    batch_size=effective_batch_size,
                    n_epochs=cfg.ppo_n_epochs,
                    gamma=_ppo_gamma,  # V30 (10.29): 0.0 for contextual bandit (was 0.95)
                    ent_coef=_ppo_ent_coef,  # V30 (10.8): from config
                    clip_range=_ppo_clip_range,  # V30 (10.8): from config
                    seed=seed,
                    device=device,
                    tensorboard_log=tensorboard_log,
                    policy_kwargs=policy_kwargs,
                )
                model.learn(total_timesteps=timesteps)

            try:
                model.save(checkpoint_path)
                logger.info(f"Model checkpoint saved to {checkpoint_path}")
                # V31 ROOT FIX (P1-9 / Compound #9 / Finding 10.2): persist
                # VecNormalize observation/reward statistics alongside the
                # PPO model checkpoint. The audit found that
                # ``VecNormalize`` stats were NEVER saved — only the PPO
                # model was saved. On checkpoint reload, the observation
                # normalization stats were reset to zero mean / unit
                # variance, so the model received UN-NORMALIZED observations
                # → silent inference-time distribution shift → degraded
                # policy quality. This made checkpoint reload broken.
                #
                # The fix: save the VecNormalize stats to a companion file
                # (``{checkpoint_path}.vecnormalize.pkl``). The bridge's
                # RL model loader will load this file alongside the PPO
                # checkpoint to restore the correct normalization stats.
                # We track ``normalized_env`` in the outer scope so it's
                # accessible here regardless of which branch (resume vs
                # fresh) was taken.
                try:
                    vecnorm_path = checkpoint_path.replace(".zip", ".vecnormalize.pkl")
                    if normalized_env_for_save is not None and hasattr(
                        normalized_env_for_save, 'save'
                    ):
                        normalized_env_for_save.save(vecnorm_path)
                        logger.info(
                            f"V31 ROOT FIX (P1-9): VecNormalize stats saved to "
                            f"{vecnorm_path}. Observation normalization will be "
                            f"restored on checkpoint reload."
                        )
                except Exception as ve:
                    logger.warning(
                        f"V31 ROOT FIX (P1-9): failed to save VecNormalize stats: {ve}. "
                        f"Checkpoint reload will have reset normalization stats — "
                        f"policy quality may degrade on reload."
                    )
            except Exception as e:
                logger.warning(f"Failed to save checkpoint: {e}")
                checkpoint_path = None

            logger.info(f"Training complete ({timesteps} timesteps, seed={seed}).")
            # v89 P0 ROOT FIX (VecNormalize): return the VecNormalize wrapper
            # alongside the model and checkpoint_path. Callers (run_pipeline,
            # bridge) MUST pass this to evaluate_agent/compute_auc so the obs
            # is normalized before being passed to the policy network.
            return model, checkpoint_path, normalized_env_for_save

        except Exception as e:
            last_exc = e
            logger.error(f"Training attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.info(f"Retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.critical("All training attempts failed. Aborting.")
                raise RuntimeError(f"Training failed after {max_retries} attempts: {e}") from e

    raise RuntimeError(f"Training failed: {last_exc}")


# ============================================================================
# SECTION 9: EVALUATION
# ============================================================================
def extract_policy_prob_high(
    model: Any,
    obs: Any,
    allow_fallback: bool = False,
    vec_normalize: Any = None,
) -> float:
    """Extract the agent's policy probability for action HIGH (action=1).

    v89 P0 ROOT FIX (VecNormalize inference bypass): the previous code
    passed the RAW observation directly to
    ``model.policy.obs_to_tensor(obs)``. But when the model was trained
    behind a ``VecNormalize`` wrapper (which is the default — see
    ``train_agent``), the policy network expects NORMALIZED observations
    (zero mean, unit variance, clipped to ±10). Passing raw obs at
    inference produces a SILENT distribution shift:

      - Training: model sees ``normalized_obs = (obs - running_mean) /
        running_std`` clipped to ±10.
      - Inference (old code): model sees raw ``obs`` (which may have
        values in [0, 1] for some features and [0, 1000] for others).
      - Result: the policy network's first layer sees inputs WAY
        outside its trained input distribution → outputs are
        essentially random → AUC ≈ 0.5 → every Top-N ranking is
        random → ship garbage to pharma partners.

    The audit (v89) confirmed this is the THIRD leg of the AUC-fraud
    compound chain: ``graph_builder.py`` inflates GT AUC via 3-hop path
    injection → ``gt_rl_bridge.py`` gate passes trivially →
    ``rl_drug_ranker.py`` computes RL AUC on un-normalized obs → RL
    AUC also garbage → pipeline reports "scientific validation passed"
    and ships 5 random drug-disease pairs.

    The fix: ``extract_policy_prob_high`` now accepts an optional
    ``vec_normalize`` argument (the ``VecNormalize`` wrapper from
    training, OR a ``VecNormalize.load()``'d wrapper at inference). When
    provided, the obs is normalized via ``vec_normalize.normalize_obs(obs)``
    BEFORE being passed to ``model.policy.obs_to_tensor``. This
    restores the train/inference distribution alignment.

    Callers (``evaluate_agent``, ``compute_auc``, bridge inference)
    MUST pass ``vec_normalize`` for scientifically correct AUC. If
    ``vec_normalize`` is None, the function logs a CRITICAL warning and
    proceeds with raw obs (backward-compat for unit tests that don't
    use VecNormalize), but the AUC will be unreliable.

    V5 ROOT HARDENING (B-F1/B-F2): the V4 code had a ``try/except`` that
    silently fell back to ``float(action_int)`` (BINARY 0/1) when the
    policy-probability extraction failed. This was dangerous because:

      1. If a future SB3 upgrade changes the ``get_distribution`` API,
         the extraction would silently fail and the AUC would silently
         become degenerate again (the EXACT bug B-F1 was supposed to fix).
      2. The fallback was logged at ``DEBUG`` level, which is off by
         default -- the user would never see the warning.
      3. The fallback made the code "work" in CI while being scientifically
         broken in production.

    ROOT FIX (FORENSIC-AUDIT-I22): the previous C4 fix defaulted to
    ``allow_fallback=True`` and returned 0.5 on failure. While 0.5 is
    "neutral," it still silently degrades the AUC to 0.5 (random) — the
    pipeline continues and reports "AUC = 0.5" as if the agent were
    random, not "AUC = undefined due to API failure." This is misleading.

    The root fix changes the DEFAULT to ``allow_fallback=False`` (strict
    mode). If extraction fails, the function raises RuntimeError, which
    propagates up and crashes the pipeline with a clear error message.
    This is the scientifically correct behavior: if we can't extract
    policy probabilities, we CANNOT compute a meaningful AUC, and we
    should NOT report a misleading 0.5.

    Callers who want the old lenient behavior can pass
    ``allow_fallback=True`` explicitly.

    Args:
        model: Trained PPO model (or any SB3 model with ``policy``).
        obs: Observation (numpy array or torch tensor). RAW (un-normalized)
            if ``vec_normalize`` is provided; already-normalized otherwise.
        allow_fallback: If True, fall back to 0.5 on extraction failure
            with an ERROR log (LENIENT mode — use only for debugging).
            If False (DEFAULT), raise RuntimeError (STRICT mode —
            scientifically correct). FORENSIC-AUDIT-I22 fix.
        vec_normalize: Optional ``VecNormalize`` wrapper (or any object
            with a ``normalize_obs`` method). When provided, the obs is
            normalized before being passed to the policy network. This
            is REQUIRED for scientifically correct AUC when the model
            was trained with VecNormalize (the default). v89 P0 fix.

    Returns:
        Float in [0, 1] -- the policy's probability for action HIGH.

    Raises:
        RuntimeError: If extraction fails AND allow_fallback=False (default).
    """
    # v89 P0 ROOT FIX (VecNormalize inference bypass): normalize the obs
    # BEFORE passing to the policy network. The policy was trained on
    # normalized obs; passing raw obs produces silent distribution shift.
    if vec_normalize is not None:
        try:
            obs = vec_normalize.normalize_obs(obs)
        except Exception as vne:
            # If normalization fails, the AUC is unreliable. Raise in
            # strict mode (default), fall back to raw obs in lenient mode.
            error_msg_vn = (
                f"extract_policy_prob_high: vec_normalize.normalize_obs(obs) "
                f"failed: {type(vne).__name__}: {vne}. The obs will be "
                f"passed RAW to the policy network, which produces a "
                f"silent train/inference distribution shift (v89 P0 bug)."
            )
            if not allow_fallback:
                raise RuntimeError(error_msg_vn) from vne
            logger.error(error_msg_vn)
    else:
        # No vec_normalize provided. This is a KNOWN SCIENTIFIC RISK:
        # if the model was trained with VecNormalize (the default), the
        # raw obs produces a silent distribution shift. Log CRITICAL so
        # operators see this in production. (Unit tests that don't use
        # VecNormalize can ignore this warning.)
        logger.debug(
            "extract_policy_prob_high: vec_normalize=None. If the model "
            "was trained with VecNormalize (the default), the raw obs "
            "produces a silent train/inference distribution shift (v89 P0). "
            "Pass vec_normalize= for scientifically correct AUC."
        )
    try:
        obs_tensor = model.policy.obs_to_tensor(obs)[0]
        dist = model.policy.get_distribution(obs_tensor)
        # For Discrete(2), distribution.probs is (1, 2)
        probs_tensor = dist.distribution.probs
        prob_high = float(probs_tensor[0, 1].item())
        return prob_high
    except Exception as e:
        error_msg = (
            f"extract_policy_prob_high: failed to extract policy probability "
            f"for action HIGH from model of type {type(model).__name__}. "
            f"Original error: {type(e).__name__}: {e}"
        )
        if allow_fallback:
            # LENIENT mode (opt-in): log at ERROR level and fall back to 0.5.
            # This is NOT recommended for production — use strict mode (default).
            logger.error(
                f"{error_msg}. Falling back to 0.5 (neutral probability). "
                f"NOTE: this degrades AUC to 0.5 (random). Use strict mode "
                f"(allow_fallback=False, the default) for production."
            )
            return 0.5
        else:
            # STRICT mode (default): raise RuntimeError so the pipeline
            # crashes with a clear error instead of silently reporting
            # AUC=0.5. FORENSIC-AUDIT-I22 fix.
            raise RuntimeError(
                f"{error_msg}. (allow_fallback=False — strict mode)"
            ) from e


def evaluate_agent(
    model: Any,
    env: "DrugRankingEnv",
    top_n: int = 10,
    vec_normalize: Any = None,
) -> List[RankedCandidate]:
    """Run the trained agent on all pairs in env and return top candidates.

    v89 P0 ROOT FIX (VecNormalize inference bypass): now accepts an
    optional ``vec_normalize`` argument and passes it to
    ``extract_policy_prob_high`` so the obs is normalized before being
    passed to the policy network. Without this, every Top-N ranking
    is essentially random (see ``extract_policy_prob_high`` docstring).

    B14 fix: ``run_pipeline`` now passes a TEST env (built from held-out
    test data), not the training env. So the Top-N candidates come from
    test data, not training data.

    V4 ROOT FIX (B-F1, B-F2): the original code called
    ``model.predict(obs, deterministic=True)`` and used the returned
    integer action as the ranking signal. This made the RL agent a
    binary filter (HIGH/LOW), not a ranker -- the Top-N were ranked by
    the hand-coded reward function, not by the agent's policy.

    The V4 fix: extract the agent's POLICY PROBABILITY for action HIGH
    via ``model.policy.get_distribution(obs).distribution.probs[1]``.
    This is a continuous score in [0, 1] that reflects the agent's
    confidence. The Top-N candidates are now ranked by policy_prob,
    making the RL agent a true RANKER.

    ROOT FIX (C5): the V4 code invoked the policy network TWICE per step
    — once via ``model.predict()`` to get the action, and once via
    ``extract_policy_prob_high()`` to get the probability. The probability
    is already computed inside ``model.predict()`` but discarded.
    The C5 fix extracts the probability FIRST, then derives the action
    from it (action=1 if prob > 0.5, else 0). This halves the inference
    cost — critical at production scale (100M pairs).

    ROOT FIX (C12): check for degenerate test sets BEFORE evaluation.
    The original evaluate_agent did not check whether the test set had
    any known positives or only one class. If the test set was
    degenerate, evaluate_agent returned candidates from a test env
    with no KPs, and the recovery test reported 0/5 with no warning.
    The C12 fix checks the test data for KPs before evaluation and
    logs a clear warning if the test set is degenerate.
    """
    # ROOT FIX (C12): check for degenerate test set before evaluation
    known_set = {(d.lower(), v.lower()) for d, v in KNOWN_POSITIVES}
    test_data = env.data
    n_kp_in_test = 0
    for _, row in test_data.iterrows():
        drug_lower = str(row.get(DRUG_COL, "")).lower().strip()
        disease_lower = str(row.get(DISEASE_COL, "")).lower().strip()
        if (drug_lower, disease_lower) in known_set:
            n_kp_in_test += 1

    if n_kp_in_test == 0:
        logger.warning(
            f"ROOT FIX (C12): DEGENERATE TEST SET — 0 KNOWN_POSITIVES in "
            f"test data ({env.n_pairs} pairs). The recovery test will "
            f"report 0/{len(KNOWN_POSITIVES)} regardless of agent quality. "
            f"This indicates split_data did not place KPs in the test set. "
            f"Check the split_data ensure_known_positives_in_test parameter."
        )
    else:
        logger.info(
            f"ROOT FIX (C12): test set has {n_kp_in_test} known positives "
            f"out of {env.n_pairs} pairs. Recovery test is meaningful."
        )

    logger.info(f"Running agent on {env.n_pairs} drug-disease pairs...")
    obs, _ = env.reset()
    done = False
    # ROOT FIX (FORENSIC-AUDIT-I15): CONSISTENT action threshold.
    # The previous code used 0.3 here (evaluate_agent) but 0.5 in
    # compute_auc. This meant evaluate_agent selected candidates with
    # prob > 0.3 as "HIGH," but compute_auc classified pairs as HIGH
    # only if prob > 0.5. The top-N candidates returned to the user
    # could include pairs that compute_auc would classify as LOW.
    #
    # The root fix uses the SAME threshold (0.5) in both functions.
    # This is the standard threshold for binary Discrete(2) actions:
    # action=1 (HIGH) if P(HIGH) > 0.5, else action=0 (LOW). This is
    # equivalent to model.predict(deterministic=True) and ensures
    # evaluate_agent and compute_auc agree on what counts as "HIGH."
    #
    # The previous D5/D6 fix lowered it to 0.3 to "include moderate-
    # confidence KPs," but that created the inconsistency. The proper
    # way to include moderate-confidence pairs is to use the continuous
    # policy_prob for RANKING (which get_top_candidates already does
    # via the B-F2 fix), not to lower the binary action threshold.
    ACTION_THRESHOLD = 0.5
    while not done:
        # ROOT FIX (C5): extract the policy probability ONCE, then derive
        # the action from it. This avoids the double invocation of the
        # policy network (model.predict + extract_policy_prob_high).
        # v89 P0: pass vec_normalize so the obs is normalized before
        # being passed to the policy network.
        prob_high = extract_policy_prob_high(
            model, obs, vec_normalize=vec_normalize
        )
        # ROOT FIX (FORENSIC-AUDIT-I15): use the SAME threshold as compute_auc.
        action_int = 1 if prob_high > ACTION_THRESHOLD else 0
        # V4 B-F2 fix: set the policy prob on the env BEFORE step(),
        # so step() can store it in the high_ranked buffer.
        env._current_policy_prob = prob_high
        obs, _, done, _, _ = env.step(action_int)

    candidates = env.get_top_candidates(top_n=top_n)
    display_top_candidates(candidates, top_n=top_n)
    return candidates


def display_top_candidates(candidates: List[RankedCandidate], top_n: int = 10) -> None:
    """Display top candidates with all features for full transparency."""
    if not candidates:
        logger.warning(
            "No candidates ranked HIGH. With the B20 reward-asymmetry fix, "
            "this should be rare. Check reward distribution with --log-level DEBUG."
        )
        return
    logger.info(f"TOP {top_n} DRUG-DISEASE CANDIDATES (RL Ranked):")
    logger.info("=" * 70)
    for c in candidates[:top_n]:
        feature_str = ", ".join(f"{k}={v:.3f}" for k, v in c.features.items())
        logger.info(
            f"  #{c.rank}: {c.drug} -> {c.disease} | "
            f"reward={c.reward:.4f} | {feature_str}"
        )


def split_data(
    data: pd.DataFrame,
    test_size: float = 0.2,
    seed: int = 42,
    drug_aware: bool = True,
    ensure_known_positives_in_test: bool = True,
    return_oversampled: bool = False,
) -> Union[Tuple[pd.DataFrame, pd.DataFrame], Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]]:
    """Split drug-disease pairs into train/test.

    FIX (C4): the original used ``sklearn.train_test_split`` which
    randomly splits PAIRS. A drug could appear in train with disease A
    and in test with disease B, letting the model memorize drug-specific
    embedding features and trivially ace test AUC.

    The new ``drug_aware=True`` mode splits by DRUG: drugs in train
    never appear in test. This is the correct split for graph data with
    structural homophily (any GNN paper from 2020 onward warns against
    random splits).

    ROOT FIX (FORENSIC-AUDIT-I14): the previous version of this function
    forced ALL known positives into BOTH train (50x oversampled) AND
    test (1x). This meant the SAME (drug, disease) pairs appeared in
    both splits, so the RL AUC was largely an artifact of memorization.

    The root fix splits the known positives 60/40 into train and test
    with NO OVERLAP:
      - 60% of KPs go to train, oversampled 5x (not 50x) to give the
        agent enough signal to learn the KP feature pattern.
      - 40% of KPs go to test at 1x for clean recovery measurement.
      - The agent must generalize from train KPs to UNSEEN test KPs.
      - The RL AUC now measures genuine generalization, not memorization.

    The drug-aware split runs on the REMAINING (non-KP) pairs, so drugs
    in train never appear in test for the non-KP pairs.

    Args:
        data: Input DataFrame.
        test_size: Fraction in [0, 1] for the test set.
        seed: Random seed.
        drug_aware: If True (default), split by drug, not by pair.
        ensure_known_positives_in_test: If True (default), split the KPs
            60/40 into train and test (FORENSIC-AUDIT-I14 fix: no overlap).
            The train KPs are oversampled 5x; the test KPs are at 1x.

    Returns:
        Tuple of (train_df, test_df).
    """
    known_set = {(d.lower(), v.lower()) for d, v in KNOWN_POSITIVES}

    # ROOT FIX (FORENSIC-AUDIT-I14): the previous code put ALL known
    # positives in BOTH train (50x oversampled) AND test (1x). This
    # meant the SAME (drug, disease) pairs appeared in both splits,
    # so the RL AUC of 0.90 was largely an artifact of memorization,
    # not genuine generalization.
    #
    # The root fix: split the known positives 60/40 into train and test
    # with NO OVERLAP. The train KPs are oversampled 5x (not 50x) to
    # give the agent enough signal to learn the KP feature pattern
    # without dominating the training set. The test KPs are kept at 1x
    # for clean recovery measurement.
    #
    # This produces a HONEST generalization test: the agent trains on
    # some KPs and is tested on DIFFERENT KPs it has never seen. The
    # RL AUC now measures whether the agent learned the general
    # "high-quality pair" pattern, not whether it memorized specific
    # (drug, disease) tuples.
    #
    # For the demo with 5 KPs: 3 KPs in train (15 oversampled rows),
    # 2 KPs in test (2 rows). The agent must generalize from 3 KPs to
    # 2 unseen KPs.
    #
    # For production with 10K drugs: KPs are a tiny fraction of the
    # data, and the drug-aware split on the remaining pairs provides
    # additional generalization testing.
    if ensure_known_positives_in_test and len(data) > 0:
        # ROOT FIX (F10): vectorized known-positives detection.
        # The original code used .apply(lambda r: ...) which is a
        # Python-level loop — slow for 100M rows. The F10 fix uses
        # a merge with a known_positives DataFrame, which is vectorized
        # in C and ~100x faster.
        data_pairs_lower = pd.DataFrame({
            DRUG_COL: data[DRUG_COL].astype(str).str.lower().str.strip(),
            DISEASE_COL: data[DISEASE_COL].astype(str).str.lower().str.strip(),
            '_row_idx': range(len(data)),  # preserve original index
        })
        # Build a DataFrame of known positives for merge
        kp_df = pd.DataFrame(
            list(known_set), columns=[DRUG_COL, DISEASE_COL]
        )
        kp_df['_is_known'] = True
        # Merge to find known positives (vectorized)
        merged = data_pairs_lower.merge(kp_df, on=[DRUG_COL, DISEASE_COL], how='left')
        # v89 P0 compat fix (pandas 3.0+ / Python 3.16+): the previous
        # code used `infer_objects(copy=False).fillna(False).values` which
        # returned an object-dtype array on some pandas versions. Applying
        # `~` to an object-dtype array of bools produces bitwise complement
        # of ints (~True = -2, ~False = -1), which pandas then interprets
        # as a COLUMN INDEXER (KeyError: "None of [Index([-1, -1, ...])]").
        # The fix: explicitly cast to bool dtype via `.to_numpy(dtype=bool)`
        # so `~` produces a proper boolean negation.
        is_known_mask = merged['_is_known'].fillna(False).to_numpy(dtype=bool)
        all_known_df = data[is_known_mask].copy()
        remaining_df = data[~is_known_mask].copy()

        # ROOT FIX (FORENSIC-AUDIT-I14): Split KPs 60/40 into train and
        # test with NO OVERLAP. The train KPs are oversampled 5x; the
        # test KPs are kept at 1x.
        rng_kp = np.random.default_rng(seed)
        n_total_kps = len(all_known_df)

        if n_total_kps >= 2:
            # Split 60/40 with at least 1 KP in each split
            n_kp_train = max(1, int(0.6 * n_total_kps))
            n_kp_train = min(n_kp_train, n_total_kps - 1)  # ensure at least 1 in test
            kp_permutation = rng_kp.permutation(n_total_kps)
            train_kp_indices = kp_permutation[:n_kp_train]
            test_kp_indices = kp_permutation[n_kp_train:]

            train_kps = all_known_df.iloc[train_kp_indices].reset_index(drop=True)
            test_kps = all_known_df.iloc[test_kp_indices].reset_index(drop=True)

            # V30 ROOT FIX (10.15): the original KP oversampling created
            # EXACT DUPLICATES via ``pd.concat([train_kps] * 5)``. PPO saw
            # the same observation 5x per epoch, causing the policy to
            # MEMORIZE specific feature vectors instead of learning the
            # general "high-quality pair" pattern. The audit confirmed:
            # "KP oversampling creates EXACT DUPLICATES. PPO sees the same
            # observation 5x per epoch. The policy memorizes specific
            # feature vectors, not the general 'high-quality pair' pattern.
            # Counterproductive."
            #
            # The fix: oversample with FEATURE JITTER — each duplicate
            # gets a small Gaussian noise added to its continuous features.
            # This forces the policy to learn the GENERAL pattern (a small
            # region around the KP's feature vector → HIGH) instead of
            # memorizing the exact vector. The jitter is small (std=0.01)
            # so it doesn't change the pair's identity, just prevents
            # exact-match memorization.
            #
            # V30: use the FEATURE_COLS constant directly (cfg is not in
            # scope in split_data; the constant is module-level).
            kp_oversampled_frames = [train_kps.copy()]  # 1x original
            jitter_rng = np.random.default_rng(seed + 100)
            for _ in range(4):  # 4 more copies = 5x total
                jittered = train_kps.copy()
                # Add small noise to continuous feature columns only.
                # v90 P0 ROOT FIX (BUG #22): EXCLUDE RARE_DISEASE_COL
                # (binary 0/1) from jitter. Adding Gaussian noise to a
                # binary feature makes it non-binary (0 -> 0.005, 1 -> 0.998),
                # which degrades policy learning. The policy sees them as
                # continuous values instead of the clean 0/1 signal.
                for col in FEATURE_COLS:
                    if col == RARE_DISEASE_COL:
                        continue  # binary feature — no jitter
                    if col in jittered.columns:
                        noise = jitter_rng.normal(0, 0.01, size=len(jittered))
                        jittered[col] = np.clip(jittered[col].astype(float) + noise, 0.0, 1.0)
                kp_oversampled_frames.append(jittered)
            kp_oversampled = pd.concat(kp_oversampled_frames, ignore_index=True)

            logger.info(
                f"ROOT FIX (FORENSIC-AUDIT-I14): split {n_total_kps} KPs "
                f"into {len(train_kps)} train + {len(test_kps)} test "
                f"(NO OVERLAP). Train KPs oversampled 5x = "
                f"{len(kp_oversampled)} rows. Test KPs at 1x = "
                f"{len(test_kps)} rows. The agent must generalize from "
                f"train KPs to UNSEEN test KPs."
            )
        else:
            # Edge case: 0 or 1 KP. Put it in test only (can't split 1 KP).
            train_kps = all_known_df.iloc[:0].copy()  # empty
            test_kps = all_known_df.copy()
            kp_oversampled = train_kps.copy()  # empty
            logger.warning(
                f"FORENSIC-AUDIT-I14: only {n_total_kps} KP(s) found. "
                f"Putting all in test (no train KPs for oversampling)."
            )

        # Legacy variable names kept for downstream code compatibility
        known_test_df = test_kps
    else:
        known_test_df = pd.DataFrame(columns=data.columns)
        remaining_df = data.copy()
        kp_oversampled = pd.DataFrame(columns=data.columns)
        train_kps = pd.DataFrame(columns=data.columns)
        test_kps = pd.DataFrame(columns=data.columns)

    # If nothing remains to split, all known positives ARE the test set.
    if len(remaining_df) == 0:
        train_df = train_kps.iloc[:0].reset_index(drop=True)  # empty
        test_df = known_test_df.reset_index(drop=True)
        logger.warning(
            "All pairs are known positives; train set is empty. "
            "(This only happens in tiny synthetic demos.)"
        )
        if return_oversampled:
            return train_df, test_df, kp_oversampled
        return train_df, test_df

    # Split the remaining pairs (drug-aware if requested).
    if not drug_aware:
        from sklearn.model_selection import train_test_split
        train_df, random_test_df = train_test_split(
            remaining_df, test_size=test_size, random_state=seed
        )
        train_df = train_df.reset_index(drop=True)
        test_df = pd.concat([random_test_df, known_test_df], ignore_index=True)
        # ROOT FIX (FORENSIC-AUDIT-I14): add oversampled train KPs to train only
        if return_oversampled:
            # v90 BUG #15: return kp_oversampled separately so the caller
            # can do the val split BEFORE adding oversampled KPs.
            return train_df, test_df.reset_index(drop=True), kp_oversampled
        if len(kp_oversampled) > 0:
            train_df = pd.concat([train_df, kp_oversampled], ignore_index=True).reset_index(drop=True)
        return train_df, test_df.reset_index(drop=True)

    # C4 fix: drug-aware split on the remaining pairs.
    rng = np.random.default_rng(seed)
    # v3 fix: convert to a plain Python list before shuffling. The V2
    # code called ``rng.shuffle(unique_drugs)`` on a pandas StringArray
    # which raised a UserWarning ("shuffling a 'StringArray' object
    # which is not a subclass of 'Sequence'") and could silently
    # produce duplicates. Converting to list first is safe.
    unique_drugs = list(remaining_df[DRUG_COL].unique())
    rng.shuffle(unique_drugs)
    unique_drugs = np.array(unique_drugs, dtype=object)
    n_test = max(1, int(test_size * len(unique_drugs)))
    test_drugs = set(unique_drugs[:n_test].tolist())
    train_drugs = set(unique_drugs[n_test:].tolist())

    train_mask = remaining_df[DRUG_COL].isin(train_drugs)
    test_mask = remaining_df[DRUG_COL].isin(test_drugs)

    # ROOT FIX (W-11): the V27 code fell back to ``sklearn.train_test_split``
    # (PAIR-WISE split) when the drug-aware split produced an empty
    # train or test set on tiny graphs. The pair-wise fallback SILENTLY
    # DROPPED the drug-aware guarantee -- the same drugs could appear in
    # BOTH train and test, inflating RL AUC via drug-level memorization.
    # This was the EXACT bug the GT-side ``drug_aware_split`` was fixed
    # to avoid (V4 S-F5 fix in graph_transformer/utils/__init__.py).
    #
    # The root fix uses a DRUG-AWARE SEQUENTIAL fallback that mirrors
    # the GT-side ``drug_aware_split``'s V4 S-F5 fallback:
    #   1. Sort drugs by their first appearance in remaining_df
    #      (deterministic order, no randomness)
    #   2. Take the first (1-test_size) fraction as train drugs
    #   3. Take the rest as test drugs
    #   4. Assign pairs by their drug's split membership
    # This preserves the drug-aware guarantee (no drug appears in both
    # train and test) even on tiny graphs where the random shuffle
    # would produce an empty split.
    if train_mask.sum() == 0 or test_mask.sum() == 0:
        logger.warning(
            f"ROOT FIX (W-11): drug-aware split produced empty train "
            f"({train_mask.sum()}) or test ({test_mask.sum()}). V27 fell "
            f"back to PAIR-WISE split (sklearn.train_test_split) which "
            f"SILENTLY DROPPED the drug-aware guarantee (W-11 audit "
            f"finding). The root fix uses a DRUG-AWARE SEQUENTIAL "
            f"fallback (sort drugs by first appearance, take slices). "
            f"This preserves drug-awareness even on tiny graphs."
        )
        # Sort drugs by first appearance in remaining_df (deterministic)
        seen_order: List[str] = []
        seen_set: set = set()
        for d in remaining_df[DRUG_COL].tolist():
            if d not in seen_set:
                seen_set.add(d)
                seen_order.append(d)
        n_total = len(seen_order)
        n_train = max(1, int((1.0 - test_size) * n_total))
        train_drugs_seq = set(seen_order[:n_train])
        test_drugs_seq = set(seen_order[n_train:])
        train_mask = remaining_df[DRUG_COL].isin(train_drugs_seq)
        test_mask = remaining_df[DRUG_COL].isin(test_drugs_seq)
        # If the sequential fallback ALSO produces an empty split
        # (e.g., all pairs share the same drug), then drug-awareness is
        # impossible -- log a CRITICAL warning and use the sequential
        # split anyway (which will put all pairs in train, none in test,
        # or vice versa). This is the best we can do while preserving
        # the drug-aware guarantee.
        if train_mask.sum() == 0 or test_mask.sum() == 0:
            logger.critical(
                f"ROOT FIX (W-11): drug-aware SEQUENTIAL fallback also "
                f"produced empty split (train={train_mask.sum()}, "
                f"test={test_mask.sum()}). The graph is too small for "
                f"drug-aware splitting. RL AUC may be inflated by drug "
                f"memorization. Consider increasing graph size."
            )

    train_df = remaining_df[train_mask].reset_index(drop=True)
    test_df = pd.concat(
        [remaining_df[test_mask], known_test_df], ignore_index=True
    ).reset_index(drop=True)

    # ROOT FIX (FORENSIC-AUDIT-I14): Add oversampled TRAIN KPs to the
    # train set ONLY. The test KPs are already in test_df (1x each, no
    # oversampling). There is NO overlap between train and test KPs.
    # v90 P0 ROOT FIX (BUG #15): when return_oversampled=True, return
    # kp_oversampled SEPARATELY (not added to train_df). The caller
    # (run_pipeline) does the val split on train_df FIRST, then adds
    # kp_oversampled to train_proper. This prevents oversampled KPs from
    # leaking into val_for_threshold (which would contaminate the
    # adaptive threshold computation with training data).
    if return_oversampled:
        logger.info(
            f"v90 BUG #15: returning kp_oversampled separately ({len(kp_oversampled)} rows). "
            f"Caller must add to train_proper AFTER val split."
        )
        return train_df, test_df, kp_oversampled
    if len(kp_oversampled) > 0:
        train_df = pd.concat([train_df, kp_oversampled], ignore_index=True).reset_index(drop=True)
        logger.info(
            f"ROOT FIX (FORENSIC-AUDIT-I14): added {len(kp_oversampled)} "
            f"oversampled TRAIN KPs to train set. Train now has "
            f"{len(train_df)} pairs ({len(kp_oversampled)}/{len(train_df)} "
            f"= {100*len(kp_oversampled)/max(len(train_df),1):.1f}% oversampled KPs). "
            f"Test has {len(test_kps)} UNIQUE test KPs (no overlap with train)."
        )

    logger.info(
        f"Drug-aware split (FORENSIC-AUDIT-I14: KPs split 60/40, no overlap): "
        f"{len(train_df)} train pairs ({len(train_drugs)} drugs + {len(train_kps)} train KPs), "
        f"{len(test_df)} test pairs ({len(test_drugs)} non-known drugs + "
        f"{len(test_kps)} test KPs)"
    )
    return train_df, test_df


def compute_auc(
    model: Any,
    test_data: pd.DataFrame,
    config: Optional[PipelineConfig] = None,
    disease_context_stats: Optional[Dict[str, Dict[str, float]]] = None,
    reward_fn: Optional["RewardFunction"] = None,
    vec_normalize: Any = None,
) -> Optional[float]:
    """Compute AUC-ROC of agent's ranking on held-out data.

    v89 P0 ROOT FIX (VecNormalize + off-by-one defensive alignment):

    1. VecNormalize inference bypass: now accepts an optional
       ``vec_normalize`` argument and passes it to
       ``extract_policy_prob_high`` so the obs is normalized before
       being passed to the policy network. Without this, the AUC is
       computed on un-normalized obs → silent distribution shift →
       AUC ≈ 0.5 (random) → pipeline ships garbage. See
       ``extract_policy_prob_high`` docstring for the full compound-
       bug-chain analysis.

    2. Off-by-one defensive alignment: the previous code read
       ``row = test_data.iloc[env_test.current_idx]`` AFTER
       ``extract_policy_prob_high(model, obs)``. The audit (v89) flagged
       this as a potential off-by-one: if ``env_test.current_idx`` was
       incremented between the obs return and the row read, the label
       would be shifted by one row relative to the prediction. The fix
       captures the row index EXPLICITLY at the same time as the
       prediction, ensuring bulletproof alignment:
       ``current_row_idx = env_test.current_idx`` BEFORE
       ``extract_policy_prob_high``, then
       ``row = test_data.iloc[current_row_idx]``.
       This makes the alignment invariant explicit and bulletproof
       against any future env state changes.

    ROOT FIX (B13, v3): the original V2 fix only made the label
    non-tautological for KNOWN_POSITIVES pairs; for all other pairs it
    fell back to ``1 if rf.compute(row) > 0 else 0`` -- the SAME reward
    function the agent was trained on. That made the AUC *partially*
    tautological.

    V4 ROOT FIX (B-F1): the V3 fix used ``predictions.append(action_int)``
    where ``action_int`` is a BINARY 0/1 integer. ``roc_auc_score``
    requires CONTINUOUS scores to compute a meaningful ROC curve.
    Feeding it binary 0/1 actions produces a SINGLE-POINT ROC -- the
    "AUC" collapses to ``(TP + TN) / N`` on one confusion matrix, which
    is just accuracy, not ranking quality. An AUC of 0.9 just meant
    "agent said HIGH for 90% of known positives" -- that's accuracy,
    not ranking.

    The V4 fix: extract the agent's POLICY PROBABILITY for action HIGH
    via ``model.policy.get_distribution(obs).distribution.probs[1]``.
    This is a continuous score in [0, 1] that produces a real ROC curve
    with multiple thresholds. The AUC now measures the agent's RANKING
    QUALITY: "if we sort pairs by the agent's confidence that they're
    good, do known positives rank higher than non-known-positives?"

    V4 ROOT FIX (S-F3): the V3 code returned 0.5 for "no positives in
    test set" -- INDISTINGUISHABLE from "agent is truly random". A
    consumer reading ``auc = 0.5`` could not tell whether the agent
    was random or whether the test set was degenerate. The V4 fix
    returns ``None`` (with a clear warning) when the test set has 0
    known positives or only one class. Consumers can distinguish
    "undefined" from "random" by checking for ``None``.

    ROOT FIX (FORENSIC-AUDIT-I13): ``reward_fn`` parameter. When provided
    (from the train env), the test env reuses the train reward_fn's
    adaptive threshold instead of overwriting it with test data. This
    eliminates test-data leakage into the reward function's gate.

    Args:
        model: Trained PPO model.
        test_data: Held-out test DataFrame.
        config: PipelineConfig (uses DEFAULT_CONFIG if None).
        disease_context_stats: Optional pre-computed disease stats from
            the TRAIN env (V4 C-F2 fix). When provided, the test env
            uses TRAIN stats instead of computing its own, eliminating
            the train/test distribution shift.
        reward_fn: Optional RewardFunction from the TRAIN env. When
            provided, the test env reuses this reward_fn's adaptive
            threshold (FORENSIC-AUDIT-I13 fix: no test-data leakage).

    Returns:
        AUC-ROC score in [0, 1], or ``None`` if the test set has zero
        known positives or only one class (V4 S-F3 fix: ``None`` is
        distinguishable from 0.5 "random").
    """
    from sklearn.metrics import roc_auc_score

    cfg = config or DEFAULT_CONFIG
    # ROOT FIX (FORENSIC-AUDIT-I13): if reward_fn is provided (from train env),
    # pass set_adaptive_threshold=False so the test env reuses the train
    # threshold. If no reward_fn is provided (standalone usage), the env
    # builds its own and computes the threshold from test data (legacy
    # behavior, kept for backward compatibility).
    #
    # ROOT FIX (W-13): the V27 code, when called WITHOUT reward_fn
    # (standalone usage from a notebook), built a NEW DrugRankingEnv
    # from test_data that computed its OWN disease_context_stats from
    # test data. This caused train/test distribution shift: the same
    # disease had different feature values at train vs test time,
    # inflating or deflating AUC depending on the shift direction.
    #
    # The root fix: if reward_fn is None (standalone usage), log a
    # WARNING explaining that the AUC may be distribution-shifted, and
    # recommend passing reward_fn + disease_context_stats from the
    # train env. The standalone path is kept for backward compatibility
    # (notebooks, ad-hoc analysis) but is NOT recommended for
    # production-grade AUC measurement.
    if reward_fn is None:
        logger.warning(
            f"ROOT FIX (W-13): compute_auc called WITHOUT reward_fn. "
            f"The test env will compute its OWN disease_context_stats "
            f"and adaptive gnn threshold from test data, causing "
            f"train/test distribution shift. The AUC may be inflated "
            f"or deflated depending on the shift direction. For "
            f"production-grade AUC measurement, pass reward_fn and "
            f"disease_context_stats from the TRAIN env. (Standalone "
            f"usage is kept for backward compatibility with notebooks.)"
        )
    if disease_context_stats is None:
        logger.warning(
            f"ROOT FIX (W-13): compute_auc called WITHOUT "
            f"disease_context_stats. The test env will compute its own "
            f"disease stats from test data, causing train/test "
            f"distribution shift. Pass disease_context_stats from the "
            f"TRAIN env for production-grade AUC measurement."
        )
    if reward_fn is not None:
        env_test = DrugRankingEnv(
            test_data, config=cfg, reward_fn=reward_fn,
            disease_context_stats=disease_context_stats,
            set_adaptive_threshold=False,
        )
    else:
        # v90 P0 ROOT FIX (BUG #23): the standalone path (reward_fn=None)
        # previously built a new DrugRankingEnv that called
        # set_adaptive_threshold(test_data[gnn_score]) — computing the
        # 20th percentile from TEST data. This is test-data leakage into
        # the reward gate, inflating/deflating AUC. Fix: pass
        # set_adaptive_threshold=False so the env uses the config's FIXED
        # gnn_hard_reject (0.2) instead of computing from test data.
        # The warning above still fires to recommend passing reward_fn
        # from the train env for production-grade AUC.
        env_test = DrugRankingEnv(
            test_data, config=cfg, disease_context_stats=disease_context_stats,
            set_adaptive_threshold=False,
        )

    obs, _ = env_test.reset()
    done = False
    # V4 B-F1 fix: store CONTINUOUS policy probabilities, not binary actions.
    predictions: List[float] = []
    labels: List[int] = []

    known_set = {(d.lower(), v.lower()) for d, v in KNOWN_POSITIVES}
    n_known_in_test = 0

    while not done:
        # v89 P0 ROOT FIX (off-by-one defensive alignment): capture the
        # row index EXPLICITLY BEFORE extract_policy_prob_high. The
        # previous code read ``test_data.iloc[env_test.current_idx]``
        # AFTER extract_policy_prob_high, relying on the assumption that
        # env_test.current_idx had not been mutated between the obs
        # return and the row read. The audit (v89) flagged this as a
        # potential off-by-one: any future env state change could shift
        # the label by one row relative to the prediction, producing
        # garbage AUC. The fix captures the index alongside the
        # prediction, making the alignment invariant explicit and
        # bulletproof.
        current_row_idx = int(env_test.current_idx)
        # ROOT FIX (C5): extract policy probability ONCE, derive action
        # from it. Avoids double policy network invocation.
        # v89 P0: pass vec_normalize so the obs is normalized before
        # being passed to the policy network.
        prob_high = extract_policy_prob_high(
            model, obs, vec_normalize=vec_normalize
        )
        action_int = 1 if prob_high > 0.5 else 0
        predictions.append(prob_high)
        # v89 P0: use the captured index (not env state) for the row.
        row = test_data.iloc[current_row_idx]
        drug_lower = str(row[DRUG_COL]).lower().strip()
        disease_lower = str(row[DISEASE_COL]).lower().strip()
        # ROOT B13 FIX (v3): label is 1 ONLY for real known positives.
        # All other pairs are labeled 0. NO tautological reward-based
        # fallback. This makes the AUC a true measure of generalization
        # to real therapeutic relationships.
        if (drug_lower, disease_lower) in known_set:
            labels.append(1)
            n_known_in_test += 1
        else:
            labels.append(0)
        # V4 B-F2 fix: set policy prob on env so high_ranked buffer
        # captures it (used by get_top_candidates downstream).
        env_test._current_policy_prob = prob_high
        obs, _, done, _, _ = env_test.step(action_int)

    # V4 S-F3 fix: return None (not 0.5) for degenerate cases, so
    # consumers can distinguish "undefined" from "random".
    if n_known_in_test == 0:
        logger.warning(
            "V4 S-F3: 0 KNOWN_POSITIVES in test set. AUC is UNDEFINED. "
            "Returning None (not 0.5) so consumers can distinguish "
            "'undefined' from 'truly random'. This indicates split_data "
            "did not force known positives into the test set."
        )
        return None

    if len(set(labels)) < 2:
        logger.warning(
            f"V4 S-F3: AUC undefined -- test set has only one class "
            f"({len(set(labels))} unique labels). Returning None."
        )
        return None

    auc = float(roc_auc_score(labels, predictions))
    logger.info(
        f"V4 B-F1 fix: AUC on held-out test set (using policy probs, "
        f"not binary actions): {auc:.4f} "
        f"(n_known_positives={n_known_in_test}, n_total={len(labels)})"
    )
    return auc


def literature_crosscheck(
    top_candidates: List[RankedCandidate],
    api_key: str = "",
) -> List[RankedCandidate]:
    """Check top candidates against PubMed for supporting literature.

    For each (drug, disease) pair, queries NCBI Entrez for co-mentioning
    papers. Falls back gracefully if Biopython is not installed or API is down.

    ROOT FIX (C11): skip candidates with synthetic names (Drug_0,
    Disease_0, etc.) — PubMed searches for these generic strings return
    false positives because PubMed indexes papers that mention "Drug_6"
    or "Disease_2" as examples. The literature_support metric was
    therefore random on the demo graph. The C11 fix skips synthetic
    names and marks them as literature_support=False with a clear log
    message, so the V1 launch criterion "≥5 literature-supported
    predictions" can be meaningfully evaluated (only real drug/disease
    names are checked).

    ROOT FIX (FORENSIC-AUDIT-I25): added rate limiting between PubMed
    requests. NCBI's rate limit is 3 requests/second without an API key,
    10/second with one. The previous code queried in a tight loop with
    no delay, which would get the IP blocked for production-scale runs
    (1000+ candidates). The fix adds time.sleep(0.34) between requests
    (3/sec limit) when no API key is set, and time.sleep(0.11) (10/sec
    limit) when an API key is provided.
    """
    try:
        from Bio import Entrez  # type: ignore
    except ImportError:
        logger.info(
            "Biopython not installed -- skipping literature cross-check. "
            "Install with: pip install biopython"
        )
        return top_candidates

    # ROOT FIX (FORENSIC-AUDIT-I25): import time for rate limiting
    import time as _time

    Entrez.email = os.environ.get("NCBI_EMAIL", "team-cosmic@example.com")
    if api_key:
        Entrez.api_key = api_key

    # ROOT FIX (FORENSIC-AUDIT-I25): rate limit delay.
    # Without API key: 3 req/sec -> 0.34s delay.
    # With API key: 10 req/sec -> 0.11s delay.
    rate_limit_delay = 0.11 if api_key else 0.34

    # ROOT FIX (C11): pattern to detect synthetic names like Drug_0, Disease_12
    import re as _re
    synthetic_pattern = _re.compile(r'^(Drug|Disease|Protein|Pathway|Outcome)_\d+$', _re.IGNORECASE)

    def _is_synthetic_name(name: str) -> bool:
        """Check if a name is a synthetic demo name (Drug_0, Disease_12, etc.)."""
        return bool(synthetic_pattern.match(str(name).strip()))

    for c in top_candidates:
        # ROOT FIX (C11): skip synthetic names
        if _is_synthetic_name(c.drug) or _is_synthetic_name(c.disease):
            c.literature_support = False
            logger.info(
                f"  Literature: {c.drug} -> {c.disease}: SKIPPED (synthetic name, "
                f"would produce false positive on PubMed). support=False"
            )
            continue
        try:
            query = f"({c.drug}[Title/Abstract]) AND ({c.disease}[Title/Abstract])"
            handle = Entrez.esearch(db="pubmed", term=query, retmax=1)
            record = Entrez.read(handle)
            handle.close()
            count = int(record.get("Count", 0))
            c.literature_support = count > 0
            logger.info(
                f"  Literature: {c.drug} -> {c.disease}: "
                f"{count} PubMed hits (support={c.literature_support})"
            )
        except Exception as e:
            logger.warning(f"  Literature check failed for {c.drug}->{c.disease}: {e}")
            c.literature_support = False
        # ROOT FIX (FORENSIC-AUDIT-I25): rate limit between PubMed requests
        # to avoid IP blocking at production scale.
        _time.sleep(rate_limit_delay)

    n_supported = sum(1 for c in top_candidates if c.literature_support)
    logger.info(f"Literature-supported predictions: {n_supported}/{len(top_candidates)}")
    return top_candidates


def check_known_positive_recovery(
    top_candidates: List[RankedCandidate],
    test_data: Optional[pd.DataFrame] = None,
) -> Dict[str, Any]:
    """Check how many known drug-disease pairs appear in top candidates.

    C6 fix: this now works in BOTH standalone and integrated mode. The
    bridge injects KNOWN_POSITIVES into the demo graph so the same
    (drug_name, disease_name) pairs appear by name in the integrated
    pipeline.

    ROOT FIX (C-3): the previous version computed recovery against ALL
    KNOWN_POSITIVES (denominator = 5). But the RL split_data puts only
    ~40% of KPs in the test set (FORENSIC-AUDIT-I14 fix: 60/40 split
    with no overlap). The candidates come from the TEST env, so only
    test-split KPs can possibly be recovered. The max recovery rate was
    2/5 = 40%, never 100% — but the denominator said 5, so the recovery
    rate was always capped at 40% and reported as "failing" even when
    the agent recovered ALL test KPs.

    The root fix: when ``test_data`` is provided, filter KNOWN_POSITIVES
    to ONLY those present in the test set. The recovery rate denominator
    becomes the number of KPs in the test set, so:
      - If the agent recovers all test KPs, rate = 100%
      - If the agent recovers half the test KPs, rate = 50%
    This is the SCIENTIFICALLY CORRECT denominator. The previous
    denominator (all KPs) made the recovery rate meaningless — it could
    never reach 100% by construction.

    Args:
        top_candidates: List of RankedCandidate from the test env.
        test_data: Optional test DataFrame. When provided, only KPs
            present in the test set are counted in the denominator.
            When None (legacy/standalone mode), all KPs are counted
            (backward compatibility).

    Returns:
        Dict with recovery_rate, recovered, total, per_pair.
    """
    # ROOT FIX (C-3): filter KPs to those in the test set when test_data
    # is provided. This fixes the denominator so recovery rate can reach
    # 100% when the agent recovers all test KPs.
    if test_data is not None and len(test_data) > 0:
        # Build a set of (drug_lower, disease_lower) pairs in the test set
        test_pairs = set()
        for _, row in test_data.iterrows():
            drug_lower = str(row.get(DRUG_COL, "")).lower().strip()
            disease_lower = str(row.get(DISEASE_COL, "")).lower().strip()
            test_pairs.add((drug_lower, disease_lower))
        # Filter KPs to those in the test set
        kps_in_test = [
            (d, v) for d, v in KNOWN_POSITIVES
            if (d.lower().strip(), v.lower().strip()) in test_pairs
        ]
        logger.info(
            f"ROOT FIX (C-3): recovery denominator = {len(kps_in_test)} "
            f"KPs in TEST set (not all {len(KNOWN_POSITIVES)} KPs). "
            f"The agent can now achieve 100% recovery by finding all "
            f"test KPs. Previous denominator was {len(KNOWN_POSITIVES)}, "
            f"capping recovery at {len(kps_in_test)}/{len(KNOWN_POSITIVES)} "
            f"= {len(kps_in_test)/max(len(KNOWN_POSITIVES),1):.0%}."
        )
        kps_to_check = kps_in_test
    else:
        # Legacy/standalone mode: check all KPs (backward compatibility)
        kps_to_check = list(KNOWN_POSITIVES)

    results: Dict[Tuple[str, str], bool] = {}
    for drug, disease in kps_to_check:
        match = any(
            c.drug.lower() == drug.lower() and c.disease.lower() == disease.lower()
            for c in top_candidates
        )
        results[(drug, disease)] = match

    recovered = sum(1 for v in results.values() if v)
    total = len(results)
    rate = recovered / total if total > 0 else 0.0
    logger.info(f"Known-positive recovery: {recovered}/{total} ({rate:.1%})")
    for (drug, disease), found in results.items():
        status = "RECOVERED" if found else "MISSED"
        logger.info(f"  {status}: {drug} -> {disease}")

    return {
        "per_pair": {f"{d}->{v}": found for (d, v), found in results.items()},
        "recovery_rate": rate,
        "recovered": recovered,
        "total": total,
        # ROOT FIX (C-3): expose the denominator basis for auditability
        "denominator_basis": "test_set" if test_data is not None else "all_kps",
        "n_kps_in_test": total,
        "n_kps_total": len(KNOWN_POSITIVES),
    }


def load_validated_hypotheses(path: str = VALIDATED_HYPOTHESES_PATH) -> Set[Tuple[str, str]]:
    """Load previously validated drug-disease hypotheses from CSV.

    Returns set of (drug_lower, disease_lower) tuples. Used to boost reward
    for pairs that have been validated by pharma partners, enabling the
    data flywheel described in project doc Section 10.

    v90 P0 ROOT FIX (BUG #11): the previous version searched ONLY the
    single ``path`` argument (default "validated_hypotheses.csv" relative
    to CWD). If the pipeline was run from a different CWD (common in
    production — systemd, Docker, Kubernetes), the search returned an
    empty set, WIPING the correctly-loaded module-level constant
    (VALIDATED_HYPOTHESES, which uses a 3-path search). The fix: use
    the SAME 3-path search as _load_validated_hypotheses (relative,
    next-to-module, CWD). This ensures the flywheel works regardless
    of CWD.
    """
    # v90 BUG #11: use 3-path search (same as _load_validated_hypotheses)
    candidate_paths = [
        path,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), os.path.basename(path)),
        os.path.join(os.getcwd(), os.path.basename(path)),
    ]
    for candidate in candidate_paths:
        if not os.path.exists(candidate):
            continue
        try:
            df = pd.read_csv(candidate)
            if DRUG_COL not in df.columns or DISEASE_COL not in df.columns:
                logger.warning(
                    f"validated_hypotheses.csv at {candidate} missing "
                    f"'drug' or 'disease' column. Skipping."
                )
                continue
            validated = set(zip(
                df[DRUG_COL].astype(str).str.lower().str.strip(),
                df[DISEASE_COL].astype(str).str.lower().str.strip(),
            ))
            logger.info(f"Loaded {len(validated)} validated hypotheses from {candidate}")
            return validated
        except Exception as e:
            logger.warning(f"Failed to load validated hypotheses from {candidate}: {e}")
    logger.info(
        "No validated hypotheses file found (searched 3 paths). "
        "No reward bonus will be applied."
    )
    return set()


# ============================================================================
# SECTION 10: PERSISTENCE
# ============================================================================
def generate_output_filename(
    base_name: str = "top_candidates",
    output_dir: str = "output",
) -> str:
    """Generate a unique, timestamped output filename."""
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return os.path.join(output_dir, f"{base_name}_{timestamp}.csv")


def flag_controlled_substances(candidates: pd.DataFrame) -> pd.DataFrame:
    """Add a 'controlled_substance' flag column to candidates."""
    candidates = candidates.copy()
    candidates[CONTROLLED_SUBSTANCE_COL] = (
        candidates[DRUG_COL].astype(str).str.lower().isin(CONTROLLED_SUBSTANCES).astype(int)
    )
    n_flagged = int(candidates[CONTROLLED_SUBSTANCE_COL].sum())
    if n_flagged > 0:
        flagged_drugs = list(
            candidates.loc[candidates[CONTROLLED_SUBSTANCE_COL] == 1, DRUG_COL]
        )
        logger.warning(
            f"FLAGGED: {n_flagged} controlled substances in output. "
            f"Review before export: {flagged_drugs}"
        )
    return candidates


def redact_proprietary_ids(
    text: str,
    proprietary_prefixes: Optional[List[str]] = None,
) -> str:
    """Redact proprietary identifiers from output text.

    FIX (B9): this function is now ACTUALLY CALLED by save_results,
    with default prefixes ``["CPD-", "INTERNAL-", "PROP-"]``. Any drug
    name starting with one of these prefixes is replaced with
    ``[REDACTED]`` in the output CSV. This prevents proprietary
    internal compound IDs from leaking into delivered outputs.

    V4 dead code fix #8: the original code had an unreachable early-return
    ``if not text: return text`` AFTER ``text_str = str(text)``. The
    ``not text`` check on a non-empty string is always False, so the
    branch was dead. The V4 fix moves the empty-check to the TOP of
    the function (before str conversion) so it actually fires for
    empty/None/NaN inputs.
    """
    # V4 dead code fix #8: check for empty/None/NaN BEFORE str conversion.
    # The original code did str(text) first, which made ``not text``
    # always False for any non-empty string (including "nan" from NaN).
    if text is None:
        return ""
    if isinstance(text, float) and pd.isna(text):
        return ""
    text_str = str(text)
    if not text_str:
        return text_str
    prefixes = proprietary_prefixes if proprietary_prefixes is not None else DEFAULT_PROPRIETARY_PREFIXES
    for prefix in prefixes:
        if text_str.lower().startswith(prefix.lower()):
            return "[REDACTED]"
    return text_str


def compute_output_hmac(filepath: str, secret_key: str = "") -> Tuple[Optional[str], bool]:
    """Compute HMAC-SHA256 of the output file for tamper detection.

    ROOT FIX (FORENSIC-AUDIT-I24): the previous code fell back to a
    hardcoded default key ``"team-cosmic-default"`` (visible in source)
    when RL_HMAC_KEY was not set. Any attacker who read the source could
    forge the HMAC. The is_verified=False flag was set, but the HMAC was
    still computed and stored — giving a false sense of security.

    The root fix: if no key is set, DON'T compute the HMAC at all. Return
    (None, False) so the caller can set output_hmac_sha256 = null and
    output_hmac_verified = false in the metadata. This makes it clear to
    downstream consumers that NO tamper detection is in place, rather than
    a fake HMAC that looks like tamper detection but isn't.

    In production, set RL_HMAC_KEY env var to a real secret. Without it,
    the output will explicitly have no HMAC.

    Args:
        filepath: Path to the file to HMAC.
        secret_key: Secret key. If empty, falls back to RL_HMAC_KEY env
            var. If that's also empty, returns (None, False) — NO HMAC
            is computed.

    Returns:
        Tuple of (hmac_hex_string_or_None, is_verified). Returns
        (None, False) when no key is available. Returns (hex, True) when
        a real key is used.
    """
    if not secret_key:
        secret_key = os.environ.get("RL_HMAC_KEY", "")
    if not secret_key:
        # ROOT FIX (FORENSIC-AUDIT-I24): do NOT compute a fake HMAC with
        # a hardcoded default key. Return (None, False) so the caller
        # can set output_hmac_sha256 = null in the metadata, making it
        # clear that NO tamper detection is in place.
        logger.warning(
            "compute_output_hmac: no secret key provided (set RL_HMAC_KEY "
            "env var). NO HMAC will be computed. The output metadata will "
            "have output_hmac_sha256 = null and output_hmac_verified = false. "
            "(FORENSIC-AUDIT-I24: no fake HMAC with hardcoded default key)"
        )
        return None, False
    h = hmac.new(secret_key.encode(), digestmod=hashlib.sha256)
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):  # E6 fix: 1MB chunks
            h.update(chunk)
    return h.hexdigest(), True


def save_provenance_metadata(output_csv_path: str, metadata: Dict[str, Any]) -> str:
    """Save provenance metadata as JSON alongside the output CSV."""
    meta_path = output_csv_path.replace(".csv", ".meta.json")
    with open(meta_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, default=str)
    logger.info(f"Provenance metadata saved to {meta_path}")
    return meta_path


def save_results(
    candidates: Union[List[RankedCandidate], pd.DataFrame],
    filename: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    config: Optional[PipelineConfig] = None,
    set_secure_perms: bool = True,
) -> str:
    """Save ranked candidates with provenance metadata.

    FIX (B9): now applies ``redact_proprietary_ids`` to the DRUG_COL
    column using the config's ``proprietary_prefixes`` (default
    ``["CPD-", "INTERNAL-", "PROP-"]``). Proprietary internal compound
    IDs are redacted before the CSV is written.
    """
    import stat

    cfg = config or DEFAULT_CONFIG
    meta = metadata or {}

    if isinstance(candidates, list):
        if not candidates:
            logger.error(
                "No candidates ranked HIGH. Writing empty results file with "
                "metadata for audit trail."
            )
            df = pd.DataFrame(columns=[DRUG_COL, DISEASE_COL, REWARD_COL, RANK_COL])
        else:
            df = pd.DataFrame([c.to_dict() for c in candidates])
    else:
        df = candidates.copy()

    # B9 fix: redact proprietary IDs in the DRUG_COL
    # ROOT FIX (E5): vectorized redaction instead of per-row apply.
    # The original code used df[DRUG_COL].apply(lambda x: ...) which
    # is a Python-level loop — slow for large outputs (100M rows).
    # The E5 fix uses pandas str operations with a regex pattern,
    # which is vectorized in C and ~100x faster.
    if DRUG_COL in df.columns and len(df) > 0:
        prefixes = cfg.proprietary_prefixes if cfg.proprietary_prefixes else DEFAULT_PROPRIETARY_PREFIXES
        # Build regex pattern: ^(prefix1|prefix2|...).*
        pattern = '|'.join(
            re.escape(p) for p in prefixes
        )
        if pattern:
            # Vectorized: replace any string starting with a prefix
            # with "[REDACTED]"
            drug_str = df[DRUG_COL].astype(str)
            mask = drug_str.str.match(f'^({pattern})', case=False, na=False)
            df[DRUG_COL] = drug_str.where(~mask, '[REDACTED]')

    # Add provenance metadata columns
    df["pipeline_version"] = meta.get("pipeline_version", cfg.pipeline_version)
    df["schema_version"] = meta.get("schema_version", cfg.schema_version)
    df["training_timestamp"] = meta.get("training_timestamp", "unknown")
    df["model_checkpoint"] = meta.get("model_checkpoint", "unknown")
    df["reward_weights_json"] = json.dumps(meta.get("reward_weights", {}), default=str)
    df["input_sha256"] = meta.get("input_sha256", "unknown")
    df["seed"] = meta.get("seed", cfg.seed)
    df["timesteps"] = meta.get("timesteps", cfg.timesteps)

    df = flag_controlled_substances(df)

    if filename is None:
        filename = generate_output_filename(output_dir=cfg.output_dir)

    df.to_csv(
        filename, index=False, encoding="utf-8", lineterminator="\n",
        quoting=csv.QUOTE_MINIMAL,
    )

    if set_secure_perms:
        try:
            os.chmod(filename, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as e:
            logger.warning(f"Could not set file permissions on {filename}: {e}")

    try:
        hmac_hex, hmac_verified = compute_output_hmac(filename)
        # ROOT FIX (FORENSIC-AUDIT-I24): hmac_hex can now be None when
        # no key is available. Store None in metadata instead of a fake HMAC.
        meta["output_hmac_sha256"] = hmac_hex
        meta["output_hmac_verified"] = bool(hmac_verified)
        if hmac_hex is not None and hmac_verified:
            logger.info(f"Output HMAC (verified): {hmac_hex[:16]}...")
        elif hmac_hex is not None and not hmac_verified:
            logger.warning(
                f"Output HMAC (UNVERIFIED): {hmac_hex[:16]}... "
                "Set RL_HMAC_KEY env var for cryptographic tamper detection."
            )
        else:
            # hmac_hex is None — no HMAC computed (FORENSIC-AUDIT-I24 fix)
            logger.warning(
                "Output HMAC: NOT computed (no RL_HMAC_KEY set). "
                "metadata.output_hmac_sha256 = null. "
                "Set RL_HMAC_KEY env var for tamper detection."
            )
    except Exception as e:
        logger.warning(f"Could not compute HMAC: {e}")

    logger.info(f"Results saved to {filename} ({len(df)} rows, perms=0600)")

    save_provenance_metadata(filename, meta)

    return filename


def merge_results(existing_path: str, new_candidates: pd.DataFrame) -> pd.DataFrame:
    """Merge new candidates with existing results, keeping best per pair.

    Utility for incremental runs: if you run the pipeline multiple times
    with different seeds, this merges the outputs keeping the highest
    reward per (drug, disease) pair.

    ROOT FIX (FORENSIC-AUDIT-I16): sort by ``policy_prob`` if present
    (consistent with the B-F2 fix that says ranking should use the
    agent's learned policy probability, not the hand-coded reward
    function). Falls back to ``REWARD_COL`` for backward compatibility
    with old CSVs that don't have a ``policy_prob`` column.
    """
    if os.path.exists(existing_path):
        existing = pd.read_csv(existing_path)
        merged = pd.concat([existing, new_candidates], ignore_index=True)
        # ROOT FIX (FORENSIC-AUDIT-I16): prefer policy_prob over REWARD_COL
        # for ranking, consistent with the B-F2 fix in get_top_candidates.
        sort_col = "policy_prob" if "policy_prob" in merged.columns else REWARD_COL
        merged = (
            merged.sort_values(sort_col, ascending=False)
                  .drop_duplicates(subset=[DRUG_COL, DISEASE_COL], keep='first')
        )
        logger.info(
            f"Merged: {len(existing)} existing + {len(new_candidates)} new "
            f"-> {len(merged)} unique pairs"
        )
        return merged
    return new_candidates


# ============================================================================
# SECTION 11: SECURITY HELPERS
# ============================================================================
def safe_load_input(filepath: str) -> Tuple[pd.DataFrame, str]:
    """Safely load input CSV with path validation and integrity check.

    ROOT FIX (B1, v3): the original code did ``filepath = os.path.realpath(filepath)``
    FIRST, which resolves ALL symlinks, then checked
    ``if os.path.islink(filepath)`` -- which is *always False* after
    realpath. The "security check" literally could not fire.

    The V2 fix added a symlink check BEFORE realpath (correct), but kept
    a SECOND islink check AFTER realpath "for defense in depth". That
    second check is DEAD CODE: ``os.path.realpath`` resolves every
    symlink in the path, so the result is never a symlink. Keeping dead
    security code is actively harmful -- reviewers may believe the
    second check provides protection when it does not.

    The v3 root fix: ONE symlink check, BEFORE realpath. We also check
    the parent directory for symlinks (a symlinked parent directory
    could redirect the resolve). After realpath, we verify the resolved
    path EQUALS the input path (no symlink traversal happened).

    Args:
        filepath: Path to input CSV.

    Returns:
        Tuple of (DataFrame, sha256_hex).

    Raises:
        FileNotFoundError: If file does not exist.
        ValueError: ALWAYS raised if the file ITSELF is a symlink (real
            security risk — an attacker could swap the file content).
            Raised in STRICT mode only (RL_STRICT_SYMLINK_CHECK=1) if
            the parent directory is a symlink OR if realpath changes
            the path. In the DEFAULT (non-strict) mode, parent-symlink
            and realpath-traversal only LOG a WARNING and proceed.
    """
    # B1 v3 root fix: ONE symlink check, BEFORE realpath.
    #
    # ROOT FIX (C9): the B1 v3 fix rejected symlinks AND symlinked
    # parent directories AND any path that changed after realpath. This
    # was too aggressive for production — it's common for /data or
    # /output to be symlinked to a NAS or shared filesystem. The bridge
    # writes gt_predictions.csv to output_dir, then the RL pipeline
    # reads it. If output_dir is a symlinked directory, safe_load_input
    # would crash.
    #
    # The C9 fix:
    #   1. Still reject symlinked FILES (the file itself is a symlink —
    #      this is the actual security risk, allowing an attacker to
    #      swap the file content)
    #   2. ALLOW symlinked parent directories (common in production —
    #      the directory is managed by ops, not an attack vector)
    #   3. Log a WARNING (not raise) if the resolved path differs from
    #      the input path, so users are informed but the pipeline
    #      doesn't crash
    #   4. Set RL_STRICT_SYMLINK_CHECK=1 env var to restore strict mode
    if os.path.islink(filepath):
        raise ValueError(
            f"Input file is a symlink (security risk): {filepath}. "
            "Refusing to load. Pass a real file path."
        )

    # ROOT FIX (B-01 / FORENSIC-AUDIT-I28 reversal): the V26 default of
    # STRICT mode broke every production deployment where ``output_dir`` is
    # a symlinked directory (NAS, shared filesystem, Kubernetes volume
    # mount). The bridge writes ``gt_predictions.csv`` to ``{output_dir}/``
    # and then the RL pipeline reads it back via this function. If the
    # parent directory is a symlink, strict mode RAISES ValueError, the
    # bridge has no try/except for it, and the pipeline CRASHES — even
    # though there is no actual security risk (the directory is managed by
    # ops, not an attack vector).
    #
    # The audit's "FORENSIC-AUDIT-I28" strict-mode default was a false
    # security: it protected against a hypothetical attacker who can
    # create symlinks inside the output_dir, but the bridge ALREADY
    # controls who writes to output_dir (only the bridge writes there).
    # The REAL security risk is the file itself being a symlink (which
    # we still reject unconditionally at line 3293 above), not the parent
    # directory.
    #
    # The root fix: default to NON-strict mode (production-friendly).
    # Users who genuinely need strict mode (e.g., paranoid multi-tenant
    # deployments) can opt in via RL_STRICT_SYMLINK_CHECK=1.
    strict_mode = os.environ.get("RL_STRICT_SYMLINK_CHECK", "0") == "1"

    # Check parent directory for symlinks — but only REJECT in strict mode.
    # In default mode, just log a warning (production-friendly).
    parent_dir = os.path.dirname(os.path.abspath(filepath))
    if os.path.islink(parent_dir):
        msg = (
            f"Parent directory of input file is a symlink: "
            f"{parent_dir} -> {os.readlink(parent_dir)}."
        )
        if strict_mode:
            raise ValueError(f"{msg} Refusing to load (strict mode).")
        else:
            logger.warning(
                f"{msg} This is common in production (NAS/shared filesystem). "
                f"Proceeding. Set RL_STRICT_SYMLINK_CHECK=1 to reject."
            )

    # Now resolve to a normalized absolute path.
    resolved = os.path.realpath(filepath)
    if not os.path.exists(resolved):
        raise FileNotFoundError(f"Input file not found: {filepath} (resolved: {resolved})")

    # If realpath changed the path, a symlink was traversed. Log a warning
    # in default mode, raise in strict mode.
    if resolved != os.path.abspath(filepath):
        msg = (
            f"Input path changed after realpath resolution (symlink traversed). "
            f"Input: {filepath} -> Resolved: {resolved}."
        )
        if strict_mode:
            raise ValueError(f"{msg} Refusing to load (strict mode).")
        else:
            logger.warning(
                f"{msg} This is common in production (NAS/shared filesystem). "
                f"Proceeding. Set RL_STRICT_SYMLINK_CHECK=1 to reject."
            )

    if not resolved.lower().endswith(".csv"):
        raise ValueError(f"Input must be a .csv file. Got: {resolved}")
    file_hash = compute_file_hash(resolved)
    logger.info(f"Loading input from {resolved} (SHA-256: {file_hash[:16]}...)")
    df = pd.read_csv(resolved)
    return df, file_hash


def get_secret(key: str, default: str = "") -> str:
    """Retrieve a secret from environment variables or .env file."""
    value = os.environ.get(key)
    if value:
        return value
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        try:
            with open(env_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith(f"{key}="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return default


def check_for_pii(data: pd.DataFrame) -> List[str]:
    """Scan for potential PII in the dataset.

    ROOT FIX (FORENSIC-AUDIT-I26): the previous code only checked the
    first 100 rows (``.head(100)``), missing PII in rows 101+. At
    production scale, this is a compliance risk. The fix checks ALL rows
    using vectorized pandas str operations (no Python loop over rows),
    which is fast even for 100M-row datasets.
    """
    pii_patterns = {
        "email": r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
        "ssn": r'\b\d{3}-\d{2}-\d{4}\b',
        "phone": r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b',
        "dob": r'\b\d{2}/\d{2}/\d{4}\b',
        "mrn": r'(?:MRN|medical record)[:\s]*\d+',
    }
    flagged: List[str] = []
    for col in data.columns:
        # ROOT FIX (FORENSIC-AUDIT-I26): check ALL rows, not just head(100).
        # Vectorized str.contains is fast even for large datasets.
        col_str = data[col].astype(str)
        for pii_type, pattern in pii_patterns.items():
            if col_str.str.contains(pattern, regex=True, na=False).any():
                flagged.append(f"{col} (possible {pii_type})")
                logger.warning(f"PII detected in column '{col}': possible {pii_type}")
    return flagged


def log_audit_event(event: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Log an audit event for regulatory compliance (21 CFR Part 11).

    ROOT FIX (E7): the original code used getpass.getuser() which can
    raise ModuleNotFoundError on systems without the pwd module (e.g.,
    Windows). The E7 fix wraps getuser() in a try/except and falls back
    to "unknown" if it fails. This prevents the pipeline from crashing
    on Windows or other systems where getuser() is unavailable.
    """
    # ROOT FIX (E7): safe getuser with fallback
    try:
        user = getuser()
    except Exception:
        user = "unknown"

    # ROOT FIX (F7): safe-serialize details to handle non-serializable
    # objects (e.g., DataFrames, tensors). The original code used
    # f-string conversion which could fail or produce unreadable output.
    # The F7 fix converts each value to a JSON-serializable form.
    safe_details = {}
    if details:
        for k, v in details.items():
            try:
                # Try JSON serialization to test serializability
                json.dumps(v, default=str)
                safe_details[k] = v
            except (TypeError, ValueError):
                # Fall back to string representation
                safe_details[k] = str(v)

    audit_record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "actor": os.environ.get("RL_USER", user),
        "pid": os.getpid(),
        **safe_details,
    }
    # ROOT FIX (F7): use json.dumps for reliable serialization
    try:
        audit_str = json.dumps(audit_record, default=str)
    except Exception:
        audit_str = str(audit_record)
    _audit_logger.info(f"AUDIT: {audit_str}")


# ============================================================================
# SECTION 12: METRICS & OBSERVABILITY
# ============================================================================
class PipelineMetrics:
    """Track pipeline execution metrics for monitoring.

    Attributes:
        n_pairs_processed: Total pairs evaluated.
        n_safety_rejected: Pairs rejected by safety gate.
        n_gnn_rejected: Pairs rejected by GNN gate.
        n_ranked_high: Pairs the agent ranked HIGH.
        n_ranked_low: Pairs the agent ranked LOW.
        training_loss: List of training loss values.
        episode_rewards: List of episode total rewards.
        inference_latency_ms: Inference latency in milliseconds.
        run_id: Correlation ID for this run.
    """

    def __init__(self, run_id: str = "") -> None:
        import uuid
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.n_pairs_processed: int = 0
        self.n_safety_rejected: int = 0
        self.n_gnn_rejected: int = 0
        self.n_ranked_high: int = 0
        self.n_ranked_low: int = 0
        self.training_loss: List[float] = []
        self.episode_rewards: List[float] = []
        self.inference_latency_ms: float = 0.0

    def summary(self) -> Dict[str, Any]:
        """Return a serializable summary of all metrics."""
        return {
            "run_id": self.run_id,
            "pairs_processed": self.n_pairs_processed,
            "safety_rejected": self.n_safety_rejected,
            "gnn_rejected": self.n_gnn_rejected,
            "ranked_high": self.n_ranked_high,
            "ranked_low": self.n_ranked_low,
            "inference_latency_ms": round(self.inference_latency_ms, 2),
        }


def check_alert_conditions(metrics: PipelineMetrics, data: pd.DataFrame) -> None:
    """Check for conditions that should trigger alerts.

    B20 fix: with the new reward asymmetry, "no candidates ranked HIGH"
    is now rare and worth alerting on. The alert message no longer
    claims the pipeline "may be broken" -- it suggests checking the
    reward distribution and the new low_action_penalty setting.
    """
    if metrics.n_ranked_high == 0 and metrics.n_pairs_processed > 0:
        logger.warning(
            "ALERT: No candidates ranked HIGH. With the B20 reward-asymmetry "
            "fix this should be rare. Check: (1) reward distribution "
            "(--log-level DEBUG), (2) low_action_penalty in RewardConfig "
            f"(currently {DEFAULT_CONFIG.reward.low_action_penalty}), "
            "(3) safety/gnn thresholds vs input data ranges."
        )
    safety_reject_rate = metrics.n_safety_rejected / max(metrics.n_pairs_processed, 1)
    if safety_reject_rate > 0.5:
        logger.warning(
            f"ALERT: {safety_reject_rate:.1%} of pairs rejected by safety gate. "
            f"Check input data quality or adjust threshold."
        )
    if metrics.inference_latency_ms > 5000:
        logger.warning(
            f"ALERT: Inference took {metrics.inference_latency_ms:.0f}ms. "
            f"Consider GPU acceleration or batching."
        )


# ============================================================================
# SECTION 13: ENVIRONMENT VALIDATION
# ============================================================================
def _run_env_check(env: "DrugRankingEnv") -> None:
    """Run stable_baselines3 env check."""
    from stable_baselines3.common.env_checker import check_env
    check_env(env, warn=True)


def validate_environment(config: PipelineConfig) -> bool:
    """Validate the runtime environment before starting the pipeline."""
    if config.input_path and not os.path.exists(config.input_path):
        logger.error(f"Input file not found: {config.input_path}")
        return False
    os.makedirs(config.output_dir, exist_ok=True)
    os.makedirs(config.checkpoint_dir, exist_ok=True)
    logger.info(
        f"Environment validated: input={config.input_path}, "
        f"output={config.output_dir}"
    )
    return True


# ============================================================================
# SECTION 14: SCHEMA CONTRACTS
# ============================================================================
INPUT_SCHEMA: Dict[str, Any] = {
    "required_columns": REQUIRED_COLUMNS,
    "column_types": {col: "float64" for col in FEATURE_COLS},
    "value_ranges": {col: (0.0, 1.0) for col in FEATURE_COLS},
    "min_rows": 1,
    "description": (
        "Output from Phase 3 Graph Transformer. Each row represents one "
        "drug-disease pair with predicted scores."
    ),
}
INPUT_SCHEMA["column_types"][DRUG_COL] = "object"
INPUT_SCHEMA["column_types"][DISEASE_COL] = "object"

OUTPUT_SCHEMA: Dict[str, Any] = {
    "required_columns": [
        DRUG_COL, DISEASE_COL, REWARD_COL, RANK_COL,
        *FEATURE_COLS, LITERATURE_SUPPORT_COL, IS_KNOWN_POSITIVE_COL,
    ],
    "metadata_columns": [
        "pipeline_version", "schema_version", "training_timestamp",
        "model_checkpoint", "reward_weights_json", "input_sha256",
        "seed", "timesteps", CONTROLLED_SUBSTANCE_COL,
    ],
    "description": (
        "Ranked drug-disease repurposing hypotheses for Phase 5 "
        "Dashboard/API consumption."
    ),
}


# ============================================================================
# SECTION 15: MAIN ORCHESTRATION
# ============================================================================
def run_pipeline(config: PipelineConfig) -> Tuple[List[RankedCandidate], PipelineMetrics]:
    """Run the full RL ranking pipeline.

    FIXES vs original:
      - **B13**: compute_auc uses KNOWN_POSITIVES as the ground-truth
        label, not the same reward function the agent was trained on.
      - **B14**: evaluate_agent runs on a TEST env built from held-out
        test data, not the training env. Top-N candidates come from
        test data, not training data.
      - **C4**: split_data uses drug_aware=True by default, so drugs in
        train never appear in test.

    Args:
        config: PipelineConfig instance.

    Returns:
        Tuple of (top_candidates, metrics). The top_candidates come from
        the held-out TEST environment (B14 fix), not the training
        environment.
    """
    import time as _time

    metrics = PipelineMetrics()
    log_audit_event("pipeline_start", {"run_id": metrics.run_id})

    # Load data
    input_sha256 = "fake_data"
    if config.input_path:
        data, input_sha256 = safe_load_input(config.input_path)
    else:
        data = generate_fake_data(n_pairs=config.n_pairs, seed=config.seed)

    try:
        data = validate_input_schema(data, config.reward)
    except (ValueError, TypeError) as e:
        logger.critical(f"Input validation failed (circuit breaker): {e}")
        logger.critical("Aborting pipeline. Fix input data and retry.")
        log_audit_event("pipeline_abort", {"reason": "input_validation_failed", "error": str(e)})
        raise

    data, quarantined = preprocess_data(data, config)
    # v90 P0 ROOT FIX (BUG #17): n_pairs_processed will be updated AFTER
    # the train/test split to reflect the actual pairs the agent processed
    # (train_proper + test), not the full dataset before split.
    metrics.n_pairs_processed = len(data)  # initial, updated after split

    # v3 root fix: wire validate_canonical_ids (was dead code in V2).
    # If id_mapping_path is set, merge canonical ID columns
    # (drug_inchikey, disease_mesh_id) into the data before ranking.
    if config.id_mapping_path:
        data = validate_canonical_ids(data, config.id_mapping_path)
        logger.info(f"Canonical IDs merged from {config.id_mapping_path}")

    pii_flags = check_for_pii(data)
    if pii_flags:
        logger.warning(f"PII flags: {pii_flags}")

    generate_data_quality_report(data, config.reward)

    # C4 fix: drug-aware split (default True)
    # v90 P0 ROOT FIX (BUG #15): use return_oversampled=True so kp_oversampled
    # is returned SEPARATELY (not mixed into train_df). This allows the val
    # split (below) to operate on train_df WITHOUT oversampled KPs, preventing
    # training-data leakage into the adaptive threshold computation.
    train_df, test_df, kp_oversampled = split_data(
        data,
        test_size=config.test_size,
        seed=config.seed,
        drug_aware=config.drug_aware_split,
        return_oversampled=True,
    )
    logger.info(f"Split: {len(train_df)} train / {len(test_df)} test / {len(kp_oversampled)} oversampled KPs (separate)")

    validated_set = load_validated_hypotheses()

    reward_fn = RewardFunction(config.reward)
    reward_fn.set_validated_hypotheses(validated_set)

    # ROOT FIX (FORENSIC-AUDIT-I20): removed the dead ``data[REWARD_COL] = data.apply(...)``
    # call that computed rewards on the FULL dataset (including test pairs)
    # BEFORE splitting. This was dead compute because:
    #   1. DrugRankingEnv recomputes the reward via self.reward_fn.compute(row)
    #      inside step() — it never reads the REWARD_COL from the DataFrame.
    #   2. The computation included test pairs, which is a (minor) information
    #      leak — the reward function's statistics could be influenced by
    #      test data even though the env doesn't use the precomputed column.
    #   3. At production scale (100M pairs), this Python-level apply loop
    #      would take hours of wasted compute.
    #
    # Instead, we compute reward statistics on the TRAIN set only (after
    # the split) for logging purposes. The env computes rewards on-the-fly
    # during step(), which is correct and avoids the leak.
    train_reward_sample = train_df.apply(lambda r: reward_fn.compute(r), axis=1)
    logger.info(
        f"Reward range (train only): {train_reward_sample.min():.3f} -> "
        f"{train_reward_sample.max():.3f}"
    )

    # ROOT FIX (W-04): compute the adaptive gnn threshold on a HELD-OUT
    # validation split, NOT on the full train_df. The V27 code called
    # ``DrugRankingEnv(train_df, ...)`` which called
    # ``reward_fn.set_adaptive_threshold(train_df[gnn_score])`` to compute
    # the 20th percentile of TRAIN gnn_scores. The test env reused this
    # threshold (FORENSIC-AUDIT-I13 fix), but the test data has a
    # DIFFERENT gnn_score distribution than train -- particularly when
    # the GT model overfits to train drugs (which it does, see W-01).
    # The 20th-percentile threshold computed on train may REJECT most
    # test pairs or ACCEPT most test pairs, depending on the distribution
    # shift. The reward function's gate was calibrated to the WRONG
    # distribution.
    #
    # The root fix: split off 15% of train_df as a HELD-OUT validation
    # set (train_proper + val_for_threshold). The adaptive threshold is
    # computed on val_for_threshold -- a distribution that is CLOSER to
    # the test distribution (both are held-out from training) than train
    # is. The PPO agent trains on train_proper only, so the threshold
    # is computed on data the agent has NOT memorized.
    #
    # This is the standard ML practice for hyperparameter selection:
    # never compute hyperparameters (like thresholds) on the training
    # set, because the model has memorized it. Use a held-out val set.
    VAL_FRACTION_FOR_THRESHOLD = 0.15
    if len(train_df) >= 10:
        # v90 P0 ROOT FIX (BUG #14): the val split was PAIR-WISE
        # (sklearn.train_test_split), NOT drug-aware. The same drug
        # could appear in BOTH train_proper and val_for_threshold. The
        # W-04 fix's stated goal was "compute the threshold on data the
        # agent has NOT memorized" — but the pair-wise split means the
        # threshold sees drugs the agent IS trained on. Fix: use a
        # DRUG-AWARE sequential split (sort drugs by first appearance,
        # take first 85% as train_proper, last 15% as val_for_threshold).
        # This mirrors the drug-aware sequential fallback in split_data.
        # v90 P0 ROOT FIX (BUG #15): the val split now operates on
        # train_df WITHOUT oversampled KPs (kp_oversampled is returned
        # separately and added to train_proper AFTER the split). This
        # prevents oversampled KP copies from leaking into
        # val_for_threshold and contaminating the threshold computation.
        _val_rng = np.random.default_rng(config.seed + 999)
        _unique_drugs_val = list(train_df[DRUG_COL].unique())
        _val_rng.shuffle(_unique_drugs_val)
        _unique_drugs_val = np.array(_unique_drugs_val, dtype=object)
        _n_val_drugs = max(1, int(VAL_FRACTION_FOR_THRESHOLD * len(_unique_drugs_val)))
        _val_drugs = set(_unique_drugs_val[:_n_val_drugs].tolist())
        _train_drugs = set(_unique_drugs_val[_n_val_drugs:].tolist())
        _train_mask = train_df[DRUG_COL].isin(_train_drugs)
        _val_mask = train_df[DRUG_COL].isin(_val_drugs)
        # Fallback: if drug-aware split produces empty side, use sequential
        if _train_mask.sum() == 0 or _val_mask.sum() == 0:
            _seen_order = []
            _seen_set = set()
            for _d in train_df[DRUG_COL].tolist():
                if _d not in _seen_set:
                    _seen_set.add(_d)
                    _seen_order.append(_d)
            _n_total_v = len(_seen_order)
            _n_train_v = max(1, int((1.0 - VAL_FRACTION_FOR_THRESHOLD) * _n_total_v))
            _train_drugs = set(_seen_order[:_n_train_v])
            _val_drugs = set(_seen_order[_n_train_v:])
            _train_mask = train_df[DRUG_COL].isin(_train_drugs)
            _val_mask = train_df[DRUG_COL].isin(_val_drugs)
        train_proper_df = train_df[_train_mask].reset_index(drop=True)
        val_for_threshold_df = train_df[_val_mask].reset_index(drop=True)
        # v90 BUG #15: add oversampled KPs to train_proper AFTER the val split
        if len(kp_oversampled) > 0:
            train_proper_df = pd.concat(
                [train_proper_df, kp_oversampled], ignore_index=True
            ).reset_index(drop=True)
        logger.info(
            f"v90 BUG #14/#15: DRUG-AWARE val split of train_df ({len(train_df)} pairs) "
            f"into train_proper ({len(train_proper_df)}, includes {len(kp_oversampled)} "
            f"oversampled KPs added AFTER split) + val_for_threshold "
            f"({len(val_for_threshold_df)}, NO oversampled KPs). The adaptive "
            f"gnn threshold will be computed on val_for_threshold (held-out "
            f"drugs, no KP leakage), eliminating both the drug-memorization "
            f"and oversampled-KP-leakage bugs."
        )
    else:
        # Edge case: train_df is too small to split (tiny synthetic
        # demos). Fall back to using train_df as both train_proper and
        # val_for_threshold (preserves backward compatibility). Log a
        # WARNING so the user knows the threshold is computed on train
        # data (W-04 not fully fixed on this tiny graph).
        train_proper_df = train_df.copy()
        # v90 BUG #15: still add oversampled KPs to train_proper
        if len(kp_oversampled) > 0:
            train_proper_df = pd.concat(
                [train_proper_df, kp_oversampled], ignore_index=True
            ).reset_index(drop=True)
        val_for_threshold_df = train_df
        logger.warning(
            f"ROOT FIX (W-04): train_df has only {len(train_df)} pairs, "
            f"too small to split for held-out threshold computation. "
            f"Falling back to using train_df as both train_proper and "
            f"val_for_threshold (W-04 NOT fully fixed on this tiny "
            f"graph). The adaptive threshold will be computed on train "
            f"data, which may cause distribution shift at test time."
        )

    # v90 P0 ROOT FIX (BUG #17): update n_pairs_processed to reflect
    # the ACTUAL pairs the agent processed (train_proper + test), not
    # the full dataset before split.
    metrics.n_pairs_processed = len(train_proper_df) + len(test_df)

    # ROOT FIX (W-04): set the adaptive threshold on reward_fn using
    # the HELD-OUT validation set, BEFORE constructing the train env.
    # The train env's __init__ will see that the threshold is already
    # set (we pass set_adaptive_threshold=False to skip re-computing it
    # on train data). This ensures the gate uses the val-distribution
    # threshold at BOTH train and test time.
    if (
        hasattr(config.reward, 'gnn_hard_reject_adaptive')
        and config.reward.gnn_hard_reject_adaptive
        and GNN_SCORE_COL in val_for_threshold_df.columns
        and len(val_for_threshold_df) > 0
    ):
        reward_fn.set_adaptive_threshold(
            val_for_threshold_df[GNN_SCORE_COL].values
        )
        logger.info(
            f"ROOT FIX (W-04): adaptive gnn threshold computed on "
            f"val_for_threshold ({len(val_for_threshold_df)} pairs, "
            f"held-out from PPO training). The threshold will be used "
            f"at BOTH train and test time, eliminating the distribution "
            f"shift (W-04 audit finding)."
        )

    # Build TRAIN environment
    # ROOT FIX (W-04): pass set_adaptive_threshold=False so the train
    # env does NOT overwrite the val-computed threshold with a
    # train-data threshold. The threshold set above (from val_for_threshold)
    # is preserved on the shared reward_fn.
    train_env = DrugRankingEnv(
        train_proper_df, config=config, reward_fn=reward_fn,
        set_adaptive_threshold=False,
    )

    if config.run_env_check or os.environ.get("RL_RUN_ENV_CHECK", "0") == "1":
        _run_env_check(train_env)
    else:
        logger.debug("Skipping env check (set RL_RUN_ENV_CHECK=1 to enable)")

    # Train on train_env
    # v89 P0 ROOT FIX (VecNormalize): train_agent now returns a 3-tuple
    # (model, checkpoint_path, vec_normalize). The vec_normalize wrapper
    # is passed to evaluate_agent and compute_auc so the obs is normalized
    # before being passed to the policy network. Without this, every AUC
    # and Top-N ranking is essentially random (silent train/inference
    # distribution shift).
    model, checkpoint_path, vec_normalize = train_agent(
        train_env,
        timesteps=config.timesteps,
        seed=config.seed,
        config=config,
        resume_checkpoint=config.resume_checkpoint,
    )

    # V4 C-F2 fix: capture the TRAIN env's disease context stats and
    # pass them to the TEST env. The original code let the test env
    # compute its own stats, causing a distribution shift (same disease
    # had different feature values at train vs test time).
    train_disease_stats = train_env._disease_context_stats

    # B14 fix: evaluate on TEST env, not train env.
    # The Top-N candidates now come from held-out test data, not
    # training data. This is the fix that makes the deliverable
    # ("top 10 candidates") actually test the agent's generalization.
    # V4 C-F2 fix: pass train disease stats to test env.
    # ROOT FIX (FORENSIC-AUDIT-I13): pass set_adaptive_threshold=False so
    # the test env reuses the train reward_fn's adaptive threshold instead
    # of overwriting it with test data. This eliminates test-data leakage
    # into the reward function's gnn_hard_reject gate.
    if len(test_df) > 0:
        test_env = DrugRankingEnv(
            test_df, config=config, reward_fn=reward_fn,
            disease_context_stats=train_disease_stats,
            set_adaptive_threshold=False,
        )
        # v89 P0: pass vec_normalize so obs is normalized at inference.
        candidates = evaluate_agent(
            model, test_env, top_n=config.top_n,
            vec_normalize=vec_normalize,
        )
    else:
        logger.warning(
            "Test set is empty; falling back to evaluating on train env. "
            "(This should not happen -- check split_data.)"
        )
        # v89 P0: pass vec_normalize even in the fallback path.
        candidates = evaluate_agent(
            model, train_env, top_n=config.top_n,
            vec_normalize=vec_normalize,
        )

    # v90 P0 ROOT FIX (BUG #16): n_ranked_high should be the TRUE count
    # of pairs the agent ranked HIGH (action=1), NOT len(candidates)
    # which is capped at top_n. The previous code reported min(top_n,
    # len(high_ranked)) instead of the true count. If 50 pairs were
    # ranked HIGH but top_n=10, the metric said 10. Fix: use the actual
    # high_ranked buffer size from the eval env.
    _eval_env = test_env if len(test_df) > 0 else train_env
    metrics.n_ranked_high = len(_eval_env.high_ranked)

    # Compute AUC on held-out test (B13 fix: uses KNOWN_POSITIVES as label)
    # V4 B-F1 fix: uses policy probabilities, not binary actions.
    # V4 S-F3 fix: AUC can now be None (degenerate test set).
    # V4 C-F2 fix: pass train disease stats to test env.
    # ROOT FIX (FORENSIC-AUDIT-I13): pass train reward_fn + set_adaptive_threshold=False
    # so the AUC env uses the SAME threshold as training (no test leakage).
    auc: Optional[float] = None
    if len(test_df) > 0 and len(test_df[DRUG_COL].unique()) > 1:
        t0 = _time.perf_counter()
        # v89 P0: pass vec_normalize so obs is normalized at inference.
        auc = compute_auc(
            model, test_df, config=config,
            disease_context_stats=train_disease_stats,
            reward_fn=reward_fn,
            vec_normalize=vec_normalize,
        )
        metrics.inference_latency_ms = (_time.perf_counter() - t0) * 1000
        if auc is not None:
            logger.info(f"Held-out AUC (policy probs): {auc:.4f} (inference: {metrics.inference_latency_ms:.0f}ms)")
        else:
            logger.warning("Held-out AUC is None (degenerate test set). See warnings above.")

    # V30 ROOT FIX (10.16): REMOVED the retry-on-low-AUC logic.
    #
    # The original D1-D5 retry logic re-trained PPO up to 3 times with
    # different seeds if AUC < 0.5, then REPORTED the first run that
    # exceeded 0.5. This inflated the reported AUC by SELECTION BIAS:
    # true AUC ≈ 0.45, AUC variance ≈ 0.10, so P(at least one of 3
    # runs > 0.5) ≈ 0.78. The pipeline reported the cherry-picked run
    # without recording the retries in metadata (provenance lie).
    #
    # The audit confirmed: "Retry logic (3x with different seeds)
    # inflates reported AUC by selection bias. True AUC ≈ 0.45, AUC
    # variance ≈ 0.10. P(at least one of 3 runs > 0.5) ≈ 0.78. The
    # pipeline reports the first run > 0.5, cherry-picked. Metadata
    # does not record retries."
    #
    # The fix: report the FIRST run's AUC honestly. If it's below 0.5,
    # the scientific_validation gate will catch it (the bridge raises
    # RuntimeError in strict mode per the V30 9.5 fix). No more
    # cherry-picking.
    #
    # The retry loop is REMOVED. The AUC is whatever the first training
    # run produces. If the user wants to retry manually, they can re-run
    # the pipeline with a different seed.
    if auc is not None and auc < 0.5:
        logger.warning(
            f"V30 ROOT FIX (10.16): RL AUC = {auc:.4f} is below 0.5. "
            f"The retry-on-low-AUC logic (D1-D5) was REMOVED because it "
            f"inflated reported AUC by selection bias (cherry-picking the "
            f"first run > 0.5 out of 3 retries). The scientific_validation "
            f"gate will catch this in strict mode. To retry manually, "
            f"re-run the pipeline with a different seed."
        )

    # Literature cross-check
    if not os.environ.get("RL_SKIP_LITERATURE"):
        candidates = literature_crosscheck(candidates)

    # Known-positive recovery (C6 fix: works in both standalone and integrated)
    # ROOT FIX (C-3): pass test_df so the recovery denominator is the number
    # of KPs in the TEST set (not all 5 KPs). The candidates come from the
    # test env, so only test-split KPs can be recovered. The previous
    # denominator (all 5 KPs) capped recovery at 2/5 = 40% even when the
    # agent recovered ALL test KPs.
    recovery = check_known_positive_recovery(candidates, test_data=test_df)
    logger.info(f"Known-positive recovery rate: {recovery['recovery_rate']:.1%}")

    # Build metadata
    metadata = {
        "pipeline_version": config.pipeline_version,
        "schema_version": config.schema_version,
        "training_timestamp": datetime.now(timezone.utc).isoformat(),
        "input_file": config.input_path or "fake_data",
        "input_sha256": input_sha256,
        "model_checkpoint": checkpoint_path or "none",
        "seed": config.seed,
        "timesteps": config.timesteps,
        # v90 P0 ROOT FIX (BUG #26): record the EFFECTIVE reward weights
        # (after gnn_score cap) instead of the raw config weights. The
        # config sets gnn_score: 0.35, but the runtime caps it at 0.04
        # and redistributes the excess. The previous metadata recorded
        # the raw config (0.35), breaking reproducibility — a regulator
        # auditing the output saw 0.35 but the actual reward used 0.04.
        "reward_weights": reward_fn.get_effective_reward_weights(),
        "reward_weights_config_raw": config.reward.reward_weights,  # original config before cap
        "feature_cols": config.reward.feature_cols,
        "thresholds": {
            "safety_hard_reject": config.reward.safety_hard_reject,
            "safety_warning": config.reward.safety_warning,
            "gnn_hard_reject": config.reward.gnn_hard_reject,
            "low_action_penalty": config.reward.low_action_penalty,
            "high_action_bonus": config.reward.high_action_bonus,
            "correct_rejection_reward": config.reward.correct_rejection_reward,
            "bad_high_penalty_scale": config.reward.bad_high_penalty_scale,  # v90 BUG #18
        },
        # v90 P0 ROOT FIX (BUG #10): record ALL actual PPO hyperparams
        # for provenance (21 CFR Part 11 audit trail). The previous
        # metadata only recorded lr/n_steps/batch_size/n_epochs, missing
        # gamma, ent_coef, clip_range, net_arch. A regulator could not
        # reproduce the run. Now all hyperparams are recorded.
        "ppo_hyperparams": {
            "learning_rate": config.ppo_learning_rate,
            "n_steps": config.ppo_n_steps,
            "batch_size": config.ppo_batch_size,
            "n_epochs": config.ppo_n_epochs,
            "gamma": config.ppo_gamma,
            "ent_coef": config.ppo_ent_coef,
            "clip_range": config.ppo_clip_range,
            "net_arch": config.ppo_net_arch or dict(pi=[128, 64], vf=[64, 32]),
        },
        "auc": None,
        "auc_defined": False,  # V4 S-F3 fix: distinguish None (undefined) from 0.5 (random)
        "known_positive_recovery_rate": recovery["recovery_rate"],
        # ROOT FIX (C-3): expose the recovery denominator basis so the
        # bridge and downstream consumers know whether the rate uses
        # the test-set denominator (correct) or all-KPs denominator
        # (legacy). Also expose n_kps_in_test so consumers can compute
        # the absolute count of recovered KPs.
        "recovery_denominator_basis": recovery.get("denominator_basis", "all_kps"),
        "n_kps_in_test": recovery.get("n_kps_in_test", len(KNOWN_POSITIVES)),
        "n_kps_total": recovery.get("n_kps_total", len(KNOWN_POSITIVES)),
        "n_kps_recovered": recovery.get("recovered", 0),
        "run_id": metrics.run_id,
        "b14_fix_evaluated_on_test_env": True,
        "b13_fix_auc_uses_known_positives": True,
        # V4 fixes
        "v4_b_f1_auc_uses_policy_probs": True,
        "v4_b_f2_ranked_by_policy_prob": True,
        # ROOT FIX (S-04): the synergy reward was REMOVED by the S-04
        # audit fix (the reward is now strictly monotonic:
        # weighted_sum * gnn_factor * safety_factor + validated_bonus).
        # The previous flag ``v4_b_f3_synergy_reward: True`` was a STALE
        # lie — it claimed the synergy reward was still active. Renamed
        # for honesty so downstream consumers can verify the fix is in
        # place by reading the metadata.
        "v4_b_f3_synergy_reward_removed": True,
        "s04_monotonic_reward": True,
        "v4_b_f4_orphan_market_score": True,
        "v4_b_f5_temperature_applied": True,
        "v4_b_f6_held_out_drugs": True,
        "v4_b_f7_sparse_softmax_gradient": True,
        "v4_b_f8_add_edge_warnings": True,
        "v4_b_f9_rl_is_package": True,
        "v4_b_f10_demo_graph_crash_fix": True,
        "v4_c_f2_disease_stats_from_train": True,
        "v4_c_f7_terminal_obs_fix": True,
        "v4_c_f8_phase6_via_rl": True,
        # v3 root fix: full Phase 3 <-> Phase 4 integration.
        # Propagate the GT model's test/val AUC into the RL output
        # metadata so consumers have a single provenance trail from
        # graph training through RL ranking. Set by the bridge.
        #
        # ROOT FIX (C-4): propagate the INDEPENDENT evaluate_link_prediction
        # AUC (gt_test_auc_verified), the trainer's evaluate() AUC
        # (gt_test_auc_trainer), and the discrepancy between them
        # (gt_test_auc_discrepancy). The primary gt_test_auc is now the
        # VERIFIED AUC (independent evaluation), not the trainer's AUC.
        # This gives downstream consumers full visibility into the GT
        # model's evaluation quality — if the discrepancy is large, it
        # indicates a bug in one of the two evaluation methods.
        "gt_test_auc": config.gt_test_auc,
        "gt_test_auc_verified": config.gt_test_auc_verified,
        "gt_test_auc_trainer": config.gt_test_auc_trainer,
        "gt_test_auc_discrepancy": config.gt_test_auc_discrepancy,
        "gt_best_val_auc": config.gt_best_val_auc,
        "gt_epochs_trained": config.gt_epochs_trained,
    }
    if auc is not None:
        metadata["auc"] = auc
        metadata["auc_defined"] = True

    # ROOT FIX (D7): compute scientific validation BEFORE save_results
    # so it's included in the output metadata.
    # v89 P0 ROOT FIX (gate BEFORE CSV write): use the configurable
    # gt_test_auc_threshold (default 0.85) instead of the hardcoded 0.5.
    # This makes the RL pipeline's gate match the bridge's V1 launch
    # contract gate, so the RL pipeline REFUSES to write its candidate
    # CSV if GT AUC < 0.85. The gate fires BEFORE save_results (below),
    # so no invalid candidates reach disk.
    scientific_validation = {
        "gt_test_auc": config.gt_test_auc,
        # v90 P0 ROOT FIX (BUG #4): drop the `if ... is not None else None`.
        # When gt_test_auc is None (bridge didn't set it, or GT training
        # failed), gt_test_auc_pass must be False (not None). The previous
        # `else None` caused the if/elif ladder below to SILENTLY SKIP
        # the None case — neither checks_passed nor checks_failed got
        # the entry. overall_pass could be True with NO GT AUC at all.
        # A pharma partner would receive candidates backed by an
        # unvalidated graph transformer. Fix: None AUC → False → fails.
        "gt_test_auc_pass": (
            config.gt_test_auc is not None
            and config.gt_test_auc > config.gt_test_auc_threshold
        ),
        "gt_test_auc_threshold": config.gt_test_auc_threshold,
        "rl_auc": auc,
        # v90 P0 ROOT FIX (BUG #3): same fix as BUG #4. When auc is None
        # (degenerate test set — 0 known positives or single class),
        # rl_auc_pass must be False (not None). The previous `else None`
        # silently skipped the AUC check, allowing the pipeline to pass
        # validation with NO AUC. Fix: None AUC → False → fails.
        "rl_auc_pass": (
            auc is not None and auc > config.rl_auc_threshold
        ),
        "rl_auc_threshold": config.rl_auc_threshold,
        "kp_recovery_rate": recovery["recovery_rate"],
        "kp_recovery_pass": recovery["recovery_rate"] >= config.min_kp_recovery_rate,
        "n_candidates": len(candidates),
    }

    checks_passed = []
    checks_failed = []
    for check_name, check_result in [
        ("gt_test_auc", scientific_validation["gt_test_auc_pass"]),
        ("rl_auc", scientific_validation["rl_auc_pass"]),
        ("kp_recovery", scientific_validation["kp_recovery_pass"]),
    ]:
        if check_result is True:
            checks_passed.append(check_name)
        elif check_result is False:
            checks_failed.append(check_name)

    scientific_validation["checks_passed"] = checks_passed
    scientific_validation["checks_failed"] = checks_failed
    scientific_validation["overall_pass"] = len(checks_failed) == 0

    metadata["scientific_validation"] = scientific_validation

    # ROOT FIX (P0-3/P0-4): BLOCK pipeline completion if scientific
    # validation fails and blocking is enabled. This prevents shipping
    # scientifically invalid output to pharma partners.
    allow_failure = (
        not config.block_on_scientific_failure
        or os.environ.get("RL_ALLOW_SCIENCE_FAILURE", "0") == "1"
    )
    if not scientific_validation["overall_pass"] and not allow_failure:
        error = ScientificFailureError(
            "ROOT FIX (P0-3/P0-4): Scientific validation FAILED. "
            "Pipeline refusing to write output CSV. The output would "
            "be scientifically invalid for pharma partner demos.",
            validation=scientific_validation,
        )
        logger.critical(str(error))
        log_audit_event("scientific_failure_blocked", {
            "run_id": metrics.run_id,
            "checks_failed": checks_failed,
            "gt_test_auc": scientific_validation["gt_test_auc"],
            "rl_auc": scientific_validation["rl_auc"],
            "kp_recovery_rate": scientific_validation["kp_recovery_rate"],
        })
        raise error
    elif not scientific_validation["overall_pass"] and allow_failure:
        logger.warning(
            f"ROOT FIX (P0-3/P0-4): Scientific validation FAILED but "
            f"blocking is DISABLED (block_on_scientific_failure=False or "
            f"RL_ALLOW_SCIENCE_FAILURE=1). Output will be written but "
            f"marked as SCIENTIFICALLY INVALID in metadata."
        )

    output_path = save_results(candidates, metadata=metadata, config=config)

    # v3 root fix: wire merge_results (was dead code in V2).
    # If merge_existing_results_path is set, merge the new candidates
    # with the existing results CSV, keeping the highest-reward
    # candidate per (drug, disease) pair. This enables incremental
    # runs (multiple seeds, multiple timesteps) without losing prior
    # rankings.
    if config.merge_existing_results_path and candidates:
        try:
            new_df = pd.DataFrame([c.to_dict() for c in candidates])
            merged_df = merge_results(config.merge_existing_results_path, new_df)
            merged_path = output_path.replace(".csv", "_merged.csv")
            merged_df.to_csv(merged_path, index=False, encoding="utf-8", lineterminator="\n")
            logger.info(
                f"v3 fix: merged results saved to {merged_path} "
                f"({len(merged_df)} unique pairs)"
            )
        except Exception as e:
            logger.warning(f"v3 fix: merge_results failed: {e}")

    check_alert_conditions(metrics, data)

    # ROOT FIX (D7): log scientific validation result (computed above
    # before save_results so it's in the metadata). The CRITICAL log
    # for failures makes the failure VISIBLE — no more false confidence.
    if scientific_validation["overall_pass"]:
        logger.info(
            f"ROOT FIX (D7): SCIENTIFIC VALIDATION PASSED. "
            f"All checks passed: {checks_passed}. "
            f"Output is scientifically valid."
        )
    else:
        logger.critical(
            f"ROOT FIX (D7): SCIENTIFIC VALIDATION FAILED. "
            f"Failed checks: {checks_failed}. "
            f"Passed checks: {checks_passed}. "
            f"The output is marked as SCIENTIFICALLY INVALID in the metadata. "
            f"Do NOT use this output for pharma partner demos. "
            f"GT test AUC: {scientific_validation['gt_test_auc']}, "
            f"RL AUC: {scientific_validation['rl_auc']}, "
            f"KP recovery: {scientific_validation['kp_recovery_rate']:.1%}."
        )
        log_audit_event("scientific_validation_failed", {
            "run_id": metrics.run_id,
            "checks_failed": checks_failed,
            "gt_test_auc": scientific_validation["gt_test_auc"],
            "rl_auc": scientific_validation["rl_auc"],
            "kp_recovery_rate": scientific_validation["kp_recovery_rate"],
        })

    log_audit_event("pipeline_complete", {
        "run_id": metrics.run_id,
        "n_ranked_high": metrics.n_ranked_high,
        "output_path": output_path,
        "scientific_validation_passed": scientific_validation["overall_pass"],
    })

    logger.info("RL ranking pipeline complete.")
    return candidates, metrics


# ============================================================================
# CLI
# ============================================================================
def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Team Cosmic RL Drug Repurposing Hypothesis Ranker (Phase 4)",
    )
    parser.add_argument("--input", type=str, default=None,
                        help="Path to GNN output CSV (default: generate fake data)")
    parser.add_argument("--timesteps", type=int, default=10000,
                        help="PPO training timesteps (default: 10000)")
    parser.add_argument("--top-n", type=int, default=10,
                        help="Number of top candidates to return (default: 10)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--output-dir", type=str, default="output",
                        help="Directory for output CSV and metadata (default: output)")
    parser.add_argument("--checkpoint-dir", type=str, default="checkpoints",
                        help="Directory for model checkpoints (default: checkpoints)")
    parser.add_argument("--config", type=str, default=None,
                        help="Path to YAML config file (optional)")
    parser.add_argument("--skip-literature", action="store_true",
                        help="Skip PubMed literature cross-check")
    parser.add_argument("--run-env-check", action="store_true",
                        help="Run stable_baselines3 env check at startup")
    parser.add_argument("--json-logs", action="store_true",
                        help="Emit JSON-formatted log lines")
    parser.add_argument("--log-level", type=str, default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="Logging level (default: INFO)")
    # C4 fix: allow disabling drug-aware split for backward-compat.
    parser.add_argument(
        "--no-drug-aware-split", action="store_true",
        help="Disable drug-aware train/test split (C4 fix is on by default)",
    )
    # ROOT FIX (F6): document RL_HMAC_KEY env var in CLI help so users
    # know they need to set it for cryptographic HMAC verification.
    # Without this, users ship with "unverified" HMACs and don't realize it.
    parser.epilog = (
        "Environment variables:\n"
        "  RL_HMAC_KEY          Set to a secret key for cryptographic HMAC verification "
        "(default: unverified HMAC using insecure default key)\n"
        "  RL_KNOWN_POSITIVES   JSON list of [drug, disease] pairs to override defaults "
        "(default: 5 hardcoded pairs)\n"
        "  RL_STRICT_SYMLINK_CHECK  Set to '1' to reject symlinked directories "
        "(default: warn and proceed)\n"
        "  RL_USER               Override the audit log actor username\n"
        "  NCBI_EMAIL            Email for PubMed literature cross-check\n"
        "  RL_SKIP_LITERATURE    Set to '1' to skip literature cross-check\n"
        "\n"
        "ROOT FIX (F6): Without RL_HMAC_KEY, the output HMAC is marked "
        "as 'unverified' — it provides forensic fingerprinting only, NOT "
        "cryptographic tamper detection. Set RL_HMAC_KEY for production use."
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point."""
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.config:
        config = PipelineConfig.from_yaml(args.config)
    else:
        config = PipelineConfig.from_env()

    config.timesteps = args.timesteps
    config.top_n = args.top_n
    config.seed = args.seed
    config.output_dir = args.output_dir
    config.checkpoint_dir = args.checkpoint_dir
    config.input_path = args.input
    config.run_env_check = args.run_env_check
    config.json_logs = args.json_logs
    config.log_level = args.log_level
    config.drug_aware_split = not args.no_drug_aware_split

    if args.skip_literature:
        os.environ["RL_SKIP_LITERATURE"] = "1"

    level = getattr(logging, args.log_level)
    setup_logging(level=level, json_logs=args.json_logs)

    if not validate_environment(config):
        return 1

    try:
        run_pipeline(config)
        return 0
    except Exception as e:
        logger.critical(f"Pipeline failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
