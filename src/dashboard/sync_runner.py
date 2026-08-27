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
        snexi_enrich_details=creds.snexi_enrich_details,
        constatimmo_enrich_details=creds.constatimmo_enrich_details,
        dry_run=creds.dry_run,
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
    if business:
        try:
            # sync_to_google_calendar returns counts; we patch it to also return per-event results
            created, updated, skipped = await _sync_with_counts(business, settings)
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

    return stats


async def _sync_with_counts(
    events: list[Appointment],
    settings: Settings,
) -> tuple[int, int, int]:
    """Wrapper around sync_to_google_calendar that returns (created, updated, skipped)."""
    # We monkey-patch the logger to capture counts from the sync module.
    # A cleaner approach is to refactor sync_to_google_calendar to return counts,
    # but we keep the existing code untouched for now.
    from calendar_sync.sync.google_calendar import sync_to_google_calendar

    created = updated = skipped = 0
    original_info = logger.info

    def _capture(msg, *args, **kwargs):
        nonlocal created, updated, skipped
        msg_str = str(msg)
        if "[SYNC] Created:" in msg_str:
            parts = msg_str.split("|")
            for p in parts:
                p = p.strip()
                if p.startswith("Created:"):
                    try:
                        created = int(p.split(":")[1].strip())
                    except ValueError:
                        pass
                elif p.startswith("Skipped:"):
                    try:
                        skipped = int(p.split(":")[1].strip())
                    except ValueError:
                        pass
        if "[GOOGLE] Updated:" in msg_str:
            updated += 1
        original_info(msg, *args, **kwargs)

    logger.info = _capture
    try:
        await sync_to_google_calendar(events, settings)
    finally:
        logger.info = original_info

    return created, updated, skipped


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
