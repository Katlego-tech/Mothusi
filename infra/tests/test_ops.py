"""Exercise the runbook as an operator, with no AWS/network access."""

import json
import os
import re
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

OPS = Path(__file__).resolve().parents[1] / "ops.sh"
FAKE = Path(__file__).with_name("fake_aws.py")


@pytest.fixture
def rig(tmp_path):
    for name in ("aws", "curl"):
        shim = tmp_path / name
        shim.write_text(f'#!/bin/sh\nexport FAKE_CLI_NAME={name}\nexec python3 "{FAKE}" "$@"\n')
        shim.chmod(0o755)
    state = {
        "parameters": {
            "/mothusi/staging/model-host/instance-id": "i-other-host",
            "/mothusi/staging/db/endpoint": "other-db.random.rds.amazonaws.com",
            "/mothusi/staging/cluster-name": "other-cluster",
            "/mothusi/staging/service-names": "worker,api",
            "/mothusi/staging/front/distribution-domain": "other.cloudfront.net",
        },
        "db": "stopped",
        "ec2": "stopped",
        "ecs": {name: {"desired": 0, "running": 0} for name in ("worker", "api")},
    }
    path = tmp_path / "state.json"

    def run(verb, **changes):
        state.update(changes)
        path.write_text(json.dumps(state))
        env = {
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "FAKE_STATE": str(path),
            "OPS_WAIT_CAP_S": "1",
            "OPS_POLL_S": "0.05",
        }
        expected_region = state.get("region", "af-south-1")
        if "region" in state:
            env["AWS_REGION"] = expected_region
        else:
            env.pop("AWS_REGION", None)
        result = subprocess.run(
            ["bash", str(OPS), "staging", verb], env=env, capture_output=True, text=True, timeout=15
        )
        state.update(json.loads(path.read_text()))
        for call in state.get("calls", []):
            if call[0] == "aws":
                assert call[call.index("--region") + 1] == expected_region
        return result

    return state, run


def running(state):
    state.update(db="available", ec2="running")
    for svc in state["ecs"].values():
        svc.update(desired=1, running=1)


def mutations(state):
    return [
        call[2]
        for call in state["calls"]
        if call[0] == "aws"
        and call[2]
        in {
            "start-db-instance",
            "start-instances",
            "send-command",
            "update-service",
            "stop-instances",
            "stop-db-instance",
        }
    ]


def test_cold_start_order_and_readiness(rig):
    state, run = rig
    result = run("start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith("overall: Running")
    assert mutations(state) == [
        "start-instances",
        "start-db-instance",
        "send-command",
        "send-command",
        "update-service",
        "update-service",
    ]
    assert state["commands"] == [
        "/opt/mothusi/model-ready.sh",
        'HOME=/root ollama run qwen3:14b ""',
    ]
    actions = [call[2] if call[0] == "aws" else "curl" for call in state["calls"]]
    assert (
        actions.index("start-instances")
        < actions.index("start-db-instance")
        < actions.index("describe-instance-status")
        < actions.index("send-command")
        < actions.index("update-service")
        < actions.index("curl")
    )
    assert any("https://other.cloudfront.net/api/health" in call for call in state["calls"])
    assert re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ .*RDS", result.stdout)


def test_running_start_is_idempotent(rig):
    state, run = rig
    running(state)
    result = run("start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert mutations(state) == []


@pytest.mark.parametrize(
    "changes,step",
    [
        ({"model_ready": False}, "model readiness"),
        ({"command_pending": True}, "model readiness"),
        ({"db_after_change": "starting"}, "RDS available"),
        ({"ec2_checks": "initializing\tinitializing"}, "EC2 status"),
        ({"ecs_stuck": True}, "ECS"),
        ({"health": {"status": "ok", "db": "unreachable", "ollama": "ok"}}, "health"),
        ({"curl_fail": True}, "health"),
    ],
)
def test_wait_failures_are_bounded_and_degraded(rig, changes, step):
    _, run = rig
    before = time.monotonic()
    result = run("start", **changes)
    assert result.returncode == 1
    assert time.monotonic() - before < 10
    assert "overall: Degraded" in result.stdout, (result.stdout, result.stderr)
    assert step in result.stdout


def test_stop_reverse_order_and_idempotence(rig):
    state, run = rig
    running(state)
    result = run("stop")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith("overall: Stopped")
    assert mutations(state) == [
        "update-service",
        "update-service",
        "stop-instances",
        "stop-db-instance",
    ]
    state["calls"] = []
    assert run("stop").returncode == 0
    assert mutations(state) == []


@pytest.mark.parametrize(
    "db,ec2,desired,count,overall",
    [
        ("stopped", "stopped", 0, 0, "Stopped"),
        ("available", "running", 1, 1, "Running"),
        ("starting", "pending", 1, 0, "Starting"),
        ("stopping", "stopping", 0, 1, "Stopping"),
        ("available", "stopped", 0, 0, "Stopping"),
    ],
)
def test_status_states(rig, db, ec2, desired, count, overall):
    state, run = rig
    for svc in state["ecs"].values():
        svc.update(desired=desired, running=count)
    result = run("status", db=db, ec2=ec2)
    assert result.returncode == 0
    assert f"overall: {overall}" in result.stdout
    for label in ("RDS:", "EC2:", "ECS worker:", "ECS api:", "health:"):
        assert label in result.stdout
    assert mutations(state) == []


@pytest.mark.parametrize("verb", ["start", "stop", "status"])
def test_missing_required_ssm_names_parameter(rig, verb):
    state, run = rig
    del state["parameters"]["/mothusi/staging/db/endpoint"]
    result = run(verb)
    assert result.returncode == (0 if verb == "status" else 1)
    assert "Cannot read SSM parameter /mothusi/staging/db/endpoint" in result.stdout + result.stderr
    assert "overall: Degraded" in result.stdout


def test_absent_service_list_is_supported(rig):
    state, run = rig
    del state["parameters"]["/mothusi/staging/service-names"]
    result = run("start")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(call[1] == "ecs" for call in state["calls"])
    assert state["commands"] == [
        "/opt/mothusi/model-ready.sh",
        'HOME=/root ollama run qwen3:14b ""',
    ]


def test_old_rds_stop_warning_uses_event_date(rig):
    _, run = rig
    date = (datetime.now(UTC) - timedelta(days=6, hours=1)).isoformat()
    result = run("status", stopped_at=date)
    assert result.returncode == 0
    assert "warning:" in result.stdout
    assert "RDS event" in result.stdout
    assert "6 days" in result.stdout


def test_stop_timeout_is_degraded(rig):
    state, run = rig
    running(state)
    result = run("stop", db_after_change="stopping")
    assert result.returncode == 1
    assert "RDS stopped" in result.stdout
    assert "overall: Degraded" in result.stdout


def test_custom_region_is_used_on_every_call(rig):
    state, run = rig
    result = run("status", region="eu-west-1")
    assert result.returncode == 0
    assert state["calls"]


def test_readiness_retries_after_failed_exit(rig):
    state, run = rig
    result = run("start", readiness_failures=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["commands"] == [
        "/opt/mothusi/model-ready.sh",
        "/opt/mothusi/model-ready.sh",
        'HOME=/root ollama run qwen3:14b ""',
    ]


def test_inflight_command_is_polled_without_resubmission(rig):
    state, run = rig
    result = run("start", command_pending=True)
    assert result.returncode == 1
    assert state["commands"] == ["/opt/mothusi/model-ready.sh"]


def test_warm_failure_cannot_start_ecs(rig):
    state, run = rig
    result = run("start", warm_fails=True)
    assert result.returncode == 1
    assert "marker warm-up" in result.stdout
    assert "update-service" not in mutations(state)


def test_optional_parameter_access_denied_is_fatal(rig):
    _, run = rig
    result = run("start", service_parameter_error="AccessDeniedException")
    assert result.returncode == 1
    assert (
        "Cannot read SSM parameter /mothusi/staging/service-names" in result.stdout + result.stderr
    )


@pytest.mark.parametrize(
    "health",
    [
        {"status": "ok", "ollama": "ok"},
        {"status": "ok", "db": "ok", "ollama": "ok", "knowledge": "unreachable"},
        ["ok"],
        "invalid JSON",
    ],
)
def test_status_rejects_missing_or_bad_dependencies(rig, health):
    state, run = rig
    running(state)
    result = run("status", health=health)
    assert result.returncode == 0
    assert "overall: Degraded" in result.stdout


def test_recent_stop_has_no_six_day_warning(rig):
    _, run = rig
    date = (datetime.now(UTC) - timedelta(days=5)).isoformat()
    result = run("status", stopped_at=date)
    assert result.returncode == 0
    assert "warning:" not in result.stdout


@pytest.mark.parametrize(
    "name", ["model-host/instance-id", "db/endpoint", "cluster-name", "front/distribution-domain"]
)
@pytest.mark.parametrize("failure", ["missing", "unreadable"])
@pytest.mark.parametrize("verb", ["status", "start", "stop"])
def test_parameter_failure_is_reported_once_without_losing_known_components(
    rig, name, failure, verb
):
    state, run = rig
    parameter = f"/mothusi/staging/{name}"
    if failure == "missing":
        del state["parameters"][parameter]
    else:
        # Empty/None text is also an unreadable value, without altering the fake CLI.
        state["parameters"][parameter] = "None"
    result = run(verb)
    output = result.stdout + result.stderr
    assert result.returncode == (0 if verb == "status" else 1)
    assert output.count(f"Cannot read SSM parameter {parameter}") == 1
    assert result.stdout.count("overall:") == 1
    assert result.stdout.rstrip().endswith("overall: Degraded")
    assert "Operation failed" not in output
    assert mutations(state) == []
    if verb == "status":
        assert "RDS:" in result.stdout
        assert "EC2:" in result.stdout
        assert "health:" in result.stdout
        if name != "cluster-name":
            assert "ECS worker:" in result.stdout
            assert "ECS api:" in result.stdout
        if name != "db/endpoint":
            assert "RDS: stopped (other-db)" in result.stdout
        if name != "model-host/instance-id":
            assert "EC2: stopped (i-other-host)" in result.stdout
        assert not any(call[0] == "curl" for call in state["calls"])


@pytest.mark.parametrize("verb", ["status", "start", "stop"])
def test_unreadable_service_parameter_reports_partial_status_once(rig, verb):
    state, run = rig
    result = run(verb, service_parameter_error="AccessDeniedException")
    assert result.returncode == (0 if verb == "status" else 1)
    assert (result.stdout + result.stderr).count(
        "Cannot read SSM parameter /mothusi/staging/service-names"
    ) == 1
    assert result.stdout.count("overall:") == 1
    assert result.stdout.rstrip().endswith("overall: Degraded")
    assert mutations(state) == []
    if verb == "status":
        assert "RDS: stopped (other-db)" in result.stdout
        assert "EC2: stopped (i-other-host)" in result.stdout


@pytest.mark.parametrize(
    "db,ec2,desired,count,expected",
    [
        ("available", "stopped", 0, 0, "Stopping"),
        ("available", "running", 0, 0, "Stopping"),
        ("stopped", "running", 1, 1, "Starting"),
        ("available", "stopped", 1, 1, "Starting"),
        ("starting", "stopped", 0, 0, "Starting"),
        ("available", "pending", 0, 0, "Starting"),
        ("starting", "stopping", 0, 0, "Degraded"),
    ],
)
def test_partial_lifecycle_status(rig, db, ec2, desired, count, expected):
    state, run = rig
    for service in state["ecs"].values():
        service.update(desired=desired, running=count)
    result = run("status", db=db, ec2=ec2)
    assert result.returncode == 0
    assert result.stdout.rstrip().endswith(f"overall: {expected}")
    assert mutations(state) == []


@pytest.mark.parametrize("verb", ["status", "start", "stop"])
def test_required_parameter_access_denied(rig, verb):
    state, run = rig
    name = "/mothusi/staging/front/distribution-domain"
    result = run(verb, parameter_errors={name: "AccessDeniedException"})
    assert result.returncode == (0 if verb == "status" else 1)
    assert result.stdout.rstrip().endswith("overall: Degraded")
    assert result.stdout.count("overall:") == 1
    assert (result.stdout + result.stderr).count(f"Cannot read SSM parameter {name}") == 1
    assert mutations(state) == []
    if verb == "status":
        assert "RDS: stopped (other-db)" in result.stdout
        assert "EC2: stopped (i-other-host)" in result.stdout
        assert "ECS worker:" in result.stdout


@pytest.mark.parametrize(
    "db,ec2,expected",
    [
        ("available", "stopped", "Stopping"),
        ("stopped", "running", "Starting"),
    ],
)
def test_partial_lifecycle_without_ecs(rig, db, ec2, expected):
    state, run = rig
    del state["parameters"]["/mothusi/staging/service-names"]
    result = run("status", db=db, ec2=ec2)
    assert result.returncode == 0
    assert result.stdout.rstrip().endswith(f"overall: {expected}")
    assert mutations(state) == []


def test_no_gpu_capacity_fails_fast_without_starting_rds(rig):
    # deployment-aws.md §5.2: the g4dn starts first, so a capacity shortage (2026-11-12) ends
    # the start in seconds with RDS untouched rather than billing.
    state, run = rig
    result = run("start", ec2_start_error="InsufficientInstanceCapacity")
    assert result.returncode == 1
    assert "start-db-instance" not in mutations(state)
    assert "overall: Degraded" in result.stdout
