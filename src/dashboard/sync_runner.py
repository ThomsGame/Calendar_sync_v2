"""Bridge between the dashboard and the calendar_sync engine.

Loads per-user credentials from the database, constructs a Settings object,
and runs the full scrape → filter → sync pipeline.
All results are recorded in the SyncRun / SyncEvent tables.
"""

from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime
from typing import Optional

from loguru import logger

from calendar_sync.config import Settings
from calendar_sync.filters.business import build_business_appointments
from calendar_sync.models.appointment import Appointment, AppointmentSource
from dashboard import get_db
from dashboard.crypto import decrypt
from dashboard.models import SyncEvent, SyncRun, User, UserCredentials


def _build_settings(creds: UserCredentials) -> Settings:
    """Construct a Settings object from decrypted database credentials."""
    from dashboard import get_db
    from flask import current_app

    return Settings(
        snexi_url="https://snexi.fr/portail",
        snexi_username=creds.snexi_username or "",
        snexi_password=decrypt(creds.snexi_password_enc or ""),
        constatimmo_url="https://constatonline.constatimmo.com",
        constatimmo_username=creds.constatimmo_username or "",
        constatimmo_password=decrypt(creds.constatimmo_password_enc or ""),
        constatimmo_headless=creds.constatimmo_headless,
        google_calendar_id=creds.google_calendar_os_id or "primary",
        google_calendar_os_id=creds.google_calendar_os_id or "",
        google_calendar_odm_id=creds.google_calendar_odm_id or "",
        # Pass the stored refresh token so google_calendar.py bypasses token.json
        google_oauth_client_id=current_app.config.get("GOOGLE_CLIENT_ID", ""),
        google_oauth_client_secret=current_app.config.get("GOOGLE_CLIENT_SECRET", ""),
        google_refresh_token=decrypt(creds.google_refresh_token_enc or ""),
        snexi_enrich_details=creds.snexi_enrich_details,
        constatimmo_enrich_details=creds.constatimmo_enrich_details,
        dry_run=creds.dry_run,
        # Gmail draft settings
        gmail_drafts_enabled=creds.gmail_drafts_enabled,
        constatimmo_contact_email=creds.constatimmo_contact_email or "",
        snexi_contact_email=creds.snexi_contact_email or "",
        sender_name=creds.sender_name or "",
    )


def _record_events(
    run_id: int,
    user_id: int,
    events: list[Appointment],
    action: str,
) -> list[SyncEvent]:
    """Convert Appointment list to SyncEvent DB records."""
    records = []
    for evt in events:
        meta = evt.meta
        records.append(
            SyncEvent(
                run_id=run_id,
                user_id=user_id,
                source=evt.source.value if evt.source else "unknown",
                event_type=meta.type.value if meta else None,
                action=action,
                summary=evt.text[:255] if evt.text else None,
                date=evt.date,
                start_time=evt.start_time,
                end_time=evt.end_time,
                address=(evt.address or "")[:500] or None,
                os_number=evt.os_number,
                odm_number=evt.odm_number,
            )
        )
    return records


async def _run_sync_async(
    run_id: int,
    user_id: int,
    settings: Settings,
    creds: UserCredentials,
) -> dict:
    """Core async pipeline: scrape → filter → sync → record."""
    from calendar_sync.scrapers.constatimmo import login_constatimmo
    from calendar_sync.scrapers.snexi import enrich_snexi_appointments, login_snexi
    from calendar_sync.scrapers.base import BrowserManager
    from calendar_sync.sync.google_calendar import sync_to_google_calendar

    stats: dict = {
        "snexi_raw": 0,
        "constatimmo_raw": 0,
        "events_kept": 0,
        "events_created": 0,
        "events_updated": 0,
        "events_skipped": 0,
        "email_drafts_created": 0,
        "error": None,
        "sync_events": [],
    }

    # --- Snexi ---
    snexi_events: list[Appointment] = []
    if settings.snexi_username and settings.snexi_password:
        manager = BrowserManager(headless=True)
        try:
            await manager.launch()
            page = await manager.new_page()
            try:
                raw = await login_snexi(page, settings)
                tagged = [e.model_copy(update={"source": AppointmentSource.SNEXI}) for e in raw]
                snexi_events = await enrich_snexi_appointments(page, tagged, settings)
                stats["snexi_raw"] = len(snexi_events)
                logger.info(f"[RUN {run_id}][SNEXI] Extracted {len(snexi_events)} events.")
            except Exception as e:
                logger.error(f"[RUN {run_id}][SNEXI] Failed: {e}")
                stats["error"] = f"Snexi: {e}"
            finally:
                await manager.close()
        except Exception as e:
            logger.error(f"[RUN {run_id}][SNEXI] Browser launch failed: {e}")
            stats["error"] = f"Snexi browser: {e}"

    # --- Constatimmo ---
    constatimmo_events: list[Appointment] = []
    if settings.constatimmo_username and settings.constatimmo_password:
        try:
            constatimmo_events = await login_constatimmo(settings)
            stats["constatimmo_raw"] = len(constatimmo_events)
            logger.info(f"[RUN {run_id}][CONSTATIMMO] Extracted {len(constatimmo_events)} events.")
        except Exception as e:
            logger.error(f"[RUN {run_id}][CONSTATIMMO] Failed: {e}")
            if stats["error"]:
                stats["error"] += f" | Constatimmo: {e}"
            else:
                stats["error"] = f"Constatimmo: {e}"

    # --- Filter ---
    all_events = snexi_events + constatimmo_events
    business, filter_stats = build_business_appointments(all_events)
    stats["events_kept"] = filter_stats["kept"]
    logger.info(f"[RUN {run_id}] Kept {filter_stats['kept']}/{len(all_events)} events after filtering.")

    # --- Sync to Google Calendar ---
    newly_created_events: list[Appointment] = []
    if business:
        try:
            created, updated, skipped, newly_created_events = await _sync_with_counts(business, settings)
            stats["events_created"] = created
            stats["events_updated"] = updated
            stats["events_skipped"] = skipped
            stats["sync_events"] = business
        except Exception as e:
            logger.error(f"[RUN {run_id}][GOOGLE] Sync failed: {e}")
            if stats["error"]:
                stats["error"] += f" | Google: {e}"
            else:
                stats["error"] = f"Google: {e}"

    # --- Gmail Drafts ---
    if settings.gmail_drafts_enabled and newly_created_events:
        try:
            from calendar_sync.email import DraftRecipients, create_gmail_drafts
            from calendar_sync.sync.google_calendar import get_gmail_service

            gmail_service = get_gmail_service(settings)
            if gmail_service:
                recipients = DraftRecipients(
                    snexi_contact=settings.snexi_contact_email or None,
                    constatimmo_contact=settings.constatimmo_contact_email or None,
                )
                draft_results = create_gmail_drafts(
                    service=gmail_service,
                    appointments=newly_created_events,
                    recipients=recipients,
                    sender_name=settings.sender_name,
                    dry_run=settings.dry_run,
                )
                stats["draft_results"] = draft_results
                stats["email_drafts_created"] = sum(1 for r in draft_results if r.ok)
                logger.info(
                    f"[RUN {run_id}][EMAIL] {stats['email_drafts_created']}"
                    f"/{len(draft_results)} drafts created."
                )
            else:
                logger.warning(f"[RUN {run_id}][EMAIL] Gmail service unavailable — skipping drafts.")
        except Exception as e:
            logger.error(f"[RUN {run_id}][EMAIL] Draft creation failed: {e}")
            if stats["error"]:
                stats["error"] += f" | Email: {e}"
            else:
                stats["error"] = f"Email: {e}"

    return stats


async def _sync_with_counts(
    events: list[Appointment],
    settings: Settings,
) -> tuple[int, int, int, list[Appointment]]:
    """Wrapper around sync_to_google_calendar that returns counts + new appointments."""
    from calendar_sync.sync.google_calendar import sync_to_google_calendar

    created, updated, skipped, newly_created = await sync_to_google_calendar(events, settings)
    return created, updated, skipped, newly_created


def run_sync_for_user(user_id: int, trigger: str = "manual") -> Optional[int]:
    """
    Entry point called from Flask routes or the scheduler.

    Creates a SyncRun record, runs the async pipeline, updates the record.
    Returns the run_id, or None if the user has no credentials.
    """
    db = get_db()

    user: Optional[User] = db.get(User, user_id)
    if not user:
        logger.error(f"[SYNC] User {user_id} not found.")
        return None

    creds: Optional[UserCredentials] = user.credentials
    if not creds:
        logger.warning(f"[SYNC] User {user_id} has no credentials configured.")
        return None

    settings = _build_settings(creds)

    # Create run record
    run = SyncRun(user_id=user_id, trigger=trigger, status="running", started_at=datetime.now())
    db.add(run)
    db.commit()
    run_id = run.id

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            stats = loop.run_until_complete(
                _run_sync_async(run_id, user_id, settings, creds)
            )
        finally:
            loop.close()

        # Persist sync events
        sync_events = _record_events(
            run_id=run_id,
            user_id=user_id,
            events=stats.get("sync_events", []),
            action="created",
        )
        for se in sync_events:
            db.add(se)

        # Persist email draft records
        from dashboard.models import EmailDraft
        for dr in stats.get("draft_results", []):
            db.add(EmailDraft(
                user_id=user_id,
                run_id=run_id,
                gmail_draft_id=dr.gmail_draft_id if dr.ok else None,
                recipient=dr.recipient,
                subject=dr.subject,
                body_preview=dr.body_preview[:500],
                status="draft" if dr.ok else "error",
            ))

        # Update run
        run.finished_at = datetime.now()
        run.status = "error" if (stats["error"] and stats["events_kept"] == 0) else (
            "partial" if stats["error"] else "success"
        )
        run.snexi_raw = stats["snexi_raw"]
        run.constatimmo_raw = stats["constatimmo_raw"]
        run.events_kept = stats["events_kept"]
        run.events_created = stats["events_created"]
        run.events_updated = stats["events_updated"]
        run.events_skipped = stats["events_skipped"]
        run.email_drafts_created = stats["email_drafts_created"]
        if stats["error"]:
            run.error_message = str(stats["error"])[:2000]

        db.commit()
        logger.info(f"[SYNC] Run {run_id} completed: {run.status}")

    except Exception as e:
        logger.error(f"[SYNC] Run {run_id} crashed: {e}\n{traceback.format_exc()}")
        try:
            run.finished_at = datetime.now()
            run.status = "error"
            run.error_message = f"{e}\n{traceback.format_exc()}"[:2000]
            db.commit()
        except Exception:
            pass

    return run_id


async def test_snexi_connection(username: str, password: str) -> dict:
    """Attempt a Snexi login and return result dict {ok, message, event_count}."""
    from calendar_sync.scrapers.base import BrowserManager
    from calendar_sync.scrapers.snexi import login_snexi

    settings = Settings(
        snexi_username=username,
        snexi_password=password,
        snexi_enrich_details=False,
        dry_run=True,
    )

    manager = BrowserManager(headless=True)
    try:
        await manager.launch()
        page = await manager.new_page()
        events = await login_snexi(page, settings)
        await manager.close()
        return {"ok": True, "message": f"Connexion réussie — {len(events)} événements trouvés.", "event_count": len(events)}
    except Exception as e:
        try:
            await manager.close()
        except Exception:
            pass
        return {"ok": False, "message": f"Échec : {e}", "event_count": 0}


async def test_constatimmo_connection(username: str, password: str) -> dict:
    """Attempt a Constatimmo login and return result dict."""
    from calendar_sync.scrapers.constatimmo import login_constatimmo

    settings = Settings(
        constatimmo_username=username,
        constatimmo_password=password,
        constatimmo_enrich_details=False,
        constatimmo_headless=True,
        dry_run=True,
    )
    try:
        events = await login_constatimmo(settings)
        return {"ok": True, "message": f"Connexion réussie — {len(events)} événements trouvés.", "event_count": len(events)}
    except Exception as e:
        return {"ok": False, "message": f"Échec : {e}", "event_count": 0}
