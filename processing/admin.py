from django.contrib import admin

from processing.models import (
    ApplicationSection,
    ComplianceReport,
    EnvironmentalAlert,
    Facility,
    Incident,
    Inspection,
    NonConformity,
    NonConformityEvidence,
    ProcessingApplication,
    ProcessingDocument,
    Processor,
    RiskCause,
    TraceabilityRun,
)


class FacilityInline(admin.TabularInline):
    model = Facility
    extra = 0


class ApplicationSectionInline(admin.TabularInline):
    model = ApplicationSection
    extra = 0


class RiskCauseInline(admin.TabularInline):
    model = RiskCause
    extra = 0


class EvidenceInline(admin.TabularInline):
    model = NonConformityEvidence
    extra = 0


@admin.register(Processor)
class ProcessorAdmin(admin.ModelAdmin):
    list_display = ["name", "reference", "processing_type", "state", "status", "compliance_score"]
    list_filter = ["status", "processing_type", "state", "region"]
    search_fields = ["name", "reference", "rc_number", "tin"]
    inlines = [FacilityInline]


@admin.register(ProcessingApplication)
class ProcessingApplicationAdmin(admin.ModelAdmin):
    list_display = ["reference", "company", "processing_type", "state", "stage", "decision", "submitted_on"]
    list_filter = ["stage", "decision", "processing_type", "state"]
    search_fields = ["reference", "company", "rc_number"]
    inlines = [ApplicationSectionInline, RiskCauseInline]


@admin.register(NonConformity)
class NonConformityAdmin(admin.ModelAdmin):
    list_display = ["reference", "title", "severity", "status", "raised_on", "due_on"]
    list_filter = ["status", "severity", "section"]
    search_fields = ["reference", "title"]
    inlines = [EvidenceInline]


@admin.register(Inspection)
class InspectionAdmin(admin.ModelAdmin):
    list_display = ["reference", "facility_name", "inspection_type", "status", "scheduled_for"]
    list_filter = ["status", "inspection_type", "state"]
    search_fields = ["reference", "facility_name", "inspector_name"]


@admin.register(EnvironmentalAlert)
class EnvironmentalAlertAdmin(admin.ModelAdmin):
    list_display = ["reference", "parameter", "facility_name", "severity", "status", "detected_on"]
    list_filter = ["status", "severity", "state"]
    search_fields = ["reference", "parameter", "facility_name"]


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ["reference", "incident_type", "facility_name", "severity", "status", "reported_on"]
    list_filter = ["status", "severity", "state"]
    search_fields = ["reference", "incident_type", "facility_name"]


@admin.register(TraceabilityRun)
class TraceabilityRunAdmin(admin.ModelAdmin):
    list_display = ["reference", "input_batch", "output_batch", "qc_verdict", "started_at"]
    list_filter = ["qc_verdict"]
    search_fields = ["reference", "input_batch", "output_batch"]


@admin.register(ProcessingDocument)
class ProcessingDocumentAdmin(admin.ModelAdmin):
    list_display = ["name", "section", "issuer", "issued_on", "expires_on"]
    list_filter = ["section"]
    search_fields = ["name", "reference", "issuer"]


admin.site.register([Facility, ComplianceReport])
