"""Governance Copilot: answers drawn from the governance register itself.

This is retrieval, not generation. A question is matched against policies,
risks, regulatory findings, stakeholders and documents the caller may read,
and the answer is assembled only from those records, each one cited. Nothing
is written that a record does not say, and the response is always marked for
human review before anyone relies on it.
"""
import re

from django.db.models import Q

from governance import models as m
from governance import permissions as perms

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "in", "is", "it", "of", "on",
    "or", "our", "that", "the", "this", "to", "what", "which", "who", "why", "with", "draft", "give",
    "show", "list", "me", "about", "any", "all", "do", "does", "we", "us", "there", "please", "suggest",
    "identify", "compare", "briefing", "executive",
}

MAX_RESULTS = 8

KIND_WORDS = {
    "policy": ("polic",),
    "risk": ("risk",),
    "finding": ("finding", "regulat", "requirement"),
    "stakeholder": ("stakeholder",),
    "document": ("document", "report"),
}


def terms(question):
    words = re.findall(r"[a-z0-9]+", question.lower())
    return [w for w in words if len(w) > 2 and w not in STOPWORDS][:12]


def _search(queryset, fields, words):
    query = Q()
    for word in words:
        for field in fields:
            query |= Q(**{f"{field}__icontains": word})
    return queryset.filter(query) if words else queryset.none()


def _score(text, words):
    text = text.lower()
    return sum(text.count(word) for word in words)


def answer(user, question):
    words = terms(question)
    hits = []

    for policy in _search(m.Policy.objects.select_related("owner"), ["title", "domain", "reference", "sector", "status", "executive_summary", "objectives", "regulatory_instruments"], words)[:25]:
        text = " ".join([policy.title, policy.domain, policy.reference, policy.sector, policy.status, policy.executive_summary, policy.objectives])
        hits.append({
            "kind": "policy",
            "id": str(policy.pk),
            "reference": policy.reference,
            "title": policy.title,
            "detail": f"{policy.get_status_display()}, {policy.version}, domain {policy.domain}.",
            "score": _score(text, words),
        })

    for risk in _search(m.Risk.objects.all(), ["title", "description", "reference", "category", "sector", "status", "controls", "treatment_plan"], words)[:25]:
        text = " ".join([risk.title, risk.description, risk.reference, risk.category, risk.sector, risk.status, risk.controls])
        hits.append({
            "kind": "risk",
            "id": str(risk.pk),
            "reference": risk.reference,
            "title": risk.title,
            "detail": (
                f"{risk.get_status_display()}, inherent {risk.inherent_score}, residual {risk.residual_score}, "
                f"controls {risk.get_control_effectiveness_display().lower()}."
                + (f" Treatment: {risk.treatment_plan}" if risk.treatment_plan else "")
            ),
            "score": _score(text, words),
        })

    for finding in _search(m.RegulatoryFinding.objects.all(), ["requirement", "instrument", "authority", "gap", "reference", "sector", "status"], words)[:25]:
        text = " ".join([finding.requirement, finding.instrument, finding.authority, finding.gap, finding.reference, finding.sector, finding.status])
        hits.append({
            "kind": "finding",
            "id": str(finding.pk),
            "reference": finding.reference,
            "title": finding.requirement,
            "detail": f"{finding.get_status_display()}. Authority {finding.authority}." + (f" Gap: {finding.gap}" if finding.gap else ""),
            "score": _score(text, words),
        })

    for stakeholder in _search(m.Stakeholder.objects.filter(is_active=True), ["name", "engagement_plan"], words)[:25]:
        hits.append({
            "kind": "stakeholder",
            "id": str(stakeholder.pk),
            "reference": stakeholder.get_category_display(),
            "title": stakeholder.name,
            "detail": f"Influence {stakeholder.influence}/10, interest {stakeholder.interest}/10.",
            "score": _score(stakeholder.name + " " + stakeholder.engagement_plan, words),
        })

    readable = perms.classifications(user)
    for document in _search(m.GovernanceDocument.objects.filter(classification__in=readable), ["name", "original_filename"], words)[:25]:
        hits.append({
            "kind": "document",
            "id": str(document.pk),
            "reference": document.get_classification_display(),
            "title": document.name,
            "detail": f"{document.get_document_type_display()}, uploaded {document.created_at:%Y-%m-%d}.",
            "score": _score(document.name, words),
        })

    # A question that names a kind of record ("risks", "policies") ranks that
    # kind first.
    named = {kind for kind, stems in KIND_WORDS.items() if any(word.startswith(stem) for word in words for stem in stems)}
    for hit in hits:
        if hit["kind"] in named:
            hit["score"] += 100
    hits.sort(key=lambda hit: hit["score"], reverse=True)
    hits = hits[:MAX_RESULTS]
    for hit in hits:
        hit.pop("score")

    if not words:
        summary = "Ask about a policy, risk, regulation, stakeholder or document by name or subject."
    elif not hits:
        summary = "No governance record matches this question. Nothing has been inferred beyond the register."
    else:
        lines = [f"{len(hits)} record(s) in the governance register match this question:"]
        for hit in hits:
            lines.append(f"[{hit['reference']}] {hit['title']}: {hit['detail']}")
        summary = "\n".join(lines)

    return {
        "question": question,
        "terms": words,
        "answer": summary,
        "citations": hits,
        "method": "retrieval",
        "requires_review": True,
    }
