"""Event classification and business appointment filtering."""

from __future__ import annotations

import re
from typing import Optional

from calendar_sync.models.appointment import (
    Appointment,
    AppointmentType,
    EventColor,
    EventMeta,
)


def classify_event(evt: Appointment) -> EventMeta:
    """Classify an event by color and type based on CSS style and text content."""
    style = (evt.style or "").lower()
    computed_bg = (evt.computed_bg or "").lower()
    text = (evt.text or "").lower()

    style_ref = f"{style} {computed_bg}"

    is_red = "rgb(207, 36, 36)" in style_ref or "rgb(207,36,36)" in style_ref
    is_blue = bool(re.search(r"rgb\(18,\s*17,\s*171\)|rgba\(18,\s*17,\s*171", style_ref))
    is_green = bool(re.search(r"rgb\(17,\s*138,\s*123\)|rgba\(17,\s*138,\s*123", style_ref))
    is_purple = (
        "156, 39, 176" in style_ref
        or "123, 31, 162" in style_ref
        or "103, 58, 183" in style_ref
        or "128, 0, 128" in style_ref
        or bool(re.search(r"\bodm\b", text))
    )

    text_says_entree = bool(
        re.search(r"\bedl\s+entr[ée]e\b|\bentr[ée]e\b|\bentrer\b|\bentrant\b", text)
    )
    text_says_sortie = bool(
        re.search(r"\bedl\s+sortie\b|\bsortie\b|\bsortir\b|\bsortant\b", text)
    )
    text_says_indispo = bool(re.search(r"indisponibilit[ée]", text))
    is_trajet = bool(re.search(r"\btrajet\b", text))

    type_ = AppointmentType.AUTRE
    if is_red or text_says_indispo:
        type_ = AppointmentType.INDISPONIBILITE
    elif text_says_entree or is_blue:
        type_ = AppointmentType.ENTREE
    elif text_says_sortie or is_green:
        type_ = AppointmentType.SORTIE
    elif is_purple:
        type_ = AppointmentType.ODM

    color = EventColor.AUTRE
    if is_purple:
        color = EventColor.VIOLET
    elif is_green:
        color = EventColor.VERT
    elif is_blue:
        color = EventColor.BLEU
    elif is_red:
        color = EventColor.ROUGE

    return EventMeta(color=color, type=type_, is_trajet=is_trajet)


def build_business_appointments(
    events: list[Appointment],
) -> tuple[list[Appointment], dict]:
    """Filter events to keep only business-relevant ones (entree/sortie/odm).

    Returns:
        Tuple of (filtered_events, stats_dict)
    """
    business: list[Appointment] = []
    sortie_count = 0
    entree_count = 0
    odm_count = 0
    skipped_red = 0
    skipped_trajet = 0
    source_counts = {"snexi": 0, "constatimmo": 0, "unknown": 0}

    for evt in events:
        meta = classify_event(evt)

        if meta.type == AppointmentType.INDISPONIBILITE:
            skipped_red += 1
            continue
        if meta.is_trajet:
            skipped_trajet += 1
            continue
        if meta.type not in (AppointmentType.ENTREE, AppointmentType.SORTIE, AppointmentType.ODM):
            continue

        if meta.type == AppointmentType.SORTIE:
            sortie_count += 1
        if meta.type == AppointmentType.ENTREE:
            entree_count += 1
        if meta.type == AppointmentType.ODM:
            odm_count += 1

        source = evt.source.value if evt.source else "unknown"
        if source in source_counts:
            source_counts[source] += 1
        else:
            source_counts["unknown"] += 1

        evt.meta = meta
        business.append(evt)

    stats = {
        "total": len(events),
        "kept": len(business),
        "sortieCount": sortie_count,
        "entreeCount": entree_count,
        "odmCount": odm_count,
        "skippedRed": skipped_red,
        "skippedTrajet": skipped_trajet,
        "sourceCounts": source_counts,
    }

    return business, stats
