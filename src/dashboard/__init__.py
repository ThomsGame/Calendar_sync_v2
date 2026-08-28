"""Flask application factory for the Calendar Sync dashboard."""

from __future__ import annotations

import os

import bcrypt
from flask import Flask

# Allow OAuth2 over HTTP for local development (localhost redirects).
# oauthlib requires HTTPS by default; this env var disables that check.
# Safe because localhost is not accessible from the internet.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
from flask_login import LoginManager
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

from dashboard.models import Base, User

# Module-level db session (scoped per thread/request)
engine = None
SessionLocal = None
db_session = None
login_manager = LoginManager()


def create_app(config: dict | None = None) -> Flask:
    """Create and configure the Flask application."""
    global engine, SessionLocal, db_session

    app = Flask(__name__, template_folder="templates", static_folder="static")

    # --- Configuration ---
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", os.urandom(32))
    app.config["DATABASE_URL"] = os.environ.get("DATABASE_URL", "sqlite:///dashboard.db")
    app.config["GOOGLE_CLIENT_ID"] = os.environ.get("GOOGLE_CLIENT_ID", "")
    app.config["GOOGLE_CLIENT_SECRET"] = os.environ.get("GOOGLE_CLIENT_SECRET", "")
    app.config["GOOGLE_REDIRECT_URI"] = os.environ.get(
        "GOOGLE_REDIRECT_URI", "http://localhost:5000/setup/google/callback"
    )
    app.config["ADMIN_EMAIL"] = os.environ.get("ADMIN_EMAIL", "")

    if config:
        app.config.update(config)

    # --- Database ---
    engine = create_engine(
        app.config["DATABASE_URL"],
        connect_args={"check_same_thread": False} if "sqlite" in app.config["DATABASE_URL"] else {},
    )
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db_session = scoped_session(SessionLocal)

    # Create all tables
    Base.metadata.create_all(bind=engine)

    # --- Flask-Login ---
    login_manager.init_app(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."
    login_manager.login_message_category = "warning"

    @login_manager.user_loader
    def load_user(user_id: str) -> User | None:
        return db_session.get(User, int(user_id))

    # Ensure db session is cleaned up after each request
    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db_session.remove()

    # --- Seed admin user if configured ---
    with app.app_context():
        _seed_admin(app)

    # --- Register blueprints ---
    from dashboard.auth import bp as auth_bp
    from dashboard.setup import bp as setup_bp
    from dashboard.views.dashboard import bp as dash_bp
    from dashboard.views.history import bp as history_bp
    from dashboard.views.settings import bp as settings_bp
    from dashboard.views.admin import bp as admin_bp
    from dashboard.views.email_drafts import bp as drafts_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(setup_bp, url_prefix="/setup")
    app.register_blueprint(dash_bp)
    app.register_blueprint(history_bp, url_prefix="/history")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    app.register_blueprint(admin_bp, url_prefix="/admin")
    app.register_blueprint(drafts_bp, url_prefix="/drafts")

    # --- Start scheduler ---
    from dashboard.scheduler import start_scheduler
    start_scheduler(app)

    return app


def get_db():
    """Return the scoped db session."""
    return db_session


def _seed_admin(app: Flask) -> None:
    """Create admin user on first run if ADMIN_EMAIL and ADMIN_PASSWORD are set."""
    admin_email = app.config.get("ADMIN_EMAIL", "")
    admin_password = os.environ.get("ADMIN_PASSWORD", "")
    if not admin_email or not admin_password:
        return

    session = db_session()
    existing = session.query(User).filter_by(email=admin_email).first()
    if existing:
        return

    pw_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
    admin = User(
        email=admin_email,
        password_hash=pw_hash,
        is_admin=True,
        setup_complete=True,
    )
    session.add(admin)
    session.commit()
    print(f"[DASHBOARD] Admin user created: {admin_email}")
