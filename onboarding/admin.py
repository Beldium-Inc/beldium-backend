from django.contrib import admin

from onboarding.models import (
    OnboardingDashboard,
    OnboardingOption,
    ParticipantApplication,
    ParticipantDocument,
    RegulatorAccessRequest,
)


class OnboardingOptionInline(admin.TabularInline):
    model = OnboardingOption
    extra = 0
    fields = ["order", "key", "kind", "label", "description", "role", "org_path", "external_portal", "external_label", "note", "enabled"]


@admin.register(OnboardingDashboard)
class OnboardingDashboardAdmin(admin.ModelAdmin):
    list_display = ["sector", "key", "label", "order", "enabled"]
    list_filter = ["sector", "key", "enabled"]
    list_editable = ["order", "enabled"]
    inlines = [OnboardingOptionInline]


@admin.register(OnboardingOption)
class OnboardingOptionAdmin(admin.ModelAdmin):
    list_display = ["dashboard", "key", "kind", "label", "role", "order", "enabled"]
    list_filter = ["dashboard__sector", "dashboard__key", "kind", "enabled"]
    list_editable = ["order", "enabled"]


class ParticipantDocumentInline(admin.TabularInline):
    model = ParticipantDocument
    extra = 0
    readonly_fields = ["title", "file", "original_name", "uploaded_by", "created_at"]


@admin.register(ParticipantApplication)
class ParticipantApplicationAdmin(admin.ModelAdmin):
    list_display = ["organisation", "sector", "status", "submitted_at", "created_at"]
    list_filter = ["sector", "status"]
    inlines = [ParticipantDocumentInline]


@admin.register(RegulatorAccessRequest)
class RegulatorAccessRequestAdmin(admin.ModelAdmin):
    list_display = ["agency_name", "official_email", "status", "created_at", "decided_at"]
    list_filter = ["status"]
    # Approval writes the organisation and grants, so it goes through the API
    # (onboarding.services.approve_regulator_request), not a field edit here.
    readonly_fields = ["status", "approved_sectors", "decided_by", "decided_at", "organisation"]
