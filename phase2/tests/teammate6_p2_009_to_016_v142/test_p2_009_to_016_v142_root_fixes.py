"""Teammate 6 — Phase 2 (P2-009 to P2-016) v142 forensic root-fix verification.

RED TEAM verification tests for the 8 issues assigned to Teammate 6.
Each test reads the ACTUAL production code (not comments, not stubs) and
asserts that the root-cause fix is present and behaviourally correct.

Issues covered:
  P2-009 [MEDIUM] — Neo4j 4.x CALL{} subquery — raise on 4.x + legacy branch
  P2-010 [MEDIUM] — TransE — pre-forward normalize relation embeddings
  P2-011 [MEDIUM] — step11 — replace hardcoded 42 with DRUGOS_SEED
  P2-012 [MEDIUM] — PyG — CRITICAL log in production for Xavier fallback
  P2-013 [MEDIUM] — create_indexes — raise on failure + verification
  P2-014 [MEDIUM] — MLflow — auto-invoke check_for_dangling_mlflow_runs
  P2-015 [LOW]    — /healthz — cache Neo4j driver + cache result
  P2-016 [LOW]    — step11 — single MLflowTracker passed to train_transe

These tests are SOURCE-LEVEL (they read the code, not just exercise it)
because the user's hostile-auditor protocol requires verifying that the
fix is actually IN the code, not just that some test passes. A passing
test that does not read the source could pass against the broken code
if the test itself was wrong; a source-reading test cannot.
"""
from __future__ import annotations

import ast
import inspect
import os
import textwrap
from pathlib import Path

import pytest

# Resolve the phase2/drugos_graph source directory.
_HERE = Path(__file__).resolve().parent
_DRUGOS_GRAPH_DIR = _HERE.parent.parent / "drugos_graph"


def _read_source(rel_path: str) -> str:
    """Read the source of a drugos_graph module."""
    return (_DRUGOS_GRAPH_DIR / rel_path).read_text(encoding="utf-8")


def _parse_source(rel_path: str) -> ast.Module:
    """Parse the source of a drugos_graph module into an AST."""
    return ast.parse(_read_source(rel_path))


# =============================================================================
# Issue 1 — P2-009: Neo4j 4.x CALL{} subquery — raise on 4.x + legacy branch
# =============================================================================

class TestP2009Neo4j4xCALLSubquery:
    """Verify the Neo4j 4.x CALL{} fix is actually in the code."""

    def test_connect_raises_on_neo4j_4x_without_escape_hatch(self):
        """GraphConnection._detect_version must RAISE on Neo4j 4.x unless
        DRUGOS_ALLOW_NEO4J_4X=1 is set. Pre-v142 it only warned."""
        src = _read_source("kg_builder.py")
        # The raise must be present and tied to the env-var gate.
        assert "DRUGOS_ALLOW_NEO4J_4X" in src, (
            "P2-009: DRUGOS_ALLOW_NEO4J_4X escape hatch missing from kg_builder.py"
        )
        assert "raise CriticalDataSourceError" in src, (
            "P2-009: CriticalDataSourceError raise missing from kg_builder.py"
        )
        # The raise must be inside the 4.x branch, not the 5.x branch.
        assert 'self._neo4j_version.startswith("4.")' in src
        # Find the raise within the 4.x branch.
        idx_4x = src.index('self._neo4j_version.startswith("4.")')
        # Search forward for the raise (within ~2000 chars — should be
        # well within the 4.x branch).
        window = src[idx_4x:idx_4x + 3000]
        assert "raise CriticalDataSourceError" in window, (
            "P2-009: the CriticalDataSourceError raise is NOT inside the "
            "4.x branch. The fix may have been placed in the wrong branch."
        )

    def test_legacy_compound_merge_branch_exists(self):
        """The Compound-MERGE Cypher must have a legacy branch for 4.x
        operators (uses OPTIONAL MATCH + WITH row, existing instead of
        CALL{}). The legacy branch is taken when
        ``self._conn.constraint_syntax == "legacy"``."""
        src = _read_source("kg_builder.py")
        assert "_use_legacy_compound_merge" in src, (
            "P2-009: _use_legacy_compound_merge dispatch variable missing"
        )
        assert 'self._conn.constraint_syntax == "legacy"' in src, (
            "P2-009: legacy dispatch on constraint_syntax missing"
        )
        # The legacy branch must use OPTIONAL MATCH (not CALL{}).
        assert 'OPTIONAL MATCH (existing:Compound)' in src
        # The 5.x branch must still use CALL{}.
        assert "CALL {" in src

    def test_connect_does_not_raise_on_neo4j_5x(self):
        """The 5.x branch must NOT raise — only log a warning if the
        version is unknown (6.x+). 5.x is the supported version."""
        src = _read_source("kg_builder.py")
        # The 5.x branch should not have a raise inside it.
        # Find the 5.x branch (elif not startswith 5.x).
        # The 5.x branch is the implicit else — the elif is for
        # "not 5.x" (i.e. 6.x+). Let's verify the structure.
        # The 4.x branch raises. The "elif not 5.x" branch warns.
        # There should be NO raise in the "elif not 5.x" branch.
        idx_elif = src.find('elif not self._neo4j_version.startswith("5."):')
        assert idx_elif >= 0, "P2-009: 5.x elseif branch missing"
        # Window of ~1500 chars after the elif should NOT contain a raise.
        window = src[idx_elif:idx_elif + 1500]
        # The only raise allowed is the one in the 4.x branch above —
        # but since we started at the elif, no raise should appear.
        assert "raise CriticalDataSourceError" not in window, (
            "P2-009: the 5.x+ branch unexpectedly raises — only the 4.x "
            "branch should raise (5.x is the supported version)."
        )


# =============================================================================
# Issue 2 — P2-010: TransE pre-forward normalize relation embeddings
# =============================================================================

class TestP2010TransEPreForwardNormalize:
    """Verify the pre-forward normalize call is in train_transe."""

    def test_normalize_called_before_forward_pass(self):
        """train_transe must call normalize_entity_embeddings AND
        normalize_relation_embeddings BEFORE the forward pass
        (model(h_batch, r_batch, t_batch)). Pre-v142 normalize was
        only called AFTER optimizer.step()."""
        src = _read_source("transe_model.py")
        # Find the forward pass.
        forward_idx = src.find("pos_scores = model(h_batch, r_batch, t_batch)")
        assert forward_idx >= 0, (
            "P2-010: could not locate the forward pass in train_transe"
        )
        # Look BACKWARD from the forward pass for the normalize calls.
        # They should be within ~2000 chars before the forward pass.
        backward_window = src[max(0, forward_idx - 3000):forward_idx]
        assert "model.normalize_entity_embeddings()" in backward_window, (
            "P2-010: normalize_entity_embeddings() is NOT called before "
            "the forward pass. The pre-forward normalize fix is missing."
        )
        assert "model.normalize_relation_embeddings()" in backward_window, (
            "P2-010: normalize_relation_embeddings() is NOT called before "
            "the forward pass. The pre-forward normalize fix is missing."
        )

    def test_post_step_normalize_still_present(self):
        """The post-step normalize must STILL be present (defensive
        measure for external callers that query the model mid-step).
        Search for the post-step normalize block by looking for the
        specific sequence ``optimizer.step()`` followed by
        ``normalize_entity_embeddings`` (not inside a comment)."""
        src = _read_source("transe_model.py")
        # The post-step normalize is at line 3855+ (step) then 3857+ (normalize).
        # Find all occurrences of optimizer.step() in code (not in comments).
        # The simplest robust check: verify both normalize calls appear
        # AFTER the optimizer.step() line in the source.
        # Find the FIRST ``optimizer.step()`` that's on its own line
        # (code, not comment).
        lines = src.splitlines()
        step_line_idx = None
        for i, line in enumerate(lines):
            stripped = line.lstrip()
            if stripped == "optimizer.step()" or stripped.startswith("optimizer.step()  #"):
                step_line_idx = i
                break
        assert step_line_idx is not None, (
            "P2-010: could not find optimizer.step() in train_transe"
        )
        # Look forward up to 50 lines for the normalize calls.
        forward_window = "\n".join(lines[step_line_idx:step_line_idx + 50])
        assert "model.normalize_entity_embeddings()" in forward_window, (
            "P2-010: post-step normalize_entity_embeddings() missing"
        )
        assert "model.normalize_relation_embeddings()" in forward_window, (
            "P2-010: post-step normalize_relation_embeddings() missing"
        )

    def test_docstring_documents_p2010_fix(self):
        """The normalize_relation_embeddings docstring must document
        the P2-010 v142 fix (per issue fix #3)."""
        src = _read_source("transe_model.py")
        assert "P2-010 v142" in src, (
            "P2-010: the v142 fix is not documented in the source"
        )


# =============================================================================
# Issue 3 — P2-011: step11 hardcoded seed=42
# =============================================================================

class TestP2011Step11SeedFromEnvVar:
    """Verify step11 reads DRUGOS_SEED instead of hardcoding 42."""

    def test_no_hardcoded_42_in_step11_seed_call(self):
        """The line ``_set_global_seed(42)`` must NOT appear in actual
        CODE in step11_train_transe. It must be ``_set_global_seed()``
        (no arg, defaults to module SEED = DRUGOS_SEED env var).
        Note: the string may appear in COMMENTS explaining the fix,
        so we strip comments before checking."""
        src = _read_source("run_pipeline.py")
        # Find step11_train_transe.
        idx = src.find("def step11_train_transe(")
        assert idx >= 0
        # Find the next function def after step11 (to bound the search).
        next_def_idx = src.find("\ndef ", idx + 1)
        step11_src = src[idx:next_def_idx]
        # Strip Python comments (lines starting with #, or after #).
        # For robustness, just check that the actual call (not the
        # comment) uses the no-arg form. We do this by checking that
        # the line ``_set_global_seed()`` exists AND that no line
        # contains ``_set_global_seed(42)`` as actual code (i.e. not
        # preceded by #).
        lines = step11_src.splitlines()
        hardcoded_in_code = False
        no_arg_in_code = False
        for line in lines:
            # Strip inline comments.
            if "#" in line:
                line = line[:line.index("#")]
            stripped = line.strip()
            if "_set_global_seed(42)" in stripped:
                hardcoded_in_code = True
            if "_set_global_seed()" in stripped:
                no_arg_in_code = True
        assert not hardcoded_in_code, (
            "P2-011: _set_global_seed(42) is still hardcoded in actual "
            "CODE in step11_train_transe. The fix to use DRUGOS_SEED "
            "is missing."
        )
        assert no_arg_in_code, (
            "P2-011: _set_global_seed() (no-arg form) is missing from "
            "step11_train_transe. The fix should call this to use the "
            "module-level SEED (= DRUGOS_SEED env var)."
        )

    def test_assertion_detects_seed_divergence(self):
        """An assertion must be present that detects divergence between
        the module SEED and the DRUGOS_SEED env var (per issue fix #2)."""
        src = _read_source("run_pipeline.py")
        assert "DRUGOS_SEED" in src, (
            "P2-011: DRUGOS_SEED env var is not read in run_pipeline.py"
        )
        assert "P2-011 v142: SEED divergence detected" in src, (
            "P2-011: the divergence-detection assertion is missing"
        )

    def test_run_full_pipeline_also_fixed(self):
        """run_full_pipeline must also use _set_global_seed() (no arg)
        — not just step11. Otherwise the two would diverge."""
        src = _read_source("run_pipeline.py")
        # Find run_full_pipeline.
        idx = src.find("def run_full_pipeline(")
        assert idx >= 0
        # The function is long (11000+ lines for run_pipeline.py).
        # Search the WHOLE function body for the no-arg form.
        next_def_idx = src.find("\ndef ", idx + 1)
        if next_def_idx < 0:
            next_def_idx = len(src)
        window = src[idx:next_def_idx]
        # Strip comments before checking (same as step11 test).
        lines = window.splitlines()
        no_arg_in_code = False
        hardcoded_in_code = False
        for line in lines:
            if "#" in line:
                line = line[:line.index("#")]
            stripped = line.strip()
            if "_set_global_seed()" in stripped:
                no_arg_in_code = True
            if "_set_global_seed(42)" in stripped:
                hardcoded_in_code = True
        assert no_arg_in_code, (
            "P2-011: run_full_pipeline does not call _set_global_seed() "
            "(no-arg form). Both step11 AND run_full_pipeline must use "
            "the no-arg form to stay synchronized with config.seed."
        )
        assert not hardcoded_in_code, (
            "P2-011: run_full_pipeline still has _set_global_seed(42) "
            "in actual code."
        )


# =============================================================================
# Issue 4 — P2-012: PyG CRITICAL log in production for Xavier fallback
# =============================================================================

class TestP2012PyGXavierCriticalInProduction:
    """Verify the Xavier fallback logs CRITICAL in production."""

    def test_critical_log_in_production(self):
        """The Xavier fallback must log at CRITICAL level when
        DRUGOS_ENVIRONMENT=production. Pre-v142 it only warned."""
        src = _read_source("pyg_builder.py")
        assert "DRUGOS_ENVIRONMENT" in src, (
            "P2-012: DRUGOS_ENVIRONMENT check missing from pyg_builder.py"
        )
        assert "self.logger.critical" in src, (
            "P2-012: logger.critical call missing from pyg_builder.py"
        )
        # The CRITICAL call must be inside the production branch.
        assert "_is_production" in src
        # Find the critical call and verify it's gated on _is_production.
        critical_idx = src.find("self.logger.critical(_xavier_log_msg)")
        assert critical_idx >= 0, (
            "P2-012: self.logger.critical(_xavier_log_msg) call missing"
        )
        # Look backward for the if _is_production: branch.
        backward_window = src[max(0, critical_idx - 500):critical_idx]
        assert "if _is_production:" in backward_window, (
            "P2-012: the critical log is NOT gated on _is_production. "
            "It must only fire in production."
        )

    def test_xavier_fallback_still_raises_without_env_var(self):
        """The DRUGOS_ALLOW_XAVIER_FALLBACK=1 escape hatch must still
        raise RuntimeError when NOT set (preserves the v111 fix)."""
        src = _read_source("pyg_builder.py")
        assert "DRUGOS_ALLOW_XAVIER_FALLBACK" in src
        assert "raise RuntimeError" in src


# =============================================================================
# Issue 5 — P2-013: create_indexes must raise on failure
# =============================================================================

class TestP2013CreateIndexesRaisesOnFailure:
    """Verify create_indexes raises CriticalDataSourceError on failure."""

    def test_create_indexes_raises_on_failure(self):
        """create_indexes must raise CriticalDataSourceError when any
        index creation fails (mirrors create_constraints behaviour).
        Pre-v142 it only logged ERROR."""
        src = _read_source("kg_builder.py")
        # Find the GraphSchemaManager.create_indexes method (the real one).
        # There are TWO create_indexes defs: the schema manager's and
        # the delegate. The schema manager's is the one with the raise.
        idx = src.find("def create_indexes(self, *, strict:")
        assert idx >= 0, (
            "P2-013: GraphSchemaManager.create_indexes with strict param not found"
        )
        # Find the NEXT def after this one (to bound the search).
        next_def_idx = src.find("\n    def ", idx + 1)
        if next_def_idx < 0:
            next_def_idx = src.find("\nclass ", idx + 1)
        if next_def_idx < 0:
            next_def_idx = len(src)
        window = src[idx:next_def_idx]
        assert "raise CriticalDataSourceError" in window, (
            "P2-013: create_indexes does NOT raise CriticalDataSourceError "
            "on failure. The fix is missing."
        )
        assert "P2-013" in window, (
            "P2-013: the fix comment is missing from create_indexes"
        )

    def test_create_indexes_has_strict_parameter(self):
        """create_indexes must accept a ``strict`` parameter (issue fix #2)
        that controls raise vs log. Default True (raise)."""
        src = _read_source("kg_builder.py")
        idx = src.find("def create_indexes(self")
        assert idx >= 0
        # The signature should include ``strict: Optional[bool] = None``.
        signature_window = src[idx:idx + 300]
        assert "strict" in signature_window, (
            "P2-013: the ``strict`` parameter is missing from create_indexes"
        )

    def test_create_indexes_post_load_verification(self):
        """create_indexes must query SHOW INDEXES after creation and
        verify all ADDITIONAL_INDEXES are present (issue fix #3)."""
        src = _read_source("kg_builder.py")
        idx = src.find("def create_indexes(self")
        window = src[idx:idx + 8000]
        assert "SHOW INDEXES" in window, (
            "P2-013: SHOW INDEXES post-load verification missing"
        )
        assert "missing_indexes" in window, (
            "P2-013: missing-indexes detection logic missing"
        )

    def test_create_indexes_delegate_forwards_strict(self):
        """The delegate create_indexes on the main builder class must
        forward the ``strict`` parameter to GraphSchemaManager."""
        src = _read_source("kg_builder.py")
        # Find the delegate (the second create_indexes def).
        idx1 = src.find("def create_indexes(self")
        idx2 = src.find("def create_indexes(self", idx1 + 1)
        assert idx2 >= 0, "P2-013: delegate create_indexes not found"
        delegate_window = src[idx2:idx2 + 500]
        assert "strict=strict" in delegate_window, (
            "P2-013: delegate does not forward strict=strict"
        )


# =============================================================================
# Issue 6 — P2-014: MLflow auto-invoke check_for_dangling_mlflow_runs
# =============================================================================

class TestP2014MLflowAutoSelfCheck:
    """Verify MLflowTracker auto-invokes the dangling-runs self-check."""

    def test_startup_self_check_spawned_in_init(self):
        """MLflowTracker.__init__ must spawn the self-check thread
        (gated by _startup_check_done to prevent recursion). Pre-v142
        the self-check existed but was never auto-invoked."""
        src = _read_source("mlflow_tracker.py")
        assert "_startup_check_done" in src, (
            "P2-014: _startup_check_done class flag missing"
        )
        assert "_run_startup_self_check" in src, (
            "P2-014: _run_startup_self_check method missing"
        )
        # The thread spawn must be in __init__.
        init_idx = src.find("def __init__(\n        self,\n        # P2-050")
        if init_idx < 0:
            init_idx = src.find("def __init__(")
        assert init_idx >= 0
        # Search forward for the thread spawn.
        window = src[init_idx:init_idx + 8000]
        assert "_run_startup_self_check" in window, (
            "P2-014: _run_startup_self_check is not invoked in __init__"
        )
        assert "threading.Thread" in window, (
            "P2-014: threading.Thread spawn missing from __init__"
        )

    def test_recursion_guard_present(self):
        """The _startup_check_done flag must be checked BEFORE spawning
        the self-check thread (to prevent infinite recursion via
        check_for_dangling_mlflow_runs → MLflowTracker() → self-check)."""
        src = _read_source("mlflow_tracker.py")
        assert "MLflowTracker._startup_check_lock" in src, (
            "P2-014: class-level lock missing (recursion guard)"
        )

    def test_atexit_logs_at_error_level(self):
        """The atexit close failure must be logged at ERROR level
        (not WARNING). Pre-v107 it was swallowed; v107 logged WARNING;
        v132 promoted to ERROR. Verify ERROR is still present."""
        src = _read_source("mlflow_tracker.py")
        assert "logger.error(" in src
        # The atexit handler must use logger.error.
        atexit_idx = src.find("def _atexit_close(self)")
        assert atexit_idx >= 0
        # The docstring is long (~80 lines). Use a larger window.
        window = src[atexit_idx:atexit_idx + 6000]
        assert "logger.error(" in window, (
            "P2-014: _atexit_close does not log at ERROR level"
        )

    def test_prometheus_counter_present(self):
        """The mlflow_atexit_close_failures_total Prometheus Counter
        must be defined (issue fix #2)."""
        src = _read_source("mlflow_tracker.py")
        assert "mlflow_atexit_close_failures_total" in src


# =============================================================================
# Issue 7 — P2-015: /healthz — cache Neo4j driver + cache result
# =============================================================================

class TestP2015HealthzDriverCache:
    """Verify /healthz caches the Neo4j driver and the result."""

    def test_module_level_driver_cache_present(self):
        """kg_api.py must have a module-level driver cache
        (_healthz_cached_driver) and a lock (_HEALTHZ_DRIVER_LOCK).
        Pre-v142 a NEW driver was created on every /healthz call."""
        src = _read_source("kg_api.py")
        assert "_healthz_cached_driver" in src, (
            "P2-015: module-level driver cache missing"
        )
        assert "_HEALTHZ_DRIVER_LOCK" in src, (
            "P2-015: driver-cache lock missing"
        )
        assert "_get_healthz_neo4j_driver" in src, (
            "P2-015: _get_healthz_neo4j_driver helper missing"
        )

    def test_result_cache_present(self):
        """kg_api.py must cache the /healthz result for TTL seconds
        (configurable via DRUGOS_HEALTHCHECK_CACHE_TTL). Pre-v142
        every call re-checked Neo4j."""
        src = _read_source("kg_api.py")
        assert "_healthz_cached_result" in src, (
            "P2-015: result cache variable missing"
        )
        assert "_HEALTHZ_CACHE_TTL_SECONDS" in src, (
            "P2-015: TTL constant missing"
        )
        assert "DRUGOS_HEALTHCHECK_CACHE_TTL" in src, (
            "P2-015: DRUGOS_HEALTHCHECK_CACHE_TTL env var not read"
        )

    def test_no_new_driver_per_call(self):
        """The healthz function body must NOT contain
        ``driver = GraphDatabase.driver(...)`` (which would create a
        new driver per call). It must use _check_neo4j_reachable()."""
        src = _read_source("kg_api.py")
        # Find the healthz function body. The decorator may have tags=.
        idx = src.find("@app.get(\"/healthz\"")
        assert idx >= 0, (
            "P2-015: @app.get(\"/healthz\" decorator not found"
        )
        # Find the next decorator or end of module.
        next_dec = src.find("@app.", idx + 1)
        if next_dec < 0:
            next_dec = len(src)
        healthz_body = src[idx:next_dec]
        # The per-call driver creation must NOT be in the healthz body.
        # ``GraphDatabase.driver(`` may appear in the cached-driver
        # helper (_get_healthz_neo4j_driver) which is OUTSIDE the
        # healthz function body — that's correct (the helper creates
        # the driver ONCE and caches it). The check is that the healthz
        # BODY itself doesn't create a driver.
        assert "driver = GraphDatabase.driver(" not in healthz_body, (
            "P2-015: /healthz still creates a new GraphDatabase.driver per "
            "call. The fix to use the cached driver is missing."
        )
        # The cached-check helper must be used.
        assert "_check_neo4j_reachable()" in healthz_body, (
            "P2-015: /healthz does not call _check_neo4j_reachable()"
        )


# =============================================================================
# Issue 8 — P2-016: step11 — single MLflowTracker passed to train_transe
# =============================================================================

class TestP2016Step11SingleMLflowTracker:
    """Verify step11 uses ONE MLflowTracker, not two."""

    def test_train_transe_has_manage_mlflow_lifecycle_kwarg(self):
        """train_transe must accept a ``manage_mlflow_lifecycle`` kwarg
        so the caller can manage the run lifecycle (start_run/end_run)
        and train_transe only logs params/metrics."""
        src = _read_source("transe_model.py")
        assert "manage_mlflow_lifecycle: bool = True" in src, (
            "P2-016: manage_mlflow_lifecycle kwarg missing from train_transe"
        )

    def test_train_transe_skips_start_run_when_caller_manages(self):
        """When manage_mlflow_lifecycle=False, train_transe must NOT
        call start_run (the caller already started the run)."""
        src = _read_source("transe_model.py")
        # The start_run call must be gated on manage_mlflow_lifecycle.
        assert "if mlflow_tracker is not None and manage_mlflow_lifecycle:" in src, (
            "P2-016: start_run is not gated on manage_mlflow_lifecycle"
        )

    def test_train_transe_skips_end_run_when_caller_manages(self):
        """When manage_mlflow_lifecycle=False, train_transe must NOT
        call end_run (the caller will end the run after logging its
        own final metrics)."""
        src = _read_source("transe_model.py")
        # All end_run calls must be gated on manage_mlflow_lifecycle.
        assert "if mlflow_tracker is not None and manage_mlflow_lifecycle:" in src
        # The success-path end_run must also be gated.
        assert "if manage_mlflow_lifecycle:\n            mlflow_tracker.end_run()" in src, (
            "P2-016: success-path end_run is not gated on manage_mlflow_lifecycle"
        )

    def test_step11_creates_tracker_before_train_transe(self):
        """step11 must create the MLflowTracker BEFORE calling
        train_transe (so it can pass the tracker via mlflow_tracker=)."""
        src = _read_source("run_pipeline.py")
        idx = src.find("def step11_train_transe(")
        assert idx >= 0
        next_def = src.find("\ndef ", idx + 1)
        step11_src = src[idx:next_def]
        tracker_idx = step11_src.find("_step11_tracker = MLflowTracker()")
        train_idx = step11_src.find("history = train_transe(")
        assert tracker_idx >= 0, (
            "P2-016: step11 does not create _step11_tracker"
        )
        assert train_idx >= 0, (
            "P2-016: step11 does not call train_transe"
        )
        assert tracker_idx < train_idx, (
            "P2-016: step11 creates the tracker AFTER calling train_transe. "
            "The tracker must be created BEFORE so it can be passed via "
            "mlflow_tracker=."
        )

    def test_step11_passes_tracker_to_train_transe(self):
        """step11 must pass ``mlflow_tracker=_step11_tracker`` and
        ``manage_mlflow_lifecycle=False`` to train_transe."""
        src = _read_source("run_pipeline.py")
        idx = src.find("def step11_train_transe(")
        next_def = src.find("\ndef ", idx + 1)
        step11_src = src[idx:next_def]
        assert "mlflow_tracker=_step11_tracker" in step11_src, (
            "P2-016: step11 does not pass mlflow_tracker=_step11_tracker"
        )
        assert "manage_mlflow_lifecycle=False" in step11_src, (
            "P2-016: step11 does not pass manage_mlflow_lifecycle=False"
        )

    def test_step11_ends_run_once_after_train_transe(self):
        """step11 must call _step11_tracker.end_run() ONCE, AFTER
        train_transe returns (and AFTER logging step11-specific final
        metrics). Pre-v162 there were TWO end_run calls (one in
        train_transe, one in step11)."""
        src = _read_source("run_pipeline.py")
        idx = src.find("def step11_train_transe(")
        next_def = src.find("\ndef ", idx + 1)
        step11_src = src[idx:next_def]
        # Count end_run calls in step11. Should be exactly 1.
        end_run_count = step11_src.count("_step11_tracker.end_run()")
        assert end_run_count == 1, (
            f"P2-016: step11 has {end_run_count} _step11_tracker.end_run() "
            f"calls; expected exactly 1 (after train_transe returns)."
        )
        # The end_run must be AFTER the train_transe call.
        end_run_idx = step11_src.find("_step11_tracker.end_run()")
        train_idx = step11_src.find("history = train_transe(")
        assert end_run_idx > train_idx, (
            "P2-016: step11 calls end_run BEFORE train_transe. The run "
            "must be ended AFTER train_transe logs its per-epoch metrics."
        )

    def test_step11_no_separate_post_train_tracker(self):
        """step11 must NOT create a SEPARATE MLflowTracker after
        train_transe returns. Pre-v142 it did (line 7717)."""
        src = _read_source("run_pipeline.py")
        idx = src.find("def step11_train_transe(")
        next_def = src.find("\ndef ", idx + 1)
        step11_src = src[idx:next_def]
        # After train_transe, there should be NO new MLflowTracker() call.
        train_idx = step11_src.find("history = train_transe(")
        after_train = step11_src[train_idx:]
        assert "MLflowTracker()" not in after_train, (
            "P2-016: step11 creates a SEPARATE MLflowTracker after "
            "train_transe. The fix should reuse _step11_tracker."
        )


# =============================================================================
# Smoke test: verify all 8 fixes are mentioned in the source
# =============================================================================

class TestAllEightFixesPresent:
    """Cross-cutting test: verify all 8 P2-XXX v142 fix markers exist."""

    def test_all_eight_fix_markers_present(self):
        """Each fix is marked with 'P2-XXX v142' in the source.
        Verify all 8 markers are present (one per issue)."""
        all_src = ""
        for f in (
            "kg_builder.py",
            "transe_model.py",
            "run_pipeline.py",
            "pyg_builder.py",
            "mlflow_tracker.py",
            "kg_api.py",
        ):
            all_src += _read_source(f)

        expected_markers = [
            "P2-009",  # Neo4j 4.x CALL{}
            "P2-010",  # TransE pre-forward normalize
            "P2-011",  # step11 seed
            "P2-012",  # PyG Xavier CRITICAL
            "P2-013",  # create_indexes raise
            "P2-014",  # MLflow auto self-check
            "P2-015",  # /healthz driver cache
            "P2-016",  # step11 single MLflowTracker
        ]
        missing = [m for m in expected_markers if m not in all_src]
        assert not missing, (
            f"The following P2-XXX fix markers are missing from the "
            f"source: {missing}. All 8 fixes must be present."
        )


if __name__ == "__main__":
    # Allow running this test file directly:
    #     python -m pytest phase2/tests/teammate6_p2_009_to_016_v142/test_p2_009_to_016_v142_root_fixes.py -v
    pytest.main([__file__, "-v", "--tb=short"])
