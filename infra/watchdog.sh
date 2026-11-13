#!/usr/bin/env bash
# The Mothusi watchdog: runs on your machine and exits loudly the moment the environment needs you,
# instead of you finding out by polling AWS.
#
#   bash infra/watchdog.sh                    watch until something goes wrong (Ctrl-C to stop)
#   bash infra/watchdog.sh --gpu-busy         also alarm when the GPU sits idle (an eval or a run)
#   bash infra/watchdog.sh --until <pid>      exit 0 when that process ends (the eval, a deploy)
#   bash infra/watchdog.sh --once             one pass, then exit
#
# It alarms (prints ALARM, a desktop notification and the terminal bell, then exits 1) on:
#   - a failed, cancelled or timed-out GitHub Actions run started since the watchdog began;
#   - a stack updated since then that failed or rolled back;
#   - an ECS service short of its desired tasks for 3 checks, a failed deployment, or a task that
#     failed to start or whose container exited;
#   - the GPU host not running, or failing its status checks;
#   - a failed model load in Ollama's log on the host;
#   - a run that halted, or that waits for the teacher (the core's WATCHDOG_* log markers);
#   - with --gpu-busy, the GPU under 10% for 4 checks in a row (a model switch reads ~2% for ~75 s,
#     so one low reading is not an alarm).
# It only reads. It never stops, starts or changes anything: the GPU stays up.
#
# Needs: the AWS CLI signed in to the account, jq, and gh (for the Actions check; skipped without).
set -uo pipefail

region=${AWS_REGION:-af-south-1}
interval=${WATCHDOG_INTERVAL_S:-60}
gpu_busy=no
until_pid=""
once=no
while [ $# -gt 0 ]; do
    case "$1" in
        --gpu-busy) gpu_busy=yes ;;
        --until) until_pid="${2:?--until needs a process id}"; shift ;;
        --once) once=yes ;;
        -h|--help) sed -n '2,23p' "$0"; exit 0 ;;
        *) echo "watchdog: unknown argument $1" >&2; exit 2 ;;
    esac
    shift
done

# The environment's names live in one place (mothusi_infra/stage.py).
infra_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
names="$(PYTHONPATH="$infra_dir" python3 -c '
from mothusi_infra.stage import PRODUCTION as s
print(s.ssm("").rstrip("/"), s.stack_prefix, "/" + s.label)')" || { echo "watchdog: can't read stage.py" >&2; exit 2; }
read -r ssm stack_prefix log_prefix <<<"$names"

aws_() { aws --region "$region" "$@"; }
param() { aws_ ssm get-parameter --name "$ssm/$1" --query Parameter.Value --output text 2>/dev/null; }
now() { date '+%H:%M SAST'; }
# The CLI prints times with whatever offset it likes (UTC or local): compare as epoch seconds.
since_start() { [ "$(date -d "$1" +%s 2>/dev/null || echo 0)" -ge "$started_s" ]; }

alarm() {
    echo "ALARM $(now): $*"
    command -v notify-send >/dev/null 2>&1 && notify-send -u critical "Mothusi watchdog" "$*"
    printf '\a'
    exit 1
}

started_iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
started_s="$(date +%s)"
logs_since_ms=$(( started_s * 1000 ))
declare -A short_of=()
idle=0

cluster="$(param cluster-name)" || cluster=""
host="$(param model-host/instance-id)" || host=""
[ -n "$cluster" ] && [ -n "$host" ] || alarm "can't read $ssm/cluster-name or $ssm/model-host/instance-id: is the AWS CLI signed in?"
echo "$(now) watching cluster $cluster, GPU host $host, stacks $stack_prefix*/Mothusi*$( [ "$gpu_busy" = yes ] && echo ', GPU busy')"

check_actions() {
    command -v gh >/dev/null 2>&1 || return 0
    local failed
    failed="$(gh run list --limit 20 --json name,conclusion,createdAt,url \
        --jq ".[] | select(.createdAt >= \"$started_iso\") | select(.conclusion == \"failure\" or .conclusion == \"cancelled\" or .conclusion == \"timed_out\") | \"\(.name) \(.conclusion): \(.url)\"" 2>/dev/null | head -1)"
    [ -z "$failed" ] || alarm "GitHub Actions run $failed"
}

check_stacks() {
    local when what
    while IFS=$'\t' read -r when what; do
        since_start "$when" && alarm "stack $what"
    done < <(aws_ cloudformation describe-stacks --output json 2>/dev/null | jq -r --arg p "$stack_prefix" '
        .Stacks[] | select((.StackName | startswith($p)) or (.StackName | startswith("Mothusi")))
        | select(.StackStatus | test("ROLLBACK|FAILED"))
        | "\(.LastUpdatedTime // .CreationTime)\t\(.StackName) is \(.StackStatus)"')
}

check_ecs() {
    local services json name desired running reason
    services="$(param service-names)" || services=""
    [ -n "$services" ] && [ "$services" != None ] || return 0
    IFS=',' read -ra svc <<<"$services"
    json="$(aws_ ecs describe-services --cluster "$cluster" --services "${svc[@]}" --output json 2>/dev/null)" || return 0
    while read -r name desired running; do
        if [ "$running" -lt "$desired" ]; then
            short_of[$name]=$(( ${short_of[$name]:-0} + 1 ))
            [ "${short_of[$name]}" -ge 3 ] && alarm "ECS $name has $running of $desired tasks running (3 checks)"
        else
            short_of[$name]=0
        fi
    done < <(jq -r '.services[] | "\(.serviceName) \(.desiredCount) \(.runningCount)"' <<<"$json")
    reason="$(jq -r '.services[] | .serviceName as $s | .deployments[] | select(.rolloutState == "FAILED") | "\($s): \(.rolloutStateReason)"' <<<"$json" | head -1)"
    [ -z "$reason" ] || alarm "ECS deployment failed for $reason"
    for name in "${svc[@]}"; do
        read -ra arns <<<"$(aws_ ecs list-tasks --cluster "$cluster" --service-name "$name" --desired-status STOPPED --query 'taskArns[:5]' --output text 2>/dev/null)"
        [ "${#arns[@]}" -gt 0 ] && [ "${arns[0]}" != None ] || continue
        while IFS=$'\t' read -r when reason; do
            since_start "$when" && alarm "ECS $name task stopped at $when, $reason"
        done < <(aws_ ecs describe-tasks --cluster "$cluster" --tasks "${arns[@]}" --output json 2>/dev/null | jq -r '
            .tasks[] | select(.stoppedAt != null)
            | select(.stopCode == "TaskFailedToStart" or .stopCode == "EssentialContainerExited")
            | "\(.stoppedAt)\t\(.stopCode): \(.stoppedReason), exit \([.containers[].exitCode] | map(tostring) | join(","))"')
    done
}

check_gpu_host() {
    local state checks cid out util loads
    read -r state checks <<<"$(aws_ ec2 describe-instance-status --instance-ids "$host" --include-all-instances \
        --query 'InstanceStatuses[0].[InstanceState.Name, InstanceStatus.Status]' --output text 2>/dev/null)"
    [ "$state" = running ] || alarm "the GPU host $host is ${state:-unreadable}"
    [ "$checks" != impaired ] || alarm "the GPU host $host is failing its status checks"
    # On the host itself: GPU utilisation, and any failed model load since the last check.
    cid="$(aws_ ssm send-command --instance-ids "$host" --document-name AWS-RunShellScript \
        --parameters "commands=[\"nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits\",\"journalctl -u ollama --since '-$(( interval + 30 )) s' --no-pager | grep -c -i -E 'error loading model|load failed|panic' || true\"]" \
        --query Command.CommandId --output text 2>/dev/null)" || return 0
    sleep 4
    out="$(aws_ ssm get-command-invocation --command-id "$cid" --instance-id "$host" --query StandardOutputContent --output text 2>/dev/null)" || return 0
    util="$(sed -n 1p <<<"$out" | tr -dc 0-9)"
    loads="$(sed -n 2p <<<"$out" | tr -dc 0-9)"
    [ "${loads:-0}" -eq 0 ] || alarm "Ollama logged a failed model load on the GPU host (journalctl -u ollama)"
    if [ "$gpu_busy" = yes ] && [ -n "$util" ]; then
        if [ "$util" -lt 10 ]; then idle=$(( idle + 1 )); else idle=0; fi
        [ "$idle" -ge 4 ] && alarm "the GPU has been idle (${util}%) for 4 checks while work should be running"
    fi
}

check_runs() {
    local hits next
    next=$(( $(date +%s) * 1000 ))
    hits="$(aws_ logs filter-log-events --log-group-name "$log_prefix/core" --start-time "$logs_since_ms" \
        --filter-pattern '?WATCHDOG_RUN_HALTED ?WATCHDOG_NEEDS_TEACHER' --query 'events[].message' --output text 2>/dev/null | head -c 300)"
    logs_since_ms=$next
    [ -z "$hits" ] || [ "$hits" = None ] || alarm "a run needs you: $hits"
}

while :; do
    check_actions
    check_stacks
    check_ecs
    check_gpu_host
    check_runs
    if [ -n "$until_pid" ] && ! kill -0 "$until_pid" 2>/dev/null; then
        echo "$(now) process $until_pid ended; nothing went wrong while it ran"
        exit 0
    fi
    [ "$once" = yes ] && { echo "$(now) all clear"; exit 0; }
    sleep "$interval"
done
