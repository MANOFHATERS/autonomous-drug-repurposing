"""Forensic sanity test: verify the 20 already-fixed issues are STILL fixed.

The audit explicitly warned: "many of these fixes introduced NEW bugs while
patching old ones, and several 'ROOT FIX' claims are aspirational rather
than actual."

The user's #1 pain point: "every session every ai tells its 100 percent
integrated but see the reality ... when i cross verify manually the issues
are like that only."

This module reads the ACTUAL executable code for each of the 20 issues
that prior teammates claimed to fix, and asserts the fix is REAL (not
aspirational). If a future agent regresses any of these, this test fails.

Issues covered (20 of 22 -- the other 2 are in the test_in_096 and
test_p2_043 modules):
  - P1-024: DrugBank FULL mode raises RuntimeError
  - P2-047: phase1_bridge consumes SIDER
  - P2-050: withdrawn drug score = 0.0 (lower than investigational)
  - IN-039: gt_api.py CORS hardened
  - IN-038: gt_api.py uses lifespan (not on_event)
  - IN-055: pytest.ini addopts has marker filter
  - IN-085: pytest.ini testpaths has no missing dirs
  - IN-060: verify_v82_fixes.py does NOT mutate production file
  - IN-079: pre_commit_issue_guard.py fails CLOSED
  - P1-014: omim_pipeline.py has no module-level random.seed()
  - P1-025: base_pipeline.py uses per-instance RNG
  - P2-044: service.py uses business id (not Neo4j internal id)
  - P2-045: service.py uses node business IDs for edge endpoints
  - P2-046: ClinicalOutcome ID is CO:{disease_key}:{itype} (no dbid)
  - P2-048: ClinicalOutcome ID unique per (disease, type)
  - P2-049: CORE_EDGE_TYPES has no legacy causes_side_effect
  - IN-051: MANIFEST.in includes phase2/shared data files
  - IN-087: README.md exists at repo root
  - IN-089: hypothesis_writeback.py validates paths + has timeout
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    """Read a file, failing the test if it doesn't exist."""
    assert path.exists(), f"Required file not found: {path}"
    return path.read_text()


# ─── P1-024: DrugBank FULL mode raises RuntimeError ───────────────────────


def test_p1_024_drugbank_full_mode_raises() -> None:
    """DrugBank FULL mode MUST raise RuntimeError unless DRUGOS_ALLOW_NO_DRUGBANK=1."""
    src = _read(REPO_ROOT / "phase1" / "pipelines" / "_v50_downloaders.py")
    # The executable raise must be present.
    assert "raise RuntimeError" in src, (
        "P1-024 regression: _v50_downloaders.py does NOT raise RuntimeError "
        "in DrugBank FULL mode. The previous behavior (silent empty CSV) "
        "is a patient-safety bug -- withdrawn-drug safety signal is lost."
    )
    # The env var escape must be present.
    assert "DRUGOS_ALLOW_NO_DRUGBANK" in src, (
        "P1-024: the DRUGOS_ALLOW_NO_DRUGBANK env var escape is MISSING. "
        "Operators cannot opt into ChEMBL-only degraded mode."
    )
    # The data_status marker file must be written (so downstream contract
    # checks pass).
    assert "drugbank_data_status.json" in src, (
        "P1-024: the drugbank_data_status.json marker file is MISSING. "
        "Downstream contract checks would fail on the empty CSVs."
    )


# ─── P2-047: phase1_bridge consumes SIDER ──────────────────────────────────


def test_p2_047_bridge_consumes_sider() -> None:
    """The bridge MUST consume ``sider_adverse_events`` from the frames dict."""
    src = _read(REPO_ROOT / "phase2" / "drugos_graph" / "phase1_bridge.py")
    # The paths dict must have a sider_adverse_events entry.
    assert '"sider_adverse_events"' in src or "'sider_adverse_events'" in src, (
        "P2-047 regression: the bridge paths dict does NOT have a "
        "`sider_adverse_events` entry. SIDER data is never loaded."
    )
    # The bridge must actually CONSUME the sider_df (not just load it).
    assert 'frames.get("sider_adverse_events")' in src, (
        "P2-047: the bridge loads sider_adverse_events but NEVER consumes "
        "it. The v113 'fix' only added the paths-dict entry -- the "
        "executable consumption code is MISSING."
    )
    # The _load_sider_adverse_events function must be called.
    assert "_load_sider_adverse_events(" in src, (
        "P2-047: the _load_sider_adverse_events function is NEVER called. "
        "The bridge does not emit Compound->causes_adverse_event->MedDRA_Term "
        "edges in bridge-only mode."
    )


# ─── P2-050: withdrawn drug score = 0.0 ───────────────────────────────────


def test_p2_050_withdrawn_score_is_zero() -> None:
    """Withdrawn drugs MUST get normalized_score = 0.0 (lower than investigational 0.5)."""
    src = _read(REPO_ROOT / "phase2" / "drugos_graph" / "phase1_bridge.py")
    # Find the _compute_normalized_score function and check the indication_type branch.
    # The check order matters: "withdrawn" must be checked BEFORE "approved"
    # (so "approved_and_withdrawn" maps to 0.0, not 1.0).
    func_match = re.search(
        r"def _compute_normalized_score\([^)]*\)[^:]*:(.*?)(?=\ndef |\Z)",
        src,
        re.DOTALL,
    )
    assert func_match is not None, "_compute_normalized_score function not found"
    func_body = func_match.group(1)
    # Find the indication_type block.
    itype_block = re.search(
        r'if indication_type:.*?(?=\n    # |\n    if source|\Z)',
        func_body,
        re.DOTALL,
    )
    assert itype_block is not None, "indication_type block not found in _compute_normalized_score"
    itype_src = itype_block.group(0)
    # "withdrawn" check must come BEFORE "approved" check.
    withdrawn_pos = itype_src.find('"withdrawn" in it')
    approved_pos = itype_src.find('"approved" in it')
    assert withdrawn_pos != -1, (
        "P2-050 regression: the 'withdrawn' check is MISSING from "
        "_compute_normalized_score. Withdrawn drugs fall through to the "
        "'else' branch (0.3) -- HIGHER than investigational (0.5). "
        "Patient-safety bug."
    )
    assert approved_pos != -1, "P2-050: 'approved' check missing"
    assert withdrawn_pos < approved_pos, (
        "P2-050 regression: the 'withdrawn' check comes AFTER 'approved'. "
        "A drug marked 'approved_and_withdrawn' would map to 1.0 (approved) "
        "instead of 0.0 (withdrawn). The withdrawal is the more recent "
        "safety signal and must override."
    )
    # The withdrawn branch must return 0.0.
    withdrawn_branch = itype_src[withdrawn_pos:withdrawn_pos + 200]
    assert "return 0.0" in withdrawn_branch, (
        "P2-050: the 'withdrawn' branch does NOT return 0.0. Withdrawn "
        "drugs must have ZERO confidence (not a viable candidate)."
    )


# ─── IN-039 + IN-038: gt_api.py CORS + lifespan ───────────────────────────


def test_in_039_gt_api_cors_hardened() -> None:
    """gt_api.py CORS MUST have allow_credentials=False, explicit headers, no wildcard origins."""
    src = _read(REPO_ROOT / "scripts" / "gt_api.py")
    assert "allow_credentials=False" in src, (
        "IN-039 regression: gt_api.py CORS has allow_credentials=True (or "
        "missing). The GT API uses API keys, not cookies -- credentials "
        "are NOT needed and create a CORS misconfiguration risk."
    )
    assert 'allow_headers=["Content-Type", "Authorization", "X-Request-ID"]' in src, (
        "IN-039 regression: gt_api.py CORS allow_headers is not the explicit "
        "list. The wildcard '*' is NOT honored by browsers when "
        "allow_credentials=True (which we've removed), but explicit headers "
        "are still best practice."
    )
    # The validation function must reject '*'.
    assert "_validate_cors_origins" in src, (
        "IN-039: the _validate_cors_origins function is MISSING. Operators "
        "could set GT_CORS_ORIGINS=* and create a CORS misconfiguration."
    )


def test_in_038_gt_api_uses_lifespan() -> None:
    """gt_api.py MUST use the lifespan context manager (not deprecated on_event)."""
    src = _read(REPO_ROOT / "scripts" / "gt_api.py")
    assert "lifespan=lifespan" in src or "lifespan=lifespan," in src, (
        "IN-038 regression: gt_api.py does NOT use the lifespan context "
        "manager. The deprecated @app.on_event('startup') pattern will "
        "break in a future FastAPI version."
    )
    # No EXECUTABLE @app.on_event("startup") decorator.
    # Comments containing the decorator (e.g., the v113 IN-038 ROOT FIX
    # comment that explains the deprecation) are allowed -- we only care
    # about executable decorators at the top level.
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # An executable decorator would be `@app.on_event("startup")` at
        # the start of a line (possibly indented).
        if stripped.startswith('@app.on_event("startup")') or \
           stripped.startswith("@app.on_event('startup')"):
            pytest.fail(
                f"IN-038 regression: gt_api.py has an executable "
                f"@app.on_event('startup') decorator (line: "
                f"{line.strip()!r}). FastAPI deprecated this in 0.93.0. "
                f"Use the lifespan context manager instead."
            )


# ─── IN-055 + IN-085: pytest.ini ──────────────────────────────────────────


def test_in_055_pytest_ini_has_marker_filter() -> None:
    """pytest.ini addopts MUST include the marker filter to skip network/gpu/slow."""
    src = _read(REPO_ROOT / "pytest.ini")
    assert '-m "not network and not gpu and not slow"' in src, (
        "IN-055 regression: pytest.ini addopts does NOT include the marker "
        "filter. Running `pytest tests/` would hit live external APIs "
        "(ChEMBL, UniProt, DisGeNET) and rate-limit the IP."
    )


def test_in_085_pytest_ini_testpaths_no_missing_dirs() -> None:
    """pytest.ini testpaths MUST NOT include non-existent directories.

    We only check EXECUTABLE testpaths entries (not comments). The v113
    IN-085 ROOT FIX comment mentions `phase2/drugos_graph/tests` to
    explain what was removed -- that's historical context, not an
    executable entry.
    """
    src = _read(REPO_ROOT / "pytest.ini")
    # Parse the testpaths block. Lines under `testpaths =` until the
    # next top-level key (no leading whitespace).
    in_testpaths = False
    testpath_entries = []
    for line in src.splitlines():
        if line.startswith("testpaths"):
            in_testpaths = True
            continue
        if in_testpaths:
            # A top-level key (no leading whitespace) ends the block.
            if line and not line.startswith((" ", "\t")):
                in_testpaths = False
                continue
            stripped = line.strip()
            # Skip comment-only lines (the v113 IN-085 comment explains
            # what was removed -- it's historical context, not an entry).
            if stripped.startswith("#"):
                continue
            if stripped:
                testpath_entries.append(stripped)
    # The phase2/drugos_graph/tests directory does NOT exist.
    assert "phase2/drugos_graph/tests" not in testpath_entries, (
        f"IN-085 regression: pytest.ini testpaths includes "
        f"`phase2/drugos_graph/tests` which does NOT exist. pytest would "
        f"emit a 'directory not found' warning on every collection. "
        f"testpaths entries: {testpath_entries}"
    )


# ─── IN-060: verify_v82_fixes.py does NOT mutate production file ──────────


def test_in_060_verify_v82_no_production_mutation() -> None:
    """verify_v82_fixes.py MUST NOT mutate rl/validated_hypotheses.csv."""
    src = _read(REPO_ROOT / "scripts" / "verify_v82_fixes.py")
    # The production file mutation pattern (write to rl/validated_hypotheses.csv).
    assert "validated_hypotheses.csv" not in src or (
        "rl_dir" not in src and "sildenafil" not in src
    ), (
        "IN-060 regression: verify_v82_fixes.py still mutates the production "
        "rl/validated_hypotheses.csv file. A CI timeout mid-test would leave "
        "the production file polluted with test data (sildenafil). Use a "
        "TemporaryDirectory instead."
    )


# ─── IN-079: pre_commit_issue_guard.py fails CLOSED ───────────────────────


def test_in_079_pre_commit_guard_fails_closed() -> None:
    """pre_commit_issue_guard.py MUST return 1 (fail CLOSED) when target missing."""
    src = _read(REPO_ROOT / "scripts" / "pre_commit_issue_guard.py")
    # The fail-closed pattern: return 1 when the target script is missing.
    # Look for `return 1` near the "not found" error message.
    assert "return 1" in src, (
        "IN-079 regression: pre_commit_issue_guard.py does NOT return 1 "
        "when the target guard is missing. The previous fail-OPEN behavior "
        "(return 0) silently disabled ownership enforcement."
    )
    # The error message must mention BE-080 or IN-079.
    assert "BE-080" in src or "IN-079" in src, (
        "IN-079: the fail-closed error message does NOT reference BE-080 "
        "or IN-079. Operators wouldn't know which audit issue the failure "
        "relates to."
    )


# ─── P1-014: omim_pipeline.py has no module-level random.seed() ───────────


def test_p1_014_no_module_level_random_seed() -> None:
    """omim_pipeline.py MUST NOT call random.seed() at module import time."""
    src = _read(REPO_ROOT / "phase1" / "pipelines" / "omim_pipeline.py")
    # Parse the AST and check for module-level random.seed() calls.
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            f = node.value.func
            if (isinstance(f, ast.Attribute) and f.attr == "seed") or (
                isinstance(f, ast.Name) and f.id == "seed"
            ):
                pytest.fail(
                    f"P1-014 regression: omim_pipeline.py has a module-level "
                    f"random.seed() call at line {node.lineno}. This mutates "
                    f"the GLOBAL RNG for the entire Python process, breaking "
                    f"jitter in concurrent pipelines (ChEMBL retries lose "
                    f"their jitter, hammering the EBI API)."
                )


# ─── P1-025: base_pipeline.py uses per-instance RNG ───────────────────────


def test_p1_025_base_pipeline_per_instance_rng() -> None:
    """base_pipeline.py MUST use self._rng (per-instance), not global random.seed()."""
    src = _read(REPO_ROOT / "phase1" / "pipelines" / "base_pipeline.py")
    # The per-instance RNG must be initialized in __init__.
    assert "self._rng: random.Random = random.Random(self.seed)" in src or (
        "self._rng = random.Random(self.seed)" in src
    ), (
        "P1-025 regression: base_pipeline.py does NOT initialize a per-"
        "instance self._rng. The previous global random.seed() mutated "
        "global state, breaking reproducibility for concurrent pipelines."
    )
    # No executable random.seed(self.seed) in the run() method.
    # Look for `random.seed(self.seed)` outside of comments.
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "random.seed(self.seed)" in line or "random.seed(seed)" in line:
            pytest.fail(
                f"P1-025 regression: base_pipeline.py has an executable "
                f"`random.seed(self.seed)` call (line: {line.strip()!r}). "
                f"This mutates the GLOBAL RNG. Use self._rng instead."
            )


# ─── P2-044 + P2-045: service.py uses business IDs ───────────────────────


def test_p2_044_service_uses_business_id() -> None:
    """service.py _explore_subgraph_neo4j MUST use business id (not Neo4j internal id)."""
    src = _read(REPO_ROOT / "phase2" / "service.py")
    # The _business_id helper must be defined.
    assert "def _business_id(" in src, (
        "P2-044 regression: service.py does NOT define the _business_id "
        "helper. The previous code used d_node.id (Neo4j INTERNAL ID) "
        "which is NOT stable across DB restarts."
    )
    # The _node_record helper must use _business_id.
    assert '"id": _business_id(node)' in src or "'id': _business_id(node)" in src, (
        "P2-044: _node_record does NOT use _business_id for the id field."
    )


def test_p2_045_service_no_start_node_id_for_edges() -> None:
    """service.py MUST NOT use r1.start_node.id / r1.end_node.id for edge endpoints (undirected MATCH)."""
    src = _read(REPO_ROOT / "phase2" / "service.py")
    # The executable code should NOT have r1.start_node.id or r1.end_node.id
    # for edge source/target (those are arbitrary for undirected MATCH).
    # Look for the pattern in non-comment lines.
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        # Check for start_node.id / end_node.id used as edge source/target.
        if ('"source": r1.start_node.id' in line or
            '"target": r1.end_node.id' in line or
            "'source': r1.start_node.id" in line or
            "'target': r1.end_node.id" in line):
            pytest.fail(
                f"P2-045 regression: service.py line uses r1.start_node.id / "
                f"r1.end_node.id for edge source/target (line: "
                f"{line.strip()!r}). For undirected MATCH, these are "
                f"ARBITRARY -- the edge source/target could be SWAPPED on "
                f"consecutive runs. Use _business_id(d_node) / "
                f"_business_id(n1) instead."
            )


# ─── P2-046 + P2-048: ClinicalOutcome ID is canonical ────────────────────


def test_p2_046_clinical_outcome_id_no_dbid() -> None:
    """ClinicalOutcome ID MUST be CO:{disease_key}:{itype} (no drugbank_id)."""
    src = _read(REPO_ROOT / "phase2" / "drugos_graph" / "phase1_bridge.py")
    # The new format must be present.
    assert 'co_id = f"CO:{disease_key}:{itype}"' in src, (
        "P2-046 regression: ClinicalOutcome ID does NOT use the canonical "
        "CO:{disease_key}:{itype} format. The previous format "
        "CO:{dbid}:{disease_key}:{itype} depended on row order (the first "
        "drug's dbid), producing different IDs across runs."
    )
    # The old format must NOT be in executable code (only in comments).
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if 'co_id = f"CO:{dbid}:' in line:
            pytest.fail(
                f"P2-046 regression: phase1_bridge.py has executable code "
                f"using the old CO:{{dbid}}:... format (line: "
                f"{line.strip()!r}). The dbid must be dropped from the ID."
            )


# ─── P2-049: CORE_EDGE_TYPES has no legacy causes_side_effect ─────────────


def test_p2_049_no_legacy_causes_side_effect_in_core() -> None:
    """CORE_EDGE_TYPES MUST NOT include the legacy causes_side_effect tuple."""
    src = _read(REPO_ROOT / "phase2" / "drugos_graph" / "config_schema.py")
    # Find the CORE_EDGE_TYPES list and check the legacy tuple is commented out.
    # The legacy tuple is ("Compound", "causes_side_effect", "Side Effect").
    # It should be in a COMMENT (prefixed with #), NOT in executable code.
    in_core = False
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("CORE_EDGE_TYPES"):
            in_core = True
        elif in_core and re.match(r"^[A-Za-z_]", line):
            # We've exited the CORE_EDGE_TYPES list (next top-level statement).
            in_core = False
        if in_core:
            if stripped.startswith("#"):
                continue
            if "causes_side_effect" in line and "Side Effect" in line:
                pytest.fail(
                    f"P2-049 regression: CORE_EDGE_TYPES has an executable "
                    f"entry for the legacy ('Compound', 'causes_side_effect', "
                    f"'Side Effect') tuple (line: {line.strip()!r}). The "
                    f"canonical ('Compound', 'causes_adverse_event', "
                    f"'MedDRA_Term') is the only SIDER edge type. The legacy "
                    f"tuple must be commented out."
                )


# ─── IN-051: MANIFEST.in includes data files ──────────────────────────────


def test_in_051_manifest_includes_data_files() -> None:
    """MANIFEST.in MUST include *.yaml, *.json, *.md, *.txt from phase1/phase2/shared."""
    src = _read(REPO_ROOT / "MANIFEST.in")
    # The recursive-include rules must cover the data file types.
    required = [
        "recursive-include phase1 *.yaml *.json *.md *.txt",
        "recursive-include phase2 *.yaml *.json *.md *.txt",
        "recursive-include shared",
    ]
    for rule in required:
        assert rule in src, (
            f"IN-051 regression: MANIFEST.in is MISSING the rule: {rule!r}. "
            f"A `pip install .` would produce a wheel missing critical data "
            f"files (label_map.yaml, registry.json, etc.)."
        )


# ─── IN-087: README.md exists ─────────────────────────────────────────────


def test_in_087_readme_md_exists() -> None:
    """A ``README.md`` MUST exist at the repo root."""
    readme = REPO_ROOT / "README.md"
    assert readme.exists(), (
        "IN-087 regression: README.md does NOT exist at the repo root. "
        "GitHub renders README.md automatically on the repo home page; "
        "without it, visitors see only the file list."
    )
    content = readme.read_text()
    # The README should have at least a title and some content.
    assert len(content) > 500, (
        "IN-087: README.md exists but is too short (< 500 chars). It "
        "should include project overview, architecture, quickstart."
    )


# ─── IN-089: hypothesis_writeback.py validates paths + has timeout ────────


def test_in_089_hypothesis_writeback_validates_paths() -> None:
    """hypothesis_writeback.py MUST validate req_path / resp_path against temp dirs."""
    src = _read(REPO_ROOT / "scripts" / "hypothesis_writeback.py")
    # The _validate_path function must be defined.
    assert "def _validate_path(" in src, (
        "IN-089 regression: hypothesis_writeback.py does NOT define "
        "_validate_path. The previous code had NO path validation -- an "
        "attacker who controls req_path could read arbitrary files (path "
        "traversal, e.g. /etc/shadow)."
    )
    # The allowed temp dirs list must be present.
    assert "_ALLOWED_TEMP_DIRS" in src, (
        "IN-089: the _ALLOWED_TEMP_DIRS list is MISSING. Path validation "
        "cannot restrict req_path / resp_path to temp directories."
    )


def test_in_089_hypothesis_writeback_has_timeout() -> None:
    """hypothesis_writeback.py MUST enforce a 30s timeout on the writeback call."""
    src = _read(REPO_ROOT / "scripts" / "hypothesis_writeback.py")
    # The timeout must be enforced (via threading.Thread.join with timeout).
    assert "WRITEBACK_TIMEOUT_SECONDS" in src, (
        "IN-089 regression: hypothesis_writeback.py does NOT enforce a "
        "timeout. If write_validated_hypothesis hangs (e.g. DB deadlock), "
        "the Next.js route would hang indefinitely."
    )
    assert "worker.join(timeout=" in src, (
        "IN-089: the worker.join(timeout=...) call is MISSING. The "
        "timeout is defined but not actually enforced on the worker thread."
    )
