from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from compliance.models import ComplianceApplication
from organisations.models import Organisation, OrganisationMembership


def logistics_payloads():
    return {
        'organisation': {'logistics_type': 'Mineral Haulage Company'},
        'services': {'selected_services': ['Bulk mineral haulage'], 'geographic_coverage': 'nationwide'},
        'capability': {'years_haulage_experience': 8, 'drivers_employed': 12, 'vehicles_operated': 9, 'dispatch_coordinators': 2},
        'operations': {'operates_own_fleet': True, 'active_vehicles': 9, 'gps_tracking_available': True,
                       'maximum_movements_per_month': 120, 'evidence_standards': ['GPS trip logs', 'Weighbridge tickets']},
        'conflict': {'owns_mineral_assets': False, 'serves_mining_companies': True, 'trades_minerals': False,
                     'relationships': 'None declared.', 'agreed': True},
    }


class LogisticsOnboardingSectionTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner@haulage.test', email_verified_at=timezone.now())
        self.member = User.objects.create_user('member@haulage.test', email_verified_at=timezone.now())
        self.outsider = User.objects.create_user('outsider@other.test', email_verified_at=timezone.now())
        self.org = Organisation.objects.create(name='Sahel Haulage', organisation_type='logistics_company', verification_status='under_review')
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role='owner')
        OrganisationMembership.objects.create(organisation=self.org, user=self.member, role='member')
        self.application = ComplianceApplication.objects.create(organisation=self.org, created_by=self.owner, sector='logistics')

    def url(self, section):
        return reverse('compliance-application-logistics-section', args=[self.application.id, section])

    def test_each_logistics_section_is_stored_under_its_own_key(self):
        self.client.force_authenticate(self.owner)
        for section, payload in logistics_payloads().items():
            with self.subTest(section=section):
                response = self.client.patch(self.url(section), {'data': payload}, format='json')
                self.assertEqual(response.status_code, 200, response.data)
        self.application.refresh_from_db()
        self.assertEqual(set(self.application.logistics_profile), set(logistics_payloads()))
        self.assertEqual(self.application.logistics_profile['capability']['drivers_employed'], 12)
        self.assertEqual(self.application.logistics_profile['services']['states_covered'], [])

    def test_invalid_logistics_answers_are_refused(self):
        self.client.force_authenticate(self.owner)
        cases = [
            ('services', {'selected_services': ['Gold smuggling'], 'geographic_coverage': 'nationwide'}),
            ('services', {'selected_services': ['Bulk mineral haulage'], 'geographic_coverage': 'selected_states', 'states_covered': []}),
            ('conflict', {**logistics_payloads()['conflict'], 'agreed': False}),
            ('operations', {'operates_own_fleet': True, 'evidence_standards': ['Crystal ball']}),
            ('capability', {'years_haulage_experience': 150}),
        ]
        for section, payload in cases:
            with self.subTest(section=section, payload=payload):
                response = self.client.patch(self.url(section), {'data': payload}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
        self.application.refresh_from_db()
        self.assertEqual(self.application.logistics_profile, {})

    def test_unknown_logistics_section_is_not_found(self):
        self.client.force_authenticate(self.owner)
        response = self.client.patch(self.url('mining-capability'), {'data': {}}, format='json')
        self.assertEqual(response.status_code, 404)

    def test_read_only_member_and_outsider_cannot_write_logistics_sections(self):
        for user, expected in [(self.member, 403), (self.outsider, 404)]:
            with self.subTest(user=user.email):
                self.client.force_authenticate(user)
                response = self.client.patch(self.url('services'), {'data': logistics_payloads()['services']}, format='json')
                self.assertEqual(response.status_code, expected)
        self.application.refresh_from_db()
        self.assertEqual(self.application.logistics_profile, {})

    def test_progress_counts_logistics_sections_not_mining_ones(self):
        self.client.force_authenticate(self.owner)
        before = self.client.get(reverse('compliance-application-detail', args=[self.application.id])).data['progress']
        self.assertFalse(before['sections']['capability'])
        self.assertNotIn('inspection_capability', before['sections'])

        self.client.patch(self.url('capability'), {'data': logistics_payloads()['capability']}, format='json')
        after = self.client.get(reverse('compliance-application-detail', args=[self.application.id])).data['progress']
        self.assertTrue(after['sections']['capability'])
        self.assertIn('services', after['sections'])
