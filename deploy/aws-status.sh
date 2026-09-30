#!/bin/bash
# Checks which steps of docs/aws-deployment.md are done for one environment,
# and rebuilds ~/beldium-<env>.vars from what actually exists in AWS.
# Read-only: it only runs describe/list/get calls and never changes AWS.
#
#   bash aws-status.sh            # staging
#   bash aws-status.sh production
#   source ~/beldium-staging.vars # afterwards, to load the values
set -u
ENV="${1:-staging}"
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1 AWS_PAGER=""
VARS=~/beldium-$ENV.vars
NEXT=""

# Secrets can't be read back from AWS, so keep any already saved.
OLD_DB_PASSWORD=$(grep '^export DB_PASSWORD=' "$VARS" 2>/dev/null | tail -1 | cut -d'"' -f2)

cat > "$VARS" <<'EOF'
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export AWS_PAGER=""
export APP=beldium
save() { echo "export $1=\"${!1}\"" >> ~/beldium-$ENV.vars; echo "saved $1=${!1}"; }
EOF
echo "export ENV=$ENV" >> "$VARS"
keep() { echo "export $1=\"$2\"" >> "$VARS"; }
ok()   { echo "  ✅ $*"; }
todo() { echo "  ❌ $*"; [ -z "$NEXT" ] && NEXT="$*"; }
wait_() { echo "  ⏳ $*"; }
val()  { [ -n "$1" ] && [ "$1" != "None" ]; }

echo "Beldium $ENV, region $AWS_REGION"

echo "Step 0. Login"
ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null)
if ! val "$ACCOUNT"; then echo "  ❌ Not logged in to AWS. Open CloudShell from the AWS console."; exit 1; fi
ok "account $ACCOUNT"; keep ACCOUNT "$ACCOUNT"

echo "Step 2. GitHub OIDC provider"
val "$(aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[?contains(Arn,'token.actions.githubusercontent.com')].Arn" --output text)" \
  && ok "exists" || todo "Step 2: create the GitHub OIDC provider"

echo "Step 3. ECR repository"
aws ecr describe-repositories --repository-names beldium-backend >/dev/null 2>&1 \
  && ok "beldium-backend" || todo "Step 3: create the ECR repository"

echo "Step 4. Network"
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=tag:Name,Values=beldium-$ENV-vpc" --query 'Vpcs[0].VpcId' --output text 2>/dev/null)
if val "$VPC_ID"; then
  ok "VPC $VPC_ID"; keep VPC_ID "$VPC_ID"
  for kind in public private; do
    ids=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=*$kind*" --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
    count=$(echo "$ids" | tr ',' '\n' | grep -c subnet-)
    name=$(echo "${kind}_SUBNETS" | tr '[:lower:]' '[:upper:]')
    if [ "$count" -eq 2 ]; then ok "$kind subnets $ids"; keep "$name" "$ids"; else todo "Step 4: expected 2 $kind subnets, found $count"; fi
  done
  for pair in tasks:TASKS_SG db:DB_SG cache:CACHE_SG; do
    sg=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=beldium-$ENV-${pair%%:*}" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null)
    if val "$sg"; then ok "security group beldium-$ENV-${pair%%:*} $sg"; keep "${pair##*:}" "$sg"; printf -v "${pair##*:}" '%s' "$sg"
    else todo "Step 4: create security group beldium-$ENV-${pair%%:*}"; fi
  done
  for rule in DB_SG:5432 CACHE_SG:6379; do
    var="${rule%%:*}"; port="${rule##*:}"; sg="${!var:-}"
    val "$sg" || continue
    n=$(aws ec2 describe-security-groups --group-ids "$sg" --query "SecurityGroups[0].IpPermissions[?FromPort==\`$port\`] | length(@)" --output text)
    [ "$n" -ge 1 ] 2>/dev/null && ok "port $port open to the tasks group" || todo "Step 4: allow port $port on $var from TASKS_SG"
  done
else
  todo "Step 4: create the VPC (console wizard, name beldium-$ENV, region N. Virginia)"
fi

echo "Step 5. Database"
aws rds describe-db-subnet-groups --db-subnet-group-name "beldium-$ENV" >/dev/null 2>&1 \
  && ok "DB subnet group" || todo "Step 5b: create the DB subnet group"
status=$(aws rds describe-db-instances --db-instance-identifier "beldium-$ENV" --query 'DBInstances[0].DBInstanceStatus' --output text 2>/dev/null)
if val "$status"; then
  keep PG_VERSION "$(aws rds describe-db-instances --db-instance-identifier "beldium-$ENV" --query 'DBInstances[0].EngineVersion' --output text)"
  if [ "$status" = "available" ]; then
    DB_HOST=$(aws rds describe-db-instances --db-instance-identifier "beldium-$ENV" --query 'DBInstances[0].Endpoint.Address' --output text)
    ok "database available at $DB_HOST"; keep DB_HOST "$DB_HOST"
  else wait_ "database is '$status' (creating takes 10 to 15 minutes; run this script again later)"; fi
  if [ -n "$OLD_DB_PASSWORD" ]; then keep DB_PASSWORD "$OLD_DB_PASSWORD"; ok "database password is saved"
  else todo "Step 5: the database exists but its password was not saved. Reset it (see guide: 'Lost the database password')"; fi
else
  [ -n "$OLD_DB_PASSWORD" ] && keep DB_PASSWORD "$OLD_DB_PASSWORD"
  todo "Step 5c: create the database"
fi

echo "Step 6. Cache (Valkey)"
aws elasticache describe-cache-subnet-groups --cache-subnet-group-name "beldium-$ENV" >/dev/null 2>&1 \
  && ok "cache subnet group" || todo "Step 6: create the cache subnet group"
cstatus=$(aws elasticache describe-replication-groups --replication-group-id "beldium-$ENV" --query 'ReplicationGroups[0].Status' --output text 2>/dev/null)
if val "$cstatus"; then
  if [ "$cstatus" = "available" ]; then
    CACHE_HOST=$(aws elasticache describe-replication-groups --replication-group-id "beldium-$ENV" --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address' --output text)
    ok "cache available at $CACHE_HOST"; keep CACHE_HOST "$CACHE_HOST"
  else wait_ "cache is '$cstatus' (about 10 minutes; run this script again later)"; fi
else todo "Step 6: create the cache"; fi

echo "Step 7. Upload bucket"
BUCKET=""
for name in "beldium-$ENV-uploads" "beldium-$ENV-uploads-$ACCOUNT"; do
  aws s3api head-bucket --bucket "$name" --expected-bucket-owner "$ACCOUNT" >/dev/null 2>&1 && { BUCKET=$name; break; }
done
if [ -n "$BUCKET" ]; then
  ok "bucket $BUCKET"; keep BUCKET "$BUCKET"
  [ "$(aws s3api get-bucket-versioning --bucket "$BUCKET" --query Status --output text)" = "Enabled" ] \
    && ok "versioning on" || todo "Step 7: turn on versioning for $BUCKET"
else todo "Step 7: create the upload bucket"; fi

echo "Step 8. Log group"
[ "$(aws logs describe-log-groups --log-group-name-prefix "/ecs/beldium-$ENV" --query "length(logGroups[?logGroupName=='/ecs/beldium-$ENV'])" --output text)" = "1" ] \
  && ok "/ecs/beldium-$ENV" || todo "Step 8: create the log group"

echo "Step 9. Settings in SSM"
# Results come back 10 per page, so count names across all pages.
names=$(aws ssm get-parameters-by-path --path "/beldium/$ENV/" --query 'Parameters[].Name' --output text 2>/dev/null | tr '\t' '\n' | sed 's|.*/||' | grep -v '^$')
n=$(echo "$names" | grep -c . )
missing=""
for want in SECRET_KEY DATABASE_URL REDIS_URL ALLOWED_HOSTS ECS_SERVICE_HOST CSRF_TRUSTED_ORIGINS CORS_ALLOWED_ORIGINS \
            COMPLIANCE_PORTAL_ORIGINS MINER_PORTAL_ORIGINS FRONTEND_URL DEFAULT_FROM_EMAIL EMAIL_HOST_PASSWORD \
            AWS_STORAGE_BUCKET_NAME NUM_PROXIES; do
  echo "$names" | grep -qx "$want" || missing="$missing $want"
done
[ -z "$missing" ] && ok "all 14 parameters" || todo "Step 9: missing settings ($n of 14 stored):$missing"

echo "Step 10. IAM roles"
for role in ecs-execution ecs-task ecs-infrastructure github-deploy; do
  aws iam get-role --role-name "beldium-$ENV-$role" >/dev/null 2>&1 && ok "beldium-$ENV-$role" || todo "Step 10: create role beldium-$ENV-$role"
done

echo "Step 11. Cluster"
[ "$(aws ecs describe-clusters --clusters "beldium-$ENV" --query "length(clusters[?status=='ACTIVE'])" --output text 2>/dev/null)" = "1" ] \
  && ok "beldium-$ENV" || todo "Step 11: create the cluster"

echo "Steps 12-14. Image and services"
aws ecs describe-task-definition --task-definition "beldium-$ENV-web" >/dev/null 2>&1 \
  && ok "task definitions registered" || todo "Step 12: push the first image and register task definitions (on your Mac)"
services=$(aws ecs list-services --cluster "beldium-$ENV" --query 'serviceArns' --output text 2>/dev/null)
for svc in web worker beat; do
  echo "$services" | grep -q "beldium-$ENV-$svc" && ok "service beldium-$ENV-$svc" || todo "Step $([ $svc = web ] && echo 13 || echo 14): create service beldium-$ENV-$svc"
done
WEB_SERVICE_ARN=$(echo "$services" | tr '\t' '\n' | grep "beldium-$ENV-web" | head -1)
val "$WEB_SERVICE_ARN" && keep WEB_SERVICE_ARN "$WEB_SERVICE_ARN"

echo
if [ -n "$NEXT" ]; then echo "NEXT: $NEXT"; else echo "All checked steps are done."; fi
echo "Now run:  source $VARS"
