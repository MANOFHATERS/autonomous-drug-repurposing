"""shared.tests package — cross-phase contract consistency tests.

This package contains tests that verify contract consistency ACROSS
phases (e.g. Phase 1 schema matches Phase 2 bridge expectations,
Phase 3 checkpoint format matches Phase 4 reader expectations).

These tests run in CI on every PR (Task 332) and MUST pass before merge.
"""
from __future__ import annotations
