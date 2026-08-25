"""Tests for utility helper functions."""

from calendar_sync.utils.helpers import (
    add_days,
    compact_text,
    extract_odm_number,
    extract_os_number,
    parse_french_month,
    parse_time_parts,
    parse_week_start_from_label,
    strip_accents_lower,
    to_iso_date,
    week_monday_iso,
)


def test_compact_text():
    assert compact_text("  hello   world  ") == "hello world"
    assert compact_text("") == ""
    assert compact_text(None) == ""


def test_strip_accents_lower():
    assert strip_accents_lower("ÉTAGE") == "etage"
    assert strip_accents_lower("Propriétaire") == "proprietaire"
    assert strip_accents_lower("Bâtiment") == "batiment"


def test_parse_french_month():
    assert parse_french_month("aout") == 8
    assert parse_french_month("août") == 8
    assert parse_french_month("janvier") == 1
    assert parse_french_month("septembre") == 9
    assert parse_french_month("invalid") is None


def test_to_iso_date():
    assert to_iso_date(2026, 8, 3) == "2026-08-03"
    assert to_iso_date(2026, 12, 25) == "2026-12-25"


def test_parse_week_start_from_label():
    assert parse_week_start_from_label("3 - 9 aout 2026") == "2026-08-03"
    assert parse_week_start_from_label("10 - 16 août 2026") == "2026-08-10"
    assert parse_week_start_from_label("invalid") is None


def test_add_days():
    assert add_days("2026-08-03", 1) == "2026-08-04"
    assert add_days("2026-08-31", 1) == "2026-09-01"
    assert add_days(None, 1) is None


def test_week_monday_iso():
    result = week_monday_iso(0)
    assert len(result) == 10
    # Should be a Monday
    from datetime import datetime

    dt = datetime.strptime(result, "%Y-%m-%d")
    assert dt.weekday() == 0  # Monday


def test_parse_time_parts():
    assert parse_time_parts("11:50 - 13:00") == ("11:50", "13:00")
    assert parse_time_parts("08:30") == ("08:30", None)
    assert parse_time_parts("no time here") == (None, None)


def test_extract_os_number():
    assert extract_os_number("11:50 - 13:00 OS n°2356727 EDL entrée") == "2356727"
    assert extract_os_number("OS 2336555 sortie") == "2336555"
    assert extract_os_number("no os here") == ""


def test_extract_odm_number():
    assert extract_odm_number("ODM: 11362860 (T3)") == "11362860"
    assert extract_odm_number("odmRedirect=11380798") == "11380798"
    assert extract_odm_number("no odm here") == ""
