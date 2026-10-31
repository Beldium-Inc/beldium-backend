from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import User
from compliance.models import ComplianceApplication
from organisations.models import Organisation, OrganisationMembership


class LogisticsPartnerProgressTests(APITestCase):
    """A compliance partner in the logistics sector answers the shared sections."""

    def setUp(self):
        self.owner = User.objects.create_user('owner@desk.test', email_verified_at=timezone.now())
        self.org = Organisation.objects.create(name='Logistics Desk', organisation_type='compliance_partner')
        OrganisationMembership.objects.create(organisation=self.org, user=self.owner, role='owner')
        self.application = ComplianceApplication.objects.create(
            organisation=self.org, created_by=self.owner, sector='logistics',
            organisation_profile={'name': 'Logistics Desk'}, representative={'authorised': True},
            services={'selected_services': ['Inspection']}, professional_capability={'years_mining_experience': 5},
            inspection_capability={'conducts_physical_inspections': True}, conflict_declaration={'agreed': True},
            declaration={'confirmed': True},
        )

    def test_shared_sections_count_towards_progress(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(reverse('compliance-application-detail', args=[self.application.id]))
        sections = response.data['progress']['sections']
        for name in ['organisation', 'inspection_capability', 'conflict_declaration', 'declaration']:
            self.assertTrue(sections[name], name)
        self.assertNotIn('capability', sections)

    def test_the_haulage_section_route_is_gone(self):
        with self.assertRaises(NoReverseMatch):
            reverse('compliance-application-logistics-section', args=[self.application.id, 'services'])
