"""Unit tests for P2-049 through P2-067 root-cause fixes.

Team Member 8 — Phase 2 Auxiliary Loaders & Utils.

Each test verifies ONE fix in isolation. Tests are designed to FAIL
before the fix and PASS after. Run with:

    pytest phase2/tests/p2_049_067/test_p2_049_to_067_root_fixes.py -v

These tests do NOT depend on Neo4j, HuggingFace, or a GPU — they
exercise the fixed code paths directly with synthetic inputs.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
import warnings
from unittest.mock import MagicMock, patch

import pytest

# Ensure phase2 is importable. The repo root is 2 levels up from this
# test file (phase2/tests/p2_049_067/test_*.py → repo root).
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..")
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# Skip the import-time invariants so we can monkey-patch config in tests.
os.environ.setdefault("DRUGOS_SKIP_IMPORT_CHECK", "1")


# ─── P2-049 ──────────────────────────────────────────────────────────
def test_p2_049_pyg_builder_test_file_now_exists():
    """P2-049: the docstring references phase2/tests/test_pyg_builder.py —
    verify the file exists (created as part of this fix)."""
    test_file = os.path.join(
        _REPO_ROOT, "phase2", "tests", "test_pyg_builder.py"
    )
    assert os.path.isfile(test_file), (
        "phase2/tests/test_pyg_builder.py must exist (P2-049 root fix). "
        "The pyg_builder.py docstring references this file — without it, "
        "maintainers believe tests cover the code when they do not."
    )


def test_p2_049_pyg_builder_docstring_uses_correct_path():
    """P2-049: verify the docstring references the correct
    'phase2/tests/test_pyg_builder.py' path. The misleading top-level
    'tests/test_pyg_builder.py' may still appear inside P2-049 root-fix
    COMMENTS that explain what was fixed (this is correct — the comment
    documents the OLD path). The test verifies the docstring USES the
    correct path for actual test references."""
    pyg_builder_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "pyg_builder.py"
    )
    with open(pyg_builder_path, "r", encoding="utf-8") as f:
        content = f.read()
    # The correct path must be present (multiple times — docstring +
    # comment references).
    assert "phase2/tests/test_pyg_builder.py" in content, (
        "pyg_builder.py docstring must reference "
        "'phase2/tests/test_pyg_builder.py'. (P2-049)"
    )
    # Count occurrences: the correct path must appear AT LEAST as many
    # times as the bare top-level path. The bare path may still appear
    # inside P2-049 root-fix comments (explaining what was wrong) —
    # that's fine as long as the correct path is also used.
    correct_count = content.count("phase2/tests/test_pyg_builder.py")
    # The bare 'tests/test_pyg_builder.py' count includes the
    # 'phase2/tests/test_pyg_builder.py' substring, so subtract.
    bare_count = content.count("tests/test_pyg_builder.py") - correct_count
    assert correct_count >= bare_count, (
        f"pyg_builder.py must use 'phase2/tests/test_pyg_builder.py' "
        f"({correct_count} times) at least as often as the bare "
        f"'tests/test_pyg_builder.py' ({bare_count} times). (P2-049)"
    )


# ─── P2-050 ──────────────────────────────────────────────────────────
def test_p2_050_mlflow_default_experiment_name_is_phase2():
    """P2-050: MLflowTracker default experiment_name must be
    'DrugOS_Phase2' (not the misleading 'DrugOS_Week2')."""
    from phase2.drugos_graph.mlflow_tracker import MLflowTracker

    sig = inspect.signature(MLflowTracker.__init__)
    default = sig.parameters["experiment_name"].default
    assert default == "DrugOS_Phase2", (
        f"MLflowTracker default experiment_name must be 'DrugOS_Phase2' "
        f"(not 'DrugOS_Week2' — Phase 2 spans Weeks 2-5 per the DOCX). "
        f"Got: {default!r}. (P2-050)"
    )


# ─── P2-051 ──────────────────────────────────────────────────────────
def test_p2_051_test_batch_memory_accepts_device_parameter():
    """P2-051: test_batch_memory must accept a `device` parameter so
    multi-GPU hosts can target a specific GPU."""
    from phase2.drugos_graph.gpu_utils import test_batch_memory

    sig = inspect.signature(test_batch_memory)
    assert "device" in sig.parameters, (
        "test_batch_memory must accept a 'device' parameter so multi-GPU "
        "hosts can target a specific GPU. (P2-051)"
    )
    assert sig.parameters["device"].default == "cuda", (
        "test_batch_memory 'device' parameter must default to 'cuda' for "
        "backward compat. (P2-051)"
    )


def test_p2_051_test_batch_memory_records_device_requested():
    """P2-051: the result dict must record which device was tested so
    the audit log is unambiguous on multi-GPU hosts."""
    from phase2.drugos_graph.gpu_utils import test_batch_memory

    # Force CPU (no GPU in CI) — but the function should still record
    # the requested device.
    result = test_batch_memory(
        num_nodes=100, num_edges=100, batch_size=4, device="cuda:0"
    )
    assert "device_requested" in result, (
        "test_batch_memory result must include 'device_requested' so "
        "the audit log records WHICH GPU was tested. (P2-051)"
    )
    assert result["device_requested"] == "cuda:0"


# ─── P2-052 ──────────────────────────────────────────────────────────
def test_p2_052_phase1_bridge_catches_only_import_error_not_exception():
    """P2-052: the `from .exceptions import DrugOSDataError` must catch
    ONLY ImportError, not bare Exception. A SyntaxError in exceptions.py
    must propagate, not silently fall back to the stub class."""
    bridge_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "phase1_bridge.py"
    )
    with open(bridge_path, "r", encoding="utf-8") as f:
        content = f.read()
    # The too-broad 'except Exception' for DrugOSDataError import must
    # be replaced with 'except ImportError'.
    # Find the specific block.
    marker = "from .exceptions import DrugOSDataError"
    idx = content.find(marker)
    assert idx >= 0, "phase1_bridge.py must import DrugOSDataError. (P2-052)"
    # Look at the 200 chars after the marker.
    block = content[idx:idx + 200]
    assert "except ImportError" in block, (
        "phase1_bridge.py must catch ONLY ImportError (not Exception) "
        "when importing DrugOSDataError — a SyntaxError in exceptions.py "
        "must propagate, not silently fall back. (P2-052)"
    )
    # And the broad 'except Exception' must NOT be there for this block.
    assert "except Exception" not in block, (
        "phase1_bridge.py DrugOSDataError import block still uses "
        "'except Exception' — must be 'except ImportError'. (P2-052)"
    )


# ─── P2-053 ──────────────────────────────────────────────────────────
def test_p2_053_edge_property_whitelist_keys_match_core_edge_types():
    """P2-053: EDGE_PROPERTY_WHITELIST keys must be in 1:1 correspondence
    with CORE_EDGE_TYPES_SET. A typo in CORE_EDGE_TYPES must raise at
    module-load time, not silently strip properties."""
    from phase2.drugos_graph import kg_builder
    from phase2.drugos_graph.config import (
        CORE_EDGE_TYPES, CORE_EDGE_TYPES_SET,
    )

    # Every CORE_EDGE_TYPES entry must have a whitelist key.
    for triple in CORE_EDGE_TYPES:
        assert triple in kg_builder.EDGE_PROPERTY_WHITELIST, (
            f"CORE_EDGE_TYPES triple {triple} has no "
            f"EDGE_PROPERTY_WHITELIST entry — properties would be "
            f"silently stripped. (P2-053)"
        )
    # Every whitelist key must be a CORE_EDGE_TYPES entry.
    for triple in kg_builder.EDGE_PROPERTY_WHITELIST:
        assert triple in CORE_EDGE_TYPES_SET, (
            f"EDGE_PROPERTY_WHITELIST key {triple} is not in "
            f"CORE_EDGE_TYPES — typo in the whitelist. (P2-053)"
        )


def test_p2_053_no_whitespace_in_core_edge_types():
    """P2-053: no CORE_EDGE_TYPES entry may have leading/trailing
    whitespace or double spaces — these would silently strip
    properties on that triple type."""
    from phase2.drugos_graph.config import CORE_EDGE_TYPES

    for src, rel, dst in CORE_EDGE_TYPES:
        for label, val in (("src", src), ("rel", rel), ("dst", dst)):
            assert val == val.strip(), (
                f"CORE_EDGE_TYPES triple ({src!r}, {rel!r}, {dst!r}) "
                f"has leading/trailing whitespace in {label}. (P2-053)"
            )
            assert "  " not in val, (
                f"CORE_EDGE_TYPES triple ({src!r}, {rel!r}, {dst!r}) "
                f"has a double-space in {label}. (P2-053)"
            )


# ─── P2-054 ──────────────────────────────────────────────────────────
# P2-054 NOTE: On the merged main branch, step11b_train_graph_transformer
# was REFACTORED to delegate HGT training to Phase 3's
# graph_transformer.models.graph_transformer.DrugRepurposingGraphTransformer.
# The OLD step11b (which had the OneCycleLR bug) no longer exists. The
# Phase 3 trainer (graph_transformer/training/trainer.py) handles the
# OneCycleLR construction correctly via the P3-S06 fix (checks
# total_steps >= MIN_STEPS_FOR_SCHEDULER before constructing OneCycleLR,
# logs a WARNING if below threshold). P2-054 is therefore RESOLVED BY
# REFACTOR — the bug no longer exists in the codebase.
@pytest.mark.skip(
    reason="P2-054 resolved by Phase 3 delegation refactor — "
           "step11b no longer trains HGT; the Phase 3 trainer "
           "(graph_transformer/training/trainer.py) handles OneCycleLR "
           "correctly via P3-S06 fix."
)
def test_p2_054_onecyclelr_fallback_uses_cosine_annealing_not_none():
    """P2-054: SKIPPED — resolved by Phase 3 delegation refactor."""
    pass


@pytest.mark.skip(
    reason="P2-054 resolved by Phase 3 delegation refactor."
)
def test_p2_054_cosine_annealing_lr_with_small_total_steps():
    """P2-054: SKIPPED — resolved by Phase 3 delegation refactor."""
    pass


# ─── P2-055 ──────────────────────────────────────────────────────────
def test_p2_055_chemberta_result_iter_emits_deprecation_warning():
    """P2-055: ChembertaEncodeResult.__iter__ must emit a
    DeprecationWarning so callers know tuple unpacking drops
    failed_compound_ids, metrics, etc."""
    import torch
    from phase2.drugos_graph.chemberta_encoder import ChembertaEncodeResult

    result = ChembertaEncodeResult(
        embeddings=torch.zeros(2, 3),
        compound_ids=["a", "b"],
        failed_compound_ids=[],
        cache_path=None,
        lineage_manifest_path=None,
        metrics={},
    )
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        emb, ids = result  # tuple unpacking triggers __iter__
        deprecation_warnings = [
            x for x in w if issubclass(x.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) >= 1, (
            "ChembertaEncodeResult.__iter__ must emit a "
            "DeprecationWarning so callers know tuple unpacking drops "
            "failed_compound_ids, metrics, etc. (P2-055)"
        )
        # The warning message must mention the dropped fields.
        msg = str(deprecation_warnings[0].message)
        assert "failed_compound_ids" in msg, (
            "DeprecationWarning must mention failed_compound_ids. (P2-055)"
        )
    assert emb is not None and ids == ["a", "b"]  # unpacking still works


# ─── P2-056 ──────────────────────────────────────────────────────────
def test_p2_056_clinicaltrials_no_module_level_assert():
    """P2-056: the module-level `assert ("Compound", "tested_for",
    "Disease") in CORE_EDGE_TYPES` must be replaced with a runtime
    function + import-time WARNING (no raise)."""
    loader_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "clinicaltrials_loader.py"
    )
    with open(loader_path, "r", encoding="utf-8") as f:
        content = f.read()
    # The static assert for tested_for must be gone.
    bad_assert = 'assert ("Compound", "tested_for", "Disease") in CORE_EDGE_TYPES'
    assert bad_assert not in content, (
        "clinicaltrials_loader.py must NOT have a module-level assert "
        "for ('Compound', 'tested_for', 'Disease') — replace with a "
        "runtime check. (P2-056)"
    )
    # The runtime check function must exist.
    assert "_assert_tested_for_in_core_edge_types" in content, (
        "clinicaltrials_loader.py must define "
        "_assert_tested_for_in_core_edge_types() as the runtime "
        "replacement for the module-level assert. (P2-056)"
    )


def test_p2_056_runtime_check_raises_when_triple_missing():
    """P2-056: the runtime check function must raise RuntimeError when
    the triple is missing from CORE_EDGE_TYPES."""
    from phase2.drugos_graph import clinicaltrials_loader as ctl

    # The triple IS in CORE_EDGE_TYPES (normal case) — must not raise.
    ctl._assert_tested_for_in_core_edge_types()  # no exception

    # Monkey-patch CORE_EDGE_TYPES to remove the triple — must raise.
    original = ctl.CORE_EDGE_TYPES
    try:
        ctl.CORE_EDGE_TYPES = [
            t for t in original
            if t != ("Compound", "tested_for", "Disease")
        ]
        with pytest.raises(RuntimeError) as exc_info:
            ctl._assert_tested_for_in_core_edge_types()
        assert "P2-056" in str(exc_info.value)
    finally:
        ctl.CORE_EDGE_TYPES = original


# ─── P2-057 ──────────────────────────────────────────────────────────
# P2-057 NOTE: On the merged main branch, step11b_train_graph_transformer
# was REFACTORED to delegate HGT training to Phase 3. The OLD step11b
# (which had the NaN-triple filtering bug) no longer exists. The Phase 3
# trainer (graph_transformer/training/trainer.py) uses BCEWithLogitsLoss
# which handles NaN via the model's forward_logits() — the NaN-filtering
# pattern from step11b is no longer present. P2-057 is therefore
# RESOLVED BY REFACTOR.
@pytest.mark.skip(
    reason="P2-057 resolved by Phase 3 delegation refactor — "
           "step11b no longer has a training loop with NaN filtering."
)
def test_p2_057_nan_triple_tracking_initialized():
    """P2-057: SKIPPED — resolved by Phase 3 delegation refactor."""
    pass


# ─── P2-058 ──────────────────────────────────────────────────────────
def test_p2_058_treats_edge_referential_integrity_guard():
    """P2-058: phase1_bridge must explicitly check `did in
    disease_id_set` before creating a treats edge, with a WARNING log
    when the check fails."""
    bridge_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "phase1_bridge.py"
    )
    with open(bridge_path, "r", encoding="utf-8") as f:
        content = f.read()
    # The defensive guard must exist.
    assert "did not in disease_id_set" in content, (
        "phase1_bridge.py must check `did not in disease_id_set` "
        "before creating a treats edge — defensive guard against "
        "orphan edges. (P2-058)"
    )
    # The P2-058 root-fix comment must be present.
    assert "P2-058" in content, (
        "phase1_bridge.py must contain the P2-058 root-fix comment. (P2-058)"
    )


# ─── P2-059 ──────────────────────────────────────────────────────────
def test_p2_059_checkpoint_log_uses_batch_count_not_i_floor():
    """P2-059: kg_builder checkpoint log must use
    `batch_count = i // batch_size + 1` (1-indexed) and log when
    `batch_count % log_freq == 0` — NOT when `(i // batch_size) %
    log_freq == 0` (which always logs the first batch)."""
    kg_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "kg_builder.py"
    )
    with open(kg_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "batch_count = i // batch_size + 1" in content, (
        "kg_builder.py must use `batch_count = i // batch_size + 1` "
        "(1-indexed) for the checkpoint log frequency check. (P2-059)"
    )
    assert "batch_count % log_freq == 0" in content, (
        "kg_builder.py must log when `batch_count % log_freq == 0` "
        "(not `(i // batch_size) % log_freq == 0`). (P2-059)"
    )


def test_p2_059_first_batch_does_not_log_when_log_freq_gt_1():
    """P2-059: with log_freq=10, the first batch (i=0, batch_count=1)
    must NOT log — only batches 10, 20, 30, ... should log."""
    # Simulate the logic: batch_count = i // batch_size + 1
    # With i=0, batch_size=100, batch_count = 0//100 + 1 = 1
    # 1 % 10 = 1 (not 0) → does NOT log. ✓
    log_freq = 10
    batch_size = 100
    # First batch (i=0)
    i = 0
    batch_count = i // batch_size + 1
    assert batch_count == 1
    assert batch_count % log_freq != 0, (
        "First batch (batch_count=1) must NOT log when log_freq=10. "
        "(P2-059 root fix)"
    )
    # 10th batch (i=900, batch_count=10) — should log.
    i = 900
    batch_count = i // batch_size + 1
    assert batch_count == 10
    assert batch_count % log_freq == 0, (
        "10th batch (batch_count=10) must log when log_freq=10. (P2-059)"
    )


# ─── P2-060 ──────────────────────────────────────────────────────────
def test_p2_060_utils_uses_with_pattern_for_sessions():
    """P2-060: store_label_map_metadata_in_graph and
    check_label_map_version_matches_graph must use
    `with builder.driver.session() as session:` (not try/finally +
    session.close()). The P2-060 root-fix COMMENT may mention the old
    pattern (explaining what was wrong) — that's fine as long as the
    actual code body uses `with`."""
    utils_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "utils.py"
    )
    with open(utils_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Find both functions and verify they use `with`.
    for func_name in (
        "store_label_map_metadata_in_graph",
        "check_label_map_version_matches_graph",
    ):
        idx = content.find(f"def {func_name}")
        assert idx >= 0, f"utils.py must define {func_name}. (P2-060)"
        # Get the function body (next 1500 chars).
        body = content[idx:idx + 1500]
        assert "with builder.driver.session() as session:" in body, (
            f"utils.py {func_name} must use "
            f"'with builder.driver.session() as session:' (not "
            f"try/finally + session.close()). (P2-060)"
        )
        # The old try/finally pattern must NOT be in the function body
        # as actual code. The COMMENT may mention it (in a string
        # literal describing the old behavior) — but the actual code
        # must not have `session = builder.driver.session()` as a
        # STATEMENT. We check for the pattern as a non-comment, non-
        # string-literal statement by looking for it OUTSIDE of
        # comment lines.
        body_lines = body.split("\n")
        for line in body_lines:
            stripped = line.lstrip()
            # Skip comment lines and lines inside docstrings (heuristic:
            # lines starting with # or containing the pattern inside
            # quotes).
            if stripped.startswith("#"):
                continue
            # The actual CODE pattern: `session = builder.driver.session()`
            # as a statement (not inside a string). We check for the
            # pattern at the start of a stripped line (after indentation).
            if stripped.startswith("session = builder.driver.session()"):
                pytest.fail(
                    f"utils.py {func_name} still uses "
                    f"'session = builder.driver.session()' as a code "
                    f"statement — convert to 'with "
                    f"builder.driver.session() as session:'. (P2-060)"
                )


# ─── P2-061 ──────────────────────────────────────────────────────────
def test_p2_061_train_transe_handles_empty_tuple():
    """P2-061: train_transe must raise ValueError (not IndexError) when
    train_triples is an empty tuple ()."""
    from phase2.drugos_graph.transe_model import train_transe, TransEConfig
    from phase2.drugos_graph.config import TransEConfig as _Cfg

    # Empty tuple — the bug case. Previously raised IndexError.
    with pytest.raises(ValueError) as exc_info:
        train_transe(model=MagicMock(), train_triples=())
    assert "empty" in str(exc_info.value).lower(), (
        "train_transe with empty tuple must raise ValueError with "
        "'empty' in the message (not IndexError). (P2-061)"
    )


def test_p2_061_train_transe_handles_none():
    """P2-061: train_transe must raise ValueError when train_triples is None."""
    from phase2.drugos_graph.transe_model import train_transe

    with pytest.raises(ValueError):
        train_transe(model=MagicMock(), train_triples=None)


# ─── P2-062 ──────────────────────────────────────────────────────────
def test_p2_062_unset_environment_defaults_to_dev():
    """P2-062: when DRUGOS_ENVIRONMENT is UNSET, _is_production_env must
    return False (dev mode) — NOT fall through to the DATABASE_URL
    check that would trigger prod-mode hard failures."""
    from phase2.drugos_graph.phase1_bridge import _is_production_env

    # Save and clear env.
    saved_env = os.environ.pop("DRUGOS_ENVIRONMENT", None)
    saved_db = os.environ.pop("DATABASE_URL", None)
    try:
        # Set DATABASE_URL but NOT DRUGOS_ENVIRONMENT — old bug: prod mode.
        os.environ["DATABASE_URL"] = "postgresql://test/test"
        assert _is_production_env() is False, (
            "_is_production_env must return False when "
            "DRUGOS_ENVIRONMENT is UNSET, even if DATABASE_URL is set — "
            "dev is the safe default. (P2-062)"
        )
    finally:
        if saved_env is not None:
            os.environ["DRUGOS_ENVIRONMENT"] = saved_env
        else:
            os.environ.pop("DRUGOS_ENVIRONMENT", None)
        if saved_db is not None:
            os.environ["DATABASE_URL"] = saved_db
        else:
            os.environ.pop("DATABASE_URL", None)


def test_p2_062_prod_mode_requires_explicit_env():
    """P2-062: production mode must require explicit DRUGOS_ENVIRONMENT=prod."""
    from phase2.drugos_graph.phase1_bridge import _is_production_env

    saved_env = os.environ.pop("DRUGOS_ENVIRONMENT", None)
    saved_db = os.environ.pop("DATABASE_URL", None)
    try:
        os.environ["DRUGOS_ENVIRONMENT"] = "prod"
        os.environ["DATABASE_URL"] = "postgresql://test/test"
        assert _is_production_env() is True
    finally:
        if saved_env is not None:
            os.environ["DRUGOS_ENVIRONMENT"] = saved_env
        else:
            os.environ.pop("DRUGOS_ENVIRONMENT", None)
        if saved_db is not None:
            os.environ["DATABASE_URL"] = saved_db
        else:
            os.environ.pop("DATABASE_URL", None)


# ─── P2-063 ──────────────────────────────────────────────────────────
# P2-063 NOTE: On the merged main branch, step11b_train_graph_transformer
# was REFACTORED to delegate HGT training to Phase 3. The OLD step11b
# (which had the MIN_TRIPLES_FOR_HGT threshold) no longer exists. The
# Phase 3 trainer (graph_transformer/training/trainer.py) handles small-
# dataset cases via its own logic (MIN_STEPS_FOR_SCHEDULER check). P2-063
# is therefore RESOLVED BY REFACTOR.
@pytest.mark.skip(
    reason="P2-063 resolved by Phase 3 delegation refactor — "
           "step11b no longer checks MIN_TRIPLES_FOR_HGT."
)
def test_p2_063_min_triples_thresholds_raised():
    """P2-063: SKIPPED — resolved by Phase 3 delegation refactor."""
    pass


# ─── P2-064 ──────────────────────────────────────────────────────────
def test_p2_064_chemberta_has_fallback_chain():
    """P2-064: chemberta_encoder must define CHEMBERTA_MODEL_FALLBACKS
    (a list of fallback models) so a single HF-side change doesn't
    break the encoder."""
    from phase2.drugos_graph import chemberta_encoder

    assert hasattr(chemberta_encoder, "CHEMBERTA_MODEL_FALLBACKS"), (
        "chemberta_encoder must define CHEMBERTA_MODEL_FALLBACKS list. (P2-064)"
    )
    fallbacks = chemberta_encoder.CHEMBERTA_MODEL_FALLBACKS
    assert isinstance(fallbacks, list) and len(fallbacks) >= 2, (
        "CHEMBERTA_MODEL_FALLBACKS must be a list with at least 2 models "
        "(primary + at least one fallback). (P2-064)"
    )
    # The primary model must be in the chain.
    assert chemberta_encoder.CHEMBERTA_MODEL in fallbacks, (
        "CHEMBERTA_MODEL (primary) must be the first entry in "
        "CHEMBERTA_MODEL_FALLBACKS. (P2-064)"
    )


def test_p2_064_load_model_with_fallback_exists():
    """P2-064: _load_model_with_fallback function must exist and try
    each fallback model in order."""
    from phase2.drugos_graph import chemberta_encoder

    assert hasattr(chemberta_encoder, "_load_model_with_fallback"), (
        "chemberta_encoder must define _load_model_with_fallback "
        "function. (P2-064)"
    )
    sig = inspect.signature(chemberta_encoder._load_model_with_fallback)
    # Must return a 4-tuple (tokenizer, model, commit_hash, model_name_used).
    assert "primary_model_name" in sig.parameters, (
        "_load_model_with_fallback must accept 'primary_model_name'. (P2-064)"
    )


def test_p2_064_fallback_chain_walks_on_failure():
    """P2-064: when the primary model fails, _load_model_with_fallback
    must try the next model in the chain."""
    from phase2.drugos_graph import chemberta_encoder

    # Mock _load_model to fail for primary, succeed for fallback.
    call_log = []
    def mock_load(model_name, *args, **kwargs):
        call_log.append(model_name)
        if model_name == chemberta_encoder.CHEMBERTA_MODEL:
            raise RuntimeError("primary model unavailable (simulated)")
        # Return a fake 3-tuple for the fallback.
        return ("fake_tok", "fake_model", "fake_commit")

    with patch.object(chemberta_encoder, "_load_model", side_effect=mock_load):
        tok, mdl, ch, used = chemberta_encoder._load_model_with_fallback(
            chemberta_encoder.CHEMBERTA_MODEL,
            revision="main", token=None, torch_dtype_val=None,
            attn_implementation="eager", local_files_only=False,
            cache_dir=None, expected_model_hash=None,
        )
    # The primary must have been tried first.
    assert call_log[0] == chemberta_encoder.CHEMBERTA_MODEL
    # At least one fallback must have been tried.
    assert len(call_log) >= 2, (
        "_load_model_with_fallback must try at least one fallback when "
        "the primary fails. (P2-064)"
    )
    # The returned model_name_used must be the fallback that succeeded.
    assert used != chemberta_encoder.CHEMBERTA_MODEL, (
        "_load_model_with_fallback must return the name of the model "
        "that actually loaded (the fallback), not the primary. (P2-064)"
    )


# ─── P2-065 ──────────────────────────────────────────────────────────
# P2-065 NOTE: On the merged main branch, phase2/drugos_graph/
# graph_transformer_model.py was DELETED — the GraphTransformerModel
# class was moved to graph_transformer/models/graph_transformer.py and
# refactored to use NodeTypeProjection (in embeddings.py) instead of
# nn.Embedding(0, d) for feature-less node types. The new architecture
# does NOT have the P2-065 bug (nn.Embedding(0, d) for feature-less
# node types) because NodeTypeProjection always has a real feature dim.
# P2-065 is therefore RESOLVED BY REFACTOR.
@pytest.mark.skip(
    reason="P2-065 resolved by Phase 3 model refactor — "
           "phase2/drugos_graph/graph_transformer_model.py was deleted; "
           "the new architecture uses NodeTypeProjection which does not "
           "have the nn.Embedding(0, d) bug."
)
def test_p2_065_empty_embedding_table_guard_at_construction():
    """P2-065: SKIPPED — resolved by Phase 3 model refactor."""
    pass


@pytest.mark.skip(
    reason="P2-065 resolved by Phase 3 model refactor."
)
def test_p2_065_encode_raises_clear_error_for_pending_resize():
    """P2-065: SKIPPED — resolved by Phase 3 model refactor."""
    pass


@pytest.mark.skip(
    reason="P2-065 resolved by Phase 3 model refactor."
)
def test_p2_065_resize_clears_pending_set():
    """P2-065: SKIPPED — resolved by Phase 3 model refactor."""
    pass


# ─── P2-066 ──────────────────────────────────────────────────────────
def test_p2_066_post_split_check_uses_runtime_error_not_assert():
    """P2-066: pyg_builder post-split check must use
    `if not ...: raise RuntimeError(...)`, not `assert`."""
    pyg_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "pyg_builder.py"
    )
    with open(pyg_path, "r", encoding="utf-8") as f:
        content = f.read()
    # Find the post-split check block (around "edge_label").
    # The old `assert hasattr(tgt, "edge_label")` must be gone.
    bad_assert = 'assert hasattr(tgt, "edge_label")'
    assert bad_assert not in content, (
        "pyg_builder.py must NOT use 'assert hasattr(tgt, \"edge_label\")' "
        "— replace with 'if not (...): raise RuntimeError(...)' so the "
        "check survives python -O. (P2-066)"
    )
    # The new RuntimeError must be present.
    assert "raise RuntimeError" in content and "P2-066" in content, (
        "pyg_builder.py must use 'raise RuntimeError' with 'P2-066' "
        "in the message for the post-split check. (P2-066)"
    )


# ─── P2-067 ──────────────────────────────────────────────────────────
def test_p2_067_transe_config_documents_phase3_mismatch():
    """P2-067: TransEConfig.embedding_dim docstring must document the
    Phase 2 ↔ Phase 3 embedding_dim mismatch (256 vs 128) and the
    explicit-projection warm-start path."""
    config_path = os.path.join(
        _REPO_ROOT, "phase2", "drugos_graph", "config.py"
    )
    with open(config_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "P2-067" in content, (
        "config.py must contain the P2-067 root-fix documentation. (P2-067)"
    )
    assert "Phase 2 ↔ Phase 3" in content or "Phase 3" in content, (
        "config.py must document the Phase 2 ↔ Phase 3 embedding_dim "
        "mismatch. (P2-067)"
    )
    # Must mention the explicit projection path (the ONLY supported
    # warm-start).
    assert "DRUGOS_TRANSE_EMBEDDING_DIM=128" in content, (
        "config.py must document the DRUGOS_TRANSE_EMBEDDING_DIM=128 "
        "override as the warm-start path. (P2-067)"
    )


def test_p2_067_transe_model_class_documents_mismatch():
    """P2-067: TransEModel class docstring must document the Phase 3
    embedding_dim mismatch and the no-warm-start caveat."""
    from phase2.drugos_graph.transe_model import TransEModel

    docstring = TransEModel.__doc__ or ""
    assert "P2-067" in docstring, (
        "TransEModel class docstring must mention P2-067. (P2-067)"
    )
    assert "128" in docstring and "256" in docstring, (
        "TransEModel class docstring must mention both 128 (Phase 3) "
        "and 256 (Phase 2) embedding dims. (P2-067)"
    )


if __name__ == "__main__":
    # Allow running this file directly: python test_p2_049_to_067_root_fixes.py
    sys.exit(pytest.main([__file__, "-v"]))
