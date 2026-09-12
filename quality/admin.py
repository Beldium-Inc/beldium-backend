from django.contrib import admin

from quality import models as m


@admin.register(m.QualityApplication)
class QualityApplicationAdmin(admin.ModelAdmin):
    list_display = ["reference", "organisation", "status", "risk_score", "assigned_to", "created_at"]
    list_filter = ["status"]
    search_fields = ["reference", "organisation__name"]


@admin.register(m.Sample)
class SampleAdmin(admin.ModelAdmin):
    list_display = ["reference", "material", "status", "buyer_spec", "created_at"]
    list_filter = ["status"]
    search_fields = ["reference", "material", "lot"]


@admin.register(m.BuyerSpec)
class BuyerSpecAdmin(admin.ModelAdmin):
    list_display = ["name", "buyer_org", "material"]
    search_fields = ["name", "buyer_org", "material"]


@admin.register(m.Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ["reference", "sample", "status", "issued_at"]
    list_filter = ["status"]
    search_fields = ["reference", "sample__reference"]


@admin.register(m.QualityNonConformity)
class QualityNonConformityAdmin(admin.ModelAdmin):
    list_display = ["reference", "title", "severity", "status", "raised_at"]
    list_filter = ["status", "severity"]
    search_fields = ["reference", "title", "against"]
