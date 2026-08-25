"""Google Calendar synchronization with deduplication."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from google.auth import jwt
from googleapiclient.discovery import build
from loguru import logger

from calendar_sync.config import Settings
from calendar_sync.models.appointment import Appointment, EventMeta
from calendar_sync.utils.helpers import compact_text, extract_odm_number, extract_os_number


def _build_datetime(date_iso: str, time_hhmm: str = "08:00") -> str:
    """Build ISO datetime string from date and time."""
    return f"{date_iso}T{time_hhmm.zfill(5)}:00"


def _add_minutes(date_iso: str, time_hhmm: str, minutes: int) -> str:
    """Add minutes to a datetime."""
    parts = time_hhmm.split(":")
    h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    dt = datetime.strptime(f"{date_iso}T{h:02d}:{m:02d}:00", "%Y-%m-%dT%H:%M:%S")
    dt += timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_date_from_text(text: Optional[str]) -> Optional[str]:
    """Parse date from text like '10/08/2026'."""
    m = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", str(text or ""))
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def _parse_times_from_text(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Parse time range from text."""
    s = str(text or "")
    m = re.search(r"\b(\d{1,2}:\d{2})\s*[\-–]\s*(\d{1,2}:\d{2})\b", s)
    if m:
        return m.group(1).zfill(5), m.group(2).zfill(5)
    m = re.search(r"\b(\d{1,2}:\d{2})\b", s)
    if m:
        return m.group(1).zfill(5), None
    return None, None


def _build_summary(evt: Appointment, meta: EventMeta) -> str:
    """Build Google Calendar event summary."""
    is_odm = evt.source.value == "constatimmo" or meta.type.value == "odm" or bool(
        re.search(r"\bodm\b", evt.text or "", re.IGNORECASE)
    )
    family = "ODM" if is_odm else "OS"
    suffix = "E" if meta.type.value == "entree" else "S"
    return f"{family} {suffix}"


def _get_dedup_family(evt: Appointment, meta: EventMeta) -> str:
    """Determine dedup family (os or odm)."""
    is_odm = evt.source.value == "constatimmo" or meta.type.value == "odm" or bool(
        re.search(r"\bodm\b", evt.text or "", re.IGNORECASE)
    )
    return "odm" if is_odm else "os"


def _get_event_ref_number(evt: Appointment, meta: EventMeta) -> str:
    """Get reference number for deduplication."""
    family = _get_dedup_family(evt, meta)
    if family == "odm":
        by_field = compact_text(evt.odm_number or "")
        by_text = extract_odm_number(f"{evt.text} {evt.description or ''}")
        by_url = extract_odm_number(evt.detail_url or "")
        return by_field or by_text or by_url
    by_field = compact_text(evt.os_number or "")
    by_text = extract_os_number(f"{evt.text} {evt.description or ''}")
    return by_field or by_text


def _build_odm_description(evt: Appointment) -> str:
    """Build ODM event description with contact details."""
    owner = compact_text(evt.owner or "")
    tenant = compact_text(evt.tenant or "")
    phone = compact_text(evt.tenant_mobile or evt.tenant_phone or "")
    contact_parts = [
        f"Proprietaire: {owner}" if owner else "",
        f"Locataire: {tenant}" if tenant else "",
        f"Telephone locataire: {phone}" if phone else "",
    ]
    base = compact_text(evt.description or evt.text or "")
    if not any(contact_parts):
        return base
    return " | ".join(filter(None, [base] + contact_parts))


async def sync_to_google_calendar(events: list[Appointment], settings: Settings) -> None:
    """Sync appointments to Google Calendar."""
    credentials_path = Path(settings.google_credentials_path)
    dry_run = settings.dry_run

    if not credentials_path.exists():
        logger.error(f"[GOOGLE] Credentials file not found: {credentials_path}")
        return

    credentials = json.loads(credentials_path.read_text())
    client_email = credentials["client_email"]
    private_key = credentials["private_key"]

    auth = jwt.Credentials.from_service_account_info(
        credentials,
        scopes=["https://www.googleapis.com/auth/calendar"],
    )
    service = build("calendar", "v3", credentials=auth)

    # Fetch existing events
    now = datetime.now()
    time_min = (now - timedelta(days=14)).isoformat() + "Z"
    time_max = (now + timedelta(days=120)).isoformat() + "Z"

    existing_events = []
    for cal_id in {settings.os_calendar_id, settings.odm_calendar_id}:
        try:
            result = service.events().list(
                calendarId=cal_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=2500,
            ).execute()
            for ev in result.get("items", []):
                ev["_calendarId"] = cal_id
                existing_events.append(ev)
        except Exception as e:
            logger.warning(f"[GOOGLE] Could not list events from {cal_id}: {e}")

    created_count = 0
    skipped_count = 0

    for evt in events:
        meta = evt.meta or EventMeta()
        if meta.type.value == "indisponibilite" or meta.is_trajet:
            skipped_count += 1
            continue

        parsed_date = evt.date or _parse_date_from_text(evt.text) or _parse_date_from_text(evt.description)
        t = _parse_times_from_text(f"{evt.start_time or ''} {evt.end_time or ''} {evt.time_raw or ''} {evt.text or ''}")
        start_time = evt.start_time or t[0] or "08:00"
        end_time = evt.end_time or t[1]

        if not parsed_date:
            logger.warning(f"[SYNC] Date not found, skipping: {evt.text}")
            skipped_count += 1
            continue

        start_iso = _build_datetime(parsed_date, start_time)
        end_iso = _build_datetime(parsed_date, end_time) if end_time else _add_minutes(parsed_date, start_time, 45)

        summary = _build_summary(evt, meta)
        family = _get_dedup_family(evt, meta)
        ref_number = _get_event_ref_number(evt, meta)
        target_cal_id = settings.odm_calendar_id if family == "odm" else settings.os_calendar_id
        start_key = start_iso[:16]

        final_description = _build_odm_description(evt) if family == "odm" else (evt.description or evt.text or "")

        g_event = {
            "summary": summary,
            "location": evt.address or "",
            "description": final_description,
            "start": {"dateTime": start_iso, "timeZone": "Europe/Paris"},
            "end": {"dateTime": end_iso, "timeZone": "Europe/Paris"},
        }

        # Dedup by ref number + start time
        dup_by_ref = None
        if ref_number:
            for ev in existing_events:
                if ev.get("_calendarId") != target_cal_id:
                    continue
                ev_start = (ev.get("start", {}).get("dateTime", ""))[:16]
                if ev_start != start_key:
                    continue
                ev_src = f"{ev.get('summary', '')} {ev.get('description', '')} {ev.get('location', '')}"
                if family == "odm":
                    ev_ref = extract_odm_number(ev_src)
                else:
                    ev_ref = extract_os_number(ev_src)
                if ev_ref == ref_number:
                    dup_by_ref = ev
                    break
            else:
                dup_by_ref = None

        if dup_by_ref:
            skipped_count += 1
            logger.info(f"[SYNC][DUPLICATE] {family.upper()} {ref_number} already exists at {start_iso} - skipped.")
            continue

        # Check by summary + time
        dup_event = None
        candidate_summaries = [summary.lower()]
        for ev in existing_events:
            if (ev.get("_calendarId", target_cal_id)) != target_cal_id:
                continue
            ev_summary = (ev.get("summary", "") or "").lower()
            ev_start = (ev.get("start", {}).get("dateTime", ""))[:16]
            if ev_summary in candidate_summaries and ev_start == start_key:
                dup_event = ev
                break

        if dup_event:
            needs_update = (
                (dup_event.get("summary", "") or "").strip() != summary
                or (dup_event.get("location", "") or "").strip() != (g_event["location"] or "")
                or (dup_event.get("description", "") or "").strip() != (g_event["description"] or "")
            )
            if not needs_update:
                skipped_count += 1
                continue
            if dry_run:
                created_count += 1
                logger.info(f"[DRY_RUN] Would update: {summary} {start_iso} cal={target_cal_id}")
                continue
            try:
                service.events().patch(
                    calendarId=target_cal_id,
                    eventId=dup_event["id"],
                    body={
                        "summary": summary,
                        "location": g_event["location"],
                        "description": g_event["description"],
                    },
                ).execute()
                created_count += 1
                logger.info(f"[GOOGLE] Updated: {summary} ID={dup_event['id']} cal={target_cal_id}")
            except Exception as e:
                logger.error(f"[GOOGLE] Error updating event: {e}")
            continue

        if dry_run:
            created_count += 1
            logger.info(f"[DRY_RUN] Would create: {summary} {start_iso} cal={target_cal_id}")
            continue

        try:
            result = service.events().insert(
                calendarId=target_cal_id, body=g_event
            ).execute()
            created_count += 1
            logger.info(f"[GOOGLE] Created: {summary} ID={result['id']} cal={target_cal_id}")
            existing_events.append({
                "summary": summary,
                "description": g_event["description"],
                "location": g_event["location"],
                "start": {"dateTime": start_iso},
                "_calendarId": target_cal_id,
            })
        except Exception as e:
            logger.error(f"[GOOGLE] Error creating event: {e}")

    logger.info(f"[SYNC] Created: {created_count} | Skipped: {skipped_count}")
