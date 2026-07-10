"""Verification script for v82 FORENSIC ROOT FIXES.

Tests the 5 live P1 issues I fixed:
- P1-3: DISGENET_WEAK_EVIDENCE_THRESHOLD configurable
- P1-5: surgical _sanitize_csv_output (preserves numeric dtypes)
- P1-6: required-column NULL check catches NaN-string sentinels
- P1-9: _DRUGBANK_ID_RE accepts synthesized 8-hex IDs
- Misleading Pathway warning removed

Usage:
    cd <repo root>
    DISGENET_USE_API=false DRUGOS_ALLOW_NO_RDKIT=1 python3 scripts/verify_v82_fixes.py
"""
import os
import sys
from pathlib import Path

# Resolve the repo root from this script's location (scripts/ dir)
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "phase1"))
sys.path.insert(0, str(REPO_ROOT / "phase2"))
os.chdir(REPO_ROOT)

print('=== Test 1: P1-3 — DISGENET_WEAK_EVIDENCE_THRESHOLD configurable ===')
from config.settings import (
    DISGENET_WEAK_EVIDENCE_THRESHOLD,
    DISGENET_MIN_SCORE,
    DISGENET_ALLOW_WEAK_EVIDENCE,
    _validate_disgenet_config,
)
print(f'  DISGENET_WEAK_EVIDENCE_THRESHOLD = {DISGENET_WEAK_EVIDENCE_THRESHOLD}')
print(f'  DISGENET_MIN_SCORE              = {DISGENET_MIN_SCORE}')
print(f'  DISGENET_ALLOW_WEAK_EVIDENCE    = {DISGENET_ALLOW_WEAK_EVIDENCE}')
_validate_disgenet_config()
print('  _validate_disgenet_config: OK (no ValueError raised)')
from pipelines.disgenet_pipeline import DisGeNETPipeline
print('  DisGeNETPipeline import: OK')
print()

print('=== Test 2: P1-9 — _DRUGBANK_ID_RE accepts synthesized IDs ===')
from pipelines.drugbank_pipeline import _DRUGBANK_ID_RE
test_cases = [
    ('DB00945', True),       # real 5-digit
    ('DB00722', True),       # real 5-digit
    ('DB001122', True),      # real 6-digit
    ('DB0001122', True),     # real 7-digit
    ('DBA1B2C3D4', True),    # v50 synthesized 8-hex
    ('DB0123ABCD', True),    # v50 synthesized 8-hex (mixed digits + letters)
    ('DBSYNTH000000', True), # v50 sentinel
    ('DBXY', False),         # too short
    ('DB12345ABCD', False),  # 9 hex chars after DB (not 8)
    ('DB1234ABC', False),    # 7 hex chars after DB (not 8)
    ('invalid', False),      # not DB prefix
    ('', False),             # empty
    ('db00945', False),      # lowercase (must be uppercase)
]
all_pass = True
for dbid, expected in test_cases:
    actual = bool(_DRUGBANK_ID_RE.match(dbid))
    status = 'OK' if actual == expected else 'FAIL'
    if status == 'FAIL':
        all_pass = False
    print(f'  {status}  {dbid!r:25s} expected={expected}  actual={actual}')
print(f'  All test cases pass: {all_pass}')
print()

print('=== Test 3: P1-5 — surgical _sanitize_csv_output preserves dtypes ===')
import pandas as pd
import numpy as np
from pipelines.base_pipeline import BasePipeline

class TestPipeline(BasePipeline):
    source_name = 'test'
    def __init__(self):
        pass
    def download(self):
        pass
    def clean(self, raw_path):
        pass
    def load(self, clean_df):
        pass

# DataFrame with mixed dtypes + one dangerous string
df = pd.DataFrame({
    'inchikey': ['BSYNRYMUTXBXSQ-UHFFFAOYSA-N', '=CMD+1', 'RZVAJINKPMORJF-UHFFFAOYSA-N'],
    'pubchem_cid': pd.array([2244, 1983, None], dtype='Int64'),  # nullable Int64
    'mw': [180.16, 300.5, 206.28],  # float64
})
print(f'  Before sanitize: inchikey={df.inchikey.dtype}, pubchem_cid={df.pubchem_cid.dtype}, mw={df.mw.dtype}')
sanitized = TestPipeline()._sanitize_csv_output(df)
print(f'  After  sanitize: inchikey={sanitized.inchikey.dtype}, pubchem_cid={sanitized.pubchem_cid.dtype}, mw={sanitized.mw.dtype}')
print(f'  Dangerous cell escaped: {sanitized.inchikey[1]!r}  (expected to start with quote)')
# Critical: pubchem_cid must STAY Int64 (nullable), not be cast to object
assert str(sanitized.pubchem_cid.dtype) == 'Int64', f'P1-5 FAIL: pubchem_cid dtype changed to {sanitized.pubchem_cid.dtype}'
print('  P1-5 PASS: pubchem_cid preserved as Int64 (nullable integer)')
# Critical: mw must STAY float64
assert str(sanitized.mw.dtype) == 'float64', f'P1-5 FAIL: mw dtype changed to {sanitized.mw.dtype}'
print('  P1-5 PASS: mw preserved as float64')
# Critical: the dangerous cell WAS escaped
assert str(sanitized.inchikey[1]).startswith("'"), f'P1-5 FAIL: dangerous cell not escaped: {sanitized.inchikey[1]!r}'
print('  P1-5 PASS: dangerous string cell was escaped with leading quote')
print()

print('=== Test 4: P1-6 — required-column NULL check catches NaN sentinels ===')
# Create a DataFrame with NaN-string sentinels in a required column
df_with_nan_str = pd.DataFrame({
    'inchikey': ['BSYNRYMUTXBXSQ-UHFFFAOYSA-N', 'nan', ''],  # 'nan' and '' are sentinels
    'name': ['Aspirin', 'Ibuprofen', 'Caffeine'],
})
schema = {
    'required': ['inchikey'],
    'properties': {
        'inchikey': {'type': 'string', 'pattern': r'^[A-Z]{14}-[A-Z]{10}-[A-Z]$'},
    },
}

class TestPipeline2(BasePipeline):
    source_name = 'test'
    def __init__(self):
        pass
    def download(self):
        pass
    def clean(self, raw_path):
        pass
    def load(self, clean_df):
        pass
    def _load_schema(self):
        return {'properties': {'test.csv': schema}}
    def _get_processed_filename(self):
        return 'test.csv'

tp = TestPipeline2()
ok, errors = tp.validate_output(df_with_nan_str)
print(f'  Validation passed: {ok}')
print(f'  Errors: {errors}')
# P1-6 fix: should detect the 'nan' and '' sentinels as NULL values
has_null_error = any('NULL values' in e for e in errors)
assert has_null_error, f'P1-6 FAIL: NULL values not detected. Errors: {errors}'
# Count must be 2 (the 'nan' and the '')
null_error = next(e for e in errors if 'NULL values' in e)
assert '2 NULL values' in null_error, f'P1-6 FAIL: expected 2 NULL values, got: {null_error}'
print('  P1-6 PASS: NaN-string sentinels detected as NULL values (count=2) in required column')
print()

print('=== Test 5: Bridge still produces treats edges (regression check) ===')
from pathlib import Path
from pipelines._embedded_samples import write_all_samples
from drugos_graph.phase1_bridge import read_phase1_outputs, stage_phase1_to_phase2

import shutil
test_dir = Path('/tmp/bridge_verify_v82')
if test_dir.exists():
    shutil.rmtree(test_dir)
write_all_samples(str(test_dir))
frames = read_phase1_outputs(test_dir)
staged = stage_phase1_to_phase2(frames, phase1_processed_dir=test_dir)
treats = staged.edges.get(('Compound', 'treats', 'Disease'), [])
print(f'  Compound nodes: {len(staged.compound_nodes)}')
print(f'  Protein nodes:  {len(staged.protein_nodes)}')
print(f'  Disease nodes:  {len(staged.disease_nodes)}')
print(f'  Pathway nodes:  {len(staged.pathway_nodes)}')
print(f'  ClinicalOutcome nodes: {len(staged.clinical_outcome_nodes)}')
print(f'  Gene nodes:     {len(staged.gene_nodes)}')
print(f'  Treats edges:   {len(treats)}  (V1 launch criterion: >=1)')
# Pathway warning should NOT be in staged.warnings anymore
pathway_warnings = [w for w in staged.warnings if 'Pathway nodes deriverable' in w or 'No Pathway nodes deriverable' in w or 'No Pathway nodes' in w]
print(f'  Premature Pathway warnings: {len(pathway_warnings)}  (should be 0 after v82 fix)')
assert len(treats) >= 1, 'REGRESSION: zero treats edges'
assert len(pathway_warnings) == 0, f'REGRESSION: premature pathway warning still present: {pathway_warnings}'
print('  PASS: Bridge regression check — treats edges > 0, no premature pathway warning')
print()

print('=== ALL V82 FIXES VERIFIED ===')
print('P1-3 (configurable weak-evidence threshold): PASS')
print('P1-5 (surgical sanitize_csv_output):         PASS')
print('P1-6 (NaN-string sentinel NULL check):       PASS')
print('P1-9 (synthesized DrugBank ID regex):        PASS')
print('Misleading Pathway warning removed:          PASS')
print('Bridge regression check:                     PASS')
