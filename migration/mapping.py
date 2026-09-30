"""Which old tables merge into which v2 tables, and how rows are matched.

MAPPINGS is processed top to bottom, so parents must come before the tables
that reference them (organisations before memberships, and so on).

Only accounts_user is filled in. It is the one part of the old schema this
repository documents: accounts/management/commands/import_legacy_users.py
reads id, email, password, is_active, is_staff, is_superuser, phone_number,
account_verified and account_verified_at from the old accounts_user table.
Everything else about the old database is unknown until compare_schemas.py
has been run against a restored dump. PENDING lists the v2 customer-data
tables still to be mapped, with the natural keys to match them on.

To add a table: run compare_schemas.py, find the old table that holds the same
records, then move its entry from PENDING into MAPPINGS as a TableMap. The
merge scripts refuse to run if a mapping names a column that does not exist
or leaves a NOT NULL v2 column without a value.
"""
from merge_engine import (
    ForeignKey,
    TableMap,
    normalize_company_name,
    normalize_email,
    normalize_phone,
    normalize_registration_number,
    now,
)

USERS = TableMap(
    target="accounts_user",
    source="accounts_user",
    columns={
        "email": lambda r: (r["email"] or "").strip().lower(),
        "password": "password",  # hash copied as-is, so the old password keeps working
        "is_active": "is_active",
        "is_staff": "is_staff",
        "is_superuser": "is_superuser",
        "phone_number": lambda r: (r["phone_number"] or "")[:30],
        "email_verified_at": lambda r: r["account_verified_at"] if r["account_verified"] else None,
    },
    # Blank portal marks a legacy account; login lets it through
    # (accounts/models.py, comment on User.portal).
    # TODO after compare_schemas: map first_name, last_name, date_joined ->
    # created_at, last_login if the old table has them.
    defaults={
        "first_name": "",
        "last_name": "",
        "country": "",
        "onboarding_role": "",
        "portal": "",
        "created_at": lambda r: now(),
        "updated_at": lambda r: now(),
    },
    natural_keys=[("email",), ("phone_number",)],
    normalizers={"email": normalize_email, "phone_number": normalize_phone},
)

MAPPINGS = [
    USERS,
]

# v2 tables that hold customer data, in the order they must be merged, with
# the natural keys to match on. Evidence: MIGRATION_AUDIT.md section 7.
PENDING = [
    ("organisations_organisation", [("country", "registration_number"), ("email",), ("name", "organisation_type")],
     {"registration_number": normalize_registration_number, "email": normalize_email, "name": normalize_company_name}),
    ("organisations_organisationmembership", [("organisation_id", "user_id")], {}),
    ("compliance_complianceapplication", [("organisation_id",)], {}),  # one per organisation (OneToOne)
    ("compliance_personnel", [("application_id", "full_name", "role")], {}),
    ("compliance_compliancedocument", [("application_id", "document_type")], {}),
    ("mining_miningorganisationprofile", [("organisation_id",)], {}),
    ("mining_minesite", [("organisation_id", "name")], {"name": normalize_company_name}),
    ("processing_processor", [("rc_number",)], {"rc_number": normalize_registration_number}),
    ("export_exporter", [("contact_email",)], {"contact_email": normalize_email}),
    ("export_buyer", [("contact_email",)], {"contact_email": normalize_email}),
    ("logistics_logisticscompany", [("contact_email",)], {"contact_email": normalize_email}),
    ("logistics_vehicle", [("registration",), ("vin",)], {}),
    ("logistics_driver", [("licence_number",)], {}),
    ("warehousing_warehouseoperator", [("contact_email",)], {"contact_email": normalize_email}),
    ("marketplace_sellerprofile", [("contact_email",)], {"contact_email": normalize_email}),
]

__all__ = ["MAPPINGS", "PENDING", "ForeignKey"]
