# Calendar Sync — CLI

Scrapes appointment schedules from **Snexi** and **Constatimmo**, filters them
to keep only billable events (états des lieux d'entrée/sortie, ODM), pushes
them to **Google Calendar**, and creates **Gmail draft emails** notifying the
other platform of each new unavailability.

---

## Table of contents

1. [How it works](#how-it-works)
2. [Requirements](#requirements)
3. [Installation](#installation)
4. [Google Cloud setup](#google-cloud-setup)
5. [Configuration](#configuration)
6. [First run — Google OAuth](#first-run--google-oauth)
7. [Running the CLI](#running-the-cli)
8. [Output & logs](#output--logs)
9. [Event filtering logic](#event-filtering-logic)
10. [Gmail draft logic](#gmail-draft-logic)
11. [Scheduling automatic runs](#scheduling-automatic-runs)
12. [Troubleshooting](#troubleshooting)

---

## How it works

```
Snexi portal ──► browser automation ──► raw events (4 weeks)
                                            │
Constatimmo ───► browser automation ──► raw events (roadmap)
                                            │
                                    ┌───────▼────────┐
                                    │  Filter engine  │
                                    │ keep: entrée /  │
                                    │ sortie / ODM    │
                                    │ drop: indispo / │
                                    │ trajet          │
                                    └───────┬────────┘
                                            │
                              ┌─────────────▼──────────────┐
                              │     Google Calendar sync    │
                              │  dedup by ref number +      │
                              │  start time, create/update  │
                              └─────────────┬──────────────┘
                                            │ newly created events only
                              ┌─────────────▼──────────────┐
                              │      Gmail drafts           │
                              │  Snexi event → draft to     │
                              │  Constatimmo contact        │
                              │  Constatimmo event → draft  │
                              │  to Snexi contact           │
                              └────────────────────────────┘
```

A full run takes **3–6 minutes** depending on network speed and the number
of detail pages to enrich.

---

## Requirements

| Requirement | Version |
|---|---|
| Python | ≥ 3.11 |
| uv | ≥ 0.5 (package manager) |
| Chromium | installed via Playwright |
| Google Cloud project | with Calendar API + Gmail API enabled |

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/ThomsGame/Calendar_sync_v2.git
cd Calendar_sync_v2
```

### 2. Install dependencies

```bash
# Install uv if not already present
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install project + all dependencies into a local virtualenv
uv sync
```

### 3. Install Playwright's Chromium browser

```bash
uv run playwright install chromium
```

> **Linux / WSL users:** if Playwright reports your distro is unsupported, the
> binary may already be cached. Run `uv run python -c "from playwright.sync_api
> import sync_playwright; print('ok')"` to check. If it prints `ok`, you're
> fine.

---

## Google Cloud setup

You need a Google Cloud project with two APIs enabled and an OAuth 2.0 client
credential created. This is a **one-time setup per Google account**.

### 1. Create or select a project

Go to [console.cloud.google.com](https://console.cloud.google.com) and create
a new project (e.g. `calendar-sync`).

### 2. Enable APIs

In the project, go to **APIs & Services → Library** and enable:

- **Google Calendar API**
- **Gmail API**

### 3. Configure the OAuth consent screen

Go to **APIs & Services → OAuth consent screen**:

- User type: **External**
- App name: anything (e.g. `Calendar Sync`)
- Support email: your email
- Scopes: add `calendar`, `gmail.compose`, `gmail.readonly`
- Test users: add the Google account(s) you will sync to

> While the app is in **Testing** mode only accounts listed as test users can
> authorize it. This is fine for personal use — you do not need to publish the
> app.

### 4. Create OAuth credentials

Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**:

- Application type: **Desktop app** (or *Web application* if you plan to use
  the dashboard — see dashboard documentation)
- Name: anything

Download the JSON or copy the **Client ID** and **Client Secret** — you will
need them in the next step.

---

## Configuration

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Then edit `.env`:

```dotenv
# ── Snexi ────────────────────────────────────────────────────────────────────
SNEXI_URL=https://snexi.fr/portail
SNEXI_USERNAME=your_snexi_login
SNEXI_PASSWORD=your_snexi_password
SNEXI_HEADLESS=true          # false = show browser window (useful for debugging)
SNEXI_ENRICH_DETAILS=true    # fetch individual OS detail pages (address, tenant…)

# ── Constatimmo ───────────────────────────────────────────────────────────────
CONSTATIMMO_URL=https://constatonline.constatimmo.com
CONSTATIMMO_USERNAME=your_constatimmo_email
CONSTATIMMO_PASSWORD=your_constatimmo_password
CONSTATIMMO_HEADLESS=true
CONSTATIMMO_ENRICH_DETAILS=true  # fetch individual ODM detail pages

# ── Google OAuth 2.0 ──────────────────────────────────────────────────────────
GOOGLE_OAUTH_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
GOOGLE_TOKEN_PATH=./token.json   # where the auth token is stored after first login

# ── Google Calendar ───────────────────────────────────────────────────────────
# Use "primary" for your main calendar, or a specific calendar ID
# (find IDs in Google Calendar → Settings → [calendar name] → Calendar ID)
GOOGLE_CALENDAR_OS_ID=primary        # Snexi OS events land here
GOOGLE_CALENDAR_ODM_ID=primary       # Constatimmo ODM events land here

# Event colours in Google Calendar (1–11, Google's colour palette)
# 5 = banana/yellow   11 = tomato/red
GOOGLE_COLOR_SNEXI=5
GOOGLE_COLOR_CONSTATIMMO=11

# ── Gmail drafts ──────────────────────────────────────────────────────────────
GMAIL_DRAFTS_ENABLED=true

# When a Snexi OS event is created, draft a notification to Constatimmo:
CONSTATIMMO_CONTACT_EMAIL=contact@constatimmo.com

# When a Constatimmo ODM is created, draft a notification to Snexi:
SNEXI_CONTACT_EMAIL=contact@snexi.fr

# Optional: your name appears at the bottom of each draft
SENDER_NAME=Jean Dupont

# ── Flags ─────────────────────────────────────────────────────────────────────
DRY_RUN=false   # true = preview only, nothing written to Google Calendar or Gmail
```

### Environment variable reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `SNEXI_USERNAME` | Yes | — | Snexi login |
| `SNEXI_PASSWORD` | Yes | — | Snexi password |
| `SNEXI_HEADLESS` | No | `true` | Run browser headless |
| `SNEXI_ENRICH_DETAILS` | No | `true` | Fetch OS detail pages |
| `CONSTATIMMO_USERNAME` | Yes | — | Constatimmo email |
| `CONSTATIMMO_PASSWORD` | Yes | — | Constatimmo password |
| `CONSTATIMMO_HEADLESS` | No | `true` | Run browser headless |
| `CONSTATIMMO_ENRICH_DETAILS` | No | `true` | Fetch ODM detail pages |
| `GOOGLE_OAUTH_CLIENT_ID` | Yes | — | Google Cloud OAuth client ID |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Yes | — | Google Cloud OAuth client secret |
| `GOOGLE_TOKEN_PATH` | No | `./token.json` | Path to store the OAuth token |
| `GOOGLE_CALENDAR_OS_ID` | No | `primary` | Calendar for Snexi OS events |
| `GOOGLE_CALENDAR_ODM_ID` | No | `primary` | Calendar for Constatimmo ODM events |
| `GOOGLE_COLOR_SNEXI` | No | `5` | Google Calendar colour for OS events |
| `GOOGLE_COLOR_CONSTATIMMO` | No | `11` | Google Calendar colour for ODM events |
| `GMAIL_DRAFTS_ENABLED` | No | `true` | Enable Gmail draft creation |
| `CONSTATIMMO_CONTACT_EMAIL` | No | — | Recipient for Snexi-triggered drafts |
| `SNEXI_CONTACT_EMAIL` | No | — | Recipient for Constatimmo-triggered drafts |
| `SENDER_NAME` | No | — | Signature name in draft emails |
| `DRY_RUN` | No | `false` | Preview mode — no writes |

---

## First run — Google OAuth

The first time you run the tool it needs your permission to access your Google
Calendar and Gmail. This is a **one-time browser step**.

### Step 1 — Generate the authorization URL

Run this command to get a URL:

```bash
uv run python -c "
import urllib.parse as up

CLIENT_ID = 'your-client-id.apps.googleusercontent.com'
SCOPES = ' '.join([
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.readonly',
])
url = (
    'https://accounts.google.com/o/oauth2/auth'
    '?response_type=code'
    f'&client_id={CLIENT_ID}'
    f'&redirect_uri={up.quote(\"urn:ietf:wg:oauth:2.0:oob\", safe=\"\")}'
    f'&scope={up.quote(SCOPES)}'
    '&access_type=offline'
    '&prompt=consent'
)
print(url)
"
```

Replace `your-client-id.apps.googleusercontent.com` with your actual client ID
from Google Cloud.

### Step 2 — Authorize in your browser

Open the printed URL in any browser. Log in with the Google account you want to
sync to. Click **Allow** on the consent screen. Google will display an
**authorization code** directly on the page.

### Step 3 — Exchange the code for a token

```bash
uv run python -c "
import urllib.parse as up, urllib.request, json, ssl, pathlib

code   = 'PASTE-CODE-HERE'
cid    = 'your-client-id.apps.googleusercontent.com'
secret = 'your-client-secret'

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

data = up.urlencode({
    'code': code, 'client_id': cid, 'client_secret': secret,
    'redirect_uri': 'urn:ietf:wg:oauth:2.0:oob',
    'grant_type': 'authorization_code',
}).encode()

req = urllib.request.Request(
    'https://oauth2.googleapis.com/token', data=data, method='POST',
    headers={'Content-Type': 'application/x-www-form-urlencoded'},
)
with urllib.request.urlopen(req, context=ctx) as r:
    t = json.loads(r.read())

pathlib.Path('token.json').write_text(json.dumps({
    'token': t['access_token'],
    'refresh_token': t['refresh_token'],
    'token_uri': 'https://oauth2.googleapis.com/token',
    'client_id': cid, 'client_secret': secret,
    'scopes': [
        'https://www.googleapis.com/auth/calendar',
        'https://www.googleapis.com/auth/gmail.compose',
        'https://www.googleapis.com/auth/gmail.readonly',
    ],
    'expiry': None,
}, indent=2))
print('token.json saved — refresh_token present:', bool(t.get('refresh_token')))
"
```

A `token.json` file is created. **Keep it safe** — it grants access to your
Google Calendar and Gmail. Add it to `.gitignore` (already done in this repo).

From this point on the tool refreshes the token automatically. You only need
to repeat this step if you revoke access in your Google account settings.

---

## Running the CLI

### Full extraction + sync (normal use)

```bash
uv run calendar-sync
```

Scrapes both platforms, filters events, syncs to Google Calendar, creates
Gmail drafts for new events. Takes 3–6 minutes.

### Dry run — preview without writing anything

```bash
uv run calendar-sync --dry-run
```

Runs the full scrape and filter pipeline but does **not** write to Google
Calendar or Gmail. Shows exactly what would be created/updated. Useful to
verify the tool is working before committing changes.

### Sync only — re-push from cache without re-scraping

```bash
uv run calendar-sync --sync-only
```

Re-syncs the last scraped event list to Google Calendar without hitting the
Snexi or Constatimmo websites again. Uses the `appointments.cache.json` file
written by the last full run. The cache expires after **2 hours**.

---

## Output & logs

Every run produces timestamped log lines to stderr:

```
11:42:36 | INFO     | Calendar Sync — starting full extraction
11:42:36 | INFO     |   Snexi user    : jnjockba
11:42:36 | INFO     |   Constatimmo   : jacquesnjockbalepa@yahoo.fr
11:42:36 | INFO     |   Calendar OS   : primary
11:42:36 | INFO     |   Calendar ODM  : primary
11:42:36 | INFO     |   Dry run       : False
11:42:42 | INFO     | [LOGIN] Espace client clicked.
11:42:49 | INFO     | [LOGIN] Indisponibilites menu clicked.
11:43:12 | INFO     | [EXTRACTION] Week 1: 50 appointments.
11:43:15 | INFO     | [EXTRACTION] Week 2: 26 appointments.
11:43:18 | INFO     | [EXTRACTION] Week 3: 17 appointments.
11:43:21 | INFO     | [EXTRACTION] Week 4: 24 appointments.
11:43:21 | INFO     | [EXTRACTION] 117 total appointments extracted.
11:45:56 | INFO     | [CONSTATIMMO] 10 events detected. Enriched: 0.
11:46:03 | INFO     | [FILTER] Kept: 33 | Entrees: 18 | Sorties: 15 | ODM: 0 | Skipped red (indispo): 71 | Skipped trajet: 23
11:46:04 | INFO     | [GOOGLE] Fetched 32 existing events from 1 calendar(s).
11:46:04 | INFO     | [GOOGLE] Created: OS E on 2026-09-07 11:00-12:20
11:46:05 | INFO     | [GOOGLE] Sync complete — Created: 2 | Updated: 0 | Skipped: 31
11:46:05 | INFO     | [EMAIL] 2 draft(s) created
11:46:05 | INFO     |   → contact@constatimmo.com | Indisponibilité – état des lieux d'entrée le lundi 7 septembre 2026
11:46:05 | INFO     | All done.
```

### Output files written to the working directory

| File | Contents |
|---|---|
| `appointments.filtered.json` | All kept events in JSON (entrées/sorties/ODM) |
| `appointments.cache.json` | Same + timestamp, used by `--sync-only` |
| `appointments.stats.json` | Filter counters per run |
| `token.json` | Google OAuth token (auto-refreshed) |

---

## Event filtering logic

Raw events from both platforms are passed through a classifier before being
synced to Google Calendar.

### Snexi — classification by CSS colour

Snexi renders events with background colours that encode their type:

| CSS colour | Type | Action |
|---|---|---|
| `rgb(207, 36, 36)` — red | Indisponibilité | **Skip** |
| `rgb(18, 17, 171)` — blue | EDL Entrée (OS) | **Keep** |
| `rgb(17, 138, 123)` — green | EDL Sortie (OS) | **Keep** |
| Purple variants | ODM | **Keep** |
| Text contains "trajet" | Trajet | **Skip** |

### Constatimmo — classification by table row content

Constatimmo events in the roadmap table are all ODM-type and kept by default.
Rows without a valid ODM reference are skipped.

### Google Calendar event naming

| Event type | Calendar title |
|---|---|
| OS Entrée (Snexi) | `OS E` |
| OS Sortie (Snexi) | `OS S` |
| ODM Entrée | `ODM E` |
| ODM Sortie | `ODM S` |
| ODM (other) | `ODM` |

### Deduplication

Before creating an event the tool checks Google Calendar for an existing event
with the same **OS/ODM reference number** at the same **start time**. If one
is found it is skipped (or updated if address/description changed). This
prevents duplicate entries on repeated runs.

---

## Gmail draft logic

For every event **newly created** in Google Calendar the tool creates a Gmail
draft notifying the other platform that the slot is now unavailable:

| New calendar event | Draft sent to |
|---|---|
| Snexi OS (entrée/sortie) | `CONSTATIMMO_CONTACT_EMAIL` |
| Constatimmo ODM | `SNEXI_CONTACT_EMAIL` |

The draft is **not sent automatically** — it lands in your Gmail Drafts folder
for you to review and send. The subject and body are generated in French:

```
Subject: Indisponibilité – état des lieux d'entrée le lundi 7 septembre 2026 (ref. 2381960)

Madame, Monsieur,

Je me permets de vous contacter afin de vous informer que je ne serai pas
disponible le lundi 7 septembre 2026 de 11:00 – 12:20.

En effet, un état des lieux d'entrée a été programmé via Snexi sur ce
créneau, ce qui me rend indisponible pour toute autre intervention durant
cette période.
...
```

If `CONSTATIMMO_CONTACT_EMAIL` or `SNEXI_CONTACT_EMAIL` is not set, drafts for
that direction are silently skipped (no error).

---

## Scheduling automatic runs

### Linux / WSL — cron

Run the sync every morning at 07:00:

```bash
crontab -e
```

Add:

```
0 7 * * * cd /path/to/Calendar_sync_v2 && /path/to/.local/bin/uv run calendar-sync >> /var/log/calendar-sync.log 2>&1
```

Find your `uv` path with `which uv`.

### Linux — systemd timer

Create `/etc/systemd/system/calendar-sync.service`:

```ini
[Unit]
Description=Calendar Sync — Snexi & Constatimmo to Google Calendar

[Service]
Type=oneshot
WorkingDirectory=/path/to/Calendar_sync_v2
ExecStart=/home/youruser/.local/bin/uv run calendar-sync
EnvironmentFile=/path/to/Calendar_sync_v2/.env
StandardOutput=journal
StandardError=journal
```

Create `/etc/systemd/system/calendar-sync.timer`:

```ini
[Unit]
Description=Run Calendar Sync daily at 07:00

[Timer]
OnCalendar=*-*-* 07:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now calendar-sync.timer
sudo systemctl status calendar-sync.timer
```

---

## Troubleshooting

### "No calendar iframe found" / 0 events from Snexi

The Snexi calendar takes several seconds to render after login. The tool waits
up to 25 seconds for it to appear. If you consistently get 0 events:

- Set `SNEXI_HEADLESS=false` to watch the browser — this lets you see what
  is happening visually
- Check that your Snexi credentials are correct
- Snexi may have changed their page structure — open an issue on GitHub

### "Extraction failed" / login loop on Constatimmo

Constatimmo occasionally returns a 403 if a persistent browser session is
detected. The tool uses a fresh browser context each time to avoid this. If it
persists, delete the `.browser/constatimmo/` directory and retry.

### "invalid_grant: Token has been expired or revoked"

Your `token.json` refresh token has been revoked. This happens if:

- You revoked access in [myaccount.google.com/permissions](https://myaccount.google.com/permissions)
- Google automatically revoked it after 6 months of inactivity (only affects
  apps in Testing mode on Google Cloud)

**Fix:** delete `token.json` and repeat the [First run — Google OAuth](#first-run--google-oauth) steps.

### SSL certificate errors on corporate networks

On networks with a corporate CA (e.g. some company VPNs), Python may reject
Google's certificates. The tool automatically works around this by using a
relaxed SSL context for all Google API calls. If you still see SSL errors,
ensure `REQUESTS_CA_BUNDLE` is not set to a broken bundle:

```bash
unset REQUESTS_CA_BUNDLE
uv run calendar-sync
```

### "Fernet key must be 32 url-safe base64-encoded bytes"

This error only appears when running the **dashboard**, not the CLI. See the
dashboard documentation.

### Dry run to verify before a live run

Always safe to run first:

```bash
uv run calendar-sync --dry-run
```

This shows exactly what the tool would create without touching Google Calendar
or Gmail.
