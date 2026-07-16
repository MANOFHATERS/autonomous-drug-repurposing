"""shared.contracts package — cross-phase contracts.

This package contains contracts that are shared across multiple phases.
Each contract module is imported by at least two phases (writer + reader),
so any change to a contract is a compile-time error on both sides.

Modules
-------
- :mod:`urls`           — canonical URL paths for all Python services.
- :mod:`feature_names`  — canonical RL feature names (Phase 3 bridge ->
  Phase 4 env).
- :mod:`writeback`      — writeback contract (Phase 4 writer ->
  Phase 3 trainer reader).
"""
from __future__ import annotations

from shared.contracts.urls import (
    URL_KG_STATS,
    URL_KG_EXPLORE,
    URL_PREDICT,
    URL_TOP_K,
    URL_RANK,
    URL_VALIDATE,
    URL_HEALTH,
    ALL_SERVICE_URLS,
    SERVICE_PORTS,
)
from shared.contracts.feature_names import (
    FEATURE_GNN_SCORE,
    FEATURE_SAFETY_SCORE,
    FEATURE_MARKET_SCORE,
    FEATURE_EFFICACY_SCORE,
    FEATURE_PATENT_SCORE,
    FEATURE_ADME_SCORE,
    CANONICAL_RL_FEATURE_NAMES,
    CANONICAL_RL_FEATURE_ORDER,
    FEATURE_DESCRIPTIONS,
)
from shared.contracts.writeback import (
    WRITEBACK_FILENAME,
    WRITEBACK_WRITER_PATH,
    WRITEBACK_READER_PATH,
    WRITEBACK_CSV_COLUMNS,
    WRITEBACK_OUTCOME_VALUES,
    WRITEBACK_POSITIVE_OUTCOMES,
    WRITEBACK_NEGATIVE_OUTCOMES,
    WRITEBACK_OUTCOME_TO_LABEL,
)

__all__ = [
    # URLs
    "URL_KG_STATS", "URL_KG_EXPLORE", "URL_PREDICT", "URL_TOP_K",
    "URL_RANK", "URL_VALIDATE", "URL_HEALTH",
    "ALL_SERVICE_URLS", "SERVICE_PORTS",
    # Feature names
    "FEATURE_GNN_SCORE", "FEATURE_SAFETY_SCORE", "FEATURE_MARKET_SCORE",
    "FEATURE_EFFICACY_SCORE", "FEATURE_PATENT_SCORE", "FEATURE_ADME_SCORE",
    "CANONICAL_RL_FEATURE_NAMES", "CANONICAL_RL_FEATURE_ORDER",
    "FEATURE_DESCRIPTIONS",
    # Writeback
    "WRITEBACK_FILENAME", "WRITEBACK_WRITER_PATH", "WRITEBACK_READER_PATH",
    "WRITEBACK_CSV_COLUMNS", "WRITEBACK_OUTCOME_VALUES",
    "WRITEBACK_POSITIVE_OUTCOMES", "WRITEBACK_NEGATIVE_OUTCOMES",
    "WRITEBACK_OUTCOME_TO_LABEL",
]
