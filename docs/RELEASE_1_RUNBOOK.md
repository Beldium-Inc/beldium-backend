# Release 1 runbook: what a person has to do

Everything in Release 1 that could be done in code is in two pull requests:
backend Beldium-Inc/beldium-backend#42 and frontend Beldium-Inc/beldium-new-frontend#63.
These are the steps that need a person, an account or an outside party, in order.
Commands are for zsh in Mac Terminal, run from the backend repository folder.

## 1. Fix the failing Vercel project (before merging the frontend)

The frontend now refuses to build on a build server without `VITE_API_URL`,
so it can never go live pointing at localhost. The Vercel project
`beldium-new-frontend` (team `master-obi-davids-projects`) does not have it,
so its preview deploy fails. The other two projects deploy the same code
fine.

1. Sign in to vercel.com with the account that owns `master-obi-davids-projects`.
2. Open the project `beldium-new-frontend`, then Settings, then Environment Variables.
3. Add `VITE_API_URL` with the value `https://api.beldium.com`, ticked for
   Production and Preview. No trailing slash, no `/api/v1`.
4. Do the same check in `beldium-new-frontend-clys` and `miners-portal`:
   the variable must be there for Production, or their live deploys fail
   the same way after the merge.
5. Open the failed deployment on PR 63 and press Redeploy. It should pass.

Sessions only survive a page reload on the real domains (for example
`compliance.beldium.com`), because the refresh cookie is shared only between
sites under `beldium.com`. Vercel preview addresses (`*.vercel.app`) are
also not portal origins, so sign in is refused there. Test sign in on the
real domains or locally.

## 2. Merge and deploy the backend

1. Merge PR 42. Pushing to `main` deploys to production.
2. Watch GitHub, Actions, the deploy run. It runs the migrations as a
   one-off task first and stops if one fails. Migrations in this release:
   quality 0005 to 0012, accounts 0010 and 0011, common 0001, vault 0003.
   Quality 0005 marks every existing quality row legacy and stops with a
   list if two certificates share a verification hash.
3. Check it answers: `curl -s https://api.beldium.com/health/`

## 3. Give the first Q&C roles on production

Until this is done the new role, audit log and settings screens are closed
to everyone. Everyone else keeps working as before.

```bash
aws login --region us-east-1
```

```bash
export ENV=production
```

```bash
NET=$(aws ecs describe-services --cluster beldium-$ENV --services beldium-$ENV-worker --query 'services[0].networkConfiguration.awsvpcConfiguration' --output json | python3 -c 'import json,sys; c=json.load(sys.stdin); print("awsvpcConfiguration={subnets=[%s],securityGroups=[%s],assignPublicIp=%s}" % (",".join(c["subnets"]), ",".join(c["securityGroups"]), c["assignPublicIp"]))')
```

Replace the two email addresses with the real people. Each must already
have an account. The Admin and the Q&C Partner must be different people,
because Admin gives roles and Q&C approves them.

```bash
aws ecs run-task --cluster beldium-$ENV --launch-type FARGATE --task-definition beldium-$ENV-web --network-configuration "$NET" --overrides '{"containerOverrides":[{"name":"Main","command":["manage","grant_first_quality_role","--email","ADMIN-PERSON@beldium.com","--role","ADM"]}]}'
```

```bash
aws ecs run-task --cluster beldium-$ENV --launch-type FARGATE --task-definition beldium-$ENV-web --network-configuration "$NET" --overrides '{"containerOverrides":[{"name":"Main","command":["manage","grant_first_quality_role","--email","QC-PARTNER@beldium.com","--role","QC"]}]}'
```

Check the task logs in CloudWatch, group `/ecs/beldium-production`, for
"Created active ADM assignment" and "Created active QC assignment". After
that every other role is given on the Users and roles screen: Admin
requests, Q&C approves.

`bootstrap_admin` now needs `"--confirm-environment","production"` added to
its command to run on production. Commands that delete data stay refused
there.

## 4. Merge and deploy the frontend

1. Only after step 2 is live. The frontend keeps the access token in memory
   and restores sessions from the backend's new refresh cookie.
2. Merge PR 63. Vercel deploys it.
3. Everyone signed in before signs in once more (the old token store is
   cleared).
4. Sign in to Quality & Control as the Q&C Partner. You should see Users and
   roles, Audit trail, Rule settings and Security in the menu. Reload the
   page: you should stay signed in.

## 5. Prove the backups (E14)

Read only, about five minutes:

```bash
aws rds describe-db-instances --region us-east-1 --db-instance-identifier beldium-production --query 'DBInstances[0].{RetentionDays:BackupRetentionPeriod,LatestRestorable:LatestRestorableTime,MultiAZ:MultiAZ,DeletionProtection:DeletionProtection,Encrypted:StorageEncrypted,SubnetGroup:DBSubnetGroup.DBSubnetGroupName,SecurityGroups:VpcSecurityGroups[].VpcSecurityGroupId}'
```

```bash
aws s3api get-bucket-versioning --bucket "$(aws ssm get-parameter --region us-east-1 --name /beldium/production/AWS_STORAGE_BUCKET_NAME --query Parameter.Value --output text)"
```

`RetentionDays` must be above 0 (the guide says 14), `LatestRestorable`
within minutes of now, `DeletionProtection` true, and versioning `Enabled`.

Test restore into a separate, throwaway instance. It reads the backups and
never writes to production. Put the `SubnetGroup` and the first
`SecurityGroups` value from the first command in place of the two `<...>`:

```bash
aws rds restore-db-instance-to-point-in-time --region us-east-1 --source-db-instance-identifier beldium-production --target-db-instance-identifier beldium-restore-test --use-latest-restorable-time --db-instance-class db.t4g.small --db-subnet-group-name <SubnetGroup> --vpc-security-group-ids <SecurityGroup> --no-publicly-accessible --no-multi-az --no-deletion-protection --tags Key=purpose,Value=restore-test
```

```bash
time aws rds wait db-instance-available --region us-east-1 --db-instance-identifier beldium-restore-test
```

Write down the time it printed: that is the real recovery time. Then
delete the copy. Read the name twice: it must say `beldium-restore-test`.

```bash
aws rds delete-db-instance --region us-east-1 --db-instance-identifier beldium-restore-test --skip-final-snapshot --delete-automated-backups
```

## 6. Make two-step sign-in required for staff and partners (G8)

1. Tell every Beldium staff member, laboratory, inspector and sampler to set
   up an authenticator app on the Security page. Suppliers, buyers and export
   agents are not asked.
2. In `deploy/ecs/config.json`, change `"SECOND_FACTOR_ENFORCED": "False"` to
   `"True"` under `web`, open a pull request and merge it.
3. From then on anyone in those roles without the app sees only a notice and
   the Security page until they set it up.

## 7. Turn the content security policy on

After a week on the live site with no "Content-Security-Policy-Report-Only"
messages in the browser console, rename that header to
`Content-Security-Policy` in `src/server.ts` (frontend) and merge.

## 8. Outside services (E4)

| Item | What to do | Then in our system |
|---|---|---|
| SMS sender ID | In the Termii dashboard, request a sender ID named Beldium. Approval takes days. | `aws ssm put-parameter --region us-east-1 --name /beldium/production/TERMII_API_KEY --type SecureString --value '<key>'` and the same for `TERMII_SENDER_ID` as String. Then add both names to `parameters` in `deploy/ecs/config.json`, add `"SMS_ENABLED": "True"` under `web` and `worker`, and merge. Create the parameters first or the deploy fails. |
| Virus scanner | Run ClamAV (`clamav/clamav` image) as a small ECS service in the same network, port 3310, reachable from the web tasks only. | Add `"CLAMAV_HOST": "<its private address>"` under `web`. Until then uploads show "Not scanned yet". |
| Penetration test | Book a tester for after this release is live. Give them the staging site. | Put their findings into the task lists. |
| E-signature | Pick the method for certificates (Release 4). | None yet. |

## 9. Values Q&C still owes

Each is a placeholder that makes its check refuse to run. When Q&C sends a
value in writing, the Q&C Partner enters it on Rule settings: Change, the
value, the date it takes effect, and the reason (quote the Q&C letter).

custody time gap, HOLD deadline, the two GPS tolerances, phone clock gap,
BORDERLINE rule per parameter, mineral codes, seal and assignment prefixes,
ID sequence rules. Also: confirm David as the second approver for uploads on
behalf of a company, and whether the laboratory may ever see party names.
The FAIL dispute step is not built and must not be until Q&C sends the rule.
