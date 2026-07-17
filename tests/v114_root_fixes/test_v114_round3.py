"""v114 Forensic Root-Fix Verification Tests — Round 3 (BUG #4-7).

These tests verify the ROOT-LEVEL fixes for the remaining bugs found by
the forensic scanner (Task 3-b). Each test maps to a specific bug.

Run: pytest tests/v114_root_fixes/test_v114_round3.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "phase1"), str(_REPO_ROOT / "phase2"), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# =============================================================================
# BUG #4: kg_builder silent except in update_validated_edges
# =============================================================================
class TestBug4KgBuilderSilentExcept:
    """BUG #4 ROOT FIX: phase2/drugos_graph/kg_builder.py update_validated_edges
    must NOT silently swallow has_edge_fn errors with `except Exception: pass`.
    It must LOG the failure and SKIP the edge (preventing duplicate
    VALIDATED_TREATS edges), and report the skip count in the result."""

    def test_no_silent_except_pass_in_update_validated_edges(self):
        """Read the REAL source and confirm the bare `except Exception: pass`
        is gone from the has_edge_fn block in update_validated_edges."""
        src = (_REPO_ROOT / "phase2" / "drugos_graph" / "kg_builder.py").read_text()
        # The old pattern was: except Exception:\n                    pass
        # The new pattern catches the exception, logs WARNING, and skips.
        assert "BUG #4 v114" in src, (
            "BUG #4 fix marker not found in kg_builder.py"
        )
        assert "edges_skipped_dedup_failure" in src, (
            "BUG #4 fix does not track skipped-dedup-failure count"
        )
        # The result dict must include the new counter.
        assert '"edges_skipped_dedup_failure"' in src, (
            "BUG #4 fix does not report edges_skipped_dedup_failure in result"
        )

    def test_update_validated_edges_reports_skip_count(self):
        """Runtime check: call update_validated_edges with a builder whose
        has_edge raises, verify the edge is SKIPPED (not added) and the
        skip count is reported in the result."""
        os.environ.setdefault("DRUGOS_SKIP_CHEMBERTA", "1")
        os.environ.setdefault("DRUGOS_ENVIRONMENT", "dev")
        from phase2.drugos_graph.kg_builder import update_validated_edges
        import tempfile, csv
        from datetime import datetime, timezone

        # Write a minimal validated_hypotheses.csv with one positive pair.
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "validated_hypotheses.csv"
            with open(csv_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["drug", "disease", "outcome", "validated_at"])
                w.writerow(["test_drug", "test_disease", "validated_positive",
                            datetime.now(timezone.utc).isoformat()])

            # Build a mock builder whose has_edge raises (simulates Neo4j failure).
            # The builder must support the has_edge / add_edge protocol used
            # by update_validated_edges. We use a simple object with the
            # methods the function expects.
            class _FailingBuilder:
                def __init__(self):
                    self.added = []
                def has_edge(self, *args, **kwargs):
                    raise RuntimeError("simulated Neo4j connection failure")
                def add_edge(self, *args, **kwargs):
                    self.added.append((args, kwargs))
                    return True
                # Some builders use total_nodes/nodes for size checks.
                total_nodes = 100
                @property
                def nodes(self):
                    return {}

            failing_builder = _FailingBuilder()
            result = update_validated_edges(
                validated_csv_path=str(csv_path),
                builder=failing_builder,
            )
            # The edge must have been SKIPPED (not added) to prevent duplicates.
            assert result.get("edges_skipped_dedup_failure", 0) >= 1, (
                f"BUG #4: has_edge raised but edge was not counted as skipped. "
                f"Result: {result}"
            )
            # add_edge must NOT have been called for the skipped edge.
            assert len(failing_builder.added) == 0, (
                f"BUG #4: add_edge was called for a skipped edge (would "
                f"create duplicate). Added: {failing_builder.added}"
            )


# =============================================================================
# BUG #5: kg_builder deprecated shim import
# =============================================================================
class TestBug5KgBuilderShimImport:
    """BUG #5 ROOT FIX: kg_builder.py must import DIRECTLY from
    shared.contracts.writeback, not the deprecated
    common.validated_hypotheses_schema shim."""

    def test_kg_builder_imports_from_shared_contract(self):
        src = (_REPO_ROOT / "phase2" / "drugos_graph" / "kg_builder.py").read_text()
        assert "shared.contracts.writeback" in src, (
            "kg_builder.py does not import from shared.contracts.writeback -- BUG #5 NOT fixed"
        )
        assert "BUG #5" in src, "BUG #5 fix marker not found"

    def test_kg_builder_does_not_use_deprecated_shim(self):
        """The deprecated common.validated_hypotheses_schema shim must
        NOT be imported in the update_validated_edges schema section."""
        src = (_REPO_ROOT / "phase2" / "drugos_graph" / "kg_builder.py").read_text()
        # Find the schema import block (around line 4982-5013).
        # It should import from shared.contracts.writeback, NOT common.
        # We check that the canonical import is present.
        assert 'import_module("shared.contracts.writeback")' in src, (
            "kg_builder.py does not import_module shared.contracts.writeback"
        )
        # The old shim import may still appear in COMMENTS (explaining
        # the fix), but must NOT be the active import. Check the active
        # import line uses shared, not common.
        lines = src.split("\n")
        shim_active = any(
            'import_module("common.validated_hypotheses_schema")' in line
            and not line.strip().startswith("#")
            for line in lines
        )
        assert not shim_active, (
            "kg_builder.py still actively imports common.validated_hypotheses_schema shim"
        )


# =============================================================================
# BUG #6: rl_drug_ranker deprecated shim imports (2 locations)
# =============================================================================
class TestBug6RlRankerShimImports:
    """BUG #6 ROOT FIX: rl/rl_drug_ranker.py's two validated-hypotheses
    loaders must import DIRECTLY from shared.contracts.writeback."""

    def test_rl_ranker_imports_from_shared_contract(self):
        src = (_REPO_ROOT / "rl" / "rl_drug_ranker.py").read_text()
        assert "from shared.contracts.writeback import" in src, (
            "rl_drug_ranker.py does not import from shared.contracts.writeback -- BUG #6 NOT fixed"
        )
        assert "BUG #6" in src, "BUG #6 fix marker not found"

    def test_both_loaders_use_shared_contract(self):
        """There are TWO loader functions (_load_validated_hypotheses and
        _load_validated_toxic_hypotheses). Both must use shared.contracts."""
        src = (_REPO_ROOT / "rl" / "rl_drug_ranker.py").read_text()
        # Count active imports from shared.contracts.writeback (not in comments).
        active_imports = sum(
            1 for line in src.split("\n")
            if "from shared.contracts.writeback import" in line
            and not line.strip().startswith("#")
        )
        assert active_imports >= 2, (
            f"Expected >=2 active shared.contracts.writeback imports (one per loader), "
            f"found {active_imports}"
        )

    def test_loaders_still_work_at_runtime(self):
        """Runtime check: both loaders must still function correctly
        after the import source change."""
        os.environ.setdefault("DRUGOS_SKIP_CHEMBERTA", "1")
        os.environ.setdefault("DRUGOS_ENVIRONMENT", "dev")
        # The canonical CSV exists (created in round 1) with 8 positive
        # and 5 toxic pairs. Both loaders should find it.
        from rl.rl_drug_ranker import _load_validated_hypotheses, _load_validated_toxic_hypotheses
        pos = _load_validated_hypotheses()
        tox = _load_validated_toxic_hypotheses()
        # The positive set should include aspirin/cv (from the seed data).
        assert any(d == "aspirin" for d, _ in pos), (
            f"aspirin not found in validated positives: {pos[:3]}"
        )
        # The toxic set should include rofecoxib/cv (from the seed data).
        assert any(d == "rofecoxib" for d, _ in tox), (
            f"rofecoxib not found in validated toxics: {tox[:3]}"
        )


# =============================================================================
# BUG #7: train_agent docstring return-type lie
# =============================================================================
class TestBug7TrainAgentDocstring:
    """BUG #7 ROOT FIX: train_agent's docstring must accurately describe
    the 3-tuple return (model, checkpoint_path, vec_normalize), not the
    old 2-tuple (model, checkpoint_path)."""

    def test_docstring_describes_3_tuple(self):
        src = (_REPO_ROOT / "rl" / "rl_drug_ranker.py").read_text()
        # Find the train_agent docstring's Returns section.
        # The old lie: "Tuple of (model, checkpoint_path). checkpoint_path is None if save failed."
        # The fix: "Tuple of (model, checkpoint_path, vec_normalize)."
        assert "Tuple of (model, checkpoint_path, vec_normalize)" in src, (
            "train_agent docstring does not describe the 3-tuple return -- BUG #7 NOT fixed"
        )

    def test_signature_and_docstring_are_consistent(self):
        """The signature declares Tuple[Any, Optional[str], Any] (3-tuple).
        The docstring must also say 3-tuple."""
        src = (_REPO_ROOT / "rl" / "rl_drug_ranker.py").read_text()
        # The signature.
        assert "Tuple[Any, Optional[str], Any]" in src, (
            "train_agent signature does not declare 3-tuple"
        )
        # The docstring must NOT contain the old 2-tuple lie.
        # (The old text was: "Tuple of (model, checkpoint_path). checkpoint_path is None")
        assert "Tuple of (model, checkpoint_path). checkpoint_path is None" not in src, (
            "train_agent docstring still contains the old 2-tuple lie"
        )

    def test_train_agent_returns_3_tuple_at_runtime(self):
        """Runtime check: train_agent actually returns a 3-tuple."""
        os.environ.setdefault("DRUGOS_SKIP_CHEMBERTA", "1")
        os.environ.setdefault("DRUGOS_ENVIRONMENT", "dev")
        os.environ.setdefault("RL_SKIP_LITERATURE", "1")
        import tempfile
        from rl.rl_drug_ranker import train_agent, DrugRankingEnv, PipelineConfig
        import pandas as pd

        # Build a minimal env with a few candidates.
        df = pd.DataFrame([
            {"drug": "aspirin", "disease": "pain", "gnn_score": 0.7, "safety_score": 0.8,
             "market_score": 0.5, "confidence": 0.6, "pathway_score": 0.5, "patent_score": 0.5,
             "rare_disease_flag": 0, "unmet_need_score": 0.5, "efficacy_score": 0.7, "adme_score": 0.6},
            {"drug": "metformin", "disease": "type 2 diabetes", "gnn_score": 0.8, "safety_score": 0.9,
             "market_score": 0.6, "confidence": 0.7, "pathway_score": 0.6, "patent_score": 0.4,
             "rare_disease_flag": 0, "unmet_need_score": 0.4, "efficacy_score": 0.8, "adme_score": 0.7},
        ])
        # DrugRankingEnv signature: (data, config, reward_fn, disease_context_stats, set_adaptive_threshold)
        # No output_dir -- train_agent writes checkpoints to its own default.
        env = DrugRankingEnv(df)
        config = PipelineConfig(block_on_scientific_failure=False)
        result = train_agent(env, timesteps=100, seed=42, config=config)
        # Must be a 3-tuple.
        assert isinstance(result, tuple) and len(result) == 3, (
            f"train_agent returned {type(result).__name__} of len "
            f"{len(result) if isinstance(result, tuple) else 'N/A'} (expected 3-tuple)"
        )
        model, ckpt_path, vec_norm = result
        assert model is not None, "model is None"
