#!/usr/bin/env bash
# Deployment runbook: docs/design/deployment-aws.md §5.2 and §6.6.
set -euo pipefail
set -E

mode=${1:-}
region=${AWS_REGION:-af-south-1}
cap=${OPS_WAIT_CAP_S:-1200}
interval=${OPS_POLL_S:-20}
deadline=0

log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
# fd 3 is the script's real stdout. With set -E the ERR trap runs inside the failing command's
# redirections, so a failed `aws_cli ... >/dev/null` sent the Degraded line to /dev/null.
exec 3>&1
failed() {
    trap - ERR
    log "Operation failed; inspect the preceding step." >&3
    printf 'overall: Degraded\n' >&3
    if [[ "$mode" == status ]]; then exit 0; fi
    exit 1
}
trap failed ERR

if [[ $# != 1 || ! "$mode" =~ ^(start|stop|status)$ ]]; then
    printf 'Usage: bash infra/ops.sh start|stop|status\n' >&2
    exit 1
fi
# The adopted environment's SSM names and the GPU host's paths keep their original stem
# (mothusi_infra/stage.py says why); read it from there rather than writing it down twice.
infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stem="$(PYTHONPATH="$infra_dir" python3 -c 'from mothusi_infra.stage import PRODUCTION; print(PRODUCTION.adopted)')"
ssm="/$stem"
ready_script="/opt/$stem/model-ready.sh"
# Seconds may be fractional for polling, but the cap is a positive integer.
if [[ ! "$cap" =~ ^[1-9][0-9]*$ ]] || ! python3 -c 'import sys; x=float(sys.argv[1]); assert 0 < x < float("inf")' "$interval"; then
    log 'OPS_WAIT_CAP_S must be a positive integer and OPS_POLL_S must be positive.'
    failed
fi

aws_cli() {
    # Bound even a hung CLI/network call by the current polling deadline. This
    # also avoids relying on AWS waiter defaults or installed timeout utilities.
    python3 -c '
import subprocess, sys, time
cap, deadline = int(sys.argv[1]), float(sys.argv[2])
remaining = min(cap, deadline - time.time()) if deadline else cap
if remaining <= 0:
    sys.exit(124)
try:
    sys.exit(subprocess.run(sys.argv[3:], timeout=remaining).returncode)
except subprocess.TimeoutExpired:
    print("AWS CLI call exceeded the step deadline.", file=sys.stderr)
    sys.exit(124)
' "$cap" "$deadline" aws "$@" --region "$region"
}

parameter() {
    # Called directly in a conditional: no ERR trap can turn a failed read into
    # successful command-substitution output. Store only the actual SSM value.
    local name="$1" destination="$2" value
    printf -v "$destination" '%s' ''
    if ! value=$(aws_cli ssm get-parameter --name "$name" --query 'Parameter.Value' --output text); then
        log "Cannot read SSM parameter $name." >&2
        return 1
    fi
    if [[ -z "$value" || "$value" == None ]]; then
        log "Cannot read SSM parameter $name: it has no value." >&2
        return 1
    fi
    printf -v "$destination" '%s' "$value"
}

log "Read deployment parameters for $mode in $region"
parameters_ok=yes
host='' endpoint='' cluster='' domain=''
if ! parameter "$ssm/model-host/instance-id" host; then parameters_ok=no; fi
if ! parameter "$ssm/db/endpoint" endpoint; then parameters_ok=no; fi
db=${endpoint%%.*}
if ! parameter "$ssm/cluster-name" cluster; then parameters_ok=no; fi
if ! parameter "$ssm/front/distribution-domain" domain; then parameters_ok=no; fi
# Absence is permitted; permission/network errors must not hide deployed services.
services_known=yes
if service_names=$(aws_cli ssm get-parameter --name "$ssm/service-names" --query 'Parameter.Value' --output text 2>&1); then
    if [[ "$service_names" == None ]]; then service_names=''; fi
elif [[ "$service_names" == *ParameterNotFound* ]]; then
    service_names=''
else
    log "Cannot read SSM parameter $ssm/service-names." >&2
    parameters_ok=no
    services_known=no
    service_names=''
fi
services=()
if [[ -n "$service_names" ]]; then IFS=',' read -r -a services <<< "$service_names"; fi
if [[ "$parameters_ok" == no && "$mode" != status ]]; then
    printf 'overall: Degraded\n'
    exit 1
fi

rds_state() {
    [[ -n "$db" ]] || return 1
    aws_cli rds describe-db-instances --db-instance-identifier "$db" --query 'DBInstances[0].DBInstanceStatus' --output text
}
ec2_state() {
    [[ -n "$host" ]] || return 1
    aws_cli ec2 describe-instances --instance-ids "$host" --query 'Reservations[0].Instances[0].State.Name' --output text
}
ec2_ok() {
    local checks
    [[ $(ec2_state) == running ]] || return 1
    checks=$(aws_cli ec2 describe-instance-status --instance-ids "$host" --query 'InstanceStatuses[0].[InstanceStatus.Status,SystemStatus.Status]' --output text) || return 1
    [[ "$checks" == $'ok\tok' ]]
}
ecs_state() {
    [[ -n "$cluster" ]] || return 1
    aws_cli ecs describe-services --cluster "$cluster" --services "$1" --query '[length(failures),services[0].desiredCount,services[0].runningCount,services[0].pendingCount,length(services[0].deployments),services[0].status]' --output text
}
ecs_ok() {
    local row errors desired running pending deployments active
    row=$(ecs_state "$1") || return 1
    read -r errors desired running pending deployments active <<< "$row"
    [[ "$errors" == 0 && "$desired" == "$2" && "$running" == "$2" && "$pending" == 0 && "$deployments" == 1 && "$active" == ACTIVE ]]
}
rds_is() { [[ $(rds_state) == "$1" ]]; }
ec2_is() { [[ $(ec2_state) == "$1" ]]; }

poll() {
    local step=$1
    shift
    deadline=$(python3 -c 'import sys,time; print(time.time()+int(sys.argv[1]))' "$cap")
    log "Wait for $step (cap ${cap}s)"
    while true; do
        if "$@"; then
            deadline=0
            log "$step ready"
            return 0
        fi
        if python3 -c 'import sys,time; sys.exit(0 if time.time() >= float(sys.argv[1]) else 1)' "$deadline"; then
            deadline=0
            log "$step timed out after ${cap}s; Degraded"
            return 1
        fi
        python3 -c 'import sys,time; time.sleep(max(0,min(float(sys.argv[1]),float(sys.argv[2])-time.time())))' "$interval" "$deadline"
    done
}

delivery_timeout=$cap
if (( delivery_timeout < 30 )); then delivery_timeout=30; fi
if (( delivery_timeout > 2592000 )); then delivery_timeout=2592000; fi
command_id=''
command_step() {
    local command=$1 retry=$2 parameters result command_status response
    if [[ -z "$command_id" ]]; then
        parameters=$(python3 -c 'import json,sys; print(json.dumps({"commands":[sys.argv[1]]}))' "$command") || return 1
        command_id=$(aws_cli ssm send-command --instance-ids "$host" --document-name AWS-RunShellScript --parameters "$parameters" --timeout-seconds "$delivery_timeout" --query 'Command.CommandId' --output text) || { command_id=''; return 1; }
    fi
    result=$(aws_cli ssm get-command-invocation --command-id "$command_id" --instance-id "$host" --query '[Status,ResponseCode]' --output text) || return 1
    read -r command_status response <<< "$result"
    if [[ "$command_status" == Success && "$response" == 0 ]]; then
        command_id=''
        return 0
    fi
    case "$command_status" in
        Failed|Cancelled|TimedOut|Success)
            if [[ "$retry" == yes ]]; then command_id=''; fi
            ;;
    esac
    return 1
}

health_ok() {
    local reply limit
    [[ -n "$domain" ]] || return 1
    limit=$cap
    if [[ "$deadline" != 0 ]]; then
        limit=$(python3 -c 'import sys,time; print(max(.001,float(sys.argv[1])-time.time()))' "$deadline") || return 1
    fi
    reply=$(curl -fsS --max-time "$limit" "https://${domain}/api/health") || return 1
    python3 -c '
import json,sys
try:
    data=json.loads(sys.argv[1])
    # models is the tag list, not a dependency status (§6.5).
    assert isinstance(data,dict) and all(data.get(k)=="ok" for k in ("status","db","ollama"))
    assert all(v=="ok" for k,v in data.items() if k!="models")
except (ValueError,AssertionError):
    sys.exit(1)
' "$reply"
}

warn_old_stop() {
    local stopped_at
    # RDS events retain the most recent stopped message's timestamp. Do not use
    # InstanceCreateTime, which is creation time rather than the stop time.
    if ! stopped_at=$(aws_cli rds describe-events --source-type db-instance --source-identifier "$db" --duration 10080 --query "sort(Events[?contains(Message, \`stopped\`)].Date)[-1]" --output text); then
        log 'warning: cannot determine the RDS stop age from RDS events.'
        return
    fi
    if [[ -z "$stopped_at" || "$stopped_at" == None ]]; then
        log 'warning: no stopped RDS event found; stop age is unknown.'
    elif python3 -c 'from datetime import datetime,timezone; import sys; t=datetime.fromisoformat(sys.argv[1].replace("Z","+00:00")); sys.exit(0 if (datetime.now(timezone.utc)-t).total_seconds()>6*86400 else 1)' "$stopped_at"; then
        log 'warning: DB has been stopped more than 6 days (latest stopped RDS event); RDS restarts after 7 days.'
    fi
}

status() {
    local db_status host_status row errors desired running pending deployments active service
    local stopped=yes ready=yes starting=no stopping=no broken=no
    local services_up=yes services_down=yes
    if [[ "$parameters_ok" != yes ]]; then broken=yes; fi
    db_status=$(rds_state) || db_status=unknown
    host_status=$(ec2_state) || host_status=unknown
    log "RDS: $db_status (${db:-parameter unavailable})"
    log "EC2: $host_status (${host:-parameter unavailable})"
    [[ "$db_status" == stopped && "$host_status" == stopped ]] || stopped=no
    [[ "$db_status" == available && "$host_status" == running ]] || ready=no
    case "$db_status" in starting) starting=yes;; stopping) stopping=yes;; available|stopped) ;; *) broken=yes;; esac
    case "$host_status" in pending) starting=yes;; stopping) stopping=yes;; running|stopped) ;; *) broken=yes;; esac
    if [[ "$services_known" != yes ]]; then log 'ECS: unknown (service list unavailable)'; fi
    for service in "${services[@]}"; do
        row=$(ecs_state "$service") || row=unknown
        log "ECS $service: $row"
        read -r errors desired running pending deployments active <<< "$row"
        if [[ "$errors" != 0 || "$active" != ACTIVE ]]; then broken=yes; fi
        [[ "$desired" == 1 ]] || services_up=no
        [[ "$desired" == 0 ]] || services_down=no
        [[ "$desired" == 0 && "$running" == 0 && "$pending" == 0 ]] || stopped=no
        [[ "$desired" == 1 && "$running" == 1 && "$pending" == 0 && "$deployments" == 1 ]] || ready=no
        if [[ "$desired" == 0 && "$running" != 0 ]]; then stopping=yes; fi
        if [[ "$desired" == 1 && ( "$running" != 1 || "$pending" != 0 || "$deployments" != 1 ) ]]; then starting=yes; fi
    done
    if [[ "$ready" == yes && "$broken" == no ]] && ec2_ok && health_ok; then
        log 'health: ok'
        overall=Running
    else
        log 'health: unavailable or dependencies not ready'
        if [[ "$broken" == yes ]]; then overall=Degraded
        elif [[ "$stopped" == yes ]]; then overall=Stopped
        elif [[ "$stopping" == yes && "$starting" == no ]]; then overall=Stopping
        elif [[ "$starting" == yes && "$stopping" == no ]]; then overall=Starting
        # Between API transitions, available/running components are neutral.
        # ECS desired counts give the direction for a partially stopped stack.
        # A fully ready stack with failed health remains Degraded.
        elif [[ "$ready" == no && "$starting" == no && "$stopping" == no ]]; then
            if [[ "$services_down" == yes && "$services_up" == no ]]; then overall=Stopping
            elif [[ "$services_up" == yes && "$services_down" == no ]]; then overall=Starting
            elif (( ${#services[@]} == 0 )) && [[ "$db_status" == available && "$host_status" == stopped ]]; then overall=Stopping
            elif (( ${#services[@]} == 0 )) && [[ "$db_status" == stopped && "$host_status" == running ]]; then overall=Starting
            else overall=Degraded
            fi
        else overall=Degraded
        fi
    fi
    if [[ "$db_status" == stopped ]]; then warn_old_stop; fi
    printf 'overall: %s\n' "$overall"
}

if [[ "$mode" == status ]]; then status; exit 0; fi
if [[ "$mode" == start ]]; then
    log 'overall: Starting'
    needs_model=no
    # The g4dn first: when af-south-1b has no capacity, start-instances fails in seconds and the
    # ERR trap ends the start Degraded before RDS is started (deployment-aws.md §5.2). Then RDS,
    # so both boot together; the waits follow.
    current=$(ec2_state)
    if [[ "$current" != running ]]; then needs_model=yes; fi
    if [[ "$current" == stopping ]]; then poll 'EC2 stopped before restart' ec2_is stopped; current=stopped; fi
    if [[ "$current" == stopped ]]; then
        log "Start EC2 $host"
        aws_cli ec2 start-instances --instance-ids "$host" >/dev/null
    fi
    current=$(rds_state)
    if [[ "$current" != available ]]; then needs_model=yes; fi
    if [[ "$current" == stopping ]]; then poll 'RDS stopped before restart' rds_is stopped; current=stopped; fi
    if [[ "$current" == stopped ]]; then
        log "Start RDS $db"
        aws_cli rds start-db-instance --db-instance-identifier "$db" >/dev/null
    fi
    poll 'RDS available' rds_is available
    poll 'EC2 status ok' ec2_ok
    # An already healthy deployment needs no remote commands or warm-up.
    all_ready=yes
    for service in "${services[@]}"; do
        if ! ecs_ok "$service" 1; then all_ready=no; fi
    done
    if [[ "$needs_model" == yes || "$all_ready" != yes ]] || ! health_ok; then
        poll 'model readiness' command_step "$ready_script" yes
        # SSM Run Command starts shells without HOME, and the Ollama CLI panics without it.
        poll 'marker warm-up' command_step 'HOME=/root ollama run qwen3:14b ""' no
        for service in "${services[@]}"; do
            if ! ecs_ok "$service" 1; then
                log "Set ECS $service desired count 1"
                aws_cli ecs update-service --cluster "$cluster" --service "$service" --desired-count 1 >/dev/null
            fi
        done
        for service in "${services[@]}"; do
            poll "ECS $service stable" ecs_ok "$service" 1
        done
    fi
    poll 'health' health_ok
else
    log 'overall: Stopping'
    for service in "${services[@]}"; do
        if ! ecs_ok "$service" 0; then
            log "Set ECS $service desired count 0"
            aws_cli ecs update-service --cluster "$cluster" --service "$service" --desired-count 0 >/dev/null
        fi
    done
    for service in "${services[@]}"; do
        poll "ECS $service stopped" ecs_ok "$service" 0
    done
    current=$(ec2_state)
    if [[ "$current" == pending ]]; then poll 'EC2 running before stop' ec2_is running; current=running; fi
    if [[ "$current" != stopped && "$current" != stopping ]]; then
        log "Stop EC2 $host"
        aws_cli ec2 stop-instances --instance-ids "$host" >/dev/null
    fi
    poll 'EC2 stopped' ec2_is stopped
    current=$(rds_state)
    if [[ "$current" == starting ]]; then poll 'RDS available before stop' rds_is available; current=available; fi
    if [[ "$current" != stopped && "$current" != stopping ]]; then
        log "Stop RDS $db"
        aws_cli rds stop-db-instance --db-instance-identifier "$db" >/dev/null
    fi
    poll 'RDS stopped' rds_is stopped
fi
status
if [[ "$mode" == start && "$overall" != Running ]] || [[ "$mode" == stop && "$overall" != Stopped ]]; then exit 1; fi
