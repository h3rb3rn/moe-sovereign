"""
test_deployment_artifacts.py — Smoke tests for the universal deployment refactor.

Validates the artifacts introduced by the "Single-Artifact / Multi-Wrapper /
Multi-Profile" refactor:

  - Dockerfile        : multi-stage, non-root, OCI-compliant
  - Helm chart        : lint + renders for both enterprise and solo profiles
  - LXC setup.sh      : executable, syntactically valid bash, idempotent structure
  - Podman Quadlet    : well-formed systemd unit
  - Alloy configs     : present and referenced by the LXC setup

These tests are intentionally lightweight — they don't require a live k8s
cluster or an LXC host. They verify the *shape* of the deployment contract so
that a future refactor cannot silently break the multi-platform story.

External tools used when available (tests are skipped if missing):
  - helm   : `helm lint` and `helm template`
  - shellcheck (optional) : deeper shell-script linting
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
CHART_DIR = REPO_ROOT / "charts" / "moe-sovereign"


def _run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run a subprocess and capture stdout+stderr. Never raises — callers assert."""
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=False,
    )


# ── Dockerfile ────────────────────────────────────────────────────────────────


class TestDockerfile:
    """The refactored Dockerfile must satisfy the OCI/rootless/OpenShift contract."""

    @pytest.fixture(scope="class")
    def dockerfile(self) -> str:
        return (REPO_ROOT / "Dockerfile").read_text()

    def test_is_multi_stage(self, dockerfile: str) -> None:
        """Two FROM statements → multi-stage build (builder + runtime)."""
        from_lines = [ln for ln in dockerfile.splitlines() if ln.strip().startswith("FROM ")]
        assert len(from_lines) >= 2, f"expected ≥2 FROM stages, got {len(from_lines)}"

    def test_has_builder_stage(self, dockerfile: str) -> None:
        assert "AS builder" in dockerfile

    def test_has_runtime_stage(self, dockerfile: str) -> None:
        assert "AS runtime" in dockerfile

    def test_runs_as_non_root_uid_1001(self, dockerfile: str) -> None:
        """USER 1001 (numeric, not name) is required for OpenShift SCC compliance."""
        assert "USER 1001" in dockerfile, "Dockerfile must drop privileges to UID 1001"

    def test_has_oci_labels(self, dockerfile: str) -> None:
        """OCI labels are required for Harbor/Quay vulnerability scanning."""
        for label in (
            "org.opencontainers.image.title",
            "org.opencontainers.image.source",
            "org.opencontainers.image.licenses",
        ):
            assert label in dockerfile, f"missing OCI label {label!r}"

    def test_cmd_is_exec_form(self, dockerfile: str) -> None:
        """Exec-form CMD (JSON array) forwards SIGTERM cleanly to the Python process."""
        cmd_lines = [ln.strip() for ln in dockerfile.splitlines() if ln.strip().startswith("CMD")]
        assert cmd_lines, "Dockerfile must define a CMD"
        assert cmd_lines[-1].startswith("CMD ["), (
            f"CMD must use exec form (JSON array), got: {cmd_lines[-1]!r}"
        )

    def test_has_healthcheck(self, dockerfile: str) -> None:
        assert "HEALTHCHECK" in dockerfile

    def test_writable_paths_configurable_via_env(self, dockerfile: str) -> None:
        """Env-var driven paths are mandatory for readOnlyRootFilesystem mounts."""
        for var in ("MOE_LOGS_DIR", "MOE_CACHE_DIR", "MOE_EXPERTS_DIR"):
            assert var in dockerfile, f"missing env-var {var!r}"

    def test_no_apt_get_in_runtime_stage(self, dockerfile: str) -> None:
        """build-essential must live only in the builder stage."""
        # Split on "FROM" to isolate stages; runtime is the last stage.
        stages = dockerfile.split("FROM ")
        runtime_stage = stages[-1]
        assert "build-essential" not in runtime_stage, (
            "build-essential leaked into the runtime stage — breaks multi-stage slimming"
        )

    def test_default_profile_is_team(self, dockerfile: str) -> None:
        """Solo/team/enterprise switching should default to the middle-ground profile."""
        assert "MOE_PROFILE=team" in dockerfile


# ── Helm chart ────────────────────────────────────────────────────────────────


HELM = shutil.which("helm")


@pytest.mark.skipif(HELM is None, reason="helm binary not installed")
class TestHelmChart:
    """Helm chart renders cleanly for enterprise and solo profiles."""

    @pytest.fixture(scope="class", autouse=True)
    def _dep_build(self) -> None:
        """Ensure subcharts are present before rendering."""
        if not (CHART_DIR / "charts").exists():
            res = _run([HELM, "dependency", "build", str(CHART_DIR)])
            if res.returncode != 0:
                pytest.skip(f"helm dependency build failed: {res.stderr}")

    def test_chart_yaml_is_valid(self) -> None:
        meta = yaml.safe_load((CHART_DIR / "Chart.yaml").read_text())
        assert meta["name"] == "moe-sovereign"
        assert meta["apiVersion"] == "v2"
        # All four data-tier subcharts must be declared as conditional deps.
        dep_names = {d["name"] for d in meta.get("dependencies", [])}
        assert {"postgresql", "kafka", "valkey", "neo4j"}.issubset(dep_names)
        for d in meta["dependencies"]:
            assert "condition" in d, f"dep {d['name']} must be conditional"

    def test_helm_lint_passes(self) -> None:
        res = _run([HELM, "lint", str(CHART_DIR)])
        assert res.returncode == 0, f"helm lint failed:\n{res.stdout}\n{res.stderr}"

    def test_enterprise_profile_renders(self) -> None:
        """Enterprise = no subcharts, only the three Python deployments."""
        res = _run([HELM, "template", "test", str(CHART_DIR)])
        assert res.returncode == 0, res.stderr
        docs = list(yaml.safe_load_all(res.stdout))
        kinds = [d.get("kind") for d in docs if isinstance(d, dict)]
        # Core kinds that must be present
        assert kinds.count("Deployment") == 3, f"expected 3 deployments, got {kinds.count('Deployment')}"
        assert "Ingress" in kinds
        assert "NetworkPolicy" in kinds
        assert "ServiceAccount" in kinds
        assert "ConfigMap" in kinds
        # Enterprise profile must NOT pull in StatefulSets from subcharts.
        assert "StatefulSet" not in kinds, "enterprise profile must not instantiate data-tier"

    def test_solo_profile_renders_with_subcharts(self) -> None:
        """Solo = full stack from Bitnami subcharts (postgres, kafka, valkey)."""
        values = CHART_DIR / "values-solo.yaml"
        res = _run([HELM, "template", "test", str(CHART_DIR), "-f", str(values)])
        assert res.returncode == 0, res.stderr
        docs = list(yaml.safe_load_all(res.stdout))
        kinds = [d.get("kind") for d in docs if isinstance(d, dict)]
        # Solo should bring in at least 3 StatefulSets (pg, kafka, valkey)
        assert kinds.count("StatefulSet") >= 3, (
            f"expected ≥3 StatefulSets from subcharts, got {kinds.count('StatefulSet')}"
        )

    def test_openshift_toggle_switches_ingress_to_route(self) -> None:
        """Setting openshift.enabled=true must emit Route objects, not Ingress."""
        res = _run([
            HELM, "template", "test", str(CHART_DIR),
            "--set", "openshift.enabled=true",
        ])
        assert res.returncode == 0, res.stderr
        docs = list(yaml.safe_load_all(res.stdout))
        kinds = [d.get("kind") for d in docs if isinstance(d, dict)]
        assert "Route" in kinds, "OpenShift mode must emit Route resources"
        assert "Ingress" not in kinds, "OpenShift mode must suppress Ingress"

    def test_orchestrator_deployment_security_context(self) -> None:
        """Orchestrator pod must run non-root with ALL caps dropped."""
        res = _run([HELM, "template", "test", str(CHART_DIR)])
        assert res.returncode == 0
        for doc in yaml.safe_load_all(res.stdout):
            if (isinstance(doc, dict)
                    and doc.get("kind") == "Deployment"
                    and "orchestrator" in doc["metadata"]["name"]):
                spec = doc["spec"]["template"]["spec"]
                pod_sec = spec.get("securityContext", {})
                assert pod_sec.get("runAsNonRoot") is True
                container = spec["containers"][0]
                csec = container["securityContext"]
                assert csec["allowPrivilegeEscalation"] is False
                assert csec["readOnlyRootFilesystem"] is True
                assert csec["capabilities"]["drop"] == ["ALL"]
                return
        pytest.fail("orchestrator Deployment not found in rendered output")

    def test_orchestrator_has_writable_empty_dirs(self) -> None:
        """readOnlyRootFilesystem requires emptyDir mounts for logs/cache/tmp."""
        res = _run([HELM, "template", "test", str(CHART_DIR)])
        for doc in yaml.safe_load_all(res.stdout):
            if (isinstance(doc, dict)
                    and doc.get("kind") == "Deployment"
                    and "orchestrator" in doc["metadata"]["name"]):
                volumes = doc["spec"]["template"]["spec"]["volumes"]
                empty_dirs = [v["name"] for v in volumes if "emptyDir" in v]
                for required in ("logs", "cache", "tmp"):
                    assert required in empty_dirs, f"missing emptyDir volume {required!r}"
                return
        pytest.fail("orchestrator Deployment not found")


# ── values files ──────────────────────────────────────────────────────────────


class TestHelmValuesFiles:
    """Profile values files must carry the expected knobs (no helm required)."""

    @pytest.mark.parametrize("profile,filename", [
        ("enterprise", "values.yaml"),
        ("solo",       "values-solo.yaml"),
        ("team",       "values-team.yaml"),
    ])
    def test_profile_value(self, profile: str, filename: str) -> None:
        data = yaml.safe_load((CHART_DIR / filename).read_text())
        assert data["profile"] == profile

    def test_enterprise_has_subcharts_disabled(self) -> None:
        data = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
        for sub in ("postgresql", "kafka", "valkey", "neo4j"):
            assert data[sub]["enabled"] is False, (
                f"{sub} must default to disabled in the enterprise profile"
            )

    def test_team_has_subcharts_enabled(self) -> None:
        data = yaml.safe_load((CHART_DIR / "values-team.yaml").read_text())
        for sub in ("postgresql", "kafka", "valkey", "neo4j"):
            assert data[sub]["enabled"] is True, (
                f"{sub} must be enabled in the team profile"
            )

    def test_solo_bounds_resource_limits(self) -> None:
        """Solo profile must cap orchestrator memory well below the team default."""
        data = yaml.safe_load((CHART_DIR / "values-solo.yaml").read_text())
        mem_limit = data["orchestrator"]["resources"]["limits"]["memory"]
        # Accept any sub-gigabyte cap (256Mi/512Mi/768Mi ...) but reject multi-GiB.
        assert mem_limit.endswith("Mi"), (
            f"solo profile must cap orchestrator below 1Gi, got {mem_limit}"
        )


# ── LXC setup script ─────────────────────────────────────────────────────────


class TestLXCSetupScript:
    """deploy/lxc/setup.sh contract."""

    @pytest.fixture(scope="class")
    def script_path(self) -> Path:
        return REPO_ROOT / "deploy" / "lxc" / "setup.sh"

    @pytest.fixture(scope="class")
    def script(self, script_path: Path) -> str:
        return script_path.read_text()

    def test_exists_and_executable(self, script_path: Path) -> None:
        assert script_path.exists()
        assert os.access(script_path, os.X_OK), "setup.sh must be chmod +x"

    def test_bash_syntax_valid(self, script_path: Path) -> None:
        res = _run(["bash", "-n", str(script_path)])
        assert res.returncode == 0, f"bash -n failed:\n{res.stderr}"

    def test_uses_set_euo_pipefail(self, script: str) -> None:
        assert "set -euo pipefail" in script, "strict mode is mandatory"

    def test_refuses_non_root(self, script: str) -> None:
        assert "$EUID" in script and "die" in script

    def test_creates_service_user_with_fixed_uid(self, script: str) -> None:
        assert "MOE_UID" in script
        assert "useradd" in script
        assert "loginctl enable-linger" in script, (
            "linger is required for user-scoped systemd services to survive logout"
        )

    def test_installs_quadlet_unit(self, script: str) -> None:
        assert "moe-orchestrator.container" in script
        assert ".config/containers/systemd" in script

    def test_alloy_optional_via_loki_url(self, script: str) -> None:
        """Alloy install must be skipped when LOKI_URL is empty (pure compute host)."""
        assert "LOKI_URL" in script
        # Both code paths exist: a guarded install block and an opt-out branch.
        assert 'if [[ -n "${LOKI_URL}" ]]' in script

    def test_prints_summary(self, script: str) -> None:
        """Operators need a human-readable summary at the end."""
        assert "bootstrap complete" in script.lower()


# ── Podman Quadlet unit ──────────────────────────────────────────────────────


class TestQuadletUnit:
    """deploy/podman/systemd/moe-orchestrator.container — systemd-native unit."""

    @pytest.fixture(scope="class")
    def unit(self) -> str:
        path = REPO_ROOT / "deploy" / "podman" / "systemd" / "moe-orchestrator.container"
        return path.read_text()

    def test_has_container_section(self, unit: str) -> None:
        assert "[Container]" in unit
        assert "[Service]" in unit
        assert "[Install]" in unit

    def test_image_points_at_registry(self, unit: str) -> None:
        assert "Image=" in unit
        assert "ghcr.io/moe-sovereign/orchestrator" in unit

    def test_readonly_rootfs_with_tmpfs(self, unit: str) -> None:
        """Must match the k8s containerSecurityContext.readOnlyRootFilesystem guarantee."""
        assert "ReadOnly=true" in unit
        assert "Tmpfs=/tmp" in unit

    def test_drops_all_capabilities(self, unit: str) -> None:
        assert "DropCapability=ALL" in unit
        assert "NoNewPrivileges=true" in unit

    def test_uses_journald_log_driver(self, unit: str) -> None:
        """Journald is what bridges LXC logs into Alloy → Loki."""
        assert "LogDriver=journald" in unit

    def test_volumes_use_user_home(self, unit: str) -> None:
        """%h is systemd's user-home placeholder — essential for rootless mode."""
        assert "%h/moe/logs" in unit or "%h/moe/cache" in unit


# ── Alloy config ─────────────────────────────────────────────────────────────


class TestAlloyConfig:
    """The universal Alloy config is the same across LXC/Compose/k8s."""

    @pytest.fixture(scope="class")
    def river(self) -> str:
        return (REPO_ROOT / "deploy" / "alloy" / "alloy.river").read_text()

    def test_has_loki_write(self, river: str) -> None:
        assert 'loki.write "default"' in river
        assert 'sys.env("LOKI_URL")' in river

    def test_has_otlp_receiver_for_tempo(self, river: str) -> None:
        assert "otelcol.receiver.otlp" in river
        assert "otelcol.exporter.otlp" in river

    def test_extracts_traceparent_into_label(self, river: str) -> None:
        """This is the cross-platform log⇄trace correlation mechanism."""
        assert "traceparent" in river
        assert "trace_id" in river

    def test_journal_source_present_for_lxc(self, river: str) -> None:
        """Journald source is how the LXC path ships logs."""
        assert "loki.source.journal" in river

    def test_systemd_service_exists(self) -> None:
        service = REPO_ROOT / "deploy" / "alloy" / "alloy.systemd.service"
        assert service.exists()
        content = service.read_text()
        assert "ExecStart=/usr/local/bin/alloy" in content
        assert "SupplementaryGroups=systemd-journal" in content, (
            "alloy must be in systemd-journal group to read the journal"
        )


# ── Cross-artifact consistency ───────────────────────────────────────────────


class TestCrossArtifactConsistency:
    """The same contract must hold across Dockerfile, Helm, and Quadlet."""

    def test_uid_1001_used_everywhere(self) -> None:
        """The Dockerfile's UID 1001 must match chart values and LXC script."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
        lxc = (REPO_ROOT / "deploy" / "lxc" / "setup.sh").read_text()

        assert "USER 1001" in dockerfile
        assert values["podSecurityContext"]["runAsUser"] == 1001
        assert values["containerSecurityContext"]["runAsUser"] == 1001
        assert 'MOE_UID:-1001' in lxc

    def test_port_8000_used_everywhere(self) -> None:
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        values = yaml.safe_load((CHART_DIR / "values.yaml").read_text())
        unit = (REPO_ROOT / "deploy" / "podman" / "systemd" / "moe-orchestrator.container").read_text()

        assert "EXPOSE 8000" in dockerfile
        assert values["orchestrator"]["port"] == 8000
        assert "PublishPort=8000:8000" in unit

    def test_env_var_names_consistent(self) -> None:
        """MOE_LOGS_DIR / MOE_CACHE_DIR / MOE_EXPERTS_DIR appear in all three places."""
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        cm_template = (CHART_DIR / "templates" / "configmap-env.yaml").read_text()
        unit = (REPO_ROOT / "deploy" / "podman" / "systemd" / "moe-orchestrator.container").read_text()

        for var in ("MOE_LOGS_DIR", "MOE_CACHE_DIR", "MOE_EXPERTS_DIR"):
            assert var in dockerfile, f"{var} missing from Dockerfile"
            assert var in cm_template, f"{var} missing from chart ConfigMap"
            assert var in unit, f"{var} missing from Quadlet unit"
