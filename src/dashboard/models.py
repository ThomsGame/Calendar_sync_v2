"""SQLAlchemy models for the dashboard."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from flask_login import UserMixin
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(UserMixin, Base):
    """Application user account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    setup_complete: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    credentials: Mapped[Optional[UserCredentials]] = relationship(
        "UserCredentials", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    sync_runs: Mapped[list[SyncRun]] = relationship(
        "SyncRun", back_populates="user", cascade="all, delete-orphan", order_by="SyncRun.started_at.desc()"
    )
    email_drafts: Mapped[list[EmailDraft]] = relationship(
        "EmailDraft", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User {self.email}>"


class UserCredentials(Base):
    """Encrypted per-user credentials for all platforms."""

    __tablename__ = "user_credentials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), unique=True, nullable=False)

    # Snexi (encrypted)
    snexi_username: Mapped[Optional[str]] = mapped_column(String(255))
    snexi_password_enc: Mapped[Optional[str]] = mapped_column(Text)

    # Constatimmo (encrypted)
    constatimmo_username: Mapped[Optional[str]] = mapped_column(String(255))
    constatimmo_password_enc: Mapped[Optional[str]] = mapped_column(Text)
    constatimmo_headless: Mapped[bool] = mapped_column(Boolean, default=True)

    # Google Calendar
    google_calendar_os_id: Mapped[Optional[str]] = mapped_column(String(255))
    google_calendar_odm_id: Mapped[Optional[str]] = mapped_column(String(255))
    google_refresh_token_enc: Mapped[Optional[str]] = mapped_column(Text)
    google_token_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Feature flags
    snexi_enrich_details: Mapped[bool] = mapped_column(Boolean, default=True)
    constatimmo_enrich_details: Mapped[bool] = mapped_column(Boolean, default=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)

    # Sync schedule (hour of day, 0-23, Europe/Paris)
    sync_hour: Mapped[int] = mapped_column(Integer, default=7)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="credentials")


class SyncRun(Base):
    """Record of a single sync execution for a user."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime)

    # Status: pending | running | success | partial | error
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    trigger: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # manual | scheduled

    # Counts
    snexi_raw: Mapped[int] = mapped_column(Integer, default=0)
    constatimmo_raw: Mapped[int] = mapped_column(Integer, default=0)
    events_kept: Mapped[int] = mapped_column(Integer, default=0)
    events_created: Mapped[int] = mapped_column(Integer, default=0)
    events_updated: Mapped[int] = mapped_column(Integer, default=0)
    events_skipped: Mapped[int] = mapped_column(Integer, default=0)
    email_drafts_created: Mapped[int] = mapped_column(Integer, default=0)

    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="sync_runs")
    events: Mapped[list[SyncEvent]] = relationship(
        "SyncEvent", back_populates="run", cascade="all, delete-orphan"
    )

    @property
    def duration_seconds(self) -> Optional[int]:
        if self.finished_at and self.started_at:
            return int((self.finished_at - self.started_at).total_seconds())
        return None

    @property
    def is_running(self) -> bool:
        return self.status in ("pending", "running")


class SyncEvent(Base):
    """Individual calendar event created/updated during a sync run."""

    __tablename__ = "sync_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, ForeignKey("sync_runs.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Event metadata
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # snexi | constatimmo
    event_type: Mapped[Optional[str]] = mapped_column(String(20))     # entree | sortie | odm
    action: Mapped[str] = mapped_column(String(20), nullable=False)   # created | updated | skipped

    summary: Mapped[Optional[str]] = mapped_column(String(255))
    date: Mapped[Optional[str]] = mapped_column(String(10))            # YYYY-MM-DD
    start_time: Mapped[Optional[str]] = mapped_column(String(5))       # HH:MM
    end_time: Mapped[Optional[str]] = mapped_column(String(5))
    address: Mapped[Optional[str]] = mapped_column(Text)
    os_number: Mapped[Optional[str]] = mapped_column(String(50))
    odm_number: Mapped[Optional[str]] = mapped_column(String(50))
    google_event_id: Mapped[Optional[str]] = mapped_column(String(255))
    calendar_id: Mapped[Optional[str]] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    # Relationships
    run: Mapped[SyncRun] = relationship("SyncRun", back_populates="events")


class EmailDraft(Base):
    """Record of a Gmail draft created for a user."""

    __tablename__ = "email_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    run_id: Mapped[Optional[int]] = mapped_column(Integer, ForeignKey("sync_runs.id"))

    gmail_draft_id: Mapped[Optional[str]] = mapped_column(String(255))
    recipient: Mapped[Optional[str]] = mapped_column(String(255))
    subject: Mapped[Optional[str]] = mapped_column(String(500))
    body_preview: Mapped[Optional[str]] = mapped_column(Text)   # first 500 chars

    # Status: draft | sent (user sent it manually) | unknown
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), nullable=False)

    # Relationships
    user: Mapped[User] = relationship("User", back_populates="email_drafts")
