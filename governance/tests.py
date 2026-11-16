from tempfile import TemporaryDirectory
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import AccountAuditEvent, User
from governance import models as m

R = m.GovernanceRole
BASE = "/api/v1/governance"


def make_user(email, **extra):
    return User.objects.create_user(
        email=email, password="Str0ng-Passw0rd!", email_verified_at=timezone.now(), first_name=email.split("@")[0], **extra
    )


def make_member(email, role, status=m.MemberStatus.ACTIVE):
    user = make_user(email)
    m.GovernanceMember.objects.create(user=user, role=role, status=status, organisation="Beldium Internal")
    return user


class GovernanceTestCase(APITestCase):
    def setUp(self):
        self.admin = make_member("admin@gov.test", R.SUPER_ADMIN)
        self.executive = make_member("exec@gov.test", R.EXECUTIVE)
        self.executive2 = make_member("exec2@gov.test", R.EXECUTIVE)
        self.analyst = make_member("analyst@gov.test", R.ANALYST)
        self.officer = make_member("officer@gov.test", R.RISK_OFFICER)
        self.reviewer = make_member("reviewer@gov.test", R.REVIEWER)
        self.viewer = make_member("viewer@gov.test", R.VIEWER)
        self.outsider = make_user("outsider@other.test")
        self.suspended = make_member("suspended@gov.test", R.EXECUTIVE, status=m.MemberStatus.SUSPENDED)

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def policy(self, author=None, **extra):
        author = author or self.analyst
        data = {
            "title": "Responsible Sourcing",
            "domain": "Supply Chain",
            "owner": str(author.pk),
            "executive_summary": "Minimum standards.",
            **extra,
        }
        response = self.as_(author).post(f"{BASE}/policies/", data, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def act(self, user, policy_id, action, comment=""):
        return self.as_(user).post(f"{BASE}/policies/{policy_id}/transition/", {"action": action, "comment": comment}, format="json")


class AccessTests(GovernanceTestCase):
    def test_non_member_and_suspended_member_are_refused(self):
        for user in [self.outsider, self.suspended]:
            for path in ["policies/", "risks/", "dashboard/", "compliance/", "documents/", "people/"]:
                response = self.as_(user).get(f"{BASE}/{path}")
                self.assertEqual(response.status_code, 403, (user.email, path))

    def test_anonymous_is_refused(self):
        self.assertEqual(self.client.get(f"{BASE}/policies/").status_code, 401)

    def test_me_reports_role_and_capabilities(self):
        data = self.as_(self.reviewer).get(f"{BASE}/me/").data
        self.assertEqual(data["role"], R.REVIEWER)
        self.assertTrue(data["capabilities"]["review"])
        self.assertFalse(data["capabilities"]["author"])
        self.assertFalse(data["capabilities"]["approve"])
        outsider = self.as_(self.outsider).get(f"{BASE}/me/").data
        self.assertEqual(outsider["status"], "none")
        self.assertIsNone(outsider["role"])

    def test_staff_without_membership_are_super_administrators_but_cannot_author(self):
        staff = make_user("staff@beldium.test", is_staff=True)
        self.assertEqual(self.as_(staff).get(f"{BASE}/me/").data["role"], R.SUPER_ADMIN)
        response = self.as_(staff).post(f"{BASE}/risks/", {}, format="json")
        self.assertIn(response.status_code, {400, 403})
        response = self.as_(staff).post(
            f"{BASE}/stakeholders/", {"name": "X", "category": "ngo", "influence": 1, "interest": 1, "owner": str(self.analyst.pk)}, format="json"
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_cannot_write_anything(self):
        client = self.as_(self.viewer)
        self.assertEqual(client.post(f"{BASE}/policies/", {"title": "X", "domain": "Y", "owner": str(self.viewer.pk)}, format="json").status_code, 403)
        self.assertEqual(
            client.post(f"{BASE}/stakeholders/", {"name": "X", "category": "ngo", "influence": 1, "interest": 1, "owner": str(self.analyst.pk)}, format="json").status_code,
            403,
        )

    def test_owner_must_be_an_active_member(self):
        response = self.as_(self.analyst).post(
            f"{BASE}/policies/", {"title": "X", "domain": "Y", "owner": str(self.outsider.pk)}, format="json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("owner", response.data["error"]["details"])

    def test_there_is_no_delete_path(self):
        policy = self.policy()
        self.assertEqual(self.as_(self.executive).delete(f"{BASE}/policies/{policy['id']}/").status_code, 405)


class PolicyWorkflowTests(GovernanceTestCase):
    def test_full_workflow_versions_and_events(self):
        policy = self.policy()
        self.assertEqual(policy["status"], "draft")
        self.assertEqual(policy["version"], "v0.1")
        pid = policy["id"]

        self.assertEqual(self.act(self.analyst, pid, "submit").status_code, 200)
        self.assertEqual(self.act(self.reviewer, pid, "start_review").status_code, 200)
        response = self.act(self.reviewer, pid, "request_revision", "Cite the OECD guidance.")
        self.assertEqual(response.data["status"], "revision_requested")

        edit = self.as_(self.analyst).patch(f"{BASE}/policies/{pid}/", {"objectives": "Cite OECD DDG."}, format="json")
        self.assertEqual(edit.status_code, 200)
        response = self.act(self.analyst, pid, "submit")
        self.assertEqual(response.data["version"], "v0.2")

        self.act(self.reviewer, pid, "start_review")
        self.act(self.reviewer, pid, "recommend")
        response = self.act(self.executive, pid, "approve")
        self.assertEqual(response.data["status"], "approved", response.data)
        response = self.act(self.executive, pid, "publish")
        self.assertEqual(response.data["status"], "published")
        self.assertEqual(response.data["version"], "v1.0")

        actions = [event["action"] for event in response.data["events"]]
        self.assertEqual(actions[-1], "create")
        self.assertIn("approve", actions)
        self.assertTrue(AccountAuditEvent.objects.filter(event_type="governance.policy_publish").exists())

    def test_author_cannot_approve_own_policy(self):
        policy = self.policy(author=self.executive)
        pid = policy["id"]
        self.act(self.executive, pid, "submit")
        self.act(self.reviewer, pid, "start_review")
        response = self.act(self.executive, pid, "approve")
        self.assertEqual(response.status_code, 403)
        self.assertNotIn("approve", self.as_(self.executive).get(f"{BASE}/policies/{pid}/").data["allowed_actions"])
        self.assertEqual(self.act(self.executive2, pid, "approve").status_code, 200)

    def test_wrong_role_is_refused_at_each_step(self):
        pid = self.policy()["id"]
        self.assertEqual(self.act(self.viewer, pid, "submit").status_code, 403)
        self.act(self.analyst, pid, "submit")
        self.assertEqual(self.act(self.analyst, pid, "start_review").status_code, 403)
        self.act(self.reviewer, pid, "start_review")
        self.assertEqual(self.act(self.reviewer, pid, "approve").status_code, 403)
        self.assertEqual(self.act(self.officer, pid, "approve").status_code, 403)

    def test_out_of_order_transition_is_a_conflict(self):
        pid = self.policy()["id"]
        response = self.act(self.executive, pid, "publish")
        self.assertEqual(response.status_code, 409)

    def test_rejection_and_revision_need_a_reason(self):
        pid = self.policy()["id"]
        self.act(self.analyst, pid, "submit")
        self.assertEqual(self.act(self.executive, pid, "reject").status_code, 400)
        self.assertEqual(self.act(self.reviewer, pid, "request_revision").status_code, 400)

    def test_policy_cannot_be_edited_once_submitted(self):
        pid = self.policy()["id"]
        self.act(self.analyst, pid, "submit")
        response = self.as_(self.analyst).patch(f"{BASE}/policies/{pid}/", {"title": "Changed"}, format="json")
        self.assertEqual(response.status_code, 409)


class RiskAndFindingTests(GovernanceTestCase):
    def risk(self, **extra):
        data = {
            "title": "Ransomware on ledger", "category": "cybersecurity", "sector": "warehousing",
            "owner": str(self.officer.pk), "likelihood": 3, "impact": 4,
            "residual_likelihood": 2, "residual_impact": 3, "control_effectiveness": "partial", **extra,
        }
        return self.as_(self.officer).post(f"{BASE}/risks/", data, format="json")

    def test_scores_and_validation(self):
        response = self.risk()
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["inherent_score"], 12)
        self.assertEqual(response.data["residual_score"], 6)
        self.assertEqual(self.risk(residual_likelihood=5, residual_impact=5).status_code, 400)
        self.assertEqual(self.risk(likelihood=6).status_code, 400)

    def test_escalation_and_closure_rules(self):
        rid = self.risk().data["id"]
        self.assertEqual(self.as_(self.analyst).post(f"{BASE}/risks/{rid}/escalate/", {"comment": "x"}, format="json").status_code, 403)
        self.assertEqual(self.as_(self.officer).post(f"{BASE}/risks/{rid}/escalate/", {}, format="json").status_code, 400)
        response = self.as_(self.officer).post(f"{BASE}/risks/{rid}/escalate/", {"comment": "Board attention."}, format="json")
        self.assertEqual(response.data["status"], "escalated")
        self.assertEqual(self.as_(self.executive).post(f"{BASE}/risks/{rid}/set-status/", {"status": "closed"}, format="json").status_code, 400)
        response = self.as_(self.executive).post(f"{BASE}/risks/{rid}/set-status/", {"status": "closed", "comment": "Mitigated."}, format="json")
        self.assertEqual(response.data["status"], "closed")
        self.assertEqual(self.as_(self.officer).patch(f"{BASE}/risks/{rid}/", {"title": "x"}, format="json").status_code, 400)

    def test_finding_verified_by_someone_else(self):
        response = self.as_(self.analyst).post(
            f"{BASE}/findings/", {"requirement": "Mining Act s.42", "authority": "Ministry of Mines", "gap": "CDA missing"}, format="json"
        )
        fid = response.data["id"]
        executive_finding = self.as_(self.executive).post(
            f"{BASE}/findings/", {"requirement": "Data Act", "authority": "DPC"}, format="json"
        ).data["id"]
        self.assertEqual(self.as_(self.executive).post(f"{BASE}/findings/{executive_finding}/verify/", {}, format="json").status_code, 403)
        self.assertEqual(self.as_(self.analyst).post(f"{BASE}/findings/{fid}/verify/", {}, format="json").status_code, 403)
        self.assertEqual(self.as_(self.reviewer).post(f"{BASE}/findings/{fid}/close/", {"resolution": "x"}, format="json").status_code, 409)
        response = self.as_(self.reviewer).post(f"{BASE}/findings/{fid}/verify/", {}, format="json")
        self.assertEqual(response.data["status"], "verified")
        self.assertEqual(self.as_(self.analyst).patch(f"{BASE}/findings/{fid}/", {"gap": "y"}, format="json").status_code, 400)
        response = self.as_(self.reviewer).post(f"{BASE}/findings/{fid}/close/", {"resolution": "Filed."}, format="json")
        self.assertEqual(response.data["status"], "closed")


class MilestoneAndAssessmentTests(GovernanceTestCase):
    def test_milestone_dates_and_progress_must_agree(self):
        pid = self.policy()["id"]
        base = {"policy": pid, "name": "Pilot", "phase": "pilot", "owner": str(self.analyst.pk), "start_date": "2026-12-01", "end_date": "2027-03-01"}
        client = self.as_(self.analyst)
        self.assertEqual(client.post(f"{BASE}/milestones/", {**base, "end_date": "2026-11-01"}, format="json").status_code, 400)
        self.assertEqual(client.post(f"{BASE}/milestones/", {**base, "status": "complete", "progress": 50}, format="json").status_code, 400)
        self.assertEqual(client.post(f"{BASE}/milestones/", {**base, "status": "on_track", "progress": 40}, format="json").status_code, 201)

    def test_scenario_scores_are_validated(self):
        assessment = self.as_(self.analyst).post(f"{BASE}/assessments/", {"title": "Royalty reform"}, format="json").data
        client = self.as_(self.analyst)
        good = {"assessment": assessment["id"], "name": "A", "cost": "10", "benefit": "25", "scores": {"economic": 3}}
        response = client.post(f"{BASE}/scenarios/", good, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["benefit_cost_ratio"], "2.50")
        self.assertEqual(client.post(f"{BASE}/scenarios/", {**good, "scores": {"economic": 9}}, format="json").status_code, 400)
        self.assertEqual(client.post(f"{BASE}/scenarios/", {**good, "scores": {"made_up": 2}}, format="json").status_code, 400)


@override_settings(STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"}, "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}})
class DocumentTests(GovernanceTestCase):
    def setUp(self):
        super().setUp()
        self.media = TemporaryDirectory()
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()

    def tearDown(self):
        self.override.disable()
        self.media.cleanup()

    def upload(self, user, classification):
        file = SimpleUploadedFile("report.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        return self.as_(user).post(
            f"{BASE}/documents/", {"name": f"{classification} report", "classification": classification, "document_type": "risk_report", "file": file}, format="multipart"
        )

    def test_classification_limits_what_each_role_sees(self):
        self.assertEqual(self.upload(self.officer, "confidential").status_code, 201)
        self.assertEqual(self.upload(self.officer, "restricted").status_code, 201)
        self.assertEqual(self.upload(self.officer, "internal").status_code, 201)
        self.assertEqual(len(self.as_(self.executive).get(f"{BASE}/documents/").data["results"]), 3)
        self.assertEqual(len(self.as_(self.reviewer).get(f"{BASE}/documents/").data["results"]), 2)
        viewer_docs = self.as_(self.viewer).get(f"{BASE}/documents/").data["results"]
        self.assertEqual([d["classification"] for d in viewer_docs], ["internal"])
        confidential = m.GovernanceDocument.objects.get(classification="confidential")
        self.assertEqual(self.as_(self.viewer).get(f"{BASE}/documents/{confidential.pk}/download/").status_code, 404)
        self.assertEqual(self.as_(self.executive).get(f"{BASE}/documents/{confidential.pk}/download/").status_code, 200)

    def test_wrong_file_type_and_wrong_role_are_refused(self):
        bad = SimpleUploadedFile("script.exe", b"MZ", content_type="application/octet-stream")
        response = self.as_(self.officer).post(f"{BASE}/documents/", {"name": "x", "classification": "internal", "file": bad}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.upload(self.viewer, "internal").status_code, 403)
        self.assertEqual(self.upload(self.reviewer, "confidential").status_code, 400)


class InvitationAndAccessRequestTests(GovernanceTestCase):
    @mock.patch("accounts.services.enqueue_account_email")
    def test_invitation_lifecycle(self, enqueue):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.as_(self.admin).post(
                f"{BASE}/invitations/", {"email": "New@Person.test", "name": "New Person", "organisation": "DFI", "role": R.REVIEWER}, format="json"
            )
        self.assertEqual(response.status_code, 201, response.data)
        token = response.data["token"]
        enqueue.assert_called_once()
        self.assertEqual(enqueue.call_args.args[0], "send_governance_invitation_email")
        self.assertNotIn(token, str(m.GovernanceInvitation.objects.values_list("token_hash", flat=True)))

        self.client.force_authenticate(None)
        lookup = self.client.post(f"{BASE}/invitations/lookup/", {"token": token}, format="json")
        self.assertEqual(lookup.data["email"], "new@person.test")

        self.assertEqual(self.as_(self.outsider).post(f"{BASE}/invitations/accept/", {"token": token}, format="json").status_code, 403)
        invitee = make_user("new@person.test")
        response = self.as_(invitee).post(f"{BASE}/invitations/accept/", {"token": token}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["role"], R.REVIEWER)
        self.assertEqual(self.as_(invitee).get(f"{BASE}/policies/").status_code, 200)
        self.assertEqual(self.as_(invitee).post(f"{BASE}/invitations/accept/", {"token": token}, format="json").status_code, 409)

    @mock.patch("accounts.services.enqueue_account_email")
    def test_only_super_admin_manages_invitations(self, _enqueue):
        payload = {"email": "x@y.test", "name": "X", "organisation": "Y", "role": R.VIEWER}
        self.assertEqual(self.as_(self.executive).post(f"{BASE}/invitations/", payload, format="json").status_code, 403)
        self.assertEqual(self.as_(self.executive).get(f"{BASE}/invitations/").status_code, 403)
        created = self.as_(self.admin).post(f"{BASE}/invitations/", payload, format="json")
        self.assertEqual(self.as_(self.admin).post(f"{BASE}/invitations/", payload, format="json").status_code, 409)
        resent = self.as_(self.admin).post(f"{BASE}/invitations/{created.data['id']}/resend/", format="json")
        self.assertEqual(resent.data["sent_count"], 2)
        self.assertNotEqual(resent.data["token"], created.data["token"])
        # The old link stops working once a fresh one is sent.
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(f"{BASE}/invitations/lookup/", {"token": created.data["token"]}, format="json").status_code, 404)
        revoked = self.as_(self.admin).post(f"{BASE}/invitations/{created.data['id']}/revoke/", format="json")
        self.assertEqual(revoked.data["status"], "revoked")

    def test_access_request_and_decision(self):
        payload = {
            "organisation": "Regulatory Institution", "job_title": "Reviewer", "requested_role": R.REVIEWER,
            "profile": {"full_name": "Out Sider"}, "scope": ["Risk Management", "Mining"],
            "declarations": {"conflict_of_interest": True, "confidentiality": True, "code_of_conduct": True},
        }
        self.assertEqual(
            self.as_(self.outsider).post(f"{BASE}/access-requests/", {**payload, "requested_role": R.EXECUTIVE}, format="json").status_code, 400
        )
        self.assertEqual(
            self.as_(self.outsider).post(f"{BASE}/access-requests/", {**payload, "declarations": {"confidentiality": True}}, format="json").status_code, 400
        )
        response = self.as_(self.outsider).post(f"{BASE}/access-requests/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.as_(self.outsider).get(f"{BASE}/policies/").status_code, 403)
        member_id = response.data["id"]

        self.assertEqual(self.as_(self.executive).post(f"{BASE}/members/{member_id}/decide/", {"status": "active"}, format="json").status_code, 403)
        self.assertEqual(self.as_(self.admin).post(f"{BASE}/members/{member_id}/decide/", {"status": "suspended"}, format="json").status_code, 409)
        response = self.as_(self.admin).post(f"{BASE}/members/{member_id}/decide/", {"status": "active"}, format="json")
        self.assertEqual(response.data["status"], "active")
        # A fresh instance, as a new request would load: the role is cached per request.
        outsider = User.objects.get(pk=self.outsider.pk)
        self.assertEqual(self.as_(outsider).get(f"{BASE}/policies/").status_code, 200)

    def test_admin_cannot_change_own_access(self):
        own = m.GovernanceMember.objects.get(user=self.admin)
        response = self.as_(self.admin).post(f"{BASE}/members/{own.pk}/decide/", {"status": "suspended"}, format="json")
        self.assertEqual(response.status_code, 403)


class IntelligenceAndReportTests(GovernanceTestCase):
    def test_compliance_lists_every_sector(self):
        response = self.as_(self.viewer).get(f"{BASE}/compliance/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row["sector"] for row in response.data["sectors"]],
            ["mining", "quality", "logistics", "warehousing", "processing", "export", "marketplace"],
        )
        self.assertTrue(all(row["available"] for row in response.data["sectors"]), response.data)
        processing = next(row for row in response.data["sectors"] if row["sector"] == "processing")
        self.assertIsNone(processing["open_requests"])

    def test_dashboard_and_sector_filter(self):
        self.policy(sector="mining")
        response = self.as_(self.viewer).get(f"{BASE}/dashboard/?sector=mining")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["risk_trend"]), 6)
        self.assertEqual(response.data["recent_activity"], [])
        self.assertTrue(self.as_(self.executive).get(f"{BASE}/dashboard/").data["recent_activity"])
        self.assertEqual(self.as_(self.viewer).get(f"{BASE}/dashboard/?sector=moon").status_code, 400)

    def test_reports_render_and_are_audited(self):
        self.policy()
        pdf = self.as_(self.analyst).get(f"{BASE}/reports/executive/?output=pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        csv_response = self.as_(self.analyst).get(f"{BASE}/reports/risk/?output=csv")
        self.assertEqual(csv_response["Content-Type"], "text/csv")
        self.assertEqual(self.as_(self.viewer).get(f"{BASE}/reports/risk/").status_code, 403)
        self.assertEqual(self.as_(self.analyst).get(f"{BASE}/reports/nope/").status_code, 404)
        self.assertEqual(AccountAuditEvent.objects.filter(event_type="governance.report_downloaded").count(), 2)

    def test_audit_is_for_admins_and_executives(self):
        self.policy()
        self.assertEqual(self.as_(self.analyst).get(f"{BASE}/audit/").status_code, 403)
        rows = self.as_(self.executive).get(f"{BASE}/audit/").data
        self.assertEqual(rows[0]["event"], "policy_created")

    def test_copilot_cites_only_register_records(self):
        self.policy(title="Responsible Mineral Sourcing")
        response = self.as_(self.viewer).post(f"{BASE}/copilot/", {"question": "What is our mineral sourcing position?"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["requires_review"])
        self.assertEqual(response.data["citations"][0]["kind"], "policy")
        empty = self.as_(self.viewer).post(f"{BASE}/copilot/", {"question": "zebra quantum"}, format="json")
        self.assertEqual(empty.data["citations"], [])
