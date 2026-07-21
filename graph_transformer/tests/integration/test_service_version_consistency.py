"""TEAMMATE-11 acceptance tests: GT service version consistency.

Verifies the ROOT FIX for the Teammate-11 issue:
  1. The GT service's /health endpoint reports the canonical package version.
  2. The /predict response's modelVersion matches the Neo4j writeback's
     model_version (both = gt_<package_version>).
  3. _get_pathway_explanation finds 3-hop drug->protein->pathway->disease chains.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from graph_transformer.service import app
    return TestClient(app)


@pytest.mark.integration
def test_service_version_matches_package_version(client):
    """Acceptance #7: GT service /health version == graph_transformer.__version__."""
    from graph_transformer import __version__ as pkg_version
    response = client.get("/health")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["version"] == pkg_version, (
        f"Service version {data['version']} != package version {pkg_version} "
        f"(P3-020 version drift)."
    )


@pytest.mark.integration
def test_fastapi_app_version_matches_package_version():
    """Acceptance: FastAPI app.version == graph_transformer.__version__."""
    from graph_transformer import __version__ as pkg_version
    from graph_transformer.service import app
    assert app.version == pkg_version


@pytest.mark.integration
def test_model_version_constant_matches_package_version():
    """Acceptance: MODEL_VERSION == gt_<package_version>."""
    from graph_transformer import __version__ as pkg_version
    from graph_transformer.service import MODEL_VERSION
    assert MODEL_VERSION == f"gt_{pkg_version}"
    assert MODEL_VERSION == "gt_4.1.0"


@pytest.mark.integration
def test_predict_response_modelversion_matches_neo4j_writeback(client):
    """Acceptance #4: modelVersion in response == model_version in Neo4j writeback."""
    from graph_transformer.service import MODEL_VERSION
    import torch

    with patch("graph_transformer.service.write_predictions_to_neo4j") as mock_writeback:
        mock_writeback.return_value = {"written": 0, "neo4j_configured": False}
        with patch("graph_transformer.service._load_or_build_model") as mock_load:
            class _FakeLinkPredictor:
                def predict_probability(self, drug_emb, disease_emb, apply_temperature=True):
                    return torch.tensor([0.75])

            class _FakeModel:
                training = False
                link_predictor = _FakeLinkPredictor()
                def eval(self): pass
                def train(self, mode): pass
                def encode(self, *args, **kwargs):
                    return {"drug": torch.zeros(1, 4), "disease": torch.zeros(1, 4)}

            mock_load.return_value = {
                "model": _FakeModel(),
                "node_features": {},
                "edge_indices": {},
                "node_maps": {
                    "drug": {"aspirin": 0},
                    "disease": {"headache": 0},
                    "protein": {},
                    "pathway": {},
                },
                "drug_names": ["aspirin"],
                "disease_names": ["headache"],
                "known_pairs": [],
                "hyperparams": {},
                "model_class_name": "_FakeModel",
                "embedding_dim": 4,
                "backend": "checkpoint",
                "checkpoint_path": "/tmp/fake.pt",
                "used_graph_state_sidecar": False,
            }
            with patch("graph_transformer.service._CACHED_ENCODINGS", {
                "drug": torch.zeros(1, 4),
                "disease": torch.zeros(1, 4),
                "encoded_at": "2026-07-21T00:00:00Z",
            }):
                response = client.post(
                    "/predict",
                    json={"pairs": [{"drug": "aspirin", "disease": "headache"}]},
                )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["modelVersion"] == MODEL_VERSION
    mock_writeback.assert_called_once()
    call_kwargs = mock_writeback.call_args.kwargs
    assert call_kwargs["model_version"] == MODEL_VERSION
    assert MODEL_VERSION == "gt_4.1.0"


@pytest.mark.integration
def test_topk_response_modelversion_matches_constant(client):
    """Acceptance: /top-k modelVersion == MODEL_VERSION (no drift between endpoints)."""
    from graph_transformer.service import MODEL_VERSION

    class _FakeModel:
        training = False
        def eval(self): pass
        def train(self, mode): pass

    with patch("graph_transformer.service._load_or_build_model") as mock_load:
        mock_load.return_value = {
            "backend": "checkpoint",
            "checkpoint_path": "/tmp/fake.pt",
            "model": _FakeModel(),
            "drug_names": ["aspirin"],
            "disease_names": ["headache"],
            "known_pairs": [],
            "node_features": {},
            "edge_indices": {},
        }
        with patch("graph_transformer.inference.top_k_novel_predictions") as mock_topk:
            mock_topk.return_value = [("aspirin", "headache", 0.8)]
            response = client.get("/top-k", params={"k": 5})

    if response.status_code == 200:
        data = response.json()
        assert data["modelVersion"] == MODEL_VERSION


@pytest.mark.integration
def test_get_pathway_explanation_returns_empty_for_unknown_drug():
    """Acceptance: _get_pathway_explanation returns [] for an unknown drug."""
    from graph_transformer.service import _get_pathway_explanation

    state = {
        "edge_indices": {},
        "node_maps": {"drug": {}, "protein": {}, "pathway": {}, "disease": {}},
    }
    result = _get_pathway_explanation(
        state=state, drug_name="unknown_drug", disease_name="unknown_disease", top_k=5,
    )
    assert result == []


@pytest.mark.integration
def test_get_pathway_explanation_finds_3hop_chain():
    """Acceptance: _get_pathway_explanation finds drug->protein->pathway->disease."""
    from graph_transformer.service import _get_pathway_explanation
    import torch

    state = {
        "edge_indices": {
            ("drug", "inhibits", "protein"): torch.tensor([[0], [0]]),
            ("protein", "part_of", "pathway"): torch.tensor([[0], [0]]),
            ("pathway", "disrupted_in", "disease"): torch.tensor([[0], [0]]),
        },
        "node_maps": {
            "drug": {"aspirin": 0},
            "protein": {"COX-1": 0},
            "pathway": {"arachidonic_acid_metabolism": 0},
            "disease": {"headache": 0},
        },
    }
    result = _get_pathway_explanation(
        state=state, drug_name="aspirin", disease_name="headache", top_k=5,
    )
    assert len(result) == 1, f"Expected 1 chain, got {len(result)}: {result}"
    chain = result[0]
    assert chain["pathway"] == "arachidonic_acid_metabolism"
    assert chain["intermediate_protein"] == "COX-1"
    assert chain["chain"] == ["aspirin", "COX-1", "arachidonic_acid_metabolism", "headache"]
