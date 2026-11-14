from django.contrib import admin

from quality import models as m


class ReadOnlyAdmin(admin.ModelAdmin):
    """Quality records change only through the API, where the role, owner and
    state rules are checked and the change is audited. The admin site may
    look but never add, change or delete."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(m.QualityApplication)
class QualityApplicationAdmin(ReadOnlyAdmin):
    list_display = ["reference", "organisation", "status", "risk_score", "assigned_to", "created_at"]
    list_filter = ["status"]
    search_fields = ["reference", "organisation__name"]


@admin.register(m.Sample)
class SampleAdmin(ReadOnlyAdmin):
    list_display = ["reference", "material", "status", "buyer_spec", "created_at"]
    list_filter = ["status"]
    search_fields = ["reference", "material", "lot"]


@admin.register(m.BuyerSpec)
class BuyerSpecAdmin(ReadOnlyAdmin):
    list_display = ["name", "buyer_org", "material"]
    search_fields = ["name", "buyer_org", "material"]


@admin.register(m.Certificate)
class CertificateAdmin(ReadOnlyAdmin):
    list_display = ["reference", "sample", "status", "issued_at"]
    list_filter = ["status"]
    search_fields = ["reference", "sample__reference"]


@admin.register(m.QualityNonConformity)
class QualityNonConformityAdmin(ReadOnlyAdmin):
    list_display = ["reference", "title", "severity", "status", "raised_at"]
    list_filter = ["status", "severity"]
    search_fields = ["reference", "title", "against"]


@admin.register(m.QualityProfessionalApplication)
class QualityProfessionalApplicationAdmin(ReadOnlyAdmin):
    list_display = ("reference", "applicant", "organisation", "status", "submitted_at")
    list_filter = ("status",)
    search_fields = ("reference", "applicant__email")


@admin.register(m.QualityRole)
class QualityRoleAdmin(ReadOnlyAdmin):
    list_display = ("code", "name", "is_internal", "second_factor_required")


@admin.register(m.QualityRoleAssignment)
class QualityRoleAssignmentAdmin(ReadOnlyAdmin):
    list_display = ("user", "role", "organisation", "status", "valid_from", "valid_to")
    list_filter = ("status", "role")
    search_fields = ("user__email",)


# The audit log is deliberately not registered here. Decision 8: Admin has
# no access to the audit log. It is read through /quality/audit-log/ only.
