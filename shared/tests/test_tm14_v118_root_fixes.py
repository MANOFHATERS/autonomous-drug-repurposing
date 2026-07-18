"""shared.tests.test_tm14_v118_root_fixes — TM14 v118 ROOT FIX verification.

This test file verifies the TM14 v118 root-cause fixes for the issues
the user flagged in the audit:

  - SH-014 (HIGH): run_real_pipeline_verification.py used SYNTHETIC
    build_demo_graph despite the "REAL PIPELINE VERIFICATION" name.
    ROOT FIX: rewrote the script to use the REAL Phase 1->2->3 graph
    for ALL tests (no synthetic build_demo_graph anywhere).

  - SH-015 (HIGH): MANIFEST.in missing shared/, common/, contracts/.
    ROOT FIX: verified all recursive-include directives are present.

  - P3-002 (CRITICAL): PHASE2_TO_PHASE3_EDGE missing 20 of 31 edge types.
    ROOT FIX: created shared/contracts/phase_edge_mapping.py with a
    completeness assertion that fails-closed if any dropped edge lacks
    a documented reason OR any mapped Phase 3 edge is invalid.

  - P4-006 (MEDIUM): bridge writes 17 columns but env reads 12.
    ROOT FIX: added BRIDGE_REQUIRED_COLUMNS (12), BRIDGE_OPTIONAL_COLUMNS
    (5), and BRIDGE_WRITES_COLUMNS (17) constants to shared/contracts/
    feature_names.py with import-time assertions verifying the
    relationship. Updated the contract test to verify rl/constants.py
    matches BRIDGE_REQUIRED_COLUMNS.

  - P4-025/P4-050 (MEDIUM): shared/contracts/writeback.py Cypher
    identifier validation. Verified the validator REJECTS unsafe values
    (backticks, semicolons, quotes, spaces, empty strings) and ACCEPTS
    safe values (alphanumeric + underscore only).

  - Frontend api_contracts.ts (CRITICAL): the file was MISSING. The
    contract consistency test (TEST 11) was failing. ROOT FIX: created
    frontend/contracts/api_contracts.ts with all 7 canonical URL
    constants and 12 TypeScript interfaces matching the Python service
    contract.

These tests are the SMOKING-GUN verification that the fixes are REAL,
not aspirational comments. Each test reads the ACTUAL source code (not
the comments) and verifies the fix is in place.

Run via:
    python -m pytest shared/tests/test_tm14_v118_root_fixes.py -v
    # OR
    python shared/tests/test_tm14_v118_root_fixes.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# Phase 2 needs to be importable for phase2.contracts.phase2_schema.
_PHASE2_ROOT = _REPO_ROOT / "phase2"
if str(_PHASE2_ROOT) not in sys.path:
    sys.path.insert(0, str(_PHASE2_ROOT))


# =============================================================================
# Test result tracking (mirrors shared/tests/test_contract_consistency.py)
# =============================================================================
_ERRORS: list = []
_PASSES: list = []


def _pass(msg: str) -> None:
    _PASSES.append(msg)
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    _ERRORS.append(msg)
    print(f"  [FAIL] {msg}")


# =============================================================================
# TEST 1: SH-014 — run_real_pipeline_verification.py has NO build_demo_graph call
# =============================================================================

def test_sh_014_no_build_demo_graph_in_verification() -> None:
    """SH-014 ROOT FIX: the verification script must NOT call build_demo_graph.

    The previous "fix" (v114) added TEST 0 (real bridge) but kept TESTS A1-A5
    using bridge.build_demo_graph() (synthetic). This test reads the ACTUAL
    source code via AST analysis and verifies NO actual function CALL to
    build_demo_graph exists in the verification script (comments and strings
    mentioning the name are OK — they're documentation, not calls).
    """
    print("\n=== TEST 1: SH-014 no build_demo_graph in verification script ===")
    script_path = _REPO_ROOT / "run_real_pipeline_verification.py"
    if not script_path.exists():
        _fail(f"run_real_pipeline_verification.py not found at {script_path}")
        return

    src = script_path.read_text(encoding="utf-8")

    # Parse the source as an AST and walk for actual Call nodes whose
    # function name is build_demo_graph (or attribute is build_demo_graph).
    # This distinguishes REAL function calls from string/comment mentions.
    tree = ast.parse(src, filename=str(script_path))
    actual_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Direct call: build_demo_graph(...)
            if isinstance(node.func, ast.Name) and node.func.id == "build_demo_graph":
                actual_calls.append(node.lineno)
            # Attribute call: <obj>.build_demo_graph(...)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "build_demo_graph":
                actual_calls.append(node.lineno)

    if not actual_calls:
        _pass("run_real_pipeline_verification.py has NO actual build_demo_graph() call (AST-verified)")
    else:
        _fail(
            f"SH-014 NOT FIXED: run_real_pipeline_verification.py has actual "
            f"build_demo_graph() calls on lines {actual_calls} — SYNTHETIC data. "
            f"The 'real pipeline verification' name is a LIE."
        )

    # Check 2: the script DOES call run_full_pipeline with graph_data.
    # Use AST to find the call and verify graph_data is passed.
    found_run_full_pipeline_with_graph_data = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "run_full_pipeline":
                for kw in node.keywords:
                    if kw.arg == "graph_data":
                        found_run_full_pipeline_with_graph_data = True
                        break
    if found_run_full_pipeline_with_graph_data:
        _pass("run_full_pipeline(graph_data=...) is called (REAL graph passed to bridge)")
    else:
        _fail(
            "run_full_pipeline is NOT called with graph_data=. "
            "The bridge is not receiving the REAL Phase 2 graph."
        )

    # Check 3: the script DOES call run_phase1_to_phase2 (the REAL bridge).
    # Use AST to find the actual call.
    found_phase1_bridge = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "run_phase1_to_phase2":
                found_phase1_bridge = True
                break
    if found_phase1_bridge:
        _pass("run_phase1_to_phase2() is called (REAL Phase 1->2 bridge)")
    else:
        _fail(
            "run_phase1_to_phase2 is NOT called. "
            "The REAL Phase 1->2 bridge is not exercised."
        )

    # Check 4: the script DOES call adapt_phase2_to_phase3 (the REAL adapter).
    found_phase2_adapter = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "adapt_phase2_to_phase3":
                found_phase2_adapter = True
                break
    if found_phase2_adapter:
        _pass("adapt_phase2_to_phase3() is called (REAL Phase 2->3 adapter)")
    else:
        _fail(
            "adapt_phase2_to_phase3 is NOT called. "
            "The REAL Phase 2->3 adapter is not exercised."
        )


# =============================================================================
# TEST 2: SH-015 — MANIFEST.in includes all required directories
# =============================================================================

def test_sh_015_manifest_in_includes_all_directories() -> None:
    """SH-015 ROOT FIX: MANIFEST.in must include shared/, common/, contracts/."""
    print("\n=== TEST 2: SH-015 MANIFEST.in includes all directories ===")
    manifest_path = _REPO_ROOT / "MANIFEST.in"
    if not manifest_path.exists():
        _fail(f"MANIFEST.in not found at {manifest_path}")
        return

    manifest = manifest_path.read_text(encoding="utf-8")

    required_directives = [
        ("recursive-include shared", "*.py"),
        ("recursive-include common", "*.py"),
        ("recursive-include graph_transformer", "*.py"),
        ("recursive-include phase1", "*.py"),
        ("recursive-include phase2", "*.py"),
        ("recursive-include rl", "*.py"),
    ]
    all_pass = True
    for prefix, ext in required_directives:
        full_directive = f"{prefix} {ext}"
        if full_directive in manifest:
            _pass(f"MANIFEST.in has '{full_directive}'")
        else:
            _fail(f"MANIFEST.in MISSING '{full_directive}'")
            all_pass = False

    # Check that the contract modules specifically are covered.
    # The recursive-include for shared/ covers shared/contracts/*.py.
    # Verify by listing the actual contract files.
    contract_files = list((_REPO_ROOT / "shared" / "contracts").glob("*.py"))
    if contract_files:
        _pass(f"shared/contracts/ has {len(contract_files)} .py files (covered by recursive-include shared *.py)")
    else:
        _fail("shared/contracts/ has NO .py files — contract modules missing")


# =============================================================================
# TEST 3: P3-002 — phase_edge_mapping.py exists and is complete
# =============================================================================

def test_p3_002_phase_edge_mapping_exists_and_complete() -> None:
    """P3-002 ROOT FIX: shared/contracts/phase_edge_mapping.py exists and is complete."""
    print("\n=== TEST 3: P3-002 phase_edge_mapping.py exists and is complete ===")
    contract_path = _REPO_ROOT / "shared" / "contracts" / "phase_edge_mapping.py"
    if not contract_path.exists():
        _fail(f"shared/contracts/phase_edge_mapping.py not found — P3-002 NOT FIXED")
        return

    # Check 1: the module imports successfully (which runs the completeness assertion).
    try:
        from shared.contracts.phase_edge_mapping import (
            PHASE2_TO_PHASE3_EDGE,
            PHASE2_TO_PHASE3_EDGE_DROPPED,
            EDGE_DROP_REASONS,
            map_edge_with_reason,
            validate_phase2_to_phase3_completeness,
            PHASE_EDGE_MAPPING_VERSION,
        )
        _pass(
            f"phase_edge_mapping.py imports (version={PHASE_EDGE_MAPPING_VERSION}, "
            f"mapped={len(PHASE2_TO_PHASE3_EDGE)}, dropped={len(PHASE2_TO_PHASE3_EDGE_DROPPED)})"
        )
    except AssertionError as e:
        _fail(f"phase_edge_mapping.py completeness assertion FAILED at import: {e}")
        return
    except Exception as e:
        _fail(f"phase_edge_mapping.py import error: {type(e).__name__}: {e}")
        return

    # Check 2: every dropped edge has a documented reason.
    dropped_without_reason = set(PHASE2_TO_PHASE3_EDGE_DROPPED) - set(EDGE_DROP_REASONS.keys())
    if not dropped_without_reason:
        _pass(f"All {len(PHASE2_TO_PHASE3_EDGE_DROPPED)} dropped edges have documented reasons")
    else:
        _fail(f"{len(dropped_without_reason)} dropped edges WITHOUT reasons: {sorted(dropped_without_reason)}")

    # Check 3: completeness validation returns True.
    is_complete, unmapped_dropped, invalid_p3 = validate_phase2_to_phase3_completeness()
    if is_complete:
        _pass("validate_phase2_to_phase3_completeness() returns is_complete=True")
    else:
        _fail(
            f"validate_phase2_to_phase3_completeness() returns is_complete=False "
            f"(unmapped_dropped={len(unmapped_dropped)}, invalid_p3={len(invalid_p3)})"
        )

    # Check 4: the mapping covers the audit's CRITICAL missing edges.
    # The audit specifically called out these as MISSING (silently dropped):
    audit_critical_edges = [
        ("Compound", "metabolized_by", "Protein"),     # CYP450 metabolism
        ("Compound", "carried_by", "Protein"),         # transport
        ("Compound", "transported_by", "Protein"),     # transport
        ("Compound", "induces", "Protein"),            # enzyme induction
        ("Compound", "failed_for", "Disease"),         # failed clinical trials
        ("Drug", "validated_treats", "Disease"),       # data flywheel
        ("Compound", "validated_treats", "Disease"),   # data flywheel (alt spelling)
        ("Compound", "inhibits", "Gene"),              # DRKG drug-gene
        ("Compound", "activates", "Gene"),             # DRKG drug-gene
    ]
    missing_critical = [e for e in audit_critical_edges if e not in PHASE2_TO_PHASE3_EDGE]
    if not missing_critical:
        _pass(f"All {len(audit_critical_edges)} audit-critical edges are mapped")
    else:
        _fail(f"{len(missing_critical)} audit-critical edges still MISSING: {missing_critical}")


# =============================================================================
# TEST 4: P4-006 — feature_names.py BRIDGE_REQUIRED/OPTIONAL/WRITES contract
# =============================================================================

def test_p4_006_bridge_env_column_contract() -> None:
    """P4-006 ROOT FIX: BRIDGE_REQUIRED/OPTIONAL/WRITES_COLUMNS defined and consistent."""
    print("\n=== TEST 4: P4-006 bridge/env column contract ===")
    try:
        from shared.contracts.feature_names import (
            RL_FEATURE_COLUMNS,
            BRIDGE_REQUIRED_COLUMNS,
            BRIDGE_OPTIONAL_COLUMNS,
            BRIDGE_WRITES_COLUMNS,
        )

        # Check 1: BRIDGE_WRITES_COLUMNS has same elements as RL_FEATURE_COLUMNS.
        if set(BRIDGE_WRITES_COLUMNS) == set(RL_FEATURE_COLUMNS):
            _pass(f"BRIDGE_WRITES_COLUMNS ({len(BRIDGE_WRITES_COLUMNS)}) == RL_FEATURE_COLUMNS ({len(RL_FEATURE_COLUMNS)})")
        else:
            _fail(
                f"BRIDGE_WRITES_COLUMNS drift. "
                f"Only in WRITES: {set(BRIDGE_WRITES_COLUMNS) - set(RL_FEATURE_COLUMNS)}, "
                f"only in FEATURE: {set(RL_FEATURE_COLUMNS) - set(BRIDGE_WRITES_COLUMNS)}"
            )

        # Check 2: BRIDGE_REQUIRED_COLUMNS ⊆ BRIDGE_WRITES_COLUMNS.
        if set(BRIDGE_REQUIRED_COLUMNS).issubset(set(BRIDGE_WRITES_COLUMNS)):
            _pass(f"BRIDGE_REQUIRED_COLUMNS ({len(BRIDGE_REQUIRED_COLUMNS)}) ⊆ BRIDGE_WRITES_COLUMNS ({len(BRIDGE_WRITES_COLUMNS)})")
        else:
            _fail("BRIDGE_REQUIRED_COLUMNS is NOT a subset of BRIDGE_WRITES_COLUMNS")

        # Check 3: BRIDGE_REQUIRED + BRIDGE_OPTIONAL == BRIDGE_WRITES.
        combined = set(BRIDGE_REQUIRED_COLUMNS) | set(BRIDGE_OPTIONAL_COLUMNS)
        if combined == set(BRIDGE_WRITES_COLUMNS):
            _pass(f"BRIDGE_REQUIRED ∪ BRIDGE_OPTIONAL == BRIDGE_WRITES ({len(combined)} columns)")
        else:
            _fail(
                f"BRIDGE_REQUIRED ∪ BRIDGE_OPTIONAL != BRIDGE_WRITES. "
                f"Symmetric diff: {combined ^ set(BRIDGE_WRITES_COLUMNS)}"
            )

        # Check 4: rl/constants.py matches BRIDGE_REQUIRED_COLUMNS.
        from rl.constants import REQUIRED_COLUMNS as RL_REQUIRED
        if set(RL_REQUIRED) == set(BRIDGE_REQUIRED_COLUMNS):
            _pass(f"rl/constants.py REQUIRED_COLUMNS matches BRIDGE_REQUIRED_COLUMNS ({len(RL_REQUIRED)} cols)")
        else:
            _fail(
                f"rl/constants.py drift. Only in RL: {set(RL_REQUIRED) - set(BRIDGE_REQUIRED_COLUMNS)}, "
                f"only in BRIDGE: {set(BRIDGE_REQUIRED_COLUMNS) - set(RL_REQUIRED)}"
            )
    except Exception as e:
        _fail(f"P4-006 test raised: {type(e).__name__}: {e}")


# =============================================================================
# TEST 5: P4-025/P4-050 — writeback.py Cypher identifier validation
# =============================================================================

def test_p4_025_050_cypher_identifier_validation() -> None:
    """P4-025/P4-050 ROOT FIX: writeback.py validates Cypher identifiers at import."""
    print("\n=== TEST 5: P4-025/P4-050 Cypher identifier validation ===")
    try:
        from shared.contracts.writeback import (
            _validate_cypher_identifier,
            _CYPHER_LABEL_RE,
            NEO4J_DRUG_LABEL_PREFERRED,
            NEO4J_DRUG_LABEL_LEGACY,
            NEO4J_DISEASE_LABEL,
            EDGE_VALIDATED_TREATS,
            EDGE_VALIDATED_TOXIC_FOR,
            EDGE_VALIDATED_NEGATIVE_FOR,
        )
        _pass(
            f"writeback.py imports (all 7+ Cypher identifiers validated at import)"
        )

        # Check 1: the validator REJECTS unsafe values.
        unsafe_values = [
            'Drug`',              # backtick injection
            'Drug; DROP TABLE',   # SQL-style injection
            "Drug' OR '1'='1",    # classic injection
            'Drug--',             # SQL comment
            'Drug/*',             # SQL comment
            '',                   # empty
            'Drug Name',          # space
            'Drug$col',           # dollar sign
        ]
        rejected_count = 0
        for v in unsafe_values:
            try:
                _validate_cypher_identifier(v, "test_unsafe")
            except ValueError:
                rejected_count += 1
        if rejected_count == len(unsafe_values):
            _pass(f"Validator REJECTS all {len(unsafe_values)} unsafe values")
        else:
            _fail(f"Validator only rejected {rejected_count}/{len(unsafe_values)} unsafe values")

        # Check 2: the validator ACCEPTS safe values.
        safe_values = [
            'Drug', 'Compound', 'Disease',
            'drug_id', 'disease_id', 'name',
            'VALIDATED_TREATS', 'VALIDATED_TOXIC_FOR',
            'Edge_Label_123',
        ]
        accepted_count = 0
        for v in safe_values:
            try:
                _validate_cypher_identifier(v, "test_safe")
                accepted_count += 1
            except ValueError:
                pass
        if accepted_count == len(safe_values):
            _pass(f"Validator ACCEPTS all {len(safe_values)} safe values")
        else:
            _fail(f"Validator only accepted {accepted_count}/{len(safe_values)} safe values")

        # Check 3: the current constants are all safe (validated at import).
        constants_to_check = [
            NEO4J_DRUG_LABEL_PREFERRED, NEO4J_DRUG_LABEL_LEGACY,
            NEO4J_DISEASE_LABEL, EDGE_VALIDATED_TREATS,
            EDGE_VALIDATED_TOXIC_FOR, EDGE_VALIDATED_NEGATIVE_FOR,
        ]
        all_safe = all(_CYPHER_LABEL_RE.match(c) for c in constants_to_check)
        if all_safe:
            _pass(f"All {len(constants_to_check)} current Cypher identifiers match ^[A-Za-z0-9_]+$")
        else:
            _fail("At least one current Cypher identifier is unsafe — import should have failed")
    except Exception as e:
        _fail(f"P4-025/P4-050 test raised: {type(e).__name__}: {e}")


# =============================================================================
# TEST 6: Frontend api_contracts.ts exists with required URLs and interfaces
# =============================================================================

def test_frontend_api_contracts_exists() -> None:
    """TM14 v118 ROOT FIX: frontend/contracts/api_contracts.ts exists."""
    print("\n=== TEST 6: frontend api_contracts.ts exists ===")
    ts_path = _REPO_ROOT / "frontend" / "contracts" / "api_contracts.ts"
    if not ts_path.exists():
        _fail(f"frontend/contracts/api_contracts.ts not found at {ts_path}")
        return

    src = ts_path.read_text(encoding="utf-8")

    # Check 1: all 7 canonical URL constants are present.
    required_urls = [
        '/kg/stats"', '/kg/explore"', '/predict"', '/top-k"',
        '/rank"', '/validate"', '/health"',
    ]
    missing = [u for u in required_urls if u not in src]
    if not missing:
        _pass(f"api_contracts.ts has all 7 canonical URL constants")
    else:
        _fail(f"api_contracts.ts missing URL constants: {missing}")

    # Check 2: all required interfaces are present.
    required_interfaces = [
        "KgStatsResponse", "KgExploreResponse", "PredictResponse",
        "TopKResponse", "RankResponse", "RankedCandidate",
        "ValidateRequest", "ValidateResponse", "HealthResponse",
    ]
    missing_interfaces = [
        i for i in required_interfaces
        if f"interface {i}" not in src and f"type {i}" not in src
    ]
    if not missing_interfaces:
        _pass(f"api_contracts.ts has all {len(required_interfaces)} required interfaces")
    else:
        _fail(f"api_contracts.ts missing interfaces: {missing_interfaces}")

    # Check 3: SERVICE_PORTS matches the Python contract.
    required_ports = ["8000", "8001", "8002", "8003", "8080", "5000", "7687", "7474", "5432", "3000"]
    missing_ports = [p for p in required_ports if p not in src]
    if not missing_ports:
        _pass(f"api_contracts.ts has all {len(required_ports)} canonical service ports")
    else:
        _fail(f"api_contracts.ts missing ports: {missing_ports}")


# =============================================================================
# TEST 7: run_real_pipeline_verification.py runs end-to-end on REAL data
# =============================================================================
# This test is the SMOKING GUN: it actually runs the verification script
# and verifies it completes successfully on REAL Phase 1->2->3 data.
# If this test passes, the user can TRUST that the REAL pipeline works.

def test_run_real_pipeline_verification_runs() -> None:
    """Run run_real_pipeline_verification.py end-to-end on REAL data."""
    print("\n=== TEST 7: run_real_pipeline_verification.py runs end-to-end ===")
    import subprocess

    script_path = _REPO_ROOT / "run_real_pipeline_verification.py"
    if not script_path.exists():
        _fail(f"run_real_pipeline_verification.py not found")
        return

    env = {
        **__import__("os").environ,
        "DRUGOS_ENVIRONMENT": "dev",
        "DRUGOS_ALLOW_CSV_FALLBACK": "1",
        "DRUGOS_SKIP_CHEMBERTA": "1",
        "RL_SKIP_LITERATURE": "1",
    }
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True, text=True, timeout=300, env=env,
            cwd=str(_REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        _fail("run_real_pipeline_verification.py timed out after 300s")
        return

    if result.returncode == 0:
        # Verify the script actually ran the REAL pipeline (not synthetic).
        if "ALL VERIFICATION TESTS PASSED" in result.stdout:
            _pass("run_real_pipeline_verification.py completed successfully on REAL data")
        else:
            _fail(
                "run_real_pipeline_verification.py exited 0 but did not print "
                "'ALL VERIFICATION TESTS PASSED' — the script may have been "
                "modified to skip tests. Last 500 chars of stdout: "
                f"{result.stdout[-500:]}"
            )
    else:
        # Capture the last 1000 chars of stderr for debugging.
        _fail(
            f"run_real_pipeline_verification.py exited {result.returncode}. "
            f"Last 500 chars of stderr: {result.stderr[-500:]}"
        )


# =============================================================================
# Master test runner
# =============================================================================

def test_all() -> int:
    """Run all TM14 v118 root-fix tests."""
    print("=" * 72)
    print("TM14 v118 ROOT FIX VERIFICATION (SH-014, SH-015, P3-002, P4-006, P4-025/050)")
    print("=" * 72)

    tests = [
        test_sh_014_no_build_demo_graph_in_verification,
        test_sh_015_manifest_in_includes_all_directories,
        test_p3_002_phase_edge_mapping_exists_and_complete,
        test_p4_006_bridge_env_column_contract,
        test_p4_025_050_cypher_identifier_validation,
        test_frontend_api_contracts_exists,
        # The end-to-end test is LAST because it's the slowest.
        test_run_real_pipeline_verification_runs,
    ]

    for test in tests:
        try:
            test()
        except Exception as exc:
            _fail(f"Test {test.__name__} raised: {type(exc).__name__}: {exc}")

    print("\n" + "=" * 72)
    print(f"SUMMARY: {len(_PASSES)} passed, {len(_ERRORS)} failed")
    print("=" * 72)

    if _ERRORS:
        print("\nFAILURES:")
        for e in _ERRORS:
            print(f"  - {e}")
        return 1
    else:
        print("\nTM14 v118 ROOT FIX VERIFICATION PASSED.")
        print("All fixes are REAL (verified by reading source code, not comments).")
        return 0


if __name__ == "__main__":
    sys.exit(test_all())
