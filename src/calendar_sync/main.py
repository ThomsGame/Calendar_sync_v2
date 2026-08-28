"""Main entry point: orchestrate scrape -> filter -> sync.

Run as a standalone script:
    calendar-sync              # Full extraction + sync
    calendar-sync --sync-only  # Re-sync from cache (max 2h old)
    calendar-sync --dry-run    # Preview only, nothing written to Google Calendar
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

from calendar_sync.config import load_settings
from calendar_sync.filters.business import build_business_appointments
from calendar_sync.models.appointment import Appointment, AppointmentSource
from calendar_sync.scrapers.base import BrowserManager
from calendar_sync.scrapers.constatimmo import login_constatimmo
from calendar_sync.scrapers.snexi import enrich_snexi_appointments, login_snexi
from calendar_sync.email import DraftRecipients, create_gmail_drafts
from calendar_sync.sync.google_calendar import get_gmail_service, sync_to_google_calendar


CACHE_FILE = Path("appointments.cache.json")
FILTERED_FILE = Path("appointments.filtered.json")
CACHE_TTL_MS = 2 * 60 * 60 * 1000  # 2 hours

# Configure loguru for readable CLI output
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}",
    colorize=True,
    level="DEBUG",
)


async def run_full_sync(dry_run_override: bool | None = None) -> None:
    """Full extraction + sync pipeline."""
    settings = load_settings()
    if dry_run_override is not None:
        settings = settings.model_copy(update={"dry_run": dry_run_override})

    logger.info("=" * 60)
    logger.info("Calendar Sync — starting full extraction")
    logger.info(f"  Snexi user    : {settings.snexi_username or '(not configured)'}")
    logger.info(f"  Constatimmo   : {settings.constatimmo_username or '(not configured)'}")
    logger.info(f"  Calendar OS   : {settings.os_calendar_id}")
    logger.info(f"  Calendar ODM  : {settings.odm_calendar_id}")
    logger.info(f"  Dry run       : {settings.dry_run}")
    logger.info("=" * 60)

    # -----------------------------------------------------------------------
    # Snexi extraction
    # -----------------------------------------------------------------------
    snexi_events: list[Appointment] = []
    if settings.snexi_username and settings.snexi_password:
        logger.info("[SNEXI] Starting extraction...")
        manager = BrowserManager(headless=settings.snexi_headless)
        try:
            await manager.launch()
            page = await manager.new_page()
            try:
                raw = await login_snexi(page, settings)
                tagged = [e.model_copy(update={"source": AppointmentSource.SNEXI}) for e in raw]
                logger.info(f"[SNEXI] {len(tagged)} raw events fetched. Enriching details...")
                snexi_events = await enrich_snexi_appointments(page, tagged, settings)
                logger.info(f"[SNEXI] {len(snexi_events)} events after enrichment.")
            except Exception as e:
                logger.error(f"[SNEXI] Extraction failed: {e}")
            finally:
                await manager.close()
        except Exception as e:
            logger.error(f"[SNEXI] Browser launch failed: {e}")
    else:
        logger.warning("[SNEXI] No credentials configured — skipping.")

    # -----------------------------------------------------------------------
    # Constatimmo extraction
    # -----------------------------------------------------------------------
    constatimmo_events: list[Appointment] = []
    if settings.constatimmo_username and settings.constatimmo_password:
        logger.info("[CONSTATIMMO] Starting extraction...")
        try:
            constatimmo_events = await login_constatimmo(settings)
            logger.info(f"[CONSTATIMMO] {len(constatimmo_events)} events fetched.")
        except Exception as e:
            logger.error(f"[CONSTATIMMO] Extraction failed: {e}")
    else:
        logger.warning("[CONSTATIMMO] No credentials configured — skipping.")

    # -----------------------------------------------------------------------
    # Combine & filter
    # -----------------------------------------------------------------------
    all_events = snexi_events + constatimmo_events
    logger.info(f"[FILTER] Total raw events: {len(all_events)} (Snexi: {len(snexi_events)}, Constatimmo: {len(constatimmo_events)})")

    business, stats = build_business_appointments(all_events)
    logger.info(
        f"[FILTER] Kept: {stats['kept']} | "
        f"Entrees: {stats['entreeCount']} | "
        f"Sorties: {stats['sortieCount']} | "
        f"ODM: {stats['odmCount']} | "
        f"Skipped red (indispo): {stats['skippedRed']} | "
        f"Skipped trajet: {stats['skippedTrajet']}"
    )

    # -----------------------------------------------------------------------
    # Save outputs
    # -----------------------------------------------------------------------
    FILTERED_FILE.write_text(
        json.dumps([e.to_cal_dict() for e in business], indent=2, ensure_ascii=False)
    )
    CACHE_FILE.write_text(json.dumps({
        "timestamp": int(datetime.now().timestamp() * 1000),
        "events": [e.to_cal_dict() for e in business],
    }, indent=2, ensure_ascii=False))
    Path("appointments.stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False)
    )
    logger.info(f"[OUTPUT] Saved {len(business)} events to {FILTERED_FILE}")

    if not business:
        logger.warning("[SYNC] No events to sync — done.")
        return

    # -----------------------------------------------------------------------
    # Sync to Google Calendar
    # -----------------------------------------------------------------------
    logger.info("[GOOGLE] Starting calendar sync...")
    created, updated, skipped, newly_created = await sync_to_google_calendar(business, settings)
    logger.info("=" * 60)
    logger.info(f"[GOOGLE] Sync complete — Created: {created} | Updated: {updated} | Skipped: {skipped}")

    # -----------------------------------------------------------------------
    # Gmail drafts — one per newly created event, to the other platform
    # -----------------------------------------------------------------------
    if settings.gmail_drafts_enabled and newly_created and not settings.dry_run:
        logger.info(f"[EMAIL] Creating Gmail drafts for {len(newly_created)} new event(s)...")
        gmail = get_gmail_service(settings)
        if gmail:
            recipients = DraftRecipients(
                snexi_contact=settings.snexi_contact_email or None,
                constatimmo_contact=settings.constatimmo_contact_email or None,
            )
            results = create_gmail_drafts(
                service=gmail,
                appointments=newly_created,
                recipients=recipients,
                sender_name=settings.sender_name,
                dry_run=False,
            )
            ok = sum(1 for r in results if r.ok)
            failed = len(results) - ok
            logger.info(f"[EMAIL] {ok} draft(s) created" + (f" | {failed} failed" if failed else ""))
            for r in results:
                if r.ok:
                    logger.info(f"  → {r.recipient} | {r.subject[:60]}")
                else:
                    logger.warning(f"  ✗ {r.recipient} | {r.error}")
        else:
            logger.warning("[EMAIL] Gmail service unavailable — skipping drafts.")
    elif settings.gmail_drafts_enabled and newly_created and settings.dry_run:
        logger.info(f"[EMAIL] Dry run — would create {len(newly_created)} draft(s), skipping.")

    logger.info("=" * 60)
    logger.info("All done.")
    logger.info("=" * 60)


async def run_sync_only() -> None:
    """Re-sync from cache without re-scraping."""
    settings = load_settings()
    cached_events: list[Appointment] | None = None
    age_minutes: int | None = None

    if CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text())
            if isinstance(cache.get("events"), list) and isinstance(cache.get("timestamp"), int):
                age = int(datetime.now().timestamp() * 1000) - cache["timestamp"]
                if age <= CACHE_TTL_MS:
                    cached_events = [Appointment(**e) for e in cache["events"]]
                    age_minutes = round(age / 60000)
        except Exception as e:
            logger.warning(f"[SYNC-ONLY] Cache read error: {e}")

    if not cached_events and FILTERED_FILE.exists():
        try:
            stat = FILTERED_FILE.stat()
            age = int(datetime.now().timestamp() * 1000) - int(stat.st_mtime * 1000)
            if age <= CACHE_TTL_MS:
                raw = json.loads(FILTERED_FILE.read_text())
                cached_events = [Appointment(**e) for e in raw]
                age_minutes = round(age / 60000)
                CACHE_FILE.write_text(json.dumps({
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "events": [e.to_cal_dict() for e in cached_events],
                }, indent=2, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"[SYNC-ONLY] Filtered file read error: {e}")

    if not cached_events:
        logger.error("[SYNC-ONLY] No valid event cache (< 2h). Run 'calendar-sync' to re-extract.")
        sys.exit(1)

    logger.info(f"[SYNC-ONLY] Cache is {age_minutes} min old — {len(cached_events)} events.")
    created, updated, skipped, newly_created = await sync_to_google_calendar(cached_events, settings)
    logger.info(f"[SYNC-ONLY] Done — Created: {created} | Updated: {updated} | Skipped: {skipped}")

    if settings.gmail_drafts_enabled and newly_created and not settings.dry_run:
        gmail = get_gmail_service(settings)
        if gmail:
            recipients = DraftRecipients(
                snexi_contact=settings.snexi_contact_email or None,
                constatimmo_contact=settings.constatimmo_contact_email or None,
            )
            results = create_gmail_drafts(
                service=gmail,
                appointments=newly_created,
                recipients=recipients,
                sender_name=settings.sender_name,
                dry_run=False,
            )
            ok = sum(1 for r in results if r.ok)
            logger.info(f"[EMAIL] {ok}/{len(results)} draft(s) created.")


def main() -> None:
    """CLI entry point."""
    args = set(sys.argv[1:])

    if "--sync-only" in args:
        asyncio.run(run_sync_only())
    elif "--dry-run" in args:
        asyncio.run(run_full_sync(dry_run_override=True))
    else:
        asyncio.run(run_full_sync())


if __name__ == "__main__":
    main()
