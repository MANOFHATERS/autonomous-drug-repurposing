"""
P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
Integration tests for the RL service's GTRLBridge caching.

THE BUG (forensic root cause):
  The previous ``_load_candidates_from_checkpoint`` in rl/service.py
  built a NEW ``GTRLBridge`` on EVERY /rank request:
      bridge = GTRLBridge(...)
      bridge.build_model()              # ← trains the graph transformer
      rl_input_df = bridge.generate_rl_input()   # ← full graph traversal
  On a real KG, ``build_model()`` takes minutes (it trains the graph
  transformer) and ``generate_rl_input()`` does a full graph traversal.
  Every /rank request added multi-minute latency — the pharma partner
  API was unusable.

ROOT FIX:
  ``get_cached_bridge()`` builds the bridge ONCE (lazy on first /rank
  call), then reuses it for all subsequent /rank requests. The PPO
  model + VecNormalize sidecar are still loaded per-request (the
  checkpoint may change between requests), but the expensive bridge
  build is cached.

  ``invalidate_bridge_cache()`` clears the cache. Called by the
  /reload endpoint (admin-only) after a KG update.

VERIFICATION:
  These tests verify:
    - First call to ``get_cached_bridge()`` builds the bridge (1 call).
    - Second call returns the SAME bridge (no rebuild).
    - ``invalidate_bridge_cache()`` clears the cache so the next call
      rebuilds.
    - The /reload endpoint requires admin auth (RL_ADMIN_TOKEN env var).

NOTE ON MOCKING:
  The tests inject a FAKE ``graph_transformer.gt_rl_bridge`` module
  into ``sys.modules`` so the tests don't need torch (a 2GB+ install).
  The fake module's ``GTRLBridge`` attribute is a MagicMock that
  records calls — this lets us verify the cache prevents rebuilds
  without actually training the graph transformer.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure repo root is on sys.path so `import rl.service` works.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Tests must disable RL_REQUIRE_AUTH so importing rl.service doesn't
# require org_id for every request.
os.environ.setdefault("RL_REQUIRE_AUTH", "false")


@pytest.fixture
def mock_gt_rl_bridge():
    """Inject a FAKE graph_transformer.gt_rl_bridge module into sys.modules.

    The real ``graph_transformer/__init__.py`` imports torch (2GB+ dep).
    Tests don't need torch — they just need to verify the CACHE LOGIC of
    ``get_cached_bridge()``. This fixture pre-populates ``sys.modules``
    with a fake module whose ``GTRLBridge`` attribute is a MagicMock.

    When ``get_cached_bridge()`` does:
        from graph_transformer.gt_rl_bridge import GTRLBridge
    Python's import system finds the fake module in ``sys.modules`` and
    returns its ``GTRLBridge`` attribute (a MagicMock) — WITHOUT running
    the real ``graph_transformer/__init__.py`` (which would import torch).

    Yields:
        Tuple of (fake_module, fake_bridge_instance).
    """
    # Create a fake bridge INSTANCE (returned by GTRLBridge()).
    fake_bridge = MagicMock(name="FakeGTRLBridgeInstance")
    fake_bridge.generate_rl_input.return_value = [1, 2, 3]

    # Create a fake GTRLBridge CLASS.
    fake_class = MagicMock(name="FakeGTRLBridgeClass", return_value=fake_bridge)

    # Create the fake module.
    fake_module = types.ModuleType("graph_transformer.gt_rl_bridge")
    fake_module.GTRLBridge = fake_class

    # Also create a fake parent package.
    fake_pkg = types.ModuleType("graph_transformer")
    fake_pkg.__path__ = []
    fake_pkg.gt_rl_bridge = fake_module

    orig_pkg = sys.modules.get("graph_transformer")
    orig_sub = sys.modules.get("graph_transformer.gt_rl_bridge")

    sys.modules["graph_transformer"] = fake_pkg
    sys.modules["graph_transformer.gt_rl_bridge"] = fake_module

    try:
        yield fake_module, fake_bridge
    finally:
        if orig_pkg is not None:
            sys.modules["graph_transformer"] = orig_pkg
        else:
            sys.modules.pop("graph_transformer", None)
        if orig_sub is not None:
            sys.modules["graph_transformer.gt_rl_bridge"] = orig_sub
        else:
            sys.modules.pop("graph_transformer.gt_rl_bridge", None)


@pytest.fixture(autouse=True)
def reset_bridge_cache():
    """Reset the bridge cache before and after each test."""
    import rl.service as svc
    svc._bridge_cache = None
    svc._rl_input_cache = None
    yield
    svc._bridge_cache = None
    svc._rl_input_cache = None


@pytest.mark.integration
def test_rl_service_caches_bridge_at_startup(mock_gt_rl_bridge):
    """P4-024 acceptance criterion 6: bridge is cached at startup (no rebuild)."""
    import rl.service as svc

    fake_module, fake_bridge = mock_gt_rl_bridge
    svc._bridge_cache = None
    svc._rl_input_cache = None

    # First call — should build the bridge.
    bridge1, input1 = svc.get_cached_bridge()
    assert fake_module.GTRLBridge.call_count == 1, (
        f"Expected 1 GTRLBridge constructor call on first get_cached_bridge(), "
        f"got {fake_module.GTRLBridge.call_count}"
    )
    assert bridge1 is fake_bridge
    assert input1 == [1, 2, 3]

    # Second call — should use the cache (no new GTRLBridge).
    bridge2, input2 = svc.get_cached_bridge()
    assert fake_module.GTRLBridge.call_count == 1, (
        f"Expected STILL 1 GTRLBridge constructor call after second get_cached_bridge() "
        f"(cache should prevent rebuild), got {fake_module.GTRLBridge.call_count}"
    )
    assert bridge1 is bridge2
    assert input1 is input2


@pytest.mark.integration
def test_invalidate_bridge_cache_forces_rebuild(mock_gt_rl_bridge):
    """P4-024: invalidate_bridge_cache() clears the cache so the next call rebuilds."""
    import rl.service as svc

    fake_module, fake_bridge_v1 = mock_gt_rl_bridge
    fake_bridge_v1.generate_rl_input.return_value = ["v1_row"]

    svc._bridge_cache = None
    svc._rl_input_cache = None

    # First call — builds bridge v1.
    bridge1, input1 = svc.get_cached_bridge()
    assert fake_module.GTRLBridge.call_count == 1
    assert bridge1 is fake_bridge_v1
    assert input1 == ["v1_row"]

    # Invalidate.
    svc.invalidate_bridge_cache()
    assert svc._bridge_cache is None
    assert svc._rl_input_cache is None

    # Second call — should rebuild.
    fake_bridge_v2 = MagicMock(name="FakeGTRLBridgeV2")
    fake_bridge_v2.generate_rl_input.return_value = ["v2_row"]
    fake_module.GTRLBridge.return_value = fake_bridge_v2

    bridge2, input2 = svc.get_cached_bridge()
    assert fake_module.GTRLBridge.call_count == 2, (
        f"Expected 2 GTRLBridge constructor calls after invalidate + get, "
        f"got {fake_module.GTRLBridge.call_count}"
    )
    assert bridge2 is fake_bridge_v2
    assert input2 == ["v2_row"]


@pytest.mark.integration
def test_reload_endpoint_requires_admin_token():
    """P4-024 acceptance criterion 7: /reload endpoint requires admin auth."""
    from fastapi.testclient import TestClient
    import rl.service as svc

    client = TestClient(svc.app)

    # Case 1: RL_ADMIN_TOKEN not set → 503.
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("RL_ADMIN_TOKEN", None)
        response = client.post("/reload")
        assert response.status_code == 503, (
            f"Expected 503 when RL_ADMIN_TOKEN is not set, got {response.status_code}"
        )
        assert "RL_ADMIN_TOKEN" in response.json()["detail"]

    # Case 2: RL_ADMIN_TOKEN set, no Authorization header → 401.
    with patch.dict(os.environ, {"RL_ADMIN_TOKEN": "secret-admin-token-12345"}):
        response = client.post("/reload")
        assert response.status_code == 401, (
            f"Expected 401 without Authorization header, got {response.status_code}"
        )

    # Case 3: RL_ADMIN_TOKEN set, wrong token → 401.
    with patch.dict(os.environ, {"RL_ADMIN_TOKEN": "secret-admin-token-12345"}):
        response = client.post(
            "/reload",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401, (
            f"Expected 401 with wrong token, got {response.status_code}"
        )

    # Case 4: RL_ADMIN_TOKEN set, correct token → 200 + cache invalidated.
    with patch.dict(os.environ, {"RL_ADMIN_TOKEN": "secret-admin-token-12345"}):
        svc._bridge_cache = "fake_bridge"
        svc._rl_input_cache = "fake_input"
        response = client.post(
            "/reload",
            headers={"Authorization": "Bearer secret-admin-token-12345"},
        )
        assert response.status_code == 200, (
            f"Expected 200 with correct token, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["status"] == "reloaded"
        assert svc._bridge_cache is None
        assert svc._rl_input_cache is None


@pytest.mark.integration
def test_get_cached_bridge_handles_empty_rl_input(mock_gt_rl_bridge):
    """P4-024: get_cached_bridge logs a warning when the bridge produces empty RL input."""
    import rl.service as svc

    fake_module, fake_bridge = mock_gt_rl_bridge
    fake_bridge.generate_rl_input.return_value = []

    svc._bridge_cache = None
    svc._rl_input_cache = None

    bridge, rl_input = svc.get_cached_bridge()
    assert bridge is fake_bridge
    assert rl_input == []
    assert svc._bridge_cache is fake_bridge
    assert svc._rl_input_cache == []
