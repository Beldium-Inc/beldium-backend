# Onboarding and roles: full test guide

Covers BE-O1 to BE-O7: sector options, register a company, join by code, team rights, regulator access and `me/`. Allow about 45 minutes.

## 0. Setup (once)

Backend (this branch, `claude/onboarding-roles-40ae60`), from the worktree folder:

```bash
/Volumes/Stark/Beldium-New/beldium-backend/.venv/bin/python manage.py migrate
/Volumes/Stark/Beldium-New/beldium-backend/.venv/bin/python manage.py createsuperuser
CORS_ALLOWED_ORIGINS=http://localhost:5180 COMPLIANCE_PORTAL_ORIGINS=http://localhost:5180 FRONTEND_URL=http://localhost:5180 /Volumes/Stark/Beldium-New/beldium-backend/.venv/bin/python manage.py runserver 8000
```

Frontend (`feat/mining-logistics-pages`), with `VITE_API_URL=http://localhost:8000`:

```bash
bun run --cwd /Volumes/Stark/Beldium-New/beldium-new-frontend dev --port 5180
```

The superuser is the Beldium operator. You need four more accounts with real inboxes for the signup codes: **A** company admin, **B** staff member, **C** regulator, **D** outsider. Use a private window per person, or sign out between them.

Pass means every "Expect" line holds. Tick as you go.

## 1. Options come from the server (BE-O1)

1. Open `/onboarding/sector` and pick **Processing**.
   Expect: "Register a processing company", "Join an existing processing company", "Regulatory access", and the compliance partner side.
2. Repeat for **Mining** and **Logistics**.
   Expect: Register links out to the Miner or Logistics Portal. Logistics also shows "Driver".
3. Repeat for **Quality**.
   Expect: only the Q&C partner side, with six options.
4. In Django admin (`/admin/` > Onboarding options), untick **enabled** on one option and reload.
   Expect: the option disappears. Tick it back on.

## 2. Register a company (BE-O3)

As **A**: Processing > Register a processing company, then create the account and verify it.

1. Fill the Company step and press Next.
   Expect: no error. Admin shows the organisation with type "Processing company", and A as owner.
2. Fill Operations, Plants (at least one), Representative and Documents. Upload each required file.
   Expect: each file shows "Selected: …". A wrong file type is refused with a message.
3. Reload the page halfway through.
   Expect: your answers are still there, and no second company is created.
4. Tick the declarations, sign with your full name and press Submit.
   Expect: the Submitted screen appears, and the organisation shows "Under review" in admin.
5. Try to register a second processing company as A.
   Expect: refused ("one company per sector").

## 3. Join by code and the pending state (BE-O4, D8)

1. As **A**, open the Team page and copy the join code.
   Expect: A sees the code. Only admins can see it.
2. As **B**: Processing > Join an existing processing company, then enter the code.
   Expect: "Your request to join … is waiting for approval", with a Withdraw button.
3. As **B**, open any company page by URL.
   Expect: no company data at all.
4. As **B**, press **Withdraw request**.
   Expect: "No request is waiting". Then ask to join again.
5. As **A**, approve B on the Team page.
   Expect: B signs in and sees the workspace.

## 4. Admin versus staff (section 2)

As **B** (staff):

1. Open the Team page.
   Expect: names and roles only, with no emails, no invite button and no join code.
2. Try to change a role, suspend, remove or approve.
   Expect: "Only your company admin can do this", or a 403.

As **A** (admin):

3. See the team.
   Expect: emails are visible.
4. Promote B to admin, then demote B again.
   Expect: both changes work.
5. In admin, change A's membership to admin and delete any other admin. Then try to demote, suspend or remove A.
   Expect: refused (409 `last_admin`).

## 5. Operator verifies the company

As the **superuser**:

1. Open `/staff/compliance-vetting`, tab **Processing**, then **Company applications**.
   Expect: A's company is listed.
2. Press **Verify**.
   Expect: it leaves the queue, and the organisation is "Verified" in admin.

## 6. Regulator access (BE-O5, D9)

As **C**: any sector > Regulatory access.

1. Use a gmail address with no letter.
   Expect: "Attach a letter from your agency".
2. Use an official-looking address. Ask for Mining, Export and Processing, then Send.
   Expect: the Submitted screen appears.
3. Sign in as C to Mining.
   Expect: "waiting for a Beldium operator", and no access.
4. As the **superuser**, open Regulator access requests, then Review. Untick Processing and approve.
   Expect: "Access approved".
5. Sign in as C to Mining and open sites, licences and so on.
   Expect: read-only data. Any create or edit is refused.
6. Sign in as C to Export.
   Expect: the exporter register can be read but not edited.
7. Sign in as C to Processing.
   Expect: "read-only access to Mining, Export only".
8. Make a second request with another account and reject it with a reason.
   Expect: signing in shows "declined: <reason>".

## 7. Outsider and suspension

1. As **D**, open A's application or team page by URL.
   Expect: 404 or nothing shown.
2. As **A**, suspend B. Then sign in as B.
   Expect: "Your access to … is suspended. Ask <admin> to restore it."

## 8. Automated checks (optional)

```bash
/Volumes/Stark/Beldium-New/beldium-backend/.venv/bin/python manage.py test --parallel 4
```

Expect: 594 tests, OK.

## If something fails

Note the step number, the screen text and, from DevTools > Network, the failing request's URL, status and response body.
