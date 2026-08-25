"""Appointment data models for both Snexi and Constatimmo platforms."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EventColor(str, Enum):
    BLEU = "bleu"
    VERT = "vert"
    ROUGE = "rouge"
    VIOLET = "violet"
    AUTRE = "autre"


class AppointmentType(str, Enum):
    ENTREE = "entree"
    SORTIE = "sortie"
    ODM = "odm"
    INDISPONIBILITE = "indisponibilite"
    TRAJET = "trajet"
    AUTRE = "autre"


class AppointmentSource(str, Enum):
    SNEXI = "snexi"
    CONSTATIMMO = "constatimmo"


class EventMeta(BaseModel):
    """Classification metadata for an event."""

    color: EventColor = EventColor.AUTRE
    type: AppointmentType = AppointmentType.AUTRE
    is_trajet: bool = False


class Appointment(BaseModel):
    """Unified appointment model for both platforms."""

    # Core fields (from calendar extraction)
    text: str = ""
    description: Optional[str] = None
    date: Optional[str] = None  # ISO date YYYY-MM-DD
    start_time: Optional[str] = None  # HH:MM
    end_time: Optional[str] = None  # HH:MM
    time_raw: Optional[str] = None  # Raw time string from page

    # Source tracking
    source: AppointmentSource = AppointmentSource.SNEXI
    page_url: Optional[str] = None

    # CSS / display (for classification)
    css_class: Optional[str] = None
    style: Optional[str] = None
    computed_bg: Optional[str] = None

    # Snexi-specific
    left_px: Optional[float] = None
    week_label: Optional[str] = None

    # Constatimmo-specific
    width: Optional[int] = None
    height: Optional[int] = None
    keep: Optional[bool] = None
    property_type: Optional[str] = None
    keys_status: Optional[str] = None
    door_info: Optional[str] = None
    cave_info: Optional[str] = None
    parking_info: Optional[str] = None

    # Reference numbers
    os_number: Optional[str] = None  # Snexi OS number
    odm_number: Optional[str] = None  # Constatimmo ODM number

    # Detail enrichment (from detail pages)
    address: Optional[str] = None
    owner: Optional[str] = None
    manager: Optional[str] = None
    tenant: Optional[str] = None
    tenant_mobile: Optional[str] = None
    tenant_phone: Optional[str] = None  # Constatimmo only
    comment: Optional[str] = None
    key_pickup_place: Optional[str] = None
    key_drop_place: Optional[str] = None
    floor: Optional[str] = None
    door: Optional[str] = None
    digicode: Optional[str] = None
    building: Optional[str] = None
    detail_url: Optional[str] = None

    # Classification (set by classify_event)
    meta: Optional[EventMeta] = None

    def to_cal_dict(self) -> dict:
        """Export to dict suitable for JSON serialization."""
        return self.model_dump(exclude_none=True)
