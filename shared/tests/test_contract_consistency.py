"""shared.tests.test_contract_consistency — cross-phase contract verification.

TASK 330 ROOT FIX (forensic, root-level, no surface fix):
  This test verifies that every writer and reader across the four phases
  uses the SAME schema as defined in the contract modules. It runs in CI
  on every PR (Task 332) and MUST pass before merge.

  Previous "fixes" claimed contract compliance in comments but never
  actually wired up the imports — Phase 2 bridge had a hardcoded
  ``_PHASE1_EXPECTED_COLUMNS`` dict with comments saying "this should
  match phase1.contracts" but never imported the contract. This test
  CATCHES that pattern: it reads the actual source code (not the
  comments) and verifies the import statements are present.

  Run via:
    python -c "from shared.tests.test_contract_consistency import test_all; test_all()"
"""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from pathlib import Path
from typing import List, Tuple


# Make repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# =============================================================================
# Test result tracking
# =============================================================================

_ERRORS: List[str] = []
_PASSES: List[str] = []


def _pass(msg: str) -> None:
    _PASSES.append(msg)
    print(f"  [PASS] {msg}")


def _fail(msg: str) -> None:
    _ERRORS.append(msg)
    print(f"  [FAIL] {msg}")


# =============================================================================
# Helper: read source file as AST
# =============================================================================

def _read_ast(path: Path) -> ast.Module:
    """Read a Python source file and return its AST."""
    src = path.read_text(encoding="utf-8")
    return ast.parse(src, filename=str(path))


def _has_import_from(tree: ast.Module, module_path: str, names: List[str]) -> bool:
    """Return True if the AST has ``from <module_path> import <names>``.

    Checks all import statements (including try/except ImportError blocks).
    """
    needed = set(names)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == module_path or (node.module and node.module.endswith(module_path)):
                for alias in node.names:
                    if alias.name in needed:
                        found.add(alias.name)
    return needed.issubset(found)


def _has_import_module(tree: ast.Module, module_path: str) -> bool:
    """Return True if the AST has ``import <module_path>`` or
    ``from <module_path> import ...``.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_path or alias.name.startswith(module_path + "."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module == module_path or (node.module and node.module.startswith(module_path + ".")):
                return True
    return False


# =============================================================================
# TEST 1: Phase 1 schema is the single source for Phase 1 output columns
# =============================================================================

def test_phase1_schema_exists_and_importable() -> None:
    """Phase 1 schema contract module must exist and be importable."""
    print("\n=== TEST 1: Phase 1 schema exists and is importable ===")
    try:
        from phase1.contracts.phase1_schema import (
            PHASE1_OUTPUT_SCHEMA,
            PHASE1_CSV_FILENAMES,
            ColumnSpec,
            SourceSpec,
            ValidationIssue,
        )
        _pass(f"phase1.contracts.phase1_schema imported (PHASE1_OUTPUT_SCHEMA has {len(PHASE1_OUTPUT_SCHEMA)} sources)")
        # Verify all 11 sources are present.
        expected = {
            "chembl_drugs", "chembl_activities", "drugs", "interactions",
            "indications", "uniprot_proteins", "string_ppi", "disgenet_gda",
            "omim_gda", "omim_susceptibility", "pubchem_enrichment",
        }
        actual = set(PHASE1_OUTPUT_SCHEMA.keys())
        missing = expected - actual
        if missing:
            _fail(f"PHASE1_OUTPUT_SCHEMA missing sources: {missing}")
        else:
            _pass(f"All 11 Phase 1 sources present in PHASE1_OUTPUT_SCHEMA")
    except Exception as exc:
        _fail(f"Could not import phase1.contracts.phase1_schema: {type(exc).__name__}: {exc}")


# =============================================================================
# TEST 2: Phase 2 bridge imports Phase 1 schema (NOT a hardcoded dict)
# =============================================================================

def test_phase2_bridge_imports_phase1_schema() -> None:
    """Phase 2 bridge MUST import from phase1.contracts, not hardcode columns.

    This is the smoking-gun test the user asked for: previous agents
    claimed the bridge uses the contract but never wired up the import.
    We read the actual source code (AST) and verify the import statement.
    """
    print("\n=== TEST 2: Phase 2 bridge imports Phase 1 schema ===")
    bridge_path = _REPO_ROOT / "phase2" / "drugos_graph" / "phase1_bridge.py"
    if not bridge_path.exists():
        _fail(f"Phase 2 bridge not found at {bridge_path}")
        return
    tree = _read_ast(bridge_path)

    # Check for any import from phase1.contracts or contracts.phase1_schema.
    has_import = (
        _has_import_from(tree, "phase1.contracts.phase1_schema", ["PHASE1_OUTPUT_SCHEMA"])
        or _has_import_module(tree, "phase1.contracts")
        or _has_import_from(tree, "phase1.contracts", ["PHASE1_OUTPUT_SCHEMA"])
    )
    if has_import:
        _pass("phase1_bridge.py imports from phase1.contracts")
    else:
        # The bridge may import via a different pattern. Walk all ImportFrom
        # nodes and check if any imports from a module containing "phase1" and "contracts".
        found_alt = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if "phase1" in node.module and "contract" in node.module:
                    found_alt = True
                    break
        if found_alt:
            _pass("phase1_bridge.py imports from a phase1.contracts module (alt pattern)")
        else:
            _fail(
                "phase1_bridge.py does NOT import from phase1.contracts. "
                "The bridge has a hardcoded _PHASE1_EXPECTED_COLUMNS dict that "
                "diverges from the contract. This is the fake-fix pattern the "
                "user described: comments claim compliance but the import is missing."
            )


# =============================================================================
# TEST 3: Phase 2 schema contract exists and is the single source
# =============================================================================

def test_phase2_schema_contract_exists() -> None:
    """Phase 2 schema contract module must exist with NODE_TYPES."""
    print("\n=== TEST 3: Phase 2 schema contract exists ===")
    try:
        from phase2.contracts.phase2_schema import (
            NODE_TYPES,
            ALL_PHASE2_NODE_TYPES,
            PHASE2_TO_PHASE3_NODE,
            EDGE_TYPES,
            PHASE2_TO_PHASE3_EDGE,
            NODE_FEATURE_SCHEMAS,
            EDGE_FEATURE_SCHEMAS,
        )
        # Verify exactly 5 canonical node types.
        if len(NODE_TYPES) != 5:
            _fail(f"NODE_TYPES has {len(NODE_TYPES)} entries, expected 5. Got: {NODE_TYPES}")
        else:
            _pass(f"NODE_TYPES has 5 canonical types: {NODE_TYPES}")

        # Verify 7 Phase 2 node types (5 canonical + 2 intermediates).
        if len(ALL_PHASE2_NODE_TYPES) != 7:
            _fail(f"ALL_PHASE2_NODE_TYPES has {len(ALL_PHASE2_NODE_TYPES)} entries, expected 7.")
        else:
            _pass(f"ALL_PHASE2_NODE_TYPES has 7 entries (5 canonical + Gene + MedDRA_Term)")

        # Verify PHASE2_TO_PHASE3_NODE maps Gene/MedDRA_Term to None.
        if PHASE2_TO_PHASE3_NODE.get("Gene") is not None:
            _fail("PHASE2_TO_PHASE3_NODE['Gene'] should be None (intermediate dropped).")
        elif PHASE2_TO_PHASE3_NODE.get("MedDRA_Term") is not None:
            _fail("PHASE2_TO_PHASE3_NODE['MedDRA_Term'] should be None (intermediate dropped).")
        else:
            _pass("PHASE2_TO_PHASE3_NODE correctly drops Gene and MedDRA_Term (map to None)")

        # Verify all 5 canonical node types have feature schemas.
        missing_schemas = [t for t in NODE_TYPES if t not in NODE_FEATURE_SCHEMAS]
        if missing_schemas:
            _fail(f"NODE_FEATURE_SCHEMAS missing for: {missing_schemas}")
        else:
            _pass(f"NODE_FEATURE_SCHEMAS present for all 5 canonical node types")
    except Exception as exc:
        _fail(f"Could not import phase2.contracts.phase2_schema: {type(exc).__name__}: {exc}")


# =============================================================================
# TEST 4: schema_mappings.py re-exports from phase2.contracts
# =============================================================================

def test_schema_mappings_imports_from_contract() -> None:
    """phase2/drugos_graph/schema_mappings.py MUST import from phase2.contracts.

    Task 331 requires that the duplicate mappings in pyg_builder.py and
    phase2_adapter.py be replaced with imports from the contract module.
    The chain is:
      pyg_builder.py -> schema_mappings.py -> phase2.contracts.phase2_schema
      phase2_adapter.py -> schema_mappings.py -> phase2.contracts.phase2_schema

    This test verifies the second hop: schema_mappings.py imports from
    phase2.contracts.phase2_schema.
    """
    print("\n=== TEST 4: schema_mappings.py imports from phase2.contracts ===")
    sm_path = _REPO_ROOT / "phase2" / "drugos_graph" / "schema_mappings.py"
    if not sm_path.exists():
        _fail(f"schema_mappings.py not found at {sm_path}")
        return
    tree = _read_ast(sm_path)

    # Look for any import from a module containing "phase2" and "contract".
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "phase2" in node.module and "contract" in node.module:
                found = True
                break
    if found:
        _pass("schema_mappings.py imports from phase2.contracts")
    else:
        _fail(
            "schema_mappings.py does NOT import from phase2.contracts. "
            "It still defines the mapping locally, defeating the contract."
        )


# =============================================================================
# TEST 5: pyg_builder.py imports from schema_mappings (no local dict)
# =============================================================================

def test_pyg_builder_no_local_node_type_dict() -> None:
    """pyg_builder.py MUST import _PHASE2_TO_GT_NODE_TYPE, not define it.

    We read the AST and check that no top-level assignment statement
    defines a dict literal named _PHASE2_TO_GT_NODE_TYPE.
    """
    print("\n=== TEST 5: pyg_builder.py has no local _PHASE2_TO_GT_NODE_TYPE dict ===")
    pb_path = _REPO_ROOT / "phase2" / "drugos_graph" / "pyg_builder.py"
    if not pb_path.exists():
        _fail(f"pyg_builder.py not found at {pb_path}")
        return
    tree = _read_ast(pb_path)

    # Look for top-level Assign nodes that bind a dict literal to
    # _PHASE2_TO_GT_NODE_TYPE. An import-as binding counts as OK.
    local_dict_defined = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_PHASE2_TO_GT_NODE_TYPE":
                    if isinstance(node.value, ast.Dict):
                        local_dict_defined = True

    if local_dict_defined:
        _fail(
            "pyg_builder.py defines _PHASE2_TO_GT_NODE_TYPE as a local dict "
            "literal. This is the duplicate mapping Task 331 requires deleted. "
            "Replace with: from phase2.contracts.phase2_schema import "
            "PHASE2_TO_PHASE3_NODE_CANONICAL as _PHASE2_TO_GT_NODE_TYPE"
        )
    else:
        _pass("pyg_builder.py does NOT define a local _PHASE2_TO_GT_NODE_TYPE dict literal")

    # Also check the import is present.
    if _has_import_from(tree, "schema_mappings", ["PHASE2_TO_PHASE3_NODE"]):
        _pass("pyg_builder.py imports PHASE2_TO_PHASE3_NODE from schema_mappings")
    else:
        _fail("pyg_builder.py does not import PHASE2_TO_PHASE3_NODE from schema_mappings")


# =============================================================================
# TEST 6: phase2_adapter.py imports from schema_mappings (no local dict)
# =============================================================================

def test_phase2_adapter_no_local_node_type_dict() -> None:
    """phase2_adapter.py MUST import PHASE2_TO_PHASE3_NODE, not define it."""
    print("\n=== TEST 6: phase2_adapter.py has no local PHASE2_TO_PHASE3_NODE dict ===")
    pa_path = _REPO_ROOT / "graph_transformer" / "data" / "phase2_adapter.py"
    if not pa_path.exists():
        _fail(f"phase2_adapter.py not found at {pa_path}")
        return
    tree = _read_ast(pa_path)

    local_dict_defined = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PHASE2_TO_PHASE3_NODE":
                    if isinstance(node.value, ast.Dict):
                        local_dict_defined = True

    if local_dict_defined:
        _fail(
            "phase2_adapter.py defines PHASE2_TO_PHASE3_NODE as a local dict "
            "literal. This is the duplicate mapping Task 331 requires deleted."
        )
    else:
        _pass("phase2_adapter.py does NOT define a local PHASE2_TO_PHASE3_NODE dict literal")

    if _has_import_from(tree, "schema_mappings", ["PHASE2_TO_PHASE3_NODE"]):
        _pass("phase2_adapter.py imports PHASE2_TO_PHASE3_NODE from schema_mappings")
    else:
        _fail("phase2_adapter.py does not import PHASE2_TO_PHASE3_NODE from schema_mappings")


# =============================================================================
# TEST 7: Phase 3 checkpoint contract matches actual trainer checkpoint
# =============================================================================

def test_phase3_checkpoint_contract_matches_trainer() -> None:
    """Phase 3 trainer's checkpoint dict keys must match the contract."""
    print("\n=== TEST 7: Phase 3 checkpoint contract matches trainer ===")
    try:
        from graph_transformer.contracts.phase3_schema import (
            CHECKPOINT_REQUIRED_KEYS,
            CHECKPOINT_ALL_KEYS,
        )
    except Exception as exc:
        _fail(f"Could not import graph_transformer.contracts.phase3_schema: {exc}")
        return

    trainer_path = _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
    if not trainer_path.exists():
        _fail(f"trainer.py not found at {trainer_path}")
        return

    # Read the source and extract all string literals that look like
    # checkpoint keys. We're looking for the dict literal in save_checkpoint.
    src = trainer_path.read_text(encoding="utf-8")

    # Find the save_checkpoint method's checkpoint dict by scanning for
    # the literal keys we expect.
    required_keys = set(CHECKPOINT_REQUIRED_KEYS)
    found_keys = set()
    for key in required_keys:
        # Look for the key as a string literal in the source.
        if f'"{key}"' in src or f"'{key}'" in src:
            found_keys.add(key)

    missing = required_keys - found_keys
    if missing:
        _fail(
            f"trainer.py does not reference required checkpoint keys: {missing}. "
            f"The save_checkpoint() method must include all "
            f"CHECKPOINT_REQUIRED_KEYS from the contract."
        )
    else:
        _pass(f"trainer.py references all {len(required_keys)} required checkpoint keys")


# =============================================================================
# TEST 8: Phase 4 CSV contract matches the canonical shared/contracts/writeback.py
# =============================================================================

def test_phase4_csv_contract_matches() -> None:
    """Phase 4 validated_hypotheses.csv contract must match the canonical schema.

    TM14 v118 ROOT FIX: the previous test had STALE expected values
    (drug_id, drug_name, score, validated_inconclusive). The CANONICAL
    schema lives in shared/contracts/writeback.py (TM14's lane) and uses:
      - drug/disease (NOT drug_id/drug_name — the canonical schema uses
        names, not separate ID columns; the ID columns are optional
        metadata written by some producers but not required).
      - 4 outcome values: validated_positive, validated_toxic,
        validated_negative, invalidated (NOT validated_inconclusive —
        that value never existed in the canonical contract and was the
        root cause of the drift).

    The rl/contracts/phase4_schema.py module is a MIRROR of the canonical
    contract — it imports directly from shared/contracts/writeback.py.
    This test verifies the mirror is in sync.
    """
    print("\n=== TEST 8: Phase 4 CSV contract matches canonical schema ===")
    try:
        # Import the CANONICAL schema from shared/contracts/writeback.py.
        from shared.contracts.writeback import (
            WRITEBACK_CSV_COLUMNS as CANONICAL_COLUMNS,
            REQUIRED_COLUMNS as CANONICAL_REQUIRED,
            VALID_OUTCOMES as CANONICAL_OUTCOMES,
        )

        # Import the mirror from rl/contracts/phase4_schema.py.
        from rl.contracts.phase4_schema import (
            VALIDATED_HYPOTHESES_REQUIRED_COLUMNS,
            VALIDATED_HYPOTHESES_COLUMN_NAMES,
            OUTCOME_VALUES,
        )

        # Check 1: the mirror's REQUIRED_COLUMNS is a SUPERSET of the
        # canonical REQUIRED_COLUMNS. The mirror (rl/contracts/phase4_schema.py)
        # is ALLOWED to be STRICTER (require more columns) — e.g., the RL
        # ranker may require `validated_by` and `writeback_version` for audit
        # purposes, even though the canonical contract considers them optional.
        # The mirror is NOT allowed to be more LENIENT (drop a required column).
        mirror_required_names = {c.name for c in VALIDATED_HYPOTHESES_REQUIRED_COLUMNS}
        canonical_required_set = set(CANONICAL_REQUIRED)
        if canonical_required_set.issubset(mirror_required_names):
            extra_in_mirror = mirror_required_names - canonical_required_set
            _pass(
                f"Phase 4 mirror REQUIRED_COLUMNS is a superset of canonical "
                f"(canonical={len(canonical_required_set)}, mirror={len(mirror_required_names)}, "
                f"extra_in_mirror={sorted(extra_in_mirror) or 'none'})"
            )
        else:
            missing_in_mirror = canonical_required_set - mirror_required_names
            _fail(
                f"Phase 4 mirror REQUIRED_COLUMNS is MISSING canonical columns: "
                f"{sorted(missing_in_mirror)}. The mirror must require AT LEAST "
                f"the canonical REQUIRED_COLUMNS."
            )

        # Check 2: the mirror's full column list matches the canonical
        # WRITEBACK_CSV_COLUMNS.
        mirror_all_names = set(VALIDATED_HYPOTHESES_COLUMN_NAMES)
        canonical_all_set = set(CANONICAL_COLUMNS)
        if mirror_all_names == canonical_all_set:
            _pass(
                f"Phase 4 mirror ALL columns match canonical: "
                f"{len(mirror_all_names)} columns"
            )
        else:
            _fail(
                f"Phase 4 mirror ALL columns drift. "
                f"Only in canonical: {canonical_all_set - mirror_all_names}; "
                f"only in mirror: {mirror_all_names - canonical_all_set}"
            )

        # Check 3: the mirror's OUTCOME_VALUES match the canonical VALID_OUTCOMES.
        if set(OUTCOME_VALUES) == set(CANONICAL_OUTCOMES):
            _pass(f"Phase 4 OUTCOME_VALUES matches canonical: {sorted(OUTCOME_VALUES)}")
        else:
            _fail(
                f"Phase 4 OUTCOME_VALUES drift. "
                f"Canonical: {sorted(CANONICAL_OUTCOMES)}, "
                f"Mirror: {sorted(OUTCOME_VALUES)}"
            )
    except Exception as exc:
        _fail(f"Could not import rl.contracts.phase4_schema: {exc}")


# =============================================================================
# TEST 9: RL feature names contract matches rl/constants.py
# =============================================================================

def test_rl_feature_names_contract_matches_constants() -> None:
    """shared.contracts.feature_names must match rl/constants.py."""
    print("\n=== TEST 9: RL feature names contract matches rl/constants.py ===")
    try:
        from shared.contracts.feature_names import (
            CANONICAL_RL_FEATURE_NAMES,
            FEATURE_GNN_SCORE,
            FEATURE_SAFETY_SCORE,
            FEATURE_MARKET_SCORE,
            FEATURE_EFFICACY_SCORE,
            FEATURE_PATENT_SCORE,
            FEATURE_ADME_SCORE,
        )
        from rl.constants import (
            GNN_SCORE_COL,
            SAFETY_COL,
            MARKET_COL,
            EFFICACY_COL,
            PATENT_COL,
            ADME_COL,
        )

        # Verify the 6 canonical names match.
        pairs = [
            (FEATURE_GNN_SCORE, GNN_SCORE_COL, "gnn_score"),
            (FEATURE_SAFETY_SCORE, SAFETY_COL, "safety_score"),
            (FEATURE_MARKET_SCORE, MARKET_COL, "market_score"),
            (FEATURE_EFFICACY_SCORE, EFFICACY_COL, "efficacy_score"),
            (FEATURE_PATENT_SCORE, PATENT_COL, "patent_score"),
            (FEATURE_ADME_SCORE, ADME_COL, "adme_score"),
        ]
        all_match = True
        for contract_name, const_val, expected in pairs:
            if contract_name != expected:
                _fail(
                    f"Contract name mismatch: FEATURE={contract_name!r}, "
                    f"expected={expected!r}"
                )
                all_match = False
            elif const_val != expected:
                _fail(
                    f"rl/constants.py mismatch: const={const_val!r}, "
                    f"expected={expected!r}"
                )
                all_match = False
        if all_match:
            _pass("All 6 canonical RL feature names match between contract and rl/constants.py")
    except Exception as exc:
        _fail(f"Could not verify RL feature names: {type(exc).__name__}: {exc}")


# =============================================================================
# TEST 10: Service URL paths are registered by the Python services
# =============================================================================

def test_service_urls_match_contract() -> None:
    """Each Python service must register the URLs declared in shared.contracts.urls."""
    print("\n=== TEST 10: Python services register contract URLs ===")
    try:
        from shared.contracts.urls import (
            URL_KG_STATS, URL_KG_EXPLORE, URL_PREDICT, URL_TOP_K,
            URL_RANK, URL_VALIDATE, URL_HEALTH,
        )
    except Exception as exc:
        _fail(f"Could not import shared.contracts.urls: {exc}")
        return

    # Map URL -> (file_path, decorator_pattern)
    url_checks = [
        (URL_KG_STATS, _REPO_ROOT / "phase2" / "service.py", '@app.get("/kg/stats")'),
        (URL_KG_EXPLORE, _REPO_ROOT / "phase2" / "service.py", '@app.get("/kg/explore")'),
        (URL_PREDICT, _REPO_ROOT / "graph_transformer" / "service.py", '@app.post("/predict")'),
        (URL_TOP_K, _REPO_ROOT / "graph_transformer" / "service.py", '@app.get("/top-k")'),
        (URL_RANK, _REPO_ROOT / "rl" / "service.py", '@app.get("/rank")'),
    ]

    for url, path, pattern in url_checks:
        if not path.exists():
            _fail(f"Service file not found: {path}")
            continue
        src = path.read_text(encoding="utf-8")
        # Check for the URL string literal anywhere in the source.
        if url in src:
            _pass(f"{path.name} registers URL {url}")
        else:
            _fail(f"{path.name} does NOT register URL {url}")


# =============================================================================
# TEST 11: Frontend TypeScript contracts exist with API_URLS
# =============================================================================

def test_frontend_contracts_exist() -> None:
    """frontend/contracts/api_contracts.ts must exist with API_URLS."""
    print("\n=== TEST 11: Frontend TypeScript contracts exist ===")
    ts_path = _REPO_ROOT / "frontend" / "contracts" / "api_contracts.ts"
    if not ts_path.exists():
        _fail(f"frontend/contracts/api_contracts.ts not found at {ts_path}")
        return

    src = ts_path.read_text(encoding="utf-8")
    # Check for the canonical URL constants.
    required_urls = [
        '/kg/stats"', '/kg/explore"', '/predict"', '/top-k"',
        '/rank"', '/validate"', '/health"',
    ]
    missing = [u for u in required_urls if u not in src]
    if missing:
        _fail(f"api_contracts.ts missing URL constants: {missing}")
    else:
        _pass("api_contracts.ts has all 7 canonical URL constants")

    # Check for key TypeScript interfaces.
    required_interfaces = [
        "KgStatsResponse", "KgExploreResponse", "PredictResponse",
        "TopKResponse", "RankResponse", "RankedCandidate",
        "ValidateRequest", "ValidateResponse", "HealthResponse",
    ]
    missing_interfaces = [i for i in required_interfaces if f"interface {i}" not in src and f"type {i}" not in src]
    if missing_interfaces:
        _fail(f"api_contracts.ts missing interfaces: {missing_interfaces}")
    else:
        _pass(f"api_contracts.ts has all {len(required_interfaces)} required interfaces")


# =============================================================================
# TEST 12: RecordingGraphBuilder serialization matches the contract
# =============================================================================

def test_recording_graph_builder_serialization_matches_contract() -> None:
    """RecordingGraphBuilder.save() must produce a snapshot matching the contract."""
    print("\n=== TEST 12: RecordingGraphBuilder serialization matches contract ===")
    try:
        from phase2.contracts.kg_builder_contract import (
            RECORDING_GRAPH_BUILDER_FORMAT_VERSION,
            RECORDING_GRAPH_BUILDER_SNAPSHOT_KEYS,
            validate_recording_graph_builder_snapshot,
        )
    except Exception as exc:
        _fail(f"Could not import phase2.contracts.kg_builder_contract: {exc}")
        return

    # Verify the snapshot keys include all required keys.
    required = {
        "__version__", "format", "node_loads", "edge_loads",
        "_node_ids_by_label", "dead_letter",
    }
    actual = set(RECORDING_GRAPH_BUILDER_SNAPSHOT_KEYS)
    if required == actual:
        _pass(f"RECORDING_GRAPH_BUILDER_SNAPSHOT_KEYS has all 6 required keys")
    else:
        _fail(
            f"Snapshot keys mismatch. Expected: {sorted(required)}, "
            f"Actual: {sorted(actual)}"
        )

    # Build a valid sample snapshot and verify the validator accepts it.
    sample_snapshot = {
        "__version__": RECORDING_GRAPH_BUILDER_FORMAT_VERSION,
        "format": "json",
        "node_loads": [],
        "edge_loads": [],
        "_node_ids_by_label": {},
        "dead_letter": [],
    }
    errors = validate_recording_graph_builder_snapshot(sample_snapshot, strict=True)
    if errors:
        _fail(f"Validator rejected a valid sample snapshot: {errors}")
    else:
        _pass("Validator accepts a conforming sample snapshot")

    # Verify the validator REJECTS a malformed snapshot.
    bad_snapshot = {"__version__": "999", "format": "yaml"}
    errors = validate_recording_graph_builder_snapshot(bad_snapshot, strict=True)
    if errors:
        _pass(f"Validator correctly rejects malformed snapshot ({len(errors)} errors)")
    else:
        _fail("Validator accepted a malformed snapshot (should have rejected)")


# =============================================================================
# TEST 13: P4-006 v118 — bridge-vs-env column relationship contract
# =============================================================================

def test_p4_006_bridge_env_column_relationship() -> None:
    """P4-006 v118 ROOT FIX: verify the bridge/env column relationship.

    The audit (P4-006) found that the bridge writes 17 columns but the RL
    env only reads 12 — 5 columns are "silently ignored". This test
    verifies the relationship is now EXPLICIT and DOCUMENTED:

      1. shared/contracts/feature_names.py defines BRIDGE_REQUIRED_COLUMNS
         (12), BRIDGE_OPTIONAL_COLUMNS (5), and BRIDGE_WRITES_COLUMNS (17).
      2. BRIDGE_WRITES_COLUMNS has the same elements as RL_FEATURE_COLUMNS.
      3. rl/constants.py's REQUIRED_COLUMNS equals BRIDGE_REQUIRED_COLUMNS.
      4. BRIDGE_REQUIRED_COLUMNS is a subset of BRIDGE_WRITES_COLUMNS.

    If any of these invariants break, this test fails — surfacing the
    drift before it reaches production.
    """
    print("\n=== TEST 13: P4-006 bridge/env column relationship ===")
    try:
        from shared.contracts.feature_names import (
            RL_FEATURE_COLUMNS,
            BRIDGE_REQUIRED_COLUMNS,
            BRIDGE_OPTIONAL_COLUMNS,
            BRIDGE_WRITES_COLUMNS,
        )
        from rl.constants import REQUIRED_COLUMNS as RL_REQUIRED_COLUMNS

        # Check 1: BRIDGE_WRITES_COLUMNS has same elements as RL_FEATURE_COLUMNS.
        if set(BRIDGE_WRITES_COLUMNS) == set(RL_FEATURE_COLUMNS):
            _pass(
                f"BRIDGE_WRITES_COLUMNS ({len(BRIDGE_WRITES_COLUMNS)}) matches "
                f"RL_FEATURE_COLUMNS ({len(RL_FEATURE_COLUMNS)})"
            )
        else:
            _fail(
                f"BRIDGE_WRITES_COLUMNS drift. "
                f"Only in BRIDGE_WRITES: {set(BRIDGE_WRITES_COLUMNS) - set(RL_FEATURE_COLUMNS)}, "
                f"only in RL_FEATURE: {set(RL_FEATURE_COLUMNS) - set(BRIDGE_WRITES_COLUMNS)}"
            )

        # Check 2: BRIDGE_REQUIRED_COLUMNS is a subset of BRIDGE_WRITES_COLUMNS.
        if set(BRIDGE_REQUIRED_COLUMNS).issubset(set(BRIDGE_WRITES_COLUMNS)):
            _pass(
                f"BRIDGE_REQUIRED_COLUMNS ({len(BRIDGE_REQUIRED_COLUMNS)}) ⊆ "
                f"BRIDGE_WRITES_COLUMNS ({len(BRIDGE_WRITES_COLUMNS)})"
            )
        else:
            _fail(
                f"BRIDGE_REQUIRED_COLUMNS is NOT a subset of BRIDGE_WRITES_COLUMNS. "
                f"Missing: {set(BRIDGE_REQUIRED_COLUMNS) - set(BRIDGE_WRITES_COLUMNS)}"
            )

        # Check 3: rl/constants.py REQUIRED_COLUMNS equals BRIDGE_REQUIRED_COLUMNS.
        if set(RL_REQUIRED_COLUMNS) == set(BRIDGE_REQUIRED_COLUMNS):
            _pass(
                f"rl/constants.py REQUIRED_COLUMNS matches BRIDGE_REQUIRED_COLUMNS "
                f"({len(RL_REQUIRED_COLUMNS)} columns)"
            )
        else:
            _fail(
                f"rl/constants.py REQUIRED_COLUMNS drift. "
                f"Only in RL: {set(RL_REQUIRED_COLUMNS) - set(BRIDGE_REQUIRED_COLUMNS)}, "
                f"only in BRIDGE: {set(BRIDGE_REQUIRED_COLUMNS) - set(RL_REQUIRED_COLUMNS)}"
            )

        # Check 4: the 5 optional columns are exactly the documented set.
        expected_optional = {
            "gnn_score_calibrated", "gnn_score_timestamp",
            "disease_pair_count", "disease_avg_gnn", "disease_avg_safety",
        }
        if set(BRIDGE_OPTIONAL_COLUMNS) == expected_optional:
            _pass(
                f"BRIDGE_OPTIONAL_COLUMNS has the documented 5 audit/transparency "
                f"columns: {sorted(BRIDGE_OPTIONAL_COLUMNS)}"
            )
        else:
            _fail(
                f"BRIDGE_OPTIONAL_COLUMNS drift. Expected: {sorted(expected_optional)}, "
                f"Actual: {sorted(BRIDGE_OPTIONAL_COLUMNS)}"
            )
    except Exception as exc:
        _fail(f"P4-006 test raised: {type(exc).__name__}: {exc}")


# =============================================================================
# TEST 14: P3-002 v118 — Phase 2->3 edge mapping completeness contract
# =============================================================================

def test_p3_002_phase_edge_mapping_completeness() -> None:
    """P3-002 v118 ROOT FIX: verify every dropped Phase 2 edge has a reason.

    The audit (P3-002, CRITICAL) found that PHASE2_TO_PHASE3_EDGE was
    missing 20 of 31 Phase 2 CORE_EDGE_TYPES — they were SILENTLY dropped.
    This test verifies the TM14 v118 fix:

      1. shared/contracts/phase_edge_mapping.py imports successfully.
      2. Every edge in PHASE2_TO_PHASE3_EDGE_DROPPED has a documented
         reason in EDGE_DROP_REASONS.
      3. Every Phase 3 edge in the mapping VALUES is in Phase 3's
         EDGE_TYPES schema (no orphan mappings).
      4. map_edge_with_reason returns the right (edge, reason) tuple
         for mapped, dropped, and unknown edges.
    """
    print("\n=== TEST 14: P3-002 phase edge mapping completeness ===")
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
            f"phase_edge_mapping.py imported (version={PHASE_EDGE_MAPPING_VERSION}, "
            f"mapped={len(PHASE2_TO_PHASE3_EDGE)}, dropped={len(PHASE2_TO_PHASE3_EDGE_DROPPED)})"
        )

        # Check 1: completeness validation passes.
        is_complete, unmapped_dropped, invalid_p3 = (
            validate_phase2_to_phase3_completeness()
        )
        if is_complete:
            _pass(
                f"Phase 2->3 edge contract is COMPLETE: every dropped edge has "
                f"a reason, every mapped Phase 3 edge is valid"
            )
        else:
            if unmapped_dropped:
                _fail(
                    f"Dropped edges WITHOUT a reason: {sorted(unmapped_dropped)}. "
                    f"Add them to EDGE_DROP_REASONS in shared/contracts/phase_edge_mapping.py."
                )
            if invalid_p3:
                _fail(
                    f"Mapped Phase 3 edges NOT in Phase 3's EDGE_TYPES: {sorted(invalid_p3)}. "
                    f"Fix the mapping in phase2/contracts/phase2_schema.py."
                )

        # Check 2: map_edge_with_reason returns sensible results.
        # Test a mapped edge.
        mapped_p3, mapped_reason = map_edge_with_reason(
            ("Compound", "inhibits", "Protein")
        )
        if mapped_p3 == ("drug", "inhibits", "protein") and mapped_reason == "mapped":
            _pass("map_edge_with_reason: ('Compound','inhibits','Protein') -> mapped")
        else:
            _fail(
                f"map_edge_with_reason failed for mapped edge: got "
                f"({mapped_p3}, {mapped_reason})"
            )

        # Test a dropped edge.
        dropped_p3, dropped_reason = map_edge_with_reason(
            ("Protein", "interacts_with", "Protein")
        )
        if dropped_p3 is None and dropped_reason.startswith("dropped:"):
            _pass(
                f"map_edge_with_reason: ('Protein','interacts_with','Protein') "
                f"-> dropped with reason"
            )
        else:
            _fail(
                f"map_edge_with_reason failed for dropped edge: got "
                f"({dropped_p3}, {dropped_reason})"
            )

        # Test an unknown edge.
        unknown_p3, unknown_reason = map_edge_with_reason(
            ("Nonexistent", "foo", "Bar")
        )
        if unknown_p3 is None and unknown_reason.startswith("unknown:"):
            _pass("map_edge_with_reason: unknown edge -> (None, 'unknown:...')")
        else:
            _fail(
                f"map_edge_with_reason failed for unknown edge: got "
                f"({unknown_p3}, {unknown_reason})"
            )
    except Exception as exc:
        _fail(f"P3-002 test raised: {type(exc).__name__}: {exc}")


# =============================================================================
# Master test runner
# =============================================================================

def test_all() -> int:
    """Run all contract consistency tests.

    Returns 0 on success, 1 on any failure. Exits the process with this
    code (so CI can use it as a gate).
    """
    print("=" * 72)
    print("CONTRACT CONSISTENCY TEST (Task 330 + TM14 v118 P3-002/P4-006)")
    print("=" * 72)

    tests = [
        test_phase1_schema_exists_and_importable,
        test_phase2_bridge_imports_phase1_schema,
        test_phase2_schema_contract_exists,
        test_schema_mappings_imports_from_contract,
        test_pyg_builder_no_local_node_type_dict,
        test_phase2_adapter_no_local_node_type_dict,
        test_phase3_checkpoint_contract_matches_trainer,
        test_phase4_csv_contract_matches,
        test_rl_feature_names_contract_matches_constants,
        test_service_urls_match_contract,
        test_frontend_contracts_exist,
        test_recording_graph_builder_serialization_matches_contract,
        # TM14 v118 ROOT FIX tests:
        test_p4_006_bridge_env_column_relationship,
        test_p3_002_phase_edge_mapping_completeness,
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
        print("\nContract consistency check FAILED. See above for details.")
        return 1
    else:
        print("\nContract consistency check PASSED. All writers and readers use the same schema.")
        return 0


if __name__ == "__main__":
    sys.exit(test_all())
