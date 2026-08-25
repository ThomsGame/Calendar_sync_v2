"""Tests for event classification and filtering."""

import json
from pathlib import Path

from calendar_sync.filters.business import build_business_appointments, classify_event
from calendar_sync.models.appointment import (
    Appointment,
    AppointmentSource,
    AppointmentType,
    EventColor,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> list[dict]:
    path = FIXTURE_DIR / name
    if not path.exists():
        return []
    return json.loads(path.read_text())


def test_classify_blue_entree():
    apt = Appointment(
        text="11:50 - 13:00 OS n°2356727 EDL entrée",
        style="background-color: rgb(18, 17, 171);",
    )
    meta = classify_event(apt)
    assert meta.color == EventColor.BLEU
    assert meta.type == AppointmentType.ENTREE
    assert not meta.is_trajet


def test_classify_green_sortie():
    apt = Appointment(
        text="14:00 - 15:20 OS n°2336555 EDL sortie",
        style="background-color: rgb(17, 138, 123);",
    )
    meta = classify_event(apt)
    assert meta.color == EventColor.VERT
    assert meta.type == AppointmentType.SORTIE


def test_classify_purple_odm():
    apt = Appointment(
        text="Sortie ODM 11362860 10/08/2026 10:00-10:50",
        style="background-color: rgb(156, 39, 176);",
        source=AppointmentSource.CONSTATIMMO,
    )
    meta = classify_event(apt)
    assert meta.color == EventColor.VIOLET
    assert meta.type == AppointmentType.SORTIE


def test_classify_trajet():
    apt = Appointment(
        text="14:00 - 15:20 Trajet OS n°2336555",
        style="background-color: rgba(18, 17, 171, 0.6);",
    )
    meta = classify_event(apt)
    assert meta.is_trajet


def test_build_business_from_filtered_fixture():
    """Test filtering on the real appointments.filtered.json fixture."""
    raw = _load_fixture("appointments.filtered.json")
    if not raw:
        return  # Skip if fixture not available

    events = []
    for item in raw:
        apt = Appointment(
            text=item.get("text", ""),
            description=item.get("description"),
            date=item.get("date"),
            start_time=item.get("startTime"),
            end_time=item.get("endTime"),
            time_raw=item.get("timeRaw"),
            style=item.get("style"),
            source=AppointmentSource(item.get("source", "snexi")),
            odm_number=item.get("odmNumber"),
            address=item.get("address"),
            property_type=item.get("propertyType"),
            keys_status=item.get("keysStatus"),
            detail_url=item.get("detailUrl"),
        )
        events.append(apt)

    business, stats = build_business_appointments(events)

    # The fixture already has only business events (pre-filtered)
    assert stats["kept"] == len(raw)
    assert stats["skippedRed"] == 0
    assert stats["skippedTrajet"] == 0


def test_build_business_constatimmo_fixture():
    """Test filtering on constatimmo.appointments.json fixture."""
    raw = _load_fixture("constatimmo.appointments.json")
    if not raw:
        return

    events = []
    for item in raw:
        apt = Appointment(
            text=item.get("text", ""),
            description=item.get("description"),
            source=AppointmentSource.CONSTATIMMO,
            odm_number=item.get("odmNumber"),
            address=item.get("address"),
            property_type=item.get("propertyType"),
            keys_status=item.get("keysStatus"),
            detail_url=item.get("detailUrl"),
        )
        events.append(apt)

    business, stats = build_business_appointments(events)

    # All constatimmo events in the fixture are ODM (purple)
    assert stats["kept"] == len(raw)
    assert stats["sourceCounts"]["constatimmo"] == len(raw)
