# AGENTS.md — Calendar Sync Python Project

## Project Overview

Python tool that scrapes appointment data from two French real estate inspection
platforms (Snexi and Constatimmo) using browser automation, then syncs the events
to Google Calendar and optionally creates Gmail draft emails.

A Flask web dashboard provides a multi-user interface for configuration and monitoring.

---

## Architecture

```
src/
├── calendar_sync/          ← Core automation engine (standalone script)
│   ├── config.py           ← Settings via pydantic-settings (.env)
│   ├── main.py             ← CLI entry point: calendar-sync [--dry-run|--sync-only]
│   ├── models/appointment.py ← Pydantic Appointment + EventMeta models
│   ├── scrapers/
│   │   ├── base.py         ← BrowserManager (Playwright, headless Chromium)
│   │   ├── snexi.py        ← Full Snexi login → calendar → detail enrichment
│   │   └── constatimmo.py  ← Full Constatimmo login → roadmap → detail enrichment
│   ├── filters/business.py ← classify_event(), build_business_appointments()
│   ├── sync/google_calendar.py ← OAuth2 auth, Calendar sync, Gmail service builder
│   ├── utils/helpers.py    ← Date parsing, French months, text utils
│   └── email/              ← Gmail draft module (stub — pluggable)
└── dashboard/              ← Flask web dashboard
    ├── __init__.py         ← App factory (create_app), DB init, admin seed
    ├── models.py           ← SQLAlchemy: User, UserCredentials, SyncRun, SyncEvent, EmailDraft
    ├── crypto.py           ← Fernet encrypt/decrypt (DASHBOARD_SECRET_KEY env var)
    ├── auth.py             ← /login, /register, /logout
    ├── setup.py            ← /setup/* (3-step onboarding wizard + OAuth callback)
    ├── sync_runner.py      ← Bridge: DB credentials → Settings → runs sync engine
    ├── scheduler.py        ← APScheduler: daily sync at 07:00 Europe/Paris
    └── views/
        ├── dashboard.py    ← /dashboard, /sync/trigger, /sync/status
        ├── history.py      ← /history/, /history/<run_id>
        ├── settings.py     ← /settings/
        ├── admin.py        ← /admin/* (admin-only panel)
        └── email_drafts.py ← /drafts/
```

---

## Data Flow

1. **Snexi**: Login → close cookie popup → click Espace client → fill login form
   → click Indisponibilites menu → find FullCalendar iframe → extract 4 weeks of
   `.fc-event` nodes → (optional) click each OS event for detail enrichment
2. **Constatimmo**: Fresh browser (no persistent context) → login via `#sign_in`
   → navigate to `/profile#planification` → click Mon activité → Mes disponibilités
   → check `#comingOrdersCheckbox` → extract `#road-map-results` table rows
   → (optional) visit each ODM detail page
3. **Filter**: `classify_event()` assigns color + type based on CSS and text.
   Keep only `entree | sortie | odm`, drop `indisponibilite | trajet | autre`.
4. **Sync**: Load/refresh `token.json` → build Calendar service → fetch existing
   events (14 days back, 120 days forward, paginated) → dedup by ref number +
   start time → create/update/skip → return (created, updated, skipped) counts.
5. **Dashboard sync**: `sync_runner.run_sync_for_user(user_id)` decrypts DB
   credentials → builds `Settings` in-memory → calls same pipeline above →
   writes `SyncRun` + `SyncEvent` records to DB.

---

## Current Status (as of 2026-08-27)

| Component | Status | Notes |
|---|---|---|
| Snexi scraper | ✅ Working | 115 events / 4 weeks in live test |
| Constatimmo scraper | ✅ Working | 11 ODM events in live test |
| Event filter | ✅ Working | 20/20 unit tests pass |
| Google Calendar sync | ✅ Code complete | Requires `token.json` (one-time browser auth) |
| Gmail draft module | ⚠️ Stub | `email/__init__.py` is empty — logic to be ported |
| Dashboard routes | ✅ Working | 22/23 automated route tests pass |
| Dashboard auth (login/register) | ✅ Working | bcrypt hashed, Flask-Login sessions |
| Dashboard setup wizard | ✅ Working | 3-step flow + AJAX connection test |
| Dashboard sync trigger | ✅ Working | Background thread, live polling |
| Dashboard history | ✅ Working | Per-run event table |
| Dashboard admin panel | ✅ Working | Admin-only, 403 for regular users |
| Scheduler | ✅ Working | APScheduler, 07:00 Paris, embedded in Flask |

---

## Key Selectors / Login Flows

### Snexi Login
1. `https://snexi.fr/portail` → wait for load
2. Close Didomi popup: `#didomi-notice-agree-button` or text-based fallback
3. Click Espace client: `button[aria-label*="Ouvrir l'espace client"]` or DOM scan
4. Fill login: `#login` + `#password` (fallback: `input[type=text]` + `input[type=password]`)
5. Submit → click `a.lien_menu[href*='experts_indisponibilites.php']`
6. Find iframe with `indisponibilites` in URL → extract `.fc-event` nodes × 4 weeks

### Constatimmo Login
1. Fresh browser context (no persistent session — causes 403)
2. User-Agent: `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36...`
3. `https://constatonline.constatimmo.com` → redirects to `/sso/login`
4. Fill: `#sign_in` email + password inputs → submit
5. Navigate to `/profile#planification`
6. Click Mon activité → Mes disponibilités
7. Check `#comingOrdersCheckbox` → `getComingOrders()`
8. Extract `#road-map-results` table rows

### Google OAuth2 (user-consent, one-time)
- Flow: `InstalledAppFlow` with `urn:ietf:wg:oauth:2.0:oob` redirect
- Scopes: `calendar`, `gmail.compose`, `gmail.readonly`
- Token stored in `./token.json` (auto-refreshed on subsequent runs)
- SSL: corporate CA workaround — `httplib2.Http(disable_ssl_certificate_validation=True)`

---

## Color Code Mapping

### Snexi (CSS background-color)
- `rgb(207, 36, 36)` = Red = Indisponibilite → **SKIP**
- `rgb(18, 17, 171)` = Blue = EDL Entrée → **KEEP** (type=entree)
- `rgb(17, 138, 123)` = Green = EDL Sortie → **KEEP** (type=sortie)
- `rgb(156, 39, 176)` / other purples = ODM → **KEEP** (type=odm)
- Contains "trajet" in text → **SKIP**

### Google Calendar event colors (colorId)
- Snexi OS events: `colorId=5` (banana/yellow)
- Constatimmo ODM events: `colorId=11` (tomato/red)

---

## Environment Variables

### `calendar_sync` (standalone script)
| Variable | Default | Description |
|---|---|---|
| `SNEXI_URL` | `https://snexi.fr/portail` | Snexi portal URL |
| `SNEXI_USERNAME` | — | Snexi login |
| `SNEXI_PASSWORD` | — | Snexi password |
| `SNEXI_HEADLESS` | `true` | Run browser headless |
| `CONSTATIMMO_URL` | `https://constatonline.constatimmo.com` | |
| `CONSTATIMMO_USERNAME` | — | Constatimmo email |
| `CONSTATIMMO_PASSWORD` | — | Constatimmo password |
| `CONSTATIMMO_HEADLESS` | `true` | |
| `GOOGLE_CALENDAR_ID` | `primary` | Fallback calendar |
| `GOOGLE_OAUTH_CLIENT_ID` | — | Google Cloud Web app client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | — | Google Cloud client secret |
| `GOOGLE_TOKEN_PATH` | `./token.json` | Stored OAuth2 token |
| `GOOGLE_CALENDAR_OS_ID` | `primary` | Calendar for Snexi OS events |
| `GOOGLE_CALENDAR_ODM_ID` | `primary` | Calendar for Constatimmo ODM |
| `GOOGLE_COLOR_SNEXI` | `5` | Google Calendar colorId (1-11) |
| `GOOGLE_COLOR_CONSTATIMMO` | `11` | Google Calendar colorId (1-11) |
| `DRY_RUN` | `false` | Preview only — nothing written |
| `SNEXI_ENRICH_DETAILS` | `true` | Fetch detail pages (address, digicode…) |
| `CONSTATIMMO_ENRICH_DETAILS` | `true` | Fetch ODM detail pages |

### `dashboard` (Flask app — additional)
| Variable | Description |
|---|---|
| `FLASK_SECRET_KEY` | Session signing key (generate with `secrets.token_hex(32)`) |
| `DASHBOARD_SECRET_KEY` | Fernet key for credential encryption (generate with `Fernet.generate_key()`) |
| `DATABASE_URL` | SQLAlchemy URL (default: `sqlite:///dashboard.db`) |
| `ADMIN_EMAIL` | Auto-created admin account email |
| `ADMIN_PASSWORD` | Auto-created admin account password |
| `GOOGLE_CLIENT_ID` | Same as `GOOGLE_OAUTH_CLIENT_ID` |
| `GOOGLE_CLIENT_SECRET` | Same as `GOOGLE_OAUTH_CLIENT_SECRET` |
| `GOOGLE_REDIRECT_URI` | OAuth callback (e.g. `http://localhost:5000/setup/google/callback`) |
| `FLASK_DEBUG` | `false` in production |
| `PORT` | `5000` |

---

## Email Draft Module (Stub)

`src/calendar_sync/email/__init__.py` is empty. To implement:

1. Use `get_gmail_service(settings)` from `calendar_sync.sync.google_calendar`
2. Build draft body (HTML or plain text)
3. Call:
   ```python
   import base64
   from email.mime.text import MIMEText
   msg = MIMEText(body, 'html')
   msg['to'] = recipient
   msg['subject'] = subject
   raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
   service.users().drafts().create(userId='me', body={'message': {'raw': raw}}).execute()
   ```
4. Save `EmailDraft` record to DB with `run_id`, `recipient`, `subject`, `body_preview`

---

## Build & Run

```bash
# Standalone script
uv run calendar-sync              # Full extraction + sync
uv run calendar-sync --dry-run    # Preview only
uv run calendar-sync --sync-only  # Re-sync from cache (< 2h)

# Dashboard
export $(grep -v '^#' .env.dashboard | xargs)
uv run python run_dashboard.py
# → http://localhost:5000

# Tests
uv run pytest tests/ -v
```

---

## Known Issues

- **Snexi detail enrichment**: inline panel sometimes not found (5s timeout).
  Events are still extracted correctly — enrichment is best-effort.
- **Google OAuth SSL**: corporate CA bundles cause `CERTIFICATE_VERIFY_FAILED`.
  Workaround: `httplib2.Http(disable_ssl_certificate_validation=True)`.
- **APScheduler + Flask debug reloader**: use `use_reloader=False` (already set
  in `run_dashboard.py`).

---

## Legacy Reference

The original Node.js codebase (not in this repo) was at:
`/home/thomaslt/Documents/Work/Bot_calendar_sync/snexiSync.js` (2234 lines).
This Python project replaces it entirely with the same behavior.
Fixture data from the Node.js project is in `tests/fixtures/`.
