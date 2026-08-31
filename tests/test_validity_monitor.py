"""tests/test_validity_monitor.py — Unit tests for scripts/validity_monitor.py.

Covers the three check categories (permission-log scanning, container
health/crash-loop, disk space) plus the alert-cooldown mechanism that makes
this safe to run frequently via cron without spamming. `docker` calls are
mocked throughout -- these tests must never touch the real Docker daemon.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from unittest.mock import patch

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts/validity_monitor.py"
_spec = importlib.util.spec_from_file_location("validity_monitor", _MODULE_PATH)
vm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vm)


class TestAlertCooldown:
    def test_first_alert_fires_and_is_logged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        state = {"last_alert_ts": {}, "restart_counts": {}}
        rec = vm._alert(state, "k1", "critical", "boom")
        assert rec is not None
        assert "k1" in state["last_alert_ts"]
        lines = (tmp_path / "alerts.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        assert json.loads(lines[0])["key"] == "k1"

    def test_repeat_alert_within_cooldown_is_not_relogged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        monkeypatch.setattr(vm, "ALERT_COOLDOWN_S", 3600)
        state = {"last_alert_ts": {}, "restart_counts": {}}
        vm._alert(state, "k1", "critical", "boom")
        vm._alert(state, "k1", "critical", "boom again")
        lines = (tmp_path / "alerts.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1  # second call suppressed by cooldown

    def test_alert_after_cooldown_elapsed_fires_again(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        monkeypatch.setattr(vm, "ALERT_COOLDOWN_S", 1)
        state = {"last_alert_ts": {"k1": 0.0}, "restart_counts": {}}  # far in the past
        vm._alert(state, "k1", "critical", "boom")
        lines = (tmp_path / "alerts.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1

    def test_alert_write_failure_does_not_raise(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path)  # a directory, not a file -> OSError on open
        state = {"last_alert_ts": {}, "restart_counts": {}}
        vm._alert(state, "k1", "critical", "boom")  # must not raise


class TestPermissionPatterns:
    def test_matches_permission_denied(self):
        assert vm._PERMISSION_PATTERNS.search("Failed opening temp file: Permission denied")

    def test_matches_misconf(self):
        assert vm._PERMISSION_PATTERNS.search("MISCONF Valkey is configured to save RDB snapshots")

    def test_matches_read_only_filesystem(self):
        assert vm._PERMISSION_PATTERNS.search("OSError: [Errno 30] Read-only file system")

    def test_ordinary_log_line_does_not_match(self):
        assert vm._PERMISSION_PATTERNS.search("Ready to accept connections tcp") is None


class TestCheckPermissions:
    def test_flags_container_with_permission_error_in_logs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        with patch.object(vm, "_run_docker", return_value="1:M some line\nMISCONF cannot save\n"):
            state = {"last_alert_ts": {}, "restart_counts": {}}
            findings = vm.check_permissions(state, ["terra_cache"])
        assert len(findings) == 1
        assert findings[0]["key"] == "perm:terra_cache"

    def test_clean_logs_produce_no_finding(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        with patch.object(vm, "_run_docker", return_value="Ready to accept connections\n"):
            state = {"last_alert_ts": {}, "restart_counts": {}}
            findings = vm.check_permissions(state, ["terra_cache"])
        assert findings == []


class TestCheckContainerHealth:
    _LABELS = {vm._COMPOSE_PROJECT_LABEL: vm._COMPOSE_PROJECT_NAME}

    def _inspect_json(self, containers):
        return json.dumps(containers)

    def test_ignores_containers_from_other_compose_projects(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        other = [{
            "Name": "/hermes", "RestartCount": 0,
            "Config": {"Labels": {vm._COMPOSE_PROJECT_LABEL: "hermes-project"}},
            "State": {"Status": "running"},
        }]
        with patch.object(vm, "_run_docker", side_effect=["id1", self._inspect_json(other)]):
            state = {"last_alert_ts": {}, "restart_counts": {}}
            findings = vm.check_container_health(state)
        assert findings == []
        assert state["restart_counts"] == {}

    def test_flags_unhealthy_container(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        mine = [{
            "Name": "/langgraph-orchestrator", "RestartCount": 0,
            "Config": {"Labels": self._LABELS},
            "State": {"Status": "running", "Health": {"Status": "unhealthy"}},
        }]
        with patch.object(vm, "_run_docker", side_effect=["id1", self._inspect_json(mine)]):
            state = {"last_alert_ts": {}, "restart_counts": {}}
            findings = vm.check_container_health(state)
        assert len(findings) == 1
        assert "unhealthy" in findings[0]["key"]

    def test_flags_active_restart_count_delta_not_historical_count(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        mine = [{
            "Name": "/mcp-precision", "RestartCount": 5,
            "Config": {"Labels": self._LABELS},
            "State": {"Status": "running"},
        }]
        with patch.object(vm, "_run_docker", side_effect=["id1", self._inspect_json(mine)]):
            # First pass: establishes baseline (RestartCount=5 already, historical -- no alert)
            state = {"last_alert_ts": {}, "restart_counts": {"mcp-precision": 5}}
            findings = vm.check_container_health(state)
        assert findings == []

        mine[0]["RestartCount"] = 6
        with patch.object(vm, "_run_docker", side_effect=["id1", self._inspect_json(mine)]):
            findings = vm.check_container_health(state)
        assert len(findings) == 1
        assert "restart_delta" in findings[0]["key"]


class TestCheckDiskSpace:
    def test_flags_critical_disk_usage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        fake_usage = type("Usage", (), {"total": 100, "used": 97, "free": 3})()
        with patch.object(vm.shutil, "disk_usage", return_value=fake_usage):
            state = {"last_alert_ts": {}, "restart_counts": {}}
            findings = vm.check_disk_space(state)
        assert any(f["key"].startswith("disk_crit:") for f in findings)

    def test_flags_warning_disk_usage(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        fake_usage = type("Usage", (), {"total": 100, "used": 88, "free": 12})()
        with patch.object(vm.shutil, "disk_usage", return_value=fake_usage):
            state = {"last_alert_ts": {}, "restart_counts": {}}
            findings = vm.check_disk_space(state)
        assert any(f["key"].startswith("disk_warn:") for f in findings)
        assert not any(f["key"].startswith("disk_crit:") for f in findings)

    def test_healthy_disk_usage_produces_no_finding(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vm, "ALERT_LOG", tmp_path / "alerts.jsonl")
        fake_usage = type("Usage", (), {"total": 100, "used": 50, "free": 50})()
        with patch.object(vm.shutil, "disk_usage", return_value=fake_usage):
            state = {"last_alert_ts": {}, "restart_counts": {}}
            findings = vm.check_disk_space(state)
        assert findings == []
