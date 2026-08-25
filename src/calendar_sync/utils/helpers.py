"""Utility functions for date parsing, text processing, and French month names."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

# French month name mapping
FRENCH_MONTHS: dict[str, int] = {
    "janv": 1, "janvier": 1,
    "fev": 2, "fevr": 2, "fevrier": 2, "février": 2,
    "mars": 3,
    "avr": 4, "avril": 4,
    "mai": 5,
    "juin": 6,
    "juil": 7, "juillet": 7,
    "aout": 8, "août": 8,
    "sept": 9, "septembre": 9,
    "oct": 10, "octobre": 10,
    "nov": 11, "novembre": 11,
    "dec": 12, "decembre": 12, "décembre": 12,
}


def compact_text(value: Optional[str]) -> str:
    """Normalize whitespace in text."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_unicode(value: Optional[str]) -> str:
    """Normalize unicode and whitespace (NBSP etc.)."""
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def strip_accents_lower(value: str) -> str:
    """Remove diacritics and lowercase for matching."""
    import unicodedata

    normalized = unicodedata.normalize("NFD", value)
    return re.sub(r"[\u0300-\u036f]", "", normalized).lower().strip()


def parse_french_month(raw: Optional[str]) -> Optional[int]:
    """Parse a French month name to its number (1-12)."""
    m = re.sub(r"\.", "", str(raw or "")).lower().strip()
    return FRENCH_MONTHS.get(m)


def to_iso_date(year: int, month: int, day: int) -> str:
    """Format date to ISO string YYYY-MM-DD."""
    return f"{year:04d}-{month:02d}-{day:02d}"


def parse_week_start_from_label(week_label: Optional[str]) -> Optional[str]:
    """Parse week header like '3 - 9 aout 2026' to ISO date."""
    label = compact_text(week_label).lower()

    # Pattern: "3 - 9 aout 2026"
    m = re.search(r"(\d{1,2})\s*[\u2013\-]\s*\d{1,2}\s+([a-zéû\.]+)\s+(\d{4})", label)
    if m:
        day = int(m.group(1))
        month = parse_french_month(m.group(2))
        year = int(m.group(3))
        if day and month and year:
            return to_iso_date(year, month, day)

    # Pattern: "3 aout - 9 aout 2026"
    m = re.search(r"(\d{1,2})\s+([a-zéû\.]+)\s*[\u2013\-]\s*\d{1,2}\s+[a-zéû\.]+\s+(\d{4})", label)
    if m:
        day = int(m.group(1))
        month = parse_french_month(m.group(2))
        year = int(m.group(3))
        if day and month and year:
            return to_iso_date(year, month, day)

    return None


def add_days(iso_date: Optional[str], days: int) -> Optional[str]:
    """Add days to an ISO date string."""
    if not iso_date:
        return None
    parts = iso_date.split("-")
    dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]), 12, 0, 0) + timedelta(days=days)
    return to_iso_date(dt.year, dt.month, dt.day)


def week_monday_iso(week_offset: int = 0) -> str:
    """Get Monday of current week (or offset week) as ISO date."""
    now = datetime.now()
    day = now.weekday()  # Monday = 0 ... Sunday = 6
    monday = now - timedelta(days=day - week_offset * 7)
    monday = monday.replace(hour=12, minute=0, second=0, microsecond=0)
    return to_iso_date(monday.year, monday.month, monday.day)


def parse_time_parts(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Extract start/end times from text like '11:50 - 13:00'."""
    src = str(text or "")
    m = re.search(r"\b(\d{1,2}:\d{2})\s*[\-–]\s*(\d{1,2}:\d{2})\b", src)
    if m:
        return m.group(1).zfill(5), m.group(2).zfill(5)
    m = re.search(r"\b(\d{1,2}:\d{2})\b", src)
    if m:
        return m.group(1).zfill(5), None
    return None, None


def extract_os_number(text: Optional[str]) -> str:
    """Extract OS number from event text."""
    src = str(text or "").replace("\u00a0", " ")

    patterns = [
        r"\bos\s*n(?:[°ºo]|(?:um(?:e|é)ro))?\s*[:#-]?\s*(\d{5,})\b",
        r"\bordre\s*de\s*service\b[^\d]{0,20}(\d{5,})\b",
        r"\bos\b[^\d]{0,12}(\d{5,})\b",
    ]

    for pattern in patterns:
        m = re.search(pattern, src, re.IGNORECASE)
        if m and m.group(1):
            return m.group(1)

    m = re.search(r"\b(\d{6,})\b", src)
    return m.group(1) if m else ""


def extract_odm_number(text: Optional[str]) -> str:
    """Extract ODM number from text or URL."""
    src = str(text or "")
    m = re.search(r"odmRedirect=(\d{6,})", src, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\bodm\b[^\d]{0,20}(\d{6,})", src, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{6,})\b", src)
    return m.group(1) if m else ""


def format_snexi_details(fields: dict) -> str:
    """Format Snexi detail fields into pipe-delimited string."""
    lines = []
    if fields.get("address"):
        lines.append(f"Adresse: {fields['address']}")
    if fields.get("owner"):
        lines.append(f"Proprietaire: {fields['owner']}")
    if fields.get("manager"):
        lines.append(f"Gestionnaire: {fields['manager']}")
    if fields.get("tenant"):
        lines.append(f"Locataire: {fields['tenant']}")
    if fields.get("tenantMobile") or fields.get("tenant_mobile"):
        phone = fields.get("tenantMobile") or fields.get("tenant_mobile", "")
        lines.append(f"Portable locataire: {phone}")
    if fields.get("comment"):
        lines.append(f"Commentaire: {fields['comment']}")
    if fields.get("keyPickupPlace") or fields.get("key_pickup_place"):
        place = fields.get("keyPickupPlace") or fields.get("key_pickup_place", "")
        lines.append(f"Lieu recuperation cles: {place}")
    if fields.get("keyDropPlace") or fields.get("key_drop_place"):
        place = fields.get("keyDropPlace") or fields.get("key_drop_place", "")
        lines.append(f"Lieu depot cles: {place}")
    if fields.get("floor"):
        lines.append(f"Etage: {fields['floor']}")
    if fields.get("door"):
        lines.append(f"Porte: {fields['door']}")
    if fields.get("digicode"):
        lines.append(f"Digicode: {fields['digicode']}")
    if fields.get("building"):
        lines.append(f"Batiment: {fields['building']}")
    if fields.get("detailUrl") or fields.get("detail_url"):
        url = fields.get("detailUrl") or fields.get("detail_url", "")
        lines.append(f"Fiche: {url}")
    return " | ".join(lines)


def format_constatimmo_details(fields: dict) -> str:
    """Format Constatimmo detail fields into pipe-delimited string."""
    tenant_phone = fields.get("tenantMobile") or fields.get("tenant_mobile") or fields.get("tenantPhone") or fields.get("tenant_phone") or ""
    lines = []
    if fields.get("owner"):
        lines.append(f"Proprietaire: {fields['owner']}")
    if fields.get("manager"):
        lines.append(f"Gestionnaire: {fields['manager']}")
    if fields.get("tenant"):
        lines.append(f"Locataire: {fields['tenant']}")
    if tenant_phone:
        lines.append(f"Telephone locataire: {tenant_phone}")
    if fields.get("comment"):
        lines.append(f"Commentaire: {fields['comment']}")
    if fields.get("keyPickupPlace") or fields.get("key_pickup_place"):
        place = fields.get("keyPickupPlace") or fields.get("key_pickup_place", "")
        lines.append(f"Lieu recuperation cles: {place}")
    if fields.get("keyDropPlace") or fields.get("key_drop_place"):
        place = fields.get("keyDropPlace") or fields.get("key_drop_place", "")
        lines.append(f"Lieu depot cles: {place}")
    if fields.get("floor"):
        lines.append(f"Etage: {fields['floor']}")
    if fields.get("door"):
        lines.append(f"Porte: {fields['door']}")
    if fields.get("digicode"):
        lines.append(f"Digicode: {fields['digicode']}")
    if fields.get("building"):
        lines.append(f"Batiment: {fields['building']}")
    if fields.get("detailUrl") or fields.get("detail_url"):
        url = fields.get("detailUrl") or fields.get("detail_url", "")
        lines.append(f"Fiche: {url}")
    return " | ".join(lines)
