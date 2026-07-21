# Worklog — Teammate 15+16+17 Root Fix Pass

## Initial Code Reading (Hostile Auditor)

### Teammate 15 — Data Flywheel Integration
- `phase4/writeback.py` — writes to CSV (atomic, fsync+os.replace) + Neo4j edge + Phase 3 retrain trigger. ✅ well-implemented.
- `phase1/service.py:696-877` — POST /datasets/validated_hypotheses endpoint EXISTS but **STILL SWALLOWS** drug_id/disease_id lookup errors (P1-022 confirmed BROKEN at lines 759-765 and 780-786 — broad `except Exception` → `logger.warning` → proceeds with NULL). Need: distinguish `not_found` (NULL OK) from `db_error` (503), add `drug_lookup_status` / `disease_lookup_status` fields.
- `graph_transformer/data/graph_builder.py:1738-1740` — **STILL INJECTS** validated_pairs as "treats" edges AND adds to `known_pairs` (P3-008 confirmed BROKEN). Need: remove injection, store as `self.validated_pairs` for RL env.
- `graph_transformer/gt_rl_bridge.py:440` — passes `validated_hypotheses` to graph builder (which then injects them — see above). Need: pass validated_pairs to RL env via `is_validated` column.
- `rl/rl_drug_ranker.py:10417-10465` — **ALREADY** uses `build_reward_function_with_phase1_safety` (P4-026 already fixed). ✅
- `rl/rl_drug_ranker.py:10466` — calls `reward_fn.set_validated_hypotheses(validated_set)` for +0.1 bonus. ✅
- `rl/service.py:1000-1106` — has /validate endpoint that calls write_validated_hypothesis. ✅
- `backend/api/main.py` — **NO** /validate proxy endpoint. Need: add.
- `phase1/dags/retrain_on_validated_dag.py` — **DOES NOT EXIST**. Need: create.

### Teammate 16 — E2E Smoke Test + Production Readiness Gate
- `rl/rl_drug_ranker.py:11901-12247` `run_scientific_validation_gate()` — signature has NO `gt_test_auc` parameter; line 12179 `gt_test_auc = rl_auc  # proxy` (P4-005 confirmed BROKEN). Need: require `gt_test_auc` explicit, FAIL if None.
- `rl/rl_drug_ranker.py:12122` — `_vh_reward_fn.set_adaptive_threshold(test_data[GNN_SCORE_COL].values)` (P4-018 confirmed BROKEN — test-data leakage). Need: use `train_gnn_scores` parameter.
- `rl/rl_drug_ranker.py:8230-8525` `compute_auc()` — returns single `Optional[float]`. Need: return `{auc, ci_lower, ci_upper, n_bootstrap}` dict.
- `scripts/run_e2e_smoke.py` — DOES NOT EXIST. Need: create.
- `scripts/run_production_readiness_gate.py` — DOES NOT EXIST. Need: create.
- `rl/scientific_thresholds.py` — need to check.
- `rl/validate.py` — 89 lines, need to check.

### Teammate 17 — Observability + Monitoring
- `backend/api/main.py` — **DOES NOT** call `configure_app()` (no /metrics). Need: wire up.
- `graph_transformer/service.py` — **DOES NOT** call `configure_app()` (no /metrics). Need: wire up.
- `phase1/service.py` — already calls configure_app ✅ but no phase-specific metrics.
- `phase2/service.py` — already calls configure_app ✅ but no phase-specific metrics.
- `rl/service.py` — already calls configure_app ✅ but no phase-specific metrics.
- `phase2/drugos_graph/mlflow_tracker.py:230` — logs at `WARNING` level (P2-014 confirmed BROKEN). Need: ERROR level + MLFLOW_ATEXIT_CLOSE_FAILURES Counter + check_for_dangling_mlflow_runs.
- `observability/alerts.yml` — has only backup alerts. Need: add 6 alerts (GT AUC, RL reward, Neo4j latency, MLflow dangling, Airflow DAG failure, Phase 1 row count).
- Grafana dashboards directory exists but no dashboard JSON files. Need: create 4 dashboards.

## Fix Order
1. Teammate 15 — Data Flywheel fixes (P1-022, P3-008, gt_rl_bridge, backend /validate, retrain DAG)
2. Teammate 16 — Validation gate fix (gt_test_auc explicit, train_gnn_scores, bootstrap CI, E2E scripts)
3. Teammate 17 — Observability (configure_app wiring, phase metrics, mlflow atexit, alerts, dashboards)
4. Write REAL tests for each fix (not smoke tests)
5. Install deps, run pytest + python -c smoke imports
6. Branch, commit, push, verify CI
7. Merge to main, re-clone, verify fixes survive
