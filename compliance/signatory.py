"""A declaration is signed with the signer's full name, exactly as recorded.

Each application says whose full name that is: the representative on an
organisation application, the signed in account on a Q&C organisation
application, and the full legal name on a Q&C professional application.

Only spaces at either end and repeated spaces inside the name are ignored.
Any other difference, including letter case, means the names do not match.
"""

import re


def normalise_name(value):
    return re.sub(r"\s+", " ", value or "").strip()


def names_match(signed, full_name):
    full_name = normalise_name(full_name)
    return bool(full_name) and normalise_name(signed) == full_name


def mismatch_message(full_name, whose):
    """What to tell the signer, given the name they had to type."""
    full_name = normalise_name(full_name)
    if not full_name:
        return f"Enter {whose} first. The signatory name must match it exactly."
    return f"The signatory name must match {whose} exactly: {full_name}."
