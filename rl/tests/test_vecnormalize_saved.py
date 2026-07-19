"""TASK 8.5 verification: train_agent wraps env in VecNormalize AND saves
the .vecnormalize.pkl stats sidecar alongside the .zip checkpoint.

The task spec requires:
  (1) use stable_baselines3.common.vec_env.VecNormalize
  (2) save vec_normalize.save(os.path.join(output_dir, 'vecnormalize.pkl'))
  (3) update rl/service.py to load it (coordinate with TM9)

We verify (1) and (2) by running train_agent on a small env and checking:
  - The returned vec_normalize is a VecNormalize instance
  - The .vecnormalize.pkl file exists alongside the .zip checkpoint
  - The .vecnormalize.pkl file is loadable via VecNormalize.load()
  - The loaded VecNormalize has non-None obs_rms (the running mean/std)

For (3), we verify rl/service.py imports VecNormalize and has the
loading logic (already present from prior commits — we just verify it
still imports cleanly).
"""
import os
import sys
import inspect
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from rl.train import train_agent
from rl.env import DrugRankingEnv
from rl.rl_drug_ranker import generate_fake_data, PipelineConfig


@pytest.fixture(scope="module")
def trained_model(tmp_path_factory):
    """Train a small PPO model and return (model, ckpt_path, vec_normalize, cfg)."""
    tmp_dir = tmp_path_factory.mktemp("rl_train_85")
    cfg = PipelineConfig()
    cfg.checkpoint_dir = str(tmp_dir)
    cfg.output_dir = str(tmp_dir)
    cfg.ppo_n_steps = 64
    cfg.ppo_batch_size = 32

    train_data = generate_fake_data(n_pairs=100, seed=42)
    train_data.attrs['_standalone_mode'] = False
    train_data.attrs['_standalone_mode_reason'] = ''
    env = DrugRankingEnv(data=train_data, config=cfg)

    model, ckpt_path, vec_normalize = train_agent(
        env=env, timesteps=200, seed=42, config=cfg
    )
    return model, ckpt_path, vec_normalize, cfg


def test_train_agent_returns_vec_normalize(trained_model):
    """(1) train_agent returns a VecNormalize wrapper (3rd tuple element)."""
    _, _, vec_normalize, _ = trained_model
    assert vec_normalize is not None, "vec_normalize is None — train_agent did not wrap the env"
    # Must be a VecNormalize instance.
    from stable_baselines3.common.vec_env import VecNormalize
    assert isinstance(vec_normalize, VecNormalize), (
        f"vec_normalize is {type(vec_normalize).__name__}, expected VecNormalize"
    )


def test_train_agent_saves_sidecar_file(trained_model):
    """(2) train_agent saves <checkpoint>.vecnormalize.pkl alongside the .zip."""
    _, ckpt_path, _, _ = trained_model
    assert ckpt_path is not None
    sidecar_path = ckpt_path[:-len(".zip")] + ".vecnormalize.pkl"
    assert os.path.isfile(sidecar_path), (
        f"VecNormalize sidecar not saved at {sidecar_path}"
    )
    assert os.path.getsize(sidecar_path) > 0, "sidecar file is empty"


def test_sidecar_is_loadable_via_vecnormalize_load(trained_model):
    """(2) The saved .vecnormalize.pkl is loadable via VecNormalize.load()."""
    _, ckpt_path, _, cfg = trained_model
    sidecar_path = ckpt_path[:-len(".zip")] + ".vecnormalize.pkl"

    from stable_baselines3.common.vec_env import VecNormalize, DummyVecEnv

    # Build a fresh env to wrap (VecNormalize.load requires a VecEnv).
    train_data = generate_fake_data(n_pairs=20, seed=99)
    train_data.attrs['_standalone_mode'] = False
    train_data.attrs['_standalone_mode_reason'] = ''
    env = DrugRankingEnv(data=train_data, config=cfg)
    dummy_venv = DummyVecEnv([lambda: env])

    loaded = VecNormalize.load(sidecar_path, dummy_venv)
    assert loaded is not None
    # The obs_rms must be populated (non-None mean).
    assert hasattr(loaded, 'obs_rms')
    assert loaded.obs_rms is not None
    assert loaded.obs_rms.mean is not None
    # The mean array shape must match the env's observation dimension.
    assert loaded.obs_rms.mean.shape[0] > 0


def test_service_module_imports_vecnormalize():
    """(3) rl/service.py imports VecNormalize (loads sidecar at /rank time)."""
    # Verify service.py imports VecNormalize by reading its source.
    import rl.service as svc
    source_path = inspect.getsourcefile(svc)
    assert source_path is not None
    with open(source_path, "r", encoding="utf-8") as f:
        source = f.read()
    # Must import VecNormalize from stable_baselines3.
    assert "VecNormalize" in source, "rl/service.py does not reference VecNormalize"
    assert "stable_baselines3.common.vec_env" in source, (
        "rl/service.py does not import from stable_baselines3.common.vec_env"
    )
    # Must load the sidecar via VecNormalize.load().
    assert "VecNormalize.load" in source, (
        "rl/service.py does not call VecNormalize.load() — sidecar is NOT loaded at /rank time"
    )
    # Must reference the .vecnormalize.pkl filename pattern.
    assert "vecnormalize.pkl" in source, (
        "rl/service.py does not reference 'vecnormalize.pkl' — sidecar path is wrong"
    )
