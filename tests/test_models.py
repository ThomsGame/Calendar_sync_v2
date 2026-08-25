"""Tests for Appointment model."""

from calendar_sync.models.appointment import (
    Appointment,
    AppointmentSource,
    AppointmentType,
    EventColor,
    EventMeta,
)


def test_appointment_defaults():
    apt = Appointment(text="test")
    assert apt.text == "test"
    assert apt.source == AppointmentSource.SNEXI
    assert apt.date is None


def test_appointment_from_dict():
    data = {
        "text": "Sortie ODM 11362860 10/08/2026 10:00-10:50",
        "description": "ODM: 11362860 (T3) | Type de bien: T3",
        "date": "2026-08-10",
        "start_time": "10:00",
        "end_time": "10:50",
        "source": "constatimmo",
        "odm_number": "11362860",
        "address": "RESIDENCE LE COEURVILLE, 38 RUE DE LA HALTE 95120 ERMONT",
        "property_type": "T3",
        "keys_status": "A rendre",
    }
    apt = Appointment(**data)
    assert apt.odm_number == "11362860"
    assert apt.source == AppointmentSource.CONSTATIMMO
    assert apt.address == "RESIDENCE LE COEURVILLE, 38 RUE DE LA HALTE 95120 ERMONT"


def test_event_meta():
    meta = EventMeta(color=EventColor.BLEU, type=AppointmentType.ENTREE, is_trajet=False)
    assert meta.color == EventColor.BLEU
    assert meta.type == AppointmentType.ENTREE
    assert not meta.is_trajet


def test_to_cal_dict():
    apt = Appointment(
        text="test",
        date="2026-08-03",
        source=AppointmentSource.SNEXI,
        meta=EventMeta(color=EventColor.BLEU, type=AppointmentType.ENTREE),
    )
    d = apt.to_cal_dict()
    assert d["text"] == "test"
    assert d["date"] == "2026-08-03"
    assert d["meta"]["color"] == "bleu"
