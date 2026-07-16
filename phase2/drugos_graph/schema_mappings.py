"""Single source of truth for Phase 2 <-> Phase 3 schema mappings.

TASK 331 ROOT FIX (forensic, root-level, no surface fix):
  Previously this file was itself the "single source of truth" — but the
  audit (Tasks 321-335) found that having the canonical mapping live in
  ``phase2/drugos_graph/`` (an IMPLEMENTATION package) meant Phase 3 had
  to import from Phase 2's implementation code, creating a circular
  dependency. The mapping also drifted from the actual Phase 3 schema
  in subtle ways (e.g. this file's ``PHASE2_TO_PHASE3_NODE`` was
  ``Dict[str, str]`` dropping intermediates, while the contract needs
  ``Dict[str, Optional[str]]`` to explicitly mark intermediates as
  "dropped on purpose, not forgotten").

  This file is now a BACKWARD-COMPAT SHIM that re-exports from the
  canonical contract module ``phase2/contracts/phase2_schema.py``.
  Existing callers (pyg_builder.py, phase2_adapter.py, tests) continue
  to work without changes — the symbol names and value shapes are
  preserved. NEW callers should import directly from
  ``phase2.contracts.phase2_schema`` to make the contract dependency
  explicit.

Why a shim instead of deleting this file?
  The codebase has 7+ files importing from ``drugos_graph.schema_mappings``
  (pyg_builder, phase2_adapter, 5 test files). Deleting the module would
  break all of them. The shim preserves backward compatibility while
  making the contract module the canonical source. The contract
  consistency test (shared/tests/test_contract_consistency.py)
  verifies this shim imports from the contract.
"""
from __future__ import annotations

# =============================================================================
# CANONICAL IMPORTS — from phase2.contracts.phase2_schema (the contract)
# =============================================================================
# These are the SINGLE source of truth. Any change to a mapping MUST be
# made in phase2/contracts/phase2_schema.py, not here.
from phase2.contracts.phase2_schema import (
    NODE_TYPES,
    ALL_PHASE2_NODE_TYPES,
    ALL_PHASE3_NODE_TYPES,
    INTERMEDIATE_NODE_TYPES,
    EDGE_TYPES,
    PHASE2_TO_PHASE3_EDGE,
    PHASE3_TO_PHASE2_EDGE,
    NODE_FEATURE_SCHEMAS,
    EDGE_FEATURE_SCHEMAS,
    is_intermediate_node_type,
    NodeFeatureSpec,
    EdgeFeatureSpec,
    FeatureColumn,
)

# Backward-compat: Phase 2 -> Phase 3 node mapping.
# The contract has PHASE2_TO_PHASE3_NODE as Dict[str, Optional[str]] (with
# None for intermediates Gene/MedDRA_Term). Existing callers expect a
# Dict[str, str] that only contains the 5 canonical entries (raising
# KeyError for intermediates). We preserve that behavior by re-exporting
# the CANONICAL variant (which excludes intermediates).
from phase2.contracts.phase2_schema import (
    PHASE2_TO_PHASE3_NODE_CANONICAL as PHASE2_TO_PHASE3_NODE,
)

# Reverse lookup: Phase 3 -> Phase 2. This is a 1-to-1 mapping (5 entries).
from phase2.contracts.phase2_schema import (
    PHASE3_TO_PHASE2_NODE,
)


__all__ = [
    # Node types
    "NODE_TYPES",
    "ALL_PHASE2_NODE_TYPES",
    "ALL_PHASE3_NODE_TYPES",
    "INTERMEDIATE_NODE_TYPES",
    "PHASE2_TO_PHASE3_NODE",
    "PHASE3_TO_PHASE2_NODE",
    "is_intermediate_node_type",
    # Edge types
    "EDGE_TYPES",
    "PHASE2_TO_PHASE3_EDGE",
    "PHASE3_TO_PHASE2_EDGE",
    # Feature schemas
    "NODE_FEATURE_SCHEMAS",
    "EDGE_FEATURE_SCHEMAS",
    "NodeFeatureSpec",
    "EdgeFeatureSpec",
    "FeatureColumn",
]
