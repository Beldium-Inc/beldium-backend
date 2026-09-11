from django.contrib import admin

from mining.models import (
    Application,
    CorrectiveSubmission,
    DocumentRecord,
    Equipment,
    EnvRecord,
    Evidence,
    InfoRequest,
    Inspection,
    LicenceDoc,
    MineSite,
    MiningOrganisationProfile,
    NonConformity,
    PendingReview,
    ReviewSection,
    Sample,
    SafetyIncident,
    ScoreFactor,
)


class ScoreFactorInline(admin.TabularInline):
    model = ScoreFactor
    extra = 0


class ReviewSectionInline(admin.TabularInline):
    model = ReviewSection
    extra = 0


class EvidenceInline(admin.TabularInline):
    model = Evidence
    extra = 0


class LicenceDocInline(admin.TabularInline):
    model = LicenceDoc
    extra = 0


class CorrectiveSubmissionInline(admin.TabularInline):
    model = CorrectiveSubmission
    extra = 0


@admin.register(MiningOrganisationProfile)
class MiningOrganisationProfileAdmin(admin.ModelAdmin):
    list_display = ["organisation"]
    search_fields = ["organisation__name"]


@admin.register(MineSite)
class MineSiteAdmin(admin.ModelAdmin):
    list_display = ["name", "code", "mineral", "state", "status", "compliance_score", "risk"]
    list_filter = ["status", "risk", "mineral", "state"]
    search_fields = ["name", "code"]
    inlines = [ScoreFactorInline, ReviewSectionInline, LicenceDocInline]


@admin.register(ReviewSection)
class ReviewSectionAdmin(admin.ModelAdmin):
    list_display = ["site", "key", "status", "score", "weight"]
    list_filter = ["key", "status"]
    inlines = [EvidenceInline]


@admin.register(NonConformity)
class NonConformityAdmin(admin.ModelAdmin):
    list_display = ["reference", "title", "severity", "status", "deadline"]
    list_filter = ["status", "severity"]
    search_fields = ["reference", "title"]
    inlines = [CorrectiveSubmissionInline]


@admin.register(Inspection)
class InspectionAdmin(admin.ModelAdmin):
    list_display = ["reference", "site", "type", "status", "scheduled_for"]
    list_filter = ["status", "type"]
    search_fields = ["reference", "inspector_name"]


@admin.register(Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ["reference", "site", "collected_on", "status", "lab"]
    list_filter = ["status"]
    search_fields = ["reference", "lab", "certificate"]


@admin.register(EnvRecord)
class EnvRecordAdmin(admin.ModelAdmin):
    list_display = ["metric", "site", "status", "measured_on"]
    list_filter = ["status"]
    search_fields = ["metric"]


@admin.register(SafetyIncident)
class SafetyIncidentAdmin(admin.ModelAdmin):
    list_display = ["type", "site", "severity", "status", "date"]
    list_filter = ["status", "severity"]
    search_fields = ["type"]


@admin.register(Equipment)
class EquipmentAdmin(admin.ModelAdmin):
    list_display = ["name", "site", "serial", "status", "cert_expires_on"]
    list_filter = ["status"]
    search_fields = ["name", "serial"]


@admin.register(Application)
class ApplicationAdmin(admin.ModelAdmin):
    list_display = ["reference", "site_name", "type", "mineral", "status", "submitted_on"]
    list_filter = ["status"]
    search_fields = ["reference", "site_name"]


@admin.register(DocumentRecord)
class DocumentRecordAdmin(admin.ModelAdmin):
    list_display = ["name", "site", "category", "status", "expires_on"]
    list_filter = ["status"]
    search_fields = ["name"]


admin.site.register([LicenceDoc, PendingReview, InfoRequest, ScoreFactor, Evidence, CorrectiveSubmission])
