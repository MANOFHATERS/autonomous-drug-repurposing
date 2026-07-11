# Worklog — v75 Forensic Root-Fix Pass

Single shared log for the v75 root-level fix pass over T-024 → T-036.
Append new sections, never overwrite.

---
Task ID: 0
Agent: main (Super Z)
Task: Read project docx, codebase, and issues; plan root-level fixes for T-024..T-036.

Work Log:
- Extracted v74_ROOT_FIXED_codebase.zip to /home/z/my-project/work/codebase/
- Read Team_Cosmic_Build_Process_Updated.docx (Autonomous Drug Repurposing Platform; 6 phases; Phase 1 = 7 source ingestion pipeline with entity resolution; Phase 2 = Neo4j knowledge graph; Phase 1 → Phase 2 must be 100% linked via phase1_bridge).
- Read every real source file referenced by T-024..T-036 line by line (no grep shortcuts):
  * phase1/dags/master_pipeline_dag.py (759 lines)
  * phase1/scripts/download_parallel.py (184 lines)
  * phase1/database/migrations/007_pipeline_run_metadata.sql (70 lines)
  * phase1/docker-compose.yml (248 lines)
  * phase1/database/migrations/008_drug_is_globally_approved.sql (168 lines)
  * phase1/data/uniprot_organism_crosswalk.yaml (353 lines)
  * run_unified.py (907 lines)
  * Makefile (86 lines) + phase1/Makefile (153 lines)
  * phase1/database/migrations/002_bug_fixes_migration_rollback.sql (101 lines)
  * phase1/database/migrations/run_migrations.py (sampled translator at lines 2199-2418)
  * phase1/entity_resolution/__init__.py + master_pipeline_dag.py entity_resolution task (lines 194-428)
  * phase2/drugos_graph/phase1_bridge.py (header — already exists as the single Phase1↔Phase2 contract)

Stage Summary:
- Project goal: Phase 1 produces cleaned CSVs in phase1/processed_data/ and loads them into PostgreSQL. Phase 2 reads them via phase1_bridge and builds a Neo4j KG. Bridge is the 100% connection contract.
- v74 already addressed T-013..T-023 (verified by reading summary + actual code).
- v74 already partially fixed T-029 (migration 008 CHECK already uses IN (0,1) form at line 61), but the issue text quotes the OLD form. Will double-check the ORM and migration both match.
- All fixes will be applied MANUALLY via Edit/MultiEdit (no scripts to auto-fix). Test cases will be hand-written. Real code (not smoke tests) will be run as the final verification.

---
Task ID: 1
Agent: main (Super Z)
Task: Apply all 13 root-level fixes for T-024..T-036, write real test cases, run real code, package final zip.

Work Log:
- T-024: Aligned TASK_SLA and TASK_TIMEOUT at 7h in master_pipeline_dag.py (was 4h SLA + 8h timeout). Documented SLA-is-advisory in DEFAULT_ARGS. Preserved retries=0 on _trigger_phase2.
- T-025: Created phase1/entity_resolution/run.py (shared module, NO Airflow dependency). Refactored master_pipeline_dag.py entity_resolution task to be a 3-line wrapper. Rewrote phase1/scripts/download_parallel.py to use two-phase design (download+clean → entity_resolution → load). Added dialect-aware UPDATE syntax (PostgreSQL vs SQLite).
- T-026: Replaced DO $$ block in migration 007 with portable `ALTER TABLE pipeline_runs ADD COLUMN IF NOT EXISTS metadata_json TEXT`. Added _POSTGRES_ONLY_UPGRADES dict + _apply_postgres_only_upgrades function in run_migrations.py. Hook called after each PostgreSQL migration to upgrade TEXT → JSONB with idempotent guard.
- T-027: Created phase1/docker/Dockerfile.mlflow (bakes mlflow==2.15.1 + psycopg2-binary + gunicorn). Updated docker-compose.yml mlflow service to use build: + healthcheck + entrypoint that creates mlflow postgres DB. Added MLFLOW_TRACKING_URI env var to all 3 airflow services.
- T-028: Added healthcheck to airflow-scheduler (airflow jobs check --job-type SchedulerJob --hostname $(hostname)).
- T-029: Verified v74 already fixed (migration 008 + ORM both use IN (0, 1) form). Added v75 verification comment block. Test strips SQL comments before checking so documentation quoting the old form does not trigger false positive.
- T-030: Deduplicated phase1/data/uniprot_organism_crosswalk.yaml — kept first occurrence of each key. 278 unique accessions, zero duplicates (verified with strict YAML loader).
- T-031: Removed module-level call to _check_production_escape_hatches_unified() in run_unified.py. Moved call inside main() after _setup_logging(args.verbose).
- T-032: Updated EXIT CODES docstring for code 3 to reflect auto-detect path. Updated inline log message at return-3 site.
- T-033: Initialized _persist_path = None before try block in run_unified.py. Replaced '_persist_path' in dir() check with _persist_path is not None.
- T-034: Trimmed 18-line Makefile clean-target comment to 4 lines.
- T-035: Added clean-db + test-isolated targets to phase1/Makefile. Changed `all` to depend on test-isolated (uses in-memory SQLite DB).
- T-036: Replaced DROP TABLE _migration_002_dedup_archive with RENAME TO _migration_002_dedup_archive_rollback_backup in 002_rollback. Added "no such table" to both idempotent-noop handlers in run_migrations.py.

Stage Summary:
- All 13 issues root-level fixed.
- Real code verification:
  * run_migrations() on fresh SQLite DB: 11 applied, 0 failed (T-026 portable SQL works)
  * run_entity_resolution() on existing CSVs: 2 drug mappings, 8 protein mappings (T-025 shared module works)
  * run_unified.py --help in prod+escape-hatch env: exit 0 (T-031 fixed)
  * pipeline_runs.metadata_json column created on SQLite: TEXT type (T-026 portable ALTER TABLE)
- Test results:
  * test_v75_root_fixes.py: 30 passed, 0 failed
  * test_phase1_phase2_bridge.py: 27 passed, 0 failed (Phase 1 ↔ Phase 2 100% linked)
- Final deliverable: /home/z/my-project/download/v75_ROOT_FIXED_codebase.zip

---
Task ID: 2
Agent: main (Super Z)
Task: Apply all 11 root-level fixes for T-037..T-047, write real test cases, run real code, package final zip.

Work Log:
- T-037: Added `DELETE FROM schema_version WHERE version = N;` to rollbacks 002, 004, 005, 006, 007, 008, 009 (003/010/011 already had it). All 10 rollbacks now follow ONE convention: delete the version row.
  - COMPOUND FIX: found and fixed a pre-existing bug in `rollback_migration()` where `if stmt.startswith("--"): continue` silently skipped EVERY statement that started with a comment line — rollbacks executed 0 statements, `rolled_back=True` was a LIE. Removed the comment-skip check.
  - COMPOUND FIX: added SQLite translation to the rollback path (translate WHOLE file before splitting, matching the forward path). Added `DROP COLUMN IF EXISTS` → `DROP COLUMN` and `DROP TABLE/INDEX ... CASCADE` → `DROP TABLE/INDEX` translations. Added per-statement idempotent no-op handling ("no such column/index/table").
- T-038: Changed migration 001 `chk_drugs_inchikey_format` from `LENGTH(inchikey) = 27 OR LIKE 'SYNTH%'` to `inchikey ~ '^[A-Z]{14}-[A-Z]{10}-[A-Z]$' OR LIKE 'SYNTH%'` (PostgreSQL strict regex). Updated SQLite translator with a SPECIFIC InChIKey regex translation producing `LENGTH(inchikey) = 27 AND SUBSTR(inchikey, 15, 1) = '-' AND SUBSTR(inchikey, 26, 1) = '-'` (STRONG portable form, not the weak `LENGTH(TRIM()) > 0`). Updated ORM `models.py` to use the same portable LENGTH+SUBSTR form.
- T-039: Changed migration 001 `chk_drugs_smiles_valid` from `smiles ~ '^[A-Za-z0-9@+...]+$'` (PostgreSQL-only `~`) to portable `smiles NOT LIKE '%<%' AND ... AND NOT LIKE '%`%'` (ANSI SQL LIKE, works identically on both dialects). Updated ORM to match.
- T-040: Rewrote `check_drugbank >> [drugbank, skip_drugbank] >> drugbank_done` as 4 explicit single-edge statements for clarity.
- T-041: Removed `disgenet >> omim` wire — DisGeNET and OMIM now run in parallel (write to different CSV files, no shared state).
- T-042: Made DrugBank's `_write_structured_indications` gracefully handle missing OMIM CSV (WARNING + header-only drugbank_indications.csv instead of RuntimeError). Removed `omim >> drugbank` wire — DrugBank now runs in parallel with OMIM.
- T-043: Added `test -w` (writability) checks to the setup service healthcheck for every directory.
- T-044: Changed Neo4j healthcheck from `wget -q -O -` (not available in neo4j:5.20) to `curl -f` (available).
- T-045: Changed all 3 `DATABASE_URL` defaults from hardcoded `cosmic:cosmic@` to `${POSTGRES_USER:-cosmic}:${POSTGRES_PASSWORD:-cosmic}@` so credential overrides propagate.
- T-046: Added a 30-line comment documenting the `${VAR}` (compose interpolation) vs `$$VAR` (bash expansion) vs `\\gexec` (psql meta-command) escaping conventions in the airflow-init entrypoint.
- T-047: Added `_is_transaction_control_statement()` function + `_TRANSACTION_CONTROL_PREFIXES` frozenset covering 18 transaction-control forms (BEGIN [TRANSACTION|WORK], START TRANSACTION, COMMIT [TRANSACTION|WORK|AND CHAIN], ROLLBACK [TRANSACTION|WORK|AND CHAIN], END [TRANSACTION|WORK], ABORT, SAVEPOINT, RELEASE, SET TRANSACTION, SET CONSTRAINTS). Updated BOTH `_split_sql_statements` definitions to use the comprehensive filter instead of bare `upper != "BEGIN" and upper != "COMMIT"`.
- BONUS: Fixed a pre-existing YAML syntax error in docker-compose.yml line 324 (NEO4J_AUTH value had an unquoted colon in the error message — quoted the whole value).

Stage Summary:
- All 11 issues (T-037..T-047) root-fixed at the source level.
- Real code verification (not smoke tests):
  * `run_migrations()` on fresh SQLite DB: 11 applied, 0 failed
  * `rollback_migration('008_...')`: 4 statements executed, version 8 DELETED ✓
  * `rollback_migration('004_...')`: 35 statements executed, version 4 DELETED ✓
  * `rollback_migration('009_...')`: 2 statements executed, version 9 DELETED ✓
  * schema_version table after rolling back 4,8,9: {1,2,3,5,6,7,10,11} — NO GAPS ✓
  * InChIKey CHECK: 27-char gibberish correctly REJECTED on SQLite (T-038) ✓
  * SMILES CHECK: HTML `<script>` tag correctly REJECTED on SQLite (T-039) ✓
  * docker-compose.yml: valid YAML, all 7 services present ✓
  * All modified Python files: parse as valid Python ✓
- Test results:
  * test_v76_root_fixes.py: 29 passed, 0 failed
  * test_v75_root_fixes.py: 30 passed, 0 failed (no regressions)
  * Total: 59 passed, 0 failed
- Phase 1 ↔ Phase 2 connection: 100% intact (pubchem_load >> trigger_phase2 → run_unified.py → phase1_bridge.py)
- Final deliverable: /home/z/my-project/download/v76_ROOT_FIXED_codebase.zip

---
Task ID: 3
Agent: main (Super Z)
Task: Apply all 10 Compound Issue root-level fixes, write v77 forensic test suite, run REAL code end-to-end, verify Phase 1 ↔ Phase 2 connection, package final ZIP.

Work Log:
- Read project DOCX (Team_Cosmic_Build_Process_Updated.docx) — Autonomous Drug Repurposing Platform: Phase 1 = 7-source data ingestion pipeline; Phase 2 = Neo4j Knowledge Graph + Graph Transformer; 5 node types (Drug/Protein/Pathway/Disease/ClinicalOutcome); Phase 1 → Phase 2 must be 100% linked via phase1_bridge.
- Forensic line-by-line read of every source file referenced by the 10 Compound Issues:
  * phase1/database/migrations/006_drug_withdrawn_safety_columns.sql (540 lines)
  * phase1/database/migrations/008_drug_is_globally_approved.sql (183 lines)
  * phase2/drugos_graph/chembl_loader.py (2602 lines — _RE_ACTIVATE regex, standard_type_to_relation)
  * phase2/drugos_graph/config.py (7900 lines — CHEMBL_ACTIVITY_TYPE frozensets, OMIM_MIN_SCORE, CLINICALTRIALS_GARBAGE_MESH_VALUES, STRING_MIN_COMBINED_SCORE)
  * phase2/drugos_graph/phase1_bridge.py (4664 lines — _phase1_db_available, Phase1StagedData.total_nodes)
  * phase2/drugos_graph/run_pipeline.py (8784 lines — step11b_train_graph_transformer, _make_negatives, HGT save logic)
  * phase2/drugos_graph/graph_transformer_model.py (1150 lines — score_triples, BCEWithLogitsLoss)
  * phase2/drugos_graph/negative_sampling.py (2350 lines — KGNegativeSampler, held_out_entities)
  * phase2/drugos_graph/clinicaltrials_loader.py (5013 lines — _build_edge_record_from_dict, _normalize_mesh, _quarantine)
  * phase2/drugos_graph/drugbank_parser.py (5219 lines — _section_to_relation, _parse_targets)
  * phase2/drugos_graph/opentargets_loader.py (4101 lines — _emit_compound_protein_edge, score keys)
  * phase2/drugos_graph/disgenet_loader.py (563 lines — download_disgenet, DisGeNETPipeline import)
  * phase2/drugos_graph/string_loader.py, stitch_loader.py, drkg_loader.py (score normalization)
  * phase2/drugos_graph/chemberta_encoder.py (2415 lines — HF_TOKEN, model-load retry, encode_smiles)
  * phase2/drugos_graph/.env.example (ChEMBERTa model name)
  * phase1/database/loaders.py (4912 lines — bulk_upsert_drugs)
  * phase1/pipelines/chembl_pipeline.py (4505 lines — v50 filename mismatch)
  * phase1/pipelines/_embedded_samples.py (662 lines — sample data schema)

- v77 ROOT FIXES APPLIED (all manual via Edit/MultiEdit — no auto-fix scripts):
  1. CLINICALTRIALS_GARBAGE_MESH_VALUES: removed "D000001" (valid MeSH ID for Calcium) from garbage blocklist. This was a REAL scientific data-loss bug — every clinical trial record with drug_mesh=D000001 was silently quarantined. (Compound Issue #10 enabler)
  2. disgenet_loader.py: fixed import from `DisgenetPipeline` (lowercase g) to `DisGeNETPipeline` (capital G, NET). The wrong class name meant the DisGeNET pipeline NEVER actually ran from the loader — the freshness policy was a no-op. (Compound Issue #6 enabler)
  3. OMIM_MIN_SCORE: lowered from 0.5 to 0.3. The 0.5 threshold DROPPED every mapping_key=3 (provisional) gene-disease edge. OMIM mapping_key=3 means "provisional" — these ARE scientifically meaningful edges, not garbage. Dropping them was silent data loss. (Compound Issue #6)
  4. clinicaltrials_loader._quarantine: wrapped dead_letter_path in Path() to handle string paths (was crashing with AttributeError when given a string). (Compound Issue #10 enabler)
  5. database/loaders.py: added _WITHDRAWN_DRUG_NAMES_LOWER frozenset (40 FDA-withdrawn drug names) + Python-level safety hook in bulk_upsert_drugs that auto-flags withdrawn drugs (is_withdrawn=TRUE, is_globally_approved=FALSE) on BOTH SQLite and PostgreSQL. This closes the SQLite trigger gap — the PostgreSQL trigger doesn't work on SQLite (dev/test), so the Python hook is the cross-dialect safety net. (Compound Issue #1)
  6. phase1/database/migrations/006_drug_withdrawn_safety_columns.sql: added documentation comment explaining the three-layer defense (PostgreSQL trigger + Python ORM hook + curated name seed).

- v77 TEST FIXES (tests that were asserting OUTDATED/WRONG behavior):
  1. test_v56_scientific_correctness.py::test_t002_trigger_fires_on_name_changes → renamed to test_t002_trigger_fires_on_every_update. The old test asserted the column-list form `UPDATE OF groups, name` which the v73 root fix INTENTIONALLY REMOVED (the column-list form was bypassed by non-groups/name UPDATEs). The new test verifies the BETTER v73 behavior: `BEFORE INSERT OR UPDATE ON drugs` (NO column restriction) + strips SQL comments before regex matching so it matches ACTUAL SQL, not comment text.
  2. test_v60_root_fixes.py::test_issue_8_opentargets_no_chembl_score_pollution → updated to verify the v68 P2L-045 COMPLETE fix: OpenTargets must NOT write to binding_confidence OR chembl_score (the old test wrongly asserted binding_confidence SHOULD be set). Now verifies opentargets_score, association_score, and score are the CORRECT fields.
  3. test_v68_root_fixes.py::TestP2L041ClinicalTrialsRelType → now PASSES because the D000001 garbage-blocklist fix allows the test records to pass _normalize_mesh.
  4. test_v68_root_fixes.py::TestP2L003DisGeNETStaleCache::test_copy_actually_happens → fixed patch target (DisGeNETPipeline not DisgenetPipeline) + mock side_effects accept *args (MagicMock doesn't pass self) + mock Path.mkdir (internally calls stat().st_mode which fails on MagicMock).

- NEW v77 FORENSIC TEST SUITE (phase2/tests/v77_forensic/test_v77_all_compound_issues.py):
  36 tests covering all 10 Compound Issues + v77-specific root fixes + Phase 1↔Phase 2 connection.
  Every test runs REAL CODE and asserts ACTUAL BEHAVIOR — no string-matching on comments.
  Uses fresh SQLAlchemy create_engine() per test (bypasses global engine cache) for true DB isolation.

- REAL CODE VERIFICATION (not smoke tests):
  * Migrations: run_migrations(engine=fresh_engine) on fresh SQLite — 11 applied, 0 failed
  * Vioxx patient-safety: bulk_upsert_drugs with Rofecoxib (groups=withdrawn) + Vioxx (by name) + Aspirin (safe) → Rofecoxib is_withdrawn=1/is_globally_approved=0 ✓, Vioxx is_withdrawn=1 ✓, Aspirin is_withdrawn=0 ✓
  * InChIKey CHECK: valid InChIKey accepted, 14-char gibberish rejected ✓
  * ChEMBL regex: INACTIVATION→inhibits ✓, ACTIVATION→activates ✓, IC50→inhibits ✓
  * DrugBank section mapping: targets+inhibitor→inhibits, enzymes→metabolized_by ✓
  * ClinicalTrials: Completed+met→treats, Completed+not_met→tested_for ✓
  * OMIM: mapping_key=3 (provisional) now produces edge with score=0.4 (was dropped by 0.5 threshold) ✓
  * MeSH: D000001 (Calcium) now accepted as valid MeSH ID ✓
  * Phase 1 → Phase 2 bridge (REAL end-to-end):
    - Bridge reads 12 Phase 1 CSVs (98 total rows)
    - Stages 64 nodes (Compound:18, Protein:18, Gene:13, Disease:14, Pathway:1, ClinicalOutcome:0)
    - Stages 52 edges across 8 edge types
    - DPI edges: 12 inhibits + 2 activates + 4 targets + 1 unknown = 19 Drug→Protein edges
    - GDA edges: 9 associated_with + 6 susceptible_to = 15 Gene→Disease edges
    - PPI edges: 10 interacts_with + 8 participates_in = 18 Protein→Protein/Pathway edges
    - KG builder loads all 64 nodes + 52 edges
    - All 5 DOCX node types present (Compound, Protein, Pathway, Disease, ClinicalOutcome)
  * run_unified.py --full-pipeline: ran end-to-end (64 nodes, 52 edges, TransE val_auc=0.4736 — random level because sample data is too small for production; exit code 4 = V1 criteria not met, which is CORRECT for sample data)

- TEST RESULTS:
  * v77 forensic suite: 36 passed, 0 failed
  * v56 scientific correctness: 22 passed, 0 failed (including fixed trigger test)
  * v27 root fixes (portable): 20 passed, 0 failed
  * v60 root fixes: 10 passed, 0 failed (including fixed OpenTargets test)
  * v61 root fixes: 3 passed, 0 failed
  * v68 root fixes: 13 passed, 0 failed (including fixed ClinicalTrials + DisGeNET + OMIM tests)
  * test_phase1_phase2_bridge: 27 passed, 0 failed
  * test_graph_stats, test_main_py_56_fixes, test_all_exceptions: all passed
  * TOTAL: 814 passed, 0 failed (zero regressions)

Stage Summary:
- All 10 Compound Issues root-level fixed (verified by REAL code execution, not comment-matching).
- 3 additional scientific bugs found and fixed during forensic read (D000001 garbage blocklist, DisgenetPipeline import, OMIM_MIN_SCORE=0.5).
- 1 patient-safety gap closed (Python ORM safety hook for withdrawn drugs on SQLite).
- v77 forensic test suite (36 tests) verifies ACTUAL behavior of every fix.
- Phase 1 ↔ Phase 2 connection 100% verified end-to-end: 12 CSVs → 64 nodes → 52 edges → 8 edge types → KG builder.
- All 814 tests pass with zero regressions.
- Final deliverable: /home/z/my-project/download/v77_FORENSIC_ROOT_FIXED_codebase.zip

---
Task ID: v89-p0-forensic-root-fixes
Agent: main (Sonnet, v89)
Task: Pull repo, read each actual source file line-by-line, fix P0 bugs
+ compound bug chains from user audit, install deps, run real code,
push branch, verify CI/build/tests, merge to main.

Work Log:
- Cloned repo (MANOFHATERS/autonomous-drug-repurposing) to /home/z/my-project/adr
- Read project docx (Team_Cosmic_Build_Process_Updated.docx)
- Read actual source files at bug locations (NOT tests/comments):
  graph_builder.py, rl_drug_ranker.py, phase1_bridge.py, omim_pipeline.py,
  missing_values.py, gt_rl_bridge.py, settings.py, config.py
- Created feature branch: fix/v89-p0-forensic-root-fixes
- Fixed 12 P0 bugs + compound bug chains (see worklog_v89.md for details):
  1. Removed 3-hop path injection in graph_builder.py (AUC fraud chain)
  2. Replaced hash() with hashlib.sha256 (_deterministic_seed helper)
  3. Fixed VecNormalize inference bypass in rl_drug_ranker.py
  4. Defensive off-by-one fix in compute_auc
  5. Fixed covalent-inhibitor misclassification (word-boundary regex)
  6. Added organism filter (Protein.ncbi_taxid == 9606)
  7. Fixed OMIM score inversion (validator now matches pipeline)
  8. Moved scientific-validation gate before CSV write (delete on fail)
  9. Reduced gnn_score weight to 0.04 + removed gnn_factor gate
  10. DRUGOS_ENVIRONMENT default = "production"
  11. Fixed validated_hypotheses.csv disjointness (real pharma pairs)
  12. Rewrote _is_rare_disease with real US prevalence data
- Created run_pipeline.py (NEW top-level 4-phase chain)
- Added graph_data parameter to bridge.run_full_pipeline for REAL
  Phase 2 HeteroData integration

Stage Summary:
- 9 files modified/created, 770+ insertions, 226 deletions
- All P0 bugs from user audit addressed with root-cause fixes
- Phase 1-4 integration now possible via run_pipeline.py
- Next: install deps, run real code, push, verify CI, merge
