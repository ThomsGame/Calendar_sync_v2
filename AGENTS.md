# AGENTS.md - Calendar Sync Python Project

## Project Overview
This is a Python rewrite of a Node.js calendar synchronization tool. It scrapes appointment data from two French real estate platforms (Snexi and Constatimmo) using browser automation, then syncs the events to Google Calendar.

## Architecture
- **Language**: Python 3.11+ with Playwright (async) for browser automation
- **Data models**: Pydantic v2 for validation and serialization
- **Config**: Pydantic Settings loading from .env
- **Google Calendar**: google-api-python-client with service account auth

## Key Directories
```
src/calendar_sync/
  main.py              - Entry point: orchestrate scrape -> filter -> sync
  config.py            - Settings class (loads .env)
  models/appointment.py - Pydantic Appointment model with all fields
  scrapers/
    base.py            - Browser manager, shared page utilities
    snexi.py           - Snexi login, calendar extraction, detail enrichment
    constatimmo.py     - Constatimmo login, roadmap extraction, detail enrichment
  filters/business.py  - classify_event(), build_business_appointments()
  sync/google_calendar.py - Auth + event create/update/dedup
  utils/helpers.py     - Date parsing, French months, text processing
tests/                 - Unit tests using fixture data from old Node.js project
```

## Legacy Reference
The original Node.js codebase is at: /home/thomaslt/Documents/Work/Bot_calendar_sync/
- Main file: snexiSync.js (2234 lines) - the complete monolith to port from
- Fixture data: appointments.filtered.json, constatimmo.appointments.json
- The Python project REPLACES the Node.js project but keeps the same behavior

## Data Flow
1. Login to Snexi portal -> extract 4 weeks of calendar events from FullCalendar iframe
2. Login to Constatimmo -> extract from roadmap table + calendar elements
3. Optionally enrich both with detail pages (address, tenant, owner, digicode, etc.)
4. Filter: keep only entree/sortie/odm, skip indisponibilite/trajet
5. Sync to Google Calendar: dedup by ref number + start time, create/update events

## Important Selectors / Login Flows
### Snexi Login Flow
1. Navigate to SNEXI_URL
2. Close Didomi cookie popup (#didomi-notice-agree-button or text-based)
3. Click "Espace client" button (aria-label or text match)
4. Fill login form (#login + #password or fallback selectors)
5. Click Connexion/Connecter button
6. Click menu Indisponibilites (a.lien_menu[href*='experts_indisponibilites.php'])
7. Find calendar iframe containing 'indisponibilites' in URL
8. Extract from FullCalendar (.fc-event nodes) across 4 weeks

### Constatimmo Login Flow
1. Launch separate browser with userDataDir for persistent session
2. Navigate to CONSTATIMMO_URL
3. Fill email + password (multiple selector candidates)
4. Click submit
5. Navigate to /profile#planification
6. Check for SSO redirect, re-login if needed
7. Click "Mon activite" -> "Mes disponibilites"
8. Check #comingOrdersCheckbox
9. Extract from #road-map-results table rows

## Color Code Mapping (Snexi)
- rgb(207, 36, 36) = Red = Indisponibilite (SKIP)
- rgb(18, 17, 171) = Blue = EDL Entree (KEEP)
- rgb(17, 138, 123) = Green = EDL Sortie (KEEP)
- Purple/violet = ODM (KEEP)
- Contains "trajet" = Travel (SKIP)

## Environment Variables
See .env.example for all required variables.

## Testing
Tests use fixture data from the old Node.js project (copied to tests/fixtures/).
Run: pytest tests/ -v

## Build & Run
```bash
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env  # fill in credentials
calendar-sync         # or: python -m calendar_sync.main
```

## Current Status
- Project structure created
- All core modules ported from snexiSync.js
- Tests written using fixture data
- No credentials available yet - cannot test live connections
- Next steps: detail enrichment improvements, email pre-fill, dashboard
