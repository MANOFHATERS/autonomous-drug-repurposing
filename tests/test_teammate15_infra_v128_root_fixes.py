#!/usr/bin/env python3
"""
v128 Teammate 15 — Infrastructure Forensic Root-Fix Verification Suite

RED TEAM MODE: every test reads ACTUAL CODE (not comments, not test mocks).
Each test asserts the v128 root fix is present in the executable code path,
not just in the comment block.

This module is safe to run in any environment (no torch / no Docker needed).
It only reads source files and parses YAML/TOML.

Run with:  python tests/test_teammate15_infra_v128_root_fixes.py
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path
from typing import Any, Dict, List

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
COMPOSE_GPU_PATH = REPO_ROOT / "docker-compose.gpu.yml"
MAKEFILE_PATH = REPO_ROOT / "Makefile"
BACKUP_SH_PATH = REPO_ROOT / "observability" / "backup.sh"
ROOT_REQ_PATH = REPO_ROOT / "requirements.txt"
GT_REQ_PATH = REPO_ROOT / "graph_transformer" / "requirements.txt"
PHASE2_REQ_PATH = REPO_ROOT / "phase2" / "drugos_graph" / "requirements.txt"
PYPROJECT_PATH = REPO_ROOT / "phase2" / "drugos_graph" / "pyproject.toml"
DOCKERFILE_ML_PATH = REPO_ROOT / "Dockerfile.ml"
DOCKERFILE_GPU_PATH = REPO_ROOT / "Dockerfile.gpu"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class Task151SecretsFailClosed(unittest.TestCase):
    """Task 15.1: replace hardcoded secrets with ${VAR:?ERROR} fail-closed pattern."""

    def test_no_bare_postgres_password_in_compose_urls(self) -> None:
        """The audit's silent-empty-password bug: ${POSTGRES_PASSWORD} without :?ERROR
        would silently embed an empty password into a DB URL if the env var
        was unset. Verify NO bare ${POSTGRES_PASSWORD} remains in code."""
        text = _read(COMPOSE_PATH)
        # Strip comments (lines starting with whitespace+#) before checking —
        # comments are allowed to mention ${POSTGRES_PASSWORD} for explanation.
        code_lines = [
            line for line in text.splitlines()
            if not line.strip().startswith("#")
        ]
        code = "\n".join(code_lines)
        # Match ${POSTGRES_PASSWORD} NOT followed by :? or :- (bare reference).
        bare_pattern = re.compile(r"\$\{POSTGRES_PASSWORD\}(?!\:)")
        matches = bare_pattern.findall(code)
        self.assertEqual(
            matches, [],
            f"Found {len(matches)} bare ${{POSTGRES_PASSWORD}} references in docker-compose.yml code "
            f"(should all use ${{POSTGRES_PASSWORD:?ERROR...}}). "
            f"This is the silent-empty-password security bug from Task 15.1."
        )

    def test_mlflow_uses_password_with_error_guard(self) -> None:
        text = _read(COMPOSE_PATH)
        # Find MLFLOW_BACKEND_STORE_URI line and verify it uses :?ERROR
        mlflow_lines = [l for l in text.splitlines() if "MLFLOW_BACKEND_STORE_URI" in l]
        self.assertEqual(len(mlflow_lines), 1, "expected exactly 1 MLFLOW_BACKEND_STORE_URI line")
        self.assertIn(
            "${POSTGRES_PASSWORD:?ERROR", mlflow_lines[0],
            "MLFLOW_BACKEND_STORE_URI must use ${POSTGRES_PASSWORD:?ERROR...} (Task 15.1)"
        )

    def test_airflow_sqlalchemy_uses_password_with_error_guard(self) -> None:
        text = _read(COMPOSE_PATH)
        af_lines = [l for l in text.splitlines() if "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN" in l]
        self.assertEqual(len(af_lines), 1)
        self.assertIn("${POSTGRES_PASSWORD:?ERROR", af_lines[0])

    def test_frontend_database_url_uses_password_with_error_guard(self) -> None:
        text = _read(COMPOSE_PATH)
        fe_lines = [l for l in text.splitlines() if l.strip().startswith("DATABASE_URL:")]
        self.assertEqual(len(fe_lines), 1)
        self.assertIn("${POSTGRES_PASSWORD:?ERROR", fe_lines[0])

    def test_secrets_block_exists(self) -> None:
        """The Docker Compose top-level secrets: block mounts file-based secrets
        so they are NOT visible in `docker inspect` or `env`."""
        data = yaml.safe_load(_read(COMPOSE_PATH))
        secrets = data.get("secrets", {})
        expected = {
            "postgres_password", "neo4j_password", "airflow_fernet_key",
            "airflow_webserver_secret_key", "mlflow_admin_password",
            "grafana_admin_password",
        }
        self.assertTrue(
            expected.issubset(secrets.keys()),
            f"secrets block missing: {expected - set(secrets.keys())}"
        )


class Task152Phase1ServiceCanonical(unittest.TestCase):
    """Task 15.2: phase1-service on port 8001 + uvicorn phase1.service:app."""

    def test_phase1_service_port_is_8001(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase1-service"]
        port_env = svc["environment"].get("PHASE1_SERVICE_PORT")
        self.assertEqual(port_env, "8001", f"PHASE1_SERVICE_PORT must be 8001 (was 8000), got {port_env}")

    def test_phase1_service_exposes_8001(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase1-service"]
        self.assertIn("8001", svc.get("expose", []), "phase1-service must expose 8001")

    def test_phase1_service_command_is_uvicorn_canonical(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase1-service"]
        cmd = svc.get("command", [])
        self.assertEqual(
            cmd, ["uvicorn", "phase1.service:app", "--host", "0.0.0.0", "--port", "8001"],
            f"phase1-service command must be canonical uvicorn phase1.service:app --port 8001, got {cmd}"
        )

    def test_phase1_service_healthcheck_uses_8001(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase1-service"]
        hc = svc.get("healthcheck", {})
        test_cmd = hc.get("test", [])
        # Join the list-form test into a string for substring check
        test_str = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
        self.assertIn("8001", test_str, f"healthcheck must curl port 8001, got: {test_str}")
        self.assertIn("/health", test_str, f"healthcheck must curl /health, got: {test_str}")

    def test_frontend_dataset_service_url_points_to_8001(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        fe = data["services"]["frontend"]
        url = fe["environment"].get("DATASET_SERVICE_URL")
        self.assertEqual(
            url, "http://phase1-service:8001",
            f"DATASET_SERVICE_URL must be http://phase1-service:8001 (was 8000), got {url}"
        )

    def test_phase1_service_has_healthcheck(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase1-service"]
        self.assertIn("healthcheck", svc, "phase1-service must have a healthcheck")

    def test_phase1_service_has_resource_limits(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase1-service"]
        deploy = svc.get("deploy", {})
        limits = deploy.get("resources", {}).get("limits", {})
        self.assertIn("memory", limits, "phase1-service must have deploy.resources.limits.memory")
        self.assertIn("cpus", limits, "phase1-service must have deploy.resources.limits.cpus")


class Task153GTServiceCanonical(unittest.TestCase):
    """Task 15.3: switch from scripts.gt_api:app to graph_transformer.service:app."""

    def test_phase3_gt_api_command_uses_graph_transformer_service(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase3-gt-api"]
        cmd = svc.get("command", [])
        self.assertEqual(
            cmd, ["uvicorn", "graph_transformer.service:app", "--host", "0.0.0.0", "--port", "8002"],
            f"phase3-gt-api command must be uvicorn graph_transformer.service:app, got {cmd}"
        )

    def test_phase3_gt_api_healthcheck_uses_health_not_healthz(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["phase3-gt-api"]
        hc = svc.get("healthcheck", {})
        test_cmd = hc.get("test", [])
        test_str = " ".join(test_cmd) if isinstance(test_cmd, list) else str(test_cmd)
        self.assertIn("/health", test_str)
        self.assertNotIn("/healthz", test_str,
                         "phase3-gt-api healthcheck must use /health (not /healthz) — "
                         "graph_transformer.service:app does not expose /healthz")

    def test_graph_transformer_service_py_declares_health_route(self) -> None:
        """Verify the actual route exists in graph_transformer/service.py source."""
        text = _read(REPO_ROOT / "graph_transformer" / "service.py")
        # Look for the @app.get("/health") decorator
        self.assertIn('@app.get("/health")', text,
                      "graph_transformer/service.py must declare @app.get(\"/health\")")

    def test_graph_transformer_service_py_does_NOT_declare_healthz(self) -> None:
        """Verify graph_transformer.service:app does NOT have a /healthz route
        (so the OLD /healthz healthcheck would always return 404)."""
        text = _read(REPO_ROOT / "graph_transformer" / "service.py")
        # Match @app.get("/healthz") — must NOT exist
        self.assertNotIn('@app.get("/healthz")', text,
                         "graph_transformer/service.py must NOT declare /healthz "
                         "(otherwise Task 15.3 healthcheck fix is not needed)")

    def test_dockerfile_ml_default_cmd_uses_graph_transformer_service(self) -> None:
        text = _read(DOCKERFILE_ML_PATH)
        # The default CMD line
        cmd_lines = [l for l in text.splitlines() if l.startswith("CMD [")]
        self.assertEqual(len(cmd_lines), 1, f"expected 1 CMD line in Dockerfile.ml, got {len(cmd_lines)}")
        self.assertIn("graph_transformer.service:app", cmd_lines[0])

    def test_dockerfile_gpu_default_cmd_uses_graph_transformer_service(self) -> None:
        text = _read(DOCKERFILE_GPU_PATH)
        cmd_lines = [l for l in text.splitlines() if l.startswith("CMD [")]
        self.assertEqual(len(cmd_lines), 1)
        self.assertIn("graph_transformer.service:app", cmd_lines[0])

    def test_dockerfile_ml_healthcheck_uses_health(self) -> None:
        text = _read(DOCKERFILE_ML_PATH)
        hc_lines = [l for l in text.splitlines() if "curl -fsS" in l and "/health" in l]
        self.assertEqual(len(hc_lines), 1, "expected 1 HEALTHCHECK CMD line in Dockerfile.ml")
        self.assertNotIn("/healthz", hc_lines[0])

    def test_dockerfile_gpu_healthcheck_uses_health(self) -> None:
        text = _read(DOCKERFILE_GPU_PATH)
        hc_lines = [l for l in text.splitlines() if "curl -fsS" in l and "/health" in l]
        self.assertEqual(len(hc_lines), 1)
        self.assertNotIn("/healthz", hc_lines[0])


class Task154GPUSupport(unittest.TestCase):
    """Task 15.4: GPU support for Phase 3 training (DOCX V1 criterion: AWS/GCP GPU)."""

    def test_gpu_compose_override_exists(self) -> None:
        self.assertTrue(COMPOSE_GPU_PATH.exists(), "docker-compose.gpu.yml must exist (Task 15.4)")

    def test_gpu_compose_has_device_reservations_for_trainer(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_GPU_PATH))
        svc = data["services"]["phase3-trainer"]
        devices = svc.get("deploy", {}).get("resources", {}).get("reservations", {}).get("devices", [])
        self.assertEqual(len(devices), 1, f"phase3-trainer GPU override must define 1 device, got {devices}")
        dev = devices[0]
        self.assertEqual(dev.get("driver"), "nvidia", f"device driver must be nvidia, got {dev}")
        self.assertEqual(dev.get("count"), 1)
        self.assertIn("gpu", dev.get("capabilities", []))

    def test_gpu_compose_uses_dockerfile_gpu(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_GPU_PATH))
        for svc_name in ["phase3-trainer", "phase3-gt-api", "phase4-rl"]:
            svc = data["services"][svc_name]
            self.assertEqual(
                svc.get("build", {}).get("dockerfile"), "Dockerfile.gpu",
                f"{svc_name} GPU override must use Dockerfile.gpu"
            )

    def test_dockerfile_gpu_uses_cuda_base(self) -> None:
        text = _read(DOCKERFILE_GPU_PATH)
        from_lines = [l for l in text.splitlines() if l.startswith("FROM ")]
        self.assertEqual(len(from_lines), 1)
        self.assertIn("nvidia/cuda:", from_lines[0],
                      f"Dockerfile.gpu must use nvidia/cuda base image, got {from_lines[0]}")

    def test_dockerfile_gpu_installs_cuda_torch(self) -> None:
        text = _read(DOCKERFILE_GPU_PATH)
        # Look for torch==X.Y.Z+cu121 (CUDA 12.1 wheel)
        m = re.search(r"torch==(\d+\.\d+\.\d+)\+cu121", text)
        self.assertIsNotNone(m, "Dockerfile.gpu must install torch==X.Y.Z+cu121 (CUDA build)")
        ver = m.group(1)
        self.assertTrue(ver.startswith("2."), f"torch CUDA build must be 2.x, got {ver}")

    def test_dockerfile_gpu_pytorch_wheel_index(self) -> None:
        """Verify the CUDA wheel index URL is set (so pip downloads the CUDA build, not CPU)."""
        text = _read(DOCKERFILE_GPU_PATH)
        self.assertIn("download.pytorch.org/whl/cu121", text,
                      "Dockerfile.gpu must use the cu121 wheel index URL")


class Task155ResourceLimitsAndNetworks(unittest.TestCase):
    """Task 15.5: resource limits + network segmentation (edge/app/data)."""

    def test_three_networks_exist(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        nets = data["networks"]
        for name in ["edge", "app", "data"]:
            self.assertIn(name, nets, f"network {name} must exist")

    def test_data_network_is_internal(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        self.assertTrue(
            data["networks"]["data"].get("internal"),
            "data network must be internal:true (no outbound internet)"
        )

    def test_every_service_has_resource_limits(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        services = data["services"]
        # Exclude node-exporter (uses host network, no resource limits needed)
        exempt = {"node-exporter"}
        for name, svc in services.items():
            if name in exempt:
                continue
            deploy = svc.get("deploy", {})
            limits = deploy.get("resources", {}).get("limits", {})
            self.assertIn("memory", limits, f"{name} must have deploy.resources.limits.memory")
            self.assertIn("cpus", limits, f"{name} must have deploy.resources.limits.cpus")

    def test_stateful_services_have_oom_protection(self) -> None:
        """postgres + neo4j should have oom_score_adj:-500 so the OOM killer
        targets training containers first."""
        data = yaml.safe_load(_read(COMPOSE_PATH))
        for name in ["postgres", "neo4j"]:
            svc = data["services"][name]
            self.assertEqual(svc.get("oom_score_adj"), -500,
                             f"{name} must have oom_score_adj:-500")

    def test_no_host_port_on_databases(self) -> None:
        """postgres + neo4j + mlflow should NOT have host port bindings (security)."""
        data = yaml.safe_load(_read(COMPOSE_PATH))
        for name in ["postgres", "neo4j", "mlflow"]:
            svc = data["services"][name]
            self.assertNotIn("ports", svc, f"{name} must not have host port bindings (use expose)")


class Task156BackupConfiguration(unittest.TestCase):
    """Task 15.6: backup configuration for persistent volumes (Postgres, Neo4j, MLflow)."""

    def test_pg_backup_service_exists(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        self.assertIn("pg-backup", data["services"], "pg-backup sidecar service must exist")

    def test_pg_backup_mounts_mlflow_volume(self) -> None:
        """v128 TM15 Task 15.6 ROOT FIX: pg-backup must mount mlflow_data read-only
        so the backup script can tar.gz the MLflow artifact store (/mlruns)."""
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["pg-backup"]
        volumes = svc.get("volumes", [])
        found = any("mlflow_data" in str(v) and "/mlruns" in str(v) for v in volumes)
        self.assertTrue(found, f"pg-backup must mount mlflow_data:/mlruns:ro, got volumes: {volumes}")

    def test_backup_script_has_mlflow_section(self) -> None:
        """The backup.sh script must include a section that tars /mlruns."""
        text = _read(BACKUP_SH_PATH)
        self.assertIn("MLflow backup", text, "backup.sh must have an MLflow backup section")
        self.assertIn("mlflow-${TIMESTAMP}.tar.gz", text,
                      "backup.sh must produce mlflow-YYYYMMDD-HHMMSS.tar.gz archive")
        self.assertIn("MLFLOW_ARTIFACT_DIR", text,
                      "backup.sh must reference MLFLOW_ARTIFACT_DIR for the source path")

    def test_backup_script_handles_missing_mlflow_dir_gracefully(self) -> None:
        """If the mlflow volume is not mounted (e.g., dev mode), backup.sh should
        log a warning and continue (not crash)."""
        text = _read(BACKUP_SH_PATH)
        self.assertIn("MLflow backup SKIPPED", text,
                      "backup.sh must log 'MLflow backup SKIPPED' when mlflow volume is not mounted")

    def test_backup_script_retention_covers_mlflow(self) -> None:
        """The retention cleanup must also delete old MLflow backups."""
        text = _read(BACKUP_SH_PATH)
        self.assertIn("mlflow-*.tar.gz", text,
                      "backup.sh retention must clean up mlflow-*.tar.gz archives")

    def test_backup_script_summary_includes_mlflow_count(self) -> None:
        text = _read(BACKUP_SH_PATH)
        self.assertIn("MLFLOW_COUNT", text,
                      "backup.sh summary must include MLflow backup count")

    def test_backup_script_syntax_valid(self) -> None:
        """The backup script must be syntactically valid bash (no broken syntax)."""
        import subprocess
        result = subprocess.run(
            ["bash", "-n", str(BACKUP_SH_PATH)],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"bash -n backup.sh failed:\n{result.stderr}")


class Task157TorchVersionAlignment(unittest.TestCase):
    """Task 15.7: pin single torch version across all phases (resolve conflicts)."""

    def test_root_requirements_torch_pin(self) -> None:
        text = _read(ROOT_REQ_PATH)
        # Strip comments before checking — comments may reference old buggy pins
        # (e.g., "the previous torch>=2.13.0 was a future version") as examples.
        code_lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        # Match torch>=X.Y.Z,<X.(Y+1).0 (single minor series, exclusive upper)
        m = re.search(r"^torch>=(\d+\.\d+\.\d+),<(\d+\.\d+)\.0", code, re.MULTILINE)
        self.assertIsNotNone(m, f"root requirements.txt must pin torch with bounded range, got: {code[:500]}")
        lower, upper = m.group(1), m.group(2)
        # Single minor series: lower's minor (e.g., 2.2) == upper-0.1 OR
        # equivalently upper = lower_minor + 0.1 (e.g., lower=2.2.0, upper=2.3).
        lower_minor = ".".join(lower.split(".")[:2])  # "2.2"
        expected_upper = "{}.{}".format(lower.split(".")[0], int(lower.split(".")[1]) + 1)
        self.assertEqual(upper, expected_upper,
                         f"torch upper bound must be {expected_upper}.0 (single minor series {lower_minor}.x), "
                         f"got lower={lower}, upper={upper}")

    def test_graph_transformer_requirements_torch_pin_matches_root(self) -> None:
        # Strip comments before checking — comments reference old buggy pins.
        def _strip_comments(text: str) -> str:
            return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        root_text = _strip_comments(_read(ROOT_REQ_PATH))
        gt_text = _strip_comments(_read(GT_REQ_PATH))
        root_lower = re.search(r"torch>=(\d+\.\d+\.\d+)", root_text).group(1)
        gt_lower = re.search(r"torch>=(\d+\.\d+\.\d+)", gt_text).group(1)
        self.assertEqual(root_lower, gt_lower,
                         f"torch lower bound must match: root={root_lower}, gt={gt_lower}")

    def test_phase2_requirements_torch_pin_matches_root(self) -> None:
        def _strip_comments(text: str) -> str:
            return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        root_lower = re.search(r"torch>=(\d+\.\d+\.\d+)", _strip_comments(_read(ROOT_REQ_PATH))).group(1)
        p2_lower = re.search(r"torch>=(\d+\.\d+\.\d+)", _strip_comments(_read(PHASE2_REQ_PATH))).group(1)
        self.assertEqual(root_lower, p2_lower,
                         f"torch lower bound must match: root={root_lower}, phase2={p2_lower}")

    def test_pyproject_torch_pin_matches_root(self) -> None:
        root_lower = re.search(r"torch>=(\d+\.\d+\.\d+)", _read(ROOT_REQ_PATH)).group(1)
        pyproj_lower = re.search(r'"torch>=(\d+\.\d+\.\d+)', _read(PYPROJECT_PATH)).group(1)
        self.assertEqual(root_lower, pyproj_lower,
                         f"torch lower bound in pyproject.toml must match root: root={root_lower}, pyproj={pyproj_lower}")

    def test_pyproject_no_duplicate_packages(self) -> None:
        """The audit found 4 duplicate package declarations in pyproject.toml
        (neo4j, pandas, transformers, mlflow) with conflicting upper bounds.
        Verify NONE of these appear more than once in dependencies."""
        text = _read(PYPROJECT_PATH)
        # Find the dependencies = [ ... ] block
        m = re.search(r"dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
        self.assertIsNotNone(m, "pyproject.toml must have a dependencies = [...] block")
        deps_block = m.group(1)
        # For each package, count occurrences (must be exactly 1)
        for pkg in ["neo4j", "pandas", "transformers", "mlflow", "torch", "torch-geometric"]:
            # Match "pkg>=..." or "pkg==..." (case-insensitive)
            pattern = re.compile(rf'"{pkg}[><=!]', re.IGNORECASE)
            matches = pattern.findall(deps_block)
            self.assertEqual(
                len(matches), 1,
                f"pyproject.toml dependencies: '{pkg}' must appear EXACTLY ONCE (found {len(matches)}). "
                f"Duplicate declarations with different upper bounds cause pip to silently pick one "
                f"(the LAST wins in pip's resolver) — this is the 'comments are fakes' bug the user "
                f"warned about. v122 comment claimed duplicates were removed but they were STILL THERE."
            )

    def test_pyproject_numpy_bound_aligned_with_root(self) -> None:
        """The audit found pyproject.toml had numpy<3.0 while root had numpy<2.0.
        Airflow 2.10.5 + pandas 2.1.4 require numpy<2.0 — numpy 2.0 breaks both."""
        pyproj_text = _read(PYPROJECT_PATH)
        m = re.search(r'"numpy>=(\d+\.\d+),<(\d+\.\d+)"', pyproj_text)
        self.assertIsNotNone(m, "pyproject.toml must have a numpy pin")
        upper = m.group(2)
        self.assertEqual(upper, "2.0",
                         f"pyproject.toml numpy upper bound must be <2.0 (was <3.0 — "
                         f"Airflow 2.10.5 + pandas 2.1.4 require numpy<2.0), got {upper}")

    def test_root_requirements_torch_geometric_pin(self) -> None:
        text = _read(ROOT_REQ_PATH)
        # Strip comments before checking
        code_lines = [l for l in text.splitlines() if not l.lstrip().startswith("#")]
        code = "\n".join(code_lines)
        m = re.search(r"^torch-geometric>=(\d+\.\d+)\.0,<(\d+\.\d+)\.0", code, re.MULTILINE)
        self.assertIsNotNone(m, "root requirements.txt must pin torch-geometric with bounded range")
        lower_minor, upper_minor = m.group(1), m.group(2)
        # Single minor series: e.g., lower=2.5, upper=2.6 (exclusive upper = same minor 2.5.x)
        lower_major, lower_minor_num = lower_minor.split(".")
        expected_upper = "{}.{}".format(lower_major, int(lower_minor_num) + 1)
        self.assertEqual(upper_minor, expected_upper,
                         f"torch-geometric upper bound must be {expected_upper}.0 (single minor series "
                         f"{lower_minor}.x), got lower={lower_minor}, upper={upper_minor}")

    def test_dependency_matrix_consistent(self) -> None:
        """The user's Task 15.7 verification: 'python -c "import torch, torch_geometric;
        print(torch.__version__, torch_geometric.__version__)"'. Verify the pin
        bounds are compatible (torch 2.2.x ↔ torch-geometric 2.5.x)."""
        root_text = _read(ROOT_REQ_PATH)
        torch_pin = re.search(r"torch>=(\d+\.\d+)\.", root_text).group(1)
        pyg_pin = re.search(r"torch-geometric>=(\d+\.\d+)\.", root_text).group(1)
        # torch 2.2.x is compatible with torch-geometric 2.5.x (per https://data.pyg.org/whl/torch-2.2.0+cpu.html)
        self.assertEqual(torch_pin, "2.2",
                         f"canonical torch minor must be 2.2 (Dockerfile.ml pins torch==2.2.0+cpu), got {torch_pin}")
        self.assertEqual(pyg_pin, "2.5",
                         f"canonical torch-geometric minor must be 2.5 (Dockerfile.ml pins torch-geometric==2.5.3), got {pyg_pin}")


class Task158MakefileDefaults(unittest.TestCase):
    """Task 15.8: bump Makefile defaults from 5 epochs to 80 + add production target."""

    def test_makefile_uses_tabs_not_spaces(self) -> None:
        """CRITICAL: v128 ROOT FIX — the previous Makefile used 8 SPACES instead
        of TABs for recipe prefixes, causing `make help` to fail with
        'missing separator (did you mean TAB instead of 8 spaces?)'. Verify
        the recipe lines now use TAB."""
        text = _read(MAKEFILE_PATH)
        # Find recipe lines (start with whitespace, then a recipe char like @ or $( or cd)
        bad_lines = []
        for i, line in enumerate(text.splitlines(), 1):
            # Recipe lines start with leading whitespace then a non-whitespace char
            if line.startswith("    ") and not line.startswith("\t"):
                # 4+ leading spaces — check if this is a recipe (not a comment continuation)
                stripped = line.lstrip()
                if stripped and not stripped.startswith("#"):
                    bad_lines.append((i, line))
        self.assertEqual(bad_lines, [],
                         f"Makefile still has space-prefixed recipe lines (should be TAB): {bad_lines[:5]}")

    def test_make_help_works(self) -> None:
        """The simplest smoke test: `make help` must exit 0."""
        import subprocess
        result = subprocess.run(
            ["make", "help"],
            cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"make help failed (exit {result.returncode}):\n{result.stderr}")
        self.assertIn("Unified Autonomous Drug Repurposing", result.stdout)

    def test_run_4phase_passes_gt_epochs_80(self) -> None:
        """Task 15.8 verification: `make -n run-4phase | grep -E '(gt-epochs|rl-timesteps)'`
        must show --gt-epochs 80 --rl-timesteps 5000 in the output."""
        import subprocess
        result = subprocess.run(
            ["make", "-n", "run-4phase"],
            cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"make -n run-4phase failed: {result.stderr}")
        # Must contain both flags
        self.assertIn("--gt-epochs", result.stdout, "make run-4phase must pass --gt-epochs")
        self.assertIn("--rl-timesteps", result.stdout, "make run-4phase must pass --rl-timesteps")
        # Must contain the canonical defaults (80 / 5000)
        self.assertIn("80", result.stdout, "make run-4phase must default to 80 epochs")
        self.assertIn("5000", result.stdout, "make run-4phase must default to 5000 timesteps")

    def test_run_4phase_prod_target_exists(self) -> None:
        """Task 15.8: production target with 500 epochs (DOCX §6 AUC>0.85 criterion)."""
        import subprocess
        result = subprocess.run(
            ["make", "-n", "run-4phase-prod"],
            cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"make -n run-4phase-prod failed: {result.stderr}")
        self.assertIn("--gt-epochs", result.stdout)
        self.assertIn("500", result.stdout, "make run-4phase-prod must use 500 epochs")
        self.assertIn("50000", result.stdout, "make run-4phase-prod must use 50000 timesteps")

    def test_run_4phase_smoke_target_exists(self) -> None:
        """Task 15.8: smoke target with 5 epochs for CI (NOT for production)."""
        import subprocess
        result = subprocess.run(
            ["make", "-n", "run-4phase-smoke"],
            cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, f"make -n run-4phase-smoke failed: {result.stderr}")
        self.assertIn("--gt-epochs 5", result.stdout)
        self.assertIn("--rl-timesteps 100", result.stdout)

    def test_run_4phase_prod_in_help(self) -> None:
        import subprocess
        result = subprocess.run(
            ["make", "help"], cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertIn("run-4phase-prod", result.stdout)
        self.assertIn("run-4phase-smoke", result.stdout)

    def test_makefile_dotphony_includes_new_targets(self) -> None:
        text = _read(MAKEFILE_PATH)
        self.assertIn("run-4phase-prod", text)
        self.assertIn("run-4phase-smoke", text)


class ComposeStructuralIntegrity(unittest.TestCase):
    """Sanity checks on docker-compose.yml overall structure."""

    def test_compose_parses_as_valid_yaml(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        self.assertIsInstance(data, dict)
        self.assertIn("services", data)
        self.assertIn("networks", data)
        self.assertIn("volumes", data)

    def test_expected_services_present(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        expected = {
            "postgres", "neo4j", "mlflow", "phase1-airflow", "phase1-service",
            "phase2-kg-builder", "phase2-kg-api", "phase3-trainer",
            "phase3-gt-api", "phase4-rl", "frontend", "pg-backup",
        }
        actual = set(data["services"].keys())
        missing = expected - actual
        self.assertEqual(missing, set(), f"missing services: {missing}")

    def test_mlflow_service_mounts_backups_secret(self) -> None:
        """Verify the MLflow service itself mounts postgres_password secret
        (so it can connect to Postgres for backend store)."""
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["mlflow"]
        secrets = svc.get("secrets", [])
        self.assertIn("postgres_password", secrets)
        self.assertIn("mlflow_admin_password", secrets)

    def test_pg_backup_has_depends_on_postgres_healthy(self) -> None:
        data = yaml.safe_load(_read(COMPOSE_PATH))
        svc = data["services"]["pg-backup"]
        deps = svc.get("depends_on", {})
        self.assertIn("postgres", deps)
        self.assertEqual(deps["postgres"].get("condition"), "service_healthy")
        self.assertEqual(deps["neo4j"].get("condition"), "service_healthy")


def run_all() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        Task151SecretsFailClosed,
        Task152Phase1ServiceCanonical,
        Task153GTServiceCanonical,
        Task154GPUSupport,
        Task155ResourceLimitsAndNetworks,
        Task156BackupConfiguration,
        Task157TorchVersionAlignment,
        Task158MakefileDefaults,
        ComposeStructuralIntegrity,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(run_all())
