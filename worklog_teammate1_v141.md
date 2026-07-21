# Teammate 1 — Phase 1 (Data Ingestion) — Master DAG + Airflow
## Worklog: 15-Issue Forensic Audit + Root-Fix Pass

**Branch target:** `fix/teammate1-phase1-issues`
**Repo:** https://github.com/MANOFHATERS/autonomous-drug-repurposing
**Cloned to:** /home/z/my-project/work/autonomous-drug-repurposing

## Hostile-auditor methodology
Read REAL code line-by-line in the actual files (not comments, not tests, not grep-only).
Searched for the EXACT cited broken patterns; line numbers had shifted because prior
"fixes" added 400+ lines of comments.

## Per-issue status BEFORE my fixes

| # | ID | Severity | Status before | Notes |
|---|----|----------|---------------|-------|
| 1 | P1-001 | CRITICAL | FIXED | `_expected_csvs` dict gone; `_validate_output_impl` uses contract-driven resolution via `get_all_aliases()` + `get_required_id_column()` |
| 2 | P1-002 | CRITICAL | **STILL BROKEN** | `_persist_cleaned_data` at base_pipeline.py:5021-5045 STILL writes directly to dest. No temp-file-plus-rename, no fsync, no atomic sidecar |
| 3 | P1-003 | CRITICAL | FIXED | `/predict` (line 1220+) and `/top-k` (line 1427+) proxy to GT/RL services via httpx; 503 on failure |
| 4 | P1-004 | CRITICAL | FIXED | Split into `/health` (liveness, line 1116) and `/ready` (readiness, line 1136) with real probes |
| 5 | P1-005 | CRITICAL | FIXED | `_check_dpi_degraded_via_postgres` (line 1456+) uses PostgreSQL via DATABASE_URL, fail-closes in prod |
| 6 | P1-006 | CRITICAL | FIXED | SYNTH check at lines 1807-1851 iterates ALL sources with `inchikey` in required_columns |
| 7 | P1-007 | CRITICAL | **STILL BROKEN** | 5 `except Exception: logger.warning()` blocks at lines 432, 448, 668, 763 still swallow errors. No cumulative-impact tracking, no XCom degraded-state surfacing |
| 8 | P1-008 | CRITICAL | **STILL BROKEN** | base.py:133-135 STILL has no-op `if SCHEMA_VERSION == 0: SCHEMA_VERSION = SCHEMA_VERSION_FALLBACK` (0→0). run_migrations.py:4891 has no explicit handling for code_version=0 (fresh-install case mismatches) |
| 9 | P1-009 | CRITICAL | **STILL BROKEN** | service.py:171-172 `except Exception: return 0` swallows ALL exceptions silently — no log, no sentinel |
| 10 | P1-010 | HIGH | **STILL BROKEN** | service.py:92-94 `.split(",")` has NO `.strip()` — leading spaces break CORS |
| 11 | P1-011 | HIGH | FIXED | `_compute_total_proteins` (line 384+) uses `len(uniprot_ids | string_ids)` UNION |
| 12 | P1-012 | HIGH | **STILL BROKEN** | service.py:474-479 STILL iterates `("drugbank_drugs.csv", "chembl_drugs.csv", "drugs.csv")` and breaks on first non-zero. DrugBank-first wins despite ChEMBL having more drugs |
| 13 | P1-013 | HIGH | FIXED | service.py:772 uses `str(_DB_SCHEMA_VERSION)` (real SCHEMA_VERSION from database.base) |
| 14 | P1-014 | HIGH | **STILL BROKEN** | service.py:540, 559, 577 STILL use `encoding="utf-8"` instead of `utf-8-sig` — BOM breaks first column. The `_open_csv_for_read` helper exists but is NOT used here |
| 15 | P1-015 | HIGH | FIXED | No `f"SELECT COUNT(*) FROM {table}"` pattern remains |

**Issues needing real fixes: 7** (P1-002, P1-007, P1-008, P1-009, P1-010, P1-012, P1-014)

## NEW bugs introduced by prior "fixes" (also need fixing)

### NB-1: Deprecated `datetime.utcnow()` in `_validate_output_impl`
**File:** phase1/dags/master_pipeline_dag.py:2021
**Code:** `"_datetime_module.utcnow().isoformat()`
**Problem:** `_datetime_module` is `datetime.datetime` class. `datetime.utcnow()` is
deprecated in Python 3.12+ (returns naive datetime). The prior fix at lines 1235-1240
EXPLICITLY calls this out as a bug they fixed for `_log_timestamp` — but missed the
same pattern at line 2021 for the `validated_at` field. Inconsistent fix.
**Fix:** Use `datetime.now(timezone.utc)`.

### NB-2: SCHEMA_VERSION drift between contract and DB
**Files:** phase1/contracts/phase1_schema.py:996 vs phase1/database/base.py:90
- Contract: `SCHEMA_VERSION: str = "11"` (hardcoded string)
- DB: `SCHEMA_VERSION: int = _derive_schema_version()` (auto-derived, currently 20)
**Used at:** master_pipeline_dag.py:2015 — the XCom payload's `schema_version` field
reports the CONTRACT version ("11"), not the DB version (20). Phase 2 receiving this
XCom would see "11" while the DB is at 20.
**Impact:** Out-of-band confusion; Phase 2's audit log says schema=11 while DB says 20.
This is NOT directly in the 15-issue audit list, but it's a real bug adjacent to P1-008
and would surface during the user's "build check" verification.
**Decision:** Will note in worklog but NOT fix in this pass — out of scope of the 15
issues. Will note for teammate.

---

Task ID: 1
Agent: Teammate-1-main
Task: Forensic audit + root-fix pass for 15 Phase-1 issues

Work Log:
- Cloned repo via HTTPS with provided PAT.
- Read project docx (Cosmic_Build_Process_Updated.docx) — understood: 7-week,
  6-phase biomedical drug-repurposing platform with ChEMBL/DrugBank/UniProt/STRING/
  DisGeNET/OMIM/PubChem → KG → Graph Transformer → RL ranker → FastAPI/React.
- Read actual code (not comments) in:
  * phase1/dags/master_pipeline_dag.py (2431 lines)
  * phase1/service.py (1346 lines)
  * phase1/contracts/phase1_schema.py (1314 lines)
  * phase1/contracts/validate_output.py (375 lines)
  * phase1/database/base.py (256 lines)
  * phase1/database/migrations/run_migrations.py (6495 lines, sampled)
  * phase1/entity_resolution/run.py (1071 lines)
  * phase1/pipelines/base_pipeline.py (6487 lines, sampled at issue areas)
  * backend/api/main.py (2446 lines)
- Searched for the EXACT cited broken patterns; confirmed which are fixed vs still broken.
- Verified per-issue status (table above).

Stage Summary:
- 7 of 15 issues are GENUINELY FIXED by prior passes (verified by reading real code).
- 7 of 15 issues are STILL BROKEN — prior agents claimed "fixed" in comments but the
  actual code still has the bug, OR the fix was applied inconsistently.
- 1 NEW bug discovered (deprecated `datetime.utcnow()` at master_pipeline_dag.py:2021).
- 1 cross-cutting schema-version drift discovered (contract SCHEMA_VERSION="11" vs DB
  SCHEMA_VERSION=20) — out of scope of the 15 issues; noted for teammate.

NEXT: Apply root-cause fixes to the 7 broken issues + 1 new bug, then write tests,
run real code, branch → push → verify → merge → re-clone.
