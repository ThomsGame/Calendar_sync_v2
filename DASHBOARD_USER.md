# Dashboard — User Guide

This guide covers everything you need to do as an end user of the Calendar Sync dashboard: creating an account, connecting your platforms, running syncs, and reading your results.

---

## Table of contents

1. [What the dashboard does](#1-what-the-dashboard-does)
2. [Create your account](#2-create-your-account)
3. [Setup wizard — step by step](#3-setup-wizard--step-by-step)
   - [Step 1 — Snexi credentials](#step-1--snexi-credentials)
   - [Step 2 — Constatimmo credentials](#step-2--constatimmo-credentials)
   - [Step 3 — Google account](#step-3--google-account)
4. [Dashboard home](#4-dashboard-home)
5. [Running a sync](#5-running-a-sync)
6. [Sync history](#6-sync-history)
7. [Email drafts](#7-email-drafts)
8. [Settings](#8-settings)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. What the dashboard does

The dashboard is a web interface for the Calendar Sync engine. Once configured, it:

- Scrapes your upcoming appointments from **Snexi** (états des lieux OS) and **Constatimmo** (ODMs).
- Filters the relevant ones using the same colour-code rules as the CLI tool.
- Creates or updates events in your **Google Calendar** so both platforms are visible in one place.
- Creates **Gmail draft emails** so you can notify the other platform with a single click when a new appointment is added.
- Runs this pipeline automatically every day at **07:00 Paris time**, and lets you trigger it manually at any time.

---

## 2. Create your account

Open the dashboard URL your administrator gave you (e.g. `http://192.168.1.53:5000`).

If you do not yet have an account:

1. Click **Créer un compte** on the login page.
2. Enter your email address and choose a password (minimum 8 characters).
3. Confirm the password and click **S'inscrire**.

You are immediately logged in and redirected to the setup wizard.

> **Note:** The first account (admin) is created by the server administrator, not through this form. Only the admin can grant admin rights to other users.

---

## 3. Setup wizard — step by step

The wizard runs once after registration. It takes about two minutes. You can also come back later and update any section in **Paramètres**.

### Step 1 — Snexi credentials

Enter your Snexi login (the email or username you use at `snexi.fr/portail`) and your password.

- Click **Tester la connexion** to verify the credentials before saving. A browser session opens in the background; the test takes about 10 seconds.
- Click **Suivant** to save and continue.

> Passwords are encrypted with Fernet symmetric encryption before being stored. The server administrator cannot read them in plain text.

### Step 2 — Constatimmo credentials

Same as step 1 but for your Constatimmo account (`constatonline.constatimmo.com`). Enter your email and password, test if you like, then click **Suivant**.

### Step 3 — Google account

This step connects your Google account so events can be written to Google Calendar and drafts can be created in Gmail.

1. Click **Se connecter avec Google**.
2. Your browser redirects to a Google sign-in page. Sign in with the Google account whose calendar you want to use.
3. On the permissions screen, accept the two requested scopes:
   - **Google Calendar** — to read and write calendar events.
   - **Gmail** — to create draft emails (the app never sends emails on your behalf).
4. Google redirects you back to the dashboard. You should see a green "Compte Google connecté" message.
5. Two dropdowns appear: **Calendrier OS** and **Calendrier ODM**. Select which Google Calendar each event type should be written to. If you only have one calendar, leave both on **primary**.
6. Click **Terminer la configuration**.

> If you need to reconnect Google (token expired, wrong account), go to **Paramètres → Google Calendar** and click **Reconnecter Google**.

---

## 4. Dashboard home

After setup, the home page (`/dashboard`) shows:

| Section | Description |
|---|---|
| **Dernière synchronisation** | Status, date, and summary counts of the most recent run |
| **Activité récente** | The last 20 calendar events created across all your syncs |
| **Statistiques** | Total runs, total events created, number of error runs |
| **Lancer une synchronisation** | Button to trigger a manual sync right now |

If a sync is already running the button is disabled and a spinner shows the live status.

---

## 5. Running a sync

### Manual sync

Click **Lancer une synchronisation** on the home page. The button turns into a spinner; the status bar updates automatically every few seconds.

A full sync takes **5 to 10 minutes** because:
- Snexi's calendar iframe needs time to render (up to ~25 seconds per load).
- The Constatimmo scraper opens each appointment detail page one by one.
- Google Calendar API calls are made sequentially.

When finished, the status changes to **Succès** (green) or **Erreur** (red). Click the run ID to see the full event list.

### Automatic daily sync

The dashboard scheduler runs a sync for every active user every morning at **07:00 Paris time**. You do not need to do anything; the results appear in your history the next time you open the dashboard.

---

## 6. Sync history

Click **Historique** in the navigation bar to see all your past sync runs.

Each row shows:

| Column | Meaning |
|---|---|
| Date | When the sync started |
| Déclencheur | `manual` (you clicked the button) or `scheduled` (automatic daily run) |
| Statut | `success`, `partial`, or `error` |
| Créés | Calendar events created during that run |
| Mis à jour | Events updated (if already existed with changed details) |
| Brouillons | Gmail drafts created |
| Durée | How long the sync took |

Click a row to open the **detail view** for that run. It lists every individual event with its date, time, source (Snexi or Constatimmo), address, and action taken.

---

## 7. Email drafts

Click **Brouillons** in the navigation bar to see all Gmail drafts the dashboard has created for you.

Each draft is a pre-composed email addressed to the other platform:

- A **Snexi OS** event triggers a draft to your **Constatimmo contact** telling them you are unavailable on that date.
- A **Constatimmo ODM** event triggers a draft to your **Snexi contact** for the same reason.

The dashboard only creates the draft. **You** decide whether to send it (open Gmail, find the draft, review it, and click Send). The dashboard never sends email automatically.

The **Statut** column shows `draft` until you send it. Once sent in Gmail, the status stays `draft` in the dashboard (the app does not track whether you actually sent it — that is by design to avoid requiring additional Gmail permissions).

> To configure who receives these drafts, go to **Paramètres → Brouillons email**.

---

## 8. Settings

Click **Paramètres** in the navigation bar to update any part of your configuration.

### Snexi

Update your Snexi username or password. Leave the password field blank to keep the current stored password unchanged.

### Constatimmo

Same as Snexi.

### Google Calendar

Change which calendar receives OS events and which receives ODM events. You can also reconnect your Google account here if the token has expired.

**Finding a calendar ID:**
1. Open Google Calendar in your browser.
2. Click the three dots next to the calendar name → **Paramètres et partage**.
3. Scroll down to **Intégration du calendrier** → copy the **ID du calendrier** (looks like `xxx@group.calendar.google.com` or your email for the primary calendar).

### Options

| Option | Default | Description |
|---|---|---|
| Enrichir les détails Snexi | On | Open each OS detail page to fetch the full address. Adds ~2 min to scraping. |
| Enrichir les détails Constatimmo | On | Open each ODM detail page for address and tenant info. |
| Mode simulation (dry run) | Off | Scrape and filter as normal but make **no changes** to Google Calendar or Gmail. Useful to preview what would be synced. |

### Brouillons email

| Field | Description |
|---|---|
| Activer les brouillons | Master toggle. If off, no drafts are created for any sync. |
| Email Constatimmo | The address that receives drafts when a Snexi OS appointment is added. |
| Email Snexi | The address that receives drafts when a Constatimmo ODM is added. |
| Votre nom | Appears at the bottom of each draft email as the sender's name. |

---

## 9. Troubleshooting

### The sync runs but creates 0 events

- **Snexi:** Check that your Snexi calendar has appointments with the correct colour codes (the sync only picks up events in green, yellow, orange, and blue — not cancelled/grey ones).
- **Constatimmo:** Confirm there are upcoming ODMs in your Constatimmo account.
- Both platforms were scraped but everything was already in Google Calendar → **Mis à jour: 0, Ignorés: N** — this is normal, the deduplication is working correctly.

### "Compte Google non connecté" on every sync

Your Google refresh token has expired or been revoked. Go to **Paramètres → Google Calendar** and click **Reconnecter Google** to go through the OAuth flow again.

### Sync status stays "En cours" for more than 20 minutes

The scraper may have hung on one of the portals. Contact your administrator; they can see the server logs and cancel stuck runs.

### I get an error about Snexi / Constatimmo login

Your portal password may have changed. Go to **Paramètres** and update the relevant credentials.

### Dry-run mode is on but I want real events

Go to **Paramètres → Options** and turn off **Mode simulation**.

### Gmail drafts are not appearing

1. Check **Paramètres → Brouillons email**: is the toggle on and are both email fields filled in?
2. Open Gmail directly — drafts appear in the **Brouillons** folder; they do not appear as sent messages.
3. If the Google account was reconnected recently, the new token may not have Gmail compose permission — reconnect and make sure to accept all permissions on the Google consent screen.
