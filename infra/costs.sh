#!/usr/bin/env bash
# What the environment costs: what's running now and its hourly burn, and (with --spend) what the
# month has cost so far. Runs on your machine; it only reads.
#
#   bash infra/costs.sh           what's running and the burn per hour and per day (free)
#   bash infra/costs.sh --spend   also this month's usage by service and the credits applied
#                                 (Cost Explorer: two requests at $0.01 each; it lags about a day)
#
# The hourly rates come from packages/llm/pricing.toml, each with its dated AWS price-list source:
# the GPU host's on-demand rate, and the rest of the stack running (load balancer, Fargate,
# RDS, public IPs), which is an upper bound with all three services up.
set -uo pipefail

region=${AWS_REGION:-af-south-1}
spend=no
[ "${1:-}" = --spend ] && spend=yes

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pricing="$root/packages/llm/pricing.toml"
rate() {  # rate <section header line> -> the usd_per_hour under it
    awk -v h="$1" '$0 == h {found = 1; next} found && /^usd_per_hour/ {print $3; exit} /^\[/ {found = 0}' "$pricing"
}
gpu_rate="$(awk '/^name = "g4dn.xlarge"/ {found = 1; next} found && /^usd_per_hour/ {print $3; exit}' "$pricing")"
stack_rate="$(rate '[stack]')"
zar="$(awk '/^zar_per_usd/ {print $3; exit}' "$pricing")"

infra_dir="$root/infra"
read -r ssm <<<"$(PYTHONPATH="$infra_dir" python3 -c 'from mothusi_infra.stage import PRODUCTION as s; print(s.ssm("").rstrip("/"))')"
param() { aws --region "$region" ssm get-parameter --name "$ssm/$1" --query Parameter.Value --output text 2>/dev/null; }

host="$(param model-host/instance-id)"
cluster="$(param cluster-name)"
endpoint="$(param db/endpoint)"
db="${endpoint%%.*}"

gpu_state="$(aws --region "$region" ec2 describe-instances --instance-ids "$host" \
    --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null)"
db_state="$(aws --region "$region" rds describe-db-instances --db-instance-identifier "$db" \
    --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null)"
tasks="$(aws --region "$region" ecs list-tasks --cluster "$cluster" --desired-status RUNNING \
    --query 'length(taskArns)' --output text 2>/dev/null)"

echo "Running now ($(date '+%H:%M SAST')):"
printf '  GPU host %-22s %s\n' "$host" "${gpu_state:-?}"
printf '  database %-22s %s\n' "$db" "${db_state:-?}"
printf '  ECS tasks in %-18s %s running\n' "$cluster" "${tasks:-?}"

burn="$(python3 - "$gpu_state" "$db_state" "${tasks:-0}" "$gpu_rate" "$stack_rate" "$zar" <<'PY'
import sys
gpu, db, tasks, gpu_rate, stack_rate, zar = sys.argv[1], sys.argv[2], int(sys.argv[3] or 0), *map(float, sys.argv[4:])
hourly = (gpu_rate if gpu == "running" else 0) + (stack_rate if db == "available" or tasks > 0 else 0)
print(f"{hourly:.3f} {hourly * 24:.2f} {hourly * zar:.2f} {hourly * 24 * zar:.0f}")
PY
)"
read -r per_hour per_day zar_hour zar_day <<<"$burn"
echo "Burn: \$$per_hour/h (R$zar_hour), \$$per_day/day (R$zar_day)"
echo "  GPU \$$gpu_rate/h while running; the rest of the stack up to \$$stack_rate/h while the database or services run"

[ "$spend" = yes ] || exit 0

month="$(date +%Y-%m-01)"
tomorrow="$(date -d tomorrow +%F)"
echo
echo "This month ($month to today, Cost Explorer, about a day behind):"
aws ce get-cost-and-usage --region us-east-1 --time-period "Start=$month,End=$tomorrow" \
    --granularity MONTHLY --metrics UnblendedCost \
    --filter '{"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}}' \
    --group-by Type=DIMENSION,Key=SERVICE --output json 2>/dev/null | python3 -c '
import json, sys
groups = json.load(sys.stdin)["ResultsByTime"][0]["Groups"]
rows = sorted(((float(g["Metrics"]["UnblendedCost"]["Amount"]), g["Keys"][0]) for g in groups), reverse=True)
print(f"  usage   ${sum(a for a, _ in rows):.2f}")
for amount, name in rows:
    if amount >= 0.01:
        print(f"    {amount:7.2f}  {name}")
'
aws ce get-cost-and-usage --region us-east-1 --time-period "Start=$month,End=$tomorrow" \
    --granularity MONTHLY --metrics UnblendedCost \
    --filter '{"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit"]}}' \
    --query 'ResultsByTime[0].Total.UnblendedCost.Amount' --output text 2>/dev/null \
    | python3 -c 'import sys; print(f"  credits ${-float(sys.stdin.read() or 0):.2f} applied")'
