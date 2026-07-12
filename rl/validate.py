"""rl.validate — Scientific Validation (P4-008 modular wrapper).

P4-008 ROOT FIX: modular wrapper around the validation functions:
validate_input_schema, validate_environment, preprocess_data,
generate_data_quality_report, ScientificFailureError, check_alert_conditions.
See rl/env.py for the full P4-008 rationale.

Callers can now import:
    from rl.validate import validate_input_schema, ScientificFailureError
"""
from __future__ import annotations

from .rl_drug_ranker import (
    validate_input_schema,
    validate_environment,
    preprocess_data,
    generate_data_quality_report,
    validate_canonical_ids,
    ScientificFailureError,
    PipelineMetrics,
    check_alert_conditions,
    # Data dictionary + schema (used for validation)
    DATA_DICTIONARY,
    INPUT_SCHEMA,
    OUTPUT_SCHEMA,
)

__all__ = [
    "validate_input_schema",
    "validate_environment",
    "preprocess_data",
    "generate_data_quality_report",
    "validate_canonical_ids",
    "ScientificFailureError",
    "PipelineMetrics",
    "check_alert_conditions",
    "DATA_DICTIONARY",
    "INPUT_SCHEMA",
    "OUTPUT_SCHEMA",
]
