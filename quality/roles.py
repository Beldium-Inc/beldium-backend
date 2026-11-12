"""The 12 Q&C roles and the access grid, from Figure 5 of the Build Pack
(BLD-QC-DOC-VIS-021, "Who can do what: access by role and record").

This module only holds the data. It is seeded into the database by a data
migration, so the grid is stored as rows and can be read back by the API.
The permission checks live in quality/permissions.py.

Letters in the grid:
  C create, R read, U update, A approve or decide.
  A trailing "*" means a restricted view (the pack footnote "restricted view").

The footnotes and field level rules (PRS-014 section 5, PDD-015 sheet Role
Access) are still owed by Q&C.
TODO(Q&C Partner): send PRS-014 section 5 so the restricted views can be
narrowed field by field. Until then a restricted view is reported as such and
the existing record scoping applies.
"""

QC = "QC"
CEO = "CEO"
TL = "TL"
SUP = "SUP"
SMP = "SMP"
INS = "INS"
LAB = "LAB"
LOG = "LOG"
EXP = "EXP"
BYR = "BYR"
ADM = "ADM"
LGL = "LGL"

# Code, name, internal to Beldium. The long names are a reading of the guide
# and should be checked against PRS-014.
ROLES = [
    (QC, "Q&C Partner", True),
    (CEO, "CEO", True),
    (TL, "Technical Lead", True),
    (SUP, "Supplier", False),
    (SMP, "Sampler", False),
    (INS, "Inspector", False),
    (LAB, "Laboratory", False),
    (LOG, "Logistics (custody transport)", False),
    (EXP, "Export agent", False),
    (BYR, "Buyer", False),
    (ADM, "Beldium Admin", True),
    (LGL, "Legal Counsel", True),
]

ROLE_CODES = [code for code, _, _ in ROLES]

# Every user who holds any role except these must pass a second factor
# before tokens are issued (pack: internal and partner users).
NO_SECOND_FACTOR = {SUP, BYR, EXP}

# Roles that always belong to an outside organisation.
ORGANISATION_ROLES = {SUP, SMP, INS, LAB, LOG, EXP, BYR}

ACTION_LETTERS = {"C": "create", "R": "read", "U": "update", "A": "approve"}
ACTIONS = ["create", "read", "update", "approve"]

RECORD_TYPES = [
    ("supplier_profile", "Supplier profile and licence"),
    ("batch_declaration", "Batch declaration"),
    ("site_inspection_report", "Site inspection report"),
    ("sampling_record", "Sampling record"),
    ("chain_of_custody", "Chain of Custody"),
    ("test_request", "Test request"),
    ("assay_report", "Assay report"),
    ("quality_decision", "Quality decision and HOLD"),
    ("ncr_capa", "NCR / CAPA"),
    ("evidence_package", "Evidence package"),
    ("verification_certificate", "Verification certificate"),
    ("partner_register", "Partner register"),
    ("audit_log", "Audit log"),
    ("reports", "Reports and dashboards"),
    ("users_and_roles", "Users and roles"),
]

RECORD_TYPE_CODES = [code for code, _ in RECORD_TYPES]

_COLUMNS = [QC, CEO, TL, SUP, SMP, INS, LAB, LOG, EXP, BYR, ADM, LGL]

# One row per record type, one cell per column above. "" means cannot see.
# Audit log: Beldium Admin has no access (rule AT-05, decision 8). The pack
# specification table still shows it and will be corrected (DQ-14).
_GRID_ROWS = {
    "supplier_profile":         ["RA", "R", "R", "CRU", "", "", "", "", "", "", "RU", "R"],
    "batch_declaration":        ["RA", "R", "R", "CR", "R", "R", "", "", "", "", "R", ""],
    "site_inspection_report":   ["RA", "R", "R", "", "", "CR", "", "", "", "", "R", ""],
    "sampling_record":          ["RA", "R", "R", "", "CRU", "", "R*", "", "", "", "R", ""],
    "chain_of_custody":         ["RA", "R", "R", "", "CRU", "", "CRU", "CRU", "", "", "R", ""],
    "test_request":             ["CRA", "R", "R", "", "", "", "R*", "", "", "", "R", ""],
    "assay_report":             ["RA", "R", "R", "", "", "", "CR", "", "", "", "R", ""],
    "quality_decision":         ["CRA", "R", "R", "", "", "", "", "", "", "", "R", ""],
    "ncr_capa":                 ["CRUA", "R", "R", "RU", "RU", "RU", "RU", "RU", "", "", "R", ""],
    "evidence_package":         ["RA", "R", "R", "", "", "", "", "", "", "", "R", ""],
    "verification_certificate": ["CRA", "R", "R", "R", "", "", "", "", "R", "R", "R", "R"],
    "partner_register":         ["CRUA", "RA", "R", "", "R", "R", "R", "R", "", "", "RU", "R"],
    "audit_log":                ["R", "R", "R", "", "", "", "", "", "", "", "", "R"],
    "reports":                  ["R", "R", "R", "", "", "", "", "", "", "", "R", ""],
    "users_and_roles":          ["A", "R", "RU", "", "", "", "", "", "", "", "CRU", ""],
}


def grid():
    """The grid as {role_code: {record_type: (letters, restricted)}}, blanks left out."""
    result = {code: {} for code in _COLUMNS}
    for record_type in RECORD_TYPE_CODES:
        for code, cell in zip(_COLUMNS, _GRID_ROWS[record_type]):
            if not cell:
                continue
            restricted = cell.endswith("*")
            result[code][record_type] = (cell.rstrip("*"), restricted)
    return result


def letters_to_actions(letters):
    """C, R, U, A to action names. Any right on a record includes reading it,
    because nobody can create, update or decide on what they cannot see."""
    actions = {ACTION_LETTERS[letter] for letter in letters}
    if actions:
        actions.add("read")
    return [action for action in ACTIONS if action in actions]
