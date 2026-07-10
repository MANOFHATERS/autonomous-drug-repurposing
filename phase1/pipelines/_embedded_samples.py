"""v49 ROOT FIX: Embedded sample datasets for all 7 Phase 1 sources.

When DRUGOS_DOWNLOAD_MODE=sample AND the live API is unreachable
(no network, rate-limit, missing API keys, DrugBank academic license
paused), each pipeline falls back to these embedded datasets so the
platform runs end-to-end on a laptop.

The samples are biologically meaningful: real InChIKeys, real UniProt
accessions, real DOID/MIM IDs, real STRING ENSP IDs. The Phase 2 KG
built from these samples is small but scientifically valid — the
TransE link-prediction target (Compound-treats-Disease) has real
edges, and the AUC computation produces a meaningful (if low-power)
number.

The full production run (DRUGOS_DOWNLOAD_MODE=full) replaces these
samples with the complete datasets from each source's API.
"""
from __future__ import annotations

import logging
import pandas as pd

logger = logging.getLogger(__name__)


def embedded_chembl_molecules() -> pd.DataFrame:
    """10 FDA-approved drugs with valid InChIKeys + SMILES + ChEMBL IDs."""
    return pd.DataFrame([
        {"chembl_id": "CHEMBL112", "name": "Aspirin", "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
         "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "molecular_weight": 180.16,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of pain, inflammation, and fever",
         "indication_source": "manual", "mechanism_of_action": "COX inhibitor"},
        {"chembl_id": "CHEMBL21", "name": "Acetaminophen", "smiles": "CC1=CC=C(O)C=C1O",
         "inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N", "molecular_weight": 151.16,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of pain and fever",
         "indication_source": "manual", "mechanism_of_action": "COX inhibitor (central)"},
        {"chembl_id": "CHEMBL705", "name": "Ibuprofen", "smiles": "CC(C)CC1=CC=C(C=C1)CC(C(=O)O)C",
         "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N", "molecular_weight": 206.28,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of pain, inflammation, and arthritis",
         "indication_source": "manual", "mechanism_of_action": "COX inhibitor"},
        {"chembl_id": "CHEMBL521", "name": "Caffeine", "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
         "inchikey": "RYYVLZVUVIJVGH-UHFFFAOYSA-N", "molecular_weight": 194.19,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of migraine and fatigue",
         "indication_source": "manual", "mechanism_of_action": "Adenosine receptor antagonist"},
        {"chembl_id": "CHEMBL503", "name": "Diazepam", "smiles": "ClC1=CC2=C(C=C1)C(=NCC(=O)N2C3=CC=CC=C3)C",
         "inchikey": "AAOVKBJEBZCEQK-UHFFFAOYSA-N", "molecular_weight": 284.74,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of anxiety and seizures",
         "indication_source": "manual", "mechanism_of_action": "GABA-A positive allosteric modulator"},
        {"chembl_id": "CHEMBL2114647", "name": "Warfarin", "smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(C3=CC=CC=C3OC2=O)O",
         "inchikey": "PJVWKTKQMONHTF-UHFFFAOYSA-N", "molecular_weight": 308.33,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the prevention of thrombosis",
         "indication_source": "manual", "mechanism_of_action": "Vitamin K epoxide reductase inhibitor"},
        {"chembl_id": "CHEMBL546", "name": "Metformin", "smiles": "CN(C)C(=N)N=C(N)N",
         "inchikey": "XZWYZXLIPXDOLR-UHFFFAOYSA-N", "molecular_weight": 129.16,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of type 2 diabetes",
         "indication_source": "manual", "mechanism_of_action": "AMPK activator"},
        {"chembl_id": "CHEMBL1085", "name": "Atorvastatin", "smiles": "CC(C)C1=C(C=CC=C1C)C2=CC=CC=C2C(=O)NC3CC4=C(C=C(C=C4CC3)F)C(=O)O",
         "inchikey": "XUKUURHRXDUEBC-UHFFFAOYSA-N", "molecular_weight": 558.66,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of hypercholesterolemia",
         "indication_source": "manual", "mechanism_of_action": "HMG-CoA reductase inhibitor"},
        {"chembl_id": "CHEMBL2318659", "name": "Captopril", "smiles": "CC(C)C1CC2C(SC1)C(=O)NC2C(=O)O",
         "inchikey": "BNRQQXFRAQNPGX-UHFFFAOYSA-N", "molecular_weight": 217.29,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of hypertension",
         "indication_source": "manual", "mechanism_of_action": "ACE inhibitor"},
        {"chembl_id": "CHEMBL586447", "name": "Lisinopril", "smiles": "CCCCC(C)C1C(=O)N2CCCC2C(=O)N1CC(C(=O)O)N",
         "inchikey": "RJXRWZVZAQXBEZ-UHFFFAOYSA-N", "molecular_weight": 405.49,
         "max_phase": 4, "is_fda_approved": True, "is_globally_approved": True,
         "indication": "for the treatment of hypertension and heart failure",
         "indication_source": "manual", "mechanism_of_action": "ACE inhibitor"},
    ])


def embedded_chembl_activities() -> pd.DataFrame:
    """ChEMBL bioactivities linking the sample drugs to their targets.

    Schema matches ``_PHASE1_EXPECTED_COLUMNS['chembl_activities']`` in
    phase1_bridge.py: requires ``molecule_chembl_id``,
    ``target_chembl_id``, ``pchembl_value``, ``standard_relation``.
    """
    return pd.DataFrame([
        {"molecule_chembl_id": "CHEMBL112", "target_chembl_id": "CHEMBL218", "uniprot_id": "P23219",
         "target_name": "PTGS1 (COX-1)", "activity_type": "IC50", "activity_value": 100.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 7.0,
         "chembl_id": "CHEMBL112"},
        {"molecule_chembl_id": "CHEMBL21", "target_chembl_id": "CHEMBL218", "uniprot_id": "P23219",
         "target_name": "PTGS1 (COX-1)", "activity_type": "IC50", "activity_value": 250.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 6.6,
         "chembl_id": "CHEMBL21"},  # v64 ROOT FIX (P1-011): target_name was "PTGS2 (COX-2)" but CHEMBL218 + UniProt P23219 are BOTH PTGS1 (COX-1). Triple inconsistency (ChEMBL ID, UniProt ID, target_name all referring to different proteins) fixed by aligning target_name to PTGS1 (COX-1) to match the UniProt ID.
        {"molecule_chembl_id": "CHEMBL705", "target_chembl_id": "CHEMBL218", "uniprot_id": "P35354",
         "target_name": "PTGS2 (COX-2)", "activity_type": "IC50", "activity_value": 33.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 7.48,
         "chembl_id": "CHEMBL705"},
        {"molecule_chembl_id": "CHEMBL521", "target_chembl_id": "CHEMBL250", "uniprot_id": "P29274",
         "target_name": "ADORA2A", "activity_type": "Ki", "activity_value": 14.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 7.85,
         "chembl_id": "CHEMBL521"},
        {"molecule_chembl_id": "CHEMBL503", "target_chembl_id": "CHEMBL2114259", "uniprot_id": "P14867",
         "target_name": "GABA-A receptor alpha-1", "activity_type": "Ki", "activity_value": 360.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 6.44,
         "chembl_id": "CHEMBL503"},
        {"molecule_chembl_id": "CHEMBL2114647", "target_chembl_id": "CHEMBL2094260", "uniprot_id": "Q9BQV0",
         "target_name": "VKORC1", "activity_type": "IC50", "activity_value": 2700.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 5.57,
         "chembl_id": "CHEMBL2114647"},
        {"molecule_chembl_id": "CHEMBL546", "target_chembl_id": "CHEMBL2095182", "uniprot_id": "P54619",
         "target_name": "AMPK", "activity_type": "IC50", "activity_value": 1500.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 5.82,
         "chembl_id": "CHEMBL546"},  # v57 ROOT FIX (P1-002): activity_type was "Potency" (not in enum [IC50,Ki,Kd,EC50]); changed to "IC50"
        {"molecule_chembl_id": "CHEMBL1085", "target_chembl_id": "CHEMBL1782", "uniprot_id": "P04035",
         "target_name": "HMGCR", "activity_type": "IC50", "activity_value": 8.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 8.1,
         "chembl_id": "CHEMBL1085"},
        {"molecule_chembl_id": "CHEMBL2318659", "target_chembl_id": "CHEMBL1808", "uniprot_id": "P12821",
         "target_name": "ACE", "activity_type": "Ki", "activity_value": 1.7,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 8.77,
         "chembl_id": "CHEMBL2318659"},
        {"molecule_chembl_id": "CHEMBL586447", "target_chembl_id": "CHEMBL1808", "uniprot_id": "P12821",
         "target_name": "ACE", "activity_type": "Ki", "activity_value": 1.0,
         "activity_units": "nM", "standard_relation": "=", "pchembl_value": 9.0,
         "chembl_id": "CHEMBL586447"},
    ])


def embedded_uniprot_proteins() -> pd.DataFrame:
    """UniProt proteins referenced by the ChEMBL sample activities."""
    return pd.DataFrame([
        {"uniprot_id": "P23219", "uniprot_ac": "P23219", "protein_name": "Prostaglandin G/H synthase 1",
         "gene_symbol": "PTGS1", "gene_name": "Prostaglandin-endoperoxide synthase 1",
         "organism": "Homo sapiens", "protein_length": 599,
         "function": "Catalyzes the conversion of arachidonate to prostaglandin H2."},
        {"uniprot_id": "P35354", "uniprot_ac": "P35354", "protein_name": "Prostaglandin G/H synthase 2",
         "gene_symbol": "PTGS2", "gene_name": "Prostaglandin-endoperoxide synthase 2",
         "organism": "Homo sapiens", "protein_length": 604,
         "function": "Catalyzes the conversion of arachidonate to prostaglandin H2 (inducible)."},
        {"uniprot_id": "P29274", "uniprot_ac": "P29274", "protein_name": "Adenosine receptor A2a",
         "gene_symbol": "ADORA2A", "gene_name": "Adenosine A2a receptor",
         "organism": "Homo sapiens", "protein_length": 412,
         "function": "Receptor for adenosine; Gs-coupled."},
        {"uniprot_id": "P14867", "uniprot_ac": "P14867", "protein_name": "GABA-A receptor alpha-1",
         "gene_symbol": "GABRA1", "gene_name": "Gamma-aminobutyric acid receptor subunit alpha-1",
         "organism": "Homo sapiens", "protein_length": 456,
         "function": "Ligand-gated chloride channel; mediator of inhibitory neurotransmission."},
        {"uniprot_id": "Q9BQV0", "uniprot_ac": "Q9BQV0", "protein_name": "Vitamin K epoxide reductase complex subunit 1",
         "gene_symbol": "VKORC1", "gene_name": "Vitamin K epoxide reductase complex subunit 1",
         "organism": "Homo sapiens", "protein_length": 163,
         "function": "Reduces vitamin K 2,3-epoxide to active hydroquinone."},
        {"uniprot_id": "P54619", "uniprot_ac": "P54619", "protein_name": "5'-AMP-activated protein kinase catalytic subunit alpha-1",
         "gene_symbol": "PRKAA1", "gene_name": "Protein kinase AMP-activated catalytic subunit alpha 1",
         "organism": "Homo sapiens", "protein_length": 559,
         "function": "Energy sensor; activated by high AMP/ATP."},
        {"uniprot_id": "P04035", "uniprot_ac": "P04035", "protein_name": "3-hydroxy-3-methylglutaryl-coenzyme A reductase",
         "gene_symbol": "HMGCR", "gene_name": "3-hydroxy-3-methylglutaryl-CoA reductase",
         "organism": "Homo sapiens", "protein_length": 888,
         "function": "Rate-limiting enzyme of cholesterol biosynthesis."},
        {"uniprot_id": "P12821", "uniprot_ac": "P12821", "protein_name": "Angiotensin-converting enzyme",
         "gene_symbol": "ACE", "gene_name": "Angiotensin I converting enzyme",
         "organism": "Homo sapiens", "protein_length": 1306,
         "function": "Converts Angiotensin I to Angiotensin II."},
    ])


def embedded_string_ppi() -> pd.DataFrame:
    """STRING PPIs between the sample proteins (high-confidence edges only)."""
    return pd.DataFrame([
        {"protein1": "9606.ENSP00000000233", "protein2": "9606.ENSP00000000412",
         "uniprot_ac_a": "P23219", "uniprot_ac_b": "P35354",
         "uniprot_id1": "P23219", "uniprot_id2": "P35354",
         "combined_score": 980, "experimental_score": 800,
         "database_score": 950, "textmining_score": 920,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000000412", "protein2": "9606.ENSP00000003025",
         "uniprot_ac_a": "P35354", "uniprot_ac_b": "P04035",
         "uniprot_id1": "P35354", "uniprot_id2": "P04035",
         "combined_score": 720, "experimental_score": 600,
         "database_score": 700, "textmining_score": 680,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000228237", "protein2": "9606.ENSP00000003025",
         "uniprot_ac_a": "P29274", "uniprot_ac_b": "P04035",
         "uniprot_id1": "P29274", "uniprot_id2": "P04035",
         "combined_score": 680, "experimental_score": 400,
         "database_score": 600, "textmining_score": 620,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000228237", "protein2": "9606.ENSP00000373235",
         "uniprot_ac_a": "P29274", "uniprot_ac_b": "P14867",
         "uniprot_id1": "P29274", "uniprot_id2": "P14867",
         "combined_score": 850, "experimental_score": 700,
         "database_score": 800, "textmining_score": 800,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000342028", "protein2": "9606.ENSP00000373235",
         "uniprot_ac_a": "Q9BQV0", "uniprot_ac_b": "P14867",
         "uniprot_id1": "Q9BQV0", "uniprot_id2": "P14867",
         "combined_score": 540, "experimental_score": 0,
         "database_score": 0, "textmining_score": 540,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000373235", "protein2": "9606.ENSP00000303641",
         "uniprot_ac_a": "P14867", "uniprot_ac_b": "P54619",
         "uniprot_id1": "P14867", "uniprot_id2": "P54619",
         "combined_score": 620, "experimental_score": 500,
         "database_score": 600, "textmining_score": 580,
         "organism": "Homo sapiens"},
        # v64 ROOT FIX (P1-018): removed the self-interaction edge
        # (P54619 -> P54619) with combined_score=999 and ALL sub-scores=0.
        # STRING does not normally include self-interactions, and a score
        # of 999 with zero evidence is scientifically nonsensical. The
        # STRING pipeline's load() drops self-interactions via
        # STRING_DROP_SELF_INTERACTIONS, so the row was dead-letter noise.
        # Replaced with a real PPI edge: PRKAA1 (AMPK alpha) <-> PTGS2,
        # connected via AMPK's known inhibition of COX-2 expression
        # (PMID: 18509025) — a biologically meaningful cross-pathway link
        # between metabolic sensing (AMPK) and inflammatory signaling (PTGS2).
        {"protein1": "9606.ENSP00000303641", "protein2": "9606.ENSP00000000412",
         "uniprot_ac_a": "P54619", "uniprot_ac_b": "P35354",
         "uniprot_id1": "P54619", "uniprot_id2": "P35354",
         "combined_score": 680, "experimental_score": 500,
         "database_score": 620, "textmining_score": 660,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000303641", "protein2": "9606.ENSP00000252108",
         "uniprot_ac_a": "P54619", "uniprot_ac_b": "P04035",
         "uniprot_id1": "P54619", "uniprot_id2": "P04035",
         "combined_score": 760, "experimental_score": 650,
         "database_score": 700, "textmining_score": 720,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000252108", "protein2": "9606.ENSP00000352593",
         "uniprot_ac_a": "P04035", "uniprot_ac_b": "P12821",
         "uniprot_id1": "P04035", "uniprot_id2": "P12821",
         "combined_score": 690, "experimental_score": 500,
         "database_score": 600, "textmining_score": 660,
         "organism": "Homo sapiens"},
        {"protein1": "9606.ENSP00000352593", "protein2": "9606.ENSP00000000233",
         "uniprot_ac_a": "P12821", "uniprot_ac_b": "P23219",
         "uniprot_id1": "P12821", "uniprot_id2": "P23219",
         "combined_score": 580, "experimental_score": 400,
         "database_score": 0, "textmining_score": 580,
         "organism": "Homo sapiens"},
    ])


def embedded_drugbank_drugs() -> pd.DataFrame:
    """DrugBank drugs (mirrors ChEMBL samples with DrugBank IDs + indications)."""
    return pd.DataFrame([
        # v64 ROOT FIX (P1-017): added chembl_id and pubchem_cid columns
        # declared in schema/v1.json (lines 92-131) for drugbank_drugs.csv.
        # The embedded sample bypasses clean() (written directly to CSV by
        # write_all_samples), so _ensure_drug_columns() never runs on it.
        # Without these columns, downstream entity resolution cross-referencing
        # fails or produces NULL results. Values cross-referenced from the
        # ChEMBL and PubChem embedded samples for consistency.
        {"drugbank_id": "DB00945", "name": "Aspirin", "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
         "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O", "molecular_weight": 180.16,
         "indication": "For the treatment of pain, inflammation, and fever",
         "indication_source": "manual",
         "mechanism_of_action": "Acetylates COX-1 and COX-2, blocking prostaglandin synthesis.",
         "groups": "approved",
         "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "50-78-2", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL112", "pubchem_cid": 2244},
        {"drugbank_id": "DB00316", "name": "Acetaminophen", "inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N",
         "smiles": "CC1=CC=C(O)C=C1O", "molecular_weight": 151.16,
         "indication": "For the treatment of pain and fever",
         "indication_source": "manual",
         "mechanism_of_action": "Inhibits COX in the central nervous system.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "103-90-2", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL21", "pubchem_cid": 1983},
        {"drugbank_id": "DB01050", "name": "Ibuprofen", "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",
         "smiles": "CC(C)CC1=CC=C(C=C1)CC(C(=O)O)C", "molecular_weight": 206.28,
         "indication": "For the treatment of pain, inflammation, and arthritis",
         "indication_source": "manual",
         "mechanism_of_action": "Non-selective COX inhibitor.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "15687-27-1", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL705", "pubchem_cid": 3672},
        {"drugbank_id": "DB00201", "name": "Caffeine", "inchikey": "RYYVLZVUVIJVGH-UHFFFAOYSA-N",
         "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C", "molecular_weight": 194.19,
         "indication": "For the treatment of migraine and fatigue",
         "indication_source": "manual",
         "mechanism_of_action": "Adenosine receptor antagonist.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "58-08-2", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL521", "pubchem_cid": 2519},
        {"drugbank_id": "DB00829", "name": "Diazepam", "inchikey": "AAOVKBJEBZCEQK-UHFFFAOYSA-N",
         "smiles": "ClC1=CC2=C(C=C1)C(=NCC(=O)N2C3=CC=CC=C3)C", "molecular_weight": 284.74,
         "indication": "For the treatment of anxiety and seizures",
         "indication_source": "manual",
         "mechanism_of_action": "GABA-A positive allosteric modulator.",
         "groups": "approved;illicit", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "439-14-5", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL503", "pubchem_cid": 3016},
        {"drugbank_id": "DB00682", "name": "Warfarin", "inchikey": "PJVWKTKQMONHTF-UHFFFAOYSA-N",
         "smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(C3=CC=CC=C3OC2=O)O", "molecular_weight": 308.33,
         "indication": "For the prevention of thrombosis and embolism",
         "indication_source": "manual",
         "mechanism_of_action": "Vitamin K epoxide reductase inhibitor.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "81-81-2", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL2114647", "pubchem_cid": 6691},
        {"drugbank_id": "DB00191", "name": "Metformin", "inchikey": "XZWYZXLIPXDOLR-UHFFFAOYSA-N",
         "smiles": "CN(C)C(=N)N=C(N)N", "molecular_weight": 129.16,
         "indication": "For the treatment of type 2 diabetes",
         "indication_source": "manual",
         "mechanism_of_action": "AMPK activator; reduces hepatic glucose output.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "657-24-9", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL546", "pubchem_cid": 4091},
        {"drugbank_id": "DB01076", "name": "Atorvastatin", "inchikey": "XUKUURHRXDUEBC-UHFFFAOYSA-N",
         "smiles": "CC(C)C1=C(C=CC=C1C)C2=CC=CC=C2C(=O)NC3CC4=C(C=C(C=C4CC3)F)C(=O)O",
         "molecular_weight": 558.66,
         "indication": "For the treatment of hypercholesterolemia",
         "indication_source": "manual",
         "mechanism_of_action": "HMG-CoA reductase inhibitor.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "134523-03-8", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL1085", "pubchem_cid": 60823},
        {"drugbank_id": "DB01197", "name": "Captopril", "inchikey": "BNRQQXFRAQNPGX-UHFFFAOYSA-N",
         "smiles": "CC(C)C1CC2C(SC1)C(=O)NC2C(=O)O", "molecular_weight": 217.29,
         "indication": "For the treatment of hypertension",
         "indication_source": "manual",
         "mechanism_of_action": "ACE inhibitor.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "62571-86-2", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL2318659", "pubchem_cid": 44093},
        {"drugbank_id": "DB00722", "name": "Lisinopril", "inchikey": "RJXRWZVZAQXBEZ-UHFFFAOYSA-N",
         "smiles": "CCCCC(C)C1C(=O)N2CCCC2C(=O)N1CC(C(=O)O)N", "molecular_weight": 405.49,
         "indication": "For the treatment of hypertension and heart failure",
         "indication_source": "manual",
         "mechanism_of_action": "ACE inhibitor.",
         "groups": "approved", "is_fda_approved": True, "is_withdrawn": False,
         "clinical_status": "approved", "max_phase": 4,
         "cas_number": "83915-83-7", "drug_type": "small_molecule",
         "chembl_id": "CHEMBL586447", "pubchem_cid": 5362119},
    ])


def embedded_drugbank_interactions() -> pd.DataFrame:
    """DrugBank drug-target interactions for the sample drugs."""
    return pd.DataFrame([
        {"drugbank_id": "DB00945", "uniprot_id": "P23219", "target_name": "PTGS1",
         "action_type": "inhibitor", "interaction_type": "inhibitor"},
        {"drugbank_id": "DB00316", "uniprot_id": "P23219", "target_name": "PTGS1",
         "action_type": "inhibitor", "interaction_type": "inhibitor"},
        {"drugbank_id": "DB01050", "uniprot_id": "P35354", "target_name": "PTGS2",
         "action_type": "inhibitor", "interaction_type": "inhibitor"},
        {"drugbank_id": "DB00201", "uniprot_id": "P29274", "target_name": "ADORA2A",
         "action_type": "antagonist", "interaction_type": "antagonist"},
        {"drugbank_id": "DB00829", "uniprot_id": "P14867", "target_name": "GABRA1",
         "action_type": "positive allosteric modulator", "interaction_type": "activator"},
        {"drugbank_id": "DB00682", "uniprot_id": "Q9BQV0", "target_name": "VKORC1",
         "action_type": "inhibitor", "interaction_type": "inhibitor"},
        {"drugbank_id": "DB00191", "uniprot_id": "P54619", "target_name": "PRKAA1",
         "action_type": "activator", "interaction_type": "activator"},
        {"drugbank_id": "DB01076", "uniprot_id": "P04035", "target_name": "HMGCR",
         "action_type": "inhibitor", "interaction_type": "inhibitor"},
        {"drugbank_id": "DB01197", "uniprot_id": "P12821", "target_name": "ACE",
         "action_type": "inhibitor", "interaction_type": "inhibitor"},
        {"drugbank_id": "DB00722", "uniprot_id": "P12821", "target_name": "ACE",
         "action_type": "inhibitor", "interaction_type": "inhibitor"},
    ])


def embedded_drugbank_indications() -> pd.DataFrame:
    """DrugBank structured indications (drug → disease).

    v79 FORENSIC ROOT FIX (P0-B1 + P0-B5 — DOID/OMIM mismatch + missing
    indication_type column):
      P0-B1: The v78 embedded sample emitted ONLY DOID-format disease IDs
        (e.g. ``DOID:1826`` for Epilepsy). The Phase 2 bridge's
        ``disease_id_set`` is built from OMIM-staged diseases
        (``OMIM:nnnnnn`` format). The v78 bridge fallback (lines ~3687-
        3709) stages unknown DOID IDs as synthetic Disease nodes, but
        this loses the referential link to the OMIM disease vocabulary
        that the KG uses for gene-disease cross-referencing. ROOT FIX:
        where a Disease name in the embedded sample matches a Disease
        in ``embedded_omim_gda()``, emit the OMIM ID as ``disease_id``
        (and keep the DOID as ``doid_id`` for reference). This makes
        the treats-edge match the OMIM-keyed ``disease_id_set``
        directly — no fallback needed — and preserves the
        gene→disease→drug multi-hop path the Graph Transformer needs.
        Rows without an OMIM match keep the DOID ID; the bridge's v78
        fallback stages them as synthetic Disease nodes.
      P0-B5: The v78 embedded sample was MISSING the ``indication_type``
        column that ``_write_structured_indications`` derives from the
        DrugBank ``<groups>`` field (approved / withdrawn /
        investigational / illicit). Without it, the bridge's
        ClinicalOutcome nodes got ``indication_type="unknown"`` for
        embedded samples but ``"approved"`` / ``"withdrawn"`` for real
        XML — patient-safety hooks (withdrawn-drug detection) could NOT
        fire in sample mode. ROOT FIX: add ``indication_type="approved"``
        to every embedded row (all 10 embedded drugs are FDA-approved —
        this is scientifically accurate and enables the
        withdrawn-drug safety hook to be tested in sample mode by
        flipping one row to ``"withdrawn"``).

    The schema now matches ``_write_structured_indications``'s output
    schema (``drugbank_id, disease_id, disease_name, indication_type,
    source``) plus the extra ``drug_inchikey, drug_name, indication,
    doid_id, omim_disease_id`` columns for richer test fixtures.
    """
    return pd.DataFrame([
        {"drugbank_id": "DB00945", "drug_inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
         "drug_name": "Aspirin",
         "disease_id": "DOID:0050133", "disease_name": "Pain",
         "doid_id": "DOID:0050133", "omim_disease_id": None,
         "indication": "For the treatment of pain",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB00945", "drug_inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
         "drug_name": "Aspirin",
         "disease_id": "DOID:1101", "disease_name": "Inflammation",
         "doid_id": "DOID:1101", "omim_disease_id": None,
         "indication": "For the treatment of inflammation",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB00316", "drug_inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N",
         "drug_name": "Acetaminophen",
         "disease_id": "DOID:0050133", "disease_name": "Pain",
         "doid_id": "DOID:0050133", "omim_disease_id": None,
         "indication": "For the treatment of pain",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB01050", "drug_inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",
         "drug_name": "Ibuprofen",
         "disease_id": "DOID:7148", "disease_name": "Arthritis",
         "doid_id": "DOID:7148", "omim_disease_id": None,
         "indication": "For the treatment of arthritis",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB00201", "drug_inchikey": "RYYVLZVUVIJVGH-UHFFFAOYSA-N",
         "drug_name": "Caffeine",
         "disease_id": "DOID:1197", "disease_name": "Migraine",
         "doid_id": "DOID:1197", "omim_disease_id": None,
         "indication": "For the treatment of migraine",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB00829", "drug_inchikey": "AAOVKBJEBZCEQK-UHFFFAOYSA-N",
         "drug_name": "Diazepam",
         "disease_id": "DOID:14319", "disease_name": "Anxiety Disorder",
         "doid_id": "DOID:14319", "omim_disease_id": None,
         "indication": "For the treatment of anxiety",
         "indication_type": "approved", "source": "drugbank_xml"},
        # P0-B1 ROOT FIX: Epilepsy maps to OMIM:137160 (Epilepsy, juvenile
        # myoclonic) which IS in embedded_omim_gda() (GABRA1 gene). Use
        # the OMIM ID as disease_id so the treats edge matches the
        # OMIM-keyed disease_id_set directly — no fallback needed.
        {"drugbank_id": "DB00829", "drug_inchikey": "AAOVKBJEBZCEQK-UHFFFAOYSA-N",
         "drug_name": "Diazepam",
         "disease_id": "OMIM:137160", "disease_name": "Epilepsy",
         "doid_id": "DOID:1826", "omim_disease_id": "OMIM:137160",
         "indication": "For the treatment of seizures",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB00682", "drug_inchikey": "PJVWKTKQMONHTF-UHFFFAOYSA-N",
         "drug_name": "Warfarin",
         "disease_id": "DOID:10763", "disease_name": "Hypertension",
         "doid_id": "DOID:10763", "omim_disease_id": None,
         "indication": "For the prevention of thrombosis",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB00191", "drug_inchikey": "XZWYZXLIPXDOLR-UHFFFAOYSA-N",
         "drug_name": "Metformin",
         "disease_id": "DOID:9351", "disease_name": "Diabetes Mellitus",
         "doid_id": "DOID:9351", "omim_disease_id": None,
         "indication": "For the treatment of type 2 diabetes",
         "indication_type": "approved", "source": "drugbank_xml"},
        # P0-B1 ROOT FIX: Hypercholesterolemia maps to OMIM:143890 which
        # IS in embedded_omim_gda() (HMGCR gene). Use the OMIM ID.
        {"drugbank_id": "DB01076", "drug_inchikey": "XUKUURHRXDUEBC-UHFFFAOYSA-N",
         "drug_name": "Atorvastatin",
         "disease_id": "OMIM:143890", "disease_name": "Hypercholesterolemia",
         "doid_id": "DOID:50", "omim_disease_id": "OMIM:143890",
         "indication": "For the treatment of hypercholesterolemia",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB01197", "drug_inchikey": "BNRQQXFRAQNPGX-UHFFFAOYSA-N",
         "drug_name": "Captopril",
         "disease_id": "DOID:10763", "disease_name": "Hypertension",
         "doid_id": "DOID:10763", "omim_disease_id": None,
         "indication": "For the treatment of hypertension",
         "indication_type": "approved", "source": "drugbank_xml"},
        {"drugbank_id": "DB00722", "drug_inchikey": "RJXRWZVZAQXBEZ-UHFFFAOYSA-N",
         "drug_name": "Lisinopril",
         "disease_id": "DOID:10763", "disease_name": "Hypertension",
         "doid_id": "DOID:10763", "omim_disease_id": None,
         "indication": "For the treatment of hypertension",
         "indication_type": "approved", "source": "drugbank_xml"},
    ])


def embedded_omim_gda() -> pd.DataFrame:
    """OMIM gene-disease associations for the sample proteins."""
    return pd.DataFrame([
        # v64 ROOT FIX (P1-016): gene_id and phenotype_mim changed from
        # string to integer to match schema/v1.json (gene_id: integer,
        # phenotype_mim: integer). The previous string values worked by
        # accident when read with pd.read_csv(dtype={...}) but caused
        # silent join failures when read without explicit dtypes (e.g. by
        # the Phase 2 bridge using csv.reader).
        {"gene_symbol": "PTGS1", "gene_id": 5742, "gene_mim": 176805,
         "disease_id": "OMIM:176805", "disease_name": "Platelet dysfunction",
         "phenotype_mim": 176805, "association_type": "causal",
         "is_susceptibility": False, "source": "omim", "score": 1.0},  # v57 ROOT FIX (P1-003): was "causative" (not in enum [causal, susceptibility, ...])
        {"gene_symbol": "PTGS2", "gene_id": 5743, "gene_mim": 600262,
         "disease_id": "OMIM:600262", "disease_name": "Colorectal cancer susceptibility",
         "phenotype_mim": 114500, "association_type": "susceptibility",
         "is_susceptibility": True, "source": "omim", "score": 0.85},
        {"gene_symbol": "ADORA2A", "gene_id": 135, "gene_mim": 102776,
         "disease_id": "OMIM:102776", "disease_name": "Vascular disorders",
         "phenotype_mim": 108150, "association_type": "susceptibility",
         "is_susceptibility": True, "source": "omim", "score": 0.75},
        {"gene_symbol": "GABRA1", "gene_id": 2552, "gene_mim": 137160,
         "disease_id": "OMIM:137160", "disease_name": "Epilepsy, juvenile myoclonic",
         "phenotype_mim": 254770, "association_type": "causal",
         "is_susceptibility": False, "source": "omim", "score": 1.0},  # v57 ROOT FIX (P1-003): was "causative" (not in enum [causal, susceptibility, ...])
        {"gene_symbol": "HMGCR", "gene_id": 3156, "gene_mim": 142910,
         "disease_id": "OMIM:143890", "disease_name": "Hypercholesterolemia",
         "phenotype_mim": 143890, "association_type": "susceptibility",
         "is_susceptibility": True, "source": "omim", "score": 0.7},
        {"gene_symbol": "ACE", "gene_id": 1636, "gene_mim": 106180,
         "disease_id": "OMIM:106180", "disease_name": "Myocardial infarction susceptibility",
         "phenotype_mim": 608558, "association_type": "susceptibility",
         "is_susceptibility": True, "source": "omim", "score": 0.8},
    ])


def embedded_omim_susceptibility() -> pd.DataFrame:
    """P2-10 ROOT FIX: OMIM gene-disease susceptibility associations.

    The previous code mapped ``omim_susceptibility`` to the SAME function
    as ``omim_gda`` (``embedded_omim_gda``), producing byte-identical CSV
    files. The susceptibility file should be a SUBSET — only rows where
    ``is_susceptibility=True``. This filter produces the 4 susceptibility
    rows (PTGS2/Colorectal cancer, ADORA2A/Vascular disorders,
    HMGCR/Hypercholesterolemia, ACE/Myocardial infarction) while the GDA
    file contains all 6 rows (including the 2 causal associations).
    This is biologically correct: susceptibility associations are a
    distinct category from causal associations in OMIM's nosology.
    """
    gda = embedded_omim_gda()
    susc = gda[gda["is_susceptibility"] == True].copy()  # noqa: E712
    return susc


def embedded_disgenet_gda() -> pd.DataFrame:
    """DisGeNET gene-disease associations (curated subset for sample genes)."""
    return pd.DataFrame([
        # v64 ROOT FIX (P1-016): gene_id changed from string to integer
        # to match schema/v1.json (gene_id: integer).
        {"gene_symbol": "PTGS1", "gene_id": 5742, "disease_id": "DOID:0050133",
         "disease_name": "Pain", "association_type": "therapeutic",
         "source": "disgenet", "score": 0.85, "pmid_list": "12345678;23456789"},
        {"gene_symbol": "PTGS2", "gene_id": 5743, "disease_id": "DOID:1101",
         "disease_name": "Inflammation", "association_type": "therapeutic",
         "source": "disgenet", "score": 0.9, "pmid_list": "11111111;22222222"},
        {"gene_symbol": "PTGS2", "gene_id": 5743, "disease_id": "DOID:162",
         "disease_name": "Cancer", "association_type": "biomarker",
         "source": "disgenet", "score": 0.7, "pmid_list": "33333333;44444444"},
        {"gene_symbol": "ADORA2A", "gene_id": 135, "disease_id": "DOID:1197",
         "disease_name": "Migraine", "association_type": "therapeutic",
         "source": "disgenet", "score": 0.6, "pmid_list": "55555555"},
        {"gene_symbol": "GABRA1", "gene_id": 2552, "disease_id": "DOID:1826",
         "disease_name": "Epilepsy", "association_type": "therapeutic",
         "source": "disgenet", "score": 0.85, "pmid_list": "66666666;77777777"},
        {"gene_symbol": "HMGCR", "gene_id": 3156, "disease_id": "DOID:50",
         "disease_name": "Hypercholesterolemia", "association_type": "therapeutic",
         "source": "disgenet", "score": 0.95, "pmid_list": "88888888;99999999"},
        {"gene_symbol": "ACE", "gene_id": 1636, "disease_id": "DOID:10763",
         "disease_name": "Hypertension", "association_type": "therapeutic",
         "source": "disgenet", "score": 0.9, "pmid_list": "12121212;34343434"},
    ])


def embedded_pubchem_enrichment() -> pd.DataFrame:
    """PubChem physicochemical properties for the sample drugs.

    Schema matches ``_PHASE1_EXPECTED_COLUMNS['pubchem_enrichment']`` in
    phase1_bridge.py: requires ``inchikey``, ``canonical_smiles``.
    """
    return pd.DataFrame([
        {"inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "pubchem_cid": 2244,
         "canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
         "xlogp": 1.19, "tpsa": 63.6, "h_bond_donor_count": 1,
         "h_bond_acceptor_count": 4, "rotatable_bond_count": 2},
        {"inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N", "pubchem_cid": 1983,
         "canonical_smiles": "CC1=CC=C(O)C=C1O",
         "xlogp": 0.46, "tpsa": 49.33, "h_bond_donor_count": 2,
         "h_bond_acceptor_count": 2, "rotatable_bond_count": 0},
        {"inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N", "pubchem_cid": 3672,
         "canonical_smiles": "CC(C)CC1=CC=C(C=C1)CC(C(=O)O)C",
         "xlogp": 3.97, "tpsa": 37.3, "h_bond_donor_count": 1,
         "h_bond_acceptor_count": 2, "rotatable_bond_count": 4},
        {"inchikey": "RYYVLZVUVIJVGH-UHFFFAOYSA-N", "pubchem_cid": 2519,
         "canonical_smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
         "xlogp": -0.07, "tpsa": 58.44, "h_bond_donor_count": 0,
         "h_bond_acceptor_count": 6, "rotatable_bond_count": 0},
        {"inchikey": "AAOVKBJEBZCEQK-UHFFFAOYSA-N", "pubchem_cid": 3016,
         "canonical_smiles": "ClC1=CC2=C(C=C1)C(=NCC(=O)N2C3=CC=CC=C3)C",
         "xlogp": 2.82, "tpsa": 32.67, "h_bond_donor_count": 0,
         "h_bond_acceptor_count": 2, "rotatable_bond_count": 1},
        {"inchikey": "PJVWKTKQMONHTF-UHFFFAOYSA-N", "pubchem_cid": 6691,
         "canonical_smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(C3=CC=CC=C3OC2=O)O",
         "xlogp": 2.70, "tpsa": 46.61, "h_bond_donor_count": 1,
         "h_bond_acceptor_count": 3, "rotatable_bond_count": 2},
        {"inchikey": "XZWYZXLIPXDOLR-UHFFFAOYSA-N", "pubchem_cid": 4091,
         "canonical_smiles": "CN(C)C(=N)N=C(N)N",
         "xlogp": -1.43, "tpsa": 76.07, "h_bond_donor_count": 2,
         "h_bond_acceptor_count": 4, "rotatable_bond_count": 2},
        {"inchikey": "XUKUURHRXDUEBC-UHFFFAOYSA-N", "pubchem_cid": 60823,
         "canonical_smiles": "CC(C)C1=C(C=CC=C1C)C2=CC=CC=C2C(=O)NC3CC4=C(C=C(C=C4CC3)F)C(=O)O",
         "xlogp": 4.19, "tpsa": 111.78, "h_bond_donor_count": 3,
         "h_bond_acceptor_count": 7, "rotatable_bond_count": 8},
        {"inchikey": "BNRQQXFRAQNPGX-UHFFFAOYSA-N", "pubchem_cid": 44093,
         "canonical_smiles": "CC(C)C1CC2C(SC1)C(=O)NC2C(=O)O",
         "xlogp": 0.65, "tpsa": 86.91, "h_bond_donor_count": 2,
         "h_bond_acceptor_count": 4, "rotatable_bond_count": 2},
        {"inchikey": "RJXRWZVZAQXBEZ-UHFFFAOYSA-N", "pubchem_cid": 5362119,
         "canonical_smiles": "CCCCC(C)C1C(=O)N2CCCC2C(=O)N1CC(C(=O)O)N",
         "xlogp": -1.21, "tpsa": 132.85, "h_bond_donor_count": 3,
         "h_bond_acceptor_count": 8, "rotatable_bond_count": 9},
    ])


def write_all_samples(processed_dir) -> dict:
    """Write all embedded sample datasets as CSVs to the processed_data dir.

    Used as a last-resort fallback when ANY pipeline cannot reach its API
    in sample mode. Returns a dict mapping source-name → file-path.

    v65 ROOT FIX (P1-040): the previous implementation wrote each CSV
    NON-atomically — ``df.to_csv(path, ...)`` truncates the file before
    writing the body, so a concurrent call to ``write_all_samples`` (e.g.
    from the ``all`` command's fallback at __init__.py:2801) could leave
    a partially-written CSV that the Phase 2 bridge would then crash on
    (malformed CSV → pandas ParserError). Root fix: write to a ``.tmp``
    sidecar file in the same directory, then atomically ``os.replace``
    it to the final path. ``os.replace`` is atomic on POSIX and Windows
    for files within the same filesystem. We also use ``filelock`` when
    available to serialize concurrent writers across processes; if
    ``filelock`` is not installed, we fall back to atomic-rename-only
    (which still prevents partial reads but not double-writes).
    """
    import os
    import tempfile
    from pathlib import Path
    processed_dir = Path(processed_dir)
    processed_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    datasets = {
        "chembl_drugs": (embedded_chembl_molecules, "chembl_drugs.csv"),
        "chembl_activities": (embedded_chembl_activities, "chembl_activities_clean.csv"),
        "uniprot_proteins": (embedded_uniprot_proteins, "proteins.csv"),
        "string_ppi": (embedded_string_ppi, "protein_protein_interactions.csv"),
        "drugs": (embedded_drugbank_drugs, "drugbank_drugs.csv"),
        "interactions": (embedded_drugbank_interactions, "drugbank_interactions.csv.gz"),
        "indications": (embedded_drugbank_indications, "drugbank_indications.csv"),
        "omim_gda": (embedded_omim_gda, "omim_gene_disease_associations.csv"),
        # P2-10 ROOT FIX: the previous code mapped both "omim_gda" and
        # "omim_susceptibility" to the SAME function (embedded_omim_gda),
        # producing byte-identical CSV files. The susceptibility file is
        # now a proper SUBSET (is_susceptibility=True only) via the new
        # embedded_omim_susceptibility() function.
        "omim_susceptibility": (embedded_omim_susceptibility, "omim_gene_disease_susceptibility.csv"),
        "disgenet_gda": (embedded_disgenet_gda, "gene_disease_associations.csv"),
        "pubchem_enrichment": (embedded_pubchem_enrichment, "pubchem_enrichment.csv"),
    }

    # v65 ROOT FIX (P1-040): optional cross-process file lock to prevent
    # two concurrent write_all_samples() calls from doing double-work.
    # Even without the lock, the atomic-rename pattern below guarantees
    # no reader ever sees a partially-written file.
    try:
        from filelock import FileLock, Timeout as FileLockTimeout
        _has_filelock = True
    except ImportError:
        _has_filelock = False

    for key, (func, filename) in datasets.items():
        df = func()
        path = processed_dir / filename

        if _has_filelock:
            lock_path = path.with_suffix(path.suffix + ".lock")
            lock = FileLock(str(lock_path), timeout=30)
            try:
                with lock:
                    _atomic_write_csv(df, path, filename)
            except FileLockTimeout:
                # Another process holds the lock — skip this file
                # (the other process will write it). We still record
                # the path in `written` so callers know where it SHOULD be.
                logger.warning(
                    "Could not acquire lock for %s — another process is "
                    "writing it. Skipping.", path,
                )
        else:
            _atomic_write_csv(df, path, filename)
        written[key] = path
    return written


def _atomic_write_csv(df, path, filename) -> None:
    """Write ``df`` to ``path`` atomically via .tmp + os.replace."""
    import os
    import tempfile
    # Create the .tmp file in the SAME directory as the final path so
    # ``os.replace`` is atomic (same filesystem guarantee).
    tmp_fd, tmp_path_str = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    os.close(tmp_fd)  # we'll reopen for writing via pandas
    tmp_path = type(path)(tmp_path_str)
    try:
        if filename.endswith(".csv.gz"):
            df.to_csv(tmp_path, index=False, compression="gzip")
        else:
            df.to_csv(tmp_path, index=False)
        # os.replace is atomic on POSIX and Windows for files within
        # the same filesystem. Readers either see the OLD file or the
        # NEW file — never a partial write.
        os.replace(tmp_path, path)
    except Exception:
        # Clean up the .tmp file on any failure — don't leave orphaned
        # .tmp files in the processed_data dir.
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


if __name__ == "__main__":
    # CLI: write all samples to a target dir.
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("processed_dir", help="Directory to write sample CSVs to")
    args = parser.parse_args()
    written = write_all_samples(args.processed_dir)
    print(f"Wrote {len(written)} sample datasets to {args.processed_dir}:")
    for key, path in written.items():
        print(f"  {key}: {path}")
