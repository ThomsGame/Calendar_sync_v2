"""Main entry point: orchestrate scrape -> filter -> sync."""

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
from calendar_sync.scrapers.constatimmo import login_constatimmo
from calendar_sync.scrapers.snexi import enrich_snexi_appointments, login_snexi
from calendar_sync.scrapers.base import BrowserManager
from calendar_sync.sync.google_calendar import sync_to_google_calendar


CACHE_FILE = Path("appointments.cache.json")
FILTERED_FILE = Path("appointments.filtered.json")
CACHE_TTL_MS = 2 * 60 * 60 * 1000  # 2 hours


async def run_full_sync() -> None:
    """Full extraction + sync pipeline."""
    settings = load_settings()

    logger.info("--- Starting calendar sync ---")

    # Snexi extraction
    snexi_events: list[Appointment] = []
    manager = BrowserManager(headless=False)
    try:
        browser = await manager.launch()
        page = await manager.new_page()
        try:
            raw = await login_snexi(page, settings)
            tagged = [e.model_copy(update={"source": AppointmentSource.SNEXI}) for e in raw]
            snexi_events = await enrich_snexi_appointments(page, tagged, settings)
        except Exception as e:
            logger.error(f"[SNEXI] Extraction failed, continuing with Constatimmo only: {e}")
        finally:
            await manager.close()
    except Exception as e:
        logger.error(f"[SNEXI] Browser launch failed: {e}")

    # Constatimmo extraction
    constatimmo_events = await login_constatimmo(settings)

    # Combine
    all_events = snexi_events + constatimmo_events
    logger.info(
        f"Extracted {len(all_events)} events "
        f"(Snexi: {len(snexi_events)}, Constatimmo: {len(constatimmo_events)})"
    )

    # Filter
    business, stats = build_business_appointments(all_events)

    # Save outputs
    FILTERED_FILE.write_text(json.dumps([e.to_cal_dict() for e in business], indent=2, ensure_ascii=False))
    CACHE_FILE.write_text(json.dumps({
        "timestamp": int(datetime.now().timestamp() * 1000),
        "events": [e.to_cal_dict() for e in business],
    }, indent=2, ensure_ascii=False))
    Path("appointments.stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False))

    logger.info(
        f"[FILTER] Kept: {stats['kept']} | Sorties: {stats['sortieCount']} | "
        f"Entrees: {stats['entreeCount']} | ODM: {stats['odmCount']} | "
        f"Skipped red: {stats['skippedRed']} | Skipped trajet: {stats['skippedTrajet']} | "
        f"Sources: {stats['sourceCounts']}"
    )

    # Sync to Google Calendar
    await sync_to_google_calendar(business, settings)
    logger.info("--- Synchronization complete ---")


async def run_sync_only() -> None:
    """Re-sync from cache without re-scraping."""
    settings = load_settings()
    cached_events = None
    age_minutes = None

    if CACHE_FILE.exists():
        cache = json.loads(CACHE_FILE.read_text())
        if isinstance(cache.get("events"), list) and isinstance(cache.get("timestamp"), int):
            age = int(datetime.now().timestamp() * 1000) - cache["timestamp"]
            if age <= CACHE_TTL_MS:
                cached_events = [Appointment(**e) for e in cache["events"]]
                age_minutes = round(age / 60000)

    if not cached_events and FILTERED_FILE.exists():
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

    if not cached_events:
        logger.error("[SYNC-ONLY] No valid event cache (<2h). Run 'calendar-sync' to re-extract.")
        sys.exit(1)

    logger.info(f"[SYNC-ONLY] Valid data ({age_minutes} min) - {len(cached_events)} events.")
    await sync_to_google_calendar(cached_events, settings)
    logger.info("--- Synchronization complete (sync-only) ---")


def main() -> None:
    """CLI entry point."""
    if "--sync-only" in sys.argv:
        asyncio.run(run_sync_only())
    else:
        asyncio.run(run_full_sync())


if __name__ == "__main__":
    main()
