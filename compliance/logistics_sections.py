"""The Logistics organisation application.

A logistics applicant answers about its haulage services, fleet operations and
transport personnel rather than the mining sections, so those answers live in
``ComplianceApplication.logistics_profile`` under the keys below, each written
through its own ``sections/logistics-<key>/`` route. The organisation's core
identity still goes through the shared organisation section.
"""
from rest_framework import serializers

from organisations.models import OrganisationType


# The subtype an applicant picks on the organisation step. Every logistics
# applicant registers as the platform's logistics company type.
LOGISTICS_ORGANISATION_TYPES = (
    "Mineral Haulage Company",
    "General Logistics and Transport Company",
    "Fleet Owner / Fleet Operator",
    "Third-Party Logistics Provider",
    "Integrated Logistics and Warehousing Provider",
    "Independent Transport Service Provider",
    "Other Logistics Service Provider",
)
PLATFORM_ORGANISATION_TYPE = OrganisationType.LOGISTICS_COMPANY

LOGISTICS_SERVICES = (
    "Mine-to-warehouse transportation",
    "Mine-to-processing-facility transportation",
    "Warehouse-to-port transportation",
    "Processing-facility-to-port transportation",
    "Inter-facility transportation",
    "Bulk mineral haulage",
    "Containerized mineral transport",
    "Aggregation and collection transport",
    "Other mineral logistics services",
)

LOGISTICS_EVIDENCE_STANDARDS = (
    "GPS trip logs",
    "Weighbridge tickets",
    "Seal and custody records",
    "Photographic evidence",
    "Digital dispatch records",
)

# Profile keys an application has to hold before it reads as complete.
LOGISTICS_REQUIRED_SECTIONS = ("organisation", "services", "capability", "operations", "conflict")


def is_logistics_profile_application(application):
    return application.sector == "logistics"


def _count():
    return serializers.IntegerField(min_value=0, required=False, default=0)


class LogisticsOrganisationDataSerializer(serializers.Serializer):
    logistics_type = serializers.ChoiceField(choices=LOGISTICS_ORGANISATION_TYPES)


class LogisticsServicesDataSerializer(serializers.Serializer):
    selected_services = serializers.ListField(child=serializers.ChoiceField(choices=LOGISTICS_SERVICES), allow_empty=False)
    geographic_coverage = serializers.ChoiceField(choices=["nationwide", "selected_states"])
    states_covered = serializers.ListField(child=serializers.CharField(max_length=100), required=False, default=list)

    def validate(self, attrs):
        if attrs["geographic_coverage"] == "selected_states" and not attrs.get("states_covered"):
            raise serializers.ValidationError({"states_covered": "Name the states you operate in."})
        if attrs["geographic_coverage"] == "nationwide":
            attrs["states_covered"] = []
        return attrs


class LogisticsCapabilityDataSerializer(serializers.Serializer):
    years_haulage_experience = serializers.IntegerField(min_value=0, max_value=100)
    drivers_employed = _count()
    vehicles_operated = _count()
    compliance_professionals = _count()
    transport_safety_managers = _count()
    maintenance_engineers = _count()
    dispatch_coordinators = _count()
    driver_trainers = _count()
    other_technical_personnel = _count()


class LogisticsOperationsDataSerializer(serializers.Serializer):
    operates_own_fleet = serializers.BooleanField()
    active_vehicles = _count()
    gps_tracking_available = serializers.BooleanField(required=False, default=False)
    maximum_movements_per_month = _count()
    average_turnaround_time = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    typical_mobilisation_time = serializers.CharField(max_length=100, required=False, allow_blank=True, default="")
    evidence_standards = serializers.ListField(
        child=serializers.ChoiceField(choices=LOGISTICS_EVIDENCE_STANDARDS), required=False, default=list,
    )


class LogisticsConflictDataSerializer(serializers.Serializer):
    owns_mineral_assets = serializers.BooleanField()
    serves_mining_companies = serializers.BooleanField()
    trades_minerals = serializers.BooleanField()
    relationships = serializers.CharField()
    agreed = serializers.BooleanField()

    def validate_agreed(self, value):
        if not value:
            raise serializers.ValidationError("The conflict disclosure must be agreed.")
        return value


LOGISTICS_SECTIONS = {
    "organisation": LogisticsOrganisationDataSerializer,
    "services": LogisticsServicesDataSerializer,
    "capability": LogisticsCapabilityDataSerializer,
    "operations": LogisticsOperationsDataSerializer,
    "conflict": LogisticsConflictDataSerializer,
}


def logistics_section_serializer(key):
    """The `{data: ...}` envelope every logistics section route takes, for one section."""
    return type(
        f"Logistics{key.title().replace('-', '')}SectionSerializer",
        (serializers.Serializer,),
        {"data": LOGISTICS_SECTIONS[key]()},
    )
