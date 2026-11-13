"""Operator cost reporting derives its burn from the repository price table."""

import tomllib

import pytest
from fake_cli import INFRA, Rig


@pytest.mark.parametrize(
    "gpu,db,tasks,gpu_on,stack_on",
    [
        ("running", "available", 2, True, True),
        ("running", "stopped", 0, True, False),
        ("stopped", "stopped", 0, False, False),
        ("stopped", "stopped", 1, False, True),
    ],
)
def test_states_and_burn(tmp_path, gpu, db, tasks, gpu_on, stack_on):
    rig = Rig(tmp_path)
    rig.state.update(gpu=gpu, db=db, running_tasks=tasks)
    pricing = tomllib.loads((INFRA.parent / "packages/llm/pricing.toml").read_text())
    gpu_rate = next(
        item["usd_per_hour"] for item in pricing["instance"] if item["name"] == "g4dn.xlarge"
    )
    hourly = gpu_on * gpu_rate + stack_on * pricing["stack"]["usd_per_hour"]
    zar = pricing["rates"]["zar_per_usd"]
    result = rig.run("costs.sh")
    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert any("GPU host i-test-host" in line and line.endswith(gpu) for line in lines)
    assert any("database test-db" in line and line.endswith(db) for line in lines)
    assert any(
        "ECS tasks in test-cluster" in line and line.endswith(f"{tasks} running") for line in lines
    )
    assert (
        f"Burn: ${hourly:.3f}/h (R{hourly * zar:.2f}), "
        f"${hourly * 24:.2f}/day (R{hourly * 24 * zar:.0f})"
    ) in lines
    assert not rig.calls("aws", "get-cost-and-usage")


def test_spend_reports_service_usage_and_credits(tmp_path):
    rig = Rig(tmp_path)
    rig.state.update(
        usage={
            "ResultsByTime": [
                {
                    "Groups": [
                        {"Keys": [name], "Metrics": {"UnblendedCost": {"Amount": amount}}}
                        for name, amount in [("Amazon RDS", "4.25"), ("Amazon EC2", "12.50")]
                    ]
                }
            ]
        },
        credits="-9.75",
    )
    result = rig.run("costs.sh", "--spend")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "usage   $16.75" in result.stdout
    assert "12.50  Amazon EC2" in result.stdout
    assert "4.25  Amazon RDS" in result.stdout
    assert result.stdout.index("Amazon EC2") < result.stdout.index("Amazon RDS")
    assert "credits $9.75 applied" in result.stdout
    assert len(rig.calls("aws", "get-cost-and-usage")) == 2
