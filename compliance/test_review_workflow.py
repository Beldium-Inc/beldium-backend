from datetime import timedelta
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from compliance.models import (ApprovalCondition, ApplicationMessage, ComplianceApplication,
                               ComplianceDocument, Personnel, REQUIRED_DOCUMENTS)
from organisations.models import Organisation, OrganisationMembership


class ReviewWorkflowTests(APITestCase):
    def setUp(self):
        self.media = TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)
        self.owner = User.objects.create_user('owner@review.test', email_verified_at=timezone.now())
        self.staff = User.objects.create_user('staff@review.test', is_staff=True)
        self.member = User.objects.create_user('member@review.test', email_verified_at=timezone.now())
        self.outsider = User.objects.create_user('outsider@review.test')
        self.org = Organisation.objects.create(name='Review Org', organisation_type='compliance_partner', verification_status='under_review')
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role='owner')
        OrganisationMembership.objects.create(organisation=self.org, user=self.member, role='member')
        self.app = ComplianceApplication.objects.create(
            organisation=self.org, created_by=self.owner, status='under_review',
            organisation_profile={'name': 'Review Org'}, representative={'authorised': True},
            services={'selected_services': ['inspection']}, professional_capability={'years_mining_experience': 5},
            inspection_capability={'conducts_physical_inspections': True}, conflict_declaration={'agreed': True},
            declaration={'confirmed': True}, submitted_at=timezone.now())
        Personnel.objects.create(application=self.app, full_name='Inspector', role='Inspector')
        for key, title in REQUIRED_DOCUMENTS:
            ComplianceDocument.objects.create(application=self.app, document_type=key, title=title,
                                              file=self.upload(), status='verified')
        self.client.force_authenticate(self.staff)
        self.deadline = (timezone.localdate() + timedelta(days=30)).isoformat()

    def upload(self):
        return SimpleUploadedFile('evidence.pdf', b'%PDF-1.4 evidence', content_type='application/pdf')

    def url(self, action, *args):
        return reverse('compliance-application-' + action, args=[self.app.id, *args])

    def conditionally_approve(self):
        response = self.client.post(self.url('decide'), {'status': 'conditionally_approved', 'conditions': [
            {'title': 'Insurance', 'description': 'Renew insurance', 'due_date': self.deadline}]}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        return ApprovalCondition.objects.get(application=self.app)

    def test_condition_evidence_rejection_replacement_clearance_and_final_approval(self):
        condition = self.conditionally_approve()
        blocked = self.client.post(self.url('decide'), {'status': 'verified'})
        self.assertEqual(blocked.data['error']['code'], 'conditions_not_cleared')
        self.client.force_authenticate(self.owner)
        uploaded = self.client.post(self.url('condition-evidence', condition.id), {'file': self.upload()}, format='multipart')
        self.assertEqual(uploaded.status_code, 201, uploaded.data)
        self.client.force_authenticate(self.staff)
        rejected = self.client.post(self.url('review-condition-evidence', condition.id, uploaded.data['id']), {'status': 'rejected', 'notes': 'Expired'})
        self.assertEqual(rejected.data['status'], 'rejected')
        self.client.force_authenticate(self.owner)
        replacement = self.client.post(self.url('condition-evidence', condition.id), {'file': self.upload()}, format='multipart')
        self.assertEqual(replacement.status_code, 201)
        self.client.force_authenticate(self.staff)
        reviewed = self.client.post(self.url('review-condition-evidence', condition.id, replacement.data['id']), {'status': 'verified'})
        self.assertEqual(reviewed.data['status'], 'cleared')
        self.assertEqual(condition.evidence.count(), 2)
        verified = self.client.post(self.url('decide'), {'status': 'verified'})
        self.assertEqual(verified.status_code, 200, verified.data)
        self.org.refresh_from_db()
        self.assertEqual(self.org.verification_status, 'verified')
        self.assertIsNotNone(self.org.verified_at)
        self.assertEqual(self.client.post(self.url('decide'), {'status': 'rejected'}).status_code, 409)

    def test_additional_document_deadline_and_resubmission(self):
        requested = self.client.post(self.url('request-document'), {'document_type': 'insurance', 'title': 'Insurance', 'due_date': self.deadline})
        self.assertEqual(requested.status_code, 201, requested.data)
        self.assertEqual(requested.data['due_date'], self.deadline)
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.url('submit'))
        self.assertEqual(response.status_code, 409)
        self.assertIn('insurance', response.data['error']['details']['documents']['outstanding'])
        self.assertEqual(self.client.post(self.url('documents'), {'document_type': 'insurance'}, format='multipart').status_code, 400)
        upload = self.client.post(self.url('documents'), {'document_type': 'insurance', 'file': self.upload()}, format='multipart')
        self.assertEqual(upload.status_code, 200, upload.data)
        self.assertEqual(self.client.post(self.url('submit')).status_code, 200)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(self.url('decide'), {'status': 'verified'}).data['error']['code'], 'documents_not_verified')
        self.assertEqual(self.client.post(self.url('review-document', upload.data['id']), {'status': 'verified'}).status_code, 200)
        self.assertEqual(self.client.post(self.url('decide'), {'status': 'verified'}).status_code, 200)

    def test_rejected_document_blocks_resubmission_and_preserves_old_file(self):
        doc = self.app.documents.first()
        old_name = doc.file.name
        requested = self.client.post(self.url('request-document'), {'document_type': doc.document_type, 'title': doc.title})
        self.assertEqual(requested.status_code, 201)
        doc.refresh_from_db()
        self.assertEqual(doc.file.name, old_name)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url('submit')).status_code, 409)

    def test_messages_are_read_per_user_and_internal_messages_remain_private(self):
        message = ApplicationMessage.objects.create(application=self.app, author=self.staff, body='Please respond')
        ApplicationMessage.objects.create(application=self.app, author=self.staff, body='Internal', is_internal=True)
        for user in [self.owner, self.member]:
            self.client.force_authenticate(user)
            dashboard = self.client.get(reverse('dashboard-list'))
            self.assertEqual(dashboard.data['applications'][0]['unread_messages'], 1)
            listing = self.client.get(self.url('messages'))
            self.assertEqual(len(listing.data), 1)
            self.assertIsNone(listing.data[0]['read_at'])
            self.assertEqual(self.client.post(self.url('mark-messages-read')).data['marked_read'], 1)
            self.assertEqual(self.client.post(self.url('mark-messages-read')).data['marked_read'], 0)
            self.assertIsNotNone(self.client.get(self.url('messages')).data[0]['read_at'])
        self.assertEqual(message.read_receipts.count(), 2)
        self.assertEqual(self.client.post(self.url('messages'), {'body': 'Private', 'is_internal': True}, format='json').status_code, 400)

    def test_unverified_applicant_blocks_submission_and_progress(self):
        self.owner.email_verified_at = None
        self.owner.save()
        self.app.status = 'draft'
        self.app.save()
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.url('submit'))
        self.assertEqual(response.status_code, 409)
        self.assertFalse(response.data['error']['details']['sections']['account'])

    def test_mineral_experience_is_validated_and_persisted(self):
        self.app.status = 'draft'
        self.app.save()
        self.client.force_authenticate(self.owner)
        response = self.client.patch(self.url('professional-capability-section'), {'data': {'years_mining_experience': 4, 'mineral_experience': ['Gold', 'Tin']}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['professional_capability']['mineral_experience'], ['Gold', 'Tin'])
        invalid = self.client.patch(self.url('professional-capability-section'), {'data': {'years_mining_experience': 4, 'mineral_experience': ['Gold', 'gold']}}, format='json')
        self.assertEqual(invalid.status_code, 400)

    def test_invalid_transitions_and_legacy_organisation_bypass(self):
        self.assertEqual(self.client.post(reverse('organisation-decide', args=[self.org.id]), {'decision': 'verified'}).status_code, 409)
        self.assertEqual(self.client.post(self.url('decide'), {'status': 'under_review'}).status_code, 409)
        self.app.status = 'draft'
        self.app.save()
        self.assertEqual(self.client.post(self.url('decide'), {'status': 'verified'}).status_code, 409)
        self.assertEqual(self.client.post(self.url('request-document'), {'document_type': 'insurance', 'title': 'Insurance'}).status_code, 409)

    def test_deadlines_required_for_conditions_and_validated_for_requests(self):
        self.assertEqual(self.client.post(self.url('decide'), {'status': 'conditionally_approved'}).status_code, 400)
        past = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.client.post(self.url('request-document'), {'document_type': 'insurance', 'title': 'Insurance', 'due_date': past}).status_code, 400)
        self.assertEqual(self.client.post(self.url('conditions'), {'title': 'Insurance', 'description': 'Renew', 'due_date': past}).status_code, 400)
        self.assertFalse(self.app.conditions.exists())

    def test_evidence_permissions_and_duplicate_review(self):
        condition = self.conditionally_approve()
        for user, expected in [(self.outsider, 404), (self.member, 403)]:
            self.client.force_authenticate(user)
            response = self.client.post(self.url('condition-evidence', condition.id), {'file': self.upload()}, format='multipart')
            self.assertEqual(response.status_code, expected, response.data)
        self.client.force_authenticate(self.owner)
        uploaded = self.client.post(self.url('condition-evidence', condition.id), {'file': self.upload()}, format='multipart')
        review_url = self.url('review-condition-evidence', condition.id, uploaded.data['id'])
        self.assertEqual(self.client.post(review_url, {'status': 'verified'}).status_code, 403)
        self.assertEqual(self.client.post(self.url('condition-evidence', condition.id), {'file': self.upload()}, format='multipart').status_code, 409)
        self.client.force_authenticate(self.staff)
        self.assertEqual(self.client.post(review_url, {'status': 'verified'}).status_code, 200)
        self.assertEqual(self.client.post(review_url, {'status': 'verified'}).status_code, 409)

    def test_overdue_items_are_visible_and_can_still_be_remedied(self):
        condition = self.conditionally_approve()
        condition.due_date = timezone.localdate() - timedelta(days=1)
        condition.save()
        self.client.force_authenticate(self.owner)
        dashboard = self.client.get(reverse('dashboard-list'))
        self.assertTrue(dashboard.data['applications'][0]['conditions'][0]['is_overdue'])
        self.assertEqual(self.client.post(self.url('condition-evidence', condition.id), {'file': self.upload()}, format='multipart').status_code, 201)

    def test_anonymous_access_is_rejected(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url('detail')).status_code, 401)

    def test_personnel_delete_route_works_but_application_delete_is_disabled(self):
        self.app.status = 'draft'
        self.app.save()
        self.client.force_authenticate(self.owner)
        person = self.app.personnel.first()
        self.assertEqual(self.client.delete(self.url('personnel-detail', person.id)).status_code, 204)
        self.assertEqual(self.client.delete(self.url('detail')).status_code, 405)

    def test_legacy_text_conditions_must_be_structured_before_verification(self):
        self.app.status = 'conditionally_approved'
        self.app.conditional_requirements = 'Renew insurance'
        self.app.save()
        blocked = self.client.post(self.url('decide'), {'status': 'verified'})
        self.assertEqual(blocked.data['error']['code'], 'conditions_required')
        created = self.client.post(self.url('conditions'), {'title': 'Insurance', 'description': 'Renew insurance', 'due_date': self.deadline})
        self.assertEqual(created.status_code, 201)
        self.assertEqual(self.client.post(self.url('decide'), {'status': 'verified'}).data['error']['code'], 'conditions_not_cleared')

    def test_rejected_document_returns_application_to_action_required(self):
        doc = self.app.documents.first()
        doc.status = 'submitted'
        doc.save()
        rejected = self.client.post(self.url('review-document', doc.id), {'status': 'rejected', 'notes': 'Replace expired document'})
        self.assertEqual(rejected.status_code, 200)
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'action_required')
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url('submit')).status_code, 409)

    def test_failed_request_rolls_back_document_changes(self):
        from unittest.mock import patch
        from common.exceptions import ConflictError
        with patch('compliance.views.transition', side_effect=ConflictError('Concurrent state change')):
            response = self.client.post(self.url('request-document'), {'document_type': 'insurance', 'title': 'Insurance'})
        self.assertEqual(response.status_code, 409)
        self.assertFalse(self.app.documents.filter(document_type='insurance').exists())
        self.app.refresh_from_db()
        self.assertEqual(self.app.status, 'under_review')

    def test_declaration_date_is_stored_as_json(self):
        self.app.status = 'draft'
        self.app.save()
        self.client.force_authenticate(self.owner)
        data = dict.fromkeys(['accuracy_confirmed', 'documents_genuine', 'compliance_agreed', 'disclose_changes', 'authorised', 'confirmed'], True)
        data['declaration_date'] = timezone.localdate().isoformat()
        response = self.client.patch(self.url('declaration-section'), {'data': data}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.app.refresh_from_db()
        self.assertEqual(self.app.declaration['declaration_date'], data['declaration_date'])
