"""Public careers-hub submissions: nobody is signed in when these are created."""
import secrets

from django.db import models
from django.utils.text import get_valid_filename

from common.models import TimeStampedModel

# No 0/O or 1/I: applicants read these references back over the phone.
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
PATHWAY_CODES = {"internship": "INT", "volunteer": "VOL", "partnership": "PAR"}

PARTNER_SECTOR_CODES = {"logistics": "LOG", "warehousing": "WHS"}
PARTNER_SECTOR_CHOICES = tuple((key, key.replace("_", " ").title()) for key in PARTNER_SECTOR_CODES)

LOGISTICS_REQUIRED_PARTNER_DOCUMENTS = (
    "cacCertificate",
    "tinCertificate",
    "companyProfile",
    "registeredBusinessAddress",
    "representativeId",
)
LOGISTICS_OPTIONAL_PARTNER_DOCUMENTS = (
    "authorisationLetter",
    "taxClearanceCertificate",
    "transportOperatingPermit",
    "frscRtsssCertification",
    "fleetRegister",
    "fleetSafetyPolicy",
    "safetyManagerDetails",
    "driverTrainingRecords",
    "fleetInspectionRecords",
    "vehicleMaintenanceRecords",
    "speedLimiterEvidence",
    "vehicleRegistrationCertificate",
    "vehicleOwnershipEvidence",
    "roadWorthiness",
    "vehicleInsurance",
    "vehicleInspectionReport",
    "commercialVehiclePermit",
    "gpsTelematicsEvidence",
    "vehiclePhotographsCapacityEvidence",
    "trailerDocuments",
    "driversLicence",
    "driverIdentification",
    "driverPhotograph",
    "driverEngagementEvidence",
    "heavyVehicleTrainingCertificate",
    "safetyTrainingRecords",
    "medicalFitnessEvidence",
    "driverCompetencyAssessment",
    "goodsInTransitInsurance",
    "carrierLiabilityInsurance",
    "publicLiabilityInsurance",
    "employeeCompensationInsurance",
    "shipmentCargoInsurance",
    "hsePolicy",
    "transportSafetyPolicy",
    "journeyManagementProcedure",
    "emergencyResponseProcedure",
    "incidentReportingProcedure",
    "vehicleMaintenanceProcedure",
    "driverManagementProcedure",
    "cargoLoadingSecuringProcedure",
    "mineralHandlingProcedure",
    "securityCargoProtectionProcedure",
    "sampleHandlingProcedure",
    "chainOfCustodyProcedure",
    "correctiveActionRecords",
    "mineralSourceDeclaration",
    "miningLicenceSourceAuthorisation",
    "mineralBatchIdentification",
    "transportOrderWaybill",
    "materialDispatchNote",
    "loadingWeighbridgeTicket",
    "qualityCertificateAssayReport",
    "commercialInvoiceTransferRecord",
    "mineralMovementAuthorisation",
    "deliveryNote",
    "proofOfDelivery",
    "receivingWeighbridgeRecord",
    "chainOfCustodyRecord",
    # Legacy key accepted for existing frontend builds and staff email labels.
    "bankConfirmation",
)

WAREHOUSING_REQUIRED_PARTNER_DOCUMENTS = (
    "cacCertificate",
    "tinCertificate",
    "companyProfile",
    "registeredBusinessAddress",
    "representativeId",
)
WAREHOUSING_OPTIONAL_PARTNER_DOCUMENTS = (
    "authorisationLetter",
    "taxClearanceCertificate",
    "warehouseOperatingPermit",
    "facilityLeaseOrOwnership",
    "facilityLayoutPlan",
    "storageCapacityEvidence",
    "fireSafetyCertificate",
    "environmentalPermit",
    "hsePolicy",
    "securityProcedure",
    "inventoryManagementProcedure",
    "weighbridgeCalibrationCertificate",
    "insuranceCertificate",
    "publicLiabilityInsurance",
    "employeeCompensationInsurance",
    "priorExperience",
    "clientReferences",
    "bankConfirmation",
)

PARTNER_DOCUMENT_REQUIREMENTS = {
    "logistics": {
        "required": LOGISTICS_REQUIRED_PARTNER_DOCUMENTS,
        "optional": LOGISTICS_OPTIONAL_PARTNER_DOCUMENTS,
    },
    "warehousing": {
        "required": WAREHOUSING_REQUIRED_PARTNER_DOCUMENTS,
        "optional": WAREHOUSING_OPTIONAL_PARTNER_DOCUMENTS,
    },
}
REQUIRED_PARTNER_DOCUMENTS = LOGISTICS_REQUIRED_PARTNER_DOCUMENTS
PARTNER_DOCUMENT_KEYS = tuple(dict.fromkeys(
    key
    for requirement in PARTNER_DOCUMENT_REQUIREMENTS.values()
    for key in (*requirement["required"], *requirement["optional"])
))
PARTNER_AGREEMENT_KEYS = ("partnerAgreement", "codeOfConduct", "dataPrivacyAgreement", "serviceLevelAgreement")


def random_reference(prefix):
    return f"{prefix}-{''.join(secrets.choice(REFERENCE_ALPHABET) for _ in range(6))}"


def stored_name(label, filename):
    # The label keeps one applicant's files apart; the tail keeps the extension
    # when a very long original name has to be cut to fit the column.
    return f"{label}-{get_valid_filename(filename)[-120:]}"


def _application_path(instance, label, filename):
    return f"careers/applications/{instance.reference_id}/{stored_name(label, filename)}"


def resume_upload_path(instance, filename):
    return _application_path(instance, "resume", filename)


def headshot_upload_path(instance, filename):
    return _application_path(instance, "headshot", filename)


def company_profile_upload_path(instance, filename):
    return _application_path(instance, "company-profile", filename)


def partner_document_upload_path(instance, filename):
    return f"careers/partners/{instance.application.application_id}/{stored_name(instance.key, filename)}"


class Application(TimeStampedModel):
    class Pathway(models.TextChoices):
        INTERNSHIP = "internship", "Internship"
        VOLUNTEER = "volunteer", "Volunteer"
        PARTNERSHIP = "partnership", "Partnership"

    reference_id = models.CharField(max_length=20, unique=True, editable=False)
    pathway = models.CharField(max_length=20, choices=Pathway.choices, db_index=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(max_length=200)
    phone = models.CharField(max_length=40)
    country = models.CharField(max_length=80)
    state = models.CharField(max_length=80)
    city = models.CharField(max_length=80)
    linkedin = models.URLField(max_length=300)
    portfolio = models.URLField(max_length=300, blank=True)
    resume = models.FileField(upload_to=resume_upload_path, max_length=300)
    headshot = models.FileField(upload_to=headshot_upload_path, max_length=300, blank=True)
    company_profile = models.FileField(upload_to=company_profile_upload_path, max_length=300, blank=True)
    # The pathway-specific questions; the form owns their shape.
    answers = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference_id} ({self.full_name})"

    def save(self, *args, **kwargs):
        if not self.reference_id:
            self.reference_id = random_reference(f"BLD-{PATHWAY_CODES[self.pathway]}")
        super().save(*args, **kwargs)


class PartnerApplication(TimeStampedModel):
    class Status(models.TextChoices):
        # Created, documents still uploading. Never shown to the applicant or
        # emailed to staff until the final submit call lands.
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        UNDER_REVIEW = "under_review", "Under review"
        INFO_REQUESTED = "info_requested", "Information requested"
        APPROVED = "approved", "Approved"
        DASHBOARD_ACTIVE = "dashboard_active", "Dashboard active"

    application_id = models.CharField(max_length=20, unique=True, editable=False)
    sector = models.CharField(max_length=30, choices=PARTNER_SECTOR_CHOICES, default="logistics", db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT, db_index=True)
    company = models.JSONField()
    agreements = models.JSONField(default=list)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.application_id} ({self.company.get('companyName', '')})"

    def save(self, *args, **kwargs):
        if not self.application_id:
            self.application_id = random_reference(f"BLD-{PARTNER_SECTOR_CODES[self.sector]}")
        super().save(*args, **kwargs)


class PartnerDocument(TimeStampedModel):
    application = models.ForeignKey(PartnerApplication, on_delete=models.CASCADE, related_name="documents")
    key = models.CharField(max_length=40)
    file = models.FileField(upload_to=partner_document_upload_path, max_length=300)

    class Meta:
        ordering = ["key"]
        constraints = [
            models.UniqueConstraint(fields=["application", "key"], name="careers_one_document_per_key"),
        ]

    def __str__(self):
        return f"{self.application.application_id}: {self.key}"
