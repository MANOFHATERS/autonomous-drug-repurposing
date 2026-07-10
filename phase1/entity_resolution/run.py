"""Shared entry point for cross-database entity resolution.

v75 ROOT FIX (T-025 — download_parallel.py skips entity resolution):
    The forensic audit found that ``scripts/download_parallel.py`` and
    the Makefile's ``download-all`` / ``download-samples`` targets all
    called ``cls(run_id=...).run()`` for each pipeline — the FULL run
    including LOAD to DB — but NEVER ran entity resolution. The
    knowledge graph built from such a DB had no ``entity_mapping`` rows
    and ``proteins.string_id`` was never updated. The master Airflow
    DAG was the ONLY caller that ran entity resolution (inline in the
    ``@task entity_resolution`` callable).

    The COMPOUND problem: the entity resolution logic lived INLINE in
    the Airflow task body. There was no reusable function for non-
    Airflow callers. So even a developer who noticed the gap could not
    fix ``download_parallel.py`` without either (a) duplicating ~250
    lines of code (which would drift from the Airflow version, the
    classic copy-paste bug), or (b) importing the Airflow task directly
    (couples the CLI to Airflow runtime).

    ROOT FIX (master-grade):
      1. Extract the entity resolution logic into THIS module as a
         single function ``run_entity_resolution()``.
      2. The Airflow task (master_pipeline_dag.py:entity_resolution)
         becomes a thin wrapper that calls this function.
      3. ``scripts/download_parallel.py`` calls this function between
         SECOND_PASS and THIRD_PASS (PubChem needs drugs in DB, so
         resolution MUST run before PubChem download — same ordering
         as the master DAG).
      4. The Makefile's ``download-all`` and ``download-samples``
         targets continue to call ``.run()`` (full run) — they are
         documented as "unresolved DB" targets. The Makefile now
         points operators at ``download-parallel`` for the resolved
         path. (Refactoring the Makefile to use the two-phase design
         for every target is out of scope for T-025; the parallel
         path is the canonical CLI entry point for resolved DBs.)

    This module has NO Airflow dependency — it can be imported and
    called from any Python context (CLI, pytest, notebook). The only
    dependencies are pandas, SQLAlchemy, and the Phase 1 package
    (config.settings, database.connection, entity_resolution resolvers).

Usage:
    from entity_resolution.run import run_entity_resolution
    run_entity_resolution()  # raises RuntimeError if no drug data

Returns:
    A dict with keys ``drug_mappings``, ``protein_mappings``,
    ``proteins_updated`` (int counts). The return value is consumed by
    tests; production callers ignore it.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def run_entity_resolution() -> Dict[str, Any]:
    """Run cross-database entity resolution and persist results to the DB.

    Reconciles drug entities across ChEMBL, DrugBank, and PubChem using
    InChIKey matching, connectivity-block matching, and normalised-name
    matching.  Also resolves protein entities across UniProt and STRING.

    Results are persisted to the ``entity_mapping`` table and the
    ``proteins.string_id`` column is updated with resolved STRING IDs.

    Raises:
        RuntimeError: if all three drug DataFrames are empty (the
            operator has not run any download pipeline first).
    """
    import pandas as pd
    from sqlalchemy import text

    from config.settings import PROCESSED_DATA_DIR
    from database.connection import get_engine
    from entity_resolution.drug_resolver import DrugResolver
    from entity_resolution.protein_resolver import ProteinResolver

    # ------------------------------------------------------------------
    # Drug entity resolution
    # ------------------------------------------------------------------
    logger.info("Starting drug entity resolution …")
    drug_resolver = DrugResolver()

    chembl_path = PROCESSED_DATA_DIR / "drugs.csv"
    drugbank_path = PROCESSED_DATA_DIR / "drugbank_drugs.csv"
    pubchem_path = PROCESSED_DATA_DIR / "pubchem_enrichment.csv"

    chembl_df = (
        pd.read_csv(chembl_path, low_memory=False)
        if chembl_path.exists()
        else pd.DataFrame()
    )
    drugbank_df = (
        pd.read_csv(drugbank_path, low_memory=False)
        if drugbank_path.exists()
        else pd.DataFrame()
    )
    pubchem_df = (
        pd.read_csv(pubchem_path, low_memory=False)
        if pubchem_path.exists()
        else pd.DataFrame()
    )

    # FIX AUDIT-7: Validate that at least one drug DataFrame has data.
    total_drug_records = len(chembl_df) + len(drugbank_df) + len(pubchem_df)
    if total_drug_records == 0:
        logger.error(
            "All three drug DataFrames are empty (chembl=%d, drugbank=%d, pubchem=%d). "
            "This usually means the CSV files in %s do not exist or are empty. "
            "Run the download pipelines first before entity resolution.",
            len(chembl_df), len(drugbank_df), len(pubchem_df),
            PROCESSED_DATA_DIR,
        )
        raise RuntimeError(
            f"Entity resolution cannot proceed: all drug DataFrames are empty. "
            f"Ensure ChEMBL, DrugBank, and/or PubChem pipelines have been run. "
            f"Checked: {chembl_path}, {drugbank_path}, {pubchem_path}"
        )
    # Validate required columns in non-empty DataFrames
    required_drug_cols = {"inchikey", "name"}
    for name, df_check in [("chembl", chembl_df), ("drugbank", drugbank_df), ("pubchem", pubchem_df)]:
        if not df_check.empty and not required_drug_cols.issubset(set(df_check.columns)):
            missing = required_drug_cols - set(df_check.columns)
            logger.warning(
                "Drug DataFrame '%s' is missing required columns: %s. "
                "Available columns: %s. Entity resolution may produce incomplete results.",
                name, missing, list(df_check.columns),
            )

    drug_mapping_df = drug_resolver.build_mapping(chembl_df, drugbank_df, pubchem_df)
    logger.info(
        "Drug entity resolution complete: %d canonical entities",
        len(drug_mapping_df),
    )

    # ------------------------------------------------------------------
    # Protein entity resolution
    # ------------------------------------------------------------------
    logger.info("Starting protein entity resolution …")
    protein_resolver = ProteinResolver()

    proteins_path = PROCESSED_DATA_DIR / "proteins.csv"
    uniprot_df = (
        pd.read_csv(proteins_path, low_memory=False)
        if proteins_path.exists()
        else pd.DataFrame()
    )

    # FIX AUDIT-8: Also load STRING PPI data to provide protein IDs from
    # the interaction network. Extract unique UniProt IDs from both
    # uniprot_id_a and uniprot_id_b columns of the STRING processed output.
    # Schema reconciliation (GUARD-2.1, GUARD-2.2, BUG-14.1, BUG-15.1,
    # BUG-15.2): the upgraded StringPipeline now outputs the schema-
    # conformant column names `uniprot_id_a` / `uniprot_id_b` (was
    # `uniprot_a` / `uniprot_b`).
    #
    # v80 FORENSIC ROOT FIX (P0-D2 — _string_to_uniprot cross-reference
    #   index NEVER populated):
    #   The previous code only loaded the STRING PPI edges file
    #   (``protein_protein_interactions.csv``), extracted unique UniProt
    #   IDs from it, and passed that as ``string_df`` to
    #   ``protein_resolver.build_mapping``. But ``string_df`` only
    #   creates ``string_derived`` provisional entries — it does NOT
    #   populate the resolver's ``_string_to_uniprot`` cross-reference
    #   index. That index is populated ONLY from ``string_aliases_df``
    #   (STRING alias data containing the STRING→UniProt mapping).
    #   Without that index, ``resolve_single(string_id=...)`` is a dead
    #   path: it looks up ``self._string_to_uniprot.get(string_id)``
    #   which always returns None because the dict was never populated.
    #   STRING-derived protein IDs therefore NEVER resolve to UniProt,
    #   and STRING PPI edges never merge into the canonical Protein
    #   subgraph — a silent integration gap that produced a KG with
    #   disconnected STRING and UniProt protein clusters.
    #
    #   ROOT FIX: also load the STRING aliases file
    #   (``string_protein_aliases.csv`` if emitted by the STRING
    #   pipeline's clean() stage, OR the raw ``.aliases.vXX.txt.gz``
    #   in raw_dir as a fallback) and pass it as ``string_aliases_df``.
    #   This populates ``_string_to_uniprot`` so ``resolve_single`` works.
    #
    # v80 FORENSIC ROOT FIX (P0-D5 — fragile STRING column-name check):
    #   The previous code only checked for columns named
    #   ``uniprot_id_a`` / ``uniprot_id_b`` (the v49 schema). If the
    #   STRING pipeline emitted the legacy ``uniprot_a`` / ``uniprot_b``
    #   schema (or the v50 sample's ``uniprot_ac_a`` / ``uniprot_ac_b``,
    #   or the embedded ``uniprot_id1`` / ``uniprot_id2``), the
    #   ``string_protein_df`` was silently empty — 0 Protein-Protein
    #   edges in the KG, missing the entire PPI subgraph.
    #
    #   ROOT FIX: check ALL known column-name variants and use whichever
    #   pair is present. The known variants are:
    #     - ``uniprot_id_a`` / ``uniprot_id_b``  (v49 schema-conformant)
    #     - ``uniprot_a``    / ``uniprot_b``     (legacy v48)
    #     - ``uniprot_ac_a`` / ``uniprot_ac_b``  (v50 sample mode + embedded)
    #     - ``uniprot_id1``  / ``uniprot_id2``   (embedded samples legacy)
    string_path = PROCESSED_DATA_DIR / "protein_protein_interactions.csv"
    string_protein_df = pd.DataFrame()
    if string_path.exists():
        try:
            string_df = pd.read_csv(string_path, low_memory=False)
            if not string_df.empty:
                # P0-D5: check all known column-name variants for the
                # UniProt ID pair. Use the first matching pair.
                _COLUMN_PAIR_VARIANTS = [
                    ("uniprot_id_a", "uniprot_id_b"),
                    ("uniprot_a", "uniprot_b"),
                    ("uniprot_ac_a", "uniprot_ac_b"),
                    ("uniprot_id1", "uniprot_id2"),
                    ("string_protein_a", "string_protein_b"),
                ]
                col_a, col_b = None, None
                for _ca, _cb in _COLUMN_PAIR_VARIANTS:
                    if _ca in string_df.columns and _cb in string_df.columns:
                        col_a, col_b = _ca, _cb
                        logger.info(
                            "Found STRING UniProt ID columns: %s / %s",
                            col_a, col_b,
                        )
                        break
                if col_a and col_b:
                    uniprot_ids = set()
                    for col in (col_a, col_b):
                        uniprot_ids.update(
                            str(v).strip()
                            for v in string_df[col].dropna().unique()
                            if str(v).strip() and str(v).strip() != "nan"
                        )
                    if uniprot_ids:
                        string_protein_df = pd.DataFrame({"uniprot_id": list(uniprot_ids)})
                        logger.info(
                            "Extracted %d unique UniProt IDs from STRING PPI data",
                            len(string_protein_df),
                        )
                else:
                    logger.warning(
                        "STRING PPI file %s has no recognized UniProt ID "
                        "column pair. Checked variants: %s. Available "
                        "columns: %s. PPI subgraph will be empty.",
                        string_path,
                        [f"{a}/{b}" for a, b in _COLUMN_PAIR_VARIANTS],
                        list(string_df.columns),
                    )
        except Exception as exc:
            logger.warning("Failed to load STRING data for protein resolution: %s", exc)

    # v80 P0-D2: load the STRING aliases file and pass it as
    # ``string_aliases_df`` so the resolver's ``_string_to_uniprot``
    # cross-reference index is populated. Prefer the processed CSV
    # emitted by the STRING pipeline; fall back to the raw .aliases.vXX.txt.gz.
    string_aliases_df = pd.DataFrame()
    aliases_csv_path = PROCESSED_DATA_DIR / "string_protein_aliases.csv"
    if aliases_csv_path.exists():
        try:
            string_aliases_df = pd.read_csv(aliases_csv_path, low_memory=False)
            logger.info(
                "Loaded STRING aliases from processed CSV: %d rows",
                len(string_aliases_df),
            )
        except Exception as exc:
            logger.warning("Failed to load STRING aliases CSV %s: %s", aliases_csv_path, exc)
            string_aliases_df = pd.DataFrame()

    # Fallback: try to load the raw STRING aliases file from raw_dir.
    # The STRING pipeline downloads it as ``9606.protein.aliases.vXX.txt.gz``.
    if string_aliases_df.empty:
        try:
            from config.settings import RAW_DATA_DIR
            _string_raw_dir = RAW_DATA_DIR / "string"
            # Find any .aliases.*.txt.gz file in the STRING raw dir.
            _alias_files = list(_string_raw_dir.glob("*aliases*.txt.gz")) if _string_raw_dir.exists() else []
            if _alias_files:
                _alias_file = _alias_files[0]
                # The raw aliases file is gzipped, space/tab-separated,
                # with columns: string_protein_id, source, alias, source_database.
                # We only need the STRING→UniProt mapping (where source_database
                # contains "UniProt").
                import gzip
                _alias_records = []
                with gzip.open(_alias_file, "rt", encoding="utf-8") as _af:
                    for _line in _af:
                        _line = _line.strip()
                        if not _line or _line.startswith("#"):
                            continue
                        _parts = _line.split("\t") if "\t" in _line else _line.split()
                        if len(_parts) >= 4:
                            _string_id, _source, _alias, _src_db = _parts[:4]
                            if "UniProt" in _src_db or _source == "UniProt_AC":
                                _alias_records.append({
                                    "string_id": _string_id,
                                    "uniprot_id": _alias,
                                    "source": _source,
                                    "source_database": _src_db,
                                })
                if _alias_records:
                    string_aliases_df = pd.DataFrame(_alias_records)
                    logger.info(
                        "Loaded STRING aliases from raw file %s: %d UniProt mappings",
                        _alias_file.name, len(string_aliases_df),
                    )
        except Exception as exc:
            logger.warning(
                "Could not load raw STRING aliases file: %s — "
                "string_aliases_df will be empty, resolve_single(string_id=...) "
                "will not resolve STRING IDs to UniProt",
                exc,
            )

    protein_mapping_df = protein_resolver.build_mapping(
        uniprot_df,
        string_aliases_df=string_aliases_df if not string_aliases_df.empty else None,
        string_df=string_protein_df if not string_protein_df.empty else None,
    )
    logger.info(
        "Protein entity resolution complete: %d canonical entities",
        len(protein_mapping_df),
    )

    # ------------------------------------------------------------------
    # Persist drug entity mappings
    # ------------------------------------------------------------------
    if not drug_mapping_df.empty:
        # Align columns to EntityMapping schema — drop extras, fill missing
        col_map = {
            "canonical_inchikey": "canonical_inchikey",
            "canonical_name": "canonical_name",
            "chembl_id": "chembl_id",
            "drugbank_id": "drugbank_id",
            "pubchem_cid": "pubchem_cid",
            "uniprot_id": "uniprot_id",
            "string_id": "string_id",
            "match_confidence": "match_confidence",
            "match_method": "match_method",
        }
        save_df = drug_mapping_df.rename(columns=col_map)
        # Ensure pubchem_cid is numeric
        if "pubchem_cid" in save_df.columns:
            save_df["pubchem_cid"] = pd.to_numeric(
                save_df["pubchem_cid"], errors="coerce",
            )
        # Keep only columns present in EntityMapping
        model_cols = [
            "canonical_inchikey", "canonical_name", "chembl_id",
            "drugbank_id", "pubchem_cid", "uniprot_id", "string_id",
            "match_confidence", "match_method",
        ]
        for c in model_cols:
            if c not in save_df.columns:
                save_df[c] = None
        save_df = save_df[model_cols]

        # Transactional: temp table + DELETE/INSERT — atomic, rolls back on failure.
        # v9 ROOT FIX (audit F3.5): TRUNCATE TABLE is PostgreSQL-specific
        # syntax. On SQLite-backed dev/test environments it raises
        # sqlite3.OperationalError. Use DELETE FROM which is universally
        # supported (ANSI SQL) and behaves correctly within an explicit
        # transaction on both dialects.
        engine = get_engine()
        with engine.begin() as conn:
            save_df.to_sql(
                "_tmp_entity_mapping_staging",
                con=conn,
                if_exists="replace",
                index=False,
                method="multi",
                chunksize=5000,
            )
            conn.execute(text("DELETE FROM entity_mapping"))
            conn.execute(text("""
                INSERT INTO entity_mapping
                    (canonical_inchikey, canonical_name, chembl_id,
                     drugbank_id, pubchem_cid, uniprot_id, string_id,
                     match_confidence, match_method)
                SELECT
                    canonical_inchikey, canonical_name, chembl_id,
                    drugbank_id, pubchem_cid, uniprot_id, string_id,
                    match_confidence, match_method
                FROM _tmp_entity_mapping_staging
            """))
            conn.execute(text("DROP TABLE IF EXISTS _tmp_entity_mapping_staging"))
        logger.info(
            "Persisted %d drug entity mappings to database",
            len(drug_mapping_df),
        )

    # ------------------------------------------------------------------
    # Update proteins.string_id from protein resolution results
    # ------------------------------------------------------------------
    proteins_updated = 0
    if not protein_mapping_df.empty and "string_id" in protein_mapping_df.columns:
        resolved = protein_mapping_df.dropna(subset=["string_id"])
        if not resolved.empty:
            update_df = resolved[["uniprot_id", "string_id"]].copy()
            update_df = update_df.dropna(subset=["uniprot_id", "string_id"])
            if not update_df.empty:
                engine = get_engine()
                with engine.begin() as conn:
                    update_df.to_sql(
                        "_tmp_protein_string_update", con=conn,
                        if_exists="replace", index=False,
                        method="multi", chunksize=5000,
                    )
                    # v75 ROOT FIX (T-025 compound): the PostgreSQL UPDATE
                    # ... FROM syntax is not valid on SQLite. SQLite uses
                    # UPDATE ... SET col = (SELECT ...) WHERE EXISTS.
                    # Detect the dialect and dispatch the right SQL.
                    dialect_name = engine.dialect.name
                    if dialect_name == "sqlite":
                        conn.execute(text("""
                            UPDATE proteins
                            SET string_id = (
                                SELECT t.string_id
                                FROM _tmp_protein_string_update t
                                WHERE t.uniprot_id = proteins.uniprot_id
                            )
                            WHERE EXISTS (
                                SELECT 1 FROM _tmp_protein_string_update t
                                WHERE t.uniprot_id = proteins.uniprot_id
                            )
                            AND string_id IS NULL
                        """))
                    else:
                        conn.execute(text("""
                            UPDATE proteins p
                            SET string_id = t.string_id
                            FROM _tmp_protein_string_update t
                            WHERE p.uniprot_id = t.uniprot_id
                            AND p.string_id IS NULL
                        """))
                    conn.execute(text("DROP TABLE IF EXISTS _tmp_protein_string_update"))
                proteins_updated = len(resolved)
                logger.info(
                    "Updated string_id for %d proteins", proteins_updated,
                )

    logger.info("Entity resolution pipeline complete")
    return {
        "drug_mappings": len(drug_mapping_df),
        "protein_mappings": len(protein_mapping_df),
        "proteins_updated": proteins_updated,
    }


if __name__ == "__main__":
    # Allow ``python -m entity_resolution.run`` as a CLI entry point.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    result = run_entity_resolution()
    print(f"Entity resolution result: {result}")
