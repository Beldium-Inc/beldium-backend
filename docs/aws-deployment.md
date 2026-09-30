# AWS setup guide

Every step to stand up this backend on AWS, in order. Region: `us-east-1`. Do the whole guide for **staging** first, check it works, then repeat it for **production**. Both environments are built from the same files. They differ only in the values stored in SSM Parameter Store and in the resource IDs you save into GitHub.

Steps marked **(once)** are for the whole AWS account. Everything else is per environment.

Work through it from a terminal, one step at a time. Each step says what to save for later. Keep those values in a notes file **outside this repo**.

---

## What you are building

```
                    Internet
                       |
         api.beldium.com (CNAME at Truehost)
                       |
     Application Load Balancer (Express Mode, HTTPS)
                       |
  public subnets:  web tasks    worker task    beat task    (Fargate, same image)
                       |            |              |
  private subnets:  RDS PostgreSQL 18      ElastiCache Valkey
                       
  S3 bucket (uploads)   SSM Parameter Store (settings)   ECR (images)   CloudWatch Logs
```

| Role | Runs as | Command | Count |
|---|---|---|---|
| `web` | ECS Express Mode service | gunicorn | 1 or more (auto scaling) |
| `worker` | ECS service | Celery worker | 1 or more |
| `beat` | ECS service | Celery beat (hourly logistics expiry check) | **exactly 1** |
| `migrate` | one-off task, every deploy | `manage.py migrate` | 0 |

**Network choice:** tasks run in **public subnets with public IPs**, and security groups block all inbound traffic except from the load balancer. This avoids a NAT gateway, which is billed per hour plus data. The database and cache sit in private subnets and are reachable only from the tasks. Express Mode needs public subnets anyway to create an internet-facing load balancer: given private subnets, it creates an *internal* one that the internet cannot reach. If you want tasks fully private later, add a NAT gateway, move the worker and beat to private subnets, and set `TASK_ASSIGN_PUBLIC_IP=DISABLED` in GitHub.

---

## Step 0. Before you start

You need:
- AWS CLI v2 (`aws --version`), Docker Desktop running, and `psql` (Postgres 18 client).
- An admin login to the AWS account that is **not** the root user. Use IAM Identity Center or an IAM user with MFA, and log in with `aws configure sso` or `aws configure`. Check it with:

```bash
aws sts get-caller-identity
```

- Root user: MFA on, no access keys.

Set these in your shell at the start of every session. Change `ENV` to `production` on the second pass:

```bash
export AWS_REGION=us-east-1
export ENV=staging                      # staging | production
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export APP=beldium
echo "$ACCOUNT $ENV"
```

---

### Working in AWS CloudShell

Steps 1 to 11 and 13 to 19 can all be run in CloudShell (the `>_` icon in the console's top bar). Three things to know:

1. **Set the console region to US East (N. Virginia) first** (top-right menu). CloudShell and the console wizards use that region. A VPC made while the console shows Stockholm ends up in Stockholm.
2. **CloudShell forgets shell variables** when the session ends (after about 20 to 30 minutes idle). Files in your home folder are kept. Keep every value in a file and reload it each session:

```bash
cat > ~/beldium-staging.vars <<'EOF'
export AWS_REGION=us-east-1
export AWS_DEFAULT_REGION=us-east-1
export AWS_PAGER=""
export ENV=staging
export APP=beldium
save() { echo "export $1=\"${!1}\"" >> ~/beldium-$ENV.vars; echo "saved $1=${!1}"; }
EOF
source ~/beldium-staging.vars
ACCOUNT=$(aws sts get-caller-identity --query Account --output text); save ACCOUNT
```

   Start every new session with `source ~/beldium-staging.vars`. After creating something, `save NAME` stores its ID. For production, make `~/beldium-production.vars` the same way with `ENV=production`.
3. **Step 12 needs Docker and this repository**, so run it on your own machine, not in CloudShell.
4. **Lost track of where you are?** Upload `deploy/aws-status.sh` (CloudShell **Actions → Upload file**), then run `bash aws-status.sh` followed by `source ~/beldium-staging.vars`. It only reads from AWS. It marks each step done, in progress or missing, rebuilds the variables file from what exists, and names the next step.

## Step 1. Billing alarm (once)

In the console: **Billing and Cost Management → Budgets → Create budget → Monthly cost budget**. Set an amount and your email. This is the cheapest insurance against a forgotten resource.

---

## Step 2. GitHub OIDC provider (once)

This lets GitHub Actions get short-lived AWS credentials, with no stored keys.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

If it says the provider already exists, that's fine.

---

## Step 3. ECR repository (once)

One image repository shared by both environments. Images are tagged with the git commit, so tags never change.

```bash
aws ecr create-repository --repository-name beldium-backend \
  --image-scanning-configuration scanOnPush=true \
  --image-tag-mutability IMMUTABLE

aws ecr put-lifecycle-policy --repository-name beldium-backend --lifecycle-policy-text '{
  "rules": [{"rulePriority": 1, "description": "keep last 100 images",
    "selection": {"tagStatus": "any", "countType": "imageCountMoreThan", "countNumber": 100},
    "action": {"type": "expire"}}]}'
```

---

## Step 4. Network

Console: **VPC → Create VPC → VPC and more**. Use a different CIDR per environment so they can be peered later if needed.

| Field | Staging | Production |
|---|---|---|
| Name tag auto-generation | `beldium-staging` | `beldium-production` |
| IPv4 CIDR | `10.10.0.0/16` | `10.20.0.0/16` |
| Availability Zones | 2 | 2 |
| Public subnets | 2 | 2 |
| Private subnets | 2 | 2 |
| NAT gateways | **None** | **None** |
| VPC endpoints | **S3 Gateway** | **S3 Gateway** |
| DNS hostnames / resolution | both on | both on |

Then save the IDs:

```bash
export VPC_ID=$(aws ec2 describe-vpcs --filters "Name=tag:Name,Values=beldium-$ENV-vpc" --query 'Vpcs[0].VpcId' --output text)
aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'Subnets[].[SubnetId,AvailabilityZone,Tags[?Key==`Name`]|[0].Value]' --output table
```

The table should show four subnets: two with `public` in the name and two with `private`. Pick them up by name:

```bash
save VPC_ID
PUBLIC_SUBNETS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=*public*" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ','); save PUBLIC_SUBNETS
PRIVATE_SUBNETS=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" "Name=tag:Name,Values=*private*" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ','); save PRIVATE_SUBNETS
```

Each should show two IDs separated by a comma.

### Security groups

```bash
TASKS_SG=$(aws ec2 create-security-group --vpc-id "$VPC_ID" \
  --group-name beldium-$ENV-tasks --description "Beldium $ENV ECS tasks" --query GroupId --output text)
DB_SG=$(aws ec2 create-security-group --vpc-id "$VPC_ID" \
  --group-name beldium-$ENV-db --description "Beldium $ENV RDS" --query GroupId --output text)
CACHE_SG=$(aws ec2 create-security-group --vpc-id "$VPC_ID" \
  --group-name beldium-$ENV-cache --description "Beldium $ENV Valkey" --query GroupId --output text)

aws ec2 authorize-security-group-ingress --group-id "$DB_SG" --protocol tcp --port 5432 --source-group "$TASKS_SG"
aws ec2 authorize-security-group-ingress --group-id "$CACHE_SG" --protocol tcp --port 6379 --source-group "$TASKS_SG"
save TASKS_SG; save DB_SG; save CACHE_SG
```

`beldium-<env>-tasks` gets no inbound rules. Express Mode adds its own load balancer security group, which lets the load balancer reach the web container (see step 13).

---

## Step 5. Database (RDS PostgreSQL 18)

Check which 18.x versions RDS offers:

```bash
PG_VERSION=$(aws rds describe-db-engine-versions --engine postgres \
  --query "DBEngineVersions[?starts_with(EngineVersion,'18.')].EngineVersion" --output text | tr '\t' '\n' | sort -V | tail -1)
save PG_VERSION     # should print 18.something; if empty, RDS has no 18.x in this region
```

Create a password that needs no URL escaping, and keep it in your password manager:

```bash
DB_PASSWORD=$(openssl rand -hex 24); save DB_PASSWORD    # also copy it into your password manager
```

```bash
aws rds create-db-subnet-group --db-subnet-group-name beldium-$ENV \
  --db-subnet-group-description "Beldium $ENV" \
  --subnet-ids $(echo $PRIVATE_SUBNETS | tr ',' ' ')

aws rds create-db-instance \
  --db-instance-identifier beldium-$ENV \
  --engine postgres --engine-version "$PG_VERSION" \
  --db-instance-class db.t4g.small \
  --allocated-storage 20 --max-allocated-storage 100 --storage-type gp3 --storage-encrypted \
  --master-username beldium --master-user-password "$DB_PASSWORD" \
  --db-name beldium \
  --db-subnet-group-name beldium-$ENV --vpc-security-group-ids "$DB_SG" \
  --no-publicly-accessible \
  --backup-retention-period 7 --copy-tags-to-snapshot

aws rds wait db-instance-available --db-instance-identifier beldium-$ENV   # about 10 minutes
DB_HOST=$(aws rds describe-db-instances --db-instance-identifier beldium-$ENV \
  --query 'DBInstances[0].Endpoint.Address' --output text); save DB_HOST
```

**Lost the database password?** If the instance exists but `DB_PASSWORD` was never saved, set a new one. This changes only the password, not the data:

```bash
DB_PASSWORD=$(openssl rand -hex 24); save DB_PASSWORD
aws rds modify-db-instance --db-instance-identifier beldium-$ENV --master-user-password "$DB_PASSWORD" --apply-immediately
```

The instance must be `available` first. The new password takes effect within a minute or two.

**Production differences:** a larger class (for example `db.t4g.medium` or `db.m7g.large`, based on Render's current usage), `--multi-az`, `--backup-retention-period 14`, `--deletion-protection`.

RDS for Postgres 15 and later requires SSL by default. psycopg uses SSL automatically, so `DATABASE_URL` needs nothing extra.

---

## Step 6. Cache and Celery broker (ElastiCache Valkey)

**Do not use ElastiCache Serverless.** It runs in cluster mode, which Celery's Redis transport does not support. Use a node-based replication group with cluster mode off.

```bash
VALKEY_VERSION=$(aws elasticache describe-cache-engine-versions --engine valkey \
  --query "CacheEngineVersions[?starts_with(EngineVersion,'8.')].EngineVersion" --output text | tr '\t' '\n' | sort -V | tail -1)
save VALKEY_VERSION

aws elasticache create-cache-subnet-group --cache-subnet-group-name beldium-$ENV \
  --cache-subnet-group-description "Beldium $ENV" \
  --subnet-ids $(echo $PRIVATE_SUBNETS | tr ',' ' ')

aws elasticache create-replication-group \
  --replication-group-id beldium-$ENV \
  --replication-group-description "Beldium $ENV cache and Celery broker" \
  --engine valkey --engine-version "$VALKEY_VERSION" \
  --cache-node-type cache.t4g.micro \
  --num-cache-clusters 1 \
  --cache-subnet-group-name beldium-$ENV --security-group-ids "$CACHE_SG" \
  --transit-encryption-enabled --at-rest-encryption-enabled

aws elasticache wait replication-group-available --replication-group-id beldium-$ENV
CACHE_HOST=$(aws elasticache describe-replication-groups --replication-group-id beldium-$ENV \
  --query 'ReplicationGroups[0].NodeGroups[0].PrimaryEndpoint.Address' --output text); save CACHE_HOST
```

**Production differences:** `--cache-node-type cache.t4g.small`, `--num-cache-clusters 2 --automatic-failover-enabled --multi-az-enabled`.

Because transit encryption is on, the URL starts with `rediss://` and ends with `?ssl_cert_reqs=required` (step 9).

---

## Step 7. Upload bucket (S3)

Bucket names are global. If `beldium-<env>-uploads` is taken, add a suffix and use that name everywhere below.

```bash
BUCKET=beldium-$ENV-uploads; save BUCKET
aws s3api create-bucket --bucket "$BUCKET"
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled
aws s3api put-bucket-ownership-controls --bucket "$BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
```

Default encryption (SSE-S3) is on for new buckets automatically. The bucket needs no CORS rule and no bucket policy: files are streamed through the API (`common/files.py`).

---

## Step 8. Log group

```bash
aws logs create-log-group --log-group-name /ecs/beldium-$ENV
aws logs put-retention-policy --log-group-name /ecs/beldium-$ENV --retention-in-days 30   # production: 90
```

---

## Step 9. Settings in SSM Parameter Store

Every setting the containers read comes from `/beldium/<env>/<NAME>`. The task will not start if any of these is missing, and the app refuses to start if a required one is empty (`core/settings.py`).

Create a file **outside the repo**, for example `~/beldium-$ENV.params`. One `NAME=value` per line, no quotes:

```
SECRET_KEY=<python3 -c "import secrets; print(secrets.token_urlsafe(64))">
DATABASE_URL=postgres://beldium:<DB_PASSWORD>@<DB_HOST>:5432/beldium
REDIS_URL=rediss://<CACHE_HOST>:6379/0?ssl_cert_reqs=required
ALLOWED_HOSTS=api.beldium.com
ECS_SERVICE_HOST=pending.invalid
CSRF_TRUSTED_ORIGINS=https://api.beldium.com
CORS_ALLOWED_ORIGINS=https://compliance.beldium.com,https://miners.beldium.com
COMPLIANCE_PORTAL_ORIGINS=https://compliance.beldium.com
MINER_PORTAL_ORIGINS=https://miners.beldium.com
FRONTEND_URL=https://compliance.beldium.com
DEFAULT_FROM_EMAIL=noreply@beldium.com
EMAIL_HOST_PASSWORD=<Resend API key>
AWS_STORAGE_BUCKET_NAME=beldium-staging-uploads
NUM_PROXIES=1
```

For staging, use staging hostnames (for example `api-staging.beldium.com` and staging frontend origins) if you have them. `ECS_SERVICE_HOST` starts as a placeholder. AWS assigns the real host when the service is created, in a random form like `be-<32 hex characters>.ecs.us-east-1.on.aws` (not the service name). Step 13 replaces the placeholder. Use a **different** `SECRET_KEY` for each environment.

Load it:

```bash
while IFS='=' read -r name value; do
  [ -z "$name" ] && continue
  aws ssm put-parameter --name "/beldium/$ENV/$name" --type SecureString --value "$value" --overwrite >/dev/null \
    && echo "set $name"
done < ~/beldium-$ENV.params
aws ssm get-parameters-by-path --path /beldium/$ENV/ --query 'Parameters[].Name' --output text | tr '\t' '\n' | wc -l   # expect 14
```

Store the file in your password manager, then delete it from disk.

---

## Step 10. IAM roles

Four roles per environment. Create the policy files in a scratch folder, not in the repo.

### 10a. Task execution role: pulls the image, writes logs, reads SSM

```bash
cat > /tmp/ecs-tasks-trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs-tasks.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
aws iam create-role --role-name beldium-$ENV-ecs-execution --assume-role-policy-document file:///tmp/ecs-tasks-trust.json
aws iam attach-role-policy --role-name beldium-$ENV-ecs-execution \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam put-role-policy --role-name beldium-$ENV-ecs-execution --policy-name read-ssm --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":\"ssm:GetParameters\",
  \"Resource\":\"arn:aws:ssm:${AWS_REGION}:${ACCOUNT}:parameter/beldium/$ENV/*\"}]}"
```

### 10b. Task role: what the app itself can do (S3 only)

```bash
aws iam create-role --role-name beldium-$ENV-ecs-task --assume-role-policy-document file:///tmp/ecs-tasks-trust.json
aws iam put-role-policy --role-name beldium-$ENV-ecs-task --policy-name uploads-bucket --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
   {\"Effect\":\"Allow\",\"Action\":[\"s3:GetObject\",\"s3:PutObject\",\"s3:DeleteObject\"],\"Resource\":\"arn:aws:s3:::$BUCKET/*\"},
   {\"Effect\":\"Allow\",\"Action\":\"s3:ListBucket\",\"Resource\":\"arn:aws:s3:::$BUCKET\"}]}"
```

### 10c. Express Mode infrastructure role: lets ECS manage the load balancer, certificate and scaling

```bash
aws iam create-role --role-name beldium-$ENV-ecs-infrastructure --assume-role-policy-document \
  '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ecs.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
aws iam attach-role-policy --role-name beldium-$ENV-ecs-infrastructure \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
aws iam put-role-policy --role-name beldium-$ENV-ecs-infrastructure --policy-name express-mode-gaps --policy-document "{\"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",\"Action\":[\"ecs:DescribeServices\",\"ecs:UpdateService\"],\"Resource\":\"arn:aws:ecs:${AWS_REGION}:${ACCOUNT}:service/beldium-$ENV/*\"},{\"Effect\":\"Allow\",\"Action\":[\"cloudwatch:DescribeAlarms\",\"ec2:DescribeAccountAttributes\"],\"Resource\":\"*\"},{\"Effect\":\"Allow\",\"Action\":[\"cloudwatch:PutMetricAlarm\",\"cloudwatch:DeleteAlarms\",\"cloudwatch:TagResource\"],\"Resource\":\"arn:aws:cloudwatch:${AWS_REGION}:${ACCOUNT}:alarm:*\"}]}"
```

The second policy is required. On staging (2026-09-30), the managed policy (version v6) lacked `ecs:DescribeServices`, `ecs:UpdateService` and `ec2:DescribeAccountAttributes`, and its tag conditions block `cloudwatch:DescribeAlarms` and `cloudwatch:PutMetricAlarm` (the rollback alarm). Without them, creating the service rolls back ("PROVISIONING lifecycle hook(s) failed. AccessDenied"), and nothing runs. Updates never retry the load balancer after a failed creation. If that happens, delete the service (`aws ecs delete-express-gateway-service --service-arn ...`), wait for it to become INACTIVE, fix the permissions, and create it again.

### 10d. GitHub deploy role: assumed by the workflow through OIDC

Set `GH_ENV` first: `GH_ENV=staging` for staging, `GH_ENV=aws-production` for production. The trust policy pins the role to one GitHub environment, so the staging role can never deploy production. The repository has GitHub's "immutable subject" OIDC setting on, so GitHub identifies it as `repo:Beldium-Inc@255878329/beldium-backend@1355855179:environment:<env>` (owner and repo IDs included). The policy accepts that form and the older name-only form. Check the current form with `gh api repos/Beldium-Inc/beldium-backend/actions/oidc/customization/sub`.

```bash
aws iam create-role --role-name beldium-$ENV-github-deploy --assume-role-policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[{\"Effect\":\"Allow\",
  \"Principal\":{\"Federated\":\"arn:aws:iam::${ACCOUNT}:oidc-provider/token.actions.githubusercontent.com\"},
  \"Action\":\"sts:AssumeRoleWithWebIdentity\",
  \"Condition\":{\"StringEquals\":{
    \"token.actions.githubusercontent.com:aud\":\"sts.amazonaws.com\",
    \"token.actions.githubusercontent.com:sub\":[
      \"repo:Beldium-Inc@255878329/beldium-backend@1355855179:environment:${GH_ENV}\",
      \"repo:Beldium-Inc/beldium-backend:environment:${GH_ENV}\"]}}}]}"

aws iam put-role-policy --role-name beldium-$ENV-github-deploy --policy-name deploy --policy-document "{
  \"Version\":\"2012-10-17\",\"Statement\":[
   {\"Effect\":\"Allow\",\"Action\":\"ecr:GetAuthorizationToken\",\"Resource\":\"*\"},
   {\"Effect\":\"Allow\",\"Action\":[\"ecr:BatchCheckLayerAvailability\",\"ecr:InitiateLayerUpload\",\"ecr:UploadLayerPart\",
     \"ecr:CompleteLayerUpload\",\"ecr:PutImage\",\"ecr:BatchGetImage\"],
     \"Resource\":\"arn:aws:ecr:${AWS_REGION}:${ACCOUNT}:repository/beldium-backend\"},
   {\"Effect\":\"Allow\",\"Action\":[\"ecs:RegisterTaskDefinition\",\"ecs:DescribeTaskDefinition\"],\"Resource\":\"*\"},
   {\"Effect\":\"Allow\",\"Action\":[\"ecs:RunTask\",\"ecs:DescribeTasks\",\"ecs:UpdateService\",\"ecs:DescribeServices\",
     \"ecs:UpdateExpressGatewayService\",\"ecs:DescribeExpressGatewayService\"],
     \"Resource\":\"*\",\"Condition\":{\"ArnEquals\":{\"ecs:cluster\":\"arn:aws:ecs:${AWS_REGION}:${ACCOUNT}:cluster/beldium-$ENV\"}}},
   {\"Effect\":\"Allow\",\"Action\":\"iam:PassRole\",
     \"Resource\":[\"arn:aws:iam::${ACCOUNT}:role/beldium-$ENV-ecs-execution\",\"arn:aws:iam::${ACCOUNT}:role/beldium-$ENV-ecs-task\"],
     \"Condition\":{\"StringEquals\":{\"iam:PassedToService\":\"ecs-tasks.amazonaws.com\"}}}]}"
```

Not yet verified against a live deploy: whether the Express Mode update or monitor call needs more read permissions (load balancer or CloudWatch describe calls). If the "Deploy web" step fails with `AccessDenied`, add the action named in the error to this policy. If an action can't be scoped to the cluster, move it to a statement without the condition.

Wait about a minute before step 13. New roles take time to become usable.

---

## Step 11. Cluster

```bash
aws ecs create-cluster --cluster-name beldium-$ENV --capacity-providers FARGATE \
  --default-capacity-provider-strategy capacityProvider=FARGATE,weight=1 \
  --settings name=containerInsights,value=enabled
```

---

## Step 12. First image, task definitions and database schema

**Easiest: let GitHub do it (no Docker on your machine).** Do the GitHub part of step 17 first (the `staging` environment and its variables; leave `WEB_SERVICE_ARN` out for now), then push this code to the `staging` branch. The workflow builds and pushes the image, registers the three task definitions, and runs `migrate`. It then skips the services because they don't exist yet, and finishes with a notice. Continue at step 13.

**Or by hand, with Docker:**

The services need a task definition before they can be created. After this, the GitHub workflow keeps them up to date.

```bash
cd /path/to/beldium-backend
aws ecr get-login-password | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com
export IMAGE=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/beldium-backend:bootstrap-$ENV-$(git rev-parse --short HEAD)
docker build --platform linux/amd64 -t "$IMAGE" .      # --platform matters on Apple Silicon
docker push "$IMAGE"

python3 deploy/render_task_definitions.py --env $ENV --image "$IMAGE" --account-id $ACCOUNT \
  --execution-role-arn arn:aws:iam::${ACCOUNT}:role/beldium-$ENV-ecs-execution \
  --task-role-arn arn:aws:iam::${ACCOUNT}:role/beldium-$ENV-ecs-task \
  --out-dir /tmp/beldium-ecs
for role in web worker beat; do
  aws ecs register-task-definition --cli-input-json file:///tmp/beldium-ecs/$role.json \
    --query taskDefinition.taskDefinitionArn --output text
done
```

Run migrations on the empty database:

```bash
NET="awsvpcConfiguration={subnets=[$PUBLIC_SUBNETS],securityGroups=[$TASKS_SG],assignPublicIp=ENABLED}"
TASK=$(aws ecs run-task --cluster beldium-$ENV --launch-type FARGATE --task-definition beldium-$ENV-web \
  --network-configuration "$NET" \
  --overrides '{"containerOverrides":[{"name":"Main","command":["migrate"]}]}' \
  --query 'tasks[0].taskArn' --output text)
aws ecs wait tasks-stopped --cluster beldium-$ENV --tasks "$TASK"
aws ecs describe-tasks --cluster beldium-$ENV --tasks "$TASK" \
  --query 'tasks[0].[containers[0].exitCode,stoppedReason]' --output text     # expect 0
```

If the exit code isn't 0, the logs are in CloudWatch, group `/ecs/beldium-<env>`, stream `web/Main/<task id>`:

```bash
aws logs tail /ecs/beldium-$ENV --since 15m
```

**For production, skip `migrate` here.** Production's database is restored from Render at cutover (`migration/README.md`), and `migrate` runs after the restore.

---

## Step 13. Web service (Express Mode)

```bash
aws ecs create-express-gateway-service \
  --cluster beldium-$ENV \
  --service-name beldium-$ENV-web \
  --infrastructure-role-arn arn:aws:iam::${ACCOUNT}:role/beldium-$ENV-ecs-infrastructure \
  --task-definition-arn $(aws ecs describe-task-definition --task-definition beldium-$ENV-web --query taskDefinition.taskDefinitionArn --output text) \
  --health-check-path /health/ \
  --network-configuration "{\"subnets\":[\"$(echo "$PUBLIC_SUBNETS" | sed 's/,/","/g')\"],\"securityGroups\":[\"$TASKS_SG\"]}" \
  --scaling-target '{"minTaskCount":1,"maxTaskCount":4}' \
  --monitor-resources
```

This takes several minutes. It creates an internet-facing load balancer with HTTPS, a certificate for the AWS URL, auto scaling, and canary deployments with automatic rollback on 5xx errors. Production: `minTaskCount` 2.

Save the service ARN and URL:

```bash
export WEB_SERVICE_ARN=$(aws ecs list-services --cluster beldium-$ENV --query "serviceArns[?contains(@,'beldium-$ENV-web')]|[0]" --output text)
aws ecs describe-express-gateway-service --service-arn "$WEB_SERVICE_ARN" \
  --query 'service.activeConfigurations[0].ingressPaths' --output table
```

If the endpoint host differs from the `ECS_SERVICE_HOST` you set in step 9, fix the parameter:

```bash
aws ssm put-parameter --name /beldium/$ENV/ECS_SERVICE_HOST --type SecureString --overwrite --value "<host from above, no https://>"
aws ecs update-service --cluster beldium-$ENV --service beldium-$ENV-web --force-new-deployment
```

Check it:

```bash
curl -s https://<host>/health/
# {"status": "ok", "database": "ok", "redis": "ok"}
```

**If the target stays unhealthy:** open the service's target group in the EC2 console (Target groups → Targets tab) and read the reason.
- A timeout means the load balancer can't reach port 8000. Add an inbound rule to `beldium-<env>-tasks`: TCP 8000 from the load balancer's security group (the one Express Mode created, tagged `AmazonECSManaged`).
- A 503 means the database or Redis is unreachable. Check `aws logs tail /ecs/beldium-$ENV --since 10m`.

---

## Step 14. Worker and beat services

```bash
NET="awsvpcConfiguration={subnets=[$PUBLIC_SUBNETS],securityGroups=[$TASKS_SG],assignPublicIp=ENABLED}"

aws ecs create-service --cluster beldium-$ENV --service-name beldium-$ENV-worker \
  --task-definition beldium-$ENV-worker --desired-count 1 --launch-type FARGATE \
  --network-configuration "$NET"

aws ecs create-service --cluster beldium-$ENV --service-name beldium-$ENV-beat \
  --task-definition beldium-$ENV-beat --desired-count 1 --launch-type FARGATE \
  --deployment-configuration "minimumHealthyPercent=0,maximumPercent=100" \
  --network-configuration "$NET"

aws ecs wait services-stable --cluster beldium-$ENV --services beldium-$ENV-worker beldium-$ENV-beat
aws logs tail /ecs/beldium-$ENV --since 5m | grep -E "celery@|beat: Starting"
```

**Never scale beat above 1.** Two beat processes run every scheduled job twice.

---

## Step 15. Admin login

Run `bootstrap_admin` once as a one-off task. The values are passed only to this task, never stored in SSM. `$NET` is the value set in step 14:

```bash
aws ecs run-task --cluster beldium-$ENV --launch-type FARGATE --task-definition beldium-$ENV-web \
  --network-configuration "$NET" \
  --overrides '{"containerOverrides":[{"name":"Main","command":["manage","bootstrap_admin"],
    "environment":[{"name":"ADMIN_EMAIL","value":"you@beldium.com"},{"name":"ADMIN_BOOTSTRAP_SECRET","value":"<temporary password>"}]}]}'
```

Log in at `https://<host>/admin/`, then change the password there straight away. Overrides stay visible in the ECS task history for a while.

---

## Step 16. Custom domain and TLS certificate

DNS stays at Truehost. Use `api-staging.beldium.com` (or similar) for staging, and `api.beldium.com` for production.

**a. Request a certificate** in the same region:

```bash
export DOMAIN=api-staging.beldium.com     # production: api.beldium.com
CERT_ARN=$(aws acm request-certificate --domain-name $DOMAIN --validation-method DNS --query CertificateArn --output text)
sleep 10
aws acm describe-certificate --certificate-arn $CERT_ARN \
  --query 'Certificate.DomainValidationOptions[0].ResourceRecord' --output table
```

**b. At Truehost,** add the validation CNAME shown (Name → Value). Truehost may want the name without the trailing `.beldium.com.`. Then:

```bash
aws acm wait certificate-validated --certificate-arn $CERT_ARN
```

**c. Attach it to the load balancer** (console, per the AWS Express Mode guide):
1. ECS → Clusters → `beldium-<env>` → service `beldium-<env>-web` → **Resources** tab → open the **listener rule**.
2. **Actions → Edit rule**. Copy the current Host header value (the `.on.aws` URL). Remove that condition, add a **Host header** condition, paste the URL back, choose **Add OR condition value**, and enter your domain. Save.
3. Open the HTTPS listener → **Certificates** tab → **Add certificate** → pick the one you just validated.

**d. Point DNS at it.** Get the load balancer's DNS name (EC2 → Load balancers, the one tagged `AmazonECSManaged`), then add a CNAME at Truehost: `api-staging` → `<load balancer DNS name>`. **For production, do this only at cutover.** Until then, `api.beldium.com` keeps pointing at Render.

**e. Add the domain to the app settings,** if it isn't already in `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` in SSM, then force a new deployment (step 13's `update-service` command).

Express Mode keeps manual changes like these unless a later Express Mode update conflicts with them. Our workflow only changes the task definition, so it won't.

---

## Step 17. GitHub

**Settings → Environments → New environment**: create `staging` and `aws-production`. The name is not `production`: the repository's existing `Production` environment belongs to Vercel's deployments, and GitHub environment names ignore case. The workflow maps `main` to `aws-production`, and still names the AWS resources `beldium-production-*`. On `production`, add yourself under **Required reviewers**, so every production deploy waits for a click.

Add these **variables** (Environment variables, not secrets; none are sensitive) to each environment:

| Variable | Value |
|---|---|
| `AWS_ACCOUNT_ID` | `$ACCOUNT` |
| `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::<account>:role/beldium-<env>-github-deploy` |
| `ECR_REPOSITORY` | `beldium-backend` |
| `ECS_CLUSTER` | `beldium-<env>` |
| `ECS_EXECUTION_ROLE_ARN` | `arn:aws:iam::<account>:role/beldium-<env>-ecs-execution` |
| `ECS_TASK_ROLE_ARN` | `arn:aws:iam::<account>:role/beldium-<env>-ecs-task` |
| `WEB_SERVICE_ARN` | `$WEB_SERVICE_ARN` from step 13 |
| `WORKER_SERVICE` | `beldium-<env>-worker` |
| `BEAT_SERVICE` | `beldium-<env>-beat` |
| `TASK_SUBNETS` | `$PUBLIC_SUBNETS` |
| `TASK_SECURITY_GROUPS` | `$TASKS_SG` |
| `TASK_ASSIGN_PUBLIC_IP` | `ENABLED` |

Create the `staging` branch from the branch you deploy from, and push it. That runs the first automated deploy. Watch it under **Actions**.

---

## Step 18. Check staging end to end

| Check | How |
|---|---|
| Health | `curl https://<domain>/health/` returns `ok` for both database and redis |
| API docs load, static files styled | `https://<domain>/api/docs/` |
| Admin | log in at `/admin/` |
| Registration email arrives | register from the staging frontend; the worker log shows the task |
| Upload lands in S3 | upload a compliance document; `aws s3 ls s3://$BUCKET --recursive` |
| Download works | open the uploaded document from the frontend |
| Beat is running | `aws logs tail /ecs/beldium-$ENV --since 2h \| grep check_expiring_credentials` (hourly) |
| Rate limits hold across tasks | repeated bad logins get a 429 after the configured count |
| Deploy pipeline | push a trivial change to `staging` and watch it roll out |

---

## Step 19. Alarms (recommended)

Express Mode already creates a 5xx rollback alarm for deployments. Also add CloudWatch alarms, sent to an SNS topic with your email subscribed, for:
- RDS: `CPUUtilization` > 80% for 15 minutes, `FreeStorageSpace` < 2 GB.
- ElastiCache: `DatabaseMemoryUsagePercentage` > 80%.
- ECS: worker and beat `RunningTaskCount` < 1.
- ALB: target `HTTPCode_Target_5XX_Count` sustained.

---

## Step 20. Production

Set `ENV=production` and repeat steps 4 to 17 with the production differences noted in each step. Skip `migrate` in step 12, and don't point `api.beldium.com` at AWS in step 16d yet. Then follow the cutover in `migration/README.md`:

1. Stop writes on Render.
2. Dump the Render databases.
3. Restore v2 into RDS, run `migrate`, run the merge.
4. Copy uploads to S3 (below).
5. Change the `api.beldium.com` CNAME at Truehost.

Lower the TTL on that record to 300 seconds a day before cutover, so the switch (and any rollback) takes effect quickly.

---

## Reference

### Deploy flow

`.github/workflows/deploy.yml`:
- push to `staging` deploys staging
- push to `main` deploys production

Steps: run tests, assume the deploy role through OIDC, build and push `beldium-backend:<git sha>`, register the three task definitions, run `migrate` as a one-off task and stop if it fails, update web (Express Mode), worker and beat, then wait for all three to stabilise.

Migrations run while the old code still serves traffic. Keep each migration backwards compatible for one release: add a column first, and drop the old one in a later release.

### One-off commands

Run any management command the same way as step 15, with `"command":["manage","<command>", "<args>"]`.

### Loading data through a tunnel

The databases are private: nothing on the internet can reach them. To load data, open a temporary tunnel from your computer through the running worker task, using AWS Session Manager. There's no new server and no public access.

**Once per environment (CloudShell):** allow Session Manager on the worker.

```bash
aws iam put-role-policy --role-name beldium-$ENV-ecs-task --policy-name ecs-exec --policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["ssmmessages:CreateControlChannel","ssmmessages:CreateDataChannel","ssmmessages:OpenControlChannel","ssmmessages:OpenDataChannel"],"Resource":"*"}]}'
aws ecs update-service --cluster beldium-$ENV --service beldium-$ENV-worker --enable-execute-command --force-new-deployment --query service.enableExecuteCommand
aws ecs wait services-stable --cluster beldium-$ENV --services beldium-$ENV-worker
```

**On your computer, once:** `brew install --cask session-manager-plugin`, then `aws login --region us-east-1`.

**Each time:**

- Window 1: `bash deploy/db-tunnel.sh <env>`. Leave it open.
- Window 2: `export TARGET=$(bash deploy/db-url.sh <env>)`. `$TARGET` now reaches the environment's database. The password is never printed.

Restore a dump into it. `-n public` leaves the schema itself alone. `--single-transaction` means it fully succeeds or changes nothing:

```bash
pg_restore --clean --if-exists --no-owner --no-acl --single-transaction --exit-on-error -n public -d "$TARGET" migration/dumps/v2.dump
```

Then use `migration/merge.py`, `reactivate_users.py`, `verify.sql` and `smoke_test.py` with `--target "$TARGET"` (see `migration/README.md`).

Afterwards, force a new deployment of the web service so it drops old connections. When the environment no longer needs loading, turn the tunnel access off again: `update-service ... --no-enable-execute-command --force-new-deployment`, then `aws iam delete-role-policy --role-name beldium-$ENV-ecs-task --policy-name ecs-exec`.

### Moving uploaded files off Render

Render has no `AWS_STORAGE_BUCKET_NAME` set, so uploads live on the Render service's disk under `media/`. `copy_media_to_s3` reads every FileField in the database, uploads each file to the bucket under the same key, and lists records whose file is missing on disk. It only reads the database and never overwrites an existing object.

It must run where the files are: on Render (shell or one-off job). Give it temporary credentials from an IAM user that has only `s3:PutObject` and `s3:GetObject` on the production bucket, and delete that user afterwards:

```bash
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  python manage.py copy_media_to_s3 --bucket beldium-production-uploads --dry-run --report /tmp/media-report.csv
# check the counts, then run again without --dry-run
```

If Render has no persistent disk attached, files uploaded before the most recent Render deploy are already gone. The report lists them as `missing_locally`.

### Before merging `aws-migration` into `main`

Render deploys `main`. The new settings refuse to start when `ENVIRONMENT=production` and a required variable is missing, and Render doesn't have `AWS_STORAGE_BUCKET_NAME`, `CSRF_TRUSTED_ORIGINS` and others. Turn off Render auto-deploy first, or merge only at cutover.

### Costs

Main monthly costs per environment:
- RDS instance and storage
- ElastiCache node(s)
- Fargate vCPU and memory for about 3 always-on tasks (web, worker, beat)
- One Application Load Balancer
- Public IPv4 addresses (charged per address per hour)
- CloudWatch Logs

Staging can run smaller instance sizes than production. Price it for your sizes with the AWS Pricing Calculator (calculator.aws) before creating production.
