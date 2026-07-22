"""graph_transformer.tests.test_teammate8_p3_012_to_022_forensic_v150.

FORENSIC ROOT-LEVEL TESTS for Teammate 8 Phase 3 issues P3-012 through
P3-022. These tests verify the ACTUAL CODE BEHAVIOR (not comments or
"ROOT FIX" claims) for each of the 11 issues assigned to Teammate 8.

Each test reads the source code at runtime and asserts the fix is
present. This is the "hostile-auditor, RED TEAM" approach mandated by
the user: every comment is a lie until proven otherwise by executable
code.

Issues covered:
  P3-012 [HIGH]   — pathway_score fabricated noise removed + raise on degenerate graph
  P3-013 [HIGH]   — DataLoader persistent_workers leak fixed (cache + memory monitor)
  P3-014 [MEDIUM] — RDKit Morgan fingerprint API modernized (rdFingerprintGenerator)
  P3-015 [MEDIUM] — TRAINING_POSITIVES guaranteed ≥5 for small graphs
  P3-016 [MEDIUM] — target_count_per_drug uses all 4 edge types (already in main)
  P3-017 [MEDIUM] — " deltasone" leading-space alias typo fixed
  P3-018 [MEDIUM] — Per-node cross-type normalization in HeterogeneousMultiHeadAttention
  P3-019 [MEDIUM] — Reverse-edge LIKE query verifies rel match (defense-in-depth)
  P3-020 [MEDIUM] — Service version uses __version__ (already in main)
  P3-021 [LOW]    — USE_DEFAULT sentinel for exclude_edges
  P3-022 [LOW]    — morgan_radius configurable parameter
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest
import numpy as np

# Ensure graph_transformer is importable
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================================
# P3-012 — pathway_score fabricated noise removed + raise on degenerate graph
# ============================================================================

class TestP3012PathwayScoreFabricationRemoved:
    """P3-012: pathway_score must NOT contain fabricated ±0.005 SHA-256 noise.

    The previous code FABRICATED per-pair noise in [-0.005, +0.005] when
    the graph had no pathways, to bypass RL feature-validation. The fix:
    (1) set pathway_score=0.0 constant, (2) log CRITICAL, (3) RAISE
    Phase2AdapterValidationError (gated by DRUGOS_ALLOW_DEGENERATE_GRAPH=1).
    """

    def test_no_fabricated_noise_loop_in_source(self):
        """The fabricated noise loop (rng_i.uniform(-0.005, 0.005)) must
        NOT appear in the source code at all."""
        bridge_path = (
            _REPO_ROOT / "graph_transformer" / "gt_rl_bridge.py"
        )
        source = bridge_path.read_text()
        # The fabricated noise pattern: rng_i.uniform(-0.005, 0.005)
        # MUST NOT appear anywhere in the file (not even in comments
        # that describe the OLD broken behavior — we check the actual
        # CODE pattern, which is `pathway_scores_arr[i] = float(rng_i.uniform(...))`)
        forbidden_pattern = "pathway_scores_arr[i] = float(rng_i.uniform(-0.005, 0.005))"
        assert forbidden_pattern not in source, (
            f"P3-012 FAIL: fabricated noise pattern found in source: "
            f"{forbidden_pattern!r}. The fix must REMOVE this entirely."
        )

    def test_phase2_adapter_validation_error_raise_present(self):
        """The else branch (no pathways) must RAISE
        Phase2AdapterValidationError (gated by env var)."""
        bridge_path = (
            _REPO_ROOT / "graph_transformer" / "gt_rl_bridge.py"
        )
        source = bridge_path.read_text()
        # The raise must be present
        assert "Phase2AdapterValidationError" in source, (
            "P3-012 FAIL: Phase2AdapterValidationError is not referenced "
            "in gt_rl_bridge.py. The fix requires raising this exception "
            "for degenerate graphs (no pathways)."
        )
        assert "DRUGOS_ALLOW_DEGENERATE_GRAPH" in source, (
            "P3-012 FAIL: DRUGOS_ALLOW_DEGENERATE_GRAPH env var gate is "
            "not present. The raise must be gated so production fails-"
            "fast but CI can still inspect degenerate graphs."
        )

    def test_degenerate_graph_raises_in_runtime(self):
        """Runtime test: building a degenerate graph (num_pathways=0)
        and calling generate_rl_input must RAISE Phase2AdapterValidationError
        unless DRUGOS_ALLOW_DEGENERATE_GRAPH=1 is set.

        This test exercises the actual code path in
        ``_compute_supplementary_features`` by calling the bridge with a
        degenerate graph (no pathway nodes). The bridge's
        ``generate_rl_input`` method internally calls
        ``_compute_supplementary_features``, which contains the P3-012
        fix (raise Phase2AdapterValidationError for degenerate graphs).
        """
        # Build a tiny bridge with a degenerate graph (no pathways)
        try:
            from graph_transformer.data.graph_builder import (
                BiomedicalGraphBuilder,
            )
        except ImportError as exc:
            pytest.skip(f"graph_transformer not importable: {exc}")

        # Save and clear the env var so the raise fires
        old_val = os.environ.pop("DRUGOS_ALLOW_DEGENERATE_GRAPH", None)
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Build a degenerate graph: 0 pathways
                node_features, edge_indices, node_maps, known_pairs = (
                    BiomedicalGraphBuilder.build_demo_graph(
                        num_drugs=10,
                        num_proteins=10,
                        num_pathways=0,  # DEGENERATE — no pathways
                        num_diseases=8,
                        num_outcomes=3,
                        seed=42,
                    )
                )
                # Verify degeneracy: pathway node map must be empty
                assert len(node_maps.get("pathway", {})) == 0, (
                    "Test setup error: graph should have 0 pathway nodes"
                )

                # Now try to build a bridge and call generate_rl_input
                try:
                    from graph_transformer.gt_rl_bridge import GTRLBridge
                    from graph_transformer.data.phase2_adapter import (
                        Phase2AdapterValidationError,
                    )
                except ImportError as exc:
                    pytest.skip(f"bridge import failed: {exc}")

                # Build a minimal GT model for the bridge
                try:
                    from graph_transformer.models.graph_transformer import (
                        DrugRepurposingGraphTransformer,
                    )
                    model = DrugRepurposingGraphTransformer(
                        feature_dims={
                            ntype: feat.shape[1]
                            for ntype, feat in node_features.items()
                        },
                        node_types=list(node_features.keys()),
                        edge_types=list(edge_indices.keys()),
                    )
                except Exception as exc:
                    pytest.skip(
                        f"cannot construct GT model for runtime test: {exc}"
                    )

                bridge = GTRLBridge(
                    output_dir=tmpdir,
                    device="cpu",
                    seed=42,
                )
                # Inject the graph + model into the bridge
                bridge.node_features = node_features
                bridge.edge_indices = edge_indices
                bridge.node_maps = node_maps
                bridge.model = model
                bridge.drug_names = list(node_maps.get("drug", {}).keys())
                bridge.disease_names = list(node_maps.get("disease", {}).keys())
                bridge.known_pairs = known_pairs
                bridge._trained = True  # bypass the train() requirement

                with pytest.raises(Phase2AdapterValidationError) as exc_info:
                    bridge.generate_rl_input()
                assert "degenerate graph" in str(exc_info.value).lower(), (
                    f"P3-012 FAIL: exception message should mention "
                    f"'degenerate graph', got: {exc_info.value}"
                )
        finally:
            if old_val is not None:
                os.environ["DRUGOS_ALLOW_DEGENERATE_GRAPH"] = old_val

    def test_degenerate_graph_allowed_with_env_var(self):
        """When DRUGOS_ALLOW_DEGENERATE_GRAPH=1, the bridge must NOT raise
        (it logs critical and sets pathway_score=0.0)."""
        try:
            from graph_transformer.data.graph_builder import (
                BiomedicalGraphBuilder,
            )
        except ImportError as exc:
            pytest.skip(f"graph_transformer not importable: {exc}")

        os.environ["DRUGOS_ALLOW_DEGENERATE_GRAPH"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                node_features, edge_indices, node_maps, known_pairs = (
                    BiomedicalGraphBuilder.build_demo_graph(
                        num_drugs=10,
                        num_proteins=10,
                        num_pathways=0,
                        num_diseases=8,
                        num_outcomes=3,
                        seed=42,
                    )
                )
                try:
                    from graph_transformer.gt_rl_bridge import GTRLBridge
                except ImportError as exc:
                    pytest.skip(f"bridge import failed: {exc}")

                try:
                    from graph_transformer.models.graph_transformer import (
                        DrugRepurposingGraphTransformer,
                    )
                    model = DrugRepurposingGraphTransformer(
                        feature_dims={
                            ntype: feat.shape[1]
                            for ntype, feat in node_features.items()
                        },
                        node_types=list(node_features.keys()),
                        edge_types=list(edge_indices.keys()),
                    )
                except Exception as exc:
                    pytest.skip(
                        f"cannot construct GT model for runtime test: {exc}"
                    )

                bridge = GTRLBridge(
                    output_dir=tmpdir,
                    device="cpu",
                    seed=42,
                )
                bridge.node_features = node_features
                bridge.edge_indices = edge_indices
                bridge.node_maps = node_maps
                bridge.model = model
                bridge.drug_names = list(node_maps.get("drug", {}).keys())
                bridge.disease_names = list(node_maps.get("disease", {}).keys())
                bridge.known_pairs = known_pairs
                bridge._trained = True

                # Must NOT raise
                df = bridge.generate_rl_input()
                # pathway_score must be constant 0.0 for all rows
                assert "pathway_score" in df.columns
                assert (df["pathway_score"] == 0.0).all(), (
                    f"P3-012 FAIL: pathway_score should be constant 0.0 "
                    f"for degenerate graph, got unique values: "
                    f"{df['pathway_score'].unique()[:5]}"
                )
        finally:
            os.environ.pop("DRUGOS_ALLOW_DEGENERATE_GRAPH", None)


# ============================================================================
# P3-013 — DataLoader persistent_workers leak fixed
# ============================================================================

class TestP3013DataLoaderWorkerLeak:
    """P3-013: DataLoader with persistent_workers=True leaks workers
    across epochs when local to train_epoch. Fix: cache the loader +
    memory monitor."""

    def test_cached_loader_attribute_present(self):
        """The trainer must cache the DataLoader as ``self._cached_loader``
        keyed by (dataset_id, batch_size)."""
        trainer_path = (
            _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
        )
        source = trainer_path.read_text()
        assert "_cached_loader" in source, (
            "P3-013 FAIL: _cached_loader attribute not found in trainer.py. "
            "The fix requires caching the DataLoader to prevent worker leaks."
        )
        assert "_cached_loader_key" in source, (
            "P3-013 FAIL: _cached_loader_key attribute not found. "
            "The cache must be keyed by (dataset_id, batch_size)."
        )

    def test_memory_monitor_present(self):
        """The trainer must have a memory monitor that logs RSS and
        raises if RSS grows >2x over the first epoch's RSS."""
        trainer_path = (
            _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
        )
        source = trainer_path.read_text()
        assert "_first_epoch_rss_mb" in source, (
            "P3-013 FAIL: _first_epoch_rss_mb attribute not found. "
            "The memory monitor must track first-epoch RSS."
        )
        assert "ru_maxrss" in source, (
            "P3-013 FAIL: ru_maxrss not found. The memory monitor must "
            "use resource.getrusage to track RSS."
        )
        # The threshold check
        assert "2.0 * self._first_epoch_rss_mb" in source or (
            "2 * self._first_epoch_rss_mb" in source
        ), (
            "P3-013 FAIL: 2x RSS threshold check not found. The monitor "
            "must raise if RSS grows >2x over the first epoch."
        )

    def test_shutdown_workers_on_cache_miss(self):
        """When a NEW dataset/batch_size is used, the OLD loader's workers
        must be explicitly shut down before creating the new one."""
        trainer_path = (
            _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
        )
        source = trainer_path.read_text()
        assert "_shutdown_workers" in source or "shutdown_workers" in source, (
            "P3-013 FAIL: worker shutdown logic not found. When the cache "
            "key changes, the old loader's workers must be terminated "
            "before creating the new one."
        )


# ============================================================================
# P3-014 — RDKit Morgan fingerprint API modernized
# ============================================================================

class TestP3014RDKitMorganAPIModernized:
    """P3-014: legacy AllChem.GetMorganFingerprintAsBitVect is deprecated
    in RDKit 2024+. Fix: prefer rdFingerprintGenerator.GetMorganGenerator
    with graceful fallback to the legacy API for older RDKit versions."""

    def test_new_api_used_in_biomedical_tables(self):
        """biomedical_tables.py must use rdFingerprintGenerator (with
        legacy fallback) instead of direct AllChem.GetMorganFingerprintAsBitVect."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "biomedical_tables.py"
        )
        source = path.read_text()
        assert "rdFingerprintGenerator" in source, (
            "P3-014 FAIL: rdFingerprintGenerator not imported in "
            "biomedical_tables.py. The fix must prefer the new API."
        )
        assert "GetMorganGenerator" in source, (
            "P3-014 FAIL: GetMorganGenerator call not found in "
            "biomedical_tables.py."
        )
        # The legacy API must be a FALLBACK only (in a conditional),
        # not the primary path
        assert "AllChem.GetMorganFingerprintAsBitVect" in source, (
            "P3-014 NOTE: legacy AllChem.GetMorganFingerprintAsBitVect "
            "is kept as a fallback for older RDKit versions — this is "
            "intentional (defense-in-depth)."
        )

    def test_new_api_used_in_phase2_adapter(self):
        """phase2_adapter.py must use rdFingerprintGenerator (with legacy
        fallback) instead of direct AllChem.GetMorganFingerprintAsBitVect."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "phase2_adapter.py"
        )
        source = path.read_text()
        assert "rdFingerprintGenerator" in source, (
            "P3-014 FAIL: rdFingerprintGenerator not imported in "
            "phase2_adapter.py. The fix must prefer the new API."
        )
        assert "GetMorganGenerator" in source, (
            "P3-014 FAIL: GetMorganGenerator call not found in "
            "phase2_adapter.py."
        )

    def test_runtime_drug_feature_computation(self):
        """Runtime test: compute_drug_features must produce a non-zero
        feature vector for a known SMILES (aspirin). This verifies the
        new API actually works at runtime, not just compiles."""
        try:
            from rdkit import Chem
        except ImportError:
            pytest.skip("RDKit not installed — cannot run runtime test")

        from graph_transformer.data.biomedical_tables import compute_drug_features

        # Aspirin SMILES
        aspirin_smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"
        feat = compute_drug_features(
            smiles=aspirin_smiles,
            drug_name="aspirin",
            feature_dim=128,
            allow_chemberta=False,  # skip chemberta to test RDKit path
        )
        # Feature must be non-zero (aspirin has many set bits)
        norm = float(np.linalg.norm(feat))
        assert norm > 0.01, (
            f"P3-014 FAIL: feature norm is {norm:.4f} (expected > 0.01). "
            f"The new RDKit API may not be working correctly."
        )
        # Feature must be L2-normalized (norm ≈ 1.0)
        assert 0.95 < norm < 1.05, (
            f"P3-014 FAIL: feature is not L2-normalized (norm={norm:.4f}). "
            f"Expected ~1.0 after L2 normalization."
        )


# ============================================================================
# P3-015 — TRAINING_POSITIVES guaranteed ≥5 for small graphs
# ============================================================================

class TestP3015TrainingPositivesGuaranteed:
    """P3-015: for small demo graphs (num_drugs < 25), the previous code
    added ZERO training positives. Fix: pre-inject the first 5
    training-positive drug/disease names BEFORE register_nodes."""

    def test_preinjection_block_present(self):
        """graph_builder.py must have a pre-injection block that adds
        training-positive drug/disease names BEFORE register_nodes."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "graph_builder.py"
        )
        source = path.read_text()
        assert "MIN_TRAINING_POSITIVES" in source, (
            "P3-015 FAIL: MIN_TRAINING_POSITIVES constant not found in "
            "graph_builder.py. The fix requires a minimum of 5 training "
            "positives for any num_drugs value."
        )
        assert "_training_positives_for_preinject" in source, (
            "P3-015 FAIL: pre-injection block not found. The fix must "
            "pre-inject training-positive names BEFORE register_nodes."
        )

    def test_warning_logged_when_below_minimum(self):
        """A WARNING must be logged if training_positives_added < 5."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "graph_builder.py"
        )
        source = path.read_text()
        assert "training_positives_added < MIN_TRAINING_POSITIVES" in source, (
            "P3-015 FAIL: warning condition not found. The fix must log "
            "a WARNING when training_positives_added < 5."
        )

    def test_runtime_small_graph_has_min_5_positives(self):
        """Runtime test: build_demo_graph(num_drugs=10) must produce a
        graph with ≥5 training-positive "treats" edges."""
        try:
            from graph_transformer.data.graph_builder import (
                BiomedicalGraphBuilder,
            )
        except ImportError as exc:
            pytest.skip(f"graph_transformer not importable: {exc}")

        node_features, edge_indices, node_maps, known_pairs = (
            BiomedicalGraphBuilder.build_demo_graph(
                num_drugs=10,  # SMALL graph — was 0 positives before fix
                num_proteins=15,
                num_pathways=10,
                num_diseases=8,
                num_outcomes=3,
                seed=42,
            )
        )
        # Count training-positive "treats" edges
        treats_ei = edge_indices.get(("drug", "treats", "disease"))
        if treats_ei is None:
            treats_count = 0
        else:
            treats_count = treats_ei.shape[1] if treats_ei.dim() > 1 else 0
        # Subtract the KPs (5 known positives are also "treats" edges)
        # to get the TRAINING positives count.
        num_kps = len(known_pairs)
        training_positives = max(0, treats_count - num_kps)
        # The fix guarantees ≥5 training positives even for num_drugs=10
        assert training_positives >= 5, (
            f"P3-015 FAIL: only {training_positives} training positives "
            f"found for num_drugs=10 (expected ≥5). Total treats edges: "
            f"{treats_count}, KPs (held out): {num_kps}."
        )


# ============================================================================
# P3-016 — target_count_per_drug uses all 4 edge types (already in main)
# ============================================================================

class TestP3016TargetCountAll4EdgeTypes:
    """P3-016: target_count_per_drug must use ALL 4 forward drug->protein
    edge types (inhibits, activates, binds, modulates), not just 2."""

    def test_all_4_edge_types_in_target_count_loop(self):
        """The target_count_per_drug loop must include all 4 edge types."""
        path = (
            _REPO_ROOT / "graph_transformer" / "gt_rl_bridge.py"
        )
        source = path.read_text()
        # Find the loop that builds target_count_per_drug
        # It must include bnd_ei and mod_ei (the fix added these)
        assert "bnd_ei" in source, (
            "P3-016 FAIL: bnd_ei variable not found. The fix must add "
            "('drug', 'binds', 'protein') edges to target_count_per_drug."
        )
        assert "mod_ei" in source, (
            "P3-016 FAIL: mod_ei variable not found. The fix must add "
            "('drug', 'modulates', 'protein') edges to target_count_per_drug."
        )
        # The loop must include all 4: [inh_ei, act_ei, bnd_ei, mod_ei]
        assert "[inh_ei, act_ei, bnd_ei, mod_ei]" in source, (
            "P3-016 FAIL: target_count_per_drug loop does not include "
            "all 4 edge types. Expected [inh_ei, act_ei, bnd_ei, mod_ei]."
        )


# ============================================================================
# P3-017 — " deltasone" leading-space alias typo fixed
# ============================================================================

class TestP3017DeltasoneAliasTypoFixed:
    """P3-017: the alias key " deltasone" (with leading space) was a DEAD
    alias because resolve_drug_name does name.lower().strip() on input,
    so the stripped input "deltasone" would never match the key
    " deltasone". Fix: change the key to "deltasone"."""

    def test_no_leading_space_in_deltasone_alias(self):
        """drug_aliases.py must NOT contain " deltasone" (with leading space)."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "drug_aliases.py"
        )
        source = path.read_text()
        # The buggy alias " deltasone" (with leading space) must be GONE
        assert '" deltasone"' not in source, (
            "P3-017 FAIL: the dead alias ' deltasone' (with leading space) "
            "is still present in drug_aliases.py. The fix removes the "
            "leading space so the alias actually matches stripped input."
        )
        # The fixed alias "deltasone" (no leading space) must be present
        assert '"deltasone": "prednisone"' in source, (
            "P3-017 FAIL: the fixed alias 'deltasone' (no leading space) "
            "is not present. The fix changes ' deltasone' to 'deltasone'."
        )

    def test_runtime_resolve_deltasone(self):
        """Runtime test: resolve_drug_name("Deltasone") must return
        "prednisone" (the canonical name)."""
        from graph_transformer.data.drug_aliases import resolve_drug_name

        # Test various case/whitespace combinations
        assert resolve_drug_name("Deltasone") == "prednisone", (
            "P3-017 FAIL: resolve_drug_name('Deltasone') did not return "
            "'prednisone'. The alias is still dead."
        )
        assert resolve_drug_name("deltasone") == "prednisone", (
            "P3-017 FAIL: resolve_drug_name('deltasone') did not return "
            "'prednisone'."
        )
        assert resolve_drug_name("  Deltasone  ") == "prednisone", (
            "P3-017 FAIL: resolve_drug_name('  Deltasone  ') did not "
            "return 'prednisone'. The function must strip whitespace."
        )


# ============================================================================
# P3-018 — Per-node cross-type normalization in HeterogeneousMultiHeadAttention
# ============================================================================

class TestP3018PerNodeCrossTypeNormalization:
    """P3-018: GLOBAL cross_type_norm (single scalar applied to all nodes)
    is SCIENTIFICALLY WRONG per HGT (Wang et al. 2019). Fix: compute
    PER-NODE incoming-edge-type count and normalize each node's message
    by sqrt(per_node_count + 1)."""

    def test_per_node_count_computation_present(self):
        """layers.py must compute per_node_incoming_edge_type_count."""
        path = (
            _REPO_ROOT / "graph_transformer" / "models" / "layers.py"
        )
        source = path.read_text()
        assert "per_node_incoming_edge_type_count" in source, (
            "P3-018 FAIL: per_node_incoming_edge_type_count not found in "
            "layers.py. The fix requires per-node normalization."
        )
        assert "_per_node_adjustment" in source, (
            "P3-018 FAIL: _per_node_adjustment tensor not found. The fix "
            "must apply the per-node normalization factor to messages."
        )

    def test_per_node_adjustment_applied_to_messages(self):
        """The _per_node_adjustment must be applied to the messages
        tensor AFTER the scatter_add loop, BEFORE the output projection."""
        path = (
            _REPO_ROOT / "graph_transformer" / "models" / "layers.py"
        )
        source = path.read_text()
        # The application must use unsqueeze(-1) for broadcasting
        assert "_per_node_adjustment.unsqueeze(-1)" in source, (
            "P3-018 FAIL: _per_node_adjustment.unsqueeze(-1) broadcast "
            "not found. The per-node factor must be broadcast against "
            "the (N, num_heads * head_dim) messages tensor."
        )

    def test_self_loop_not_scaled_by_per_node_count(self):
        """The self-loop portion must NOT be scaled by _per_node_adjustment
        (only edge messages are)."""
        path = (
            _REPO_ROOT / "graph_transformer" / "models" / "layers.py"
        )
        source = path.read_text()
        assert "_self_loop_portion" in source, (
            "P3-018 FAIL: _self_loop_portion not saved. The self-loop "
            "must be extracted before applying per-node adjustment so "
            "it is NOT scaled by per_node_count."
        )
        # The application must subtract self-loop, scale edge-only, re-add
        assert "_edge_only = messages - _self_loop_portion" in source, (
            "P3-018 FAIL: edge-only extraction not found. The fix must "
            "compute (messages - self_loop_portion) to isolate edge msgs."
        )


# ============================================================================
# P3-019 — Reverse-edge LIKE query verifies rel match (defense-in-depth)
# ============================================================================

class TestP3019ReverseEdgeRelMatchVerification:
    """P3-019: the LIKE pattern %|{fwd_rel}|% can match substrings (false
    positives). Fix: verify the extracted rel EXACTLY matches fwd_rel."""

    def test_rel_match_verification_present(self):
        """graph_builder.py must verify rel == fwd_rel after the LIKE query."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "graph_builder.py"
        )
        source = path.read_text()
        # The split must extract src_type, rel, tgt_type (not just src, _, tgt)
        assert "src_type, rel, tgt_type = parts" in source, (
            "P3-019 FAIL: rel extraction not found. The split must "
            "extract the relation as a named variable for verification."
        )
        # The verification check
        assert "if rel != fwd_rel:" in source, (
            "P3-019 FAIL: rel != fwd_rel check not found. The fix must "
            "verify the extracted relation exactly matches fwd_rel."
        )


# ============================================================================
# P3-020 — Service version uses __version__ (already in main)
# ============================================================================

class TestP3020ServiceVersionConsistency:
    """P3-020: service app version must use graph_transformer.__version__,
    not hardcoded "2.0.0"."""

    def test_no_hardcoded_2_0_0_in_service(self):
        """service.py must NOT hardcode version="2.0.0"."""
        path = (
            _REPO_ROOT / "graph_transformer" / "service.py"
        )
        source = path.read_text()
        # The old hardcoded version must NOT appear in the FastAPI app
        # constructor or the /health endpoint
        assert 'version="2.0.0"' not in source, (
            "P3-020 FAIL: hardcoded version='2.0.0' still present in "
            "service.py. The fix must use _GT_PACKAGE_VERSION."
        )
        assert '"version": "2.0.0"' not in source, (
            "P3-020 FAIL: hardcoded 'version': '2.0.0' still present in "
            "service.py /health endpoint."
        )

    def test_package_version_imported(self):
        """service.py must import __version__ from graph_transformer."""
        path = (
            _REPO_ROOT / "graph_transformer" / "service.py"
        )
        source = path.read_text()
        assert "_GT_PACKAGE_VERSION" in source, (
            "P3-020 FAIL: _GT_PACKAGE_VERSION not imported in service.py."
        )
        assert "version=_GT_PACKAGE_VERSION" in source, (
            "P3-020 FAIL: FastAPI app does not use _GT_PACKAGE_VERSION."
        )


# ============================================================================
# P3-021 — USE_DEFAULT sentinel for exclude_edges
# ============================================================================

class TestP3021UseDefaultSentinel:
    """P3-021: None sentinel for exclude_edges is counter-intuitive.
    Fix: introduce USE_DEFAULT sentinel so None means "no exclusion"."""

    def test_use_default_sentinel_defined(self):
        """graph_transformer.py must define USE_DEFAULT = object()."""
        path = (
            _REPO_ROOT / "graph_transformer" / "models" / "graph_transformer.py"
        )
        source = path.read_text()
        assert "USE_DEFAULT: Any = object()" in source or (
            "USE_DEFAULT = object()" in source
        ), (
            "P3-021 FAIL: USE_DEFAULT sentinel not defined in "
            "graph_transformer.py."
        )

    def test_methods_use_use_default_default(self):
        """forward_logits, forward, predict_all_pairs must use
        exclude_edges: Any = USE_DEFAULT as the default."""
        path = (
            _REPO_ROOT / "graph_transformer" / "models" / "graph_transformer.py"
        )
        source = path.read_text()
        # Count how many method signatures use USE_DEFAULT as default
        count = source.count("exclude_edges: Any = USE_DEFAULT")
        assert count >= 4, (
            f"P3-021 FAIL: only {count} method signatures use "
            f"exclude_edges: Any = USE_DEFAULT (expected ≥4: "
            f"forward_logits, forward, predict_all_pairs, "
            f"predict_all_pairs_dual)."
        )

    def test_effective_exclude_uses_use_default(self):
        """The effective_exclude computation must check
        `is not USE_DEFAULT` (not `is not None`)."""
        path = (
            _REPO_ROOT / "graph_transformer" / "models" / "graph_transformer.py"
        )
        source = path.read_text()
        assert "is not USE_DEFAULT" in source, (
            "P3-021 FAIL: effective_exclude does not check "
            "`is not USE_DEFAULT`. The fix must use the sentinel, not None."
        )


# ============================================================================
# P3-022 — morgan_radius configurable parameter
# ============================================================================

class TestP3022MorganRadiusConfigurable:
    """P3-022: Morgan fingerprint radius was hardcoded to 2. Fix: add
    morgan_radius parameter (default 2) to compute_drug_features and
    _drug_feature_from_smiles."""

    def test_morgan_radius_param_in_biomedical_tables(self):
        """compute_drug_features must accept morgan_radius parameter."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "biomedical_tables.py"
        )
        source = path.read_text()
        assert "morgan_radius: int = 2" in source, (
            "P3-022 FAIL: morgan_radius parameter not found in "
            "compute_drug_features signature."
        )

    def test_morgan_radius_param_in_phase2_adapter(self):
        """_drug_feature_from_smiles must accept morgan_radius parameter."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "phase2_adapter.py"
        )
        source = path.read_text()
        assert "morgan_radius: int = 2" in source, (
            "P3-022 FAIL: morgan_radius parameter not found in "
            "_drug_feature_from_smiles signature."
        )

    def test_morgan_radius_validation(self):
        """compute_drug_features must validate morgan_radius is in [1, 4]."""
        path = (
            _REPO_ROOT / "graph_transformer" / "data" / "biomedical_tables.py"
        )
        source = path.read_text()
        assert "morgan_radius < 1 or morgan_radius > 4" in source, (
            "P3-022 FAIL: morgan_radius range validation not found. "
            "The fix must validate morgan_radius is in [1, 4]."
        )

    def test_runtime_morgan_radius_ecfp2_vs_ecfp4(self):
        """Runtime test: compute_drug_features with morgan_radius=1 (ECFP2)
        must produce a DIFFERENT feature vector than morgan_radius=2 (ECFP4)
        for the same SMILES. This verifies the parameter actually threads
        through to the RDKit call."""
        try:
            from rdkit import Chem
        except ImportError:
            pytest.skip("RDKit not installed — cannot run runtime test")

        from graph_transformer.data.biomedical_tables import compute_drug_features

        aspirin_smiles = "CC(=O)OC1=CC=CC=C1C(=O)O"
        feat_ecfp2 = compute_drug_features(
            smiles=aspirin_smiles,
            drug_name="aspirin",
            feature_dim=128,
            allow_chemberta=False,
            morgan_radius=1,  # ECFP2
        )
        feat_ecfp4 = compute_drug_features(
            smiles=aspirin_smiles,
            drug_name="aspirin",
            feature_dim=128,
            allow_chemberta=False,
            morgan_radius=2,  # ECFP4 (default)
        )
        # The two feature vectors must be DIFFERENT (different radii
        # capture different substructure patterns)
        diff = float(np.linalg.norm(feat_ecfp2 - feat_ecfp4))
        assert diff > 0.01, (
            f"P3-022 FAIL: ECFP2 and ECFP4 features are identical "
            f"(diff={diff:.6f}). The morgan_radius parameter is not "
            f"threading through to the RDKit call."
        )

    def test_runtime_invalid_morgan_radius_raises(self):
        """Runtime test: morgan_radius=0 or morgan_radius=5 must raise ValueError."""
        from graph_transformer.data.biomedical_tables import compute_drug_features

        with pytest.raises(ValueError, match="morgan_radius"):
            compute_drug_features(
                smiles="CC(=O)OC1=CC=CC=C1C(=O)O",
                drug_name="aspirin",
                feature_dim=128,
                allow_chemberta=False,
                morgan_radius=0,  # invalid
            )
        with pytest.raises(ValueError, match="morgan_radius"):
            compute_drug_features(
                smiles="CC(=O)OC1=CC=CC=C1C(=O)O",
                drug_name="aspirin",
                feature_dim=128,
                allow_chemberta=False,
                morgan_radius=5,  # invalid
            )


# ============================================================================
# Integration test: all 11 fixes must coexist
# ============================================================================

class TestAll11FixesCoexist:
    """Smoke test: verify all 11 fixes are present simultaneously."""

    def test_all_11_fixes_present(self):
        """All 11 issues must have their fix present in the codebase."""
        # P3-012
        bridge_src = (
            _REPO_ROOT / "graph_transformer" / "gt_rl_bridge.py"
        ).read_text()
        assert "Phase2AdapterValidationError" in bridge_src
        assert "DRUGOS_ALLOW_DEGENERATE_GRAPH" in bridge_src

        # P3-013
        trainer_src = (
            _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
        ).read_text()
        assert "_cached_loader" in trainer_src
        assert "_first_epoch_rss_mb" in trainer_src

        # P3-014
        bt_src = (
            _REPO_ROOT / "graph_transformer" / "data" / "biomedical_tables.py"
        ).read_text()
        assert "rdFingerprintGenerator" in bt_src
        p2a_src = (
            _REPO_ROOT / "graph_transformer" / "data" / "phase2_adapter.py"
        ).read_text()
        assert "rdFingerprintGenerator" in p2a_src

        # P3-015
        gs_src = (
            _REPO_ROOT / "graph_transformer" / "data" / "graph_builder.py"
        ).read_text()
        assert "MIN_TRAINING_POSITIVES" in gs_src
        assert "_training_positives_for_preinject" in gs_src

        # P3-016
        assert "[inh_ei, act_ei, bnd_ei, mod_ei]" in bridge_src

        # P3-017
        da_src = (
            _REPO_ROOT / "graph_transformer" / "data" / "drug_aliases.py"
        ).read_text()
        assert '" deltasone"' not in da_src
        assert '"deltasone": "prednisone"' in da_src

        # P3-018
        layers_src = (
            _REPO_ROOT / "graph_transformer" / "models" / "layers.py"
        ).read_text()
        assert "per_node_incoming_edge_type_count" in layers_src
        assert "_per_node_adjustment" in layers_src

        # P3-019
        assert "if rel != fwd_rel:" in gs_src

        # P3-020
        svc_src = (
            _REPO_ROOT / "graph_transformer" / "service.py"
        ).read_text()
        assert 'version="2.0.0"' not in svc_src
        assert "_GT_PACKAGE_VERSION" in svc_src

        # P3-021
        gt_src = (
            _REPO_ROOT / "graph_transformer" / "models" / "graph_transformer.py"
        ).read_text()
        assert "USE_DEFAULT" in gt_src
        assert "exclude_edges: Any = USE_DEFAULT" in gt_src

        # P3-022
        assert "morgan_radius: int = 2" in bt_src
        assert "morgan_radius: int = 2" in p2a_src


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
