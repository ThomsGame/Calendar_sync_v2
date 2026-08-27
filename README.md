# Calendar Sync

Synchronisation automatique des rendez-vous Snexi et Constatimmo vers Google Calendar,
avec un dashboard web multi-utilisateurs pour la configuration et le suivi.

## Fonctionnalités

- **Extraction Snexi** : connexion au portail, extraction du calendrier FullCalendar sur 4 semaines, enrichissement des détails (adresse, locataire, digicode, etc.)
- **Extraction Constatimmo** : connexion, extraction de la table roadmap + éléments calendrier, enrichissement via fiches détail
- **Filtrage** : garde uniquement les entrées/sorties/ODM, ignore les indisponibilités et trajets
- **Sync Google Calendar** : création/mise à jour d'événements avec déduplication par numéro OS/ODM, couleur différente par source
- **Brouillons Gmail** : création de brouillons d'emails (module `email/`, pluggable)
- **Dashboard web** : interface Flask multi-utilisateurs avec wizard de configuration, historique des syncs et panel admin

## Installation rapide

```bash
uv venv && uv pip install -e ".[dev]"
uv run playwright install chromium
cp .env.example .env
# Remplir les identifiants dans .env
```

Pour le guide complet d'installation et de test, voir [SETUP_GUIDE.md](./SETUP_GUIDE.md).

## Utilisation — script standalone

```bash
uv run calendar-sync              # Extraction complète + sync
uv run calendar-sync --dry-run    # Prévisualisation, rien n'est écrit
uv run calendar-sync --sync-only  # Re-sync depuis le cache (< 2h)
```

## Utilisation — dashboard web

```bash
export $(grep -v '^#' .env.dashboard | xargs)
uv run python run_dashboard.py
# → http://localhost:5000
```

## Tests

```bash
uv run pytest tests/ -v    # 20 tests unitaires
```

## Stack technique

- Python 3.11+
- Playwright (browser automation, headless Chromium)
- Pydantic v2 (modèles de données)
- google-api-python-client + google-auth-oauthlib (Google Calendar & Gmail, OAuth2 user-consent)
- Flask + Flask-Login + SQLAlchemy + APScheduler (dashboard web)
- loguru (logging)

## Architecture

```
Snexi portal   ──┐
                 ├─→ filter ──→ Google Calendar (OS events, color 5)
Constatimmo   ──┘             Google Calendar (ODM events, color 11)
                               Gmail drafts
```

Le script standalone (`calendar-sync`) et le dashboard partagent le même moteur
de scraping et de synchronisation. Le dashboard ajoute : gestion multi-utilisateurs,
stockage chiffré des credentials, historique des runs, déclenchement automatique quotidien.

## Authentification Google

L'accès à Google Calendar et Gmail utilise le flux OAuth2 user-consent.
Un fichier `token.json` est généré lors de la première exécution (nécessite un vrai navigateur).
Il est ensuite réutilisé et rafraîchi automatiquement.

Voir la section 4 de [SETUP_GUIDE.md](./SETUP_GUIDE.md) pour les détails.
