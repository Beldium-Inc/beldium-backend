# Cutover: Render → AWS production

The exact order for moving live users from Render to AWS. This is the same procedure rehearsed on staging on 2026-09-30. Run everything in **Mac Terminal** from the repository folder unless a step says otherwise. The commands are written for zsh.

**Window:** about 1 hour, of which the API is unavailable for roughly 40 minutes (Phase B to Phase D).
**Point of no return:** step D3. Once users write data to AWS, going back to Render would lose that data.

Fixed values used below:

| Name | Value |
|---|---|
| Production web address | `be-2cd2b759ecea4db0b430f113d9304e73.ecs.us-east-1.on.aws` |
| Production load balancer | `ecs-express-gateway-alb-83671ec5-1377816147.us-east-1.elb.amazonaws.com` |
| Render target of `api.beldium.com` today | `api-beldium-backend-qd86.onrender.com` |
| Production bucket | `beldium-production-uploads` |

---

## Phase 0: The day before

1. **Truehost:** set the TTL of the existing `api` CNAME record to the lowest value offered (ideally 300 seconds). Don't change where it points yet. A low TTL makes the switch, and any rollback, take effect within minutes.
2. **GitHub `aws-production` environment:** allow `main`, and require your approval:

   ```bash
   R=Beldium-Inc/beldium-backend
   gh api -X POST "repos/${R}/environments/aws-production/deployment-branch-policies" -f name=main -f type=branch --jq .name
   gh api -X PUT "repos/${R}/environments/aws-production" --input - <<EOF
   {"reviewers":[{"type":"User","id":$(gh api user --jq .id)}],"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
   EOF
   gh api "repos/${R}/environments/aws-production" --jq '[.protection_rules[].type] | join(", ")'
   ```

   ✅ The last line shows `required_reviewers, branch_policy`.
3. Tell users about the maintenance window.
4. Check your Mac is ready:
   - `aws login --region us-east-1`, then `aws sts get-caller-identity` shows `660915073018`
   - `session-manager-plugin --version` works
   - `gh auth status` shows you're logged in
   - your Backblaze read-only key is at hand
   - you can log in to the Render dashboard and Truehost
   - you know the staff (admin) login for the API

## Phase A: Copy everything while Render is still live (about 10 minutes)

**A1. Final copies of both Render databases.** These only read. Paste each Render **External Database URL** when asked; it isn't shown:

```bash
cd /Volumes/Stark/Beldium-New/beldium-backend
mkdir -p migration/dumps/final
read -rs "V2_URL?v2 external URL: "; echo
pg_dump --format=custom --no-owner --no-acl "$V2_URL" -f migration/dumps/final/v2-live.dump && echo "✅ v2 copied"
read -rs "OLD_URL?old external URL: "; echo
pg_dump --format=custom --no-owner --no-acl "$OLD_URL" -f migration/dumps/final/old.dump && echo "✅ old copied"; unset OLD_URL
```

Keep `V2_URL` set; Phase B needs it again.

**A2. Load the copies locally:**

```bash
dropdb --if-exists copy_v2_final; createdb copy_v2_final
pg_restore --no-owner --no-acl -d copy_v2_final migration/dumps/final/v2-live.dump && echo "✅ v2 local"
dropdb --if-exists copy_old_final; createdb copy_old_final
pg_restore --no-owner --no-acl -d copy_old_final migration/dumps/final/old.dump && echo "✅ old local"
```

**A3. Download any new uploads** through the live API, with the staff login. Files already downloaded are skipped:

```bash
.venv/bin/python migration/pull_render_files.py --db postgresql:///copy_v2_final --api https://api.beldium.com --out migration/dumps/media
```

✅ No `http_500` results. A `http_500` means that file is missing on Render.

**A4. Preview the merge** against the fresh old copy. Nothing is written:

```bash
.venv/bin/python migration/find_missing.py --old postgresql:///copy_old_final --v2 postgresql:///copy_v2_final --out-dir migration/out/final
```

✅ The same shape as the rehearsal: 16 users already present; 6, 6, 6, 4, 4, 7, 4, 4 missing; nothing ambiguous. If anything differs, stop and investigate before Phase B.

## Phase B: Stop writes on Render (the API goes down here)

**B1.** Render dashboard → **api-beldium-backend → Settings → Suspend Service**. The database stays up; only the API stops.

```bash
curl -s -o /dev/null -w "api.beldium.com: %{http_code}\n" --max-time 20 https://api.beldium.com/health/
```

✅ Anything other than `200` (Render shows a suspended page).

**B2. The truly final copy** of v2. It catches anything written during Phase A:

```bash
pg_dump --format=custom --no-owner --no-acl "$V2_URL" -f migration/dumps/final/v2.dump && echo "✅ final v2 copied"; unset V2_URL
dropdb --if-exists copy_v2_final; createdb copy_v2_final
pg_restore --no-owner --no-acl -d copy_v2_final migration/dumps/final/v2.dump && echo "✅ final v2 local"
.venv/bin/python migration/pull_render_files.py --db postgresql:///copy_v2_final --out migration/dumps/media --check
```

✅ `Every referenced file is downloaded.` If not, someone uploaded during Phase A: resume Render (B1 in reverse), rerun A3 with `--db postgresql:///copy_v2_final`, then suspend again and repeat B2.

## Phase C: Load production (about 20 to 30 minutes, mostly the restore)

**C1. Window 1:** open the tunnel to production and leave it open:

```bash
cd /Volumes/Stark/Beldium-New/beldium-backend
bash deploy/db-tunnel.sh production 15433
```

**C2. Window 2:**

```bash
cd /Volumes/Stark/Beldium-New/beldium-backend
export AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
export TARGET=$(bash deploy/db-url.sh production 15433)
psql "$TARGET" -Atc "select count(*) from accounts_user"
```

✅ `0`: production holds the tables but no data.

**C3. Restore** (5 to 15 minutes through the tunnel, quiet stretches are normal, don't press Ctrl+C):

```bash
time pg_restore --verbose --clean --if-exists --no-owner --no-acl --single-transaction --exit-on-error -n public -d "$TARGET" migration/dumps/final/v2.dump 2>&1 | grep -E "creating TABLE|processing data|error" ; echo "exit=${pipestatus[1]}"
psql "$TARGET" -Atc "select count(*) from accounts_user"
```

✅ `exit=0`, then the same user count as `psql -d copy_v2_final -Atc "select count(*) from accounts_user"`.

**C4. Merge, reactivate, verify:**

```bash
mkdir -p migration/out/final
psql "$TARGET" -X -A -F, -f migration/verify.sql > migration/out/final/verify-before.csv
.venv/bin/python migration/merge.py --old postgresql:///copy_old_final --target "$TARGET" --dry-run --log migration/out/final/merge-dry.jsonl
.venv/bin/python migration/merge.py --old postgresql:///copy_old_final --target "$TARGET" --log migration/out/final/merge.jsonl
.venv/bin/python migration/reactivate_users.py --old postgresql:///copy_old_final --target "$TARGET" --apply --expect 2
psql "$TARGET" -X -A -F, -f migration/verify.sql > migration/out/final/verify-after.csv
diff migration/out/final/verify-before.csv migration/out/final/verify-after.csv
sed -n '/== 2/,/== 3/p' migration/out/final/verify-after.csv
```

✅ The dry run and the real run show the same numbers as A4, and `Committed:`. Reactivation prints `Reactivated 2.` The diff shows only the expected increases, and section 2 shows `(0 rows)`.

If reactivation says `Expected 2, found N`: those users changed state on Render since the rehearsal. Nothing was changed. Note it and continue; we review them after cutover.

**C5. Files:**

```bash
aws s3 sync migration/dumps/media s3://beldium-production-uploads/ && echo "✅ current files"
read -rs "B2_KEY_ID?B2 keyID: "; echo
read -rs "B2_APPLICATION_KEY?B2 applicationKey: "; echo
export B2_KEY_ID B2_APPLICATION_KEY
.venv/bin/python migration/copy_legacy_files.py --old postgresql:///copy_old_final --bucket beldium-production-uploads
unset B2_KEY_ID B2_APPLICATION_KEY
```

✅ `copied=7` (or `already_in_bucket` if re-run).

**C6. Restart production** so every task starts clean on the loaded data:

```bash
for s in web worker beat; do aws ecs update-service --cluster beldium-production --service beldium-production-$s --force-new-deployment --query service.serviceName --output text; done
aws ecs wait services-stable --cluster beldium-production --services beldium-production-web beldium-production-worker beldium-production-beat && echo "✅ production restarted"
```

**C7. Smoke test** with the staff login, while the tunnel is still open:

```bash
.venv/bin/python migration/smoke_test.py --api https://be-2cd2b759ecea4db0b430f113d9304e73.ecs.us-east-1.on.aws --origin https://compliance.beldium.com --db "$TARGET"
```

✅ Four green lines. Also log in once with an ordinary account, without `--db`. Use `--origin https://miners.beldium.com` or `--origin https://logistics.beldium.com` for those portals' accounts.

**C8. Test through the real name**, before switching DNS:

```bash
ALB_IP=$(dig +short ecs-express-gateway-alb-83671ec5-1377816147.us-east-1.elb.amazonaws.com | head -1)
curl -s --resolve "api.beldium.com:443:${ALB_IP}" https://api.beldium.com/health/; echo
```

✅ `{"status": "ok", ...}`

**Last safe rollback point.** If anything above failed, resume the Render service and stop here. Nothing has changed for users.

## Phase D: Switch users to AWS

**D1. Truehost:** edit the existing **`api`** CNAME record. Change the target from `api-beldium-backend-qd86.onrender.com` to:

```
ecs-express-gateway-alb-83671ec5-1377816147.us-east-1.elb.amazonaws.com
```

Don't touch the `_…api` validation record. The certificate needs it to renew.

**D2. Wait for the change to show:**

```bash
until dig +short api.beldium.com CNAME | grep -q elb.amazonaws.com; do sleep 15; done; echo "✅ DNS points to AWS"
curl -s https://api.beldium.com/health/; echo
```

**D3. Real check from the real frontends:** log in on `compliance.beldium.com`, `miners.beldium.com` and `logistics.beldium.com`, open an existing document, and upload a test one. **From here on, users write to AWS.** This is the point of no return.

Everyone has to log in again (production has its own secret key). Their passwords are unchanged.

## Phase E: Finish (same day)

1. **Close the tunnel:** Ctrl+C in Window 1.
2. **Stop Render from deploying:** Render → api-beldium-backend → Settings → **Auto-Deploy: No**. Keep the service suspended and the databases running for 1 to 2 weeks as a fallback archive.
3. **Merge to `main`:** open a pull request from `aws-migration` to `main` and merge it. GitHub runs the production deploy and **waits for your approval** (Actions tab → Review deployments → Approve). ✅ All steps green.
4. **Turn off tunnel access** on production and staging:

   ```bash
   for e in production staging; do
     aws ecs update-service --cluster beldium-$e --service beldium-$e-worker --no-enable-execute-command --force-new-deployment --query service.serviceName --output text
     aws iam delete-role-policy --role-name beldium-$e-ecs-task --policy-name ecs-exec
   done
   ```

5. The hourly logistics expiry job runs on production for the first time. Its first run may create a batch of expiry notices and restrictions for documents that expired while it never ran on Render.

## Rollback (before D3 only)

1. Truehost: point `api` back to `api-beldium-backend-qd86.onrender.com`.
2. Render: **Resume Service**.
3. AWS stays as it is; nothing there needs undoing. Investigate, then plan a new window.

## Afterwards (within 2 weeks)

- Create an IAM Identity Center admin user with MFA, and stop using the root login.
- Delete the temporary Backblaze key. Decide whether to keep the Backblaze bucket as an archive.
- Delete Render services and databases. Archive one final dump somewhere safe first.
- Delete customer data from your Mac: `rm -rf migration/dumps/*`, and `dropdb` for `copy_old copy_v2 merge_rehearsal copy_old_final copy_v2_final`.
- Wipe or refresh staging, which holds a copy of real customer data from the rehearsal.
- Optional: alarms (guide Step 19), `api-staging.beldium.com`.
