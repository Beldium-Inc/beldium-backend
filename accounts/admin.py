from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.template.response import TemplateResponse

from accounts.audit import record_account_event
from accounts.test_account_cleanup import plan_removal, remove_test_accounts

from accounts.models import AccountAuditEvent, AccountRecoveryCode, EmailVerificationCode, PhoneVerificationCode, SocialIdentity, User
from organisations.models import OrganisationMembership
from organisations.rights import organisation_sector

admin.site.register(EmailVerificationCode)
admin.site.register(AccountRecoveryCode)
class ReadOnlyAuditAdmin(admin.ModelAdmin):
    """Audit rows are looked at, never added, changed or deleted here."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AccountAuditEvent)
class AccountAuditEventAdmin(ReadOnlyAuditAdmin):
    list_display = ("created_at", "event_type", "actor_email", "ip_address")
    list_filter = ("event_type",)
    search_fields = ("actor_email", "event_type")
admin.site.register(PhoneVerificationCode)
admin.site.register(SocialIdentity)


class OrganisationMembershipInline(admin.TabularInline):
    """Shows which organisation, and which sector, this user belongs to."""

    model = OrganisationMembership
    fk_name = "user"
    extra = 0
    can_delete = False
    fields = ["organisation", "organisation_type", "sector", "role", "is_active"]
    readonly_fields = ["organisation", "organisation_type", "sector", "role", "is_active"]
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        return False

    @admin.display(description="Organisation type")
    def organisation_type(self, obj):
        return obj.organisation.get_organisation_type_display()

    @admin.display(description="Sector")
    def sector(self, obj):
        return organisation_sector(obj.organisation) or "-"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ["email"]
    inlines = [OrganisationMembershipInline]
    list_display = ["email", "first_name", "last_name", "organisation_names", "is_active", "is_staff"]
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal information", {"fields": ("first_name", "last_name", "phone_number")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "email_verified_at", "phone_verified_at")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
    search_fields = ["email", "first_name", "last_name"]
    actions = ["remove_test_accounts"]

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("organisation_memberships__organisation")

    @admin.display(description="Organisation(s)")
    def organisation_names(self, obj):
        memberships = [m for m in obj.organisation_memberships.all() if m.is_active]
        return ", ".join(
            f"{m.organisation.name} ({organisation_sector(m.organisation) or 'no sector'})"
            for m in memberships
        ) or "-"

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop("remove_test_accounts", None)
        return actions

    @admin.action(description="Remove selected test accounts with their logistics records")
    def remove_test_accounts(self, request, queryset):
        """The plain delete action refuses accounts that have logistics records.
        This one clears those records first. It shows what will go and asks
        for confirmation before deleting anything."""
        if not request.user.is_superuser:
            self.message_user(request, "Only superusers can remove test accounts.", messages.ERROR)
            return None
        users = list(queryset.order_by("email"))
        if request.POST.get("confirm") != "yes":
            return TemplateResponse(request, "admin/accounts/user/remove_test_accounts.html", {
                **self.admin_site.each_context(request),
                "title": "Remove test accounts",
                "opts": self.model._meta,
                "plans": plan_removal(users, acting_user=request.user),
                "selected_ids": [str(u.pk) for u in users],
                "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            })
        for result in remove_test_accounts(users, acting_user=request.user):
            if result.deleted:
                record_account_event(request, "account.test_account_removed", email=result.email)
            self.message_user(
                request,
                f"Account {result.email}: {result.message}",
                messages.SUCCESS if result.deleted else messages.WARNING,
            )
        return None
