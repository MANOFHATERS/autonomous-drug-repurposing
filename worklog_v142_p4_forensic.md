# Worklog — v142 P4 Forensic Root Fixes (Teammate 9 + 10 Issues)

## Task ID: v142-forensic-p4
## Agent: Main agent (Claude / Super Z)
## Task: Read every P4 file line-by-line, verify each of 21 issues (P4-001 to P4-021) is still broken, fix root-cause (no surface patches), test, push to branch, verify, merge to main, re-clone.

## Forensic verification results (after reading actual code, NOT comments/tests):

| Issue | Status | Evidence (line numbers in actual code) |
|-------|--------|----------------------------------------|
| P4-001 | FIXED (by prior agent) | backend/api/main.py:1427-1759 — /top-k uses httpx.post to RL service |
| P4-002 | **STILL BROKEN** | rl/requirements.txt:22,28,32,87 — non-existent PyPI versions |
| P4-003 | FIXED | rl/rl_drug_ranker.py:11265 — enrich_candidates_with_pathways called in run_pipeline |
| P4-004 | FIXED | rl/service.py:1024+ get_cached_bridge(); :1691 /reload endpoint |
| P4-005 | FIXED | rl/rl_drug_ranker.py:12636 — gt_test_auc=None fails gate |
| P4-006 | **STILL BROKEN** | rl/__init__.py:28="4.1.0", phase4/__init__.py:15="4.1.0", rl_drug_ranker.py:120-121="4.2.0", service.py:99="1.0.0" |
| P4-007 | **STILL BROKEN** | rl_drug_ranker.py:6010-6013 — bridge_disease_* missing cols filled with 0.0, NO WARNING |
| P4-008 | FIXED | rl_drug_ranker.py:3408+ _check_withdrawn implements fail-CLOSED |
| P4-009 | FIXED | rl_drug_ranker.py:5952 — gnn_score_calibrated=0.0 (neutralized) |
| P4-010 | **STILL BROKEN** | rl_drug_ranker.py:5994-6009 — bridge disease cols min-max normalized PER-ENV |
| P4-011 | **STILL BROKEN** | rl_drug_ranker.py:7595 — model.learn() with only _metrics_callback, no EvalCallback/StopTraining |
| P4-012 | **STILL BROKEN** | rl_drug_ranker.py:7636 — model.save() only at end, no CheckpointCallback |
| P4-013 | **STILL BROKEN** | rl_drug_ranker.py:7583 — learning_rate=_ppo_lr (constant float, no schedule) |
| P4-014 | **STILL BROKEN** | Same as P4-011 — no EvalCallback |
| P4-015 | **STILL BROKEN** | rl_drug_ranker.py:7449 — DummyVecEnv([lambda: env]), n_envs ignored |
| P4-016 | FIXED | rl_drug_ranker.py:8810+ — 1000-resample bootstrap CI |
| P4-017 | **STILL BROKEN** | rl_drug_ranker.py:8868-8873 — return Dict has only global AUC, no auc_by_disease |
| P4-018 | FIXED | rl_drug_ranker.py:12913 — _vh_reward_fn.set_adaptive_threshold(train_gnn_scores) |
| P4-019 | **STILL BROKEN** | rl_drug_ranker.py:1473-1495 — _LazyList.append/extend/etc don't call _recompute_known_positives_set |
| P4-020 | **STILL BROKEN** | rl_drug_ranker.py:7252, 7556 — clip_reward=5.0 (truncates validated_bonus=0.1) |
| P4-021 | **PARTIALLY BROKEN** | rl/train.py, rl/evaluate.py, rl/validate.py, rl/cli.py are pure re-export shims |

## Issues to fix in this branch: 12 (P4-002, P4-006, P4-007, P4-010, P4-011, P4-012, P4-013, P4-014, P4-015, P4-017, P4-019, P4-020, P4-021)

## Plan:
1. P4-002: Pin to conservative ranges that exist on PyPI
2. P4-006: Align all 5 version constants to "4.2.0" (rl_drug_ranker.py's existing value)
3. P4-007: Log WARNING when bridge disease cols missing; track in env metadata
4. P4-010: Use train env's min/max for bridge disease normalization
5. P4-011 + P4-014: Add EvalCallback + StopTrainingOnNoModelImprovement
6. P4-012: Add CheckpointCallback
7. P4-013: Use linear LR schedule
8. P4-015: Use SubprocVecEnv when n_envs>1
9. P4-017: Add per-disease AUC to compute_auc return
10. P4-019: Override _LazyList mutation methods to call _recompute_known_positives_set
11. P4-020: Raise clip_reward to 10.0
12. P4-021: Add DeprecationWarning to pure re-export shims

## Branch: fix/p4-001-to-021-forensic-root-v142

## FINAL VERIFICATION (post-merge, fresh clone from main):

- Re-cloned main as `adr_verify` from `https://github.com/MANOFHATERS/autonomous-drug-repurposing.git`.
- Verified merge commit `67201e1` is the HEAD of main:
  `67201e1 Merge fix/p4-001-to-021-forensic-root-v142: 12 P4 forensic root-cause fixes (v142)`
- Ran `tests/test_p4_v142_forensic_root_fixes.py` on the FRESH clone:
  **Results: 20/20 passed, 0 failed**
- All 12 fixes (P4-002, P4-006, P4-007, P4-010, P4-011, P4-012, P4-013,
  P4-014, P4-015, P4-017, P4-019, P4-020, P4-021) are present on main
  and verified by the test suite.

## CONFLICT RESOLUTION (rebase on Teammate 11 v143):

The rebase on the latest main (which included Teammate 11 v143's refactor
of produce_evaluation_report → _run_inference_once + _compute_auc_from_predictions)
produced ONE conflict in rl/rl_drug_ranker.py. Resolution:

- Adopted TM11's `_compute_auc_from_predictions` approach (avoids double
  inference — better than my v142 approach which re-ran the policy).
- Extended `_run_inference_once` to also collect per-disease labels +
  predictions (for P4-017 per-disease AUC).
- Extended `_compute_auc_from_predictions` to accept `per_disease_labels`
  + `per_disease_preds` and compute per-disease AUC + min_per_disease_auc
  (same shape as compute_auc's return).
- The bridge_disease_min_max parameter on produce_evaluation_report is
  now UNUSED (the new code path doesn't rebuild a test env) but kept in
  the signature for backward compat with callers (run_pipeline).
- The P4-010 fix is still active in the DrugRankingEnv constructor (the
  test_env built by run_pipeline still gets bridge_disease_min_max from
  the train env — that path is unchanged).

All 20/20 tests pass on the rebased + merged main.
