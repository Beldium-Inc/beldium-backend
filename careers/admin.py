from django.contrib import admin

from careers import models


class SubmissionAdmin(admin.ModelAdmin):
    """Submissions arrive through the public API; staff read them here and
    download the documents, which the storage backend signs per request."""

    def has_add_permission(self, request):
        return False


@admin.register(models.Application)
class ApplicationAdmin(SubmissionAdmin):
    list_display = ["reference_id", "pathway", "full_name", "email", "created_at"]
    list_filter = ["pathway"]
    search_fields = ["reference_id", "full_name", "email"]
    readonly_fields = [field.name for field in models.Application._meta.fields]


class PartnerDocumentInline(admin.TabularInline):
    model = models.PartnerDocument
    extra = 0
    can_delete = False
    readonly_fields = ["key", "file", "created_at"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(models.PartnerApplication)
class PartnerApplicationAdmin(SubmissionAdmin):
    list_display = ["application_id", "company_name", "status", "submitted_at"]
    list_filter = ["status"]
    search_fields = ["application_id", "company"]
    # Status is the one thing staff change: applicants see it on the tracker.
    readonly_fields = ["application_id", "company", "agreements", "submitted_at", "created_at"]
    inlines = [PartnerDocumentInline]

    @admin.display(description="Company")
    def company_name(self, obj):
        return obj.company.get("companyName", "")
