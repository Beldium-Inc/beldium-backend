"""Keep stored files in step with the rows that point at them.

Django deletes neither a replaced file nor an orphaned one on its own, so
without this every re-upload leaves a copy behind in the bucket. Mirrors
``processing.signals`` exactly, adapted to the mining models that hold files.
"""
from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from mining.models import CorrectiveSubmission, DocumentRecord, Evidence, LicenceDoc

FILE_FIELDS = {
    Evidence: ["file"],
    LicenceDoc: ["file"],
    DocumentRecord: ["file"],
    CorrectiveSubmission: ["file"],
}


def _delete_replaced(instance, field_name):
    if not instance.pk:
        return
    old = type(instance).objects.filter(pk=instance.pk).only(field_name).first()
    if not old:
        return
    old_file = getattr(old, field_name)
    new_file = getattr(instance, field_name)
    if old_file and old_file.name != getattr(new_file, "name", ""):
        transaction.on_commit(lambda: old_file.storage.delete(old_file.name))


@receiver(pre_save, sender=Evidence)
@receiver(pre_save, sender=LicenceDoc)
@receiver(pre_save, sender=DocumentRecord)
@receiver(pre_save, sender=CorrectiveSubmission)
def delete_replaced_file(sender, instance, **kwargs):
    for field_name in FILE_FIELDS[sender]:
        _delete_replaced(instance, field_name)


@receiver(post_delete, sender=Evidence)
@receiver(post_delete, sender=LicenceDoc)
@receiver(post_delete, sender=DocumentRecord)
@receiver(post_delete, sender=CorrectiveSubmission)
def delete_file(sender, instance, **kwargs):
    for field_name in FILE_FIELDS[sender]:
        stored = getattr(instance, field_name)
        if stored:
            transaction.on_commit(lambda f=stored: f.storage.delete(f.name))
