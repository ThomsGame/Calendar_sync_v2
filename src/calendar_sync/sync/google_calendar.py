"""Google Calendar synchronization with deduplication.

Authentication uses OAuth2 user-consent flow (token.json + client_id/secret).
On first run without a valid token.json the script will print an authorization
URL — open it in a browser, authenticate with the Google account, and paste the
resulting code back. The token is then saved to token.json for future runs.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httplib2

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from loguru import logger

# Corporate CA bundles in this environment have issues with Python's stricter SSL.
# Google's endpoints are trusted; we disable verification for the httplib2 transport
# used internally by google-api-python-client.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "0")
_HTTP = httplib2.Http(disable_ssl_certificate_validation=True)

from calendar_sync.config import Settings
from calendar_sync.models.appointment import Appointment, EventMeta
from calendar_sync.utils.helpers import compact_text, extract_odm_number, extract_os_number

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/gmail.readonly",
]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _load_or_refresh_credentials(settings: Settings) -> Optional[Credentials]:
    """Load token.json, refresh if expired, run consent flow if missing."""
    token_path = Path(settings.google_token_path)
    client_id = settings.google_oauth_client_id
    client_secret = settings.google_oauth_client_secret

    creds: Optional[Credentials] = None

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception as e:
            logger.warning(f"[GOOGLE] Could not load token.json: {e}")
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            # Use requests with SSL disabled to work around corporate CA issues
            import requests
            session = requests.Session()
            session.verify = False
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            from google.auth.transport.requests import Request as GRequest
            creds.refresh(GRequest(session=session))
            token_path.write_text(creds.to_json())
            logger.info("[GOOGLE] Token refreshed and saved.")
            return creds
        except Exception as e:
            logger.warning(f"[GOOGLE] Token refresh failed: {e} — will re-authenticate.")
            creds = None

    # Need to run consent flow
    if not client_id or not client_secret:
        logger.error(
            "[GOOGLE] No valid token.json and GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET "
            "are not set. Cannot authenticate with Google."
        )
        return None

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)

    # Try localhost server first (works in interactive terminal with a browser)
    try:
        logger.info("[GOOGLE] Opening browser for OAuth2 consent...")
        creds = flow.run_local_server(port=0, open_browser=True)
    except Exception:
        # Fallback: print URL and ask for code (headless environments)
        logger.info("[GOOGLE] Browser not available — using manual consent flow.")
        flow2 = InstalledAppFlow.from_client_config(client_config, SCOPES)
        auth_url, _ = flow2.authorization_url(prompt="consent")
        logger.info(f"\n[GOOGLE] Please open this URL in your browser:\n\n  {auth_url}\n")
        code = input("[GOOGLE] Enter the authorization code: ").strip()
        flow2.fetch_token(code=code)
        creds = flow2.credentials

    token_path.write_text(creds.to_json())
    logger.info(f"[GOOGLE] Token saved to {token_path}")
    return creds


def _credentials_from_refresh_token(settings: Settings) -> Optional[Credentials]:
    """Build Credentials directly from a stored refresh token (no token.json).

    Used by the dashboard where each user's tokens are stored encrypted in the
    database rather than on disk.
    """
    if not settings.google_refresh_token:
        return None
    if not settings.google_oauth_client_id or not settings.google_oauth_client_secret:
        logger.error("[GOOGLE] GOOGLE_OAUTH_CLIENT_ID / SECRET not set — cannot use refresh token.")
        return None

    creds = Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=SCOPES,
    )
    # Force a refresh to get a valid access token
    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session = requests.Session()
        session.verify = False
        creds.refresh(Request(session=session))
        logger.info("[GOOGLE] Credentials refreshed from stored refresh token.")
        return creds
    except Exception as e:
        logger.error(f"[GOOGLE] Failed to refresh credentials from refresh token: {e}")
        return None


def _get_credentials(settings: Settings) -> Optional[Credentials]:
    """Return valid Google credentials, preferring refresh token over token.json."""
    # Dashboard users supply a refresh token directly — prefer that
    if settings.google_refresh_token:
        return _credentials_from_refresh_token(settings)
    # Standalone CLI users use token.json
    return _load_or_refresh_credentials(settings)


def get_google_service(settings: Settings):
    """Build and return an authenticated Google Calendar service."""
    creds = _get_credentials(settings)
    if not creds:
        return None
    return build("calendar", "v3", credentials=creds, http=_HTTP)


def get_gmail_service(settings: Settings):
    """Build and return an authenticated Gmail service."""
    creds = _get_credentials(settings)
    if not creds:
        return None
    return build("gmail", "v1", credentials=creds, http=_HTTP)


# ---------------------------------------------------------------------------
# Event building helpers
# ---------------------------------------------------------------------------

def _build_datetime(date_iso: str, time_hhmm: str = "08:00") -> str:
    return f"{date_iso}T{time_hhmm.zfill(5)}:00"


def _add_minutes(date_iso: str, time_hhmm: str, minutes: int) -> str:
    parts = time_hhmm.split(":")
    h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
    dt = datetime.strptime(f"{date_iso}T{h:02d}:{m:02d}:00", "%Y-%m-%dT%H:%M:%S")
    dt += timedelta(minutes=minutes)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _parse_date_from_text(text: Optional[str]) -> Optional[str]:
    m = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", str(text or ""))
    if not m:
        return None
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"


def _parse_times_from_text(text: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    s = str(text or "")
    m = re.search(r"\b(\d{1,2}:\d{2})\s*[\-\u2013]\s*(\d{1,2}:\d{2})\b", s)
    if m:
        return m.group(1).zfill(5), m.group(2).zfill(5)
    m = re.search(r"\b(\d{1,2}:\d{2})\b", s)
    if m:
        return m.group(1).zfill(5), None
    return None, None


def _is_odm_event(evt: Appointment, meta: EventMeta) -> bool:
    return (
        evt.source.value == "constatimmo"
        or meta.type.value == "odm"
        or bool(re.search(r"\bodm\b", evt.text or "", re.IGNORECASE))
    )


def _build_summary(evt: Appointment, meta: EventMeta) -> str:
    if _is_odm_event(evt, meta):
        # ODM events: "ODM E" for entree, "ODM S" for sortie, "ODM" for others
        if meta.type.value == "entree":
            return "ODM E"
        elif meta.type.value == "sortie":
            return "ODM S"
        return "ODM"
    # OS events
    suffix = "E" if meta.type.value == "entree" else "S"
    return f"OS {suffix}"


def _get_dedup_family(evt: Appointment, meta: EventMeta) -> str:
    return "odm" if _is_odm_event(evt, meta) else "os"


def _get_event_ref_number(evt: Appointment, meta: EventMeta) -> str:
    family = _get_dedup_family(evt, meta)
    if family == "odm":
        by_field = compact_text(evt.odm_number or "")
        by_text = extract_odm_number(f"{evt.text} {evt.description or ''}")
        by_url = extract_odm_number(evt.detail_url or "")
        return by_field or by_text or by_url
    by_field = compact_text(evt.os_number or "")
    by_text = extract_os_number(f"{evt.text} {evt.description or ''}")
    return by_field or by_text


def _build_description(evt: Appointment, family: str) -> str:
    """Build event description with all enriched details."""
    lines = []

    # Raw text / description
    base = compact_text(evt.description or evt.text or "")
    if base:
        lines.append(base)

    # Contact details
    if evt.owner:
        lines.append(f"Proprietaire: {compact_text(evt.owner)}")
    if evt.manager:
        lines.append(f"Gestionnaire: {compact_text(evt.manager)}")
    if evt.tenant:
        lines.append(f"Locataire: {compact_text(evt.tenant)}")
    phone = compact_text(evt.tenant_mobile or evt.tenant_phone or "")
    if phone:
        label = "Portable" if evt.tenant_mobile else "Telephone"
        lines.append(f"{label} locataire: {phone}")
    if evt.comment:
        lines.append(f"Commentaire: {compact_text(evt.comment)}")

    # Access info
    if evt.key_pickup_place:
        lines.append(f"Recuperation cles: {compact_text(evt.key_pickup_place)}")
    if evt.key_drop_place:
        lines.append(f"Depot cles: {compact_text(evt.key_drop_place)}")
    if evt.floor:
        lines.append(f"Etage: {compact_text(evt.floor)}")
    if evt.door:
        lines.append(f"Porte: {compact_text(evt.door)}")
    if evt.digicode:
        lines.append(f"Digicode: {compact_text(evt.digicode)}")
    if evt.building:
        lines.append(f"Batiment: {compact_text(evt.building)}")
    if evt.detail_url:
        lines.append(f"Fiche: {evt.detail_url}")

    return " | ".join(lines) if lines else ""


# ---------------------------------------------------------------------------
# Main sync function
# ---------------------------------------------------------------------------

async def sync_to_google_calendar(
    events: list[Appointment], settings: Settings
) -> tuple[int, int, int, list[Appointment]]:
    """Sync appointments to Google Calendar.

    Returns:
        (created_count, updated_count, skipped_count, newly_created_appointments)

    ``newly_created_appointments`` contains copies of every Appointment that was
    actually inserted into Google Calendar during this run (not updated, not
    skipped).  Each copy has ``meta`` populated so draft creation can identify
    the event type and choose the right recipient.
    """
    dry_run = settings.dry_run

    service = get_google_service(settings)
    if not service:
        logger.error("[GOOGLE] Could not build Google Calendar service — aborting sync.")
        return 0, 0, 0, []

    # Fetch existing events (14 days back, 120 days forward)
    now = datetime.utcnow()
    time_min = (now - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    time_max = (now + timedelta(days=120)).strftime("%Y-%m-%dT%H:%M:%SZ")

    existing_events: list[dict] = []
    cal_ids = {settings.os_calendar_id, settings.odm_calendar_id}
    for cal_id in cal_ids:
        page_token = None
        while True:
            try:
                result = service.events().list(
                    calendarId=cal_id,
                    timeMin=time_min,
                    timeMax=time_max,
                    singleEvents=True,
                    orderBy="startTime",
                    maxResults=500,
                    pageToken=page_token,
                ).execute()
                for ev in result.get("items", []):
                    ev["_calendarId"] = cal_id
                    existing_events.append(ev)
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
            except Exception as e:
                logger.warning(f"[GOOGLE] Could not list events from {cal_id}: {e}")
                break

    logger.info(f"[GOOGLE] Fetched {len(existing_events)} existing events from {len(cal_ids)} calendar(s).")

    created_count = 0
    updated_count = 0
    skipped_count = 0
    newly_created: list[Appointment] = []

    for evt in events:
        meta = evt.meta or EventMeta()
        if meta.type.value == "indisponibilite" or meta.is_trajet:
            skipped_count += 1
            continue

        # Resolve date and times
        parsed_date = (
            evt.date
            or _parse_date_from_text(evt.text)
            or _parse_date_from_text(evt.description)
        )
        t = _parse_times_from_text(
            f"{evt.start_time or ''} {evt.end_time or ''} {evt.time_raw or ''} {evt.text or ''}"
        )
        start_time = evt.start_time or t[0] or "08:00"
        end_time = evt.end_time or t[1]

        if not parsed_date:
            logger.warning(f"[SYNC] No date found, skipping: {evt.text!r}")
            skipped_count += 1
            continue

        start_iso = _build_datetime(parsed_date, start_time)
        end_iso = (
            _build_datetime(parsed_date, end_time)
            if end_time
            else _add_minutes(parsed_date, start_time, 45)
        )

        family = _get_dedup_family(evt, meta)
        summary = _build_summary(evt, meta)
        ref_number = _get_event_ref_number(evt, meta)
        target_cal_id = settings.odm_calendar_id if family == "odm" else settings.os_calendar_id
        start_key = start_iso[:16]
        color_id = settings.google_color_constatimmo if family == "odm" else settings.google_color_snexi
        description = _build_description(evt, family)

        g_event: dict = {
            "summary": summary,
            "location": evt.address or "",
            "description": description,
            "start": {"dateTime": start_iso, "timeZone": "Europe/Paris"},
            "end": {"dateTime": end_iso, "timeZone": "Europe/Paris"},
            "colorId": str(color_id),
        }

        # --- Dedup by ref number + start time ---
        dup_by_ref: Optional[dict] = None
        if ref_number:
            for ev in existing_events:
                if ev.get("_calendarId") != target_cal_id:
                    continue
                ev_start = (ev.get("start", {}).get("dateTime", ""))[:16]
                if ev_start != start_key:
                    continue
                ev_src = f"{ev.get('summary','')} {ev.get('description','')} {ev.get('location','')}"
                ev_ref = extract_odm_number(ev_src) if family == "odm" else extract_os_number(ev_src)
                if ev_ref == ref_number:
                    dup_by_ref = ev
                    break

        if dup_by_ref:
            skipped_count += 1
            logger.debug(f"[SYNC][DUP] {family.upper()} {ref_number} at {start_iso} — skipped (ref match).")
            continue

        # --- Dedup by summary + start time ---
        dup_event: Optional[dict] = None
        for ev in existing_events:
            if ev.get("_calendarId") != target_cal_id:
                continue
            if (ev.get("summary", "") or "").lower() == summary.lower():
                ev_start = (ev.get("start", {}).get("dateTime", ""))[:16]
                if ev_start == start_key:
                    dup_event = ev
                    break

        if dup_event:
            needs_update = (
                (dup_event.get("summary", "") or "").strip() != summary
                or (dup_event.get("location", "") or "").strip() != (g_event["location"])
                or (dup_event.get("description", "") or "").strip() != description
            )
            if not needs_update:
                skipped_count += 1
                logger.debug(f"[SYNC][DUP] {summary} at {start_iso} — unchanged, skipped.")
                continue
            if dry_run:
                updated_count += 1
                logger.info(f"[DRY_RUN] Would update: {summary} {start_iso}")
                continue
            try:
                service.events().patch(
                    calendarId=target_cal_id,
                    eventId=dup_event["id"],
                    body={
                        "summary": summary,
                        "location": g_event["location"],
                        "description": description,
                        "colorId": str(color_id),
                    },
                ).execute()
                updated_count += 1
                logger.info(f"[GOOGLE] Updated: {summary} at {start_iso} (ID={dup_event['id']})")
            except Exception as e:
                logger.error(f"[GOOGLE] Error updating event: {e}")
            continue

        # --- Create new event ---
        if dry_run:
            created_count += 1
            newly_created.append(evt.model_copy(update={"meta": meta}))
            logger.info(f"[DRY_RUN] Would create: {summary} {start_iso} @ {target_cal_id}")
            continue

        try:
            result = service.events().insert(calendarId=target_cal_id, body=g_event).execute()
            created_count += 1
            newly_created.append(evt.model_copy(update={"meta": meta}))
            logger.info(
                f"[GOOGLE] Created: {summary} on {parsed_date} {start_time}"
                f"{f'-{end_time}' if end_time else ''}"
                f"{f' @ {evt.address}' if evt.address else ''}"
                f" (ID={result['id']})"
            )
            # Add to local cache to avoid intra-run duplicates
            existing_events.append({
                "id": result["id"],
                "summary": summary,
                "description": description,
                "location": g_event["location"],
                "start": {"dateTime": start_iso},
                "_calendarId": target_cal_id,
            })
        except Exception as e:
            logger.error(f"[GOOGLE] Error creating event {summary} {start_iso}: {e}")

    logger.info(
        f"[SYNC] Done — Created: {created_count} | Updated: {updated_count} | Skipped: {skipped_count}"
    )
    return created_count, updated_count, skipped_count, newly_created
