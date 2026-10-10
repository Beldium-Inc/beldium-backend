"""Deterministic review gates and idempotent expiry monitoring."""
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.audit import record_account_event
from accounts.models import User
from common.exceptions import ConflictError
from logistics import metadata, models as m

EDITABLE = {'draft', 'awaiting_information', 'rejected'}
REVIEWABLE = {'under_review', 'awaiting_information', 'conditionally_approved'}


def audit(request, company, event, **metadata):
    record_account_event(request, 'logistics.' + event, company_id=str(company.pk), organisation_id=str(company.organisation_id), **metadata)


def notify(company, title, body):
    recipients = User.objects.filter(is_active=True).filter(
        Q(is_staff=True) |
        Q(organisation_memberships__organisation=company.organisation, organisation_memberships__is_active=True) |
        Q(logisticsaccessgrant__company=company, logisticsaccessgrant__is_active=True) |
        # The desk reviewer who claimed it holds no grant, but is the one waiting.
        Q(logistics_reviews__company=company)
    ).distinct()
    m.Notification.objects.bulk_create([m.Notification(company=company, recipient=user, title=title, body=body) for user in recipients])


def assert_state(application, states):
    if application.status not in states:
        raise ConflictError(f'This action is unavailable while the application is {application.status}.', code='invalid_application_state')


def resume_review(application):
    """Hand the application back to the desk once nothing waits on the applicant.

    A request or a rejected document parks the application on
    ``awaiting_information``, and a decision is only recorded from
    ``under_review``. Without this the reviewer accepts the replacement and
    still cannot decide, because nothing else moves the status back.
    """
    if application.status != 'awaiting_information':
        return
    if application.requests.exclude(status='accepted').exists():
        return
    if application.documents.filter(is_current=True, status='rejected').exists():
        return
    application.status = 'under_review'
    application.save(update_fields=['status', 'updated_at'])


def initialise(application):
    mineral = any('mineral' in service.lower() for service in application.company.services)
    m.DomainReview.objects.bulk_create([
        m.DomainReview(application=application, key=key, applicable=key != 'mineral' or mineral)
        for key in m.Domain.values
    ])
    application.domain_weights = {key: 1 for key in m.Domain.values}
    application.save(update_fields=['domain_weights'])


def valid_documents(application):
    return application.documents.filter(is_current=True).exclude(status='rejected').filter(Q(expires_on__isnull=True) | Q(expires_on__gte=timezone.localdate()))


def evidence_checks(application, data):
    """Reviewer-only advice on an upload, read from the file before it is stored."""
    found = metadata.inspect(data['file'])
    duplicates = application.documents.filter(is_current=True, file_metadata__sha256=found['sha256']).exclude(
        document_type=data['document_type']).values_list('title', flat=True)
    return {'file_metadata': found,
            'review_tags': metadata.advise(found, issued_on=data.get('issued_on'), duplicates=list(duplicates))}


def progress(application):
    sections = list(application.sections.all())
    required = [section for section in sections if section.applicable]
    supplied = set(valid_documents(application).values_list('domain', flat=True))
    missing_data = [section.key for section in required if not section.data]
    missing_evidence = [section.key for section in required if section.key not in supplied]
    checks = {
        'account': bool(application.created_by.is_active and application.created_by.email_verified_at),
        'company': bool(application.company.contact_email and application.company.services),
        'fleet': application.company.vehicles.filter(is_active=True).exists(),
        'drivers': application.company.drivers.filter(is_active=True).exists(),
        'sections': bool(required) and not missing_data,
        'documents': bool(required) and not missing_evidence,
        'requests': not application.requests.exclude(status='accepted').exists(),
    }
    return {'percent': round(sum(checks.values()) / len(checks) * 100), 'checks': checks,
            'missing_sections': missing_data, 'missing_document_domains': missing_evidence,
            'documents_submitted': application.documents.filter(is_current=True).count(),
            'domains_required': len(required)}


def risk(application):
    sections = [s for s in application.sections.all() if s.applicable]
    total = sum(application.domain_weights.get(s.key, 1) for s in sections)
    score = round(sum(s.score * application.domain_weights.get(s.key, 1) for s in sections) / total, 2) if total else 0
    return {'compliance_score': score, 'risk_band': 'low' if score >= 85 else 'medium' if score >= 65 else 'high',
            'policy_version': application.policy_version, 'weights': application.domain_weights,
            'domains': {s.key: {'score': s.score, 'status': s.status} for s in sections}}


def require_complete(application):
    result = progress(application)
    if result['percent'] != 100:
        raise ConflictError('Complete the application and resolve outstanding information requests.', code='application_incomplete', details=result)
    invalid = application.documents.filter(is_current=True).filter(Q(status='rejected') | Q(expires_on__lt=timezone.localdate()))
    if invalid.exists():
        raise ConflictError('Replace rejected or expired evidence before submission.', code='invalid_evidence')


def require_approval(application, conditional=False):
    require_complete(application)
    if application.documents.filter(is_current=True).exclude(status='verified').exists():
        raise ConflictError('All current evidence must be verified.', code='documents_not_verified')
    allowed = {'passed', 'attention'} if conditional else {'passed'}
    if application.sections.filter(applicable=True).exclude(status__in=allowed).exists():
        raise ConflictError('Complete the required domain sign-offs.', code='domains_not_cleared')
    if not conditional and application.conditions.filter(cleared_at__isnull=True).exists():
        raise ConflictError('Clear all approval conditions first.', code='conditions_not_cleared')
    if not conditional and application.company.restrictions.filter(resolved_at__isnull=True).exists():
        raise ConflictError('Resolve active service restrictions before full approval.', code='scope_restricted')


def credential_event(company, key, kind, message, document=None):
    event, created = m.MonitoringEvent.objects.get_or_create(event_key=key, defaults={
        'company': company, 'kind': kind, 'message': message, 'document': document})
    if created:
        notify(company, 'Credential ' + kind, message)
    return event, created


@transaction.atomic
def monitor_expiries():
    """No file parsing or claimed government/telematics integration."""
    today = timezone.localdate()
    created = 0
    for application in m.LogisticsApplication.objects.select_for_update().select_related('company'):
        if application.status in {'draft', 'rejected'}:
            continue
        company = application.company
        for document in application.documents.filter(is_current=True, expires_on__isnull=False):
            days = (document.expires_on - today).days
            if days > 30:
                continue
            kind = 'expired' if days < 0 else 'expiring'
            _, new = credential_event(company, f'document:{document.pk}:{kind}', kind,
                                      f'{document.title} expires on {document.expires_on}.', document)
            created += int(new)
            if days < 0 and document.service_scope:
                # All monitor runs lock the application, preventing duplicate restrictions.
                if not company.restrictions.filter(source_document=document, automatic=True, resolved_at__isnull=True).exists():
                    m.ScopeRestriction.objects.create(company=company, service_scope=document.service_scope,
                        reason=f'Credential expired: {document.title}', source_document=document, automatic=True)
        for model, fields in [(m.Vehicle, ['insurance_expiry', 'roadworthiness_expiry']), (m.Driver, ['licence_expiry', 'medical_expiry'])]:
            for obj in model.objects.filter(company=company, is_active=True):
                for field in fields:
                    expiry = getattr(obj, field)
                    if (expiry - today).days <= 30:
                        kind = 'expired' if expiry < today else 'expiring'
                        _, new = credential_event(company, f'{model.__name__}:{obj.pk}:{field}:{expiry}:{kind}', kind,
                            f'{model.__name__} {obj.pk}: {field} is due on {expiry}.')
                        created += int(new)
    return {'new_events': created}
