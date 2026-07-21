"""Phase 2 integration tests — exercises multiple modules end-to-end.

This subpackage holds integration tests that verify the contract between
Phase 1 (data ingestion) and Phase 2 (KG construction). The tests in
``test_p2_to_p1_contract.py`` were added by Teammate 5 (P0 root fix) to
guard against the schema-drift failure mode where the Phase 2 bridge's
hardcoded ``_PHASE1_EXPECTED_COLUMNS`` dict diverged from the canonical
Phase 1 contract (``phase1/contracts/phase1_schema.py``).
"""
