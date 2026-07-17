"""
tests/test_teammate15_infra_v116.py — Root-level verification of all 38
Teammate 15 infrastructure fixes (v116).

This test suite reads the ACTUAL infra files (docker-compose.yml, Dockerfiles,
Makefile, requirements.txt, .env.example, .gitignore) and asserts that every
issue from the audit is fixed at the ROOT level — not just commented as fixed.

Run with:
    python -m pytest tests/test_teammate15_infra_v116.py -v
"""

from __future__ import annotations

import os
import re
import textwrap
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose() -> dict:
    """Parse docker-compose.yml WITHOUT env interpolation (raw ${VAR} kept)."""
    text = _read(REPO_ROOT / "docker-compose.yml")
    return yaml.safe_load(text)


def _compose_interpolated() -> dict:
    """Parse docker-compose.yml WITH env interpolation for testing.

    Sets dummy values for every ${VAR:?ERROR} secret so the file parses.
    """
    text = _read(REPO_ROOT / "docker-compose.yml")
    env = {
        "POSTGRES_PASSWORD": "test_pw",
        "NEO4J_PASSWORD": "test_pw",
        "AIRFLOW_FERNET_KEY": "ZmDfcTF7_60GrrY167zsiPd67pEvs0aGOv2oasOM1Pg=",
        "AIRFLOW_WEBSERVER_SECRET_KEY": "test-secret-key-32-chars-long-xxx",
        "MLFLOW_ADMIN_PASSWORD": "test_pw",
        "GRAFANA_ADMIN_PASSWORD": "test_pw",
        "POSTGRES_USER": "drugos",
        "POSTGRES_DB": "drugos",
    }
    os.environ.update(env)
    # docker-compose uses ${VAR:-default} and ${VAR:?error}. Simulate by
    # replacing ${VAR:?...} and ${VAR:-...} and ${VAR} with values.
    # ${VAR:?error message} -> value of VAR (or empty if unset, but we set all)
    text = re.sub(r"\$\{(\w+):-\}", lambda m: os.environ.get(m.group(1), ""), text)
    text = re.sub(
        r"\$\{(\w+):-[^}]*\}",
        lambda m: os.environ.get(m.group(1), ""),
        text,
    )
    text = re.sub(
        r"\$\{(\w+):\?[^}]*\}",
        lambda m: os.environ.get(m.group(1), ""),
        text,
    )
    text = re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), text)
    return yaml.safe_load(text)


# ─── CRITICAL: secrets no longer hardcoded (IN-001 / BE-004 / P1-001) ────────

HARDCODED_SECRETS = [
    "drugos_dev_password",
    "dev_fernet_key_replace_in_production",
]


class TestSecretsNotHardcoded:
    """IN-001 / BE-004 / P1-001 — every hardcoded secret replaced with ${VAR}."""

    def test_compose_has_no_hardcoded_passwords(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        for secret in HARDCODED_SECRETS:
            assert secret not in text, (
                f"Hardcoded secret '{secret}' still present in docker-compose.yml "
                f"(IN-001/BE-004/P1-001 not fixed)"
            )

    def test_compose_uses_env_var_for_postgres_password(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        assert "POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:" in text, (
            "POSTGRES_PASSWORD must use ${POSTGRES_PASSWORD:?ERROR} pattern"
        )

    def test_compose_uses_env_var_for_neo4j_password(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        assert "NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:" in text, (
            "NEO4J_AUTH must use ${NEO4J_PASSWORD:?ERROR} pattern"
        )

    def test_env_example_exists_and_documents_secrets(self):
        env_example = REPO_ROOT / ".env.example"
        assert env_example.exists(), ".env.example must exist (IN-001 v116)"
        content = env_example.read_text()
        for var in [
            "POSTGRES_PASSWORD",
            "NEO4J_PASSWORD",
            "AIRFLOW_FERNET_KEY",
            "AIRFLOW_WEBSERVER_SECRET_KEY",
            "MLFLOW_ADMIN_PASSWORD",
            "GRAFANA_ADMIN_PASSWORD",
        ]:
            assert var in content, f".env.example must document {var}"

    def test_gitignore_ignores_env(self):
        gi = _read(REPO_ROOT / ".gitignore")
        assert ".env" in gi and "!.env.example" in gi, (
            ".gitignore must ignore .env but NOT .env.example"
        )

    def test_env_example_not_committed_with_real_secrets(self):
        content = _read(REPO_ROOT / ".env.example")
        assert "CHANGE_ME" in content or "GENERATE_WITH" in content, (
            ".env.example must contain placeholder values, not real secrets"
        )
        # Secrets must NOT appear as VALUES (VAR=value lines), but MAY appear
        # in comments explaining what the old broken value was.
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            var, _, val = stripped.partition("=")
            for secret in HARDCODED_SECRETS:
                assert secret not in val, (
                    f".env.example must not set {var} to the hardcoded secret '{secret}'"
                )


# ─── CRITICAL: Fernet key valid (IN-049) ─────────────────────────────────────


class TestFernetKey:
    """IN-049 — Airflow Fernet key is a valid Fernet key, not a placeholder."""

    def test_compose_sources_fernet_from_env(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        assert "AIRFLOW__CORE__FERNET_KEY: ${AIRFLOW_FERNET_KEY:" in text, (
            "Fernet key must be sourced from ${AIRFLOW_FERNET_KEY:?ERROR}"
        )
        assert "dev_fernet_key_replace_in_production" not in text

    def test_env_example_documents_fernet_generation(self):
        content = _read(REPO_ROOT / ".env.example")
        assert "Fernet.generate_key" in content, (
            ".env.example must show how to generate a valid Fernet key"
        )

    def test_entrypoint_validates_fernet(self):
        ep = _read(REPO_ROOT / "Dockerfile.airflow.entrypoint.sh")
        assert "from cryptography.fernet import Fernet" in ep
        assert "Fernet(key" in ep


# ─── CRITICAL: training epochs (IN-007) ──────────────────────────────────────


class TestTrainingEpochs:
    """IN-007 — --gt-epochs defaults to 80, --rl-timesteps to 5000."""

    def test_compose_uses_canonical_defaults(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        assert "${GT_EPOCHS:-80}" in text, (
            "compose must use --gt-epochs ${GT_EPOCHS:-80} (canonical default)"
        )
        assert "${RL_TIMESTEPS:-5000}" in text, (
            "compose must use --rl-timesteps ${RL_TIMESTEPS:-5000} (canonical default)"
        )
        assert "--gt-epochs 5" not in text, "5-epoch training must be removed"
        assert "--rl-timesteps 100" not in text, "100-timestep RL must be removed"


# ─── CRITICAL + HIGH: DATASET_SERVICE_URL (BE-005 / IN-008) ──────────────────


class TestDatasetServiceUrl:
    """BE-005 / IN-008 — frontend DATASET_SERVICE_URL points to the real service."""

    def test_frontend_dataset_url_points_to_phase1_service(self):
        compose = _compose_interpolated()
        frontend_env = compose["services"]["frontend"]["environment"]
        assert frontend_env["DATASET_SERVICE_URL"] == "http://phase1-service:8000", (
            "DATASET_SERVICE_URL must point to phase1-service:8000 (not phase1-airflow)"
        )


# ─── HIGH: network segmentation (IN-002) ─────────────────────────────────────


class TestNetworkSegmentation:
    """IN-002 — three networks: edge / app / data. data is internal."""

    def test_three_networks_defined(self):
        compose = _compose()
        nets = compose.get("networks", {})
        assert set(["edge", "app", "data"]).issubset(nets.keys()), (
            "Must define edge, app, data networks"
        )

    def test_data_network_is_internal(self):
        compose = _compose()
        assert compose["networks"]["data"].get("internal") is True, (
            "data network must be internal:true (no outbound internet)"
        )

    def test_frontend_on_edge(self):
        compose = _compose_interpolated()
        frontend_nets = compose["services"]["frontend"].get("networks", [])
        assert "edge" in frontend_nets, "frontend must be on edge network"

    def test_postgres_not_on_edge(self):
        compose = _compose_interpolated()
        pg_nets = compose["services"]["postgres"].get("networks", [])
        assert "edge" not in pg_nets, "postgres must NOT be on edge network"

    def test_phase_services_on_app(self):
        compose = _compose_interpolated()
        for svc in ["phase1-service", "phase2-kg-api", "phase3-gt-api", "phase4-rl"]:
            nets = compose["services"][svc].get("networks", [])
            assert "app" in nets, f"{svc} must be on app network"


# ─── HIGH: no exposed admin ports (IN-003) ───────────────────────────────────


class TestNoExposedPorts:
    """IN-003 — only frontend:3000 (and grafana:3001) are host-exposed."""

    def test_postgres_has_no_host_ports(self):
        compose = _compose_interpolated()
        assert "ports" not in compose["services"]["postgres"], (
            "postgres must NOT expose host ports (IN-003)"
        )

    def test_neo4j_has_no_host_ports(self):
        compose = _compose_interpolated()
        assert "ports" not in compose["services"]["neo4j"], (
            "neo4j must NOT expose host ports (IN-003)"
        )

    def test_mlflow_has_no_host_ports(self):
        compose = _compose_interpolated()
        assert "ports" not in compose["services"]["mlflow"], (
            "mlflow must NOT expose host ports (IN-003)"
        )

    def test_airflow_has_no_host_ports(self):
        compose = _compose_interpolated()
        assert "ports" not in compose["services"]["phase1-airflow"], (
            "phase1-airflow must NOT expose host ports (IN-003, IN-010)"
        )

    def test_phase_services_use_expose_not_ports(self):
        compose = _compose_interpolated()
        for svc in ["phase1-service", "phase2-kg-api", "phase3-gt-api", "phase4-rl"]:
            svc_def = compose["services"][svc]
            assert "ports" not in svc_def, f"{svc} must NOT use ports (use expose)"
            assert "expose" in svc_def, f"{svc} must use expose (internal only)"

    def test_frontend_exposes_3000(self):
        compose = _compose_interpolated()
        ports = compose["services"]["frontend"]["ports"]
        assert any("3000" in str(p) for p in ports), "frontend must expose 3000"


# ─── HIGH: resource limits (IN-004) ──────────────────────────────────────────


class TestResourceLimits:
    """IN-004 — every service has deploy.resources.limits."""

    def test_every_service_has_memory_limit(self):
        compose = _compose_interpolated()
        for name, svc in compose["services"].items():
            if name in ("node-exporter",):  # host network, no limits
                continue
            limits = svc.get("deploy", {}).get("resources", {}).get("limits", {})
            assert "memory" in limits, f"{name} must have deploy.resources.limits.memory"

    def test_postgres_has_oom_protection(self):
        compose = _compose_interpolated()
        assert compose["services"]["postgres"].get("oom_score_adj") == -500

    def test_neo4j_has_oom_protection(self):
        compose = _compose_interpolated()
        assert compose["services"]["neo4j"].get("oom_score_adj") == -500


# ─── HIGH: backups (IN-005) ──────────────────────────────────────────────────


class TestBackups:
    """IN-005 — pg-backup sidecar + backup script exist."""

    def test_pg_backup_service_exists(self):
        compose = _compose_interpolated()
        assert "pg-backup" in compose["services"], "pg-backup sidecar must exist"

    def test_backup_script_exists(self):
        script = REPO_ROOT / "observability" / "backup.sh"
        assert script.exists(), "observability/backup.sh must exist"
        assert os.access(script, os.X_OK), "backup.sh must be executable"

    def test_backups_volume_defined(self):
        compose = _compose()
        assert "backups" in compose.get("volumes", {})


# ─── HIGH: GPU support (IN-006) ──────────────────────────────────────────────


class TestGpuSupport:
    """IN-006 — Dockerfile.gpu + docker-compose.gpu.yml override exist."""

    def test_dockerfile_gpu_exists(self):
        assert (REPO_ROOT / "Dockerfile.gpu").exists()

    def test_gpu_compose_override_exists(self):
        assert (REPO_ROOT / "docker-compose.gpu.yml").exists()

    def test_gpu_dockerfile_uses_cuda_torch(self):
        text = _read(REPO_ROOT / "Dockerfile.gpu")
        assert "torch==2.2.0+cu121" in text, "GPU Dockerfile must install CUDA torch"

    def test_gpu_compose_has_device_reservation(self):
        text = _read(REPO_ROOT / "docker-compose.gpu.yml")
        assert "capabilities: [gpu]" in text
        assert "driver: nvidia" in text


# ─── HIGH: Airflow auth (IN-010) ─────────────────────────────────────────────


class TestAirflowAuth:
    """IN-010 — Airflow API deny_all + webserver secret + no host port."""

    def test_airflow_api_deny_all(self):
        compose = _compose_interpolated()
        env = compose["services"]["phase1-airflow"]["environment"]
        assert "airflow.api.auth.backend.deny_all" in env.get(
            "AIRFLOW__API__AUTH_BACKENDS", ""
        )

    def test_airflow_webserver_secret_required(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        assert "AIRFLOW__WEBSERVER__SECRET_KEY: ${AIRFLOW_WEBSERVER_SECRET_KEY:" in text


# ─── HIGH: metrics stack (IN-040) ────────────────────────────────────────────


class TestMetricsStack:
    """IN-040 — prometheus + grafana + node-exporter + cadvisor."""

    def test_prometheus_service_exists(self):
        compose = _compose_interpolated()
        assert "prometheus" in compose["services"]

    def test_grafana_service_exists(self):
        compose = _compose_interpolated()
        assert "grafana" in compose["services"]

    def test_prometheus_config_exists(self):
        assert (REPO_ROOT / "observability" / "prometheus.yml").exists()

    def test_grafana_provisioning_exists(self):
        assert (
            REPO_ROOT
            / "observability"
            / "grafana"
            / "provisioning"
            / "datasources"
            / "datasources.yml"
        ).exists()


# ─── HIGH: split trainer from API (IN-063) ───────────────────────────────────


class TestSplitTrainerFromApi:
    """IN-063 — phase3-trainer (one-shot) split from phase3-gt-api (long-running)."""

    def test_phase3_trainer_exists_and_is_one_shot(self):
        compose = _compose_interpolated()
        trainer = compose["services"]["phase3-trainer"]
        assert trainer.get("restart") == "no", "phase3-trainer must be restart: no"

    def test_phase3_gt_api_exists_and_is_long_running(self):
        compose = _compose_interpolated()
        api = compose["services"]["phase3-gt-api"]
        assert api.get("restart") == "unless-stopped"

    def test_gt_api_depends_on_trainer_completion(self):
        compose = _compose_interpolated()
        deps = compose["services"]["phase3-gt-api"]["depends_on"]
        assert deps.get("phase3-trainer") == {"condition": "service_completed_successfully"}

    def test_no_chained_train_and_serve(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        # The old pattern chained `python run_4phase.py ... && uvicorn ...` in
        # ONE service. The fix splits them. Verify no service has both
        # run_4phase AND uvicorn in the same command.
        for svc_match in re.finditer(
            r"phase3-trainer:\n(.*?)(?=\n  [a-z]|\n  #|\Z)",
            text,
            re.DOTALL,
        ):
            cmd = svc_match.group(1)
            if "command:" in cmd:
                # trainer command may have run_4phase + touch, but NOT uvicorn
                assert "uvicorn" not in cmd.split("command:")[1], (
                    "phase3-trainer must NOT chain uvicorn (IN-063)"
                )


# ─── HIGH: torch-scatter/sparse in requirements (IN-069) ─────────────────────


class TestTorchScatterSparse:
    """IN-069 — requirements.txt pins torch-scatter + torch-sparse."""

    def test_torch_scatter_in_requirements(self):
        text = _read(REPO_ROOT / "requirements.txt")
        assert "torch-scatter" in text, "requirements.txt must list torch-scatter"

    def test_torch_sparse_in_requirements(self):
        text = _read(REPO_ROOT / "requirements.txt")
        assert "torch-sparse" in text, "requirements.txt must list torch-sparse"


# ─── HIGH: consistent import paths (P1-030) ──────────────────────────────────


class TestConsistentImportPaths:
    """P1-030 — phase2-kg-builder uses consistent qualified import path."""

    def test_no_bare_drugos_graph_in_compose_command(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        # The old pattern: `from drugos_graph.phase1_bridge import ...` (bare)
        # mixed with `uvicorn phase2.drugos_graph.kg_api:app` (qualified).
        # The fix uses qualified for both.
        assert "from drugos_graph.phase1_bridge" not in text, (
            "phase2-kg-builder must use qualified 'phase2.drugos_graph.phase1_bridge'"
        )
        assert "from phase2.drugos_graph.phase1_bridge" in text


# ─── MEDIUM: Dockerfile hygiene (IN-011 / IN-012 / IN-061) ───────────────────


class TestDockerfileHygiene:
    """IN-011 / IN-012 / IN-061 — Dockerfiles pin pip, set ENV, EXPOSE, HEALTHCHECK."""

    def test_airflow_dockerfile_pins_pip(self):
        text = _read(REPO_ROOT / "Dockerfile.airflow")
        assert "pip==24.2" in text

    def test_airflow_dockerfile_has_healthcheck(self):
        text = _read(REPO_ROOT / "Dockerfile.airflow")
        assert "HEALTHCHECK" in text

    def test_airflow_dockerfile_no_duplicate_copy(self):
        text = _read(REPO_ROOT / "Dockerfile.airflow")
        # IN-050: the COPY phase1/ /opt/phase1/ line must be removed.
        assert "COPY --chown=airflow:airflow phase1/ /opt/phase1/" not in text

    def test_ml_dockerfile_uid_10001(self):
        text = _read(REPO_ROOT / "Dockerfile.ml")
        assert "useradd -m -u 10001 drugos" in text
        assert "useradd -m -u 1000 drugos" not in text

    def test_ml_dockerfile_has_expose(self):
        text = _read(REPO_ROOT / "Dockerfile.ml")
        assert "EXPOSE 8002 8003" in text

    def test_ml_dockerfile_has_healthcheck(self):
        text = _read(REPO_ROOT / "Dockerfile.ml")
        assert "HEALTHCHECK" in text

    def test_ml_dockerfile_has_python_env_vars(self):
        text = _read(REPO_ROOT / "Dockerfile.ml")
        assert "PYTHONDONTWRITEBYTECODE=1" in text
        assert "PYTHONUNBUFFERED=1" in text


# ─── MEDIUM: Dockerfile.python-ml no pytest (IN-014) + not dead (IN-013) ─────


class TestPythonMlDockerfile:
    """IN-013 / IN-014 — no pytest in base image; phase2 Dockerfile self-contained."""

    def test_no_pytest_in_python_ml(self):
        text = _read(REPO_ROOT / "Dockerfile.python-ml")
        # pytest must NOT appear in any RUN pip install line (comments OK).
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("RUN pip install"):
                assert "pytest" not in stripped, (
                    f"Dockerfile.python-ml RUN line must not install pytest: {stripped}"
                )

    def test_python_ml_aligned_to_torch_220(self):
        text = _read(REPO_ROOT / "Dockerfile.python-ml")
        assert "torch==2.2.0+cpu" in text, "Dockerfile.python-ml must use torch 2.2.0 (aligned)"

    def test_phase2_dockerfile_is_self_contained(self):
        text = _read(REPO_ROOT / "phase2" / "drugos_graph" / "Dockerfile")
        # The FROM line must be python:3.11-slim (self-contained), NOT
        # drugos-python-ml:2.0.0 (which nothing builds). Comments may mention
        # the old dependency for context.
        from_lines = [l.strip() for l in text.splitlines() if l.strip().startswith("FROM")]
        assert any("python:3.11-slim" in l for l in from_lines), (
            "phase2 Dockerfile FROM must be python:3.11-slim"
        )
        for l in from_lines:
            assert "drugos-python-ml" not in l, (
                f"phase2 Dockerfile FROM must NOT reference drugos-python-ml: {l}"
            )


# ─── MEDIUM: upper bounds on requirements (IN-018) ───────────────────────────


class TestRequirementsUpperBounds:
    """IN-018 — every dependency has an upper bound."""

    def test_pandas_has_upper_bound(self):
        text = _read(REPO_ROOT / "requirements.txt")
        assert re.search(r"^pandas>=2\.1\.4,<", text, re.MULTILINE)

    def test_numpy_has_upper_bound(self):
        text = _read(REPO_ROOT / "requirements.txt")
        assert re.search(r"^numpy>=1\.26\.3,<", text, re.MULTILINE)

    def test_torch_has_upper_bound(self):
        text = _read(REPO_ROOT / "requirements.txt")
        assert re.search(r"^torch>=2\.0,<", text, re.MULTILINE)

    def test_airflow_has_upper_bound(self):
        text = _read(REPO_ROOT / "requirements.txt")
        assert re.search(r"apache-airflow>=2\.8\.0,<3\.0\.0", text)


# ─── MEDIUM: structured logging (IN-041) ─────────────────────────────────────


class TestStructuredLogging:
    """IN-041 — every service has json-file logging with max-size."""

    def test_every_service_has_logging_config(self):
        compose = _compose_interpolated()
        for name, svc in compose["services"].items():
            if name in ("node-exporter",):
                continue
            logging = svc.get("logging", {})
            assert logging.get("driver") == "json-file", (
                f"{name} must have logging.driver: json-file"
            )
            assert "max-size" in logging.get("options", {}), (
                f"{name} must have logging.options.max-size"
            )


# ─── MEDIUM: distributed tracing (IN-042) ────────────────────────────────────


class TestDistributedTracing:
    """IN-042 — otel-collector + jaeger services exist."""

    def test_otel_collector_exists(self):
        compose = _compose_interpolated()
        assert "otel-collector" in compose["services"]

    def test_jaeger_exists(self):
        compose = _compose_interpolated()
        assert "jaeger" in compose["services"]

    def test_otel_config_exists(self):
        assert (REPO_ROOT / "observability" / "otel-collector.yml").exists()


# ─── MEDIUM: airflow-logs volume (IN-064) ────────────────────────────────────


class TestAirflowLogsVolume:
    """IN-064 — airflow-logs volume mounted at /opt/airflow/logs."""

    def test_airflow_logs_volume_defined(self):
        compose = _compose()
        assert "airflow-logs" in compose.get("volumes", {})

    def test_airflow_service_mounts_logs(self):
        compose = _compose_interpolated()
        vols = compose["services"]["phase1-airflow"].get("volumes", [])
        assert any("airflow-logs:/opt/airflow/logs" in v for v in vols), (
            "phase1-airflow must mount airflow-logs at /opt/airflow/logs"
        )


# ─── MEDIUM: Makefile test targets (P1-008 / SH-037) ─────────────────────────


class TestMakefileTestTargets:
    """P1-008 / SH-037 — institutional test not skipped; shared+root covered."""

    def test_test_phase1_does_not_skip_institutional(self):
        text = _read(REPO_ROOT / "Makefile")
        # The --ignore flag must NOT appear in any RECIPE line (tab-indented).
        # Comments may mention the old ignore for context.
        for line in text.splitlines():
            if line.startswith("\t") and "--ignore=" in line:
                assert "test_disgenet_pipeline_institutional" not in line, (
                    f"Makefile recipe must not skip institutional test: {line}"
                )

    def test_test_all_includes_shared_and_root(self):
        text = _read(REPO_ROOT / "Makefile")
        assert "test-shared" in text, "Makefile must have test-shared target (SH-037)"
        assert "test-root" in text, "Makefile must have test-root target (SH-037)"


# ─── LOW: deprecated version key (IN-028) + name key (IN-065) ────────────────


class TestComposeVersionAndName:
    """IN-028 / IN-065 — no deprecated version key; name key present."""

    def test_no_version_key(self):
        text = _read(REPO_ROOT / "docker-compose.yml")
        assert not re.search(r'^version:\s*["\']?3\.', text, re.MULTILINE), (
            "docker-compose.yml must NOT have deprecated version: key (IN-028)"
        )

    def test_name_key_present(self):
        compose = _compose()
        assert compose.get("name") == "drugos-platform", (
            "docker-compose.yml must have name: drugos-platform (IN-065)"
        )

    def test_no_container_name_directives(self):
        compose = _compose_interpolated()
        for name, svc in compose["services"].items():
            assert "container_name" not in svc, (
                f"{name} must NOT have container_name (IN-065 — let compose generate)"
            )


# ─── LOW: Makefile run-json + test order (IN-046 / IN-047 / P1-009) ──────────


class TestMakefileRunJsonAndOrder:
    """IN-046 / IN-047 / P1-009 — run-json sorts by mtime; test-all in data order."""

    def test_run_json_sorts_by_mtime(self):
        text = _read(REPO_ROOT / "Makefile")
        assert "getmtime" in text, "run-json must sort by mtime (IN-046)"

    def test_test_all_order_is_data_direction(self):
        text = _read(REPO_ROOT / "Makefile")
        # Must be test-phase1 test-phase2 test-bridge (data direction)
        match = re.search(r"test-all:\s*(test-\S+\s+)+", text)
        assert match, "test-all target must exist"
        order = match.group(0)
        assert order.index("test-phase1") < order.index("test-phase2") < order.index("test-bridge"), (
            "test-all must run phase1 → phase2 → bridge (IN-047/P1-009)"
        )


# ─── LOW: no duplicate biopython (IN-017 / P1-002 / SH-028) ──────────────────


class TestNoDuplicateBiopython:
    """IN-017 / P1-002 / SH-028 — biopython declared exactly once."""

    def test_biopython_declared_once(self):
        text = _read(REPO_ROOT / "requirements.txt")
        count = len(re.findall(r"^biopython>=", text, re.MULTILINE))
        assert count == 1, f"biopython must be declared exactly once (found {count})"


# ─── LOW: no pytest in production requirements (IN-016) ─────────────────────


class TestNoPytestInProdReqs:
    """IN-016 — pytest is NOT in requirements.txt (only requirements-dev.txt)."""

    def test_pytest_not_in_requirements_txt(self):
        text = _read(REPO_ROOT / "requirements.txt")
        # Allow it in comments, but not as an actual install line.
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or not stripped:
                continue
            assert not stripped.startswith("pytest"), (
                "requirements.txt must NOT list pytest as a dependency (IN-016)"
            )

    def test_pytest_in_requirements_dev(self):
        text = _read(REPO_ROOT / "requirements-dev.txt")
        assert "pytest" in text, "pytest must be in requirements-dev.txt"


# ─── YAML validity (catches syntax errors in all YAML files) ─────────────────


class TestYamlValidity:
    """All YAML files must parse without errors."""

    @pytest.mark.parametrize(
        "yaml_file",
        [
            "docker-compose.yml",
            "docker-compose.gpu.yml",
            "observability/prometheus.yml",
            "observability/otel-collector.yml",
            "observability/grafana/provisioning/datasources/datasources.yml",
            "observability/grafana/provisioning/dashboards/dashboards.yml",
        ],
    )
    def test_yaml_parses(self, yaml_file):
        path = REPO_ROOT / yaml_file
        with open(path) as f:
            yaml.safe_load(f)  # raises on syntax error


# ─── Shell script validity (backup.sh + entrypoint) ──────────────────────────


class TestShellScripts:
    """Shell scripts must be syntactically valid."""

    def test_backup_sh_syntax(self):
        import subprocess
        result = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "observability" / "backup.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"backup.sh syntax error: {result.stderr}"

    def test_entrypoint_syntax(self):
        import subprocess
        result = subprocess.run(
            ["bash", "-n", str(REPO_ROOT / "Dockerfile.airflow.entrypoint.sh")],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"entrypoint syntax error: {result.stderr}"


# ─── Makefile validity (tabs, not spaces) ────────────────────────────────────


class TestMakefileValidity:
    """Makefile must use tabs (not spaces) for recipe lines."""

    def test_makefile_uses_tabs(self):
        import subprocess
        result = subprocess.run(
            ["make", "-n", "-f", str(REPO_ROOT / "Makefile"), "help"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Makefile failed to parse (missing separator = spaces instead of tabs): "
            f"{result.stderr}"
        )
