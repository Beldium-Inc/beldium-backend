# Makes the audit table append only in the database itself, so no code path,
# admin action or hand written SQL can change or remove a row.
#
# UPDATE and DELETE are refused on PostgreSQL and SQLite. TRUNCATE is left
# alone on purpose, so a local or throwaway database can still be flushed;
# the flush command is guarded against real environments.

from django.db import migrations

TABLE = "quality_qualityauditentry"

POSTGRES_UP = f"""
CREATE OR REPLACE FUNCTION quality_audit_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Audit entries are append only.';
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER quality_audit_no_update BEFORE UPDATE ON {TABLE}
    FOR EACH ROW EXECUTE FUNCTION quality_audit_append_only();
CREATE TRIGGER quality_audit_no_delete BEFORE DELETE ON {TABLE}
    FOR EACH ROW EXECUTE FUNCTION quality_audit_append_only();
"""

POSTGRES_DOWN = f"""
DROP TRIGGER IF EXISTS quality_audit_no_update ON {TABLE};
DROP TRIGGER IF EXISTS quality_audit_no_delete ON {TABLE};
DROP FUNCTION IF EXISTS quality_audit_append_only();
"""

SQLITE_UP = [
    f"CREATE TRIGGER quality_audit_no_update BEFORE UPDATE ON {TABLE} "
    "BEGIN SELECT RAISE(ABORT, 'Audit entries are append only.'); END;",
    f"CREATE TRIGGER quality_audit_no_delete BEFORE DELETE ON {TABLE} "
    "BEGIN SELECT RAISE(ABORT, 'Audit entries are append only.'); END;",
]

SQLITE_DOWN = [
    "DROP TRIGGER IF EXISTS quality_audit_no_update;",
    "DROP TRIGGER IF EXISTS quality_audit_no_delete;",
]


def add_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(POSTGRES_UP)
    elif vendor == "sqlite":
        for statement in SQLITE_UP:
            schema_editor.execute(statement)


def drop_triggers(apps, schema_editor):
    vendor = schema_editor.connection.vendor
    if vendor == "postgresql":
        schema_editor.execute(POSTGRES_DOWN)
    elif vendor == "sqlite":
        for statement in SQLITE_DOWN:
            schema_editor.execute(statement)


class Migration(migrations.Migration):

    dependencies = [
        ("quality", "0008_audit_entry"),
    ]

    operations = [
        migrations.RunPython(add_triggers, drop_triggers),
    ]
