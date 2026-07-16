"""
shared — cross-phase contracts, tests, docs, and monitoring.

This package is the SINGLE source of truth for schemas, contracts, and
cross-phase integration tests. Phase 1/2/3/4 modules import from here so
that a change to a contract propagates to all consumers atomically.

Subpackages:
    contracts/   — canonical schemas (paths, column names, outcome values,
                   edge labels, feature names). Re-exports common.* for
                   backward compatibility.
    tests/       — cross-phase integration tests (data flywheel E2E,
                   toxic penalty, checkpoint atomicity, idempotency).
    docs/        — architectural documentation (data_flywheel.md, etc.).
    monitoring/  — flywheel step monitoring and alerting.
"""
