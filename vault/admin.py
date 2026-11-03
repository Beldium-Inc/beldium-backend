from django.contrib import admin

from vault.models import VaultCategory


# Only the folders are managed here. Vault documents, approvals and activity
# are append only and are deliberately not registered, so the admin site has
# no edit or delete path to them.
@admin.register(VaultCategory)
class VaultCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "sector", "code", "sort_order", "is_active"]
    list_filter = ["sector", "is_active"]
    readonly_fields = ["sector", "code"]

    def get_readonly_fields(self, request, obj=None):
        return self.readonly_fields if obj else []

    def has_delete_permission(self, request, obj=None):
        return False
