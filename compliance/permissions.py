from rest_framework.permissions import BasePermission


class IsApplicationMember(BasePermission):
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)

    def has_object_permission(self, request, view, obj):
        application = getattr(obj, "application", obj)
        return request.user.is_staff or application.organisation.memberships.filter(user=request.user, is_active=True).exists()
