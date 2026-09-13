"""Access-control and CRUD coverage for ProductionRecord and InventoryItem.

Reuses ``MiningTestCase`` from ``mining.tests`` for the three-audience setup
(operator desk / regulator / miner org / outsider org), matching the pattern
in ``mining.test_access_control_regressions``.
"""
from django.urls import reverse

from mining.models import InventoryItem, ProductionRecord
from mining.tests import MiningTestCase


class ProductionRecordAccessTests(MiningTestCase):
    def test_operator_can_list_and_create_a_production_record_for_their_own_site(self):
        self.client.force_authenticate(self.miner)
        r = self.client.post(reverse("mining-production-list"), {
            "site": str(self.site.id),
            "period_start": "2026-01-01",
            "period_end": "2026-01-31",
            "commodity": "Tin",
            "tonnage": "120.50",
            "grade": "1.250",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(ProductionRecord.objects.filter(site=self.site).count(), 1)

        listed = self.client.get(reverse("mining-production-list"))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["count"], 1)

    def test_outsider_gets_empty_queryset_and_403_on_create_for_another_orgs_site(self):
        ProductionRecord.objects.create(
            site=self.site, period_start="2026-01-01", period_end="2026-01-31",
            commodity="Tin", tonnage="50.00",
        )
        self.client.force_authenticate(self.outsider)

        listed = self.client.get(reverse("mining-production-list"))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["count"], 0)

        r = self.client.post(reverse("mining-production-list"), {
            "site": str(self.site.id),
            "period_start": "2026-02-01",
            "period_end": "2026-02-28",
            "commodity": "Tin",
            "tonnage": "10.00",
        }, format="json")
        self.assertEqual(r.status_code, 403, r.data)

    def test_operator_desk_sees_the_whole_register(self):
        ProductionRecord.objects.create(
            site=self.site, period_start="2026-01-01", period_end="2026-01-31",
            commodity="Tin", tonnage="50.00",
        )
        self.client.force_authenticate(self.operator)
        r = self.client.get(reverse("mining-production-list"))
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data["count"], 1)


class InventoryItemAccessTests(MiningTestCase):
    def test_operator_can_list_and_create_an_inventory_item_for_their_own_site(self):
        self.client.force_authenticate(self.miner)
        r = self.client.post(reverse("mining-inventory-list"), {
            "site": str(self.site.id),
            "category": "stockpile",
            "name": "Cassiterite concentrate",
            "quantity": "35.00",
            "unit": "tonnes",
            "threshold": "10.00",
        }, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(InventoryItem.objects.filter(site=self.site).count(), 1)

        listed = self.client.get(reverse("mining-inventory-list"))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["count"], 1)

    def test_outsider_gets_empty_queryset_and_403_on_create_for_another_orgs_site(self):
        InventoryItem.objects.create(
            site=self.site, category="consumable", name="Diesel", quantity="500.00", unit="litres",
        )
        self.client.force_authenticate(self.outsider)

        listed = self.client.get(reverse("mining-inventory-list"))
        self.assertEqual(listed.status_code, 200, listed.data)
        self.assertEqual(listed.data["count"], 0)

        r = self.client.post(reverse("mining-inventory-list"), {
            "site": str(self.site.id),
            "category": "spare",
            "name": "Conveyor belt",
            "quantity": "1.00",
            "unit": "unit",
        }, format="json")
        self.assertEqual(r.status_code, 403, r.data)

    def test_outsider_cannot_update_another_orgs_inventory_item(self):
        item = InventoryItem.objects.create(
            site=self.site, category="consumable", name="Diesel", quantity="500.00", unit="litres",
        )
        self.client.force_authenticate(self.outsider)
        r = self.client.patch(reverse("mining-inventory-detail", args=[item.id]), {"quantity": "1.00"}, format="json")
        self.assertEqual(r.status_code, 404, r.data)
        item.refresh_from_db()
        self.assertEqual(str(item.quantity), "500.00")
