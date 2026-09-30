#!/bin/bash
# Opens a private tunnel from this computer to an environment's RDS database,
# through the running worker task (AWS Session Manager port forwarding).
# Nothing is exposed to the internet; the tunnel lasts until you press Ctrl+C.
#
#   bash deploy/db-tunnel.sh staging            # localhost:15432 -> staging RDS
#   bash deploy/db-tunnel.sh production 15433
#
# Needs: AWS CLI signed in (aws login), the Session Manager plugin
# (brew install --cask session-manager-plugin), and ECS Exec turned on for the
# worker service (docs/aws-deployment.md, "Loading data through a tunnel").
set -euo pipefail
ENV="${1:-staging}"
PORT="${2:-15432}"
export AWS_REGION=us-east-1 AWS_PAGER=""
CLUSTER="beldium-$ENV"

command -v session-manager-plugin >/dev/null || {
  echo "Session Manager plugin missing. Install it: brew install --cask session-manager-plugin"; exit 1; }

TASK=$(aws ecs list-tasks --cluster "$CLUSTER" --service-name "beldium-$ENV-worker" --desired-status RUNNING \
  --query 'taskArns[0]' --output text)
if [ -z "$TASK" ] || [ "$TASK" = "None" ]; then
  echo "No running worker task in $CLUSTER."; exit 1
fi

read -r RUNTIME EXEC_ON < <(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$TASK" \
  --query 'tasks[0].[containers[?name==`Main`].runtimeId | [0], enableExecuteCommand]' --output text)
if [ "$EXEC_ON" != "True" ]; then
  echo "ECS Exec is off for this task. Turn it on first (guide: 'Loading data through a tunnel'), then run this again."; exit 1
fi

DB_HOST=$(aws rds describe-db-instances --db-instance-identifier "beldium-$ENV" \
  --query 'DBInstances[0].Endpoint.Address' --output text)

echo "Tunnel: localhost:$PORT -> $DB_HOST:5432 (via ${TASK##*/})"
echo "Leave this window open. In another window:  export TARGET=\$(bash deploy/db-url.sh $ENV $PORT)"
echo "Press Ctrl+C here to close the tunnel."
exec aws ssm start-session \
  --target "ecs:${CLUSTER}_${TASK##*/}_${RUNTIME}" \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters "{\"host\":[\"$DB_HOST\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"$PORT\"]}"
