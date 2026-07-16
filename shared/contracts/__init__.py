"""
shared.contracts — canonical schemas shared across Phase 1/2/3/4.

Every consumer of the data flywheel (writeback, trainer, RL ranker, bridge)
imports from this package. NO module should hardcode its own path, column
name, outcome value, edge label, or feature name.

Modules:
    writeback      — validated_hypotheses schema (path, columns, outcomes,
                     edge labels, atomic-write profile).
    feature_names  — canonical 15-column RL feature schema produced by
                     graph_transformer/gt_rl_bridge.py and consumed by
                     rl/rl_drug_ranker.py.
"""
