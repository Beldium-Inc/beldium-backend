"""Compiling oversight reports from the mining register.

Mirrors ``processing.reports`` in shape and rendering, with one deliberate
deviation: the mining app has no ``ComplianceReport``-equivalent model to
persist a generated PDF against (``mining.models`` carries no such table, and
none was added here — adding one would produce a migration the task's
constraints rule out). A mining report is therefore built and streamed back
on request rather than stored; see ``mining.views.MiningReportView``.
"""
from datetime import date, datetime, time, timedelta
from io import BytesIO

from django.db import models
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from mining.models import EnvRecord, Inspection, MineSite, NonConformity, SafetyIncident, SiteStatus

INK = colors.HexColor("#101E3D")
MUTED = colors.HexColor("#5A6785")
RULE = colors.HexColor("#D8DEEA")
BAND = colors.HexColor("#F2F5FA")


class ReportKind:
    NATIONAL = "national_compliance"
    ENVIRONMENTAL = "environmental_exceedances"
    INSPECTIONS = "inspection_programme"
    NON_CONFORMITIES = "non_conformity_register"

    CHOICES = [
        (NATIONAL, "National compliance summary"),
        (ENVIRONMENTAL, "Environmental exceedance summary"),
        (INSPECTIONS, "Inspection programme review"),
        (NON_CONFORMITIES, "Non-conformity register"),
    ]

    LABELS = dict(CHOICES)


ALL_REGIONS = "All regions"

PERIODS = {
    "last_month": "Last month",
    "last_quarter": "Last quarter",
    "year_to_date": "Year to date",
    "all_time": "All time",
}


def resolve_period(period):
    """(start date or None, human label). None means no lower bound."""
    today = timezone.localdate()
    if period == "last_month":
        start = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        return start, f"{start:%B %Y}"
    if period == "last_quarter":
        start = today - timedelta(days=90)
        return start, f"{start:%d %b %Y} – {today:%d %b %Y}"
    if period == "year_to_date":
        start = date(today.year, 1, 1)
        return start, f"1 Jan {today.year} – {today:%d %b %Y}"
    return None, "All time"


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, textColor=INK, spaceAfter=4, alignment=0),
        "subtitle": ParagraphStyle("s", parent=base["Normal"], fontSize=9.5, textColor=MUTED, spaceAfter=14),
        "heading": ParagraphStyle("h", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, textColor=INK, spaceBefore=16, spaceAfter=6),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=9, textColor=INK, leading=13),
        "note": ParagraphStyle("n", parent=base["Normal"], fontSize=8, textColor=MUTED, leading=11),
        "cell": ParagraphStyle("c", parent=base["Normal"], fontSize=8, textColor=INK, leading=10.5),
        "cellRight": ParagraphStyle("cr", parent=base["Normal"], fontSize=8, textColor=INK, leading=10.5, alignment=TA_RIGHT),
    }


TEXT_WIDTH = 170 * mm


def _table(header, rows, widths, styles, aligns=None):
    assert sum(widths) <= TEXT_WIDTH + 0.5, f"table is {sum(widths) / mm:.0f}mm wide, page fits 170mm"
    body = [[Paragraph(f"<b>{h}</b>", styles["cell"]) for h in header]]
    for row in rows:
        body.append([
            Paragraph(str(value), styles["cellRight" if aligns and aligns[i] == "r" else "cell"])
            for i, value in enumerate(row)
        ])
    table = Table(body, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BAND),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.3, RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def _metrics(styles, pairs):
    cells = [[Paragraph(f"<font size=16><b>{value}</b></font><br/><font size=7.5 color='#5A6785'>{label.upper()}</font>", styles["cell"]) for label, value in pairs]]
    table = Table(cells, colWidths=[(170 * mm) / len(pairs)] * len(pairs), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), BAND),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _chrome(reference, title):
    def draw(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, A4[1] - 12 * mm, "BELDIUM MINING COMPLIANCE")
        canvas.drawRightString(A4[0] - 20 * mm, A4[1] - 12 * mm, reference)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, A4[1] - 15 * mm, A4[0] - 20 * mm, A4[1] - 15 * mm)

        canvas.setFont("Helvetica", 7.5)
        canvas.line(20 * mm, 14 * mm, A4[0] - 20 * mm, 14 * mm)
        canvas.drawString(20 * mm, 10 * mm, title)
        canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    return draw


def _scoped(queryset, scope, field="state"):
    return queryset if scope == ALL_REGIONS else queryset.filter(**{field: scope})


def _since(queryset, start, field):
    if start is None:
        return queryset
    column = queryset.model._meta.get_field(field)
    if isinstance(column, models.DateTimeField):
        start = timezone.make_aware(datetime.combine(start, time.min), timezone.get_current_timezone())
    return queryset.filter(**{f"{field}__gte": start})


def _national(story, styles, scope, start):
    sites = _scoped(MineSite.objects.all(), scope)

    scores = list(sites.values_list("compliance_score", flat=True))
    story.append(_metrics(styles, [
        ("Registered sites", sites.count()),
        ("Operational", sites.filter(status=SiteStatus.OPERATIONAL).count()),
        ("Under review", sites.filter(status=SiteStatus.UNDER_REVIEW).count()),
        ("Suspended", sites.filter(status=SiteStatus.SUSPENDED).count()),
        ("Mean score", round(sum(scores) / len(scores)) if scores else 0),
    ]))

    story.append(Paragraph("Register by state", styles["heading"]))
    regions = {}
    for site in sites:
        row = regions.setdefault(site.state or "Unassigned", {"n": 0, "score": 0, "operational": 0, "suspended": 0})
        row["n"] += 1
        row["score"] += site.compliance_score
        row["operational"] += site.status == SiteStatus.OPERATIONAL
        row["suspended"] += site.status == SiteStatus.SUSPENDED
    story.append(_table(
        ["State", "Sites", "Operational", "Suspended", "Mean score"],
        [[name, r["n"], r["operational"], r["suspended"], round(r["score"] / r["n"]) if r["n"] else 0]
         for name, r in sorted(regions.items(), key=lambda kv: -kv[1]["n"])],
        [55 * mm, 28 * mm, 28 * mm, 28 * mm, 31 * mm], styles, [None, "r", "r", "r", "r"],
    ))

    story.append(Paragraph("Sites", styles["heading"]))
    story.append(_table(
        ["Site", "Mineral", "State", "Status", "Score"],
        [[s.name, s.mineral, s.state or "—", s.get_status_display(), s.compliance_score]
         for s in sites.order_by("-compliance_score")],
        [58 * mm, 40 * mm, 24 * mm, 27 * mm, 21 * mm], styles, [None, None, None, None, "r"],
    ))


def _environmental(story, styles, scope, start):
    records = _since(_scoped(EnvRecord.objects.select_related("site"), scope, "site__state"), start, "measured_on")
    incidents = _since(_scoped(SafetyIncident.objects.select_related("site"), scope, "site__state"), start, "date")

    story.append(_metrics(styles, [
        ("Readings", records.count()),
        ("Breaches", records.filter(status=EnvRecord.Status.BREACH).count()),
        ("Watch", records.filter(status=EnvRecord.Status.WATCH).count()),
        ("Safety incidents", incidents.count()),
        ("Critical", incidents.filter(severity=SafetyIncident.Severity.CRITICAL).count()),
    ]))

    story.append(Paragraph("Environmental readings", styles["heading"]))
    rows = [[r.site.name if r.site_id else "—", r.metric, f"{r.value} / {r.limit}", r.get_status_display(), f"{r.measured_on:%d %b %Y}"]
            for r in records.order_by("-measured_on")]
    if rows:
        story.append(_table(
            ["Site", "Metric", "Reading / limit", "Status", "Measured"],
            rows, [40 * mm, 34 * mm, 36 * mm, 30 * mm, 30 * mm], styles,
        ))
    else:
        story.append(Paragraph("No environmental readings were recorded in this period.", styles["note"]))

    story.append(Paragraph("Safety incidents", styles["heading"]))
    rows = [[i.site.name if i.site_id else "—", i.type, i.get_severity_display(), i.get_status_display(), f"{i.date:%d %b %Y}"]
            for i in incidents.order_by("-date")]
    if rows:
        story.append(_table(
            ["Site", "Type", "Severity", "Status", "Date"],
            rows, [42 * mm, 42 * mm, 30 * mm, 28 * mm, 28 * mm], styles,
        ))
    else:
        story.append(Paragraph("No safety incidents were reported in this period.", styles["note"]))


def _inspections(story, styles, scope, start):
    inspections = _since(_scoped(Inspection.objects.select_related("site"), scope, "site__state"), start, "scheduled_for")
    completed = inspections.filter(status=Inspection.Status.COMPLETED).count()
    total = inspections.count()

    story.append(_metrics(styles, [
        ("Inspections", total),
        ("Completed", completed),
        ("Scheduled", inspections.filter(status=Inspection.Status.SCHEDULED).count()),
        ("Awaiting a date", inspections.filter(status=Inspection.Status.REQUESTED).count()),
        ("Completion", f"{round(completed / total * 100) if total else 0}%"),
    ]))

    story.append(Paragraph("Programme", styles["heading"]))
    rows = [[i.reference, i.site.name if i.site_id else "—", i.get_type_display(),
             f"{i.scheduled_for:%d %b %Y}" if i.scheduled_for else "To be confirmed",
             i.inspector_name or "Unassigned", i.get_status_display(), i.result or "—"]
            for i in inspections.order_by("-scheduled_for")]
    if rows:
        story.append(_table(
            ["Reference", "Site", "Type", "Scheduled", "Inspector", "Status", "Result"],
            rows, [34 * mm, 26 * mm, 20 * mm, 22 * mm, 24 * mm, 20 * mm, 24 * mm], styles,
        ))
    else:
        story.append(Paragraph("No inspections fall in this period.", styles["note"]))


def _non_conformities(story, styles, scope, start):
    findings = _since(_scoped(NonConformity.objects.select_related("site"), scope, "site__state"), start, "created_at")
    today = timezone.localdate()
    open_findings = findings.exclude(status=NonConformity.Status.CLOSED)

    story.append(_metrics(styles, [
        ("Findings raised", findings.count()),
        ("Critical", findings.filter(severity=NonConformity.Severity.CRITICAL).count()),
        ("Still open", open_findings.count()),
        ("Overdue", open_findings.filter(deadline__lt=today).count()),
        ("Closed", findings.filter(status=NonConformity.Status.CLOSED).count()),
    ]))

    story.append(Paragraph("Findings", styles["heading"]))
    rows = [[f.reference, f.site.name if f.site_id else "—", f.category or "—", f.get_severity_display(),
             f.title, f"{f.deadline:%d %b %Y}", "Overdue" if f.is_overdue else f.get_status_display()]
            for f in findings.order_by("severity", "deadline")]
    if rows:
        story.append(_table(
            ["Reference", "Site", "Category", "Severity", "Finding", "Due", "Status"],
            rows, [34 * mm, 26 * mm, 23 * mm, 16 * mm, 33 * mm, 19 * mm, 19 * mm], styles,
        ))
    else:
        story.append(Paragraph("No findings were raised in this period.", styles["note"]))


BODIES = {
    ReportKind.NATIONAL: _national,
    ReportKind.ENVIRONMENTAL: _environmental,
    ReportKind.INSPECTIONS: _inspections,
    ReportKind.NON_CONFORMITIES: _non_conformities,
}


def build(kind, scope, period, *, reference, generated_by=None):
    """Render one report. Returns ``(pdf bytes, page count, period label)``."""
    start, period_label = resolve_period(period)
    styles = _styles()
    title = ReportKind.LABELS[kind]

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm, topMargin=22 * mm, bottomMargin=20 * mm,
        title=f"{title} — {reference}", author="Beldium Mining Compliance",
    )

    who = generated_by.full_name or generated_by.email if generated_by else "the platform"
    story = [
        Paragraph(title, styles["title"]),
        Paragraph(
            f"{scope} · {period_label} · compiled {timezone.localdate():%d %B %Y} by {who}",
            styles["subtitle"],
        ),
        Paragraph(
            "This is a point-in-time extract of the Beldium mining compliance register. "
            "Figures are those held at the moment of compilation and are not restated afterwards.",
            styles["note"],
        ),
        Spacer(1, 10),
    ]
    BODIES[kind](story, styles, scope, start)

    draw = _chrome(reference, title)
    document.build(story, onFirstPage=draw, onLaterPages=draw)
    return buffer.getvalue(), document.page, period_label
