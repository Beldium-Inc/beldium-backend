"""Compiling oversight reports from the processing register.

A report is a point-in-time extract, rendered to PDF and stored so it can be
handed to someone outside the platform. The numbers are read from the register
at the moment of generation and never recomputed: a report that changed after
it was issued would be worthless as a record of what was known when a decision
was taken.

Each kind answers one question, so the desk is not handed a single document
that buries the thing it needed.
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

from processing import scoring
from processing.models import (
    EnvironmentalAlert,
    Incident,
    Inspection,
    NonConformity,
    ProcessingApplication,
    Processor,
    ProcessorStatus,
)

# Matches the platform's own palette, so a printed report and the screen it was
# generated from do not look like different products.
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

# Reporting periods, resolved to a date range at generation time.
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


# A4 less the 20mm margins either side.
TEXT_WIDTH = 170 * mm


def _table(header, rows, widths, styles, aligns=None):
    """A table that survives a page break with its header intact.

    Overflowing the text width does not raise; reportlab silently runs the
    table off the page and wraps cells mid-token, so the widths are asserted.
    """
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
    """The headline figures, as a row of labelled numbers."""
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
    """Header and footer drawn on every page."""

    def draw(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(20 * mm, A4[1] - 12 * mm, "BELDIUM PROCESSING COMPLIANCE")
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


def _scoped(queryset, scope, field="region"):
    return queryset if scope == ALL_REGIONS else queryset.filter(**{field: scope})


def _since(queryset, start, field):
    """Lower-bound a queryset by date, whatever kind of column it is.

    Comparing a `date` against a DateTimeField under USE_TZ leaves Django to
    coerce it into a naive datetime, which it warns about and which lands in
    the wrong place either side of midnight. Datetime columns are given an
    aware midnight in the current timezone instead.
    """
    if start is None:
        return queryset
    column = queryset.model._meta.get_field(field)
    if isinstance(column, models.DateTimeField):
        start = timezone.make_aware(datetime.combine(start, time.min), timezone.get_current_timezone())
    return queryset.filter(**{f"{field}__gte": start})


# --- the four report bodies --------------------------------------------------


def _national(story, styles, scope, start):
    processors = _scoped(Processor.objects.all(), scope)
    applications = ProcessingApplication.objects.all()
    if scope != ALL_REGIONS:
        applications = applications.filter(processor__region=scope)

    scores = list(processors.values_list("compliance_score", flat=True))
    story.append(_metrics(styles, [
        ("Registered processors", processors.count()),
        ("Approved", processors.filter(status=ProcessorStatus.APPROVED).count()),
        ("Conditional", processors.filter(status=ProcessorStatus.CONDITIONAL).count()),
        ("Suspended", processors.filter(status=ProcessorStatus.SUSPENDED).count()),
        ("Mean score", round(sum(scores) / len(scores)) if scores else 0),
    ]))

    story.append(Paragraph("Register by region", styles["heading"]))
    regions = {}
    for processor in processors:
        row = regions.setdefault(processor.region or processor.state or "Unassigned", {"n": 0, "score": 0, "approved": 0, "suspended": 0})
        row["n"] += 1
        row["score"] += processor.compliance_score
        row["approved"] += processor.status == ProcessorStatus.APPROVED
        row["suspended"] += processor.status == ProcessorStatus.SUSPENDED
    story.append(_table(
        ["Region", "Processors", "Approved", "Suspended", "Mean score"],
        [[name, r["n"], r["approved"], r["suspended"], round(r["score"] / r["n"]) if r["n"] else 0]
         for name, r in sorted(regions.items(), key=lambda kv: -kv[1]["n"])],
        [55 * mm, 28 * mm, 28 * mm, 28 * mm, 31 * mm], styles, [None, "r", "r", "r", "r"],
    ))

    story.append(Paragraph("Processors", styles["heading"]))
    story.append(_table(
        ["Processor", "Type", "State", "Status", "Score"],
        [[p.name, p.get_processing_type_display(), p.state or "—", p.get_status_display(), p.compliance_score]
         for p in processors.order_by("-compliance_score")],
        [58 * mm, 40 * mm, 24 * mm, 27 * mm, 21 * mm], styles, [None, None, None, None, "r"],
    ))

    story.append(Paragraph("Applications in the period", styles["heading"]))
    rows = _since(applications, start, "created_at").order_by("-submitted_on")
    if rows:
        story.append(_table(
            ["Reference", "Applicant", "Stage", "Decision", "Complete"],
            [[a.reference, a.company, a.get_stage_display(), a.get_decision_display() if a.decision else "—",
              f"{scoring.completeness(a)}%"] for a in rows],
            [34 * mm, 54 * mm, 28 * mm, 34 * mm, 20 * mm], styles, [None, None, None, None, "r"],
        ))
    else:
        story.append(Paragraph("No applications were lodged in this period.", styles["note"]))


def _environmental(story, styles, scope, start):
    alerts = _since(_scoped(EnvironmentalAlert.objects.select_related("processor"), scope, "processor__region"), start, "detected_on")
    incidents = _since(_scoped(Incident.objects.select_related("processor"), scope, "processor__region"), start, "reported_on")

    story.append(_metrics(styles, [
        ("Exceedances", alerts.count()),
        ("Critical", alerts.filter(severity=EnvironmentalAlert.Severity.CRITICAL).count()),
        ("Still open", alerts.filter(status=EnvironmentalAlert.Status.OPEN).count()),
        ("Incidents", incidents.count()),
        ("Severe", incidents.filter(severity=Incident.Severity.SEVERE).count()),
    ]))

    story.append(Paragraph("Threshold exceedances", styles["heading"]))
    rows = [[a.reference, a.facility_name or "—", a.parameter, f"{a.reading} / {a.threshold}",
             a.get_severity_display(), a.get_status_display(), f"{a.detected_on:%d %b %Y}"]
            for a in alerts.order_by("-detected_on")]
    if rows:
        story.append(_table(
            ["Reference", "Facility", "Parameter", "Reading / limit", "Severity", "Status", "Detected"],
            rows, [34 * mm, 28 * mm, 24 * mm, 24 * mm, 16 * mm, 22 * mm, 22 * mm], styles,
        ))
    else:
        story.append(Paragraph("No exceedances were recorded in this period.", styles["note"]))

    story.append(Paragraph("Reportable incidents", styles["heading"]))
    rows = [[i.reference, i.facility_name or "—", i.incident_type, i.get_severity_display(),
             i.get_status_display(), f"{i.reported_on:%d %b %Y}"] for i in incidents.order_by("-reported_on")]
    if rows:
        story.append(_table(
            ["Reference", "Facility", "Type", "Severity", "Status", "Reported"],
            rows, [34 * mm, 30 * mm, 34 * mm, 18 * mm, 30 * mm, 24 * mm], styles,
        ))
    else:
        story.append(Paragraph("No incidents were reported in this period.", styles["note"]))


def _inspections(story, styles, scope, start):
    inspections = _since(_scoped(Inspection.objects.select_related("processor"), scope, "processor__region"), start, "scheduled_for")
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
    rows = [[i.reference, i.facility_name or "—", i.get_inspection_type_display(),
             f"{i.scheduled_for:%d %b %Y}" if i.scheduled_for else "To be confirmed",
             i.inspector_name or "Unassigned", i.get_status_display(), i.outcome or "—"]
            for i in inspections.order_by("-scheduled_for")]
    if rows:
        story.append(_table(
            ["Reference", "Facility", "Type", "Scheduled", "Inspector", "Status", "Outcome"],
            rows, [34 * mm, 26 * mm, 20 * mm, 22 * mm, 24 * mm, 20 * mm, 24 * mm], styles,
        ))
    else:
        story.append(Paragraph("No inspections fall in this period.", styles["note"]))


def _non_conformities(story, styles, scope, start):
    findings = _since(
        _scoped(NonConformity.objects.select_related("processor", "application"), scope, "processor__region"),
        start, "raised_on",
    )
    today = timezone.localdate()
    open_findings = findings.exclude(status=NonConformity.Status.CLOSED)

    story.append(_metrics(styles, [
        ("Findings raised", findings.count()),
        ("Critical", findings.filter(severity=NonConformity.Severity.CRITICAL).count()),
        ("Still open", open_findings.count()),
        ("Overdue", open_findings.filter(due_on__lt=today).count()),
        ("Closed", findings.filter(status=NonConformity.Status.CLOSED).count()),
    ]))

    story.append(Paragraph("Findings", styles["heading"]))

    def company_of(finding):
        """`company` is a serialiser field, not a column; derive it the same way."""
        if finding.application_id:
            return finding.application.company
        return finding.processor.name if finding.processor_id else "—"

    rows = [[f.reference, company_of(f), f.get_section_display(), f.get_severity_display(),
             f.title, f"{f.due_on:%d %b %Y}", "Overdue" if f.is_overdue else f.get_status_display()]
            for f in findings.order_by("severity", "due_on")]
    if rows:
        story.append(_table(
            ["Reference", "Company", "Section", "Severity", "Finding", "Due", "Status"],
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
        title=f"{title} — {reference}", author="Beldium Processing Compliance",
    )

    who = generated_by.full_name or generated_by.email if generated_by else "the platform"
    story = [
        Paragraph(title, styles["title"]),
        Paragraph(
            f"{scope} · {period_label} · compiled {timezone.localdate():%d %B %Y} by {who}",
            styles["subtitle"],
        ),
        Paragraph(
            "This is a point-in-time extract of the Beldium processing compliance register. "
            "Figures are those held at the moment of compilation and are not restated afterwards.",
            styles["note"],
        ),
        Spacer(1, 10),
    ]
    BODIES[kind](story, styles, scope, start)

    draw = _chrome(reference, title)
    document.build(story, onFirstPage=draw, onLaterPages=draw)
    return buffer.getvalue(), document.page, period_label
