from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import AccountAuditEvent, AccountRecoveryCode, EmailVerificationCode, PhoneVerificationCode, SocialIdentity, User

admin.site.register(EmailVerificationCode)
admin.site.register(AccountRecoveryCode)
admin.site.register(AccountAuditEvent)
admin.site.register(PhoneVerificationCode)
admin.site.register(SocialIdentity)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["email"]
    list_display = ["email", "first_name", "last_name", "is_active", "is_staff"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal information", {"fields": ("first_name", "last_name", "phone_number")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "email_verified_at", "phone_verified_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
    search_fields = ["email", "first_name", "last_name"]
