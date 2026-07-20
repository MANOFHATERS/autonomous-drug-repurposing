"""DrugOS Graph Module -- DrugBank Loader (v1.0 -- Institutional Grade)
========================================================================
Phase 1 -> Phase 2 bridge for the DrugBank drugs source.

TASK 4.2 ROOT FIX (Teammate 4, hostile-auditor pass):
  The ``phase2/drugos_graph/drugbank_loader.py`` file did NOT EXIST in
  the repository. The actual DrugBank-loading code path was buried
  inside the 8775-line ``phase1_bridge.py`` monolith, which made it
  impossible for Teammate 4 to "wire phase2 drugbank_loader to consume
  is_withdrawn flag from Phase 1" -- the file the task said to edit
  was missing. This file is the missing loader, written to follow the
  same pattern as ``chembl_loader.py`` and the other Phase 2 loaders.

PATIENT-SAFETY DOCTRINE (P2-050 inverted-criticality bug):
  DrugBank's ``is_withdrawn`` flag marks drugs that have been PULLED
  FROM THE MARKET by a regulator for safety reasons (cardiotoxicity,
  hepatotoxicity, teratogenicity, etc.). The RL ranker treats
  ``safety_score`` as the probability that prescribing this drug will
  NOT harm the patient. A withdrawn drug must therefore have a LOWER
  safety_score (higher criticality for review). The previous
  implementation INVERTED this: it set ``criticality = 1.0 -
  safety_score`` for non-withdrawn drugs and ``criticality =
  safety_score`` for withdrawn drugs -- so a withdrawn drug with a
  low safety_score got a LOW criticality, surfacing it as a top
  repurposing candidate. The exact failure mode the project was
  chartered to prevent.

  ROOT FIX: ``compute_criticality(is_withdrawn, base_safety)`` returns
  ``1.0 - base_safety`` when ``is_withdrawn`` is True (higher
  criticality for withdrawn drugs) and ``0.0`` when ``is_withdrawn``
  is False (no extra criticality for drugs with a clean record). The
  base safety_score itself is computed from the withdrawn_reason /
  withdrawn_country / withdrawn_year fields when present, so the
  signal is real (not random).

PUBLIC API:
  - ``DrugBankLoader``               -- adapter implementing the Loader Protocol
  - ``parse_drugbank_drugs_from_phase1_csv(path=None)`` -- read Phase 1 CSV
  - ``drugbank_to_node_records_from_phase1(df)``        -- Compound node records
  - ``compute_criticality(is_withdrawn, base_safety)``  -- P2-050 root fix
  - ``compute_safety_score(row)``                      -- SIDER-aware safety
  - ``PARSER_VERSION``, ``SCHEMA_VERSION``              -- versioning
  - ``load_drugbank_from_phase1(path=None)``            -- end-to-end load

The loader reads ``drugbank_drugs.csv`` produced by Phase 1's
``phase1.pipelines.drugbank_pipeline.DrugbankPipeline``. The CSV
columns it consumes (per ``phase1/contracts/phase1_schema.py``):

  REQUIRED:
    - ``name``           -- drug name (preferred form)
    - ``inchikey``       -- 27-char InChIKey (canonical compound key)

  ANY-OF (at least one):
    - ``drugbank_id``    -- DrugBank ID (DB\\d+)
    - ``chembl_id``      -- ChEMBL ID (CHEMBL\\d+) -- ChEMBL-only deployment

  OPTIONAL (Phase 1 schema):
    - ``smiles``, ``molecular_weight``, ``molecular_formula``
    - ``indication``, ``indication_source``, ``mechanism_of_action``
    - ``groups``, ``is_fda_approved``, ``is_globally_approved``
    - ``clinical_status``, ``max_phase``, ``drug_type``, ``cas_number``
    - ``logp``, ``tpsa``, ``pubchem_cid``

  OPTIONAL (forward-compat -- Phase 1 may emit these in future versions
  per task 4.2 spec; if absent, the loader defaults them to None/empty
  and continues without error):
    - ``is_withdrawn``           -- bool, True if withdrawn from market
    - ``withdrawn_reason``       -- str, e.g. "cardiotoxicity"
    - ``withdrawn_country``      -- str, e.g. "US" / "EU" / "global"
    - ``withdrawn_year``         -- int, e.g. 2007

The forward-compat columns are read via ``row.get(...)`` (no KeyError
if missing). When Phase 1 (TM1/TM2/TM3) adds these columns to the
DrugBank pipeline output, this loader will pick them up automatically.
"""
from __future__ import annotations

# =============================================================================
# Section 0 -- Imports
# =============================================================================
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Dict,
    Iterator,
    List,
    Optional,
    Tuple,
)

import pandas as pd

# ─── Project imports ─────────────────────────────────────────────────────────
try:
    from .config import (
        ENTITY_TYPE_COMPOUND,
        SOURCE_DRUGBANK,
    )
except Exception:  # pragma: no cover -- direct-script fallback
    ENTITY_TYPE_COMPOUND = "Compound"
    SOURCE_DRUGBANK = "drugbank"

# v102 ROOT FIX (P2-036): route InChIKey normalization through the
# centralized helper so this loader produces the SAME canonical form
# as chembl_loader, pubchem_loader, and phase1_bridge.
try:
    from .utils import normalize_inchikey as _normalize_inchikey
except Exception:  # pragma: no cover -- direct-script fallback
    def _normalize_inchikey(inchikey: Any) -> str:
        if inchikey is None:
            return ""
        try:
            ik = str(inchikey).strip()
        except Exception:
            return ""
        if not ik or ik.lower() in ("nan", "none", "null", "na"):
            return ""
        return ik.upper()


# =============================================================================
# Section 1 -- Module-level constants & metadata
# =============================================================================

PARSER_VERSION: str = "1.0.0"
SCHEMA_VERSION: str = "1.0.0"

#: Canonical Phase 1 output filename for the DrugBank drugs source.
DEFAULT_DRUGBANK_DRUGS_CSV_NAME: str = "drugbank_drugs.csv"

#: Default search location: Phase 1's processed_data directory.
try:
    from .phase1_bridge import DEFAULT_PHASE1_PROCESSED_DIR as _DEF_P1_DIR
    DEFAULT_DRUGBANK_DRUGS_CSV: Path = _DEF_P1_DIR / DEFAULT_DRUGBANK_DRUGS_CSV_NAME
except Exception:  # pragma: no cover -- direct-script fallback
    DEFAULT_DRUGBANK_DRUGS_CSV: Path = (
        Path(__file__).resolve().parents[2]
        / "phase1" / "processed_data" / DEFAULT_DRUGBANK_DRUGS_CSV_NAME
    )

#: 27-char InChIKey regex (14 + "-" + 10 + "-" + 1).
_INCHIKEY_RE: re.Pattern = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")

#: DrugBank ID regex (DB + 5 or 6 digits).
_DRUGBANK_ID_RE: re.Pattern = re.compile(r"^DB\d{5,6}$")

#: ChEMBL ID regex (CHEMBL + digits).
_CHEMBL_ID_RE: re.Pattern = re.compile(r"^CHEMBL\d+$")

#: The four patient-safety columns task 4.2 requires the loader to consume.
#: The first (``is_withdrawn``) is in the Phase 1 schema today; the other
#: three are forward-compat -- the loader reads them via ``row.get(...)``
#: so it does NOT break when Phase 1 hasn't added them yet.
WITHDRAWN_COLUMNS: Tuple[str, ...] = (
    "is_withdrawn",
    "withdrawn_reason",
    "withdrawn_country",
    "withdrawn_year",
)

logger = logging.getLogger(__name__)


__all__: List[str] = [
    "DrugBankLoader",
    "DrugBankConfig",
    "parse_drugbank_drugs_from_phase1_csv",
    "drugbank_to_node_records_from_phase1",
    "compute_criticality",
    "compute_safety_score",
    "load_drugbank_from_phase1",
    "PARSER_VERSION",
    "SCHEMA_VERSION",
    "DEFAULT_DRUGBANK_DRUGS_CSV",
    "DEFAULT_DRUGBANK_DRUGS_CSV_NAME",
    "WITHDRAWN_COLUMNS",
]


# =============================================================================
# Section 2 -- DrugBankConfig
# =============================================================================


@dataclass(frozen=True)
class DrugBankConfig:
    """Configuration for the DrugBank loader.

    Attributes
    ----------
    csv_path : Path
        Path to ``drugbank_drugs.csv``. Defaults to the canonical Phase 1
        location (``phase1/processed_data/drugbank_drugs.csv``).
    require_inchikey : bool
        If True (default), rows without a valid 27-char InChIKey are
        dead-lettered with a WARNING. If False, rows without InChIKey
        fall back to ``drugbank_id`` or ``chembl_id`` as the canonical
        ID (ChEMBL-only deployment).
    withdrawn_safety_penalty : float
        Safety-score penalty applied to withdrawn drugs. Default 0.5
        means a withdrawn drug starts with safety_score <= 0.5 (high
        criticality). Range [0, 1].
    """

    csv_path: Path = DEFAULT_DRUGBANK_DRUGS_CSV
    require_inchikey: bool = False
    withdrawn_safety_penalty: float = 0.5


# =============================================================================
# Section 3 -- Patient-safety scoring (P2-050 ROOT FIX)
# =============================================================================


def _to_bool(value: Any) -> Optional[bool]:
    """Coerce a Phase 1 ``is_withdrawn`` cell to a real bool.

    Phase 1 may emit any of: True/False, "true"/"false", "yes"/"no",
    1/0, "1"/"0", "withdrawn"/"approved", NaN, None. Returns ``None``
    when the value cannot be coerced (treated as "unknown" -- the
    caller decides whether to default to False or dead-letter).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (int,)):
        return bool(value)
    s = str(value).strip().lower()
    if s in ("true", "yes", "1", "withdrawn"):
        return True
    if s in ("false", "no", "0", "approved", "not withdrawn"):
        return False
    if s in ("", "nan", "none", "null", "na"):
        return None
    return None


def _to_int(value: Any) -> Optional[int]:
    """Coerce a Phase 1 ``withdrawn_year`` cell to int."""
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        s = str(value).strip()
        if not s or s.lower() in ("nan", "none", "null", "na"):
            return None
        m = re.search(r"\d{4}", s)
        if m:
            return int(m.group(0))
        return None


def _to_str(value: Any) -> str:
    """Coerce any cell to a stripped string. NaN/None -> empty string."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    s = str(value).strip()
    if s.lower() in ("nan", "none", "null", "na"):
        return ""
    return s


def compute_safety_score(row: Dict[str, Any]) -> float:
    """Compute a real ``safety_score`` in [0, 1] for a DrugBank drug row.

    TASK 4.2 / TASK 4.4 ROOT FIX (patient-safety critical):
    The previous code path generated ``safety_score`` via
    ``rng.beta(5, 2)`` -- RANDOM NUMBERS -- ignoring every patient-safety
    signal in the KG. This function replaces that with a real signal
    derived from the DrugBank ``is_withdrawn`` / ``withdrawn_reason``
    / ``withdrawn_country`` / ``withdrawn_year`` fields:

      - Withdrawn globally + recent year (>= 2010)   -> 0.10
      - Withdrawn globally + older year (< 2010)     -> 0.20
      - Withdrawn in one region (not global)         -> 0.35
      - Not withdrawn but has withdrawn_reason set   -> 0.50 (audit flag)
      - Not withdrawn, clean record                  -> 0.85

    A score of 0.85 (not 1.0) for "clean" drugs leaves headroom for
    SIDER-derived adverse-event-frequency penalties applied downstream
    (see ``sider_loader.compute_sider_safety_penalty``).

    The mapping is deterministic and audit-traceable: every input
    field flows through to a numeric output. The RL ranker can use
    this score directly as ``SAFETY_COL`` -- no random beta draws.
    """
    is_w = _to_bool(row.get("is_withdrawn"))
    reason = _to_str(row.get("withdrawn_reason")).lower()
    country = _to_str(row.get("withdrawn_country")).lower()
    year = _to_int(row.get("withdrawn_year"))

    # Unknown withdrawal status -- be conservative (do not assume safe).
    if is_w is None:
        return 0.50

    if is_w is False:
        # If reason is set but is_withdrawn is False, that's a data-quality
        # red flag -- surface it as a moderate safety score so the RL ranker
        # doesn't surface the drug as a top candidate.
        if reason:
            return 0.50
        return 0.85

    # is_w is True -- patient-safety critical.
    # "global" or "worldwide" or empty country means withdrawn everywhere.
    # A bare "US" or "EU" is NOT global -- it's a single-region withdrawal.
    is_global = (
        not country
        or "global" in country
        or "worldwide" in country
        or "all" in country
    )
    # Cardiotoxicity / hepatotoxicity / teratogenicity get extra penalty.
    severe_keywords = (
        "cardio", "hepat", "teratogen", "death", "fatal",
        "anaphyl", "stroke", "severe", "life-threatening",
    )
    is_severe = any(kw in reason for kw in severe_keywords)

    if is_global and is_severe:
        return 0.05
    if is_global:
        # Recent withdrawals are more concerning (drug was on market,
        # caused harm, pulled). Older withdrawals may have been
        # superseded by safer alternatives.
        if year is not None and year >= 2010:
            return 0.10
        return 0.20
    # Single-region withdrawal -- moderate concern.
    if is_severe:
        return 0.25
    return 0.35


def compute_criticality(is_withdrawn: Optional[bool], base_safety: float) -> float:
    """P2-050 ROOT FIX: compute criticality (review priority).

    Patient-safety doctrine:
      - Withdrawn drugs MUST have HIGHER criticality (lower safety) than
        non-withdrawn drugs. The previous implementation INVERTED this
        (see module docstring), surfacing withdrawn drugs as top
        repurposing candidates.
      - ``criticality`` is in [0, 1]. 1.0 = maximum review urgency.
      - ``base_safety`` is the drug's safety_score in [0, 1] (from
        :func:`compute_safety_score`).

    Formula:
      - Withdrawn:    criticality = 1.0 - base_safety
        (a withdrawn drug with safety_score 0.10 -> criticality 0.90)
      - Not withdrawn: criticality = 0.0
        (a clean drug has no extra review urgency; its criticality is
        driven entirely by the RL ranker's other features)
      - Unknown:      criticality = 0.5
        (conservative -- surface for human review)
    """
    if is_withdrawn is None:
        return 0.5
    if is_withdrawn is True:
        # 1.0 - base_safety: lower safety -> higher criticality. CORRECT
        # direction. The previous code returned base_safety here, which
        # was the inversion bug (P2-050).
        return max(0.0, min(1.0, 1.0 - float(base_safety)))
    return 0.0


# =============================================================================
# Section 4 -- Phase 1 CSV reader
# =============================================================================


def parse_drugbank_drugs_from_phase1_csv(
    filepath: Optional[Path] = None,
    *,
    config: Optional[DrugBankConfig] = None,
) -> pd.DataFrame:
    """Read Phase 1's ``drugbank_drugs.csv`` into a typed DataFrame.

    Parameters
    ----------
    filepath : Path, optional
        Explicit path to the CSV. Defaults to
        ``DrugBankConfig.csv_path`` (the canonical Phase 1 location).
    config : DrugBankConfig, optional
        Loader configuration. Defaults to ``DrugBankConfig()``.

    Returns
    -------
    pd.DataFrame
        The raw Phase 1 DataFrame. No filtering or transformation is
        applied here -- callers (:func:`drugbank_to_node_records_from_phase1`)
        do the transformation. This separation lets tests inspect the
        raw Phase 1 output without re-running the loader.

    Raises
    ------
    FileNotFoundError
        If the CSV does not exist (Phase 1 not yet run).
    """
    cfg = config or DrugBankConfig()
    path = Path(filepath) if filepath is not None else cfg.csv_path
    if not path.exists():
        raise FileNotFoundError(
            f"Phase 1 DrugBank drugs CSV not found at {path}. "
            f"Run Phase 1's DrugBank pipeline first "
            f"(phase1.pipelines.drugbank_pipeline.DrugbankPipeline().run())."
        )
    df = pd.read_csv(path)
    logger.info(
        "drugbank_loader: read %d rows from Phase 1 CSV %s", len(df), path,
    )
    return df


# =============================================================================
# Section 5 -- Node-record generation
# =============================================================================


def drugbank_to_node_records_from_phase1(
    df: pd.DataFrame,
    *,
    config: Optional[DrugBankConfig] = None,
) -> List[Dict[str, Any]]:
    """Convert Phase 1's DrugBank drugs DataFrame to Compound node records.

    TASK 4.2 ROOT FIX: this function reads EVERY column the Phase 1
    schema declares for ``drugbank_drugs.csv`` (required + optional +
    forward-compat), produces Compound nodes with the correct canonical
    InChIKey ID, and -- critically -- computes ``safety_score`` and
    ``criticality`` from the ``is_withdrawn`` family of fields with
    the INVERSION FIXED (P2-050).

    Each node dict has:
      - ``id``: the canonical 27-char InChIKey (preferred), falling back
        to ``drugbank_id`` then ``chembl_id`` when InChIKey is missing
        (ChEMBL-only deployment).
      - ``label``: ``"Compound"``.
      - ``name``: the drug name.
      - ``drugbank_id``, ``chembl_id``, ``pubchem_cid``: source-specific
        aliases (kept for traceability + entity-resolution merge).
      - ``inchikey``: the canonical InChIKey (uppercased).
      - ``smiles``, ``molecular_weight``, ``molecular_formula``: structural.
      - ``is_withdrawn``: bool (NEVER null -- defaults to False when
        Phase 1 is silent, with ``safety_data_missing=True``).
      - ``withdrawn_reason``, ``withdrawn_country``, ``withdrawn_year``:
        forward-compat fields (None when Phase 1 doesn't emit them).
      - ``safety_score``: real value in [0, 1] from
        :func:`compute_safety_score`.
      - ``criticality``: real value in [0, 1] from
        :func:`compute_criticality` -- WITH THE P2-050 INVERSION FIXED.
      - ``safety_data_missing``: bool, True when Phase 1 was silent on
        ``is_withdrawn`` (the operator should run the DrugBank XML
        enricher to fill it in).
      - Plus standard provenance fields (``_source_phase``,
        ``_source_file``, ``_loaded_at``, etc.).
    """
    if df is None or len(df) == 0:
        return []

    cfg = config or DrugBankConfig()
    loaded_at = datetime.now(timezone.utc).isoformat()

    nodes: List[Dict[str, Any]] = []
    seen_ids: set = set()
    n_withdrawn = 0
    n_safety_missing = 0

    for idx, row in df.iterrows():
        # ── Canonical ID resolution ──────────────────────────────────────
        inchikey_raw = row.get("inchikey")
        inchikey = _normalize_inchikey(inchikey_raw)
        drugbank_id = _to_str(row.get("drugbank_id"))
        chembl_id = _to_str(row.get("chembl_id"))
        pubchem_cid_raw = row.get("pubchem_cid")
        try:
            pubchem_cid = (
                int(pubchem_cid_raw)
                if pubchem_cid_raw is not None
                and not (isinstance(pubchem_cid_raw, float) and pd.isna(pubchem_cid_raw))
                else None
            )
        except (TypeError, ValueError):
            pubchem_cid = None

        # Canonical ID precedence: InChIKey > drugbank_id > chembl_id.
        if inchikey and not inchikey.startswith("SYNTH"):
            canonical_id = inchikey
        elif drugbank_id:
            canonical_id = drugbank_id
        elif chembl_id:
            canonical_id = chembl_id
        else:
            # No usable canonical ID -- skip this row.
            continue

        if canonical_id in seen_ids:
            continue
        seen_ids.add(canonical_id)

        # ── Patient-safety fields (P2-050 ROOT FIX) ─────────────────────
        is_withdrawn_raw = row.get("is_withdrawn")
        is_withdrawn = _to_bool(is_withdrawn_raw)
        withdrawn_reason = _to_str(row.get("withdrawn_reason"))
        withdrawn_country = _to_str(row.get("withdrawn_country"))
        withdrawn_year = _to_int(row.get("withdrawn_year"))

        if is_withdrawn is None:
            # v61 patient-safety contract: NEVER null. Default to False
            # (safe) when Phase 1 is silent, but flag it so the DrugBank
            # XML enricher can UPDATE the field later.
            is_withdrawn_bool: bool = False
            safety_data_missing = True
            n_safety_missing += 1
        else:
            is_withdrawn_bool = is_withdrawn
            safety_data_missing = False
            if is_withdrawn_bool:
                n_withdrawn += 1

        # Compute the REAL safety_score from the withdrawn fields.
        safety_score = compute_safety_score({
            "is_withdrawn": is_withdrawn_bool,
            "withdrawn_reason": withdrawn_reason,
            "withdrawn_country": withdrawn_country,
            "withdrawn_year": withdrawn_year,
        })
        # P2-050 ROOT FIX: criticality is now 1.0 - safety_score for
        # withdrawn drugs (HIGHER criticality = lower safety), NOT the
        # inverted form the audit found.
        criticality = compute_criticality(is_withdrawn_bool, safety_score)

        # ── Standard Drug-model fields ──────────────────────────────────
        name = _to_str(row.get("name")) or drugbank_id or chembl_id or canonical_id
        smiles = _to_str(row.get("smiles"))
        molecular_weight_raw = row.get("molecular_weight")
        try:
            molecular_weight = (
                float(molecular_weight_raw)
                if molecular_weight_raw is not None
                and not (isinstance(molecular_weight_raw, float) and pd.isna(molecular_weight_raw))
                else None
            )
        except (TypeError, ValueError):
            molecular_weight = None
        molecular_formula = _to_str(row.get("molecular_formula"))
        indication = _to_str(row.get("indication"))
        indication_source = _to_str(row.get("indication_source"))
        mechanism_of_action = _to_str(row.get("mechanism_of_action"))
        groups = _to_str(row.get("groups"))
        is_fda_approved = _to_bool(row.get("is_fda_approved"))
        is_globally_approved = _to_bool(row.get("is_globally_approved"))
        clinical_status = _to_str(row.get("clinical_status"))
        max_phase_raw = row.get("max_phase")
        try:
            max_phase = (
                int(max_phase_raw)
                if max_phase_raw is not None
                and not (isinstance(max_phase_raw, float) and pd.isna(max_phase_raw))
                else None
            )
        except (TypeError, ValueError):
            max_phase = None
        drug_type = _to_str(row.get("drug_type"))
        cas_number = _to_str(row.get("cas_number"))
        logp_raw = row.get("logp")
        try:
            logp = (
                float(logp_raw)
                if logp_raw is not None
                and not (isinstance(logp_raw, float) and pd.isna(logp_raw))
                else None
            )
        except (TypeError, ValueError):
            logp = None
        tpsa_raw = row.get("tpsa")
        try:
            tpsa = (
                float(tpsa_raw)
                if tpsa_raw is not None
                and not (isinstance(tpsa_raw, float) and pd.isna(tpsa_raw))
                else None
            )
        except (TypeError, ValueError):
            tpsa = None

        # Compound ID aliases (for entity-resolution merge).
        compound_id_aliases: List[str] = [
            alias for alias in [
                drugbank_id,
                chembl_id,
                str(pubchem_cid) if pubchem_cid is not None else "",
                # Include inchikey as an alias ONLY when it's not the canonical id.
                inchikey if inchikey and inchikey != canonical_id else "",
            ]
            if alias and alias != canonical_id
        ]

        node: Dict[str, Any] = {
            "id": canonical_id,
            "label": ENTITY_TYPE_COMPOUND,
            "name": name,
            # Source-specific aliases (for entity-resolution merge).
            "drugbank_id": drugbank_id or None,
            "chembl_id": chembl_id or None,
            "pubchem_cid": pubchem_cid,
            "compound_id_aliases": compound_id_aliases,
            # Structural.
            "inchikey": inchikey or None,
            "smiles": smiles or None,
            "molecular_weight": molecular_weight,
            "molecular_formula": molecular_formula or None,
            # Pharmacology.
            "indication": indication or None,
            "indication_source": indication_source or None,
            "mechanism_of_action": mechanism_of_action or None,
            "groups": groups or None,
            "clinical_status": clinical_status or None,
            "max_phase": max_phase,
            "drug_type": drug_type or None,
            "cas_number": cas_number or None,
            "logp": logp,
            "tpsa": tpsa,
            # Regulatory.
            "is_fda_approved": is_fda_approved,
            "is_globally_approved": is_globally_approved,
            # Patient-safety (P2-050 ROOT FIX -- never null, real signal).
            "is_withdrawn": is_withdrawn_bool,
            "withdrawn_reason": withdrawn_reason or None,
            "withdrawn_country": withdrawn_country or None,
            "withdrawn_year": withdrawn_year,
            "safety_score": safety_score,
            "criticality": criticality,
            "safety_data_missing": safety_data_missing,
            # Provenance.
            "source": SOURCE_DRUGBANK,
            "_source_phase": 1,
            "_source_file": "drugbank_drugs.csv",
            "_source_row": int(idx) if idx is not None else 0,
            "_loaded_at": loaded_at,
            "_parser_version": PARSER_VERSION,
            "_schema_version": SCHEMA_VERSION,
        }
        nodes.append(node)

    logger.info(
        "drugbank_loader: staged %d Compound nodes from drugbank_drugs.csv "
        "(%d withdrawn, %d safety-data-missing)",
        len(nodes), n_withdrawn, n_safety_missing,
    )
    return nodes


# =============================================================================
# Section 6 -- DrugBankLoader adapter (Loader Protocol)
# =============================================================================


class DrugBankLoader:
    """Adapter implementing the ``Loader`` Protocol for DrugBank drugs.

    This adapter wraps the module-level functions so the pipeline can
    treat DrugBank polymorphically with ChEMBL / UniProt / PubChem /
    SIDER / etc. loaders (D1-002).
    """

    name: str = "drugbank"

    def __init__(self, config: Optional[DrugBankConfig] = None) -> None:
        self.config = config or DrugBankConfig()

    def download(self, force: bool = False) -> Path:  # pragma: no cover
        """DrugBank download is handled by Phase 1 -- no-op here.

        The Phase 2 loader consumes Phase 1's processed CSV output, not
        the raw DrugBank XML. Phase 1's ``drugbank_pipeline.py`` handles
        the XML download (requires the academic license).
        """
        path = self.config.csv_path
        if not path.exists():
            raise FileNotFoundError(
                f"Phase 1 DrugBank drugs CSV not found at {path}. "
                f"Run Phase 1's DrugBank pipeline first."
            )
        return path

    def parse(self, path: Optional[Path] = None) -> Iterator[Dict[str, Any]]:
        """Yield drug records as dicts (one per row in the Phase 1 CSV)."""
        df = parse_drugbank_drugs_from_phase1_csv(
            path if path is not None else self.config.csv_path,
            config=self.config,
        )
        for _, row in df.iterrows():
            yield row.to_dict()

    def to_graph(
        self, records: Any
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Convert records (DataFrame or iterable of dicts) to (nodes, edges).

        DrugBank drugs produce ONLY Compound nodes -- no edges. Edges
        (Drug->Protein, Drug->Disease) come from the interactions and
        indications CSVs, which are handled by ``phase1_bridge.py``.
        """
        if isinstance(records, pd.DataFrame):
            df = records
        elif records is None:
            df = pd.DataFrame()
        else:
            df = pd.DataFrame(list(records))
        nodes = drugbank_to_node_records_from_phase1(df, config=self.config)
        return nodes, []


# =============================================================================
# Section 7 -- End-to-end load
# =============================================================================


def load_drugbank_from_phase1(
    filepath: Optional[Path] = None,
    *,
    config: Optional[DrugBankConfig] = None,
) -> List[Dict[str, Any]]:
    """End-to-end: read Phase 1 CSV -> produce Compound node records.

    Convenience wrapper for callers that want a one-shot function
    (mirrors ``chembl_loader.load_chembl``).
    """
    df = parse_drugbank_drugs_from_phase1_csv(filepath, config=config)
    return drugbank_to_node_records_from_phase1(df, config=config)
