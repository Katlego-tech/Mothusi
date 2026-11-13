#!/usr/bin/env python3
"""Stateful, strict CLI double; every invocation is recorded before dispatch."""

import json
import os
import sys
from pathlib import Path

path = Path(os.environ["FAKE_STATE"])
state = json.loads(path.read_text())
args = sys.argv[1:]
kind = os.environ["FAKE_CLI_NAME"]
state.setdefault("calls", []).append([kind, *args])


def save():
    path.write_text(json.dumps(state))


def option(name):
    return args[args.index(name) + 1]


def emit(value):
    save()
    print(value)
    sys.exit(0)


def fail(message):
    save()
    print(message, file=sys.stderr)
    sys.exit(1)


if kind == "curl":
    if state.get("curl_fail"):
        fail("HTTP 503")
    emit(
        json.dumps(
            state.get(
                "health", {"status": "ok", "db": "ok", "ollama": "ok", "models": ["qwen3:14b"]}
            )
        )
    )

if "--region" not in args:
    fail("missing --region")
service, action = args[:2]
if (service, action) == ("ssm", "get-parameter"):
    name = option("--name")
    if name in state.get("parameter_errors", {}):
        fail(state["parameter_errors"][name])
    if name == "/mothusi/staging/service-names" and state.get("service_parameter_error"):
        fail(state["service_parameter_error"])
    if name not in state["parameters"]:
        fail("ParameterNotFound")
    emit(state["parameters"][name])
if service == "rds":
    assert (
        option("--db-instance-identifier") == "other-db"
        if action != "describe-events"
        else option("--source-identifier") == "other-db"
    )
    if action == "describe-db-instances":
        emit(state["db"])
    if action == "describe-events":
        emit(state.get("stopped_at", "None"))
    if action in ("start-db-instance", "stop-db-instance"):
        state["db"] = state.get(
            "db_after_change", "available" if action.startswith("start") else "stopped"
        )
        emit("{}")
if service == "ec2":
    assert option("--instance-ids") == "i-other-host"
    if action == "describe-instances":
        emit(state["ec2"])
    if action == "describe-instance-status":
        emit(state.get("ec2_checks", "ok\tok"))
    if action == "start-instances" and state.get("ec2_start_error"):
        fail(
            f"An error occurred ({state['ec2_start_error']}) when calling the StartInstances"
            " operation"
        )
    if action in ("start-instances", "stop-instances"):
        state["ec2"] = state.get(
            "ec2_after_change", "running" if action.startswith("start") else "stopped"
        )
        emit("{}")
if service == "ssm":
    if action == "send-command":
        assert option("--instance-ids") == "i-other-host"
        command = json.loads(option("--parameters"))["commands"][0]
        state.setdefault("commands", []).append(command)
        state["last_command"] = command
        emit(f"cmd-{len(state['commands'])}")
    if action == "get-command-invocation":
        if state.get("command_pending"):
            emit("InProgress\t-1")
        if state["last_command"] == "/opt/mothusi/model-ready.sh" and state.get(
            "readiness_failures", 0
        ):
            state["readiness_failures"] -= 1
            emit("Failed\t1")
        if "ollama run" in state["last_command"] and state.get("warm_fails"):
            emit("Failed\t1")
        if state["last_command"] == "/opt/mothusi/model-ready.sh" and not state.get(
            "model_ready", True
        ):
            emit("Failed\t1")
        emit("Success\t0")
if service == "ecs":
    assert option("--cluster") == "other-cluster"
    name = option("--service" if action == "update-service" else "--services")
    svc = state["ecs"][name]
    if action == "describe-services":
        emit(
            f"0\t{svc['desired']}\t{svc['running']}\t{svc.get('pending', 0)}\t"
            f"{svc.get('deployments', 1)}\tACTIVE"
        )
    if action == "update-service":
        svc["desired"] = int(option("--desired-count"))
        if not state.get("ecs_stuck"):
            svc["running"] = svc["desired"]
        emit("{}")
fail(f"unsupported call: {args}")
