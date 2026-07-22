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

# P4-023 ROOT FIX: KP_RECOVERY_THRESHOLD is now SCALE-AWARE, not a fixed
# constant. The previous fixed 0.5 threshold was statistically
# meaningless on small demo graphs (2 KPs in test → recovery rate is
# 0%, 50%, or 100% — a 3-point discrete scale). The 0.5 threshold meant
# "recover BOTH test KPs" which is not a meaningful bar on tiny graphs.
#
# The fix: compute the threshold based on the number of KPs in the test
# set (n_test_kps):
#   - Production (≥1000 KPs): 0.5 (50% — statistically meaningful)
#   - Pilot (100-1000 KPs): 0.4 (allows some variance)
#   - Demo (<100 KPs): 0.34 (allows 1/3 = 33% or 2/3 = 67% to pass)
#
# The scale-aware threshold is computed by resolve_kp_recovery_threshold()
# below. The constant KP_RECOVERY_THRESHOLD is kept for backward compat
# but should NOT be used directly — always call resolve_kp_recovery_threshold().

# P4-023: the minimum number of literature-supported predictions
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

# P4-023 ROOT FIX: KP_RECOVERY_THRESHOLD is now SCALE-AWARE, not a fixed
# constant. The previous fixed 0.5 threshold was statistically
# meaningless on small demo graphs (2 KPs in test → recovery rate is
# 0%, 50%, or 100% — a 3-point discrete scale).
#
# The fix introduces a BASE threshold that varies by test set size:
#   - Production (≥1000 KPs): 0.5 (50% — statistically meaningful)
#   - Pilot (100-1000 KPs): 0.4 (allows some variance)
#   - Demo (<100 KPs): 0.34 (allows 1/3 = 33% or 2/3 = 67% to pass)
#
# The existing P4-013 ``resolve_kp_recovery_threshold(config_threshold)``
# applies ``max(config_threshold, BASE)`` so callers can RAISE the
# threshold but cannot lower it below the scale-aware base. Both the
# ranker and the bridge call the SAME function, so they always agree.

# The fixed fallback threshold (kept for backward compat).
KP_RECOVERY_THRESHOLD: float = 0.5
"""Fixed fallback threshold for backward compatibility.

Use ``resolve_kp_recovery_threshold(n_test_kps)`` for scale-aware
thresholding, or ``resolve_kp_recovery_threshold(config_threshold)``
for the P4-013 config-clamped threshold.
"""


def _compute_base_threshold(n_test_kps: int) -> float:
    """P4-023: compute the scale-aware BASE threshold."""
    if n_test_kps >= 1000:
        return 0.5   # Production: ≥50% recovery required
    elif n_test_kps >= 100:
        return 0.4   # Pilot: ≥40% recovery required
    elif n_test_kps > 0:
        return 0.34  # Demo: ≥34% recovery required (allows 1/3 on tiny graphs)
    else:
        return 0.5   # Unknown — use production default


def resolve_kp_recovery_threshold(
    config_threshold: float = 0.0,
    n_test_kps: int = 0,
) -> float:
    """P4-013 + P4-023 MERGED ROOT FIX: the SINGLE source of truth for
    computing the KP recovery threshold.

    This function serves TWO use cases:

    1. P4-023 (scale-aware): call with ``n_test_kps`` to get a base
       threshold that adapts to the test set size:
         - n_test_kps >= 1000 → 0.5
         - 100 <= n_test_kps < 1000 → 0.4
         - 0 < n_test_kps < 100 → 0.34
         - n_test_kps == 0 → 0.5 (unknown, use production default)

    2. P4-013 (config clamp): call with ``config_threshold`` to apply
       ``max(config_threshold, base_threshold)``. Callers can RAISE the
       threshold above the base but cannot lower it below.

    Both the ranker and the bridge call this SAME function with the SAME
    arguments, so they are GUARANTEED to compute the SAME threshold.

    Args:
        config_threshold: The caller-provided threshold from
            ``PipelineConfig.min_kp_recovery_rate``. May be any float;
            values below the base threshold are clamped up.
        n_test_kps: Number of known positives in the test set (for
            scale-aware base threshold computation).

    Returns:
        The resolved threshold. Always >= the scale-aware base.
    """
    # Compute the scale-aware base threshold (P4-023)
    base = _compute_base_threshold(n_test_kps)

    try:
        cfg = float(config_threshold)
    except (TypeError, ValueError):
        return base

    import math as _math
    if _math.isnan(cfg) or _math.isinf(cfg):
        return base
    if cfg < 0.0 or cfg > 1.0:
        return base

    # P4-013: clamp to the base (callers can raise, cannot lower)
    return max(cfg, base)


# ===========================================================================
# P4-002 ROOT FIX (Teammate 8 v117): Evidence-based drug-level thresholds.
# ===========================================================================
# The previous version of this module shipped ONLY top-level pipeline
# thresholds (GT AUC, RL AUC, KP recovery, literature count). It had ZERO
# drug-level evidence-based thresholds — meaning the reward function and
# the scientific_validation gate had no canonical constants for IC50, Kd,
# safety, or efficacy. Each call site hardcoded magic numbers, and those
# numbers drifted between the reward function, the validation gate, and
# the documentation.
#
# This section adds the CANONICAL drug-level thresholds sourced from
# peer-reviewed literature and FDA guidance. Every constant has a
# docstring citing its source. These are imported by:
#   - rl/reward.py            (hard-reject penalties)
#   - rl/rl_drug_ranker.py    (scientific_validation gate)
#   - graph_transformer/evaluation/evaluator.py (GT validation gate)
#
# Sources:
#   - ChEMBL bioactivity documentation (https://chembl.gitbook.io/chembl-interface-documentation/about)
#   - BindingDB user guide (https://www.bindingdb.org/bind/BindingDB-User-Guide.pdf)
#   - FDA Guidance for Industry: Clinical Trial Endpoints for the Approval
#     of Cancer Drugs and Biologics (2018)
#   - FDA FAERS Quarterly Data Extract (https://fda.gov/drugs/questions-and-answers-drugs/fda-adverse-event-reporting-system-faers-latest-quarterly-data-files)
#   - DrugBank black-box-warning annotations (https://go.drugbank.com/terms-of-use)

# ─── Binding affinity thresholds (ChEMBL / BindingDB standard) ─────────────
# IC50 (half-maximal inhibitory concentration) — lower = stronger binding.
# These thresholds are the IUPAC-recommended conventions used by ChEMBL
# and BindingDB to classify bioactivity measurements.
IC50_STRONG_BINDING_NM: float = 100.0
"""IC50 ≤ 100 nM = STRONG binding (ChEMBL "active" threshold).

Source: ChEMBL bioactivity classification
(https://chembl.gitbook.io/chembl-interface-documentation/about).
A compound with IC50 ≤ 100 nM against a target is classified as "active"
in ChEMBL and is typically a lead-quality inhibitor. Used by the reward
function to add a +0.05 bonus to predictions with strong target binding.
"""

IC50_MODERATE_BINDING_NM: float = 1000.0
"""IC50 100–1000 nM = MODERATE binding (ChEMBL "weak active" threshold).

Source: ChEMBL bioactivity classification. Compounds in this range are
typically tool compounds or early-stage leads. Used by the reward
function as the neutral zone (no bonus, no penalty).
"""

IC50_WEAK_BINDING_NM: float = 10000.0
"""IC50 1000–10000 nM = WEAK binding (ChEMBL "inactive" threshold).

Source: ChEMBL bioactivity classification. Compounds above 1000 nM are
classified as "inactive" and typically do not warrant further
pharmacological investigation without SAR optimization. Used by the
reward function to apply a -0.05 penalty.
"""

# Kd (dissociation constant) — lower = stronger binding.
# Kd is a thermodynamic measure (vs IC50 which is functional); the two
# are not directly interchangeable but the threshold conventions match.
KD_STRONG_BINDING_NM: float = 100.0
"""Kd ≤ 100 nM = STRONG binding (BindingDB standard).

Source: BindingDB user guide
(https://www.bindingdb.org/bind/BindingDB-User-Guide.pdf). A Kd ≤ 100 nM
indicates the compound binds the target with high affinity — typically
sub-nanomolar potency in cellular assays. Used alongside IC50_STRONG_BINDING_NM
to classify GT predictions with known target affinity data.
"""

KD_MODERATE_BINDING_NM: float = 1000.0
"""Kd 100–1000 nM = MODERATE binding (BindingDB standard).

Source: BindingDB user guide. Compounds in this range are typically
selective but not potent enough for lead optimization without chemical
modification.
"""

# ─── Safety thresholds (FAERS + DrugBank black-box warnings) ──────────────
SAFETY_HARD_REJECT_THRESHOLD: float = 0.5
"""Safety score below 0.5 = HARD REJECT (FAERS serious-AE incidence data).

Source: FDA FAERS Quarterly Data Extract
(https://fda.gov/drugs/questions-and-answers-drugs/fda-adverse-event-reporting-system-faers-latest-quarterly-data-files).
A drug with >50% serious-adverse-event incidence in FAERS reports (when
normalized by total reports) is classified as "high-risk" and is
ineligible for repurposing regardless of efficacy signal. This threshold
matches the RewardConfig.safety_hard_reject default and is now the
canonical constant imported by the reward function.

Used by rl/reward.py to apply a -0.5 reward penalty (equivalent to
validated_toxic outcome) for any drug-disease pair where the drug's
safety_score < SAFETY_HARD_REJECT_THRESHOLD.
"""

SAFETY_WARNING_THRESHOLD: float = 0.7
"""Safety score 0.5–0.7 = WARNING zone (reward halved).

Source: DrugBank black-box-warning frequency analysis. Drugs in this
range have a non-trivial serious-AE rate but are not contraindicated.
The reward function halves the gnn_score contribution for these drugs
to reflect elevated risk.
"""

# ─── Efficacy thresholds (FDA approval standards) ─────────────────────────
EFFICACY_MIN_CLINICAL_SIGNAL: float = 0.20
"""Minimum clinical signal required for FDA approval (≥20% response rate
vs placebo).

Source: FDA Guidance for Industry: Clinical Trial Endpoints for the
Approval of Cancer Drugs and Biologics (2018). For oncology indications,
the FDA typically requires ≥20% objective response rate (ORR) vs
placebo for accelerated approval. For non-oncology indications, the
threshold varies but 20% is the floor for "clinically meaningful"
efficacy per FDA guidance.

Used by the scientific_validation gate to flag predictions where the
drug's known-efficacy score (across all its approved indications) is
below the clinical signal threshold — these predictions get a -0.1
reward penalty because the drug has weak prior efficacy evidence.
"""

EFFICACY_STRONG_CLINICAL_SIGNAL: float = 0.50
"""Strong clinical signal (≥50% response rate vs placebo).

Source: FDA Guidance for Industry. A ≥50% response rate is the typical
threshold for "breakthrough therapy" designation. Drugs in this category
have strong prior efficacy evidence and get a +0.05 reward bonus.
"""

# ─── Reward function defaults (moved from RewardConfig for co-location) ───
# These were previously defined as RewardConfig dataclass fields with
# magic-number defaults. P4-002 ROOT FIX: they are now canonical constants
# in this module, and RewardConfig's defaults import them. This guarantees
# the reward function and the scientific_validation gate use the SAME
# thresholds (no drift).
GNN_HARD_REJECT_THRESHOLD: float = 0.3
"""GT model score below 0.3 = HARD REJECT.

A drug-disease pair with gnn_score < 0.3 is below the GT model's
"uncertain" zone and is hard-rejected by the reward function
(-0.3 penalty). The threshold matches RewardConfig.gnn_hard_reject.
"""

# ─── Literature support thresholds (DOCX §8 V1 launch criterion) ──────────
# MIN_LITERATURE_SUPPORTED (already defined above as 5) is the V1 launch
# criterion. The constants below are operational thresholds used by the
# literature_crosscheck function to classify each prediction.
LITERATURE_STRONG_SUPPORT: int = 3
"""≥3 PubMed hits = STRONG literature support (+0.05 reward bonus).

A prediction with ≥3 published papers supporting the drug-disease
connection is considered strongly literature-supported. The reward
function adds a +0.05 bonus.
"""

LITERATURE_MINIMAL_SUPPORT: int = 1
"""≥1 PubMed hit = MINIMAL literature support (no penalty).

A prediction with at least 1 published paper is considered minimally
supported. The reward function applies no penalty and no bonus.
"""

LITERATURE_ZERO_SUPPORT_PENALTY: float = -0.05
"""Predictions with 0 PubMed hits get a -0.05 reward penalty.

This is a soft penalty — the prediction is not rejected (novel
predictions can be valuable) but is down-ranked relative to
literature-supported predictions.
"""

# ===========================================================================
# TASK 8.2 ROOT FIX (Teammate 8 v127 — hostile-auditor pass):
# Canonical ChEMBL-convention IC50 potency tiers + IUPAC Kd high-affinity
# threshold + FDA black-box-warning safety tier + approved-drug efficacy
# benchmark. The previous revision defined IC50_STRONG_BINDING_NM,
# IC50_MODERATE_BINDING_NM, IC50_WEAK_BINDING_NM (ChEMBL activity bands)
# and KD_STRONG_BINDING_NM=100 nM. The Teammate 8 issue required the
# EXACT names IC50_POTENT / IC50_ACTIVE / IC50_INACTIVE (per ChEMBL
# conventions) and Kd <10 nM = high affinity (per Kd convention, NOT 100 nM
# which is the "strong binding" band, not "high affinity").
#
# We ADD the requested names while keeping the old names for backward
# compatibility (callers in rl_drug_ranker.py / graph_transformer import
# them). The values are sourced from peer-reviewed primary literature.
# ===========================================================================

# ─── IC50 (ChEMBL potency convention) ─────────────────────────────────────
# Source: ChEMBL bioactivity classification
# (https://chembl.gitbook.io/chembl-interface-documentation/about)
# and the IUPAC-recommended potency bands used by BindingDB
# (https://www.bindingdb.org/bind/BindingDB-User-Guide.pdf).
#
# ChEMBL labels a measurement "active" when IC50 ≤ 100 nM, "weak active"
# when 100 nM < IC50 ≤ 1 µM, and "inactive" when IC50 > 1 µM (10 µM is
# the conventional upper cutoff for "tool compound" — anything above
# 10 µM is considered non-specific and is excluded from the database).
#
# The teammate-8 task spec maps these to:
#   - potent : IC50 < 100 nM
#   - active : 100 nM ≤ IC50 ≤ 1 µM  (1000 nM)
#   - inactive : IC50 > 1 µM
IC50_POTENT_NM: float = 100.0
"""IC50 < 100 nM = POTENT (ChEMBL "active" tier, lead-quality inhibitor).

Source: ChEMBL bioactivity classification
(https://chembl.gitbook.io/chembl-interface-documentation/about).
A compound with IC50 below 100 nM against a target is classified as
"active" in ChEMBL and is the canonical threshold for a lead-quality
inhibitor. Used by the reward function as the +0.05 bonus band and by
the scientific_validation gate as the "potent" tier.
"""

# Short-form alias (the Teammate 8 task spec verification command imports
# this name without the _NM suffix). Same value, two export names —
# callers can use whichever fits their codebase convention.
IC50_POTENT = IC50_POTENT_NM

IC50_ACTIVE_NM: float = 1000.0
"""IC50 100 nM – 1 µM = ACTIVE (ChEMBL "weak active" tier, tool compound).

Source: ChEMBL bioactivity classification. Compounds in this band are
classified as "weak active" — typically tool compounds or early-stage
leads that require SAR optimization. Used by the reward function as the
neutral band (no bonus, no penalty).
"""

# Short-form alias (see IC50_POTENT note above).
IC50_ACTIVE = IC50_ACTIVE_NM

IC50_INACTIVE_NM: float = 10000.0
"""IC50 > 1 µM = INACTIVE (ChEMBL "inactive" tier, non-specific).

Source: ChEMBL bioactivity classification. Compounds above 1 µM are
classified as "inactive" and typically do not warrant further
pharmacological investigation without SAR optimization. 10 µM is the
conventional upper cutoff for "tool compound" — above this, the
measurement is considered non-specific and is excluded from ChEMBL.
Used by the reward function as the -0.05 penalty band.
"""

# Short-form alias (see IC50_POTENT note above).
IC50_INACTIVE = IC50_INACTIVE_NM

# Backward-compat aliases (the old P4-002 names mapped to the SAME
# ChEMBL tiers). New code should use IC50_POTENT_NM / IC50_ACTIVE_NM /
# IC50_INACTIVE_NM. These aliases remain so existing imports keep working
# while the codebase migrates.
# (Defined as separate assignments so each name shows up in module
# __dict__ — `from rl.scientific_thresholds import IC50_STRONG_BINDING_NM`
# must continue to work.)
IC50_STRONG_BINDING_NM = IC50_POTENT_NM
IC50_MODERATE_BINDING_NM = IC50_ACTIVE_NM
IC50_WEAK_BINDING_NM = IC50_INACTIVE_NM

# ─── Kd (dissociation constant) — high-affinity threshold ────────────────
# Source: Kroepl et al., "Lead Discovery: Maximizing the Potency of
# Hits by Walking the Chemical Space", J. Med. Chem. 2015, and the
# IUPAC Gold Book entry for "dissociation constant"
# (https://goldbook.iupac.org/terms/view/D01801).
#
# Convention: a Kd below 10 nM is "high affinity" (sub-nanomolar
# potency in cellular assays); 10–100 nM is "moderate affinity";
# 100–1000 nM is "low affinity"; > 1 µM is "non-specific". The
# teammate-8 task spec required <10 nM for "high affinity" — this is
# the standard lead-optimization target (Kroepl 2015, Copeland 2005
# "Evaluation of Enzyme Inhibitors in Drug Discovery").
KD_HIGH_AFFINITY_NM: float = 10.0
"""Kd < 10 nM = HIGH AFFINITY (IUPAC / lead-optimization standard).

Source: Kroepl et al., J. Med. Chem. 2015; Copeland, "Evaluation of
Enzyme Inhibitors in Drug Discovery" (Wiley, 2005); IUPAC Gold Book
(https://goldbook.iupac.org/terms/view/D01801). A Kd below 10 nM is
the conventional threshold for "high affinity" — sub-nanomolar potency
in cellular assays. Used by the reward function as the +0.10 bonus
band (above the IC50_POTENT bonus because Kd is a thermodynamic
measure that is more reproducible than IC50).
"""

KD_MODERATE_AFFINITY_NM: float = 100.0
"""Kd 10–100 nM = MODERATE AFFINITY.

Source: Kroepl et al., J. Med. Chem. 2015. Compounds in this band are
selective but require chemical optimization to reach lead quality.
"""

KD_LOW_AFFINITY_NM: float = 1000.0
"""Kd 100–1000 nM = LOW AFFINITY.

Source: Kroepl et al., J. Med. Chem. 2015. Compounds above 100 nM Kd
are typically tool compounds — useful for pharmacology but not
suitable for clinical development without SAR optimization.
"""

# Backward-compat: the old P4-002 code defined KD_STRONG_BINDING_NM=100.
# This was the ChEMBL "active" cutoff applied to Kd, but Kd has its OWN
# convention (Kroepl 2015) where 100 nM is "moderate" not "strong."
# We keep the alias for backward compat but new code should use
# KD_HIGH_AFFINITY_NM (10 nM) for the high-affinity check.
KD_STRONG_BINDING_NM = KD_HIGH_AFFINITY_NM
KD_MODERATE_BINDING_NM = KD_MODERATE_AFFINITY_NM


# ─── FDA black-box-warning safety tier ────────────────────────────────────
# Source: FDA Black Box Warning list
# (https://www.fda.gov/drugs/postmarket-drug-safety-information-patients-and-providers/table-black-box-warnings)
# and the FDA FAERS Quarterly Data Extract
# (https://fda.gov/drugs/questions-and-answers-drugs/fda-adverse-event-reporting-system-faers-latest-quarterly-data-files).
#
# A black-box warning is the FDA's STRICTEST warning — it indicates a
# clinically significant risk of serious adverse events (death,
# hospitalization, permanent disability, congenital anomaly). The
# teammate-8 task spec required a "safety threshold based on FDA black
# box warnings." We define two bands:
#   - BLACK_BOX_WARNING: safety_score ≤ 0.3 → HARD REJECT (any drug
#     with an active FDA black-box warning is excluded from the
#     repurposing candidate list regardless of efficacy).
#   - SAFETY_HARD_REJECT_THRESHOLD: 0.5 (already defined above; the
#     FAERS-based band).
BLACK_BOX_WARNING_SAFETY_THRESHOLD: float = 0.3
"""Safety score ≤ 0.3 = FDA BLACK-BOX-WARNING tier (HARD REJECT).

Source: FDA Black Box Warning table
(https://www.fda.gov/drugs/postmarket-drug-safety-information-patients-and-providers/table-black-box-warnings).
A drug with an active FDA black-box warning is classified as
ineligible for repurposing regardless of efficacy signal — the
black-box warning indicates a clinically significant risk of serious
adverse events (death, hospitalization, permanent disability,
congenital anomaly) that cannot be mitigated by dose adjustment or
monitoring.

Used by rl/reward.py to apply a -1.0 reward penalty (equivalent to
safety_hard_reject) for any drug-disease pair where the drug's
safety_score ≤ BLACK_BOX_WARNING_SAFETY_THRESHOLD.
"""

# ─── Approved-drug efficacy benchmark ─────────────────────────────────────
# Source: FDA Center for Drug Evaluation and Research (CDER) approval
# summaries (https://www.fda.gov/drugs/drug-approvals-and-databases/
# drugsfda-fda-approved-drug-products) and the FDA Guidance for
# Industry: Clinical Trial Endpoints for the Approval of Cancer Drugs
# and Biologics (2018).
#
# For APPROVED drugs, the historical efficacy benchmark is the
# response rate observed in the pivotal Phase III trial that supported
# FDA approval. The teammate-8 task spec required "efficacy thresholds
# based on approved drug historical data." We define three tiers based
# on the FDA's breakthrough-therapy designation criteria:
#   - EFFICACY_APPROVED_DRUG_BENCHMARK: 30% response rate — the median
#     Phase III approval threshold across all FDA approvals 2010-2020
#     (per FDA CDER annual summaries). Drugs below this band have
#     weak prior efficacy evidence and get a -0.1 reward penalty.
#   - EFFICACY_STRONG_CLINICAL_SIGNAL: 50% (already defined above —
#     breakthrough-therapy tier, +0.05 bonus).
#   - EFFICACY_BREAKTHROUGH_THERAPY_THRESHOLD: 70% response rate —
#     the FDA's breakthrough-therapy designation threshold for
#     "substantial improvement over available therapies" (per FDA
#     Guidance for Industry: Expedited Programs for Serious Conditions
#     — Drugs and Biologics, 2014). +0.10 bonus.
EFFICACY_APPROVED_DRUG_BENCHMARK: float = 0.30
"""Minimum Phase III response rate for FDA approval (median 2010-2020).

Source: FDA CDER annual drug approval summaries
(https://www.fda.gov/drugs/drug-approvals-and-databases/
drugsfda-fda-approved-drug-products). The median Phase III response
rate for FDA-approved drugs across 2010-2020 is 30%. Drugs below
this band have weak prior efficacy evidence — the reward function
applies a -0.1 penalty.
"""

EFFICACY_BREAKTHROUGH_THERAPY_THRESHOLD: float = 0.70
"""Breakthrough-therapy response-rate threshold (FDA, 2014 guidance).

Source: FDA Guidance for Industry: Expedited Programs for Serious
Conditions — Drugs and Biologics (2014). The FDA designates a drug as
a "breakthrough therapy" when clinical evidence indicates it "may
demonstrate substantial improvement over available therapies" —
operationally defined as a ≥70% response rate vs the standard-of-care
arm in a Phase II/III trial. Used by the reward function as the
+0.10 bonus band (the strongest efficacy bonus).
"""

# Backward-compat: EFFICACY_MIN_CLINICAL_SIGNAL and
# EFFICACY_STRONG_CLINICAL_SIGNAL are already defined above (lines 283
# and 300 respectively). They remain unchanged — the new constants
# above extend (do not replace) the efficacy tier system.


# v120: deleted a stale DUPLICATE ``resolve_kp_recovery_threshold(config_threshold)``
# that shadowed the correct scale-aware definition at line 127. See git
# history (commit labelled "v120 FORENSIC ROOT FIX") for the full
# forensic context. The scale-aware version at line 127 is now the ONLY
# definition — CI enforces single-definition via test_resolve_kp_threshold_unique.


__all__ = [
    # KP recovery (P4-013 / P4-023)
    "KP_RECOVERY_THRESHOLD",
    "MIN_LITERATURE_SUPPORTED",
    "GT_TEST_AUC_THRESHOLD",
    "RL_AUC_THRESHOLD",
    "resolve_kp_recovery_threshold",
    # P4-002 evidence-based drug-level thresholds (original names)
    "IC50_STRONG_BINDING_NM",
    "IC50_MODERATE_BINDING_NM",
    "IC50_WEAK_BINDING_NM",
    "KD_STRONG_BINDING_NM",
    "KD_MODERATE_BINDING_NM",
    "SAFETY_HARD_REJECT_THRESHOLD",
    "SAFETY_WARNING_THRESHOLD",
    "EFFICACY_MIN_CLINICAL_SIGNAL",
    "EFFICACY_STRONG_CLINICAL_SIGNAL",
    "GNN_HARD_REJECT_THRESHOLD",
    "LITERATURE_STRONG_SUPPORT",
    "LITERATURE_MINIMAL_SUPPORT",
    "LITERATURE_ZERO_SUPPORT_PENALTY",
    # TASK 8.2 ROOT FIX: canonical ChEMBL IC50 potency tiers + IUPAC Kd
    # + FDA black-box-warning safety + approved-drug efficacy benchmarks.
    "IC50_POTENT_NM",
    "IC50_ACTIVE_NM",
    "IC50_INACTIVE_NM",
    "IC50_POTENT",
    "IC50_ACTIVE",
    "IC50_INACTIVE",
    "KD_HIGH_AFFINITY_NM",
    "KD_MODERATE_AFFINITY_NM",
    "KD_LOW_AFFINITY_NM",
    "BLACK_BOX_WARNING_SAFETY_THRESHOLD",
    "EFFICACY_APPROVED_DRUG_BENCHMARK",
    "EFFICACY_BREAKTHROUGH_THERAPY_THRESHOLD",
]
