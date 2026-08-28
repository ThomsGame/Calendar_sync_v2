"""Gmail draft creation for unavailability notifications.

For each new calendar event created during a sync run, we create a Gmail
draft notifying the *other* platform that the time slot is no longer
available. The user reviews and sends these drafts manually from Gmail.

Logic:
  - Snexi OS event created  → draft to Constatimmo recipient
  - Constatimmo ODM created → draft to Snexi recipient

Recipients are configured per-user via dashboard settings.
If a recipient is not configured for the target platform, no draft is created
for that direction (the function skips gracefully).
"""

from __future__ import annotations

import base64
import re
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from loguru import logger

from calendar_sync.models.appointment import Appointment, AppointmentSource, AppointmentType


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

class DraftRecipients:
    """Holds per-user recipient addresses for draft emails."""

    def __init__(
        self,
        snexi_contact: Optional[str] = None,
        constatimmo_contact: Optional[str] = None,
    ) -> None:
        # snexi_contact: email address at Snexi to notify when a Constatimmo
        #   ODM is added (i.e. we are now busy → tell Snexi)
        # constatimmo_contact: email address at Constatimmo to notify when a
        #   Snexi OS event is added (i.e. we are now busy → tell Constatimmo)
        self.snexi_contact = snexi_contact
        self.constatimmo_contact = constatimmo_contact

    def recipient_for(self, appointment: Appointment) -> Optional[str]:
        """Return the email address to notify for a given appointment.

        A Snexi event means we're busy for Snexi → notify Constatimmo.
        A Constatimmo event means we're busy for Constatimmo → notify Snexi.
        """
        if appointment.source == AppointmentSource.SNEXI:
            return self.constatimmo_contact or None
        if appointment.source == AppointmentSource.CONSTATIMMO:
            return self.snexi_contact or None
        return None


# ---------------------------------------------------------------------------
# Email content builders
# ---------------------------------------------------------------------------

_FRENCH_MONTHS = {
    1: "janvier", 2: "février", 3: "mars", 4: "avril",
    5: "mai", 6: "juin", 7: "juillet", 8: "août",
    9: "septembre", 10: "octobre", 11: "novembre", 12: "décembre",
}

_FRENCH_DAYS = {
    0: "lundi", 1: "mardi", 2: "mercredi", 3: "jeudi",
    4: "vendredi", 5: "samedi", 6: "dimanche",
}


def _format_date_fr(date_iso: Optional[str]) -> str:
    """Convert 'YYYY-MM-DD' to 'lundi 12 janvier 2026'."""
    if not date_iso:
        return "date inconnue"
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        day_name = _FRENCH_DAYS[dt.weekday()]
        month_name = _FRENCH_MONTHS[dt.month]
        return f"{day_name} {dt.day} {month_name} {dt.year}"
    except ValueError:
        return date_iso


def _format_time_range(start_time: Optional[str], end_time: Optional[str]) -> str:
    if start_time and end_time:
        return f"{start_time} – {end_time}"
    if start_time:
        return f"à partir de {start_time}"
    return "heure inconnue"


def _event_type_label(appointment: Appointment) -> str:
    if appointment.meta is None:
        return "intervention"
    t = appointment.meta.type
    if t == AppointmentType.ENTREE:
        return "état des lieux d'entrée"
    if t == AppointmentType.SORTIE:
        return "état des lieux de sortie"
    if t == AppointmentType.ODM:
        return "ODM"
    return "intervention"


def _source_label(appointment: Appointment) -> str:
    if appointment.source == AppointmentSource.SNEXI:
        return "Snexi"
    if appointment.source == AppointmentSource.CONSTATIMMO:
        return "Constatimmo"
    return "une autre plateforme"


def _ref_number(appointment: Appointment) -> Optional[str]:
    return appointment.os_number or appointment.odm_number or None


def build_subject(appointment: Appointment) -> str:
    """Build the email subject line."""
    date_fr = _format_date_fr(appointment.date)
    event_label = _event_type_label(appointment)
    ref = _ref_number(appointment)
    ref_part = f" (ref. {ref})" if ref else ""
    return f"Indisponibilité – {event_label} le {date_fr}{ref_part}"


def build_body_html(appointment: Appointment, sender_name: str = "") -> str:
    """Build the HTML email body."""
    date_fr = _format_date_fr(appointment.date)
    time_range = _format_time_range(appointment.start_time, appointment.end_time)
    event_label = _event_type_label(appointment)
    source_label = _source_label(appointment)
    ref = _ref_number(appointment)

    greeting = f"Madame, Monsieur,"
    sender_line = f"<br>{sender_name}," if sender_name else ""

    ref_line = f"<br><strong>Référence :</strong> {ref}" if ref else ""
    address_line = (
        f"<br><strong>Adresse :</strong> {appointment.address}"
        if appointment.address
        else ""
    )

    body = f"""
<html>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #333;">
<p>{greeting}</p>

<p>
Je me permets de vous contacter afin de vous informer que je ne serai pas disponible
le <strong>{date_fr}</strong> de <strong>{time_range}</strong>.
</p>

<p>
En effet, un {event_label} a été programmé via <strong>{source_label}</strong> sur ce créneau,
ce qui me rend indisponible pour toute autre intervention durant cette période.
</p>

<p>
Je vous remercie de bien vouloir en tenir compte et de ne pas m'affecter de mission
sur ce créneau.
</p>

<p><strong>Détails du créneau occupé :</strong>
<br><strong>Date :</strong> {date_fr}
<br><strong>Horaires :</strong> {time_range}{ref_line}{address_line}
</p>

<p>
Restant à votre disposition pour toute question,
{sender_line}
</p>

<p style="color: #888; font-size: 12px;">
— Ce message a été généré automatiquement par Calendar Sync. Merci de le relire avant envoi.
</p>
</body>
</html>
"""
    return body.strip()


def build_body_plain(appointment: Appointment, sender_name: str = "") -> str:
    """Build the plain-text fallback for the email."""
    date_fr = _format_date_fr(appointment.date)
    time_range = _format_time_range(appointment.start_time, appointment.end_time)
    event_label = _event_type_label(appointment)
    source_label = _source_label(appointment)
    ref = _ref_number(appointment)

    lines = [
        "Madame, Monsieur,",
        "",
        f"Je me permets de vous contacter afin de vous informer que je ne serai pas disponible",
        f"le {date_fr} de {time_range}.",
        "",
        f"En effet, un {event_label} a été programmé via {source_label} sur ce créneau,",
        f"ce qui me rend indisponible pour toute autre intervention durant cette période.",
        "",
        "Je vous remercie de bien vouloir en tenir compte et de ne pas m'affecter de mission",
        "sur ce créneau.",
        "",
        "Détails du créneau occupé :",
        f"  Date    : {date_fr}",
        f"  Horaires: {time_range}",
    ]
    if ref:
        lines.append(f"  Référence: {ref}")
    if appointment.address:
        lines.append(f"  Adresse  : {appointment.address}")

    lines += [
        "",
        "Restant à votre disposition pour toute question,",
    ]
    if sender_name:
        lines.append(sender_name)
    lines += [
        "",
        "-- Ce message a été généré automatiquement par Calendar Sync.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Draft creation
# ---------------------------------------------------------------------------

def _build_mime_message(
    to: str,
    subject: str,
    body_html: str,
    body_plain: str,
) -> MIMEMultipart:
    """Assemble a MIME multipart/alternative message."""
    msg = MIMEMultipart("alternative")
    msg["to"] = to
    msg["subject"] = subject
    msg.attach(MIMEText(body_plain, "plain", "utf-8"))
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg


def _raw_encode(msg: MIMEMultipart) -> str:
    """Base64url-encode the message for the Gmail API."""
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


class DraftResult:
    """Result of a single draft creation attempt."""

    def __init__(
        self,
        appointment: Appointment,
        recipient: str,
        subject: str,
        body_preview: str,
        gmail_draft_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self.appointment = appointment
        self.recipient = recipient
        self.subject = subject
        self.body_preview = body_preview
        self.gmail_draft_id = gmail_draft_id
        self.error = error

    @property
    def ok(self) -> bool:
        return self.gmail_draft_id is not None


def create_gmail_drafts(
    service,
    appointments: list[Appointment],
    recipients: DraftRecipients,
    sender_name: str = "",
    dry_run: bool = False,
) -> list[DraftResult]:
    """Create Gmail drafts for all newly-created calendar events.

    Args:
        service: Authenticated Gmail API service (from get_gmail_service).
        appointments: List of appointments that were newly created in Google
            Calendar (i.e. action == "created" from the sync run).
        recipients: Per-user DraftRecipients config.
        sender_name: Optional name/signature appended to the email.
        dry_run: If True, build the drafts but do not call the Gmail API.

    Returns:
        List of DraftResult objects, one per appointment that had a
        configured recipient.
    """
    results: list[DraftResult] = []

    for appt in appointments:
        recipient = recipients.recipient_for(appt)
        if not recipient:
            logger.debug(
                f"[EMAIL] No recipient configured for {appt.source.value} event "
                f"on {appt.date} — skipping draft."
            )
            continue

        subject = build_subject(appt)
        body_html = build_body_html(appt, sender_name=sender_name)
        body_plain = build_body_plain(appt, sender_name=sender_name)
        body_preview = body_plain[:500]

        if dry_run:
            logger.info(
                f"[DRY_RUN][EMAIL] Would create draft → {recipient} | {subject}"
            )
            results.append(
                DraftResult(
                    appointment=appt,
                    recipient=recipient,
                    subject=subject,
                    body_preview=body_preview,
                    gmail_draft_id="dry_run",
                )
            )
            continue

        try:
            msg = _build_mime_message(recipient, subject, body_html, body_plain)
            raw = _raw_encode(msg)
            draft = service.users().drafts().create(
                userId="me",
                body={"message": {"raw": raw}},
            ).execute()
            draft_id = draft.get("id", "")
            logger.info(
                f"[EMAIL] Draft created → {recipient} | {subject} | ID={draft_id}"
            )
            results.append(
                DraftResult(
                    appointment=appt,
                    recipient=recipient,
                    subject=subject,
                    body_preview=body_preview,
                    gmail_draft_id=draft_id,
                )
            )
        except Exception as e:
            logger.error(
                f"[EMAIL] Failed to create draft for {appt.source.value} "
                f"on {appt.date}: {e}"
            )
            results.append(
                DraftResult(
                    appointment=appt,
                    recipient=recipient,
                    subject=subject,
                    body_preview=body_preview,
                    error=str(e),
                )
            )

    return results
