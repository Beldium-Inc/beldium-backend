from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import User


class AuthenticationTests(APITestCase):
    def test_register_login_and_read_profile(self):
        response = self.client.post(reverse("register"), {
            "email": "owner@example.com",
            "password": "SafePassword-2026!",
            "first_name": "Ada",
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        user = User.objects.get(email="owner@example.com")
        self.assertTrue(user.check_password("SafePassword-2026!"))

        response = self.client.post(reverse("token"), {
            "email": "owner@example.com",
            "password": "SafePassword-2026!",
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        response = self.client.get(reverse("current-user"))
        self.assertEqual(response.data["email"], "owner@example.com")
