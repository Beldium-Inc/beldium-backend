"""Organisation-level verification decided by the mining compliance desk.

A mining company is verified when the desk has verified every declared site
and every submitted document. Readiness is computed on read, never stored.
"""
from mining.models import DocumentRecord


def readiness(organisation):
    sites = list(organisation.mine_sites.all())
    docs = list(DocumentRecord.objects.filter(site__organisation=organisation).exclude(file=""))
    blockers = []
    if not sites:
        blockers.append("No mine site has been declared.")
    unverified_sites = [s.name for s in sites if s.status != "operational"]
    if unverified_sites:
        blockers.append(f"{len(unverified_sites)} site(s) not fully verified: {', '.join(unverified_sites)}.")
    unverified_docs = [d for d in docs if d.status != "verified"]
    if unverified_docs:
        blockers.append(f"{len(unverified_docs)} document(s) not verified.")
    if hasattr(organisation, "compliance_application"):
        blockers.append("This organisation is decided through its compliance application.")
    return {
        "sites_total": len(sites),
        "sites_verified": len(sites) - len(unverified_sites),
        "documents_total": len(docs),
        "documents_verified": len(docs) - len(unverified_docs),
        "blockers": blockers,
        "ready": not blockers,
    }
