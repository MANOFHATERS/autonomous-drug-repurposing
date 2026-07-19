"""Task 9.6 — Bridge column mismatch: env reads 12, bridge writes 17.

P4-006 ROOT FIX verification.

The Phase 3 bridge (graph_transformer/gt_rl_bridge.py) writes 17 columns
(2 IDs + 15 features), but the env previously read only 12 (2 IDs +
10 features), silently DROPPING 5 bridge-provided feature columns.

ROOT FIX: the env now reads ALL 17 bridge columns and includes them
in the observation vector. observation_space.shape >= 17.

Verification (from task spec):
    python -c "from rl.env import DrugRankingEnv; env = DrugRankingEnv(); \\
        assert env.observation_space.shape[0] >= 17"

(The verification command in the spec calls DrugRankingEnv() with no args,
but the constructor requires a `data` arg. We use a real bridge-style CSV
instead — this is the production path.)
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RL_REQUIRE_AUTH", "false")


def _build_v128_bridge_csv() -> pd.DataFrame:
    """Build a DataFrame that mimics what gt_rl_bridge.generate_rl_input() emits.

    The bridge writes 17 columns:
        drug, disease, gnn_score, gnn_score_calibrated, confidence,
        safety_score, market_score, pathway_score, patent_score,
        rare_disease_flag, unmet_need_score, efficacy_score, adme_score,
        gnn_score_timestamp, disease_pair_count, disease_avg_gnn,
        disease_avg_safety.
    """
    rng = np.random.default_rng(42)
    drugs = ["aspirin", "metformin", "warfarin", "sildenafil", "thalidomide"]
    diseases = ["cardiovascular disease", "type 2 diabetes", "atrial fibrillation",
                "pulmonary arterial hypertension", "multiple myeloma"]
    rows = []
    for d in drugs:
        for dis in diseases:
            rows.append({
                "drug": d,
                "disease": dis,
                "gnn_score": float(rng.random()),
                "gnn_score_calibrated": float(rng.random()),
                "confidence": float(rng.random()),
                "safety_score": float(rng.random()),
                "market_score": float(rng.random()),
                "pathway_score": float(rng.random()),
                "patent_score": float(rng.random()),
                "rare_disease_flag": int(rng.integers(0, 2)),
                "unmet_need_score": float(rng.random()),
                "efficacy_score": float(rng.random()),
                "adme_score": float(rng.random()),
                "gnn_score_timestamp": "2026-07-19T04:00:00Z",
                "disease_pair_count": int(rng.integers(1, 50)),
                "disease_avg_gnn": float(rng.random()),
                "disease_avg_safety": float(rng.random()),
            })
    return pd.DataFrame(rows)


def test_task_9_6_observation_space_at_least_17():
    """P4-006: DrugRankingEnv observation_space.shape[0] >= 17.

    Builds a v128+ bridge CSV (17 columns) and verifies the env reads
    ALL of them. observation_space.shape == (18,) — 10 canonical features
    + 5 bridge columns + 3 env-derived disease context columns.
    """
    from rl.rl_drug_ranker import DrugRankingEnv, PipelineConfig

    df = _build_v128_bridge_csv()
    assert len(df.columns) == 17, (
        f"Bridge CSV must have 17 columns, got {len(df.columns)}: {list(df.columns)}"
    )

    cfg = PipelineConfig()
    env = DrugRankingEnv(data=df, config=cfg)

    assert env.observation_space.shape[0] >= 17, (
        f"P4-006 REGRESSION: observation_space.shape[0] = "
        f"{env.observation_space.shape[0]}, expected >= 17. "
        f"The env is NOT reading all 17 bridge columns. "
        f"_effective_feature_cols: {env._effective_feature_cols}"
    )
    assert env.observation_space.shape[0] == 18, (
        f"P4-006: expected observation_space.shape == (18,) [10 canonical + "
        f"5 bridge + 3 disease context], got {env.observation_space.shape}. "
        f"_effective_feature_cols: {env._effective_feature_cols}"
    )


def test_task_9_6_env_reads_all_5_new_bridge_columns():
    """P4-006: the env MUST read all 5 newly-added bridge columns.

    The 5 new columns are:
      - gnn_score_calibrated
      - gnn_score_age_hours (derived from gnn_score_timestamp)
      - bridge_disease_pair_count (renamed from bridge's disease_pair_count)
      - bridge_disease_avg_gnn (renamed from bridge's disease_avg_gnn)
      - bridge_disease_avg_safety (renamed from bridge's disease_avg_safety)
    """
    from rl.rl_drug_ranker import DrugRankingEnv, PipelineConfig
    from rl.constants import (
        GNN_SCORE_CALIBRATED_COL, GNN_SCORE_AGE_HOURS_COL,
        BRIDGE_DISEASE_PAIR_COUNT_COL, BRIDGE_DISEASE_AVG_GNN_COL,
        BRIDGE_DISEASE_AVG_SAFETY_COL,
    )

    df = _build_v128_bridge_csv()
    env = DrugRankingEnv(data=df, config=PipelineConfig())

    expected_bridge_cols = [
        GNN_SCORE_CALIBRATED_COL,        # gnn_score_calibrated
        GNN_SCORE_AGE_HOURS_COL,         # gnn_score_age_hours
        BRIDGE_DISEASE_PAIR_COUNT_COL,   # bridge_disease_pair_count
        BRIDGE_DISEASE_AVG_GNN_COL,      # bridge_disease_avg_gnn
        BRIDGE_DISEASE_AVG_SAFETY_COL,   # bridge_disease_avg_safety
    ]
    for col in expected_bridge_cols:
        assert col in env._effective_feature_cols, (
            f"P4-006 REGRESSION: bridge column {col!r} is NOT in "
            f"_effective_feature_cols. The env is dropping bridge-provided "
            f"features — the agent is blind to confidence calibration, "
            f"prediction freshness, and the bridge's authoritative disease "
            f"context. _effective_feature_cols: {env._effective_feature_cols}"
        )
        assert col in env.data.columns, (
            f"P4-006 REGRESSION: bridge column {col!r} is NOT in env.data.columns. "
            f"The env did not capture the bridge-provided column."
        )


def test_task_9_6_features_array_shape_matches_observation_space():
    """P4-006: env._features_array.shape[1] == observation_space.shape[0]."""
    from rl.rl_drug_ranker import DrugRankingEnv, PipelineConfig

    df = _build_v128_bridge_csv()
    env = DrugRankingEnv(data=df, config=PipelineConfig())

    assert env._features_array.shape[1] == env.observation_space.shape[0], (
        f"P4-006: _features_array.shape[1] ({env._features_array.shape[1]}) != "
        f"observation_space.shape[0] ({env.observation_space.shape[0]}). "
        f"The features array must have the same number of columns as the "
        f"observation space."
    )


def test_task_9_6_reset_preserves_observation_space_shape():
    """P4-006: reset() rebuilds _features_array — shape must be preserved."""
    from rl.rl_drug_ranker import DrugRankingEnv, PipelineConfig

    df = _build_v128_bridge_csv()
    env = DrugRankingEnv(data=df, config=PipelineConfig())
    original_shape = env.observation_space.shape[0]

    obs, _ = env.reset(seed=42)
    assert obs.shape[0] == original_shape, (
        f"P4-006: reset() produced obs with shape {obs.shape}, expected "
        f"({original_shape},). The shuffle+rebuild in reset() may have "
        f"dropped columns."
    )

    # step() must also produce obs with the correct shape.
    obs2, reward, done, truncated, info = env.step(1)
    assert obs2.shape[0] == original_shape, (
        f"P4-006: step() produced obs with shape {obs2.shape}, expected "
        f"({original_shape},)."
    )


def test_task_9_6_env_falls_back_for_old_bridge_csv():
    """P4-006: env handles old bridge CSVs (pre-v128) that don't emit the 5 new columns.

    For old bridge CSVs, the env fills the new columns with 0.0 (neutral).
    The observation_space.shape is STILL 18 — the agent always sees the
    same number of features (no shape drift between old/new bridges).
    """
    from rl.rl_drug_ranker import DrugRankingEnv, PipelineConfig

    rng = np.random.default_rng(99)
    # Old bridge CSV: only 12 columns (2 IDs + 10 features). No gnn_score_calibrated,
    # no gnn_score_timestamp, no disease_pair_count/avg_gnn/avg_safety.
    rows = []
    drugs = ["aspirin", "metformin"]
    diseases = ["cv", "t2dm"]
    for d in drugs:
        for dis in diseases:
            rows.append({
                "drug": d, "disease": dis,
                "gnn_score": float(rng.random()),
                "confidence": float(rng.random()),
                "safety_score": float(rng.random()),
                "market_score": float(rng.random()),
                "pathway_score": float(rng.random()),
                "patent_score": float(rng.random()),
                "rare_disease_flag": int(rng.integers(0, 2)),
                "unmet_need_score": float(rng.random()),
                "efficacy_score": float(rng.random()),
                "adme_score": float(rng.random()),
            })
    df = pd.DataFrame(rows)

    env = DrugRankingEnv(data=df, config=PipelineConfig())

    # observation_space.shape is STILL 18 — the new columns are filled with 0.0.
    assert env.observation_space.shape[0] == 18, (
        f"P4-006: old bridge CSV should produce observation_space.shape == (18,), "
        f"got {env.observation_space.shape}. The env must fill missing bridge "
        f"columns with 0.0 (neutral) so the observation_space shape is stable "
        f"across old/new bridge versions."
    )

    # The bridge columns must be present (filled with 0.0).
    from rl.constants import (
        GNN_SCORE_CALIBRATED_COL, GNN_SCORE_AGE_HOURS_COL,
        BRIDGE_DISEASE_PAIR_COUNT_COL,
    )
    assert GNN_SCORE_CALIBRATED_COL in env.data.columns
    assert GNN_SCORE_AGE_HOURS_COL in env.data.columns
    assert BRIDGE_DISEASE_PAIR_COUNT_COL in env.data.columns
    # The values should be 0.0 (since the old bridge didn't emit them).
    assert (env.data[GNN_SCORE_CALIBRATED_COL] == 0.0).all(), (
        f"P4-006: old bridge CSV should fill {GNN_SCORE_CALIBRATED_COL} with 0.0"
    )


def test_task_9_6_constants_module_exports_new_columns():
    """P4-006: rl/constants.py exports the 5 new bridge column constants."""
    from rl.constants import (
        GNN_SCORE_CALIBRATED_COL, GNN_SCORE_AGE_HOURS_COL,
        BRIDGE_DISEASE_PAIR_COUNT_COL, BRIDGE_DISEASE_AVG_GNN_COL,
        BRIDGE_DISEASE_AVG_SAFETY_COL, OPTIONAL_BRIDGE_FEATURE_COLS,
    )
    assert GNN_SCORE_CALIBRATED_COL == "gnn_score_calibrated"
    assert GNN_SCORE_AGE_HOURS_COL == "gnn_score_age_hours"
    assert BRIDGE_DISEASE_PAIR_COUNT_COL == "bridge_disease_pair_count"
    assert BRIDGE_DISEASE_AVG_GNN_COL == "bridge_disease_avg_gnn"
    assert BRIDGE_DISEASE_AVG_SAFETY_COL == "bridge_disease_avg_safety"
    assert len(OPTIONAL_BRIDGE_FEATURE_COLS) == 5


def test_task_9_6_env_module_re_exports_new_columns():
    """P4-006: rl/env.py re-exports the new bridge column constants."""
    from rl.env import (
        GNN_SCORE_CALIBRATED_COL, GNN_SCORE_AGE_HOURS_COL,
        BRIDGE_DISEASE_PAIR_COUNT_COL, BRIDGE_DISEASE_AVG_GNN_COL,
        BRIDGE_DISEASE_AVG_SAFETY_COL, OPTIONAL_BRIDGE_FEATURE_COLS,
    )
    assert GNN_SCORE_CALIBRATED_COL == "gnn_score_calibrated"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
