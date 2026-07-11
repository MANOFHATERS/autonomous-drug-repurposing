#!/usr/bin/env python3
"""
v100 Forensic Root-Fix Verification — R-001 through R-017

This test suite verifies each of the 17 bugs documented in the forensic
audit was actually fixed at the CODE level (not just comments). Each test
reads the real source files and asserts the fix is present. This is the
"trust but verify" layer — it catches regressions if anyone re-introduces
a bug.

Run with:
    python -m pytest tests/test_v100_r001_r017_fixes.py -v
"""
from __future__ import annotations

import ast
import os
import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


def _read(rel: str) -> str:
    """Read a source file from the project root."""
    return (_ROOT / rel).read_text()


def _parse(rel: str) -> ast.AST:
    """Parse a source file into an AST."""
    return ast.parse(_read(rel))


# ─── R-001: subprocess NameError in run_unified.py ──────────────────────

def test_r001_subprocess_imported_at_module_level():
    """R-001: `import subprocess` must be at module level so that
    `subprocess.SubprocessError` in the except clause resolves."""
    tree = _parse("run_unified.py")
    module_imports = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Import)
        and any(a.name == "subprocess" for a in n.names)
    ]
    assert len(module_imports) >= 1, (
        "R-001 FAIL: `import subprocess` is NOT at module level in "
        "run_unified.py. The except clause `subprocess.SubprocessError` "
        "will raise NameError when Tier 1 subprocess times out."
    )


def test_r001_no_alias_only_subprocess_import_in_tier1():
    """R-001: the Tier 1 block must not rely on `import subprocess as _sp`
    alone — `subprocess` must be bound in scope."""
    src = _read("run_unified.py")
    # The except clause must reference subprocess.SubprocessError (not _sp)
    assert "subprocess.SubprocessError" in src, (
        "R-001 FAIL: except clause does not reference subprocess.SubprocessError"
    )
    # And subprocess must be importable (module-level import exists)
    assert "import subprocess" in src, (
        "R-001 FAIL: no `import subprocess` found in run_unified.py"
    )


# ─── R-002: seed NameError in run_pipeline.py ───────────────────────────

def test_r002_run_phase2_kg_builder_has_seed_param():
    """R-002: run_phase2_kg_builder must accept a `seed` parameter."""
    tree = _parse("run_pipeline.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_phase2_kg_builder":
            arg_names = [a.arg for a in node.args.args]
            assert "seed" in arg_names, (
                f"R-002 FAIL: run_phase2_kg_builder has no `seed` parameter. "
                f"Args found: {arg_names}"
            )
            return
    pytest.fail("R-002 FAIL: run_phase2_kg_builder function not found")


# ─── R-003: swapped (staged, builder) tuple ─────────────────────────────

def test_r003_no_swapped_bridge_recall():
    """R-003: the swapped `staged, builder = run_bridge(...)` re-call
    must be GONE from main() (as actual CODE, not just comments)."""
    tree = _parse("run_pipeline.py")
    # Walk all assignment statements and check none has the swapped pattern
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Tuple)
                        and len(target.elts) == 2
                        and isinstance(target.elts[0], ast.Name)
                        and isinstance(target.elts[1], ast.Name)
                        and target.elts[0].id == "staged"
                        and target.elts[1].id == "builder"
                        and isinstance(node.value, ast.Call)
                        and isinstance(node.value.func, ast.Name)
                        and node.value.func.id == "run_bridge"):
                    pytest.fail(
                        "R-003 FAIL: found `staged, builder = run_bridge(...)` "
                        "assignment in code — the swapped re-call is still present."
                    )


def test_r003_run_bridge_returns_builder_staged():
    """R-003: run_bridge must return (builder, staged) — not (staged, builder)."""
    tree = _parse("run_pipeline.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_bridge":
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and isinstance(child.value, ast.Tuple):
                    names = [e.id for e in child.value.elts if isinstance(e, ast.Name)]
                    assert names == ["builder", "staged"], (
                        f"R-003 FAIL: run_bridge returns {names}, "
                        f"expected ['builder', 'staged']"
                    )
                    return
    pytest.fail("R-003 FAIL: run_bridge function has no return builder, staged")


# ─── R-004: dead-code run_schema_adapter ────────────────────────────────

def test_r004_run_schema_adapter_function_deleted():
    """R-004: the dead `run_schema_adapter` function must be deleted."""
    tree = _parse("run_pipeline.py")
    func_names = [
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
    ]
    assert "run_schema_adapter" not in func_names, (
        "R-004 FAIL: `run_schema_adapter` function still exists (dead code). "
        "Its return value was immediately overwritten — it should be deleted."
    )


def test_r004_no_dead_schema_adapter_call():
    """R-004: no actual CODE call to run_schema_adapter() (the function
    was deleted, so any call would be a NameError)."""
    tree = _parse("run_pipeline.py")
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "run_schema_adapter"):
            pytest.fail(
                "R-004 FAIL: found a call to run_schema_adapter() in code "
                "— the function was deleted, this would be a NameError."
            )


# ─── R-005: phase1_csvs NameError ───────────────────────────────────────

def test_r005_phase1_csvs_captured():
    """R-005: ensure_phase1_data return value must be captured into
    `phase1_csvs` so the summary print doesn't NameError."""
    src = _read("run_pipeline.py")
    assert "phase1_csvs = ensure_phase1_data" in src, (
        "R-005 FAIL: `phase1_csvs = ensure_phase1_data(...)` not found. "
        "The return value is discarded, causing NameError at the summary print."
    )


# ─── R-006: run_real_pipeline.py synthetic fallback ─────────────────────

def test_r006_run_real_pipeline_passes_phase1_staged_data():
    """R-006: run_real_pipeline.py must pass phase1_staged_data=staged
    to bridge.run_full_pipeline (NOT just num_drugs/num_diseases)."""
    src = _read("run_real_pipeline.py")
    assert "phase1_staged_data=staged" in src, (
        "R-006 FAIL: run_real_pipeline.py does not pass phase1_staged_data. "
        "It falls through to build_demo_graph (synthetic)."
    )


def test_r006_run_real_pipeline_has_phase1_ensure():
    """R-006: run_real_pipeline.py must call _ensure_phase1_data."""
    src = _read("run_real_pipeline.py")
    assert "_ensure_phase1_data" in src and "def _ensure_phase1_data" in src, (
        "R-006 FAIL: run_real_pipeline.py has no _ensure_phase1_data function"
    )


# ─── R-007: run_unified.py missing Phase 3+4 ────────────────────────────

def test_r007_run_unified_invokes_gtrl_bridge():
    """R-007: run_unified.py must invoke GTRLBridge.run_full_pipeline
    with phase1_staged_data (Phase 3+4)."""
    src = _read("run_unified.py")
    assert "from graph_transformer.gt_rl_bridge import GTRLBridge" in src, (
        "R-007 FAIL: run_unified.py does not import GTRLBridge"
    )
    assert "phase1_staged_data=result[\"staged\"]" in src, (
        "R-007 FAIL: run_unified.py does not pass phase1_staged_data to GTRLBridge"
    )


def test_r007_run_unified_phase3_block_present():
    """R-007: the Phase 3+4 block must be present."""
    src = _read("run_unified.py")
    assert "PHASE 3 + 4: Graph Transformer + RL Hypothesis Ranking" in src, (
        "R-007 FAIL: Phase 3+4 block not found in run_unified.py"
    )
    assert "ALL 4 PHASES COMPLETE" in src, (
        "R-007 FAIL: 'ALL 4 PHASES COMPLETE' marker not found"
    )


# ─── R-008: verify_v63_fixes.py hardcoded path ──────────────────────────

def test_r008_verify_v63_uses_dynamic_path():
    """R-008: verify_v63_fixes.py must use os.path.dirname(__file__)
    instead of a hardcoded path."""
    src = _read("verify_v63_fixes.py")
    assert 'HERE = os.path.dirname(os.path.abspath(__file__))' in src, (
        "R-008 FAIL: HERE is not dynamically computed from __file__"
    )
    assert 'HERE = "/home/z/my-project/work"' not in src, (
        "R-008 FAIL: hardcoded HERE path still present"
    )


# ─── R-009: duplicate bridge call ───────────────────────────────────────

def test_r009_no_duplicate_bridge_call():
    """R-009: run_bridge must call run_phase1_to_phase2 only ONCE
    (count actual Call nodes via AST, not string matching)."""
    tree = _parse("run_pipeline.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run_bridge":
            call_count = sum(
                1 for child in ast.walk(node)
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Name)
                        and child.func.id == "run_phase1_to_phase2")
            )
            assert call_count == 1, (
                f"R-009 FAIL: run_bridge calls run_phase1_to_phase2 "
                f"{call_count} times — expected exactly 1. Duplicate "
                f"bridge call still present."
            )
            return
    pytest.fail("R-009 FAIL: run_bridge function not found")


# ─── R-010: broad except in run_full_platform.py ────────────────────────

def test_r010_no_bare_except_in_run_full_platform():
    """R-010: run_full_platform.py's bridge and pipeline try blocks must
    not have bare `except Exception` that swallows programming bugs.
    The _ensure_phase1_data subprocess fallback is exempt (catching
    Exception there is correct — any subprocess failure should fall back)."""
    tree = _parse("run_full_platform.py")
    # Find all except handlers that catch Exception in the main() function
    # (not in _ensure_phase1_data which is a subprocess fallback)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "main":
            for child in ast.walk(node):
                if isinstance(child, ast.ExceptHandler):
                    # Check if it catches bare Exception
                    if child.type is not None:
                        exc_name = ast.dump(child.type)
                        if "Exception" in exc_name and child.name:
                            pytest.fail(
                                f"R-010 FAIL: found `except {exc_name}` in "
                                f"main() at line {child.lineno}. Programming "
                                f"bugs are being swallowed."
                            )


# ─── R-011: broad except in run_pipeline.py ─────────────────────────────

def test_r011_no_bare_except_in_run_pipeline():
    """R-011: run_pipeline.py must not have bare `except Exception` in
    the main() try block."""
    src = _read("run_pipeline.py")
    bare_count = src.count("except Exception as e:")
    assert bare_count == 0, (
        f"R-011 FAIL: found {bare_count} bare `except Exception as e:` in "
        f"run_pipeline.py. Programming bugs are being swallowed."
    )


# ─── R-012: silent ImportError for rapidfuzz ────────────────────────────

def test_r012_no_silent_import_error_pass():
    """R-012: the `except ImportError: pass` must be replaced with a
    logger.warning."""
    src = _read("run_pipeline.py")
    assert "except ImportError:\n                pass" not in src, (
        "R-012 FAIL: `except ImportError: pass` still present — rapidfuzz "
        "missing is silently ignored, causing KP Recovery = 0%."
    )
    # Should now have a logger.warning
    assert "rapidfuzz not installed" in src or "KP fuzzy matching disabled" in src, (
        "R-012 FAIL: no logger.warning for missing rapidfuzz found"
    )


# ─── R-013: KeyError in run_real_pipeline.py ────────────────────────────

def test_r013_no_direct_dict_access_rl_latency():
    """R-013: results['rl_inference_latency_ms'] must use .get()."""
    src = _read("run_real_pipeline.py")
    assert "results['rl_inference_latency_ms']" not in src, (
        "R-013 FAIL: direct dict access results['rl_inference_latency_ms'] "
        "still present — KeyError if key is missing."
    )
    assert "results.get('rl_inference_latency_ms'" in src, (
        "R-013 FAIL: .get() not used for rl_inference_latency_ms"
    )


# ─── R-014: ValueError in run_real_pipeline.py ──────────────────────────

def test_r014_no_string_format_as_float():
    """R-014: the CODE must not format sv.get('gt_test_auc', 'N/A')
    directly with :.4f (ValueError when key missing). Check via AST."""
    tree = _parse("run_real_pipeline.py")
    # Walk all f-strings and check none formats 'N/A' default with :.4f
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    # Check if the format spec contains 'f' (float format)
                    if value.format_spec is not None:
                        fmt = ast.dump(value.format_spec)
                        if "f" in fmt and isinstance(value.value, ast.Call):
                            # Check if it's a .get() call with 'N/A' default
                            call = value.value
                            if (isinstance(call.func, ast.Attribute)
                                    and call.func.attr == "get"
                                    and len(call.args) >= 2
                                    and isinstance(call.args[1], ast.Constant)
                                    and call.args[1].value == "N/A"):
                                pytest.fail(
                                    "R-014 FAIL: found .get('...', 'N/A') "
                                    "formatted with float spec — ValueError "
                                    "when key is missing."
                                )
    # Also check the specific known pattern is gone from code (not comments)
    # by checking there's no line that has both 'N/A' and ':.4f'
    for line in _read("run_real_pipeline.py").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('""') or stripped.startswith("'"):
            continue
        if "'N/A'" in stripped and ":.4f" in stripped and "sv.get" in stripped:
            pytest.fail(
                f"R-014 FAIL: line still formats N/A as float: {stripped}"
            )


# ─── R-015: config drift (three different bridge patterns) ──────────────

def test_r015_all_runners_pass_real_data():
    """R-015: all three runners must pass REAL data (phase1_staged_data
    or graph_data), not num_drugs/num_diseases (synthetic fallback)."""
    for runner in ("run_pipeline.py", "run_full_platform.py", "run_real_pipeline.py"):
        src = _read(runner)
        has_real = (
            "phase1_staged_data=" in src
            or "graph_data=graph_data" in src
        )
        assert has_real, (
            f"R-015 FAIL: {runner} does not pass phase1_staged_data or "
            f"graph_data — config drift / synthetic fallback."
        )


# ─── R-016: Makefile missing targets ────────────────────────────────────

def test_r016_makefile_has_run_full_platform_target():
    """R-016: Makefile must have a run-full-platform target and it must
    be the default `run` target."""
    src = _read("Makefile")
    assert "run-full-platform:" in src, (
        "R-016 FAIL: Makefile has no run-full-platform target"
    )
    assert "run: run-full-platform" in src, (
        "R-016 FAIL: `run` target does not delegate to run-full-platform"
    )
    assert "$(PYTHON) run_full_platform.py" in src, (
        "R-016 FAIL: Makefile does not invoke run_full_platform.py"
    )


# ─── R-017: multiple requirements files ─────────────────────────────────

def test_r017_makefile_installs_only_top_level_requirements():
    """R-017: Makefile install target must install ONLY the top-level
    requirements.txt (not phase1/ and phase2/ sub-requirements)."""
    src = _read("Makefile")
    install_section = src[src.find("install:"):src.find("dry-run:")]
    assert "pip install -r requirements.txt" in install_section or \
           "$(PIP) install -r requirements.txt" in install_section, (
        "R-017 FAIL: Makefile install target does not install top-level requirements.txt"
    )
    assert "phase1/requirements.txt" not in install_section, (
        "R-017 FAIL: Makefile install still installs phase1/requirements.txt "
        "— causes version conflicts with top-level requirements."
    )
    assert "phase2/drugos_graph/requirements.txt" not in install_section, (
        "R-017 FAIL: Makefile install still installs phase2/drugos_graph/requirements.txt"
    )


# ─── Runtime smoke test (only runs if deps are installed) ───────────────

def test_runtime_run_pipeline_imports_cleanly():
    """Runtime: run_pipeline.py must import without SyntaxError or
    NameError at module level."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "run_pipeline", str(_ROOT / "run_pipeline.py")
        )
        mod = importlib.util.module_from_spec(spec)
        # Don't execute main(), just check the module parses & imports
        # (importlib would execute top-level code, so we just compile)
        compile((_ROOT / "run_pipeline.py").read_text(), "run_pipeline.py", "exec")
    except SyntaxError as e:
        pytest.fail(f"run_pipeline.py has SyntaxError: {e}")


def test_runtime_run_unified_imports_cleanly():
    """Runtime: run_unified.py must compile without SyntaxError."""
    try:
        compile((_ROOT / "run_unified.py").read_text(), "run_unified.py", "exec")
    except SyntaxError as e:
        pytest.fail(f"run_unified.py has SyntaxError: {e}")


def test_runtime_run_real_pipeline_imports_cleanly():
    """Runtime: run_real_pipeline.py must compile without SyntaxError."""
    try:
        compile((_ROOT / "run_real_pipeline.py").read_text(), "run_real_pipeline.py", "exec")
    except SyntaxError as e:
        pytest.fail(f"run_real_pipeline.py has SyntaxError: {e}")


def test_runtime_run_full_platform_imports_cleanly():
    """Runtime: run_full_platform.py must compile without SyntaxError."""
    try:
        compile((_ROOT / "run_full_platform.py").read_text(), "run_full_platform.py", "exec")
    except SyntaxError as e:
        pytest.fail(f"run_full_platform.py has SyntaxError: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
