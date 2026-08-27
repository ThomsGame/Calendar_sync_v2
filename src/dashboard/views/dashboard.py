"""Main dashboard view and sync trigger endpoint."""

from __future__ import annotations

import threading

from flask import Blueprint, flash, jsonify, redirect, render_template, url_for
from flask_login import current_user, login_required

from dashboard import get_db
from dashboard.models import SyncEvent, SyncRun

bp = Blueprint("dashboard", __name__)


@bp.route("/dashboard")
@login_required
def home():
    db = get_db()

    # Last sync run
    last_run: SyncRun | None = (
        db.query(SyncRun)
        .filter_by(user_id=current_user.id)
        .order_by(SyncRun.started_at.desc())
        .first()
    )

    # Running sync check
    running_run: SyncRun | None = (
        db.query(SyncRun)
        .filter_by(user_id=current_user.id, status="running")
        .first()
    )

    # Recent activity: last 20 events created
    recent_events = (
        db.query(SyncEvent)
        .filter_by(user_id=current_user.id)
        .order_by(SyncEvent.created_at.desc())
        .limit(20)
        .all()
    )

    # Stats
    total_runs = db.query(SyncRun).filter_by(user_id=current_user.id).count()
    total_events = db.query(SyncEvent).filter_by(user_id=current_user.id).count()
    error_runs = (
        db.query(SyncRun)
        .filter_by(user_id=current_user.id, status="error")
        .count()
    )

    return render_template(
        "dashboard.html",
        last_run=last_run,
        running_run=running_run,
        recent_events=recent_events,
        total_runs=total_runs,
        total_events=total_events,
        error_runs=error_runs,
    )


@bp.route("/sync/trigger", methods=["POST"])
@login_required
def trigger_sync():
    """Trigger a manual sync in a background thread, return run_id."""
    db = get_db()

    # Prevent duplicate concurrent runs
    running = (
        db.query(SyncRun)
        .filter_by(user_id=current_user.id, status="running")
        .first()
    )
    if running:
        return jsonify({"ok": False, "message": "Une synchronisation est déjà en cours.", "run_id": running.id})

    user_id = current_user.id

    def _run():
        from dashboard.sync_runner import run_sync_for_user
        run_sync_for_user(user_id, trigger="manual")

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    return jsonify({"ok": True, "message": "Synchronisation lancée."})


@bp.route("/sync/status")
@login_required
def sync_status():
    """Poll endpoint: return current sync status for the user."""
    db = get_db()
    run: SyncRun | None = (
        db.query(SyncRun)
        .filter_by(user_id=current_user.id)
        .order_by(SyncRun.started_at.desc())
        .first()
    )
    if not run:
        return jsonify({"status": "none"})

    return jsonify({
        "run_id": run.id,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "events_created": run.events_created,
        "events_updated": run.events_updated,
        "error_message": run.error_message,
    })
