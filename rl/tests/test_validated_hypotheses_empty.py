"""Task 9.3 — rl/validated_hypotheses.csv ships as header-only (no fake demo data).

P4-011 ROOT FIX verification.

The previous rl/validated_hypotheses.csv shipped with FAKE demo data
(pharma_partner_alpha, fake timestamps). On fresh deployments, this
biased the RL agent toward 4 specific drug-disease pairs.

ROOT FIX:
  1. rl/validated_hypotheses.csv ships as HEADER-ONLY (1 line).
  2. _load_validated_hypotheses warns if the file is empty.
  3. The file is populated by the data flywheel (phase4/writeback.py).
"""
import csv
import logging
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RL_REQUIRE_AUTH", "false")


def test_task_9_3_validated_hypotheses_csv_is_header_only():
    """P4-011: rl/validated_hypotheses.csv must have EXACTLY 1 line (header only).

    Verification command from the task spec:
        wc -l rl/validated_hypotheses.csv  # should be 1 (header only)
    """
    csv_path = REPO / "rl" / "validated_hypotheses.csv"
    assert csv_path.exists(), (
        f"P4-011 REGRESSION: {csv_path} does not exist. The file must ship "
        f"as header-only (1 line) so the data flywheel can populate it."
    )
    with open(csv_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1, (
        f"P4-011 REGRESSION: {csv_path} has {len(lines)} lines, expected 1 "
        f"(header only). The file must NOT ship with fake demo data — it "
        f"is populated by the data flywheel (phase4/writeback.py) as pharma "
        f"partners validate hypotheses. Lines: {lines}"
    )


def test_task_9_3_validated_hypotheses_csv_has_canonical_headers():
    """P4-011 + P4-033: the header must use the canonical 10-column schema."""
    csv_path = REPO / "rl" / "validated_hypotheses.csv"
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
    expected = [
        "drug", "disease", "outcome", "validated_by",
        "validation_study_id", "validated_at", "notes",
        "original_gt_score", "original_rl_rank", "writeback_version",
    ]
    assert header == expected, (
        f"P4-011/P4-033 REGRESSION: header mismatch. "
        f"Expected: {expected}\nGot: {header}"
    )


def test_task_9_3_validated_hypotheses_csv_has_no_fake_demo_data():
    """P4-011: the file must NOT contain fake demo data (pharma_partner_alpha, etc.)."""
    csv_path = REPO / "rl" / "validated_hypotheses.csv"
    content = csv_path.read_text(encoding="utf-8").lower()
    forbidden_substrings = [
        "pharma_partner_alpha",
        "pharma_partner_beta",
        "pharma_partner_gamma",
        "demo_partner",
        "fake_partner",
        "test_partner",
    ]
    for substr in forbidden_substrings:
        assert substr.lower() not in content, (
            f"P4-011 REGRESSION: {csv_path} contains the fake demo data "
            f"substring '{substr}'. The file must ship as HEADER-ONLY — "
            f"fake demo data biases the RL agent toward specific drug-disease pairs."
        )


def test_task_9_3_load_validated_hypotheses_warns_on_empty_file(caplog, tmp_path, monkeypatch):
    """P4-011 v128: _load_validated_hypotheses MUST warn if the file is empty.

    On a fresh deployment, the file ships as header-only. The function
    should log a WARNING (not CRITICAL) so the operator knows the reward
    bonus is currently a no-op, but doesn't get paged at 3am.

    This test isolates the loader by pointing ALL candidate paths at a
    tmp_path with a header-only CSV. The phase1/processed_data/ file is
    bypassed by setting VALIDATED_HYPOTHESES_CSV to the tmp_path.
    """
    import rl.rl_drug_ranker as rdr

    # Build a header-only CSV in tmp_path.
    csv_path = tmp_path / "validated_hypotheses.csv"
    csv_path.write_text(
        "drug,disease,outcome,validated_by,validation_study_id,validated_at,"
        "notes,original_gt_score,original_rl_rank,writeback_version\n",
        encoding="utf-8",
    )

    # Point ALL env vars at the empty CSV so the loader can't find a
    # non-empty file in any of its 4 candidate paths.
    monkeypatch.setenv("RL_VALIDATED_HYPOTHESES_PATH", str(csv_path))
    monkeypatch.setenv("VALIDATED_HYPOTHESES_CSV", str(csv_path))

    # Also mock shared.contracts.writeback.get_validated_csv_path to return
    # our tmp_path (the function imports this at runtime).
    try:
        import shared.contracts.writeback as wb
        monkeypatch.setattr(wb, "get_validated_csv_path", lambda: str(csv_path))
    except ImportError:
        pass  # The fallback path in _load_validated_hypotheses uses the env var.

    # Call the loader FUNCTION directly (not via the lazy proxy). The lazy
    # proxy may have been replaced by a plain list if a prior test called
    # retrain_on_validated (which reassigns VALIDATED_HYPOTHESES = list(merged)).
    # Calling _load_validated_hypotheses() directly avoids the proxy entirely.
    with caplog.at_level(logging.WARNING):
        result = rdr._load_validated_hypotheses()

    # The header-only file has 0 data rows → result is empty.
    assert len(result) == 0, (
        f"P4-011: expected empty result from header-only CSV, got {result}"
    )

    # A WARNING about the empty file must be in the logs.
    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any("EMPTY" in m or "empty" in m for m in warning_messages), (
        f"P4-011 v128 REGRESSION: no warning logged for empty "
        f"validated_hypotheses.csv. Warning messages: {warning_messages}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
