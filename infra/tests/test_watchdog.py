"""Watchdog alarms and consecutive-check thresholds against local CLI doubles."""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fake_cli import Rig
from mothusi_infra.stage import PRODUCTION


@pytest.fixture
def rig(tmp_path):
    return Rig(tmp_path)


def timestamp(hours):
    return (
        (datetime.now(UTC) + timedelta(hours=hours))
        .astimezone(timezone(timedelta(hours=2)))
        .isoformat()
    )


def stack(when, status):
    return {
        "StackName": PRODUCTION.stack("Service"),
        "StackStatus": status,
        "LastUpdatedTime": when,
    }


def stopped(when, code):
    return {
        "stoppedAt": when,
        "stopCode": code,
        "stoppedReason": "container failed",
        "containers": [{"exitCode": 1}],
    }


def test_all_clear_is_read_only(rig):
    result = rig.run("watchdog.sh", "--once")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all clear" in result.stdout
    calls = rig.calls("aws")
    assert calls
    for call in calls:
        action = call[4]  # aws --region <region> <service> <action>
        assert action.startswith(("describe-", "list-", "get-", "filter-")) or (
            call[3] == "ssm" and action == "send-command"
        )
        assert not action.startswith(("stop-", "start-", "update-", "delete-", "put-", "create-"))
    assert rig.calls("gh")
    assert not rig.calls("notify-send")


@pytest.mark.parametrize(
    "changes,message",
    [
        (
            {
                "runs": [
                    {
                        "name": "CI",
                        "conclusion": "failure",
                        "createdAt": timestamp(1),
                        "url": "https://example.invalid/run",
                    }
                ]
            },
            "GitHub Actions run CI failure",
        ),
        ({"stacks": [stack(timestamp(1), "UPDATE_ROLLBACK_COMPLETE")]}, "stack"),
        ({"stacks": [stack(timestamp(1), "UPDATE_FAILED")]}, "stack"),
        ({"stopped_tasks": [stopped(timestamp(1), "TaskFailedToStart")]}, "TaskFailedToStart"),
        (
            {"stopped_tasks": [stopped(timestamp(1), "EssentialContainerExited")]},
            "EssentialContainerExited",
        ),
        ({"gpu": "stopped"}, "GPU host"),
        ({"model_failures": 1}, "failed model load"),
        ({"log_event": "WATCHDOG_RUN_HALTED run-42"}, "WATCHDOG_RUN_HALTED"),
        ({"log_event": "WATCHDOG_NEEDS_TEACHER run-42"}, "WATCHDOG_NEEDS_TEACHER"),
    ],
)
def test_each_alarm(rig, changes, message):
    rig.state.update(changes)
    result = rig.run("watchdog.sh", "--once")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ALARM" in result.stdout
    assert message in result.stdout
    assert rig.calls("notify-send")
    assert "all clear" not in result.stdout


def test_failures_before_start_are_ignored_with_local_offset(rig):
    when = timestamp(-1)
    assert when.endswith("+02:00")
    rig.state.update(
        stacks=[stack(when, "UPDATE_ROLLBACK_COMPLETE")],
        stopped_tasks=[
            stopped(when, "TaskFailedToStart"),
            stopped(when, "EssentialContainerExited"),
        ],
    )
    result = rig.run("watchdog.sh", "--once")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all clear" in result.stdout
    assert "ALARM" not in result.stdout
    assert rig.calls("aws", "describe-tasks")


def test_service_shortfall_alarms_on_third_check(rig):
    rig.state["services"][0]["runningCount"] = 0
    result = rig.run("watchdog.sh")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ALARM" in result.stdout
    assert "3 checks" in result.stdout
    assert len(rig.calls("aws", "describe-services")) == 3
    assert rig.state["gpu_reads"] == 2
    assert rig.state["log_reads"] == 2


def test_four_idle_readings_alarm(rig):
    rig.state["utilization"] = [0, 2, 9, 0]
    result = rig.run("watchdog.sh", "--gpu-busy")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ALARM" in result.stdout
    assert "idle" in result.stdout
    assert rig.state["gpu_reads"] == 4
    assert rig.state["log_reads"] == 3


def test_busy_reading_resets_idle_counter(rig):
    rig.state["utilization"] = [0, 2, 9, 10, 0, 2, 9, 10]
    result = rig.run("watchdog.sh", "--gpu-busy", end_after=8)
    assert result.returncode == 0, result.stdout + result.stderr
    assert rig.state["gpu_reads"] >= 8
    assert "ALARM" not in result.stdout
    assert "ended; nothing went wrong" in result.stdout


def test_until_exits_when_process_ends(rig):
    result = rig.run("watchdog.sh", end_after=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert rig.state["log_reads"] >= 2
    assert "ended; nothing went wrong" in result.stdout
    assert "ALARM" not in result.stdout
