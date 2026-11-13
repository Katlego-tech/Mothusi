#!/usr/bin/env bash
# What a failed release leaves behind, gathered into the job log and the run summary, so a failure
# is read on GitHub instead of by polling AWS. Every section runs even when an earlier one fails.
#
# Env: STACK (the service stack), SSM_PREFIX (the environment's SSM prefix), SERVICES (comma list),
# LOG_PREFIX (the services' log groups, /<label>). AWS credentials only if sign-in got that far.
set -uo pipefail

out="$(mktemp)"
open_section() { printf '\n### %s\n\n```\n' "$1" >> "$out"; }
close_section() { printf '```\n' >> "$out"; }
publish() {
  cat "$out"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    { printf '## Release diagnostics\n'; head -c 900000 "$out"; } >> "$GITHUB_STEP_SUMMARY"
  fi
}

# 1. Who GitHub says this job is. AWS's trust policy must match `sub` and `aud` exactly; a
#    mismatch is an AccessDenied on AssumeRoleWithWebIdentity with no other detail.
open_section "GitHub OIDC claims (the release role's trust policy must match sub and aud)"
if [ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
  token="$(curl -sSf -H "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
    "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=sts.amazonaws.com" | jq -r .value)"
  payload="$(cut -d. -f2 <<<"$token" | tr '_-' '/+')"
  while [ $(( ${#payload} % 4 )) -ne 0 ]; do payload+="="; done
  # The claims only, never the token: it is a credential until it expires.
  base64 -d <<<"$payload" 2>/dev/null \
    | jq '{sub, aud, environment, ref, repository, job_workflow_ref}' >> "$out" \
    || echo "(could not decode the token's claims)" >> "$out"
else
  echo "(no OIDC token in this job: id-token: write is missing)" >> "$out"
fi
close_section

if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "No AWS credentials in this job: the release failed at or before AWS sign-in." >> "$out"
  publish
  exit 0
fi

# 2. CloudFormation: why the service stack's update failed or rolled back.
open_section "CloudFormation: failed and rolled-back events on $STACK"
aws cloudformation describe-stack-events --stack-name "$STACK" --max-items 80 \
  --query "StackEvents[?contains(ResourceStatus,'FAILED') || contains(ResourceStatus,'ROLLBACK')].[Timestamp,LogicalResourceId,ResourceStatus,ResourceStatusReason]" \
  --output text >> "$out" 2>&1 || echo "(could not read the stack's events)" >> "$out"
close_section

# 3. ECS: what each service says, and why its tasks stopped.
cluster="$(aws ssm get-parameter --name "$SSM_PREFIX/cluster-name" --query Parameter.Value --output text 2>/dev/null)"
IFS=',' read -ra names <<<"$SERVICES"
open_section "ECS services in ${cluster:-?}: desired, running and the latest events"
aws ecs describe-services --cluster "$cluster" --services "${names[@]}" \
  --query 'services[].{service:serviceName,desired:desiredCount,running:runningCount,events:events[:8].message}' \
  --output yaml >> "$out" 2>&1 || echo "(could not describe the services)" >> "$out"
close_section
open_section "ECS: the last stopped tasks and why they stopped"
for name in "${names[@]}"; do
  read -ra arns <<<"$(aws ecs list-tasks --cluster "$cluster" --service-name "$name" \
    --desired-status STOPPED --query 'taskArns[:5]' --output text 2>/dev/null)"
  if [ "${#arns[@]}" -gt 0 ] && [ "${arns[0]}" != "None" ]; then
    aws ecs describe-tasks --cluster "$cluster" --tasks "${arns[@]}" \
      --query 'tasks[].{task:taskArn,stopped:stoppedAt,stopCode:stopCode,reason:stoppedReason,containers:containers[].{name:name,exit:exitCode,reason:reason}}' \
      --output yaml >> "$out" 2>&1 || echo "($name: could not describe its stopped tasks)" >> "$out"
  else
    echo "$name: no stopped tasks" >> "$out"
  fi
done
close_section

# 4. The services' own logs: the traceback is usually here.
for name in "${names[@]}"; do
  open_section "$LOG_PREFIX/$name: the last 30 minutes (last 100 lines)"
  aws logs tail "$LOG_PREFIX/$name" --since 30m --format short 2>&1 | tail -n 100 >> "$out"
  close_section
done

publish
