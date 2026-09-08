from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from compliance.models import ConditionEvidence, ComplianceDocument, Personnel


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


@receiver(pre_save, sender=ComplianceDocument)
def delete_replaced_document(sender, instance, **kwargs):
    _delete_replaced(instance, "file")


@receiver(pre_save, sender=Personnel)
def delete_replaced_personnel_files(sender, instance, **kwargs):
    _delete_replaced(instance, "cv")
    _delete_replaced(instance, "certificate")


@receiver(post_delete, sender=ComplianceDocument)
def delete_document_file(sender, instance, **kwargs):
    if instance.file:
        transaction.on_commit(lambda: instance.file.storage.delete(instance.file.name))


@receiver(post_delete, sender=Personnel)
def delete_personnel_files(sender, instance, **kwargs):
    if instance.cv:
        transaction.on_commit(lambda: instance.cv.storage.delete(instance.cv.name))
    if instance.certificate:
        transaction.on_commit(lambda: instance.certificate.storage.delete(instance.certificate.name))


@receiver(post_delete, sender=ConditionEvidence)
def delete_condition_evidence_file(sender, instance, **kwargs):
    if instance.file:
        transaction.on_commit(lambda: instance.file.storage.delete(instance.file.name))
