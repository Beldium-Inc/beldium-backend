"""Governance reports, compiled from the register at the moment of download.

Each kind is a list of titled tables built from live records. The same tables
render to PDF for circulation or to CSV for analysis. Every download is
written to the audit trail with the kind and format, so there is a record of
who took what out of the workspace and when.
"""
import csv
import io

from django.db.models import Count
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from governance import intelligence
from governance import models as m

INK = colors.HexColor("#101E3D")
MUTED = colors.HexColor("#5A6785")
RULE = colors.HexColor("#D8DEEA")
BAND = colors.HexColor("#F2F5FA")

KINDS = {
    "executive": "Executive governance summary",
    "policy": "Policy implementation",
    "risk": "Risk assessment",
    "regulatory": "Regulatory findings",
    "compliance": "Cross sector compliance",
    "stakeholder": "Stakeholder analysis",
    "roadmap": "Roadmap and KPIs",
}

FORMATS = {"pdf", "csv"}


def _name(user):
    return (getattr(user, "full_name", "") or user.email) if user else ""


def _date(value):
    return value.strftime("%Y-%m-%d") if value else ""


def policy_table():
    rows = [
        [p.reference, p.title, p.domain, _name(p.owner), p.version, _date(p.review_date), p.get_status_display()]
        for p in m.Policy.objects.select_related("owner").order_by("reference")
    ]
    return ("Policies", ["Reference", "Title", "Domain", "Owner", "Version", "Review date", "Status"], rows)


def risk_table():
    rows = [
        [
            r.reference, r.title, r.get_category_display(), r.get_sector_display(), _name(r.owner),
            str(r.inherent_score), r.get_control_effectiveness_display(), str(r.residual_score),
            _date(r.due_date), r.get_status_display(),
        ]
        for r in m.Risk.objects.select_related("owner").order_by("-residual_likelihood", "reference")
    ]
    rows.sort(key=lambda row: int(row[7]), reverse=True)
    return ("Enterprise risks", ["Ref", "Risk", "Category", "Sector", "Owner", "Inherent", "Controls", "Residual", "Due", "Status"], rows)


def finding_table():
    rows = [
        [f.reference, f.requirement, f.authority, f.gap, _name(f.recorded_by), _date(f.created_at), f.get_status_display()]
        for f in m.RegulatoryFinding.objects.select_related("recorded_by").order_by("reference")
    ]
    return ("Regulatory findings", ["Ref", "Requirement", "Authority", "Gap", "Recorded by", "Date", "Status"], rows)


def compliance_table():
    def show(value):
        return "Not tracked" if value is None else str(value)

    rows = [
        [
            row["name"], show(row["registered"]), show(row["verified"]),
            "" if row["verified_rate"] is None else f"{row['verified_rate']}%",
            show(row["open_findings"]), show(row["expiring_30d"]), show(row["open_requests"]),
        ]
        for row in intelligence.sector_summary()
    ]
    return (
        "Sector compliance",
        ["Sector", "Registered", "Verified", "Verified rate", "Open findings", "Expiring in 30 days", "Open requests"],
        rows,
    )


def stakeholder_table():
    rows = [
        [s.name, s.get_category_display(), str(s.influence), str(s.interest), _name(s.owner), _date(s.last_engaged_on)]
        for s in m.Stakeholder.objects.filter(is_active=True).select_related("owner")
    ]
    return ("Stakeholders", ["Name", "Category", "Influence", "Interest", "Owner", "Last engaged"], rows)


def milestone_table():
    rows = [
        [ms.policy.reference, ms.get_phase_display(), ms.name, _name(ms.owner), _date(ms.start_date), _date(ms.end_date), ms.kpi, f"{ms.progress}%", ms.get_status_display()]
        for ms in m.Milestone.objects.select_related("policy", "owner")
    ]
    return ("Milestones", ["Policy", "Phase", "Milestone", "Owner", "Start", "End", "KPI", "Progress", "Status"], rows)


def overview_table():
    policies = dict(m.Policy.objects.values_list("status").annotate(n=Count("id")))
    risks = dict(m.Risk.objects.values_list("status").annotate(n=Count("id")))
    rows = [
        ["Approved or published policies", str(policies.get("approved", 0) + policies.get("published", 0))],
        ["Policies awaiting review", str(policies.get("submitted", 0) + policies.get("under_review", 0) + policies.get("recommended", 0))],
        ["Open enterprise risks", str(sum(n for status, n in risks.items() if status != "closed"))],
        ["Escalated risks", str(risks.get("escalated", 0))],
        ["Regulatory findings pending verification", str(m.RegulatoryFinding.objects.filter(status="pending_verification").count())],
        ["Delayed milestones", str(m.Milestone.objects.filter(status="delayed").count())],
    ]
    return ("Overview", ["Measure", "Value"], rows)


def build(kind):
    if kind == "executive":
        return [overview_table(), risk_table(), policy_table(), compliance_table()]
    if kind == "policy":
        return [policy_table(), milestone_table()]
    if kind == "risk":
        return [risk_table()]
    if kind == "regulatory":
        return [finding_table()]
    if kind == "compliance":
        return [compliance_table()]
    if kind == "stakeholder":
        return [stakeholder_table()]
    if kind == "roadmap":
        return [milestone_table()]
    raise KeyError(kind)


def render_csv(kind, tables):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([KINDS[kind], f"Generated {timezone.now():%Y-%m-%d %H:%M} UTC"])
    for title, header, rows in tables:
        writer.writerow([])
        writer.writerow([title])
        writer.writerow(header)
        writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def render_pdf(kind, tables, generated_by):
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=KINDS[kind],
        author="Beldium Governance",
    )
    styles = getSampleStyleSheet()
    heading = ParagraphStyle("heading", parent=styles["Heading1"], textColor=INK, fontSize=18, spaceAfter=4)
    sub = ParagraphStyle("sub", parent=styles["Normal"], textColor=MUTED, fontSize=9)
    section = ParagraphStyle("section", parent=styles["Heading2"], textColor=INK, fontSize=12, spaceBefore=10, spaceAfter=6)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8, leading=10, textColor=INK)
    head = ParagraphStyle("head", parent=cell, textColor=MUTED, fontName="Helvetica-Bold")

    story = [
        Paragraph("BELDIUM GOVERNANCE, RISK AND POLICY INTELLIGENCE", sub),
        Paragraph(KINDS[kind], heading),
        Paragraph(f"Generated {timezone.now():%d %B %Y, %H:%M} UTC by {generated_by}. Compiled from the live register at the time of download.", sub),
        Spacer(1, 6 * mm),
    ]
    for title, header, rows in tables:
        story.append(Paragraph(title, section))
        if not rows:
            story.append(Paragraph("No records.", sub))
            continue
        data = [[Paragraph(h, head) for h in header]] + [[Paragraph(str(value), cell) for value in row] for row in rows]
        table = Table(data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(table)
    document.build(story)
    return buffer.getvalue()
