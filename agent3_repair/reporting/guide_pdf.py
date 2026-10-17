"""Build a client-facing v4 PDF without source citations or source-page images."""

from __future__ import annotations

import re
from html import escape
from io import BytesIO
from typing import Any


def _client_text(value: Any) -> str:
    """Drop provenance-oriented text that is intended for internal review."""
    text = str(value or "").strip()
    if re.search(
        r"(?i)\b(source|manual|service bulletin|document|evidence|retrieval|retrieved|citation|corpus|chunk|source_id)\b|\b(?:ifixit|nhtsa|tsb_[\w-]+)\b",
        text,
    ):
        return ""
    return text


def _scope_label(scope: Any) -> str:
    return {
        "vehicle_specific": "For this vehicle",
        "platform_wide": "For this vehicle platform",
        "make_wide": "For vehicles from this make",
        "some_vehicles": "For vehicles matching the conditions described",
        "generic_maintenance": "General maintenance guidance",
    }.get(str(scope), "Applicability not specified")


def build_guide_pdf(guide: dict[str, Any]) -> tuple[bytes, int]:
    """Return the client PDF and zero (no optional illustration renderer is bundled)."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
    except ImportError as exc:
        raise RuntimeError(
            "PDF export requires ReportLab. Install dependencies with: python -m pip install -r requirements.txt"
        ) from exc

    output = BytesIO()
    vehicle = guide.get("vehicle") or {}
    doc = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=17 * mm,
        leftMargin=17 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Car Sound Doctor — Repair Guide",
        author="Car Sound Doctor",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ClientGuideTitle", parent=styles["Title"], alignment=TA_CENTER,
        textColor=colors.HexColor("#17324d"), spaceAfter=8,
    )
    heading_style = ParagraphStyle(
        "ClientGuideHeading", parent=styles["Heading2"],
        textColor=colors.HexColor("#17324d"), spaceBefore=10, spaceAfter=5,
    )
    small_style = ParagraphStyle("ClientGuideSmall", parent=styles["BodyText"], fontSize=8, leading=10)

    def para(text: Any, style=styles["BodyText"]) -> Paragraph:
        return Paragraph(escape(str(text or "")).replace("\n", "<br/>"), style)

    statuses = {
        "guide_available": "Repair guide",
        "diagnosis_only": "Inspection guide",
        "insufficient_information": "More information needed",
        "professional_assistance_required": "Professional service recommended",
    }
    fault = guide.get("fault") or {}
    repair = guide.get("repair_guide") or {}
    story = [
        para("Car Sound Doctor — Repair Guide", title_style),
        para(
            f"{vehicle.get('year', '')} {vehicle.get('make', '')} {vehicle.get('model', '')}"
            + (f" · {vehicle['engine']}" if vehicle.get("engine") else ""),
            styles["Heading2"],
        ),
        para(f"Guide: {statuses.get(guide.get('status'), 'Repair guide')}"),
        para(f"Issue: {_client_text(fault.get('name')) or 'Not specified'}"),
    ]

    applicability = guide.get("applicability") or {}
    if applicability:
        note = _client_text(applicability.get("note"))
        story.extend([
            para("Applicability", heading_style),
            para(_scope_label(applicability.get("scope")) + (f" — {note}" if note else "")),
        ])

    warnings = [
        text for text in (_client_text(item) for item in (guide.get("safety_warnings") or []))
        if text
    ]
    if warnings:
        story.append(para("Safety Warnings", heading_style))
        story.extend(para("• " + warning) for warning in warnings)

    parts = guide.get("parts_to_replace") or []
    visible_parts = []
    for part in parts:
        name = _client_text(part.get("name"))
        if not name:
            continue
        role = {"replace": "Replace", "inspect": "Inspect", "suspect": "Possible cause"}.get(
            part.get("role"), "Component"
        )
        detail = f"{role}: {name}"
        if part.get("part_number"):
            detail += f" · Part number: {part['part_number']}"
        visible_parts.append(detail)
    if visible_parts:
        story.append(para("Parts and Components", heading_style))
        story.extend(para(item) for item in visible_parts)

    steps = repair.get("steps") or []
    story.append(para("Steps", heading_style))
    visible_step_count = 0
    for step in steps:
        action = _client_text(step.get("action"))
        if not action:
            continue
        visible_step_count += 1
        story.append(para(f"{step.get('number', visible_step_count)}. {action}"))
        if step.get("applicability_scope"):
            story.append(para(f"Applies to: {_scope_label(step['applicability_scope'])}", small_style))
        warning = _client_text(step.get("warning"))
        if warning:
            story.append(para("Warning: " + warning))
        story.append(Spacer(1, 3))
    if not visible_step_count:
        story.append(para("No DIY steps are available for this result."))

    tools = [text for text in (_client_text(item) for item in (repair.get("tools") or [])) if text]
    if tools:
        story.append(para("Tools", heading_style))
        story.append(para(", ".join(tools)))

    recommendation = _client_text(guide.get("recommendation"))
    if recommendation:
        story.append(para("Recommendation", heading_style))
        story.append(para(recommendation))

    limitations = [
        text for text in (_client_text(item) for item in (guide.get("limitations") or []))
        if text
    ]
    if limitations:
        story.append(para("Limitations", heading_style))
        story.extend(para("• " + item) for item in limitations)

    doc.build(story)
    return output.getvalue(), 0
