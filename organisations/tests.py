from datetime import timedelta

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User
from organisations.models import JoinRequest, Organisation, OrganisationInvitation, OrganisationMembership


class OrganisationAPITests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner@example.com", "SafePassword-2026!")
        self.member = User.objects.create_user("member@example.com", "SafePassword-2026!")

    def test_creator_becomes_owner_and_other_users_cannot_see_private_org(self):
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("organisation-list"), {
            "name": "Nasarawa Lithium Minerals Ltd",
            "organisation_type": "mining_company",
            "registration_number": "RC-1428903",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        organisation = Organisation.objects.get()
        self.assertTrue(OrganisationMembership.objects.filter(
            organisation=organisation, user=self.owner, role="owner"
        ).exists())

        self.client.force_authenticate(self.member)
        response = self.client.get(reverse("organisation-list"))
        self.assertEqual(response.data["count"], 0)

    def test_admin_can_approve_join_request(self):
        organisation = Organisation.objects.create(name="Beldium Compliance", organisation_type="compliance_partner")
        OrganisationMembership.objects.create(organisation=organisation, user=self.owner, role="owner")
        join_request = JoinRequest.objects.create(
            organisation=organisation, requester=self.member, requested_role="reviewer"
        )
        self.client.force_authenticate(self.owner)
        response = self.client.post(reverse("join-request-decide", args=[join_request.id]), {"decision": "approved"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(OrganisationMembership.objects.filter(
            organisation=organisation, user=self.member, role="reviewer", is_active=True
        ).exists())

    def test_invitation_can_only_be_accepted_by_matching_email(self):
        organisation = Organisation.objects.create(name="Federal Regulator", organisation_type="regulator")
        OrganisationMembership.objects.create(organisation=organisation, user=self.owner, role="owner")
        invitation = OrganisationInvitation.objects.create(
            organisation=organisation,
            email=self.member.email,
            role="inspector",
            invited_by=self.owner,
            expires_at=timezone.now() + timedelta(days=2),
        )
        self.client.force_authenticate(self.member)
        response = self.client.post(reverse("accept-invitation"), {"token": invitation.token})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(OrganisationMembership.objects.filter(
            organisation=organisation, user=self.member, role="inspector"
        ).exists())
