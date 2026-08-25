# Calendar Sync

Synchronisation automatique des rendez-vous Snexi et Constatimmo vers Google Calendar.

## Fonctionnalites

- **Extraction Snexi** : connexion au portail, extraction du calendrier FullCalendar sur 4 semaines, enrichment des details (adresse, locataire, digicode, etc.)
- **Extraction Constatimmo** : connexion, extraction de la table roadmap + elements calendrier, enrichment via fiches detail
- **Filtrage** : garde uniquement les entrees/sorties/ODM, ignore les indisponibilites et trajets
- **Sync Google Calendar** : creation/mise a jour d'evenements avec deduplication par numero OS/ODM

## Installation

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
```

## Configuration

```bash
cp .env.example .env
# Remplir les identifiants Snexi, Constatimmo et Google Calendar
```

## Utilisation

```bash
calendar-sync              # Extraction + sync complete
calendar-sync --sync-only  # Re-sync depuis le cache (2h)
```

## Tests

```bash
pytest tests/ -v
```

## Stack technique

- Python 3.11+
- Playwright (browser automation)
- Pydantic v2 (modeles de donnees)
- google-api-python-client (Google Calendar API)
- loguru (logging)
