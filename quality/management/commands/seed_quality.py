from django.core.management.base import BaseCommand

from organisations.models import Organisation, OrganisationType
from quality.models import BuyerSpec


class Command(BaseCommand):
    help = "Seed minimum quality buyer specifications for development and staging."

    def handle(self, *args, **options):
        buyer, _ = Organisation.objects.get_or_create(
            name="Beldium Reference Buyer",
            defaults={
                "organisation_type": OrganisationType.COMPLIANCE_PARTNER,
                "registration_number": "BLD-QUALITY-BUYER",
                "verification_status": "verified",
            },
        )
        specs = [
            {
                "name": "Gold Doré Reference Spec",
                "buyer_org": buyer.name,
                "buyer_organisation": buyer,
                "material": "gold",
                "limits": [
                    {"analyte": "Au", "unit": "%", "min": "95", "max": "", "method": "ICP-MS"},
                    {"analyte": "Ag", "unit": "%", "min": "", "max": "5", "method": "ICP-MS"},
                ],
            },
            {
                "name": "Lithium Ore Reference Spec",
                "buyer_org": buyer.name,
                "buyer_organisation": buyer,
                "material": "lithium",
                "limits": [
                    {"analyte": "Li2O", "unit": "%", "min": "5", "max": "", "method": "XRF"},
                    {"analyte": "Moisture", "unit": "%", "min": "", "max": "10", "method": "Gravimetric"},
                ],
            },
        ]
        for spec in specs:
            BuyerSpec.objects.update_or_create(name=spec["name"], defaults=spec)
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(specs)} quality buyer specs."))
