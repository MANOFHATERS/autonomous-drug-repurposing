"""v124 forensic root-cause verification tests (Teammate 2 — hostile-auditor pass).

This module contains FORENSIC tests that verify the v124 real root fixes by
reading the ACTUAL executable code (not comments, not test files). Each test
is named after the issue it guards (IN-096, P2-043, IN-072-followup) and is
designed to FAIL LOUDLY if a future agent regresses the fix.

The audit's #1 warning: "many of these fixes introduced NEW bugs while
patching old ones, and several 'ROOT FIX' claims are aspirational rather
than actual." These tests catch BOTH failure modes:

  1. Aspirational claims: a comment says "ROOT FIX" but the executable
     code doesn't actually do what the comment claims.
  2. New bugs: a fix to issue A introduces a regression in issue B
     (e.g., IN-072 deleted legacy runners but CI still referenced them).

These tests run as part of the default ``pytest tests/`` invocation
(NOT marked slow/network/gpu).
"""
