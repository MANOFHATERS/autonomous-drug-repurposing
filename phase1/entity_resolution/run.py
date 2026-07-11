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
from typing import Any, Dict, Set

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
                # v89 FORENSIC ROOT FIX (BUG #16 P1 — UniProt/STRING column
                #   detection used DIFFERENT variant lists):
                #   The previous code had TWO separate variant lists:
                #     - _COLUMN_PAIR_VARIANTS (5 pairs) for UniProt IDs
                #     - _string_col_variants (3 pairs) for STRING IDs
                #   If the UniProt pair was ``uniprot_id_a/uniprot_id_b``
                #   but the STRING pair was ``protein_a/protein_b`` (NOT in
                #   _string_col_variants), the STRING ID pairing was
                #   silently skipped. ``uniprot_to_string_id`` remained
                #   empty, and the organism inference (Source 3 in
                #   build_mapping) never fired — non-human proteins were
                #   mislabeled as "Homo sapiens" (BUG #18).
                #   ROOT FIX: UNIFY the column-pair detection. Each
                #   variant entry is a 4-tuple
                #   ``(uniprot_a, uniprot_b, string_a, string_b)``. We
                #   pick the FIRST variant whose UniProt pair is present;
                #   the STRING pair from the SAME variant is used for
                #   STRING ID extraction. If the chosen variant's STRING
                #   pair is NOT present in the DataFrame, we log a
                #   WARNING (so operators notice) but still extract the
                #   UniProt IDs (organism inference will fall back to
                #   the override table).
                _COLUMN_PAIR_VARIANTS = [
                    # (uniprot_a, uniprot_b, string_a, string_b)
                    ("uniprot_id_a", "uniprot_id_b", "string_protein_a", "string_protein_b"),
                    ("uniprot_a", "uniprot_b", "string_protein_a", "string_protein_b"),
                    ("uniprot_ac_a", "uniprot_ac_b", "string_protein_a", "string_protein_b"),
                    ("uniprot_id1", "uniprot_id2", "string_protein_a", "string_protein_b"),
                    # Legacy variant: some emitters used protein_a/protein_b
                    # for BOTH UniProt (when no uniprot_* cols) and STRING.
                    ("string_protein_a", "string_protein_b", "string_protein_a", "string_protein_b"),
                ]
                col_a, col_b = None, None
                _string_col_a, _string_col_b = None, None
                for _ca, _cb, _sa, _sb in _COLUMN_PAIR_VARIANTS:
                    if _ca in string_df.columns and _cb in string_df.columns:
                        col_a, col_b = _ca, _cb
                        # Use the STRING pair from the SAME variant.
                        if _sa in string_df.columns and _sb in string_df.columns:
                            _string_col_a, _string_col_b = _sa, _sb
                        logger.info(
                            "Found STRING UniProt ID columns: %s / %s "
                            "(STRING ID columns: %s / %s)",
                            col_a, col_b,
                            _string_col_a or "<none>", _string_col_b or "<none>",
                        )
                        break
                if col_a and col_b:
                    uniprot_ids = set()
                    # v89 FORENSIC ROOT FIX (BUG #7 P1 — uniprot_to_string_id
                    #   overwrote previous value, last STRING ID won):
                    #   The previous code used a single-valued dict
                    #   ``uniprot_to_string_id[uid_str] = sid_str``. If
                    #   the same UniProt accession appeared in multiple
                    #   STRING PPI rows (common — a protein has many
                    #   interaction partners), the dictionary assignment
                    #   overwrote the previous value. The LAST STRING ID
                    #   encountered won, with no consistency check. If
                    #   uid_str was paired with sid_a in row 1 (human)
                    #   and sid_b in row 2 (mouse, due to BUG #2
                    #   mispairing), the final mapping was uid_str →
                    #   sid_b — non-deterministic, depending on row
                    #   ordering.
                    #   ROOT FIX: use a MULTI-VALUED dict
                    #   ``uniprot_to_string_ids: dict[str, set[str]]``.
                    #   After collecting all pairings, VALIDATE that all
                    #   STRING IDs paired with the same UniProt accession
                    #   share the SAME taxonomy prefix (the part before
                    #   the first "."). If they conflict (e.g. one human
                    #   9606.* and one mouse 10090.*), the UniProt
                    #   accession is AMBIGUOUS — we DEAD-LETTER it (log
                    #   a WARNING and exclude it from the string_id
                    #   column) so the resolver's organism inference
                    #   (Source 3) does not pick a random taxonomy.
                    uniprot_to_string_ids: Dict[str, set] = {}
                    # v89 FORENSIC ROOT FIX (BUG #2 P0 — STRING ID
                    #   mispairing):
                    #   The previous code iterated
                    #   ``for col in (col_a, col_b):`` and for EACH col
                    #   checked ``for scol in (_string_col_a,
                    #   _string_col_b):``. For uid_b (from col_b), it
                    #   ALSO checked _string_col_a FIRST, found sid_a
                    #   (the SAME value as for uid_a), and broke —
                    #   WRONG. uid_b should be paired with sid_b (from
                    #   _string_col_b), not sid_a. The result: BOTH
                    #   UniProt accessions in a PPI edge were paired
                    #   with the SAME STRING ID (the one from column A).
                    #   In a cross-species PPI edge
                    #   (human ↔ mouse), both UniProt accessions got
                    #   paired with the human STRING ID, so the mouse
                    #   UniProt accession was labeled "Homo sapiens" via
                    #   taxonomy-prefix inference — corrupting organism
                    #   assignment.
                    #   ROOT FIX: iterate
                    #   ``zip((col_a, col_b), (_string_col_a,
                    #   _string_col_b))`` so uid_a pairs with sid_a and
                    #   uid_b pairs with sid_b EXPLICITLY. No inner loop.
                    if _string_col_a and _string_col_b:
                        for idx in string_df.index:
                            for _col, _scol in zip((col_a, col_b), (_string_col_a, _string_col_b)):
                                uid = string_df.at[idx, _col]
                                if pd.isna(uid):
                                    continue
                                uid_str = str(uid).strip()
                                if not uid_str or uid_str == "nan":
                                    continue
                                # Normalize UniProt accession to UPPERCASE
                                # (per UniProt spec — accessions are
                                # case-sensitive and MUST be uppercase).
                                # This prevents duplicate canonical
                                # entries for the same protein (v89
                                # BUG #4 in protein_resolver.py).
                                uid_str = uid_str.upper()
                                uniprot_ids.add(uid_str)
                                sid = string_df.at[idx, _scol]
                                if pd.notna(sid):
                                    sid_str = str(sid).strip()
                                    if sid_str and "." in sid_str:
                                        uniprot_to_string_ids.setdefault(uid_str, set()).add(sid_str)
                    else:
                        # No STRING ID columns — just collect UniProt IDs.
                        for idx in string_df.index:
                            for _col in (col_a, col_b):
                                uid = string_df.at[idx, _col]
                                if pd.isna(uid):
                                    continue
                                uid_str = str(uid).strip()
                                if not uid_str or uid_str == "nan":
                                    continue
                                uid_str = uid_str.upper()
                                uniprot_ids.add(uid_str)

                    # v89 BUG #7: validate taxonomy-prefix consistency
                    # for each UniProt accession's set of STRING IDs.
                    uniprot_to_string_id: Dict[str, str] = {}
                    _dead_lettered_uids: list = []
                    for _uid, _sids in uniprot_to_string_ids.items():
                        _taxids = {_s.split(".")[0] for _s in _sids if "." in _s}
                        if len(_taxids) > 1:
                            # Conflicting taxonomy prefixes — dead-letter.
                            logger.warning(
                                "STRING PPI: UniProt accession %s paired "
                                "with STRING IDs from MULTIPLE taxa (%s) — "
                                "organism is ambiguous. Excluding from "
                                "string_id column to prevent cross-species "
                                "contamination.",
                                _uid, sorted(_taxids),
                            )
                            _dead_lettered_uids.append(_uid)
                        elif len(_taxids) == 1:
                            # Consistent — pick the first (deterministic).
                            uniprot_to_string_id[_uid] = sorted(_sids)[0]
                        # else: no valid taxonomy prefix — skip (will be
                        # handled by the resolver's default-organism path).

                    if uniprot_ids:
                        _data = {"uniprot_id": list(uniprot_ids)}
                        # Include string_id column if we have any pairings.
                        if uniprot_to_string_id:
                            _data["string_id"] = [
                                uniprot_to_string_id.get(uid)
                                for uid in uniprot_ids
                            ]
                        string_protein_df = pd.DataFrame(_data)
                        logger.info(
                            "Extracted %d unique UniProt IDs from STRING PPI data"
                            " (%d with paired STRING IDs for organism inference,"
                            " %d dead-lettered for conflicting taxa)",
                            len(string_protein_df),
                            len(uniprot_to_string_id),
                            len(_dead_lettered_uids),
                        )
                else:
                    logger.warning(
                        "STRING PPI file %s has no recognized UniProt ID "
                        "column pair. Checked variants: %s. Available "
                        "columns: %s. PPI subgraph will be empty.",
                        string_path,
                        [f"{a}/{b}" for a, b, _sa, _sb in _COLUMN_PAIR_VARIANTS],
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
            # v89 FORENSIC ROOT FIX (BUG #3 P0 — alias file glob matched
            #   NON-HUMAN organism files):
            #   The previous code used ``*aliases*.txt.gz`` which matched
            #   ANY aliases file in the STRING raw directory — including
            #   non-human organism files (10090.protein.aliases.v12.0.txt.gz
            #   = mouse, 7227.protein.aliases.v12.0.txt.gz = fly). The
            #   code picked ``_alias_files[0]`` (alphabetically first by
            #   default glob ordering). If 10090.protein.aliases... sorted
            #   before 9606.protein.aliases..., the MOUSE aliases file was
            #   loaded — and every UniProt accession in it was treated as
            #   a mouse-to-STRING mapping. All protein mappings were
            #   wrong. The organism override table (~250 entries) did NOT
            #   catch this because most UniProt accessions are not in the
            #   table. Mouse/fly/worm proteins were provisionally entered
            #   as human and merged into human PPI subgraphs.
            #   ROOT FIX: glob SPECIFICALLY for the HUMAN aliases file
            #   (taxonomy ID 9606): ``9606.protein.aliases.*.txt.gz``.
            #   This guarantees only the human aliases file is loaded.
            #   If the human file is not present, log a WARNING (do NOT
            #   fall back to a non-human file).
            _alias_files = (
                list(_string_raw_dir.glob("9606.protein.aliases.*.txt.gz"))
                if _string_raw_dir.exists()
                else []
            )
            if _alias_files:
                _alias_file = _alias_files[0]
                # v89 FORENSIC ROOT FIX (BUG #17 P1 — fragile raw aliases
                #   parsing):
                #   The previous code split each line on tab (or
                #   whitespace as fallback) and took the first 4 fields.
                #   Problems:
                #   (a) No header validation — STRING could change the
                #       column order and we'd silently produce wrong
                #       mappings.
                #   (b) The fallback ``_line.split()`` splits on ANY
                #       whitespace, which would break if an alias
                #       contains spaces.
                #   (c) The filter ``"UniProt" in _src_db or
                #       _source == "UniProt_AC"`` was case-sensitive —
                #       STRING uses ``UniProt_AC`` (exact) but some
                #       files use ``uniprot_ac`` (lowercase).
                #   ROOT FIX: use pandas with explicit ``sep="\t"`` and
                #   ``names=[...]`` for robust parsing. Validate the
                #   header comment (STRING aliases files start with a
                #   ``#`` header line). Make the UniProt filter
                #   case-insensitive (lowercase both sides). Skip lines
                #   that don't have exactly 4 fields after splitting
                #   (defensive — corrupt lines are logged and skipped).
                import gzip
                _alias_records = []
                _skipped_lines = 0
                with gzip.open(_alias_file, "rt", encoding="utf-8") as _af:
                    _header_seen = False
                    for _line in _af:
                        _line = _line.rstrip("\n").rstrip("\r")
                        if not _line:
                            continue
                        if _line.startswith("#"):
                            # Header comment — STRING aliases files have
                            # a ``#`` line describing the columns. Mark
                            # that we've seen it (so we know the file is
                            # well-formed) but don't parse it.
                            _header_seen = True
                            continue
                        # STRICT tab split — do NOT fall back to
                        # whitespace split (BUG #17b: whitespace split
                        # breaks on aliases containing spaces).
                        _parts = _line.split("\t")
                        if len(_parts) < 4:
                            _skipped_lines += 1
                            continue
                        _string_id = _parts[0]
                        _source = _parts[1]
                        _alias = _parts[2]
                        _src_db = _parts[3]
                        # Case-insensitive UniProt filter (BUG #17c).
                        # STRING uses "UniProt_AC" (exact) but some
                        # emitters use "uniprot_ac" or "UniProt_AC_ID".
                        _src_db_lower = _src_db.lower()
                        _source_lower = _source.lower()
                        if "uniprot" in _src_db_lower or _source_lower == "uniprot_ac":
                            _alias_records.append({
                                "string_id": _string_id,
                                "uniprot_id": _alias,
                                "source": _source,
                                "source_database": _src_db,
                            })
                if not _header_seen:
                    logger.warning(
                        "STRING aliases file %s has no '#' header comment — "
                        "file format may have changed. Proceeding with "
                        "best-effort parsing.",
                        _alias_file.name,
                    )
                if _skipped_lines:
                    logger.warning(
                        "STRING aliases file %s: skipped %d lines with "
                        "fewer than 4 tab-separated fields",
                        _alias_file.name, _skipped_lines,
                    )
                if _alias_records:
                    string_aliases_df = pd.DataFrame(_alias_records)
                    logger.info(
                        "Loaded STRING aliases from raw file %s: %d UniProt mappings",
                        _alias_file.name, len(string_aliases_df),
                    )
            else:
                # v89 BUG #3: no human aliases file found — log clearly.
                if _string_raw_dir.exists():
                    _all_alias_files = list(_string_raw_dir.glob("*aliases*.txt.gz"))
                    logger.warning(
                        "No HUMAN (9606) STRING aliases file found in %s. "
                        "Found %d non-human alias files: %s. "
                        "REFUSING to load non-human aliases (would corrupt "
                        "organism assignment). string_aliases_df will be "
                        "empty — resolve_single(string_id=...) will not "
                        "resolve STRING IDs to UniProt.",
                        _string_raw_dir,
                        len(_all_alias_files),
                        [f.name for f in _all_alias_files[:5]],
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
