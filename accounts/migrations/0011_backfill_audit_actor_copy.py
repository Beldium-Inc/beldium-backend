# Fills the new actor copy on audit events that already exist, so they keep
# naming who acted if that account is removed later. Only the two new,
# empty columns are written; nothing else on the rows changes.

from django.db import migrations


def backfill(apps, schema_editor):
    Event = apps.get_model("accounts", "AccountAuditEvent")
    User = apps.get_model("accounts", "User")
    actor_ids = Event.objects.filter(actor__isnull=False, actor_email="").values_list("actor_id", flat=True).distinct()
    for user in User.objects.filter(pk__in=list(actor_ids)).only("pk", "email", "first_name", "last_name"):
        name = f"{user.first_name} {user.last_name}".strip()
        Event.objects.filter(actor_id=user.pk, actor_email="").update(actor_email=user.email, actor_name=name)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_audit_actor_copy"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
