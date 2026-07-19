"""TASK 8.1 verification: VecNormalize stats sidecar is loaded by the
scientific validation gate.

This test trains a real PPO model with train_agent (which saves the
.vecnormalize.pkl sidecar alongside the .zip checkpoint), then calls
run_scientific_validation_gate and verifies:

  1. The sidecar file exists at <checkpoint>.vecnormalize.pkl
  2. The gate loads it WITHOUT raising RuntimeError
  3. The gate produces a non-None AUC
  4. Strict mode: deleting the sidecar makes the gate raise RuntimeError
     (the gate REFUSES to run on an incomplete checkpoint)

The test is HOSTILE-AUDITOR: it does not trust comments, does not trust
existing tests, and verifies the ACTUAL behavior by running real code.
"""
import os
import sys
import shutil
import tempfile
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from rl.train import train_agent
from rl.validate import run_scientific_validation_gate
from rl.env import DrugRankingEnv
from rl.rl_drug_ranker import generate_fake_data, PipelineConfig


@pytest.fixture(scope="module")
def trained_checkpoint(tmp_path_factory):
    """Train a small PPO model and return (checkpoint_path, sidecar_path, config)."""
    tmp_dir = tmp_path_factory.mktemp("rl_train_81")
    cfg = PipelineConfig()
    cfg.checkpoint_dir = str(tmp_dir)
    cfg.output_dir = str(tmp_dir)
    cfg.ppo_n_steps = 64
    cfg.ppo_batch_size = 32

    train_data = generate_fake_data(n_pairs=100, seed=42)
    # Mark env as NOT standalone so checkpoint gets saved.
    train_data.attrs['_standalone_mode'] = False
    train_data.attrs['_standalone_mode_reason'] = ''
    env = DrugRankingEnv(data=train_data, config=cfg)

    model, ckpt_path, vec_normalize = train_agent(
        env=env, timesteps=200, seed=42, config=cfg
    )
    assert ckpt_path is not None, "train_agent refused to save checkpoint (standalone mode)"
    sidecar_path = ckpt_path[:-len(".zip")] + ".vecnormalize.pkl"
    return ckpt_path, sidecar_path, cfg


def test_sidecar_exists(trained_checkpoint):
    """(1) train_agent saves the .vecnormalize.pkl sidecar alongside the .zip."""
    ckpt_path, sidecar_path, _ = trained_checkpoint
    assert os.path.isfile(ckpt_path), f"checkpoint missing: {ckpt_path}"
    assert os.path.isfile(sidecar_path), f"sidecar missing: {sidecar_path}"
    assert os.path.getsize(sidecar_path) > 0, "sidecar is empty (0 bytes)"


def test_gate_loads_sidecar_without_error(trained_checkpoint):
    """(2) run_scientific_validation_gate loads the sidecar without RuntimeError."""
    ckpt_path, sidecar_path, cfg = trained_checkpoint
    assert os.path.isfile(sidecar_path), "precondition: sidecar must exist"

    test_data = generate_fake_data(n_pairs=50, seed=99)
    result = run_scientific_validation_gate(
        checkpoint_path=ckpt_path,
        test_data=test_data,
        config=cfg,
        top_n=5,
        thresholds={
            "gt_test_auc": 0.0,
            "rl_auc": 0.0,
            "kp_recovery": 0.0,
            "literature_min": 0,
        },
    )
    assert isinstance(result, dict)
    assert "overall_pass" in result
    assert "checks" in result
    assert "report" in result
    # AUC must be a real number (not None, not NaN) — proves the gate
    # actually ran the policy network with normalized obs.
    auc = result["report"].get("auc")
    assert auc is not None, "AUC is None — gate did not run policy network"
    assert not (isinstance(auc, float) and auc != auc), "AUC is NaN"


def test_strict_mode_raises_when_sidecar_missing(trained_checkpoint):
    """(4) Deleting the sidecar makes the gate raise RuntimeError (strict mode)."""
    ckpt_path, sidecar_path, cfg = trained_checkpoint
    assert os.path.isfile(sidecar_path), "precondition: sidecar must exist"

    # Make a COPY of the checkpoint so we can delete the sidecar without
    # breaking the other tests.
    tmp_dir = tempfile.mkdtemp(prefix="rl_strict_")
    try:
        ckpt_copy = os.path.join(tmp_dir, "model.zip")
        sidecar_copy = os.path.join(tmp_dir, "model.vecnormalize.pkl")
        shutil.copy2(ckpt_path, ckpt_copy)
        # INTENTIONALLY do NOT copy the sidecar — the gate must refuse.

        test_data = generate_fake_data(n_pairs=50, seed=99)
        with pytest.raises(RuntimeError, match="VecNormalize sidecar NOT FOUND"):
            run_scientific_validation_gate(
                checkpoint_path=ckpt_copy,
                test_data=test_data,
                config=cfg,
                top_n=5,
                thresholds={
                    "gt_test_auc": 0.0,
                    "rl_auc": 0.0,
                    "kp_recovery": 0.0,
                    "literature_min": 0,
                },
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
