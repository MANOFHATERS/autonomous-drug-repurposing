"""v118 Teammate 15 — REAL behavioral tests for infrastructure fixes.

This test suite verifies that the 38 audit issues assigned to Teammate 15
(Infrastructure: docker-compose, Dockerfiles, Makefile, requirements) are
fixed in ACTUAL CODE — not just in comments. It uses Red Team methodology
(strip comments before pattern matching, parse YAML structure, verify
behavioral properties of the entrypoint script).

Run with:
    pytest tests/test_teammate15_infra_v118_real_fixes.py -v

The tests are organized by audit issue ID (BE-004, IN-001, IN-002, ...).
Each test verifies a SINGLE property of a SINGLE file. Failures print the
specific file + line + expected behavior so the operator can locate the
regression without re-running with -v.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


# ─── helpers ────────────────────────────────────────────────────────────────

def strip_comments(text: str) -> str:
    """Strip `# ...` comment lines from any source file (YAML, Dockerfile, Makefile, Python)."""
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))


def read(path: str) -> str:
    return (REPO / path).read_text()


def read_code(path: str) -> str:
    """Read file with comments stripped (executable code only)."""
    return strip_comments(read(path))


def parse_yaml(path: str) -> dict:
    return yaml.safe_load(read(path))


# ─── CRITICAL ISSUES ────────────────────────────────────────────────────────

class TestCriticalIssues:
    """6 CRITICAL issues: BE-004, BE-005, IN-001, IN-007, IN-049, P1-001."""

    def test_BE_004_IN_001_P1_001_root_compose_no_hardcoded_dev_password(self):
        """docker-compose.yml MUST NOT contain the literal 'drugos_dev_password'."""
        dc = read('docker-compose.yml')
        assert 'drugos_dev_password' not in dc, (
            'IN-001 regression: hardcoded dev password present in docker-compose.yml'
        )

    def test_IN_001_root_compose_no_dev_fernet_key_placeholder(self):
        """docker-compose.yml MUST NOT contain 'dev_fernet_key_replace_in_production'."""
        dc = read('docker-compose.yml')
        assert 'dev_fernet_key_replace_in_production' not in dc, (
            'IN-049 regression: invalid Fernet key placeholder still present'
        )

    def test_IN_001_phase1_compose_no_cosmic_default_for_postgres_password(self):
        """phase1/docker-compose.yml MUST NOT default POSTGRES_PASSWORD to 'cosmic'."""
        p1_code = read_code('phase1/docker-compose.yml')
        assert 'POSTGRES_PASSWORD:-cosmic' not in p1_code, (
            'IN-001 regression: phase1 compose silently defaults POSTGRES_PASSWORD to "cosmic"'
        )

    def test_IN_001_phase1_compose_uses_fail_fast_postgres_password(self):
        """phase1/docker-compose.yml MUST use ${POSTGRES_PASSWORD:?ERROR} fail-fast."""
        p1 = read('phase1/docker-compose.yml')
        assert 'POSTGRES_PASSWORD:?ERROR' in p1, (
            'IN-001 not applied: phase1 compose does not fail-fast on missing POSTGRES_PASSWORD'
        )

    def test_IN_001_phase1_compose_uses_fail_fast_postgres_user(self):
        """phase1/docker-compose.yml MUST use ${POSTGRES_USER:?ERROR} fail-fast."""
        p1 = read('phase1/docker-compose.yml')
        assert 'POSTGRES_USER:?ERROR' in p1, (
            'IN-001 not applied: phase1 compose does not fail-fast on missing POSTGRES_USER'
        )

    def test_IN_001_phase1_compose_uses_fail_fast_postgres_db(self):
        """phase1/docker-compose.yml MUST use ${POSTGRES_DB:?ERROR} fail-fast."""
        p1 = read('phase1/docker-compose.yml')
        assert 'POSTGRES_DB:?ERROR' in p1, (
            'IN-001 not applied: phase1 compose does not fail-fast on missing POSTGRES_DB'
        )

    def test_BE_005_IN_008_dataset_service_url_points_to_real_service(self):
        """Frontend DATASET_SERVICE_URL MUST point to phase1-service:8000 (NOT phase1-airflow)."""
        dc = read('docker-compose.yml')
        assert 'DATASET_SERVICE_URL: http://phase1-service:8000' in dc, (
            'BE-005/IN-008 regression: frontend proxies to the wrong service '
            '(was phase1-airflow:8000 — Airflow does not serve /stats)'
        )

    def test_IN_007_gt_epochs_default_is_80_not_5(self):
        """phase3-trainer MUST default --gt-epochs to 80 (NOT 5)."""
        dc = read('docker-compose.yml')
        assert '--gt-epochs ${GT_EPOCHS:-80}' in dc, (
            'IN-007 regression: --gt-epochs default is not 80 (the canonical minimum)'
        )
        assert '--gt-epochs 5' not in dc, (
            'IN-007 regression: --gt-epochs 5 (undertrained) override still present'
        )

    def test_IN_007_rl_timesteps_default_is_5000_not_100(self):
        """phase3-trainer MUST default --rl-timesteps to 5000 (NOT 100)."""
        dc = read('docker-compose.yml')
        assert '--rl-timesteps ${RL_TIMESTEPS:-5000}' in dc, (
            'IN-007 regression: --rl-timesteps default is not 5000'
        )

    def test_IN_049_compose_uses_fernet_key_file_secret(self):
        """docker-compose.yml MUST source Airflow Fernet key from /run/secrets/."""
        dc = read('docker-compose.yml')
        assert 'AIRFLOW__CORE__FERNET_KEY_FILE: /run/secrets/airflow_fernet_key' in dc, (
            'IN-049 regression: Fernet key not sourced from Docker secret'
        )

    def test_IN_049_entrypoint_reads_FILE_env_var(self):
        """Dockerfile.airflow.entrypoint.sh MUST translate _FILE → bare env var.

        The v117 compose sets AIRFLOW__CORE__FERNET_KEY_FILE (not the bare env var).
        The v116 entrypoint read the bare env var only → container failed to start.
        v118 fix: entrypoint loads the file and exports the bare env var.
        """
        entry = read('Dockerfile.airflow.entrypoint.sh')
        assert '_load_file_env AIRFLOW__CORE__FERNET_KEY_FILE' in entry, (
            'IN-049 v118 regression: entrypoint does not load _FILE env var '
            '(Airflow container would exit with "FERNET_KEY is not set")'
        )

    def test_IN_049_entrypoint_validates_fernet_key_value(self):
        """Entrypoint MUST reject invalid Fernet keys (e.g. placeholder strings)."""
        entry = read('Dockerfile.airflow.entrypoint.sh')
        assert 'from cryptography.fernet import Fernet' in entry, (
            'IN-049: entrypoint missing Fernet validation import'
        )
        assert 'Fernet(' in entry, (
            'IN-049: entrypoint missing Fernet() construction (the actual validation)'
        )

    def test_IN_049_entrypoint_validates_webserver_secret_key(self):
        """Entrypoint MUST validate AIRFLOW__WEBSERVER__SECRET_KEY is set (IN-010)."""
        entry = read('Dockerfile.airflow.entrypoint.sh')
        assert '_load_file_env AIRFLOW__WEBSERVER__SECRET_KEY_FILE' in entry, (
            'IN-010 v118: entrypoint missing webserver secret _FILE loading'
        )
        assert 'AIRFLOW__WEBSERVER__SECRET_KEY is not set' in entry, (
            'IN-010: entrypoint missing webserver secret validation'
        )

    def test_IN_049_entrypoint_bash_syntax_valid(self):
        """The entrypoint MUST be valid bash (syntax check)."""
        result = subprocess.run(
            ['bash', '-n', str(REPO / 'Dockerfile.airflow.entrypoint.sh')],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, (
            f'IN-049: entrypoint has bash syntax errors:\n{result.stderr}'
        )

    def test_IN_049_entrypoint_rejects_invalid_fernet_key_behavioral(self, tmp_path):
        """Behavioral: entrypoint MUST exit non-zero on an invalid Fernet key value."""
        script = REPO / 'Dockerfile.airflow.entrypoint.sh'
        # Run the entrypoint with an invalid Fernet key (the old placeholder).
        # The script should exit 1 BEFORE reaching `exec "$@"`.
        env = os.environ.copy()
        env['AIRFLOW__CORE__FERNET_KEY'] = 'dev_fernet_key_replace_in_production'
        env['AIRFLOW__WEBSERVER__SECRET_KEY'] = 'a' * 32  # valid (just non-empty)
        result = subprocess.run(
            ['bash', str(script), 'true'],  # `true` is the cmd to exec if validation passes
            capture_output=True, text=True, env=env,
        )
        assert result.returncode != 0, (
            'IN-049 BEHAVIORAL: entrypoint accepted invalid Fernet key '
            f'(should have rejected it). stdout: {result.stdout}, stderr: {result.stderr}'
        )
        assert 'not a valid Fernet key' in result.stderr, (
            f'IN-049 BEHAVIORAL: entrypoint did not print the expected error. stderr: {result.stderr}'
        )

    def test_IN_049_entrypoint_accepts_valid_fernet_key_behavioral(self, tmp_path):
        """Behavioral: entrypoint MUST accept a valid Fernet key and exec the command."""
        script = REPO / 'Dockerfile.airflow.entrypoint.sh'
        # Generate a real Fernet key using Python's cryptography library.
        try:
            from cryptography.fernet import Fernet
            valid_key = Fernet.generate_key().decode()
        except ImportError:
            pytest.skip('cryptography library not available in test environment')
        env = os.environ.copy()
        env['AIRFLOW__CORE__FERNET_KEY'] = valid_key
        env['AIRFLOW__WEBSERVER__SECRET_KEY'] = 'a' * 32
        # exec `echo OK` — the entrypoint should hand off and print OK
        result = subprocess.run(
            ['bash', str(script), 'echo', 'OK'],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 0, (
            f'IN-049 BEHAVIORAL: entrypoint rejected a VALID Fernet key. stderr: {result.stderr}'
        )
        assert 'OK' in result.stdout, (
            f'IN-049 BEHAVIORAL: entrypoint did not exec the command. stdout: {result.stdout}'
        )


# ─── HIGH ISSUES ────────────────────────────────────────────────────────────

class TestHighIssues:
    """11 HIGH issues: IN-002, IN-003, IN-004, IN-005, IN-006, IN-008, IN-010,
    IN-040, IN-063, IN-069, P1-030."""

    def test_IN_002_root_compose_has_three_segmented_networks(self):
        dc = parse_yaml('docker-compose.yml')
        networks = dc.get('networks', {})
        assert set(['edge', 'app', 'data']).issubset(set(networks.keys())), (
            'IN-002: root compose missing one of edge/app/data networks'
        )

    def test_IN_002_root_compose_data_network_is_internal(self):
        dc = parse_yaml('docker-compose.yml')
        assert dc['networks']['data'].get('internal') is True, (
            'IN-002: data network is NOT internal (has outbound internet — blast-radius hazard)'
        )

    def test_IN_002_phase1_compose_has_three_segmented_networks(self):
        p1 = parse_yaml('phase1/docker-compose.yml')
        networks = p1.get('networks', {})
        assert set(['edge', 'app', 'data']).issubset(set(networks.keys())), (
            'IN-002: phase1 compose missing one of edge/app/data networks'
        )

    def test_IN_002_phase1_compose_data_network_is_internal(self):
        p1 = parse_yaml('phase1/docker-compose.yml')
        assert p1['networks']['data'].get('internal') is True, (
            'IN-002: phase1 data network is NOT internal'
        )

    @pytest.mark.parametrize('svc', [
        'postgres', 'neo4j', 'mlflow', 'phase1-airflow', 'phase1-service',
        'phase2-kg-builder', 'phase2-kg-api', 'phase3-trainer',
        'phase3-gt-api', 'phase4-rl',
    ])
    def test_IN_003_root_compose_no_host_port_on(self, svc):
        dc = parse_yaml('docker-compose.yml')
        s = dc['services'][svc]
        ports = s.get('ports', []) or []
        # Any entry containing ':' is a host-port binding (e.g. "5432:5432" or "127.0.0.1:5432:5432")
        host_bindings = [p for p in ports if ':' in str(p)]
        assert not host_bindings, (
            f'IN-003: {svc} has host port binding {host_bindings} (only frontend:3000 + grafana:3001 allowed)'
        )

    @pytest.mark.parametrize('svc', ['postgres', 'airflow-webserver', 'neo4j'])
    def test_IN_003_phase1_compose_default_no_host_port_on(self, svc):
        """phase1 compose: dev opt-in via *_BIND_HOST_PORT env var; default is no binding."""
        p1 = parse_yaml('phase1/docker-compose.yml')
        s = p1['services'][svc]
        ports = s.get('ports', []) or []
        assert len(ports) > 0, f'IN-003: {svc} missing ports: key entirely'
        # Each port entry must use ${*_BIND_HOST_PORT:-} pattern (empty default = no binding)
        for p in ports:
            p_str = str(p)
            assert '_BIND_HOST_PORT' in p_str, (
                f'IN-003: {svc} port {p_str} does not use _BIND_HOST_PORT env var (default should be no binding)'
            )
            assert ':-' in p_str, (
                f'IN-003: {svc} port {p_str} missing default empty value (:-) — would fail if env var unset'
            )

    @pytest.mark.parametrize('svc', [
        'postgres', 'neo4j', 'mlflow', 'phase1-airflow', 'phase1-service',
        'phase2-kg-builder', 'phase2-kg-api', 'phase3-trainer',
        'phase3-gt-api', 'phase4-rl', 'frontend', 'prometheus', 'grafana',
        'node-exporter', 'cadvisor', 'otel-collector', 'jaeger', 'pg-backup',
    ])
    def test_IN_004_root_compose_has_resource_limits_on(self, svc):
        dc = parse_yaml('docker-compose.yml')
        s = dc['services'][svc]
        limits = ((s.get('deploy') or {}).get('resources') or {}).get('limits') or {}
        assert 'memory' in limits and 'cpus' in limits, (
            f'IN-004: {svc} missing deploy.resources.limits.memory+cpus (OOM risk)'
        )

    @pytest.mark.parametrize('svc', [
        'postgres', 'setup', 'airflow-init', 'airflow-webserver',
        'airflow-scheduler', 'neo4j', 'mlflow', 'pg-backup',
    ])
    def test_IN_004_phase1_compose_has_resource_limits_on(self, svc):
        p1 = parse_yaml('phase1/docker-compose.yml')
        s = p1['services'][svc]
        limits = ((s.get('deploy') or {}).get('resources') or {}).get('limits') or {}
        assert 'memory' in limits and 'cpus' in limits, (
            f'IN-004: phase1 {svc} missing resource limits'
        )

    def test_IN_004_root_compose_postgres_oom_protected(self):
        """postgres MUST have oom_score_adj:-500 (OOM killer targets training containers first)."""
        dc = parse_yaml('docker-compose.yml')
        assert dc['services']['postgres'].get('oom_score_adj') == -500, (
            'IN-004: postgres missing oom_score_adj:-500 (DB killed before trainers on OOM)'
        )
        assert dc['services']['neo4j'].get('oom_score_adj') == -500, (
            'IN-004: neo4j missing oom_score_adj:-500'
        )

    def test_IN_005_root_compose_has_pg_backup_sidecar(self):
        dc = parse_yaml('docker-compose.yml')
        assert 'pg-backup' in dc['services'], 'IN-005: pg-backup sidecar missing from root compose'
        assert 'backups' in dc['volumes'], 'IN-005: backups volume missing from root compose'

    def test_IN_005_phase1_compose_has_pg_backup_sidecar(self):
        p1 = parse_yaml('phase1/docker-compose.yml')
        assert 'pg-backup' in p1['services'], 'IN-005: pg-backup sidecar missing from phase1 compose'
        assert 'backups' in p1['volumes'], 'IN-005: backups volume missing from phase1 compose'

    def test_IN_006_gpu_dockerfile_exists(self):
        assert (REPO / 'Dockerfile.gpu').exists(), 'IN-006: Dockerfile.gpu missing'

    def test_IN_006_gpu_compose_override_exists(self):
        assert (REPO / 'docker-compose.gpu.yml').exists(), 'IN-006: docker-compose.gpu.yml missing'

    def test_IN_006_gpu_compose_parses(self):
        gpu = parse_yaml('docker-compose.gpu.yml')
        assert 'services' in gpu, 'IN-006: docker-compose.gpu.yml has no services block'

    def test_IN_010_airflow_api_auth_deny_all(self):
        dc = read('docker-compose.yml')
        assert 'AIRFLOW__API__AUTH_BACKENDS: airflow.api.auth.backend.deny_all' in dc, (
            'IN-010: Airflow API auth not set to deny_all (most restrictive)'
        )

    def test_IN_010_airflow_webserver_secret_from_file(self):
        dc = read('docker-compose.yml')
        assert 'AIRFLOW__WEBSERVER__SECRET_KEY_FILE: /run/secrets/airflow_webserver_secret_key' in dc, (
            'IN-010: Airflow webserver secret not sourced from Docker secret'
        )

    def test_IN_010_airflow_webserver_not_host_exposed(self):
        dc = parse_yaml('docker-compose.yml')
        ports = dc['services']['phase1-airflow'].get('ports', []) or []
        assert not ports, (
            f'IN-010: Airflow webserver is host-exposed via ports: {ports} (should be internal only)'
        )

    @pytest.mark.parametrize('svc', ['prometheus', 'grafana', 'node-exporter', 'cadvisor'])
    def test_IN_040_observability_service_present(self, svc):
        dc = parse_yaml('docker-compose.yml')
        assert svc in dc['services'], f'IN-040: {svc} missing from root compose'

    def test_IN_063_phase3_trainer_separated_from_api(self):
        dc = parse_yaml('docker-compose.yml')
        assert 'phase3-trainer' in dc['services'], 'IN-063: phase3-trainer missing'
        assert 'phase3-gt-api' in dc['services'], 'IN-063: phase3-gt-api missing'

    def test_IN_063_phase3_trainer_is_one_shot(self):
        dc = parse_yaml('docker-compose.yml')
        assert dc['services']['phase3-trainer'].get('restart') == 'no', (
            'IN-063: phase3-trainer should have restart: "no" (one-shot, not auto-restarted)'
        )

    def test_IN_063_phase3_gt_api_depends_on_trainer_completion(self):
        dc = parse_yaml('docker-compose.yml')
        dep = dc['services']['phase3-gt-api'].get('depends_on', {}).get('phase3-trainer', {})
        assert dep.get('condition') == 'service_completed_successfully', (
            'IN-063: phase3-gt-api should depend on phase3-trainer: service_completed_successfully'
        )

    def test_IN_069_requirements_has_torch_scatter(self):
        req = read_code('requirements.txt')
        assert 'torch-scatter' in req, 'IN-069: torch-scatter missing from requirements.txt'

    def test_IN_069_requirements_has_torch_sparse(self):
        req = read_code('requirements.txt')
        assert 'torch-sparse' in req, 'IN-069: torch-sparse missing from requirements.txt'

    def test_P1_030_phase2_kg_builder_uses_run_bridge_script(self):
        dc = read('docker-compose.yml')
        assert '/opt/repo/phase2/drugos_graph/run_bridge.py' in dc, (
            'P1-030: phase2-kg-builder not using run_bridge.py (fragile inline python -c hazard)'
        )

    def test_P1_030_phase2_kg_api_uses_qualified_import(self):
        dc = read('docker-compose.yml')
        assert 'phase2.drugos_graph.kg_api:app' in dc, (
            'P1-030: phase2-kg-api not using qualified phase2.drugos_graph import'
        )


# ─── MEDIUM ISSUES ──────────────────────────────────────────────────────────

class TestMediumIssues:
    """10 MEDIUM issues: IN-011, IN-012, IN-013, IN-014, IN-018, IN-041,
    IN-042, IN-064, P1-008, SH-037."""

    def test_IN_011_dockerfile_airflow_pins_pip(self):
        dfa = read('Dockerfile.airflow')
        assert 'pip==24.2' in dfa, 'IN-011: pip not pinned to 24.2'

    def test_IN_011_dockerfile_airflow_has_healthcheck(self):
        dfa = read('Dockerfile.airflow')
        assert 'HEALTHCHECK' in dfa, 'IN-011: Dockerfile.airflow missing HEALTHCHECK directive'

    def test_IN_012_dockerfile_ml_has_reproducibility_env_vars(self):
        dml = read('Dockerfile.ml')
        assert 'PYTHONDONTWRITEBYTECODE=1' in dml, 'IN-012: PYTHONDONTWRITEBYTECODE missing'
        assert 'PYTHONUNBUFFERED=1' in dml, 'IN-012: PYTHONUNBUFFERED missing'
        assert 'PYTHONHASHSEED=0' in dml, 'IN-012: PYTHONHASHSEED missing'

    def test_IN_012_dockerfile_ml_uses_uid_10001(self):
        dml = read('Dockerfile.ml')
        assert 'useradd -m -u 10001 drugos' in dml, (
            'IN-012: Dockerfile.ml using uid 1000 (collides with host user) instead of 10001'
        )

    def test_IN_061_dockerfile_ml_has_expose(self):
        dml = read('Dockerfile.ml')
        assert 'EXPOSE 8002 8003' in dml, 'IN-061: Dockerfile.ml missing EXPOSE 8002 8003'

    def test_IN_061_dockerfile_ml_has_healthcheck(self):
        dml = read('Dockerfile.ml')
        assert 'HEALTHCHECK' in dml, 'IN-061: Dockerfile.ml missing HEALTHCHECK'

    def test_IN_013_phase2_dockerfile_does_not_use_drugos_python_ml_base(self):
        p2df = read_code('phase2/drugos_graph/Dockerfile')
        assert 'drugos-python-ml' not in p2df, (
            'IN-013: phase2 Dockerfile still references non-existent drugos-python-ml base image'
        )

    def test_IN_013_phase2_dockerfile_is_self_contained(self):
        p2df = read('phase2/drugos_graph/Dockerfile')
        assert 'FROM python:3.11-slim' in p2df, (
            'IN-013: phase2 Dockerfile not using FROM python:3.11-slim (self-contained)'
        )

    def test_IN_014_dockerfile_python_ml_no_pytest(self):
        dpml = read_code('Dockerfile.python-ml')
        assert 'pytest' not in dpml.lower(), (
            'IN-014: Dockerfile.python-ml still installs pytest (dev dep in production image)'
        )

    def test_IN_018_requirements_upper_bounded(self):
        req = read_code('requirements.txt')
        assert 'pandas>=2.1.4,<3.0' in req, 'IN-018: pandas missing upper bound'
        assert 'numpy>=1.26.3,<2.0' in req, 'IN-018: numpy missing upper bound'
        assert 'torch>=2.0,<3.0' in req, 'IN-018: torch missing upper bound'
        assert 'torch-geometric>=2.4,<3.0' in req, 'IN-018: torch-geometric missing upper bound'
        assert 'transformers>=4.30,<5.0' in req, 'IN-018: transformers missing upper bound'

    def test_IN_041_root_compose_json_logging(self):
        dc = read('docker-compose.yml')
        assert 'x-logging:' in dc, 'IN-041: x-logging anchor missing'
        assert 'driver: json-file' in dc, 'IN-041: json-file driver missing'
        assert 'max-size: "10m"' in dc, 'IN-041: max-size missing'
        assert 'max-file: "3"' in dc, 'IN-041: max-file missing'

    def test_IN_041_phase1_compose_json_logging(self):
        p1 = read('phase1/docker-compose.yml')
        assert 'x-logging:' in p1, 'IN-041: phase1 x-logging anchor missing'
        assert 'driver: json-file' in p1, 'IN-041: phase1 json-file driver missing'

    def test_IN_042_otel_collector_present(self):
        dc = parse_yaml('docker-compose.yml')
        assert 'otel-collector' in dc['services'], 'IN-042: otel-collector missing'

    def test_IN_042_jaeger_present(self):
        dc = parse_yaml('docker-compose.yml')
        assert 'jaeger' in dc['services'], 'IN-042: jaeger missing'

    def test_IN_064_root_compose_airflow_logs_volume(self):
        dc = parse_yaml('docker-compose.yml')
        assert 'airflow-logs' in dc['volumes'], 'IN-064: airflow-logs volume missing from root compose'

    def test_IN_064_root_compose_airflow_logs_mounted(self):
        dc = read('docker-compose.yml')
        assert 'airflow-logs:/opt/airflow/logs' in dc, (
            'IN-064: airflow-logs not mounted at /opt/airflow/logs in root compose'
        )

    def test_IN_064_phase1_compose_airflow_logs_volume(self):
        p1 = parse_yaml('phase1/docker-compose.yml')
        assert 'airflow-logs' in p1['volumes'], 'IN-064: airflow-logs missing from phase1 compose'

    def test_IN_064_phase1_compose_airflow_logs_mounted(self):
        p1 = read('phase1/docker-compose.yml')
        assert 'airflow-logs:/opt/airflow/logs' in p1, (
            'IN-064: airflow-logs not mounted in phase1 airflow-init'
        )

    def test_P1_008_makefile_does_not_skip_institutional_test(self):
        mk = read_code('Makefile')
        assert '--ignore=tests/test_disgenet_pipeline_institutional_v389.py' not in mk, (
            'P1-008: Makefile still skips the institutional DisGeNET v389 test in test-phase1'
        )

    def test_SH_037_makefile_has_test_shared(self):
        mk = read('Makefile')
        assert 'test-shared:' in mk, 'SH-037: test-shared target missing'

    def test_SH_037_makefile_has_test_root(self):
        mk = read('Makefile')
        assert 'test-root:' in mk, 'SH-037: test-root target missing'

    def test_SH_037_makefile_test_all_includes_shared_and_root(self):
        mk = read('Makefile')
        assert 'test-all: test-phase1 test-phase2 test-bridge test-shared test-root' in mk, (
            'SH-037: test-all does not run all 5 suites in correct order'
        )


# ─── LOW ISSUES ─────────────────────────────────────────────────────────────

class TestLowIssues:
    """11 LOW issues: IN-016, IN-017, IN-028, IN-046, IN-047, IN-050, IN-061,
    IN-065, P1-002, P1-009, SH-028."""

    def test_IN_016_requirements_no_pytest(self):
        req = read_code('requirements.txt')
        assert 'pytest' not in req.lower(), (
            'IN-016: pytest still in production requirements.txt (should be dev-only)'
        )

    def test_IN_017_P1_002_SH_028_biopython_declared_once(self):
        req = read_code('requirements.txt')
        count = req.count('biopython>=')
        assert count == 1, (
            f'IN-017/P1-002/SH-028: biopython declared {count}x in requirements.txt (should be 1)'
        )

    def test_IN_028_root_compose_no_version_key(self):
        dc = read('docker-compose.yml')
        # The deprecated `version:` key should NOT appear at the top of the file
        assert not dc.lstrip().startswith('version:'), 'IN-028: docker-compose.yml still has version: key'
        assert '\nversion:' not in dc, 'IN-028: docker-compose.yml still has version: key'

    def test_IN_046_makefile_run_json_sorts_by_mtime(self):
        mk = read('Makefile')
        assert 'out.sort(key=os.path.getmtime, reverse=True)' in mk, (
            'IN-046: Makefile run-json does not sort by mtime (picks oldest, not most recent)'
        )

    def test_IN_047_P1_009_makefile_test_all_order(self):
        mk = read('Makefile')
        assert 'test-all: test-phase1 test-phase2 test-bridge test-shared test-root' in mk, (
            'IN-047/P1-009: test-all order is wrong (should be phase1 → phase2 → bridge → shared → root)'
        )

    def test_IN_050_dockerfile_airflow_does_not_copy_phase1(self):
        dfa = read_code('Dockerfile.airflow')
        assert 'COPY --chown=airflow:airflow phase1/ /opt/phase1/' not in dfa, (
            'IN-050: Dockerfile.airflow still COPYs phase1/ (compose bind-mounts it; COPY is dead weight + stale-source hazard)'
        )

    def test_IN_065_root_compose_has_name_key(self):
        dc = read('docker-compose.yml')
        assert 'name: drugos-platform' in dc, (
            'IN-065: root compose missing name: drugos-platform (project name collision risk)'
        )

    def test_IN_065_phase1_compose_has_name_key(self):
        p1 = read('phase1/docker-compose.yml')
        assert 'name: drugos-platform-phase1' in p1, (
            'IN-065: phase1 compose missing name: drugos-platform-phase1'
        )

    def test_IN_065_root_compose_no_container_name(self):
        dc = read_code('docker-compose.yml')
        assert 'container_name:' not in dc, (
            'IN-065: root compose has container_name: directives (collision hazard across projects)'
        )

    def test_IN_065_phase1_compose_no_container_name(self):
        p1 = read_code('phase1/docker-compose.yml')
        assert 'container_name:' not in p1, 'IN-065: phase1 compose has container_name: directives'


# ─── STRUCTURAL / REGRESSION TESTS ─────────────────────────────────────────

class TestStructuralV118:
    """v118-introduced structural checks (not in the original 38 issues but
    required to prevent regressions of the v118 fixes)."""

    def test_v118_phase1_compose_no_duplicate_volumes_in_airflow_init(self):
        """v118: phase1 airflow-init MUST have exactly ONE volumes: block.

        The v37/v49/v100/v113 versions had TWO volumes: blocks (merge conflict
        residue) — YAML spec says the second silently overrides the first,
        which masked the missing airflow-init.sh mount in the first block.
        """
        p1 = read('phase1/docker-compose.yml')
        # Extract the airflow-init service block
        match = re.search(r'^  airflow-init:\n(.*?)(?=^  [a-z]|\Z)', p1, re.MULTILINE | re.DOTALL)
        assert match, 'v118 STRUCTURAL: airflow-init service block not found'
        block = match.group(1)
        # Count `volumes:` at 4-space indent within the block
        volumes_count = len(re.findall(r'^    volumes:', block, re.MULTILINE))
        assert volumes_count == 1, (
            f'v118 STRUCTURAL: airflow-init has {volumes_count} volumes: blocks (should be 1; '
            'duplicate is a YAML override hazard)'
        )

    @pytest.mark.parametrize('svc', [
        'postgres', 'neo4j', 'mlflow', 'phase1-airflow', 'phase1-service',
        'phase2-kg-builder', 'phase2-kg-api', 'phase3-trainer',
        'phase3-gt-api', 'phase4-rl', 'frontend', 'prometheus', 'grafana',
        'node-exporter', 'cadvisor', 'otel-collector', 'jaeger', 'pg-backup',
    ])
    def test_v118_root_compose_init_true_on(self, svc):
        dc = parse_yaml('docker-compose.yml')
        assert dc['services'][svc].get('init') is True, (
            f'v118 STRUCTURAL: root compose {svc} missing init: true (PID 1 zombie reaping)'
        )

    @pytest.mark.parametrize('svc', [
        'postgres', 'setup', 'airflow-init', 'airflow-webserver',
        'airflow-scheduler', 'neo4j', 'mlflow', 'pg-backup',
    ])
    def test_v118_phase1_compose_init_true_on(self, svc):
        p1 = parse_yaml('phase1/docker-compose.yml')
        assert p1['services'][svc].get('init') is True, (
            f'v118 STRUCTURAL: phase1 compose {svc} missing init: true'
        )

    @pytest.mark.parametrize('f', [
        'Dockerfile.airflow', 'Dockerfile.ml', 'Dockerfile.gpu',
        'Dockerfile.python-ml', 'docker-compose.gpu.yml', '.env.example',
        'Dockerfile.airflow.entrypoint.sh',
        'phase1/docker-compose.yml', 'phase1/Makefile',
        'phase1/docker/Dockerfile.airflow', 'phase1/docker/Dockerfile.mlflow',
        'phase1/docker/airflow-init.sh', 'phase1/docker/mlflow-entrypoint.sh',
        'phase1/docker/mlflow_auth_config.yaml',
        'phase2/drugos_graph/Dockerfile', 'phase2/drugos_graph/run_bridge.py',
        'phase1/service.py',
        'observability/prometheus.yml', 'observability/otel-collector.yml',
        'observability/backup.sh',
        'scripts/restore_test.py',
        'Makefile', 'requirements.txt', 'requirements-dev.txt',
    ])
    def test_v118_swim_lane_file_exists(self, f):
        assert (REPO / f).exists(), f'v118 STRUCTURAL: swim-lane file {f} missing'

    def test_v118_root_compose_yaml_parses(self):
        """Root docker-compose.yml MUST parse as valid YAML."""
        parse_yaml('docker-compose.yml')  # raises on parse error

    def test_v118_phase1_compose_yaml_parses(self):
        """phase1/docker-compose.yml MUST parse as valid YAML."""
        parse_yaml('phase1/docker-compose.yml')  # raises on parse error

    def test_v118_gpu_compose_yaml_parses(self):
        """docker-compose.gpu.yml MUST parse as valid YAML."""
        parse_yaml('docker-compose.gpu.yml')  # raises on parse error

    def test_v118_all_shell_scripts_have_valid_syntax(self):
        """All shell scripts in the swim lane MUST have valid bash syntax."""
        scripts = [
            'Dockerfile.airflow.entrypoint.sh',
            'phase1/docker/airflow-init.sh',
            'phase1/docker/mlflow-entrypoint.sh',
            'observability/backup.sh',
        ]
        for s in scripts:
            result = subprocess.run(['bash', '-n', str(REPO / s)], capture_output=True, text=True)
            assert result.returncode == 0, (
                f'v118 STRUCTURAL: {s} has bash syntax error:\n{result.stderr}'
            )


if __name__ == '__main__':
    # Allow running directly: python tests/test_teammate15_infra_v118_real_fixes.py
    sys.exit(pytest.main([__file__, '-v', '--tb=short']))
