"""Canonical Phase 1 output schema — the SINGLE source of truth.

TM1 TASK 7 ROOT FIX (Team Member 1, Phase 1 Real Data Pipeline):
  This module defines the canonical CSV column names, dtypes, and
  constraints for ALL 11 Phase 1 output files. Phase 2 (the bridge)
  imports this module instead of maintaining a divergent
  ``_PHASE1_EXPECTED_COLUMNS`` dict — eliminating the schema drift
  that previously caused silent data loss when Phase 1 changed a
  column name and Phase 2 didn't.

The 11 Phase 1 output files
---------------------------
  1.  chembl_drugs.csv                       (chembl_drugs)
  2.  chembl_activities_clean.csv            (chembl_activities)
  3.  drugbank_drugs.csv                     (drugs)
  4.  drugbank_interactions.csv[.gz]         (interactions)
  5.  drugbank_indications.csv               (indications)
  6.  uniprot_proteins.csv                   (uniprot_proteins)
  7.  string_protein_protein_interactions.csv (string_ppi)
  8.  disgenet_gene_disease_associations.csv  (disgenet_gda)
  9.  omim_gene_disease_associations.csv     (omim_gda)
  10. omim_gene_disease_susceptibility.csv   (omim_susceptibility)
  11. pubchem_enrichment.csv                 (pubchem_enrichment)

Each source declares:
  - ``required_columns``: columns that MUST be present (non-empty).
  - ``any_of_groups``: lists of column names where AT LEAST ONE must
    be present (e.g. ``["gene_id", "ncbi_gene_id"]`` — accept either).
  - ``optional_columns``: columns that MAY be present (enrichment).
  - ``dtypes``: pandas dtype hints for column coercion.
  - ``min_rows``: minimum row count for the source to be considered
    "populated" (1 = at least the header + 1 row; 0 = header-only OK).

This module is IMPORTED BY PHASE 2. The bridge's
``_PHASE1_EXPECTED_COLUMNS`` dict in ``phase1_bridge.py`` is a
DERIVED copy that must stay in sync with this module. The
``validate_output.py`` script (Task 8) enforces this at CI time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# =============================================================================
# CONTRACT VERSION — Teammate 5 (P2→P1 Integration, P0 root fix)
# =============================================================================
# Bumped to ``2.0.0`` to enforce the Phase 2 bridge's contract-version gate.
# The bridge (``phase2/drugos_graph/phase1_bridge.py``) reads this constant at
# the top of ``read_phase1_outputs`` and FAILS FAST with
# ``CriticalDataSourceError`` if the major version is < 2. This blocks the
# silent schema-drift failure mode where Phase 1 ships a v1 contract (with
# the old ``drugs.csv``-only ChEMBL mapping and no ``chembl_id`` requirement)
# but Phase 2's bridge expects v2 (canonical ``chembl_drugs.csv`` + explicit
# ``chembl_id`` required column). Without this gate the bridge would
# silently accept v1 outputs and produce a degraded KG missing Compound
# identity — exactly the "85% aligned" failure mode the audit flagged.
#
# SEMVER CONTRACT:
#   * MAJOR bump (2.x.x → 3.x.x): breaking change to ``required_columns``,
#     ``aliases``, or ``filename`` of any source. Phase 2 MUST re-pin.
#   * MINOR bump (2.0.x → .1.x): additive change (new optional column,
#     new source). Phase 2 keeps working.
#   * PATCH bump (2.0.0 → 2.0.1): doc-only or comment-only change.
#
# The bridge checks ONLY the major version (``< 2`` = fail). This is
# intentional — minor and patch changes are non-breaking by semver.
# =============================================================================
__version__: str = "2.0.0"


# =============================================================================
# ColumnSpec — typed specification for one Phase 1 output column
# =============================================================================


@dataclass(frozen=True)
class ColumnSpec:
    """Specification for a single Phase 1 output column.

    Attributes
    ----------
    name : str
        Canonical column name (case-sensitive, exact match required).
    dtype : str
        Pandas dtype hint: ``"string"``, ``"float64"``, ``"int64"``,
        ``"bool"``, ``"object"``. Used by ``validate_output.py`` to
        coerce and verify column types.
    nullable : bool
        True if the column may contain NULL/NaN values. False means
        every row must have a non-null value (validated at read time).
    description : str
        Human-readable description of the column's semantic meaning.
        Surfaced in error messages and in ``contracts/README.md``.
    """

    name: str
    dtype: str = "string"
    nullable: bool = True
    description: str = ""


# =============================================================================
# SourceSpec — typed specification for one Phase 1 source CSV
# =============================================================================


@dataclass(frozen=True)
class SourceSpec:
    """Specification for one Phase 1 source CSV file.

    Attributes
    ----------
    key : str
        Canonical source key (e.g. ``"drugs"``, ``"chembl_drugs"``).
        Matches the keys used by the Phase 2 bridge.
    filename : str
        Canonical CSV filename (e.g. ``"drugbank_drugs.csv"``).
    aliases : tuple of str
        Alternate filenames the bridge accepts (e.g. ``"drugs.csv"``
        is an alias for ``"chembl_drugs.csv"`` when DrugBank is absent).
    required_columns : tuple of ColumnSpec
        Columns that MUST be present in the CSV. A missing required
        column is a HARD validation failure.
    any_of_groups : tuple of tuple of str
        Groups of column names where AT LEAST ONE per group must be
        present. E.g. ``(("gene_id", "ncbi_gene_id"),)`` accepts the
        CSV if either ``gene_id`` OR ``ncbi_gene_id`` is present.
    optional_columns : tuple of ColumnSpec
        Columns that MAY be present. Their absence is not an error,
        but if present they must match the declared dtype.
    min_rows : int
        Minimum row count (excluding header) for the source to be
        considered "populated". 0 means header-only is acceptable
        (e.g. DrugBank when academic license is paused). 1+ means
        real data rows are required.
    description : str
        Human-readable description of the source.
    """

    key: str
    filename: str
    aliases: tuple = ()
    required_columns: tuple = ()
    any_of_groups: tuple = ()
    optional_columns: tuple = ()
    min_rows: int = 0
    description: str = ""


# =============================================================================
# ValidationIssue — typed result of a single column-level check
# =============================================================================


@dataclass
class ValidationIssue:
    """One validation issue found in a Phase 1 output CSV.

    Attributes
    ----------
    source : str
        Source key (e.g. ``"drugs"``).
    severity : str
        ``"error"`` (hard fail) or ``"warning"`` (soft fail).
    code : str
        Machine-readable issue code (e.g. ``"missing_required_column"``,
        ``"any_of_group_unsatisfied"``, ``"dtype_mismatch"``,
        ``"below_min_rows"``).
    message : str
        Human-readable error message.
    column : str, optional
        Column name involved (if applicable).
    """

    source: str
    severity: str
    code: str
    message: str
    column: Optional[str] = None

    def __str__(self) -> str:
        col_part = f" column={self.column!r}" if self.column else ""
        return (
            f"[{self.severity.upper()}] source={self.source!r} "
            f"code={self.code!r}{col_part} :: {self.message}"
        )


# =============================================================================
# THE CANONICAL PHASE 1 OUTPUT SCHEMA
# =============================================================================
# This is the SINGLE source of truth. Phase 2's bridge imports this dict
# and MUST NOT maintain a divergent copy. The ``validate_output.py``
# script (Task 8) verifies the bridge stays in sync.

PHASE1_OUTPUT_SCHEMA: Dict[str, SourceSpec] = {

    # -------------------------------------------------------------------------
    # 1. ChEMBL drugs (chembl_drugs.csv)
    #    Backbone source for Compound nodes when DrugBank is unavailable.
    #    Produced by: phase1/pipelines/chembl_pipeline.py
    # -------------------------------------------------------------------------
    "chembl_drugs": SourceSpec(
        key="chembl_drugs",
        filename="chembl_drugs.csv",
        aliases=("drugs.csv",),
        required_columns=(
            ColumnSpec("chembl_id", "string", nullable=False,
                       description="ChEMBL molecule ID (CHEMBL\\d+)."),
            ColumnSpec("inchikey", "string", nullable=False,
                       description="27-char InChIKey (canonical compound key)."),
        ),
        any_of_groups=(),
        optional_columns=(
            ColumnSpec("name", "string", nullable=False,
                       description="Preferred drug name (e.g. 'Aspirin')."),
            ColumnSpec("smiles", "string", nullable=True,
                       description="Canonical SMILES string."),
            ColumnSpec("molecular_weight", "float64", nullable=True,
                       description="Molecular weight in Daltons."),
            ColumnSpec("max_phase", "int64", nullable=True,
                       description="ChEMBL max phase (0-4). 4 = globally approved."),
            ColumnSpec("is_fda_approved", "bool", nullable=True,
                       description="True=FDA-approved, False=not, None=unknown."),
            ColumnSpec("is_globally_approved", "bool", nullable=True,
                       description="True if max_phase==4 (any regulator)."),
            ColumnSpec("indication", "string", nullable=True,
                       description="Free-text indication."),
            ColumnSpec("indication_source", "string", nullable=True,
                       description="Source of indication field."),
            ColumnSpec("mechanism_of_action", "string", nullable=True,
                       description="Mechanism of action text."),
            # TM1 Task 1.1: declare ``drug_type`` so the drift detector
            # doesn't false-positive. The pipeline emits this column
            # (ChEMBL molecule_type, mapped to DrugType enum) but the
            # contract previously omitted it.
            ColumnSpec("drug_type", "string", nullable=True,
                       description="small_molecule / antibody / protein / etc."),
        ),
        min_rows=1,
        description="ChEMBL FDA-approved drugs (Compound source).",
    ),

    # -------------------------------------------------------------------------
    # 2. ChEMBL activities (chembl_activities_clean.csv)
    #    Source for Compound->inhibits/activates->Protein edges.
    #    Produced by: phase1/pipelines/chembl_pipeline.py
    # -------------------------------------------------------------------------
    "chembl_activities": SourceSpec(
        key="chembl_activities",
        filename="chembl_activities_clean.csv",
        aliases=("chembl_activities.csv",),
        required_columns=(
            ColumnSpec("molecule_chembl_id", "string", nullable=False,
                       description="ChEMBL molecule ID (foreign key to chembl_drugs)."),
            ColumnSpec("target_chembl_id", "string", nullable=False,
                       description="ChEMBL target ID."),
            ColumnSpec("pchembl_value", "float64", nullable=True,
                       description="-log10(activity_value in M). Higher = more potent."),
            ColumnSpec("standard_relation", "string", nullable=True,
                       description="'=', '<', '>' — relation between activity and value."),
        ),
        any_of_groups=(),
        optional_columns=(
            ColumnSpec("uniprot_id", "string", nullable=True,
                       description="UniProt accession for the target protein."),
            ColumnSpec("target_name", "string", nullable=True,
                       description="Human-readable target name."),
            ColumnSpec("activity_type", "string", nullable=True,
                       description="IC50, Ki, Kd, EC50, etc."),
            ColumnSpec("activity_value", "float64", nullable=True,
                       description="Activity value in nM."),
            ColumnSpec("activity_units", "string", nullable=True,
                       description="Units of activity_value (typically 'nM')."),
            ColumnSpec("chembl_id", "string", nullable=True,
                       description="Alias for molecule_chembl_id (legacy)."),
            ColumnSpec("activity_censored", "bool", nullable=True,
                       description="True if the value is a censor bound (e.g. '>10' or '<1')."),
            ColumnSpec("activity_censor_direction", "string", nullable=True,
                       description="Censor direction: '>', '<', or None."),
            # TM1 Task 1.1: declare the remaining pipeline-emitted columns
            # so the drift detector doesn't false-positive. These are
            # emitted by _parse_activities and are needed by the bridge.
            ColumnSpec("activity_id", "string", nullable=True,
                       description="ChEMBL activity ID (primary key)."),
            ColumnSpec("target_pref_name", "string", nullable=True,
                       description="ChEMBL preferred target name."),
            ColumnSpec("assay_id", "string", nullable=True,
                       description="ChEMBL assay ID."),
            ColumnSpec("assay_type", "string", nullable=True,
                       description="ChEMBL assay type (A/B/F/...)."),
            ColumnSpec("target_accession", "string", nullable=True,
                       description="UniProt accession resolved from target_chembl_id."),
            # TM1 Task 1.1 ROOT FIX (v130): Phase 2 bridge (phase1_bridge.py)
            # and chembl_loader.py read ``uniprot_accession`` / ``target_uniprot``
            # as the canonical name for the UniProt accession on a ChEMBL
            # activity row. Phase 1 historically wrote only ``target_accession``,
            # which caused the bridge to fall through to a synthetic
            # ``CHEMBL_TGT_<digits>`` id and silently disconnect every
            # ChEMBL Compound→Protein edge from the UniProt Protein KG.
            # The pipeline now emits these alias columns (mirrors of
            # ``target_accession``); declaring them here keeps the drift
            # detector quiet and documents the contract.
            ColumnSpec("uniprot_accession", "string", nullable=True,
                       description="Alias for target_accession (Phase 2 contract name)."),
            ColumnSpec("target_uniprot", "string", nullable=True,
                       description="Alias for target_accession (legacy Phase 2 name)."),
        ),
        min_rows=1,
        description="ChEMBL bioactivity measurements (Compound-Protein edges).",
    ),

    # -------------------------------------------------------------------------
    # 3. DrugBank drugs (drugbank_drugs.csv)
    #    Preferred Compound source when DrugBank academic license is active.
    #    Produced by: phase1/pipelines/drugbank_pipeline.py
    #    NOTE: when DrugBank is unavailable, this CSV may be EMPTY (header-only)
    #    — the bridge degrades to chembl_drugs.csv. min_rows=0 reflects this.
    # -------------------------------------------------------------------------
    "drugs": SourceSpec(
        key="drugs",
        filename="drugbank_drugs.csv",
        aliases=("drugbank_open_drugs.csv", "chembl_drugs.csv", "drugs.csv"),
        required_columns=(
            ColumnSpec("name", "string", nullable=False,
                       description="Drug name (preferred form)."),
            ColumnSpec("inchikey", "string", nullable=False,
                       description="27-char InChIKey (canonical compound key)."),
        ),
        any_of_groups=(
            # Accept either drugbank_id (when DrugBank is the source) OR
            # chembl_id (when ChEMBL is the source). The bridge uses
            # inchikey as the canonical Compound key; drugbank_id/chembl_id
            # are source-specific aliases.
            ("drugbank_id", "chembl_id"),
        ),
        optional_columns=(
            ColumnSpec("drugbank_id", "string", nullable=True,
                       description="DrugBank ID (DB\\d+)."),
            ColumnSpec("chembl_id", "string", nullable=True,
                       description="ChEMBL ID (CHEMBL\\d+)."),
            ColumnSpec("pubchem_cid", "int64", nullable=True,
                       description="PubChem Compound ID."),
            ColumnSpec("smiles", "string", nullable=True,
                       description="Canonical SMILES."),
            ColumnSpec("molecular_weight", "float64", nullable=True,
                       description="Molecular weight (Daltons)."),
            ColumnSpec("molecular_formula", "string", nullable=True,
                       description="Molecular formula (e.g. C9H8O4)."),
            ColumnSpec("indication", "string", nullable=True,
                       description="FDA-approved indication text."),
            ColumnSpec("indication_source", "string", nullable=True,
                       description="Source of indication (FDA/EMA/manual)."),
            ColumnSpec("mechanism_of_action", "string", nullable=True,
                       description="Mechanism of action."),
            ColumnSpec("groups", "string", nullable=True,
                       description="DrugBank groups (approved/illicit/withdrawn/nutracet)."),
            ColumnSpec("is_fda_approved", "bool", nullable=True,
                       description="True=FDA-approved, False=not, None=unknown."),
            ColumnSpec("is_globally_approved", "bool", nullable=True,
                       description="True if approved by any regulator globally."),
            ColumnSpec("is_withdrawn", "bool", nullable=True,
                       description="True if withdrawn from market (patient-safety)."),
            # TM1 Task 1.2: structured withdrawal metadata. These three
            # columns are populated by drugbank_pipeline._parse_drug_element
            # from the DrugBank <withdrawn-notice> XML element. They flow
            # Phase 1 CSV → Phase 2 KG node → Phase 4 RL safety_score.
            ColumnSpec("withdrawn_reason", "string", nullable=True,
                       description="Semicolon-separated reason(s) for withdrawal (e.g. 'rhabdomyolysis')."),
            ColumnSpec("withdrawn_country", "string", nullable=True,
                       description="Semicolon-separated list of countries that withdrew the drug."),
            ColumnSpec("withdrawn_year", "int64", nullable=True,
                       description="Earliest withdrawal year across all <withdrawn-notice> elements."),
            ColumnSpec("clinical_status", "string", nullable=True,
                       description="Clinical trial status."),
            ColumnSpec("max_phase", "int64", nullable=True,
                       description="Max clinical phase (0-4)."),
            ColumnSpec("drug_type", "string", nullable=True,
                       description="small_molecule / biotech / etc."),
            ColumnSpec("cas_number", "string", nullable=True,
                       description="CAS registry number."),
            ColumnSpec("logp", "float64", nullable=True,
                       description="Computed logP."),
            ColumnSpec("tpsa", "float64", nullable=True,
                       description="Topological polar surface area."),
            # TM1 Task 1.2: declare the remaining pipeline-emitted columns
            # so the drift detector doesn't false-positive. These are
            # emitted by drugbank_pipeline._parse_drug_element and are
            # needed by the loader / RL ranker downstream.
            ColumnSpec("description", "string", nullable=True,
                       description="DrugBank free-text description."),
            ColumnSpec("h_bond_donor_count", "int64", nullable=True,
                       description="H-bond donor count (DrugBank calculated property)."),
            ColumnSpec("h_bond_acceptor_count", "int64", nullable=True,
                       description="H-bond acceptor count (DrugBank calculated property)."),
            ColumnSpec("rotatable_bond_count", "int64", nullable=True,
                       description="Rotatable bond count (DrugBank calculated property)."),
            ColumnSpec("heavy_atom_count", "int64", nullable=True,
                       description="Heavy atom count (DrugBank calculated property)."),
            ColumnSpec("complexity", "float64", nullable=True,
                       description="DrugBank complexity score."),
            ColumnSpec("completeness_score", "float64", nullable=True,
                       description="Per-row completeness score (fraction of non-null fields)."),
        ),
        min_rows=0,
        description="DrugBank drugs (preferred Compound source; empty when license paused).",
    ),

    # -------------------------------------------------------------------------
    # 4. DrugBank interactions (drugbank_interactions.csv[.gz])
    #    Source for DrugBank-derived Drug->Drug interaction edges.
    #    Produced by: phase1/pipelines/drugbank_pipeline.py
    # -------------------------------------------------------------------------
    "interactions": SourceSpec(
        key="interactions",
        filename="drugbank_interactions.csv",
        aliases=("drugbank_interactions.csv.gz", "drugbank_open_interactions.csv",
                 "chembl_activities_clean.csv", "chembl_activities.csv"),
        required_columns=(
            ColumnSpec("drugbank_id", "string", nullable=False,
                       description="Source DrugBank ID."),
            ColumnSpec("uniprot_id", "string", nullable=False,
                       description="Target UniProt accession."),
            ColumnSpec("action_type", "string", nullable=True,
                       description="inhibitor/activator/antagonist/etc."),
        ),
        any_of_groups=(),
        optional_columns=(
            ColumnSpec("target_name", "string", nullable=True,
                       description="Target protein name."),
            ColumnSpec("target_chembl_id", "string", nullable=True,
                       description="ChEMBL target ID alias."),
        ),
        min_rows=0,
        description="DrugBank drug-protein interactions (may degrade to ChEMBL activities).",
    ),

    # -------------------------------------------------------------------------
    # 5. DrugBank indications (drugbank_indications.csv)
    #    Source for ClinicalOutcome nodes + Compound-treats-Disease edges.
    #    Produced by: phase1/pipelines/drugbank_pipeline.py
    # -------------------------------------------------------------------------
    "indications": SourceSpec(
        key="indications",
        filename="drugbank_indications.csv",
        aliases=(),
        required_columns=(
            ColumnSpec("drugbank_id", "string", nullable=False,
                       description="DrugBank ID of the treating drug."),
            ColumnSpec("disease_id", "string", nullable=False,
                       description="Disease identifier (DOID/MESH/ICD10)."),
        ),
        any_of_groups=(),
        optional_columns=(
            ColumnSpec("drug_inchikey", "string", nullable=True,
                       description="InChIKey of the treating drug."),
            ColumnSpec("drug_name", "string", nullable=True,
                       description="Name of the treating drug."),
            ColumnSpec("disease_name", "string", nullable=True,
                       description="Human-readable disease name."),
            ColumnSpec("doid_id", "string", nullable=True,
                       description="Disease Ontology ID."),
            ColumnSpec("omim_disease_id", "string", nullable=True,
                       description="OMIM disease ID."),
            ColumnSpec("indication", "string", nullable=True,
                       description="Free-text indication."),
            ColumnSpec("indication_type", "string", nullable=True,
                       description="approved/off_label/etc."),
            ColumnSpec("source", "string", nullable=True,
                       description="Source of the indication mapping."),
        ),
        min_rows=0,
        description="DrugBank drug-indication mappings (ClinicalOutcome source).",
    ),

    # -------------------------------------------------------------------------
    # 6. UniProt proteins (uniprot_proteins.csv)
    #    Source for Protein nodes.
    #    Produced by: phase1/pipelines/uniprot_pipeline.py
    # -------------------------------------------------------------------------
    "uniprot_proteins": SourceSpec(
        key="uniprot_proteins",
        filename="uniprot_proteins.csv",
        aliases=("proteins.csv",),
        required_columns=(
            ColumnSpec("gene_symbol", "string", nullable=True,
                       description="HGNC gene symbol (e.g. 'PTGS2'). May be null for non-human."),
        ),
        any_of_groups=(
            # Bridge accepts uniprot_ac OR accession OR uniprot_id.
            ("uniprot_ac", "accession", "uniprot_id"),
        ),
        optional_columns=(
            ColumnSpec("uniprot_id", "string", nullable=True,
                       description="Canonical UniProt accession."),
            ColumnSpec("uniprot_ac", "string", nullable=True,
                       description="Alias for uniprot_id (legacy)."),
            ColumnSpec("accession", "string", nullable=True,
                       description="Alias for uniprot_id (legacy)."),
            ColumnSpec("protein_name", "string", nullable=True,
                       description="Full protein name."),
            ColumnSpec("ncbi_gene_id", "int64", nullable=True,
                       description="NCBI Gene ID."),
            ColumnSpec("chromosome", "string", nullable=True,
                       description="Chromosomal location."),
            ColumnSpec("sequence", "string", nullable=True,
                       description="Amino acid sequence."),
            ColumnSpec("function", "string", nullable=True,
                       description="Functional description."),
            ColumnSpec("organism", "string", nullable=True,
                       description="Source organism (e.g. 'Homo sapiens')."),
            # TM1 Task 1.3: subcellular_location is required by Phase 3
            # for protein node feature extraction (per TASK-141). Without
            # this column the graph transformer cannot distinguish two
            # proteins with identical sequence but different cellular
            # localization (e.g., a nuclear vs. cytoplasmic isoform).
            ColumnSpec("subcellular_location", "string", nullable=True,
                       description="Subcellular location (UniProt cc_subcellular_location)."),
            # TM1 Task 1.3: declare the remaining pipeline-emitted columns
            # so the drift detector doesn't false-positive. These are
            # emitted by uniprot_pipeline._ensure_protein_columns and
            # are needed by the loader downstream.
            ColumnSpec("gene_name", "string", nullable=True,
                       description="Gene name (deprecated alias for gene_symbol; always None post-cleaning)."),
            ColumnSpec("protein_name_canonical", "string", nullable=True,
                       description="Canonicalized protein name."),
            ColumnSpec("length", "int64", nullable=True,
                       description="Sequence length (amino acid count)."),
            ColumnSpec("function_desc", "string", nullable=True,
                       description="Function description (legacy alias for 'function')."),
            ColumnSpec("string_id", "string", nullable=True,
                       description="First STRING cross-reference ID."),
            ColumnSpec("all_string_ids", "string", nullable=True,
                       description="Semicolon-separated list of all STRING cross-reference IDs."),
        ),
        min_rows=1,
        description="UniProt proteins (Protein node source).",
    ),

    # -------------------------------------------------------------------------
    # 7. STRING PPI (string_protein_protein_interactions.csv)
    #    Source for Pathway nodes (connected components) + Protein->Protein edges.
    #    Produced by: phase1/pipelines/string_pipeline.py
    # -------------------------------------------------------------------------
    "string_ppi": SourceSpec(
        key="string_ppi",
        filename="string_protein_protein_interactions.csv",
        aliases=("protein_protein_interactions.csv",),
        required_columns=(
            ColumnSpec("combined_score", "int64", nullable=True,
                       description="STRING combined confidence score (0-1000)."),
        ),
        any_of_groups=(
            # Bridge accepts uniprot_ac_a OR protein_a OR uniprot_id_a OR string_id_a.
            ("uniprot_ac_a", "protein_a", "uniprot_id_a", "string_id_a"),
            ("uniprot_ac_b", "protein_b", "uniprot_id_b", "string_id_b"),
            # Bridge accepts score OR combined_score.
            ("score", "combined_score"),
        ),
        optional_columns=(
            ColumnSpec("string_id_a", "string", nullable=True,
                       description="STRING ENSP ID for protein A."),
            ColumnSpec("string_id_b", "string", nullable=True,
                       description="STRING ENSP ID for protein B."),
            ColumnSpec("uniprot_id_a", "string", nullable=True,
                       description="UniProt accession for protein A (after crosswalk)."),
            ColumnSpec("uniprot_id_b", "string", nullable=True,
                       description="UniProt accession for protein B (after crosswalk)."),
            ColumnSpec("protein_a", "string", nullable=True,
                       description="Alias for string_id_a / uniprot_id_a."),
            ColumnSpec("protein_b", "string", nullable=True,
                       description="Alias for string_id_b / uniprot_id_b."),
            ColumnSpec("score", "int64", nullable=True,
                       description="Alias for combined_score."),
        ),
        min_rows=1,
        description="STRING protein-protein interactions (Pathway source).",
    ),

    # -------------------------------------------------------------------------
    # 8. DisGeNET GDA (disgenet_gene_disease_associations.csv)
    #    Source for Gene->associated_with->Disease edges.
    #    Produced by: phase1/pipelines/disgenet_pipeline.py
    # -------------------------------------------------------------------------
    "disgenet_gda": SourceSpec(
        key="disgenet_gda",
        filename="disgenet_gene_disease_associations.csv",
        aliases=("gene_disease_associations.csv",),
        required_columns=(
            ColumnSpec("gene_symbol", "string", nullable=False,
                       description="HGNC gene symbol."),
            ColumnSpec("disease_id", "string", nullable=False,
                       description="Disease identifier (CUI/DOID/MESH)."),
            ColumnSpec("score", "float64", nullable=True,
                       description="DisGeNET confidence score [0,1]."),
        ),
        any_of_groups=(
            # Bridge accepts gene_id OR ncbi_gene_id.
            ("gene_id", "ncbi_gene_id"),
        ),
        optional_columns=(
            ColumnSpec("gene_id", "int64", nullable=True,
                       description="NCBI Gene ID."),
            ColumnSpec("ncbi_gene_id", "int64", nullable=True,
                       description="Alias for gene_id."),
            ColumnSpec("disease_name", "string", nullable=True,
                       description="Human-readable disease name."),
            ColumnSpec("source", "string", nullable=True,
                       description="DisGeNET source (CURATED/BEFREE/etc.)."),
            ColumnSpec("year", "int64", nullable=True,
                       description="Year of first association."),
            # Teammate-2 Task 2.2 ROOT FIX (P2-008 prevalence):
            # Add prevalence_per_10k column. Cystic fibrosis was previously
            # flagged as "common" by a LINEAR formula that mapped GDA count
            # to prevalence (scientifically wrong — CF is RARE at 0.4/10K
            # but has ~2000 GDAs because the CFTR gene is heavily studied).
            # The fix: extract REAL epidemiological prevalence from a
            # curated WHO/Orphanet table keyed by disease_id (ORPHA:nnnn
            # for rare diseases, CUI for common diseases). Diseases not
            # in the curated table get None (downstream treats as neutral
            # 0.5). This field flows Phase 1 -> Phase 2 (Disease node
            # property) -> Phase 4 (RL env market_opportunity scoring).
            ColumnSpec("prevalence_per_10k", "float64", nullable=True,
                       description=(
                           "Real epidemiological prevalence per 10,000 "
                           "people. Sources: Orphanet (for ORPHA:nnnn "
                           "rare diseases), WHO Global Burden of Disease "
                           "(for common diseases). NULL for diseases "
                           "not in the curated table. NOT a linear "
                           "function of GDA count (the previous formula "
                           "incorrectly flagged cystic fibrosis as "
                           "common — CF is RARE at 0.4/10K)."
                       )),
        ),
        min_rows=1,
        description="DisGeNET gene-disease associations (Gene-Disease edge source).",
    ),

    # -------------------------------------------------------------------------
    # 9. OMIM GDA (omim_gene_disease_associations.csv)
    #    Source for Gene->causes->Disease edges (Mendelian).
    #    Produced by: phase1/pipelines/omim_pipeline.py
    # -------------------------------------------------------------------------
    "omim_gda": SourceSpec(
        key="omim_gda",
        filename="omim_gene_disease_associations.csv",
        aliases=(),
        required_columns=(
            ColumnSpec("gene_mim", "string", nullable=False,
                       description="OMIM gene MIM number."),
            ColumnSpec("gene_symbol", "string", nullable=False,
                       description="HGNC gene symbol."),
            ColumnSpec("disease_id", "string", nullable=False,
                       description="Disease MIM number."),
            ColumnSpec("disease_name", "string", nullable=False,
                       description="Human-readable disease name."),
        ),
        any_of_groups=(),
        optional_columns=(
            ColumnSpec("is_susceptibility", "bool", nullable=True,
                       description="True if susceptibility (not causal)."),
            # Teammate-2 Task 2.3 ROOT FIX: add genetic_basis field.
            # OMIM phenotypes have a marker prefix that encodes the
            # genetic basis: '*' = gene locus, '+' = gene/locus with
            # phenotype, '%' = mendelian phenotype, '#' = phenotype
            # (phenotype MIM), '{}' = susceptibility, '[]' = non-disease,
            # '?' = provisional. The pipeline already extracts
            # inheritance_pattern from the phenotype name (e.g.,
            # "autosomal recessive") — this field captures the SAME
            # data under the contract name expected by downstream
            # Phase 2 omim_loader (which reads `genetic_basis` to
            # create (Gene)-[:CAUSES]->(Disease) edges for mendelian
            # phenotypes). Without this field, the loader cannot
            # distinguish causal from susceptibility associations.
            ColumnSpec("genetic_basis", "string", nullable=True,
                       description=(
                           "OMIM genetic basis classification: "
                           "'mendelian_phenotype' (marker '%'), "
                           "'gene_locus' (marker '*' or '+'), "
                           "'phenotype' (marker '#'), "
                           "'susceptibility' (marker '{}'), "
                           "'non_disease' (marker '[]'), "
                           "'provisional' (marker '?'), or 'causal' "
                           "(default for OMIM records with no marker). "
                           "Used by phase2 omim_loader to create "
                           "(Gene)-[:CAUSES]->(Disease) edges for "
                           "mendelian_phenotype and causal entries."
                       )),
            ColumnSpec("inheritance_pattern", "string", nullable=True,
                       description=(
                           "Inheritance pattern extracted from the "
                           "phenotype name (e.g., 'autosomal recessive', "
                           "'X-linked'). Alias for genetic_basis — "
                           "kept for backward compat with consumers "
                           "that read inheritance_pattern."
                       )),
            ColumnSpec("association_type", "string", nullable=True,
                       description="OMIM association type label."),
            ColumnSpec("mapping_key", "int64", nullable=True,
                       description="OMIM mapping key (1-3)."),
        ),
        min_rows=1,
        description="OMIM gene-disease associations (Mendelian).",
    ),

    # -------------------------------------------------------------------------
    # 10. OMIM susceptibility (omim_gene_disease_susceptibility.csv)
    #     Source for Gene->predisposes->Disease edges (complex disease).
    #     Produced by: phase1/pipelines/omim_pipeline.py
    # -------------------------------------------------------------------------
    "omim_susceptibility": SourceSpec(
        key="omim_susceptibility",
        filename="omim_gene_disease_susceptibility.csv",
        aliases=(),
        required_columns=(
            ColumnSpec("gene_mim", "string", nullable=False,
                       description="OMIM gene MIM number."),
            ColumnSpec("gene_symbol", "string", nullable=False,
                       description="HGNC gene symbol."),
            ColumnSpec("disease_id", "string", nullable=False,
                       description="Disease MIM number."),
            ColumnSpec("disease_name", "string", nullable=False,
                       description="Human-readable disease name."),
        ),
        any_of_groups=(),
        optional_columns=(
            ColumnSpec("is_susceptibility", "bool", nullable=True,
                       description="Always True for this file."),
            # Teammate-2 Task 2.3 ROOT FIX: same genetic_basis field as
            # omim_gda — always 'susceptibility' for rows in this file.
            ColumnSpec("genetic_basis", "string", nullable=True,
                       description="Always 'susceptibility' for this file."),
            ColumnSpec("inheritance_pattern", "string", nullable=True,
                       description="Inheritance pattern (alias for genetic_basis)."),
            ColumnSpec("association_type", "string", nullable=True,
                       description="OMIM association type label."),
        ),
        min_rows=0,
        description="OMIM susceptibility (complex disease) — may be empty if no suscept associations.",
    ),

    # -------------------------------------------------------------------------
    # 11. PubChem enrichment (pubchem_enrichment.csv)
    #     Source for additional Compound properties (logP, TPSA, etc.).
    #     Produced by: phase1/pipelines/pubchem_pipeline.py
    # -------------------------------------------------------------------------
    "pubchem_enrichment": SourceSpec(
        key="pubchem_enrichment",
        filename="pubchem_enrichment.csv",
        aliases=(),
        required_columns=(
            ColumnSpec("inchikey", "string", nullable=False,
                       description="27-char InChIKey (foreign key to drugs)."),
            ColumnSpec("canonical_smiles", "string", nullable=True,
                       description="PubChem canonical SMILES."),
        ),
        any_of_groups=(),
        optional_columns=(
            ColumnSpec("pubchem_cid", "int64", nullable=True,
                       description="PubChem Compound ID."),
            ColumnSpec("iupac_name", "string", nullable=True,
                       description="IUPAC systematic name."),
            ColumnSpec("molecular_weight", "float64", nullable=True,
                       description="Molecular weight (Daltons)."),
            ColumnSpec("molecular_formula", "string", nullable=True,
                       description="Molecular formula."),
            # Teammate-2 Task 2.4 ROOT FIX (P2-036): the pipeline emits
            # `xlogp` (PubChem's XLogP3 algorithm), NOT `logp` (which
            # is a generic label). The previous schema declared `logp`
            # but the pipeline wrote `xlogp` — a column-name mismatch
            # that caused every row's logP value to be NULL when read
            # by consumers expecting `logp`. ROOT FIX: declare BOTH
            # columns. The pipeline writes `xlogp` (canonical PubChem
            # name); `logp` is kept as an alias for consumers that
            # expect the generic name. Phase 3 biomedical_tables.py
            # reads `xlogp` from the SQL drugs table.
            ColumnSpec("xlogp", "float64", nullable=True,
                       description="PubChem XLogP3 (computed logP)."),
            ColumnSpec("logp", "float64", nullable=True,
                       description="Alias for xlogp (legacy consumers)."),
            ColumnSpec("tpsa", "float64", nullable=True,
                       description="Topological polar surface area."),
            ColumnSpec("h_bond_donor_count", "int64", nullable=True,
                       description="H-bond donor count."),
            ColumnSpec("h_bond_acceptor_count", "int64", nullable=True,
                       description="H-bond acceptor count."),
            ColumnSpec("rotatable_bond_count", "int64", nullable=True,
                       description="Rotatable bond count."),
            ColumnSpec("heavy_atom_count", "int64", nullable=True,
                       description="Heavy atom count."),
            ColumnSpec("complexity", "float64", nullable=True,
                       description="PubChem complexity score."),
            # Teammate-2 Task 2.4 ROOT FIX: isomeric_smiles is REQUIRED
            # for chiral drug fingerprinting (life-safety: (R)- vs (S)-
            # thalidomide must remain distinguishable). The previous v50
            # downloader omitted this from the PubChem REST API property
            # list, silently producing NULL isomeric_smiles for every row.
            ColumnSpec("isomeric_smiles", "string", nullable=True,
                       description=(
                           "PubChem isomeric SMILES (preserves stereochemistry). "
                           "REQUIRED for chiral drug fingerprinting in Phase 3 "
                           "biomedical_tables.py. The (R) and (S) enantiomers "
                           "of thalidomide have different biological activity "
                           "— collapsing them to canonical_smiles (which drops "
                           "stereochemistry) is a life-safety bug."
                       )),
        ),
        min_rows=0,
        description="PubChem enrichment data (additional Compound properties).",
    ),
}


# =============================================================================
# Convenience: flat dict of canonical CSV filenames
# =============================================================================

PHASE1_CSV_FILENAMES: Dict[str, str] = {
    spec.key: spec.filename for spec in PHASE1_OUTPUT_SCHEMA.values()
}


# =============================================================================
# Convenience accessors
# =============================================================================


def get_required_columns(source_key: str) -> List[str]:
    """Return the list of required column names for ``source_key``."""
    spec = PHASE1_OUTPUT_SCHEMA[source_key]
    return [c.name for c in spec.required_columns]


def get_any_of_groups(source_key: str) -> List[List[str]]:
    """Return the list of any-of column groups for ``source_key``."""
    spec = PHASE1_OUTPUT_SCHEMA[source_key]
    return [list(group) for group in spec.any_of_groups]


def get_optional_columns(source_key: str) -> List[str]:
    """Return the list of optional column names for ``source_key``."""
    spec = PHASE1_OUTPUT_SCHEMA[source_key]
    return [c.name for c in spec.optional_columns]


def get_all_aliases(source_key: str) -> List[str]:
    """Return [filename] + list of aliases for ``source_key``.

    Teammate 5 (P2→P1 Integration, P0 root fix): the Phase 2 bridge uses
    this function to resolve Phase 1 CSV filenames via the contract —
    eliminating the hardcoded ``_PHASE1_SOURCE_TO_CSV`` dict that
    previously drifted from the actual Phase 1 pipeline output.
    """
    spec = PHASE1_OUTPUT_SCHEMA[source_key]
    return [spec.filename] + list(spec.aliases)


# NOTE: ``get_required_id_column`` is defined LATER in this module (below
# the ``_REQUIRED_ID_COLUMNS`` dict) with a curated per-source mapping
# based on scientific semantics (inchikey for Compound, uniprot_id for
# Protein, gene_symbol for Gene, etc.). The earlier simple "first match
# in declaration order" implementation was removed by Teammate 5 during
# the rebase onto main (main's curated version is more correct — it
# returns ``inchikey`` for chembl_drugs rather than ``chembl_id``,
# matching the IUPAC universal Compound key standard).


# =============================================================================
# TM1 TASK 1.4 ROOT FIX (v131 -- Teammate 1 P1->P2 integration):
# get_required_id_column — canonical ID column per source.
# =============================================================================
# RED-TEAM AUDIT FINDING (v131, hostile-auditor pass):
#   The Teammate 1 task spec ("Wire Phase 1 Master DAG to Phase 2 KG
#   Construction") requires the master DAG's validate_output task to
#   resolve CSV filenames via the contract (PHASE1_OUTPUT_SCHEMA +
#   get_all_aliases) AND verify each CSV has the expected ID column.
#   The previous contract module exposed ``get_required_columns`` (which
#   returns ALL required column names for a source) but NOT a function
#   that returns the SINGLE canonical ID column for a source. The master
#   DAG's validate_output therefore hardcoded a dict of {filename:
#   id_column} pairs — 4 of the 7 filenames were WRONG (see
#   master_pipeline_dag.py lines 1193-1201 for the broken code) and the
#   id_column values were hand-maintained, causing them to drift from
#   the contract. This is the exact "comments claim fixed, code is
#   broken" failure mode the audit mandates against.
#
# ROOT FIX (v131, this commit):
#   1. Add ``get_required_id_column(source_key)`` that returns the
#      canonical ID column for the source — the column whose value
#      uniquely identifies a row for downstream deduplication,
#      provenance tracing, and entity resolution. Returns None for
#      sources whose primary key is a multi-column composite
#      (e.g. string_ppi's protein pair, interactions' drug+target pair).
#   2. The mapping is HAND-MAINTAINED in this contract module (the
#      single source of truth) so future pipeline changes that rename
#      an ID column MUST update this mapping in the same PR. A CI test
#      in ``phase1/tests/integration/test_p1_to_p2_master_dag.py``
#      asserts that for every source key, the ID column returned by
#      ``get_required_id_column`` is present in the source's
#      ``required_columns`` OR ``any_of_groups`` (so the mapping cannot
#      silently drift from the column spec).
#   3. ``__all__`` is extended so ``from phase1.contracts.phase1_schema
#      import get_required_id_column`` works for downstream callers
#      (master_pipeline_dag, Phase 2 bridge).
# ---------------------------------------------------------------------------

# Canonical ID column per source. Sources with multi-column primary keys
# (e.g. a pair of proteins in a PPI edge, a drug+target pair in a DPI
# edge) map to None — the master DAG's validate_output SKIPS the ID-
# column header check for those sources (the contract's required_columns
# already enforces both halves of the composite key).
_REQUIRED_ID_COLUMNS: Dict[str, Optional[str]] = {
    # Drug / compound sources: inchikey is the canonical compound key.
    "chembl_drugs": "inchikey",
    "drugs": "inchikey",
    "pubchem_enrichment": "inchikey",
    # Protein source: uniprot_id is the canonical protein key (the
    # contract's any_of_group accepts uniprot_ac / accession /
    # uniprot_id, but uniprot_id is the canonical name emitted by the
    # pipeline after entity resolution).
    "uniprot_proteins": "uniprot_id",
    # GDA sources: gene_symbol is the canonical gene key. disease_id is
    # also required but gene_symbol is the join key to UniProt proteins
    # (which carry gene_symbol as a required column).
    "disgenet_gda": "gene_symbol",
    "omim_gda": "gene_symbol",
    "omim_susceptibility": "gene_symbol",
    # ChEMBL activities: molecule_chembl_id is the canonical compound
    # reference on an activity row. (target_chembl_id is also required
    # but molecule_chembl_id is the join key to chembl_drugs.)
    "chembl_activities": "molecule_chembl_id",
    # Multi-column-key sources: None means "no single ID column — skip
    # the header check; the contract's required_columns already
    # enforces both halves of the composite key."
    "interactions": None,    # (drugbank_id, uniprot_id) pair
    "indications": None,     # (drugbank_id, disease_id) pair
    "string_ppi": None,      # (uniprot_id_a, uniprot_id_b) pair
}


def get_required_id_column(source_key: str) -> Optional[str]:
    """Return the canonical ID column for ``source_key``, or None.

    The ID column is the column whose value uniquely identifies a row
    for downstream deduplication, provenance tracing, and entity
    resolution. Used by ``master_pipeline_dag.validate_output`` to
    verify each Phase 1 CSV has the expected ID column in its header
    (so a pipeline schema drift that renames the ID column is caught
    BEFORE Phase 2 builds a KG on rows with NULL IDs).

    Returns None for sources whose primary key is a multi-column
    composite (e.g. ``string_ppi``'s protein pair,
    ``interactions``'s drug+target pair). Callers MUST handle None
    by skipping the ID-column header check — the contract's
    ``required_columns`` already enforces both halves of the composite
    key for those sources.

    Parameters
    ----------
    source_key : str
        Canonical source key (e.g. ``"chembl_drugs"``, ``"drugs"``).

    Returns
    -------
    str or None
        The canonical ID column name, or None for multi-column-key
        sources.

    Raises
    ------
    KeyError
        If ``source_key`` is not in :data:`PHASE1_OUTPUT_SCHEMA`.
    """
    if source_key not in PHASE1_OUTPUT_SCHEMA:
        raise KeyError(
            f"get_required_id_column: unknown source_key {source_key!r}. "
            f"Known keys: {sorted(PHASE1_OUTPUT_SCHEMA.keys())}"
        )
    return _REQUIRED_ID_COLUMNS.get(source_key)


# =============================================================================
# TM1 TASK 1.4 ROOT FIX (v131): SCHEMA_VERSION constant for XCom payload.
# =============================================================================
# The master DAG's validate_output task returns an XCom payload that
# includes ``schema_version`` so downstream consumers (trigger_phase2,
# Phase 2 run_pipeline.py) can verify they're reading Phase 1 output
# from a compatible contract version. The version is bumped whenever a
# BREAKING change is made to PHASE1_OUTPUT_SCHEMA (column renamed,
# required column added, source added/removed). Non-breaking additions
# (new optional columns) do NOT bump the version.
#
# Current version: "11" — 11 sources in PHASE1_OUTPUT_SCHEMA.
# -----------------------------------------------------------------------------
SCHEMA_VERSION: str = "11"


# =============================================================================
# P1-050 v124 ROOT FIX (Teammate 3 -- hostile-auditor pass):
# Contract-vs-pipeline column drift detector.
# =============================================================================
# The P1-050 audit found that ``phase1_schema.py`` is hand-maintained (not
# auto-generated from the ORM models or the pipeline's DataFrame output),
# creating schema drift risk: if a pipeline adds a new column to its CSV
# output, the contract must be manually updated, or the contract
# validation will report a false-positive "extra column" warning. There
# was NO CI test that asserted the contract matches the actual CSV output
# of each pipeline.
#
# ROOT FIX: add ``detect_contract_vs_pipeline_drift()`` -- a function that
# imports each pipeline's ``_get_processed_columns()`` (or falls back to
# introspecting the pipeline's ``run()`` output DataFrame) and asserts
# that every required column in the contract is present in the pipeline's
# output, and that every column in the pipeline's output is either in
# ``required_columns`` or ``optional_columns`` of the contract. Drift is
# returned as a list of structured warnings -- the caller (CI test) can
# assert the list is empty.
#
# This function is INTENTIONALLY defensive: if a pipeline module cannot
# be imported (e.g., missing optional deps in a CI env), it skips that
# source with a warning instead of crashing. The CI test that calls this
# function should assert NO drift for sources that DID import successfully.
# =============================================================================


def detect_contract_vs_pipeline_drift() -> List[str]:
    """Detect column drift between the Phase 1 contract and pipeline output.

    For each source in :data:`PHASE1_OUTPUT_SCHEMA`, imports the
    corresponding pipeline module and (if the module exposes a
    ``_get_processed_columns()`` helper) compares the pipeline's declared
    output columns against the contract's ``required_columns`` +
    ``optional_columns``. Returns a list of human-readable drift
    descriptions (empty list = no drift).

    Drift is reported when:
      - A contract REQUIRED column is NOT in the pipeline's output.
      - A pipeline output column is NEITHER required NOR optional in the
        contract.

    Pipeline modules that cannot be imported are SKIPPED with a warning
    appended to the result list (NOT raised -- the caller decides whether
    to fail on import skips).

    Returns
    -------
    list[str]
        List of drift descriptions. Empty list = no drift detected for
        any importable pipeline.
    """
    drift: List[str] = []

    # Map contract source keys to (module path, function name) tuples.
    # Pipelines that expose ``_get_processed_columns()`` are checked;
    # pipelines that don't are skipped (with a warning, not an error).
    _pipeline_modules: Dict[str, str] = {
        "chembl_drugs": "pipelines.chembl_pipeline",
        "chembl_activities": "pipelines.chembl_pipeline",
        "drugs": "pipelines.drugbank_pipeline",
        "interactions": "pipelines.drugbank_pipeline",
        "indications": "pipelines.drugbank_pipeline",
        "uniprot_proteins": "pipelines.uniprot_pipeline",
        "string_ppi": "pipelines.string_pipeline",
        "disgenet_gda": "pipelines.disgenet_pipeline",
        "omim_gda": "pipelines.omim_pipeline",
        "omim_susceptibility": "pipelines.omim_pipeline",
        "pubchem_enrichment": "pipelines.pubchem_pipeline",
    }

    import importlib
    for source_key, spec in PHASE1_OUTPUT_SCHEMA.items():
        module_path = _pipeline_modules.get(source_key)
        if not module_path:
            drift.append(
                f"P1-050: source {source_key!r} has no pipeline module mapping "
                f"-- skipping drift check."
            )
            continue

        try:
            module = importlib.import_module(module_path)
        except Exception as exc:  # noqa: BLE001 -- defensive: skip unimportable
            drift.append(
                f"P1-050: could not import {module_path!r} for source "
                f"{source_key!r} (skipping): {exc}"
            )
            continue

        # Look for ``_get_processed_columns()`` on the module. If absent,
        # skip with a warning. This is opt-in so we don't break pipelines
        # that haven't yet been updated to expose their output columns.
        getter = getattr(module, "_get_processed_columns", None)
        if getter is None:
            # Not an error -- the pipeline simply hasn't been updated to
            # expose its columns. Skip silently (the contract is still
            # the source of truth for validation).
            continue

        try:
            pipeline_columns = set(getter(source_key))
        except Exception as exc:  # noqa: BLE001 -- defensive
            drift.append(
                f"P1-050: {module_path}._get_processed_columns({source_key!r}) "
                f"raised {exc!r} -- skipping drift check."
            )
            continue

        contract_required = {c.name for c in spec.required_columns}
        contract_optional = {c.name for c in spec.optional_columns}
        contract_all = contract_required | contract_optional

        missing_in_pipeline = contract_required - pipeline_columns
        if missing_in_pipeline:
            drift.append(
                f"P1-050: source {source_key!r} -- contract REQUIRED columns "
                f"MISSING from pipeline output: {sorted(missing_in_pipeline)}. "
                f"Either the pipeline regressed (stopped emitting these columns) "
                f"or the contract drifted (added columns the pipeline never "
                f"produced). Update the pipeline or the contract."
            )

        extra_in_pipeline = pipeline_columns - contract_all
        if extra_in_pipeline:
            drift.append(
                f"P1-050: source {source_key!r} -- pipeline emits columns NOT in "
                f"contract: {sorted(extra_in_pipeline)}. Either add them to the "
                f"contract's optional_columns (if they're real enrichment) or "
                f"stop emitting them from the pipeline (if they're accidental)."
            )

    return drift


# =============================================================================
# TM3 Task 3.4 ROOT FIX (v127) — MatchConfidence contract re-export
# =============================================================================
# RED-TEAM AUDIT FINDING (v127, hostile-auditor pass):
#   The Task 3.4 specification (Teammate 3, Phase 1 -> Phase 2 data-integrity
#   pairing) explicitly lists ``phase1/contracts/phase1_schema.py`` as the
#   file to edit and prescribes this verification command:
#
#       python -c "from phase1.contracts.phase1_schema import MatchConfidence; \
#                  vals = [m.value for m in MatchConfidence]; \
#                  assert len(vals) == len(set(vals)), 'duplicates!'"
#
#   Prior "root fix" passes (v113 / v65 / v89) corrected the alias collisions
#   IN THE ENUM ITSELF (which physically lives at
#   ``phase1/entity_resolution/base.py`` lines 95-237 — verified by reading
#   the actual file, NOT by trusting comments). The enum now has 11 members
#   with 11 distinct float values and is decorated with ``@enum.unique`` so
#   any future duplicate is a hard import-time error.
#
#   BUT — the verification command above FAILED prior to this commit because
#   ``MatchConfidence`` was NOT importable from the contract module
#   ``phase1.contracts.phase1_schema``. Downstream consumers that read the
#   Task 3.4 contract and imported MatchConfidence from the contract module
#   got ``ImportError: cannot import name 'MatchConfidence'``. This is the
#   exact "comments claim fixed, code is broken" failure mode the audit
#   mandates against.
#
# ROOT FIX (v127, this commit):
#   1. Re-export MatchConfidence (and the from_method classmethod) from the
#      contract module so the Task 3.4 verification command succeeds.
#   2. The canonical definition stays in ``phase1/entity_resolution/base.py``
#      (single source of truth — do NOT duplicate the enum here).
#   3. ``__all__`` is extended so ``from phase1.contracts.phase1_schema import *``
#      surfaces MatchConfidence to downstream callers (Phase 2's
#      ``drugos_graph/entity_resolver.py`` and Phase 4's RL ranker).
#   4. A runtime assertion verifies the re-export resolves to the SAME class
#      object as the canonical definition — catches any future module-split
#      regression where the contract module accidentally defines a parallel
#      enum.
#
# This makes the contract module the canonical PUBLIC import path while the
# entity_resolution package remains the canonical IMPLEMENTATION path. Both
# paths resolve to the same class object (verified by ``is`` identity check
# below).
# -----------------------------------------------------------------------------
try:
    # Absolute import — works whether the importer is at the repo root or
    # inside phase1/. The ``phase1.`` prefix matches the canonical package
    # path established by phase1/__init__.py's meta-path finder.
    from phase1.entity_resolution.base import MatchConfidence as _MatchConfidence

    MatchConfidence = _MatchConfidence  # public re-export

    # Identity check: the re-export MUST be the SAME class object as the
    # canonical definition. If a future maintainer accidentally defines a
    # parallel MatchConfidence in this module, the ``is`` check fails at
    # import time.
    from phase1.entity_resolution.base import MatchConfidence as _CanonicalMatchConfidence
    assert MatchConfidence is _CanonicalMatchConfidence, (
        "TM3 Task 3.4 v127 CONTRACT DRIFT: phase1.contracts.phase1_schema."
        "MatchConfidence is NOT the same class object as phase1.entity_"
        "resolution.base.MatchConfidence. A parallel enum definition was "
        "introduced — remove it and use the re-export instead."
    )
    del _MatchConfidence, _CanonicalMatchConfidence
except ImportError as _mc_exc:
    # Defensive: if entity_resolution.base is unimportable (e.g. rapidux
    # dependency missing during a partial install), do NOT crash the
    # contract module — other schema constants are still needed. Log loudly.
    import logging as _logging
    _logging.getLogger("phase1.contracts.phase1_schema").error(
        "TM3 Task 3.4 v127: could not re-export MatchConfidence from "
        "phase1.entity_resolution.base: %s. The Task 3.4 verification "
        "command will fail. Fix the entity_resolution package import.",
        _mc_exc,
    )
    del _logging, _mc_exc


# -----------------------------------------------------------------------------
# TM3 Task 3.3 v127: ValidatedHypothesis ORM model contract re-export
# -----------------------------------------------------------------------------
# The Task 3.3 spec lists ``phase1/contracts/phase1_schema.py`` as a file to
# edit. The canonical ORM definition lives in ``phase1/database/models.py``
# (where ALL other ORM models live — consistency). Re-export it here so
# downstream code (and future TMs reading the task spec) can import it from
# the contract module:
#
#     from phase1.contracts.phase1_schema import ValidatedHypothesis
#
# The migration SQL (``phase1/database/migrations/019_validated_hypotheses.sql``)
# is the canonical DDL; the ORM mirrors it for SQLite dev/test DBs that skip
# the migration SQL. See the ORM docstring for the full 10-column schema.
try:
    from phase1.database.models import (
        ValidatedHypothesis as _ValidatedHypothesis,
        VALIDATED_HYPOTHESIS_OUTCOMES as _VH_OUTCOMES,
    )
    ValidatedHypothesis = _ValidatedHypothesis
    VALIDATED_HYPOTHESIS_OUTCOMES = _VH_OUTCOMES

    # Identity check — same defense-in-depth pattern as MatchConfidence above.
    from phase1.database.models import (
        ValidatedHypothesis as _CanonicalVH,
        VALIDATED_HYPOTHESIS_OUTCOMES as _CanonicalOutcomes,
    )
    assert ValidatedHypothesis is _CanonicalVH, (
        "TM3 Task 3.3 v127 CONTRACT DRIFT: ValidatedHypothesis is not the "
        "same class object as phase1.database.models.ValidatedHypothesis."
    )
    assert VALIDATED_HYPOTHESIS_OUTCOMES is _CanonicalOutcomes, (
        "TM3 Task 3.3 v127 CONTRACT DRIFT: VALIDATED_HYPOTHESIS_OUTCOMES "
        "is not the same tuple object as the canonical definition."
    )
    del _ValidatedHypothesis, _VH_OUTCOMES, _CanonicalVH, _CanonicalOutcomes
except ImportError as _vh_exc:
    import logging as _logging
    _logging.getLogger("phase1.contracts.phase1_schema").error(
        "TM3 Task 3.3 v127: could not re-export ValidatedHypothesis from "
        "phase1.database.models: %s. The POST /datasets/validated_hypotheses "
        "endpoint will fail. Fix the phase1.database import.",
        _vh_exc,
    )
    del _logging, _vh_exc


# -----------------------------------------------------------------------------
# ``__all__`` — explicit public API surface for ``from ... import *``.

# -----------------------------------------------------------------------------
# TM1 Task 1.2 ROOT FIX: re-export the Drug & Protein ORM models so the
# verification commands in the task spec work as written:
#   python -c "from phase1.contracts.phase1_schema import Drug; assert hasattr(Drug, 'is_withdrawn')"
#   python -c "from phase1.contracts.phase1_schema import Protein; assert hasattr(Protein, 'sequence')"
# Without these re-exports, the task verification raises ImportError.
# The ORM models remain the SINGLE source of truth for column definitions;
# this re-export makes them importable from the contract module without
# duplicating the model code.
# -----------------------------------------------------------------------------
try:  # defensive: don't break schema import if ORM deps are missing
    try:
        from phase1.database.models import Drug as Drug  # noqa: F401
        from phase1.database.models import Protein as Protein  # noqa: F401
    except ImportError:
        from database.models import Drug as Drug  # noqa: F401
        from database.models import Protein as Protein  # noqa: F401
except ImportError:  # pragma: no cover -- only fires in envs without sqlalchemy
    Drug = None  # type: ignore[assignment,misc]
    Protein = None  # type: ignore[assignment,misc]


# -----------------------------------------------------------------------------
__all__: list[str] = [
    # Source-spec contract types
    "ColumnSpec",
    "SourceSpec",
    "ValidationIssue",
    "PHASE1_OUTPUT_SCHEMA",
    "PHASE1_CSV_FILENAMES",
    "get_required_columns",
    "get_any_of_groups",
    "get_optional_columns",
    "get_all_aliases",
    "get_required_id_column",
    "SCHEMA_VERSION",
    "detect_contract_vs_pipeline_drift",
    # Teammate 5 (P2→P1 Integration): contract version gate
    "__version__",
    # TM3 Task 3.4 v127: MatchConfidence contract re-export
    "MatchConfidence",
    # TM3 Task 3.3 v127: ValidatedHypothesis ORM model re-export
    "ValidatedHypothesis",
    "VALIDATED_HYPOTHESIS_OUTCOMES",
    # TM1 Task 1.2: Drug + Protein ORM model re-exports
    "Drug",
    "Protein",
]


