"""Which old tables merge into which v2 tables, and how rows are matched.

MAPPINGS is processed top to bottom, so parents come before the tables that
reference them. Evidence for every choice is in migration/FINDINGS.md.

Old model -> v2 model:

  accounts_user                    -> accounts_user (all 16 already in v2; mapped
                                      so later tables can point at them)
  mining_sites_miningorganisation  -> organisations_organisation (mining_company)
  (its owner, via accounts_minerprofile) -> organisations_organisationmembership (owner)
  mining_sites_miningsite          -> mining_minesite
  compliance_minerlicense          -> mining_licencedoc, on the miner's site
  compliance_minerdocument         -> mining_documentrecord, on the miner's site
  miner profile document links     -> mining_documentrecord (environmental, licence
                                      certificate); government ID links are NOT moved
  accounts_complianceprofile       -> organisations_organisation (compliance_partner)
  (its owner)                      -> organisations_organisationmembership (owner)

Not moved: miner wallets (every amount is zero), buyer profile (only a code),
custom compliance roles (v2 has a fixed role list), government ID document
links, and profile fields v2 has no column for (see FINDINGS.md).

Files: old documents are full Backblaze B2 URLs. They are stored in v2 under
legacy/<path in the old bucket>. copy_legacy_files.py copies each file to
that key in S3.
"""
import hashlib
import re
from datetime import date, timedelta

from merge_engine import (
    ForeignKey,
    TableMap,
    normalize_company_name,
    normalize_email,
    now,
)
import secrets

# -------------------------------------------------------------- helpers


def year_token(prefix, hex_chars):
    """Same shape as the app's generators (organisations.models.generate_beldium_id,
    mining.models._reference): PREFIX-YEAR-HEX."""
    return f"{prefix}-{now().year}-{secrets.token_hex(hex_chars // 2).upper()}"


FILE_FIELD_MAX = 100  # Django FileField default max_length
LEGACY_URL = re.compile(r"^https?://[^/]+/(.+)$")


def legacy_key(url):
    """legacy/<path in the old bucket>, or a short hashed form if too long."""
    if not url:
        return ""
    match = LEGACY_URL.match(url.strip())
    path = re.sub(r"/+", "/", match.group(1) if match else url.strip())
    key = f"legacy/{path}"
    if len(key) > FILE_FIELD_MAX:
        digest = hashlib.sha256(url.encode()).hexdigest()[:12]
        key = f"legacy/{digest}/{path.rsplit('/', 1)[-1]}"[:FILE_FIELD_MAX]
    return key


def site_status(row):
    if row["status"] == "active" and row["operational_status"] == "active":
        return "operational"
    return "under_review"


def licence_status(row):
    expires = row["expiry_date"]
    if expires is None:
        return "active"
    today = date.today()
    if expires < today:
        return "expired"
    if expires <= today + timedelta(days=90):
        return "expiring"
    return "active"


def keep_created(row):
    return row["created_at"] or now()


def empty(*columns):
    return {c: "" for c in columns}


TIMESTAMPS = {"created_at": keep_created, "updated_at": lambda r: now()}
ORG_DEFAULTS = {
    **empty("registration_number", "tax_identifier", "email", "phone_number", "website", "address", "state", "rejection_reason"),
    "verification_status": "draft",
    "beldium_id": lambda r: year_token("BLD-ORG", 8),
    **TIMESTAMPS,
}
MEMBERSHIP_DEFAULTS = {"role": "owner", "is_active": True, **empty("title", "employee_id"), **TIMESTAMPS}
ORG_KEYS = [("name", "organisation_type")]
ORG_NORMALIZERS = {"name": normalize_company_name}

# -------------------------------------------------------------- users

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
    defaults={**empty("first_name", "last_name", "country", "onboarding_role", "portal"), **TIMESTAMPS},
    # Email only. Email is unique per account in v2; phone numbers are shared
    # between accounts in the real data, and matching on them made one old
    # user "ambiguous" although its email matched its own v2 account exactly.
    natural_keys=[("email",)],
    normalizers={"email": normalize_email},
)

# -------------------------------------------------------------- mining companies

MINING_ORGS = TableMap(
    label="organisations (mining companies)",
    target="organisations_organisation",
    source="mining_sites_miningorganisation",
    # One site per organisation in the old data; its state becomes the org's state.
    source_sql="""
        SELECT DISTINCT ON (o.id) o.id, o.name, o.country, o.created_at, s.state_of_operation
        FROM mining_sites_miningorganisation o
        LEFT JOIN mining_sites_miningsite s ON s.organisation_id = o.id
        ORDER BY o.id, s.created_at
    """,
    columns={
        "name": lambda r: r["name"].strip(),
        "organisation_type": lambda r: "mining_company",
        "country": lambda r: (r["country"] or "").strip() or "Nigeria",
        "state": lambda r: (r["state_of_operation"] or "").strip()[:100],
    },
    defaults=ORG_DEFAULTS,
    natural_keys=ORG_KEYS,
    normalizers=ORG_NORMALIZERS,
)

MINING_OWNERS = TableMap(
    label="memberships (mining company owners)",
    target="organisations_organisationmembership",
    source="mining_sites_miningorganisation",
    source_sql="""
        SELECT o.id, o.id AS organisation_id, p.user_id, o.created_at
        FROM mining_sites_miningorganisation o
        JOIN accounts_minerprofile p ON p.id = o.miner_id
    """,
    columns={"organisation_id": "organisation_id", "user_id": "user_id"},
    defaults=MEMBERSHIP_DEFAULTS,
    natural_keys=[("organisation_id", "user_id")],
    foreign_keys=[ForeignKey("organisation_id", "organisations_organisation"), ForeignKey("user_id", "accounts_user")],
)

SITES = TableMap(
    target="mining_minesite",
    source="mining_sites_miningsite",
    columns={
        "organisation_id": "organisation_id",
        "name": lambda r: r["name"].strip()[:255],
        "mineral": lambda r: (r["mineral_type"] or "").strip()[:100] or "Not specified",
        "state": lambda r: (r["state_of_operation"] or "").strip()[:100],
        "lga": lambda r: (r["local_government_area"] or "").strip()[:100],
        "latitude": "latitude",
        "longitude": "longitude",
        "status": site_status,
        "compliance_score": lambda r: max(0, min(100, int(r["compliance_score"] or 0))),
    },
    defaults={
        "code": lambda r: year_token("BLM-SITE", 4),
        "area_ha": 0,
        "risk": "medium",
        "capacity_tpa": 0,
        "current_tpa": 0,
        "workforce": 0,
        "verification": {},
        "risk_reasons": [],
        "production": [],
        "inventory": [],
        "transactions": [],
        **TIMESTAMPS,
    },
    natural_keys=[("organisation_id", "name")],
    normalizers={"name": normalize_company_name},
    foreign_keys=[ForeignKey("organisation_id", "organisations_organisation")],
)

# The old system hangs licences and documents on the miner; v2 hangs them on a
# site. Every old miner has exactly one organisation with exactly one site
# (FINDINGS.md), so the miner's site is unambiguous.
MINER_SITE = """
    JOIN accounts_minerprofile p ON p.id = x.miner_id
    JOIN mining_sites_miningorganisation o ON o.miner_id = p.id
    JOIN mining_sites_miningsite s ON s.organisation_id = o.id
"""

LICENCES = TableMap(
    target="mining_licencedoc",
    source="compliance_minerlicense",
    source_sql=f"SELECT x.*, s.id AS site_id FROM compliance_minerlicense x {MINER_SITE}",
    columns={
        "site_id": "site_id",
        "number": lambda r: r["license_number"].strip()[:100],
        "type": lambda r: (r["license_type"] or "").strip()[:150],
        "authority": lambda r: (r["issuing_authority"] or "").strip()[:200],
        "expires_on": "expiry_date",
        "status": licence_status,
        "file": lambda r: legacy_key(r["document"]),
    },
    defaults={"issued_on": None, **TIMESTAMPS},
    natural_keys=[("site_id", "number", "type")],
    foreign_keys=[ForeignKey("site_id", "mining_minesite")],
)

DOCUMENT_STATUS = {"pending": "pending", "verified": "verified", "rejected": "rejected", "expired": "expired"}

MINER_DOCUMENTS = TableMap(
    label="site documents (miner documents)",
    target="mining_documentrecord",
    source="compliance_minerdocument",
    source_sql=f"SELECT x.*, s.id AS site_id, p.user_id AS owner_id FROM compliance_minerdocument x {MINER_SITE}",
    columns={
        "site_id": "site_id",
        "name": lambda r: (r["document_type"] or "Document").strip()[:255],
        "category": lambda r: "Imported from previous platform",
        "expires_on": "expiry_date",
        "status": lambda r: DOCUMENT_STATUS.get(r["status"], "pending"),
        "file": lambda r: legacy_key(r["file"]),
        "uploaded_by_id": "owner_id",
    },
    defaults=TIMESTAMPS,
    natural_keys=[("site_id", "name", "file")],
    normalizers={"file": lambda v: v or "(no file)"},  # one old document has no file
    foreign_keys=[ForeignKey("site_id", "mining_minesite"), ForeignKey("uploaded_by_id", "accounts_user", on_missing="null")],
)

# Two document links stored on the old miner profile. The third,
# government_issue_document (personal ID), is deliberately not moved.
PROFILE_DOCUMENT_SQL = """
    SELECT md5(p.id::text || ':' || d.kind)::uuid AS id, d.label, d.url, s.id AS site_id,
           p.user_id AS owner_id, p.created_at
    FROM accounts_minerprofile p
    CROSS JOIN LATERAL (VALUES
        ('environmental', 'Environmental compliance document', p.environmental_compliance_document),
        ('licence', 'Licence certificate', p.license_certificate)
    ) AS d(kind, label, url)
    JOIN mining_sites_miningorganisation o ON o.miner_id = p.id
    JOIN mining_sites_miningsite s ON s.organisation_id = o.id
    WHERE coalesce(d.url, '') <> ''
"""

PROFILE_DOCUMENTS = TableMap(
    label="site documents (from miner profiles)",
    target="mining_documentrecord",
    source="accounts_minerprofile",
    source_sql=PROFILE_DOCUMENT_SQL,
    columns={
        "site_id": "site_id",
        "name": "label",
        "category": lambda r: "Imported from previous platform",
        "file": lambda r: legacy_key(r["url"]),
        "uploaded_by_id": "owner_id",
    },
    defaults={"expires_on": None, "status": "pending", **TIMESTAMPS},
    natural_keys=[("site_id", "name", "file")],
    foreign_keys=[ForeignKey("site_id", "mining_minesite"), ForeignKey("uploaded_by_id", "accounts_user", on_missing="null")],
)

# -------------------------------------------------------------- compliance partners

COMPLIANCE_ORGS = TableMap(
    label="organisations (compliance partners)",
    target="organisations_organisation",
    source="accounts_complianceprofile",
    columns={
        "name": lambda r: r["organization_name"].strip()[:255],
        "organisation_type": lambda r: "compliance_partner",
        "email": lambda r: (r["email"] or "").strip()[:254],
        "phone_number": lambda r: (r["phone_number"] or "").strip()[:30],
        "website": lambda r: (r["website_url"] or "").strip()[:200],
        "address": lambda r: (r["primary_office_address"] or "").strip(),
        "country": lambda r: (r["country_of_operation"] or "").strip()[:100] or "Nigeria",
    },
    defaults=ORG_DEFAULTS,
    natural_keys=ORG_KEYS,
    normalizers=ORG_NORMALIZERS,
)

COMPLIANCE_OWNERS = TableMap(
    label="memberships (compliance partner owners)",
    target="organisations_organisationmembership",
    source="accounts_complianceprofile",
    source_sql="SELECT id, id AS organisation_id, user_id, created_at FROM accounts_complianceprofile",
    columns={"organisation_id": "organisation_id", "user_id": "user_id"},
    defaults=MEMBERSHIP_DEFAULTS,
    natural_keys=[("organisation_id", "user_id")],
    foreign_keys=[ForeignKey("organisation_id", "organisations_organisation"), ForeignKey("user_id", "accounts_user")],
)

MAPPINGS = [
    USERS,
    MINING_ORGS,
    MINING_OWNERS,
    SITES,
    LICENCES,
    MINER_DOCUMENTS,
    PROFILE_DOCUMENTS,
    COMPLIANCE_ORGS,
    COMPLIANCE_OWNERS,
]

# Customer-data tables deliberately left in the old database. Listed so the
# scripts can say so.
PENDING = [
    ("miners_minerwallet", [], {}),
    ("accounts_buyerprofile", [], {}),
    ("accounts_compliancerole", [], {}),
    ("accounts_complianceteammember", [], {}),
]

__all__ = ["MAPPINGS", "PENDING", "ForeignKey"]
