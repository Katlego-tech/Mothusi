#!/usr/bin/env python3
"""JSON-driven CLI double and isolated operator-script harness."""

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from mothusi_infra.stage import PRODUCTION

INFRA = Path(__file__).resolve().parents[1]


class Rig:
    def __init__(self, directory):
        self.path = directory / "state.json"
        self.state = {
            "parameters": {
                PRODUCTION.ssm("cluster-name"): "test-cluster",
                PRODUCTION.ssm("model-host/instance-id"): "i-test-host",
                PRODUCTION.ssm("db/endpoint"): "test-db.example.invalid",
                PRODUCTION.ssm("service-names"): "worker,api",
            },
            "gpu": "running",
            "db": "available",
            "running_tasks": 2,
            "services": [
                {"serviceName": name, "desiredCount": 1, "runningCount": 1, "deployments": []}
                for name in ("worker", "api")
            ],
        }
        for name in ("aws", "gh", "notify-send"):
            shim = directory / name
            shim.write_text(
                f"#!/bin/sh\nexport FAKE_CLI_NAME={name}\n"
                f'exec "{sys.executable}" "{Path(__file__).resolve()}" "$@"\n'
            )
            shim.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{directory}:{os.environ['PATH']}",
            "PYTHONPATH": str(INFRA),
            "FAKE_STATE": str(self.path),
            "AWS_REGION": "af-south-1",
            "WATCHDOG_INTERVAL_S": "0",
            "WATCHDOG_SSM_WAIT_S": "0",
        }

    def run(self, script, *args, end_after=None):
        process = None
        reaper = None
        if end_after is not None:
            process = subprocess.Popen(["sleep", "60"])
            reaper = threading.Thread(target=process.wait)
            reaper.start()
            self.state.update(end_after=end_after, until_pid=process.pid)
            args = (*args, "--until", str(process.pid))
        self.path.write_text(json.dumps(self.state))
        try:
            result = subprocess.run(
                ["bash", str(INFRA / script), *args],
                env=self.env,
                capture_output=True,
                text=True,
                timeout=15,
            )
        finally:
            if process is not None:
                process.terminate()
                assert reaper is not None
                reaper.join(timeout=5)
        self.state = json.loads(self.path.read_text())
        assert not self.state.get("errors"), self.state.get("errors")
        return result

    def calls(self, kind, action=None):
        return [
            call
            for call in self.state.get("calls", [])
            if call[0] == kind and (action is None or action in call)
        ]


def main():
    path = Path(os.environ["FAKE_STATE"])
    state = json.loads(path.read_text())
    args = sys.argv[1:]
    kind = os.environ["FAKE_CLI_NAME"]
    state.setdefault("calls", []).append([kind, *args])

    def option(name):
        return args[args.index(name) + 1]

    def dispatch():
        if kind == "notify-send":
            return ""
        if kind == "gh":
            assert args[:2] == ["run", "list"]
            # gh applies the caller's --jq expression, including its start-time filter.
            import re

            match = re.search(r'createdAt >= "([^"]+)"', option("--jq"))
            assert match is not None, f"unsupported --jq expression: {option('--jq')}"
            since = match[1]
            return "\n".join(
                f"{run['name']} {run['conclusion']}: {run['url']}"
                for run in state.get("runs", [])
                if run["createdAt"] >= since
                and run["conclusion"] in {"failure", "cancelled", "timed_out"}
            )
        assert kind == "aws"
        region = option("--region")
        index = args.index("--region")
        command = args[:index] + args[index + 2 :]
        service, action = command[:2]
        assert region == ("us-east-1" if service == "ce" else "af-south-1")
        if service == "ssm" and action == "get-parameter":
            return state["parameters"][option("--name")]
        if service == "cloudformation" and action == "describe-stacks":
            return json.dumps({"Stacks": state.get("stacks", [])})
        if service == "ecs":
            assert option("--cluster") == "test-cluster"
            if action == "describe-services":
                assert args[args.index("--services") + 1 : args.index("--output")] == [
                    "worker",
                    "api",
                ]
                return json.dumps({"services": state["services"]})
            if action == "list-tasks":
                if option("--desired-status") == "RUNNING":
                    return state["running_tasks"]
                assert option("--service-name") in {"worker", "api"}
                return "task-test" if state.get("stopped_tasks") else "None"
            if action == "describe-tasks":
                return json.dumps({"tasks": state.get("stopped_tasks", [])})
        if service == "ec2":
            assert option("--instance-ids") == "i-test-host"
            if action == "describe-instances":
                return state["gpu"]
            if action == "describe-instance-status":
                return f"{state['gpu']}\t{state.get('host_checks', 'ok')}"
        if service == "rds" and action == "describe-db-instances":
            assert option("--db-instance-identifier") == "test-db"
            return state["db"]
        if service == "ssm" and action == "send-command":
            assert option("--instance-ids") == "i-test-host"
            assert option("--document-name") == "AWS-RunShellScript"
            assert "nvidia-smi" in option("--parameters")
            assert "journalctl -u ollama" in option("--parameters")
            return "command-test"
        if service == "ssm" and action == "get-command-invocation":
            assert option("--command-id") == "command-test"
            assert option("--instance-id") == "i-test-host"
            readings = state.get("utilization", [50])
            count = state.get("gpu_reads", 0)
            state["gpu_reads"] = count + 1
            return f"{readings[min(count, len(readings) - 1)]}\n{state.get('model_failures', 0)}"
        if service == "logs" and action == "filter-log-events":
            assert option("--log-group-name") == f"/{PRODUCTION.label}/core"
            assert int(option("--start-time")) > 0
            assert option("--filter-pattern") == "?WATCHDOG_RUN_HALTED ?WATCHDOG_NEEDS_TEACHER"
            state["log_reads"] = state.get("log_reads", 0) + 1
            if state.get("end_after") == state["log_reads"]:
                os.kill(state["until_pid"], signal.SIGTERM)
            return state.get("log_event", "")
        if service == "ce" and action == "get-cost-and-usage":
            record = json.loads(option("--filter"))["Dimensions"]
            assert record["Key"] == "RECORD_TYPE"
            assert option("--granularity") == "MONTHLY"
            assert option("--metrics") == "UnblendedCost"
            if record["Values"] == ["Credit"]:
                return state["credits"]
            assert record["Values"] == ["Usage"]
            assert option("--group-by") == "Type=DIMENSION,Key=SERVICE"
            return json.dumps(state["usage"])
        raise AssertionError(f"unsupported call: {kind} {args}")

    try:
        output = dispatch()
    except (AssertionError, KeyError, ValueError) as error:
        state.setdefault("errors", []).append(str(error))
        output = ""
    partial = path.with_suffix(f".{os.getpid()}.tmp")
    partial.write_text(json.dumps(state))
    os.replace(partial, path)
    print(output)
    return int(bool(state.get("errors")))


if __name__ == "__main__":
    sys.exit(main())
