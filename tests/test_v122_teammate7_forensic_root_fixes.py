"""v122 Forensic Root Fix tests — Teammate 7 issues batch.

This test file verifies the v122 forensic root fixes for the issues
that were NOT actually fixed by prior "ROOT FIX" claims (the audit's
red-team mode found that comments claimed fixes were applied but the
actual code still had the bugs).

Issues verified by this test file:
  - IN-068: apache-airflow pinned to ==2.10.5 in BOTH requirements.txt
    files; Dockerfile.airflow base image matches; build-time assertion
    exists.
  - IN-080: requirements files have upper bounds, no duplicates, no
    non-existent versions; verify_requirements_security.py audit
    passes with 0 errors.
  - IN-048: phase1/Makefile setup target uses -f docker-compose.yml
    explicitly; root Makefile has setup + setup-dev targets.
  - IN-076: phase1/docker-compose.yml setup service uses pinned
    busybox:1.36.1, chmod 750, runs as non-root user (50000:0).
  - P3-046: trainer.train_epoch has a DataLoader path for large
    training sets (>= 8192 samples) with num_workers=4, pin_memory,
    persistent_workers.

Issues that were ALREADY fixed by prior teams (verified by reading
the actual code, not the comments):
  - P3-011: per-epoch verified AUC via evaluate_link_prediction.
  - P3-012: drug-disjoint check in fit().
  - P3-014: predict_all_pairs uses torch.set_grad_enabled(False).
  - P3-020: retrain_on_validated uses weights_only=True.
  - P3-023: predict_probability lock-free.
  - SH-013: load_validated_for_retraining writes canonical schema CSV.
  - IN-054: POSTGRES_PASSWORD uses ${VAR:?ERROR}.
  - IN-062: airflow-init entrypoint moved to shell script.
  - P3-013: shuffle documented as deliberate choice.
  - P3-016: per-class temperature (shape (2,)).
  - P3-022: NodeTypeEmbedding unknown slot small random init (std=0.02).
  - P3-027: retrain_on_validated uses original_edge_types + padding.
  - P3-028: vectorized numpy Mann-Whitney AUC fallback.
  - P3-034: specific exceptions + WARNING level + health flag.
  - P3-035: warning if lr > 0.1.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ============================================================================
# IN-068: apache-airflow pinned to ==2.10.5 in BOTH requirements.txt files.
# ============================================================================


def test_in_068_phase1_requirements_pins_airflow_exact():
    """IN-068: phase1/requirements.txt MUST pin apache-airflow==2.10.5.

    The previous pin `apache-airflow>=2.10.0,<3.0.0` still allowed pip
    to UPGRADE the base image's pre-installed Airflow 2.10.5 to a future
    2.10.x release, breaking pre-installed provider packages. The exact
    pin `==2.10.5` makes pip see the requirement is already satisfied.
    """
    req_path = _REPO_ROOT / "phase1" / "requirements.txt"
    content = req_path.read_text(encoding="utf-8")
    # Find the apache-airflow line (not in a comment).
    airflow_lines = [
        line for line in content.splitlines()
        if line.strip().startswith("apache-airflow") and not line.strip().startswith("#")
    ]
    assert len(airflow_lines) == 1, (
        f"Expected exactly 1 apache-airflow line in phase1/requirements.txt, "
        f"found {len(airflow_lines)}: {airflow_lines}"
    )
    line = airflow_lines[0].strip()
    assert line == "apache-airflow==2.10.5", (
        f"IN-068: phase1/requirements.txt must pin `apache-airflow==2.10.5` "
        f"(exact match to the base image). Found: `{line}`. The previous "
        f"`>=2.10.0,<3.0.0` pin allowed pip to upgrade the base image's "
        f"Airflow, breaking pre-installed provider packages."
    )


def test_in_068_root_requirements_pins_airflow_exact():
    """IN-068: root requirements.txt MUST pin apache-airflow==2.10.5."""
    req_path = _REPO_ROOT / "requirements.txt"
    content = req_path.read_text(encoding="utf-8")
    airflow_lines = [
        line for line in content.splitlines()
        if line.strip().startswith("apache-airflow") and not line.strip().startswith("#")
    ]
    assert len(airflow_lines) == 1, (
        f"Expected exactly 1 apache-airflow line in requirements.txt, "
        f"found {len(airflow_lines)}: {airflow_lines}"
    )
    line = airflow_lines[0].strip()
    assert line == "apache-airflow==2.10.5", (
        f"IN-068: requirements.txt must pin `apache-airflow==2.10.5` "
        f"(exact match to the base image). Found: `{line}`."
    )


def test_in_068_root_dockerfile_uses_2_10_5_base_image():
    """IN-068: root Dockerfile.airflow MUST use apache/airflow:2.10.5-python3.11.

    The previous version used `apache/airflow:3.3.0-python3.11` (major
    version 3!) while requirements.txt pinned `apache-airflow<3.0.0`.
    pip would either refuse to install or silently downgrade the base
    image's Airflow, breaking provider packages.
    """
    dockerfile_path = _REPO_ROOT / "Dockerfile.airflow"
    content = dockerfile_path.read_text(encoding="utf-8")
    # Find the FROM line.
    from_lines = [
        line.strip() for line in content.splitlines()
        if line.strip().startswith("FROM ")
    ]
    assert len(from_lines) >= 1, "Dockerfile.airflow must have a FROM line"
    from_line = from_lines[0]
    assert "apache/airflow:2.10.5-python3.11" in from_line, (
        f"IN-068: Dockerfile.airflow must use `apache/airflow:2.10.5-python3.11` "
        f"(not 3.3.0). Found: `{from_line}`. The previous 3.3.0 base image "
        f"is INCOMPATIBLE with the requirements.txt pin `<3.0.0`."
    )


def test_in_068_phase1_dockerfile_uses_2_10_5_base_image():
    """IN-068: phase1/docker/Dockerfile.airflow MUST use apache/airflow:2.10.5-python3.11."""
    dockerfile_path = _REPO_ROOT / "phase1" / "docker" / "Dockerfile.airflow"
    content = dockerfile_path.read_text(encoding="utf-8")
    from_lines = [
        line.strip() for line in content.splitlines()
        if line.strip().startswith("FROM ")
    ]
    assert len(from_lines) >= 1
    from_line = from_lines[0]
    assert "apache/airflow:2.10.5-python3.11" in from_line, (
        f"IN-068: phase1/docker/Dockerfile.airflow must use "
        f"`apache/airflow:2.10.5-python3.11`. Found: `{from_line}`."
    )


def test_in_068_root_dockerfile_has_assertion():
    """IN-068: root Dockerfile.airflow MUST have a build-time assertion
    that airflow.__version__ == '2.10.5'. This makes any future drift a
    BUILD failure (loud) instead of a runtime ImportError (silent).
    """
    dockerfile_path = _REPO_ROOT / "Dockerfile.airflow"
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "airflow.__version__ == '2.10.5'" in content, (
        "IN-068: Dockerfile.airflow must have a build-time assertion "
        "`RUN python -c \"import airflow; assert airflow.__version__ == "
        "'2.10.5'\"`. This catches future drift between the base image "
        "and requirements.txt at BUILD time, not runtime."
    )


def test_in_068_phase1_dockerfile_has_assertion():
    """IN-068: phase1/docker/Dockerfile.airflow MUST have the same assertion."""
    dockerfile_path = _REPO_ROOT / "phase1" / "docker" / "Dockerfile.airflow"
    content = dockerfile_path.read_text(encoding="utf-8")
    assert "airflow.__version__ == '2.10.5'" in content


# ============================================================================
# IN-080: requirements security audit passes with 0 errors.
# ============================================================================


def test_in_080_requirements_audit_passes():
    """IN-080: the verify_requirements_security.py audit must pass with 0 errors.

    This is the INTERIM forensic root fix for IN-080. The full fix
    (hash-based installs with pip-compile --generate-hashes) is tracked
    as a separate infrastructure task. This audit enforces:
      - Every dep has an upper bound (no unbounded `>=`).
      - No duplicate declarations of the same package.
      - No references to non-existent package versions.
      - apache-airflow pinned to ==2.10.5 (IN-068).
    """
    audit_script = _REPO_ROOT / "scripts" / "verify_requirements_security.py"
    assert audit_script.exists(), (
        "IN-080: scripts/verify_requirements_security.py must exist "
        "(the audit script that enforces requirements security)."
    )
    result = subprocess.run(
        [sys.executable, str(audit_script)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"IN-080: requirements security audit FAILED with exit code "
        f"{result.returncode}.\nSTDERR:\n{result.stderr}\n"
        f"STDOUT (last 2KB):\n{result.stdout[-2048:]}"
    )


def test_in_080_no_nonexistent_versions_in_any_requirements():
    """IN-080: no requirements file may reference a package version that
    does NOT exist on PyPI. The previous graph_transformer/requirements.txt
    had `scikit-learn>=1.9.0` (latest is 1.5.x), `torch>=2.13.0` (latest
    is 2.5.x), `scipy>=1.18.0` (latest is 1.14.x), `rdkit>=2026.3.4`
    (RDKit uses CalVer; 2026.x doesn't exist). pip install would fail
    with `No matching distribution found`.
    """
    # A curated list of versions that DO NOT EXIST on PyPI as of 2024-12.
    # If any of these appear in any requirements file, the test fails.
    nonexistent_versions = [
        "scikit-learn>=1.9.0",
        "scikit-learn>=1.8.0",
        "torch>=2.13.0",
        "torch>=2.10.0",  # 2.10 doesn't exist (latest 2.5.x)
        "scipy>=1.18.0",
        "scipy>=1.16.0",
        "rdkit>=2026",
        "rdkit>=2025",
        "fastapi>=0.139",
        "fastapi>=0.130",
        "certifi>=2026",
        "certifi>=2025",
        "pyyaml>=6.0.3",
        "prometheus-client>=0.25",
        "prometheus-client>=0.22",
        "filelock>=3.30",
        "filelock>=3.20",
        "python-dotenv>=1.2",
        "python-dotenv>=1.1",
        "numpy>=2.2.6",
        "sqlalchemy>=2.0.51",
    ]
    req_files = list(_REPO_ROOT.glob("**/requirements*.txt"))
    assert len(req_files) >= 5, "Expected at least 5 requirements files"
    violations = []
    for req_file in req_files:
        content = req_file.read_text(encoding="utf-8")
        for bad in nonexistent_versions:
            # Skip lines that start with # (comments).
            for lineno, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if bad in stripped:
                    violations.append(
                        f"{req_file.relative_to(_REPO_ROOT)}:{lineno}: "
                        f"found non-existent version pin `{bad}` in line: {stripped}"
                    )
    assert not violations, (
        "IN-080: found non-existent package version pins in requirements "
        f"files. These would cause `pip install` to FAIL with `No matching "
        f"distribution found`.\n" + "\n".join(violations)
    )


def test_in_080_no_duplicate_package_declarations():
    """IN-080: no requirements file may declare the same package twice
    with different version bounds. The previous phase2/drugos_graph/
    requirements.txt had `neo4j>=5.0,<7.0` AND `neo4j>=5.0,<6.0` —
    pip silently picked one, the other was dead weight.
    """
    req_files = list(_REPO_ROOT.glob("**/requirements*.txt"))
    violations = []
    for req_file in req_files:
        content = req_file.read_text(encoding="utf-8")
        seen = {}
        for lineno, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            if stripped.startswith("-"):
                continue
            # Extract package name (everything before the first version specifier).
            m = re.match(r"^([A-Za-z0-9_.\-]+)\s*[<>=!~]", stripped)
            if not m:
                continue
            name = m.group(1).lower()
            if name in seen:
                violations.append(
                    f"{req_file.relative_to(_REPO_ROOT)}:{lineno}: "
                    f"DUPLICATE declaration of `{name}`. First seen at "
                    f"line {seen[name]}. Consolidate into a single line."
                )
            else:
                seen[name] = lineno
    assert not violations, (
        "IN-080: found duplicate package declarations in requirements "
        f"files.\n" + "\n".join(violations)
    )


# ============================================================================
# IN-048: Makefile setup target uses -f docker-compose.yml explicitly.
# ============================================================================


def test_in_048_phase1_makefile_uses_explicit_compose_file():
    """IN-048: phase1/Makefile setup target MUST use `-f docker-compose.yml`
    so an operator running `make -f phase1/Makefile setup` from the repo
    root starts the CORRECT (dev) stack, not the root production stack.
    """
    makefile_path = _REPO_ROOT / "phase1" / "Makefile"
    content = makefile_path.read_text(encoding="utf-8")
    # Find the setup target body.
    setup_match = re.search(
        r"^setup:\s*\n((?:[ \t]+.*\n)+)",
        content,
        re.MULTILINE,
    )
    assert setup_match, "phase1/Makefile must have a `setup:` target"
    setup_body = setup_match.group(1)
    assert "-f docker-compose.yml" in setup_body, (
        "IN-048: phase1/Makefile setup target must use `-f docker-compose.yml` "
        "explicitly. The previous `docker-compose up -d` (no -f) looked for "
        f"docker-compose.yml in the CWD, starting the WRONG stack if invoked "
        f"from the repo root.\nSetup body:\n{setup_body}"
    )


def test_in_048_root_makefile_has_setup_and_setup_dev():
    """IN-048: root Makefile MUST have `setup` (production) and `setup-dev`
    (dev) targets so operators can explicitly choose which stack to start.
    """
    makefile_path = _REPO_ROOT / "Makefile"
    content = makefile_path.read_text(encoding="utf-8")
    assert re.search(r"^setup:\s*$", content, re.MULTILINE), (
        "IN-048: root Makefile must have a `setup:` target (production stack)."
    )
    assert re.search(r"^setup-dev:\s*$", content, re.MULTILINE), (
        "IN-048: root Makefile must have a `setup-dev:` target (dev stack)."
    )


# ============================================================================
# IN-076: phase1/docker-compose.yml setup service hardening.
# ============================================================================


def test_in_076_setup_service_uses_pinned_busybox():
    """IN-076: setup service MUST use `busybox:1.36.1` (pinned), not
    `busybox` (unpinned :latest). Unpinned images are non-reproducible
    and could change behavior when busybox is updated.
    """
    compose_path = _REPO_ROOT / "phase1" / "docker-compose.yml"
    content = compose_path.read_text(encoding="utf-8")
    # The setup service should use busybox:1.36.1 (with version tag).
    assert "busybox:1.36.1" in content, (
        "IN-076: phase1/docker-compose.yml setup service must use "
        "`busybox:1.36.1` (pinned version), not `busybox` (unpinned :latest)."
    )
    # Make sure NO line has bare `image: busybox` (without version).
    bare_busybox_lines = [
        line for line in content.splitlines()
        if re.search(r"image:\s*busybox\s*$", line.strip())
    ]
    assert not bare_busybox_lines, (
        "IN-076: found bare `image: busybox` (unpinned). Must be "
        f"`busybox:1.36.1`.\nLines: {bare_busybox_lines}"
    )


def test_in_076_setup_service_uses_chmod_750():
    """IN-076: setup service MUST use `chmod 750` (not 775). The previous
    `chmod 775` gave group-write to UID 0 (root), allowing any container
    running as root to modify the data dirs (data injection risk).
    """
    compose_path = _REPO_ROOT / "phase1" / "docker-compose.yml"
    content = compose_path.read_text(encoding="utf-8")
    # The setup service command should use chmod 750.
    assert "chmod 750" in content, (
        "IN-076: setup service command must use `chmod 750` (not 775). "
        "The previous `chmod 775` gave group-write to root, allowing "
        "data injection by any root container."
    )
    # Find the setup service's `command:` line and verify it uses chmod 750
    # (not 775). We look for `command: sh -c "..."` containing chmod.
    # Comments may mention chmod 775 (explaining the fix), but the actual
    # command line must NOT contain it.
    in_setup_service = False
    found_command_with_775 = False
    found_command_with_750 = False
    for line in content.splitlines():
        stripped = line.strip()
        # Track when we're inside the setup service block.
        if stripped == "setup:":
            in_setup_service = True
            continue
        # A new top-level service starts at the same indentation as `setup:`.
        if in_setup_service and re.match(r"^  [a-z][\w-]+:\s*$", line):
            in_setup_service = (stripped == "setup:")
            continue
        if not in_setup_service:
            continue
        # Check the command line.
        if stripped.startswith("command:"):
            if "chmod 775" in stripped:
                found_command_with_775 = True
            if "chmod 750" in stripped:
                found_command_with_750 = True
    assert found_command_with_750, (
        "IN-076: setup service `command:` line must use `chmod 750`."
    )
    assert not found_command_with_775, (
        "IN-076: setup service `command:` line must NOT use `chmod 775`. "
        "The previous `chmod 775` gave group-write to root."
    )


def test_in_076_setup_service_runs_as_non_root():
    """IN-076: setup service MUST run as non-root user (UID 50000 = airflow).
    The previous version ran as root (busybox default), giving any
    compromised setup script full root privileges.
    """
    compose_path = _REPO_ROOT / "phase1" / "docker-compose.yml"
    content = compose_path.read_text(encoding="utf-8")
    # The setup service should have `user: "50000:0"`.
    assert 'user: "50000:0"' in content or "user: '50000:0'" in content, (
        "IN-076: setup service must have `user: \"50000:0\"` to run as "
        "the airflow user (UID 50000), not as root."
    )


# ============================================================================
# P3-046: trainer.train_epoch has a DataLoader path for large training sets.
# ============================================================================


def test_p3_046_trainer_has_dataloader_path():
    """P3-046: GraphTransformerTrainer.train_epoch MUST have a DataLoader
    path that's used when the training set is large (>= 8192 samples).
    The DataLoader uses num_workers=4, pin_memory=True, persistent_workers=True
    so the GPU is not idle while the CPU prepares the next batch.
    """
    trainer_path = _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
    content = trainer_path.read_text(encoding="utf-8")
    # The DataLoader path must exist.
    assert "TensorDataset" in content, (
        "P3-046: trainer.py must use TensorDataset for the DataLoader path."
    )
    assert "DataLoader" in content, (
        "P3-046: trainer.py must use DataLoader for prefetching."
    )
    assert "RandomSampler" in content, (
        "P3-046: trainer.py must use RandomSampler (seeded with self._gen) "
        "to preserve reproducibility."
    )
    assert "num_workers=4" in content, (
        "P3-046: DataLoader must use num_workers=4 for prefetching."
    )
    assert "pin_memory=" in content, (
        "P3-046: DataLoader must use pin_memory=True for GPU training."
    )
    assert "persistent_workers=True" in content, (
        "P3-046: DataLoader must use persistent_workers=True to avoid "
        "respawning workers every epoch."
    )
    assert "MIN_SAMPLES_FOR_DATALOADER" in content, (
        "P3-046: trainer must have a MIN_SAMPLES_FOR_DATALOADER threshold "
        "so small training sets use the faster inline batching path."
    )


def test_p3_046_dataloader_preserves_reproducibility():
    """P3-046: the DataLoader path MUST use the trainer's dedicated
    `self._gen` generator (V4 C-F6 fix) so the shuffle order is
    deterministic and independent of any other torch ops that may have
    advanced the global RNG.
    """
    trainer_path = _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
    content = trainer_path.read_text(encoding="utf-8")
    # The RandomSampler must be seeded with self._gen.
    assert "RandomSampler(dataset, generator=self._gen)" in content, (
        "P3-046: RandomSampler must be seeded with self._gen (the trainer's "
        "dedicated generator) to preserve the V4 C-F6 reproducibility fix."
    )


def test_p3_046_inline_path_preserved_for_small_sets():
    """P3-046: the inline batching path MUST be preserved for small
    training sets (< 8192 samples). DataLoader's subprocess spawn
    overhead dominates for tiny datasets, making inline batching FASTER.
    The CI/demo path uses small sets and must not regress.
    """
    trainer_path = _REPO_ROOT / "graph_transformer" / "training" / "trainer.py"
    content = trainer_path.read_text(encoding="utf-8")
    # The inline path must still exist (torch.randperm + for loop).
    assert "torch.randperm" in content, (
        "P3-046: inline batching path (torch.randperm) must be preserved "
        "for small training sets."
    )
    assert "for start in range(0, n_samples, batch_size)" in content, (
        "P3-046: inline batching for-loop must be preserved for small sets."
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
