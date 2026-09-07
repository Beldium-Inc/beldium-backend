from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from compliance.models import ComplianceDocument, Personnel


def _delete_replaced(instance, field_name):
    if not instance.pk:
        return
    old = type(instance).objects.filter(pk=instance.pk).only(field_name).first()
    if not old:
        return
    old_file = getattr(old, field_name)
    new_file = getattr(instance, field_name)
    if old_file and old_file.name != getattr(new_file, "name", ""):
        old_file.delete(save=False)


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
        instance.file.delete(save=False)


@receiver(post_delete, sender=Personnel)
def delete_personnel_files(sender, instance, **kwargs):
    if instance.cv:
        instance.cv.delete(save=False)
    if instance.certificate:
        instance.certificate.delete(save=False)
