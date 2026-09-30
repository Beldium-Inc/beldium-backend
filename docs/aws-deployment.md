# AWS deployment

How this API runs on AWS, and what has to exist before the first deploy. Region: `us-east-1`. Two environments, `staging` and `production`, built from the same files. They differ only in the values stored in SSM and in the AWS resource IDs held in GitHub.

## What runs

One Docker image (`Dockerfile`). The first argument picks the role (`deploy/docker-entrypoint.sh`):

| Role | Runs as | Command | Count |
|---|---|---|---|
| `web` | ECS Express Mode service (ALB in front) | gunicorn, `GUNICORN_WORKERS` processes | 1 or more |
| `worker` | ECS service, Fargate | Celery worker | 1 or more |
| `beat` | ECS service, Fargate | Celery beat (hourly logistics expiry check) | **exactly 1** |
| `migrate` | one-off task per deploy | `manage.py migrate` | 0 |
| `manage <cmd>` | one-off task, by hand | any management command | 0 |

Task definitions are generated from `deploy/ecs/config.json` by `deploy/render_task_definitions.py`. Families are `beldium-<env>-web`, `beldium-<env>-worker`, `beldium-<env>-beat`. Every container is named `Main`.

## Deploy flow

`.github/workflows/deploy.yml`:

- push to `staging` deploys to the `staging` GitHub environment
- push to `main` deploys to the `production` GitHub environment

Steps: run tests, assume the deploy role through OIDC, build and push `<ECR repo>:<git sha>`, register the three task definitions, run `migrate` as a one-off task and stop if it fails, point the web, worker and beat services at the new revisions, then wait until all three are stable.

Migrations run while the old code is still serving. Keep each migration backwards compatible for one release: add a column in one release, and drop the old one in a later release.

## One-time AWS setup (per environment)

Replace `<env>` with `staging` or `production`, and `<account>` with the AWS account ID.

### 1. Network

- VPC with private subnets in at least two AZs, and a NAT gateway. Tasks need outbound internet for Resend (`api.resend.com`). ECR, SSM, CloudWatch Logs and S3 go through NAT too, unless you add VPC endpoints for them.
- Security group `beldium-<env>-tasks` for all tasks. Allow outbound traffic. Express Mode manages inbound from its ALB.
- RDS security group: inbound 5432 from `beldium-<env>-tasks`.
- ElastiCache security group: inbound 6379 from `beldium-<env>-tasks`.

### 2. Data stores

- **RDS PostgreSQL 18**, one instance, private, not publicly accessible. Enable automated backups. RDS requires SSL by default. psycopg uses it automatically, so no URL change is needed.
- **ElastiCache Valkey**. If in-transit encryption is on, the URL must start with `rediss://` and end with `?ssl_cert_reqs=required`, or Celery refuses to start.
- **S3 bucket** `beldium-<env>-uploads`: Block Public Access on, versioning on, default encryption on. No bucket policy for public reads. Files are streamed through the API (`common/files.py`), so the bucket needs no CORS rule.
- **ECR repository** `beldium-backend`. One repository is shared by both environments.
- **CloudWatch log group** `/ecs/beldium-<env>`.

### 3. SSM parameters

Create each as `SecureString` under `/beldium/<env>/`. The task will not start if any are missing. The names come from `deploy/ecs/config.json`:

| Name | Example value (staging) | Notes |
|---|---|---|
| `SECRET_KEY` | 64+ random characters | Different per environment |
| `DATABASE_URL` | `postgres://beldium:...@<rds-endpoint>:5432/beldium` | |
| `REDIS_URL` | `rediss://<valkey-endpoint>:6379/0?ssl_cert_reqs=required` | |
| `ALLOWED_HOSTS` | `api.beldium.com` (production) | Staging uses its own hostname |
| `ECS_SERVICE_HOST` | `pending.invalid`, then the Express URL host | Update once the service exists, then redeploy |
| `CSRF_TRUSTED_ORIGINS` | `https://api.beldium.com` | |
| `CORS_ALLOWED_ORIGINS` | `https://compliance.beldium.com,https://miners.beldium.com` | No localhost |
| `COMPLIANCE_PORTAL_ORIGINS` | `https://compliance.beldium.com` | No localhost |
| `MINER_PORTAL_ORIGINS` | `https://miners.beldium.com` | No localhost |
| `FRONTEND_URL` | `https://compliance.beldium.com` | Used in email links |
| `DEFAULT_FROM_EMAIL` | `noreply@beldium.com` | |
| `EMAIL_HOST_PASSWORD` | Resend API key | |
| `AWS_STORAGE_BUCKET_NAME` | `beldium-<env>-uploads` | |
| `NUM_PROXIES` | `1` | ALB is the only proxy |

`ENVIRONMENT` is set by the task definition, not SSM. Leave `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` out: the task role gives S3 access.

### 4. IAM

**Task execution role** `beldium-<env>-ecs-execution`
- Managed policy `AmazonECSTaskExecutionRolePolicy`.
- `ssm:GetParameters` on `arn:aws:ssm:us-east-1:<account>:parameter/beldium/<env>/*`.
- `kms:Decrypt`, only if the parameters use a customer-managed KMS key.

**Task role** `beldium-<env>-ecs-task`
- `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on `arn:aws:s3:::beldium-<env>-uploads/*`
- `s3:ListBucket` on `arn:aws:s3:::beldium-<env>-uploads`

**Express Mode infrastructure role**: the role ECS uses to manage the ALB and scaling. Follow the ECS Express Mode console prompt or docs to create it.

**GitHub OIDC provider**: create once per account, with URL `https://token.actions.githubusercontent.com` and audience `sts.amazonaws.com`.

**Deploy role** `beldium-<env>-github-deploy`, trust policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:Beldium-Inc/beldium-backend:environment:<env>"
      }
    }
  }]
}
```

The `sub` pins the role to one GitHub environment, so the staging role cannot deploy production.

Permissions:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Effect": "Allow", "Action": "ecr:GetAuthorizationToken", "Resource": "*"},
    {"Effect": "Allow", "Action": [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage"
    ], "Resource": "arn:aws:ecr:us-east-1:<account>:repository/beldium-backend"},
    {"Effect": "Allow", "Action": ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"], "Resource": "*"},
    {"Effect": "Allow", "Action": [
      "ecs:RunTask", "ecs:DescribeTasks", "ecs:UpdateService", "ecs:DescribeServices",
      "ecs:UpdateExpressGatewayService", "ecs:DescribeExpressGatewayService"
    ], "Resource": "*", "Condition": {"ArnEquals": {"ecs:cluster": "arn:aws:ecs:us-east-1:<account>:cluster/<cluster>"}}},
    {"Effect": "Allow", "Action": "iam:PassRole", "Resource": [
      "arn:aws:iam::<account>:role/beldium-<env>-ecs-execution",
      "arn:aws:iam::<account>:role/beldium-<env>-ecs-task"
    ], "Condition": {"StringEquals": {"iam:PassedToService": "ecs-tasks.amazonaws.com"}}}
  ]
}
```

Not verified: whether `--monitor-resources` on the Express update needs extra read permissions (for example on the load balancer). If that step fails with AccessDenied, add the action named in the error, or remove the two `--monitor-*` flags. The `services-stable` wait that follows still gates the deploy.

### 5. First image and task definitions

The services need a task definition to exist before the workflow can update them. Do this once, by hand, with admin credentials:

```bash
docker build --platform linux/amd64 -t <account>.dkr.ecr.us-east-1.amazonaws.com/beldium-backend:bootstrap .
docker push <account>.dkr.ecr.us-east-1.amazonaws.com/beldium-backend:bootstrap
python deploy/render_task_definitions.py --env <env> \
  --image <account>.dkr.ecr.us-east-1.amazonaws.com/beldium-backend:bootstrap \
  --account-id <account> \
  --execution-role-arn arn:aws:iam::<account>:role/beldium-<env>-ecs-execution \
  --task-role-arn arn:aws:iam::<account>:role/beldium-<env>-ecs-task \
  --out-dir build/ecs
for role in web worker beat; do aws ecs register-task-definition --cli-input-json file://build/ecs/$role.json; done
```

Run `migrate` once before starting anything (same `run-task` call as the workflow's migration step).

### 6. Services

Web (Express Mode):

```bash
aws ecs create-express-gateway-service \
  --cluster <cluster> \
  --service-name beldium-<env>-web \
  --infrastructure-role-arn <express-infrastructure-role-arn> \
  --task-definition-arn <beldium-<env>-web revision arn> \
  --health-check-path /health/ \
  --network-configuration '{"subnets":["<private-subnet-a>","<private-subnet-b>"],"securityGroups":["<tasks-sg>"]}'
```

Worker and beat:

```bash
aws ecs create-service --cluster <cluster> --service-name beldium-<env>-worker \
  --task-definition beldium-<env>-worker --desired-count 1 --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<a>,<b>],securityGroups=[<tasks-sg>],assignPublicIp=DISABLED}"

aws ecs create-service --cluster <cluster> --service-name beldium-<env>-beat \
  --task-definition beldium-<env>-beat --desired-count 1 --launch-type FARGATE \
  --deployment-configuration "minimumHealthyPercent=0,maximumPercent=100" \
  --network-configuration "awsvpcConfiguration={subnets=[<a>,<b>],securityGroups=[<tasks-sg>],assignPublicIp=DISABLED}"
```

Never scale beat above 1. Two beats run every scheduled job twice.

After the web service exists, put its URL host into `/beldium/<env>/ECS_SERVICE_HOST` and redeploy.

**Open item, not verified:** how a custom domain (`api.beldium.com`) attaches to an Express Mode service. The CLI has no domain or certificate option. This needs to be confirmed in the Express Mode docs or console before cutover. In any case, DNS stays at Truehost as a CNAME to whatever hostname AWS gives, and TLS needs an ACM certificate for `api.beldium.com`, validated by a DNS record added at Truehost.

### 7. GitHub

Create two environments in the repository settings, `staging` and `production`. Add a required reviewer on `production` if you want a manual gate. Add these **variables** (not secrets; none of them are sensitive) to each:

| Variable | Example |
|---|---|
| `AWS_ACCOUNT_ID` | `123456789012` |
| `AWS_DEPLOY_ROLE_ARN` | `arn:aws:iam::<account>:role/beldium-<env>-github-deploy` |
| `ECR_REPOSITORY` | `beldium-backend` |
| `ECS_CLUSTER` | cluster name |
| `ECS_EXECUTION_ROLE_ARN` | `arn:aws:iam::<account>:role/beldium-<env>-ecs-execution` |
| `ECS_TASK_ROLE_ARN` | `arn:aws:iam::<account>:role/beldium-<env>-ecs-task` |
| `WEB_SERVICE_ARN` | Express service ARN |
| `WORKER_SERVICE` | `beldium-<env>-worker` |
| `BEAT_SERVICE` | `beldium-<env>-beat` |
| `TASK_SUBNETS` | `subnet-aaa,subnet-bbb` |
| `TASK_SECURITY_GROUPS` | `sg-ccc` |

Create a `staging` branch from `main`.

## Running a one-off command

Example: create the admin login. Pass one-off values as overrides, never as SSM parameters, so they don't apply to every boot:

```bash
aws ecs run-task --cluster <cluster> --launch-type FARGATE \
  --task-definition beldium-<env>-web \
  --network-configuration "awsvpcConfiguration={subnets=[<a>,<b>],securityGroups=[<tasks-sg>],assignPublicIp=DISABLED}" \
  --overrides '{"containerOverrides":[{"name":"Main","command":["manage","bootstrap_admin"],
    "environment":[{"name":"ADMIN_EMAIL","value":"..."},{"name":"ADMIN_BOOTSTRAP_SECRET","value":"..."}]}]}'
```

Overrides show up in the ECS task description. For the admin password, change it through `/admin/` right after.

## Moving uploaded files off Render

Render has no `AWS_STORAGE_BUCKET_NAME` set, so uploads are on the Render service's local disk under `media/`. `copy_media_to_s3` reads every FileField in the database, uploads each file to the bucket under the same key, and lists any record whose file is missing on disk. It only reads the database, and it never overwrites an existing object.

It has to run where the files are: on the Render web service (Render shell, or a one-off job), with temporary AWS credentials that can only `PutObject` and `GetObject` on the production bucket:

```bash
AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
  python manage.py copy_media_to_s3 --bucket beldium-production-uploads --dry-run --report /tmp/media-report.csv
# check the counts, then run again without --dry-run
```

Delete those credentials afterwards. If Render has no persistent disk attached, files uploaded before the most recent Render deploy are already gone. The report lists them as `missing_locally`.

## Before merging this branch to main

Render deploys `main` too. These settings make the app refuse to start when `ENVIRONMENT=production` and any required variable is missing. Render has no `AWS_STORAGE_BUCKET_NAME` and no `CSRF_TRUSTED_ORIGINS`, and it may also be missing others. `DEBUG` now defaults to off, and the app refuses to start with `DEBUG` on in production. Either turn off Render auto-deploy before merging, or merge only at cutover.
