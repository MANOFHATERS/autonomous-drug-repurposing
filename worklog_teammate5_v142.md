# Worklog — Teammate 5 Phase 2 (Knowledge Graph) P2-001 to P2-008 Forensic Root Fixes (v142)

---

Task ID: v142-teammate5-p2-001-to-008
Agent: Teammate 5 (Claude / Super Z)
Task: Forensic root-cause fixes for 8 Phase 2 issues (3 CRITICAL + 5 HIGH) assigned to Teammate 5 in the v142 audit. Read each file line-by-line, verify each issue by reading actual code (not comments), fix at the root level, run real code to verify, then create a branch, push, and merge to main.

## Issue Inventory & Verified Status

| Issue | Severity | Status Before | Status After | Root Fix Applied |
|-------|----------|---------------|--------------|------------------|
| P2-001 | CRITICAL | Partially fixed (fold existed, wired in) | VERIFIED working end-to-end + contract test added | No code change needed — verified fold creates ClinicalOutcome nodes from MedDRA_Term, re-routes edges, is idempotent, and is wired into phase1_bridge.load_into_graph |
| P2-002 | CRITICAL | Already fixed (tri-state logic) | VERIFIED — strict tri-state logic at clinicaltrials_loader.py:3930-4001 | No code change needed — verified True→treats, False→failed_for, None→tested_for (None is not False trap is closed) |
| P2-003 | CRITICAL | NOT FIXED (silent -1.0 sentinel) | FIXED — train_transe RAISES + training_succeeded flag + step11 early-skip | transe_model.py: added training_succeeded field to TrainingHistory; RAISE TransETrainingError when val_triples is None and test_triples provided; RAISE when best_state_dict is None at end of training; run_pipeline.py: step11 returns {"skipped": True, "reason": "no_val_triples"} when val_idx_list is empty; surfaces training_succeeded flag in return dict |
| P2-004 | HIGH | NOT FIXED (dual sys.path) | FIXED — single canonical import path | kg_api.py: removed _PHASE2_ROOT from sys.path (only _REPO_ROOT added); changed /healthz handler from `from drugos_graph import phase1_bridge` to canonical `from phase2.drugos_graph import phase1_bridge` |
| P2-005 | HIGH | NOT FIXED (RNG inside loop) | FIXED — _val_rng created ONCE outside for-loop + assertion | transe_model.py: moved `_val_rng = _random.Random(int(config.seed) + 1)` OUTSIDE the per-relation for-loop; added `id(_val_rng) == _val_rng_object_id` assertion that fires if a future maintainer moves it back inside |
| P2-006 | HIGH | NOT FIXED (escape hatch for scientific bug) | FIXED — RAISE when relation_to_types empty, use actual types per relation when populated | transe_model.py: when relation_to_types is empty in the val-negatives fallback, RAISE ALWAYS (no DRUGOS_ALLOW_NO_SAMPLER escape hatch — scientific correctness bug); when relation_to_types is non-empty, look up ACTUAL (head_type, tail_type) per relation instead of hardcoded Compound/Disease |
| P2-007 | HIGH | NOT FIXED (silent default to 0) | FIXED — relation_idx required, RAISE when None, opt-in flag for legacy path | negative_sampling.py: KGNegativeSampler.combined_sampling now requires relation_idx; RAISES ValueError when None; RAISES ValueError when relation_idx=0 without allow_relation_idx_zero=True opt-in; transe_model.py: legacy single-pool caller passes relation_idx=0 + allow_relation_idx_zero=True with CRITICAL log |
| P2-008 | HIGH | Already fixed (canonicalNodeCount + singular) | VERIFIED — service emits canonicalNodeCount, frontend uses ClinicalOutcome singular | No code change needed — verified _compute_canonical_node_count helper, CANONICAL_NODE_TYPES uses "ClinicalOutcome" (singular), frontend ml-contracts.ts CANONICAL_NODE_TYPES uses "ClinicalOutcome" (singular), kg-service.ts reads canonicalNodeCount |

## Files Modified

1. `phase2/drugos_graph/transe_model.py` — P2-003, P2-005, P2-006, P2-007 (caller) fixes
2. `phase2/drugos_graph/negative_sampling.py` — P2-007 fix
3. `phase2/drugos_graph/kg_api.py` — P2-004 fix
4. `phase2/drugos_graph/run_pipeline.py` — P2-003 (step11 caller) fix
5. `phase2/tests/test_teammate5_p2_001_to_008_v142_forensic_root.py` — NEW contract test file (22 tests covering all 8 issues)

## Files Verified (No Change Needed)

1. `phase2/contracts/phase2_schema.py` — P2-001 schema mappings already correct
2. `phase2/drugos_graph/clinical_outcome_folder.py` — P2-001 fold function already implemented
3. `phase2/drugos_graph/phase1_bridge.py` — P2-001 fold already wired into load_into_graph
4. `phase2/drugos_graph/clinicaltrials_loader.py` — P2-002 tri-state logic already in place
5. `phase2/service.py` — P2-008 canonicalNodeCount + CANONICAL_NODE_TYPES already correct
6. `frontend/src/lib/ml-contracts.ts` — P2-008 ClinicalOutcome (singular) already used
7. `frontend/src/lib/services/kg-service.ts` — P2-008 canonicalNodeCount already read
8. `graph_transformer/data/phase2_adapter.py` — P2-001 validation already present

## Verification

- All 4 modified files pass Python AST syntax check
- All 4 modified files import successfully (torch, numpy, pydantic, fastapi, torch_geometric installed)
- Real-code smoke test for P2-001 fold: PASS (creates ClinicalOutcome nodes, re-routes edges, idempotent)
- Real-code smoke test for P2-003: PASS (train_transe raises TransETrainingError when val_triples=None and test_triples provided)
- Real-code smoke test for P2-007: PASS (combined_sampling raises ValueError when relation_idx is None or 0 without opt-in; works with opt-in)
- 22 new contract tests PASS: `pytest phase2/tests/test_teammate5_p2_001_to_008_v142_forensic_root.py` → 22 passed
- 7 existing contract tests PASS: `pytest phase2/tests/integration/test_p2_to_p3_pyg_hetero_data.py phase2/tests/integration/test_kg_stats_canonical_count.py` → 7 passed
- Broader Phase 2 test suite: 2032 passed (vs 2010 baseline before my changes) — 22 new passing tests, ZERO new failures (103 pre-existing failures unchanged)

## Scientific Integrity Notes

- The fixes are ROOT-LEVEL: no silent fallbacks, no escape hatches for scientific correctness bugs, no -1.0 sentinels that can be misinterpreted
- The P2-003 fix makes the DOCX V1 launch criterion (">0.85 AUC on held-out drug-disease pairs") STRUCTURALLY VERIFIABLE — silent -1.0 returns are now hard errors
- The P2-006 fix removes the DRUGOS_ALLOW_NO_SAMPLER=1 escape hatch for the relation_to_types-empty path — this is a scientific correctness bug, not a unit-test mode concern
- The P2-007 fix makes the false-negative filter HONEST — callers must explicitly opt in to the legacy relation_idx=0 behavior with a CRITICAL log
- The P2-005 fix ensures val AUC variance is unbiased — best-model selection is now based on independent val negatives across relations
- The P2-004 fix eliminates the dual-module-instance drift — /healthz and execution path now see the SAME module object

## Stage Summary

- 8 issues verified by reading actual code (not comments, not tests)
- 5 issues required code fixes (P2-003, P2-004, P2-005, P2-006, P2-007)
- 3 issues were already correctly fixed (P2-001, P2-002, P2-008) — verified by running real code, not by trusting comments
- 22 new contract tests added covering all 8 issues
- Zero new test failures introduced
- Branch: `fix/teammate5-phase2-p2-001-to-008-forensic-root-v142`
- Ready to push, verify CI green, merge to main, then clone fresh to verify
