"""Teammate 7 v127 Phase 3 root-forensic test suite.

Hostile-auditor tests for Tasks 7.1-7.5. Each test reads ACTUAL CODE
(not comments) to verify the fix is in place AND exercises the runtime
behavior to verify it actually works.

Verification commands (run from repo root):
  python -m pytest tests/team7_v127_phase3_root_fixes/test_per_epoch_auc.py -v
  python -m pytest tests/team7_v127_phase3_root_fixes/test_gradient_clipping.py -v
  python -m pytest tests/team7_v127_phase3_root_fixes/test_no_data_leakage.py -v
  python -m pytest tests/team7_v127_phase3_root_fixes/test_mlflow_tracking.py -v
  python -m pytest tests/team7_v127_phase3_root_fixes/test_prediction_writeback.py -v
"""
