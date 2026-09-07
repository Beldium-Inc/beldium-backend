from organisations.models import MembershipRole


ROLE_PERMISSIONS = {
    MembershipRole.OWNER: {"organisation.manage", "members.manage", "applications.submit", "reviews.manage", "inspections.manage"},
    MembershipRole.ADMIN: {"organisation.manage", "members.manage", "applications.submit", "reviews.manage", "inspections.manage"},
    MembershipRole.COMPLIANCE_MANAGER: {"members.view", "applications.submit", "reviews.manage", "inspections.manage"},
    MembershipRole.REVIEWER: {"members.view", "reviews.manage", "applications.view"},
    MembershipRole.MINING_COMPLIANCE_OFFICER: {"members.view", "reviews.manage", "applications.view"},
    MembershipRole.INSPECTOR: {"members.view", "inspections.manage", "applications.view"},
    MembershipRole.MINING_ENGINEER: {"members.view", "inspections.contribute", "applications.view"},
    MembershipRole.GEOLOGIST: {"members.view", "inspections.contribute", "applications.view"},
    MembershipRole.ENVIRONMENTAL_SPECIALIST: {"members.view", "reviews.contribute", "applications.view"},
    MembershipRole.HSE_SPECIALIST: {"members.view", "reviews.contribute", "applications.view"},
    MembershipRole.LEGAL_REGULATORY_SPECIALIST: {"members.view", "reviews.contribute", "applications.view"},
    MembershipRole.ANALYST: {"members.view", "reports.view", "applications.view"},
    MembershipRole.MEMBER: {"members.view", "applications.view"},
    MembershipRole.READ_ONLY: {"members.view", "applications.view"},
    MembershipRole.OTHER: {"members.view"},
}


def permissions_for_role(role):
    return sorted(ROLE_PERMISSIONS.get(role, set()))
