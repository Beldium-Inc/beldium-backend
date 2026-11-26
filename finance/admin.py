from django.contrib import admin

from finance.models import Invoice, Payment, PlatformFeeRate


@admin.register(PlatformFeeRate)
class PlatformFeeRateAdmin(admin.ModelAdmin):
    list_display = ("version", "rate_percent", "effective_from", "note")
    ordering = ("-version",)

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

admin.site.register(Invoice)
admin.site.register(Payment)
