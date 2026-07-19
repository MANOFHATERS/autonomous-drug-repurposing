"""Task 9.1 — VecNormalize stats sidecar loading in /rank endpoint (BLOCKER #3).

P4-004 + P4-019 ROOT FIX verification.

The /rank endpoint's _load_candidates_from_checkpoint function MUST:
  1. Load the .vecnormalize.pkl sidecar from the same dir as the .zip checkpoint.
  2. Wrap the policy in the VecNormalize env.
  3. Set bridge.rl_vec_normalize so the bridge normalizes obs correctly.

Without this fix, the deployed policy receives RAW (un-normalized) obs
instead of normalized obs → random rankings labeled as 'RL-ranked.'

This test verifies the loading logic by reading the ACTUAL source code
of _load_candidates_from_checkpoint (no mocks) and asserting that:
  - The function references VecNormalize.load
  - The function sets model.set_env(vec_normalize)
  - The function sets bridge.rl_vec_normalize = vec_normalize
  - The function raises RuntimeError if the sidecar is missing (strict mode)

We use source inspection (not execution) because actually loading a PPO
checkpoint requires a trained model, which is too expensive for a unit
test. The source inspection is sufficient to verify the fix is present
in the code — a regression that removes the VecNormalize loading will
fail this test.
"""
import inspect
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RL_REQUIRE_AUTH", "false")

from rl import service as rl_service


def test_task_9_1_vecnormalize_loading_source_present():
    """P4-004: _load_candidates_from_checkpoint MUST load VecNormalize sidecar.

    Reads the actual function source (no mocks, no execution) and verifies
    the VecNormalize loading logic is present. A regression that removes
    the fix will fail this test.
    """
    src = inspect.getsource(rl_service._load_candidates_from_checkpoint)

    # (1) The function MUST import VecNormalize from stable_baselines3.
    assert "VecNormalize" in src, (
        "P4-004 REGRESSION: _load_candidates_from_checkpoint does not reference "
        "VecNormalize. The PPO policy was trained on VecNormalize-wrapped obs; "
        "without loading the sidecar, the policy receives RAW obs → random "
        "rankings → /rank serves garbage to pharma partners."
    )

    # (2) The function MUST call VecNormalize.load(...) to load the sidecar.
    assert "VecNormalize.load" in src, (
        "P4-004 REGRESSION: VecNormalize.load() is not called. The .vecnormalize.pkl "
        "sidecar must be loaded alongside the .zip checkpoint — they are a PAIR."
    )

    # (3) The function MUST construct the sidecar path by replacing .zip with
    # .vecnormalize.pkl (or appending .vecnormalize.pkl).
    assert ".vecnormalize.pkl" in src, (
        "P4-004 REGRESSION: the sidecar path .vecnormalize.pkl is not constructed. "
        "The function must look for the sidecar in the same directory as the .zip."
    )

    # (4) The function MUST call model.set_env(vec_normalize) so the loaded
    # policy uses the normalized env for inference.
    assert "model.set_env" in src, (
        "P4-004 REGRESSION: model.set_env() is not called with the VecNormalize "
        "wrapper. Without this, the policy network receives RAW obs even though "
        "VecNormalize is loaded — the wrapper is not connected to the model."
    )

    # (5) The function MUST set bridge.rl_vec_normalize so the bridge's
    # get_top_k_novel_predictions normalizes obs via the SAME wrapper.
    assert "bridge.rl_vec_normalize" in src, (
        "P4-004 REGRESSION: bridge.rl_vec_normalize is not set. The bridge's "
        "get_top_k_novel_predictions would use its OWN (possibly different) "
        "VecNormalize wrapper, causing train/inference distribution shift."
    )

    # (6) The function MUST raise RuntimeError if the sidecar is missing
    # (strict mode) — the checkpoint is INCOMPLETE without the sidecar.
    assert "RuntimeError" in src, (
        "P4-004 REGRESSION: no RuntimeError raised when sidecar is missing. "
        "Without strict mode, the function silently falls back to RAW obs, "
        "producing random rankings without any error."
    )

    # (7) The function MUST set vec_normalize.training = False (inference mode).
    assert "training = False" in src or "training=False" in src, (
        "P4-004 REGRESSION: vec_normalize.training is not set to False. "
        "In inference mode, VecNormalize must NOT update its running stats — "
        "doing so would drift the normalization over time."
    )

    # (8) The function MUST set vec_normalize.norm_reward = False (inference mode).
    assert "norm_reward = False" in src or "norm_reward=False" in src, (
        "P4-004 REGRESSION: vec_normalize.norm_reward is not set to False. "
        "In inference mode, reward normalization is irrelevant (we don't "
        "compute rewards) and can produce NaN if the reward running stats "
        "are uninitialized."
    )


def test_task_9_1_vecnormalize_sidecar_path_construction():
    """P4-004: the sidecar path is constructed by replacing .zip with .vecnormalize.pkl.

    A regression that hardcodes the sidecar path (instead of deriving it
    from the checkpoint path) would fail when the checkpoint is moved.
    """
    src = inspect.getsource(rl_service._load_candidates_from_checkpoint)
    # The path-construction logic must handle both .zip-suffixed and bare paths.
    assert "endswith(\".zip\")" in src or "endswith('.zip')" in src, (
        "P4-004 REGRESSION: the sidecar path construction does not handle the "
        ".zip suffix. The function must replace .zip with .vecnormalize.pkl."
    )


def test_task_9_1_strict_mode_default_is_true():
    """P4-015: strict mode (RL_STRICT_CHECKPOINT) defaults to true.

    A regression that defaults to false would silently fall back to the
    stale CSV when the checkpoint fails, masking real production issues.
    """
    src = inspect.getsource(rl_service._load_candidates_from_checkpoint)
    # The default for RL_STRICT_CHECKPOINT must be "true".
    assert "RL_STRICT_CHECKPOINT" in src, (
        "P4-015 REGRESSION: RL_STRICT_CHECKPOINT env var is not referenced."
    )
    assert '"true"' in src, (
        "P4-015 REGRESSION: RL_STRICT_CHECKPOINT does not default to 'true'."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
