"""
Unit tests for the outbound-trial appointment import helpers.

These cover the pure parsing/validation logic in
``dashboard.routes.admin_api`` — phone normalization, datetime parsing, and
CSV column-alias mapping — with no DB or network dependency.
"""
from __future__ import annotations

from datetime import datetime

from dashboard.routes.admin_api import (
    _normalize_phone,
    _parse_import_datetime,
    _parse_import_file,
)


def test_normalize_phone_accepts_indian_formats():
    assert _normalize_phone("9876543210") == "+919876543210"
    assert _normalize_phone("+91 98765 43210") == "+919876543210"
    assert _normalize_phone("919876543210") == "+919876543210"
    assert _normalize_phone("098765-43210") == "+919876543210"


def test_normalize_phone_rejects_invalid():
    assert _normalize_phone("12345") is None
    assert _normalize_phone("1234567890") is None      # must start 6-9
    assert _normalize_phone("") is None
    assert _normalize_phone(None) is None


def test_parse_import_datetime_formats():
    for s in (
        "2026-06-23 14:30",
        "23/06/2026 14:30",
        "23/06/2026 02:30 PM",
        "2026-06-23T14:30",
        "23 Jun 2026 14:30",
    ):
        dt = _parse_import_datetime(s)
        assert dt is not None and dt.tzinfo is not None, s

    assert _parse_import_datetime("not a date") is None
    assert _parse_import_datetime("") is None
    assert _parse_import_datetime(datetime(2026, 6, 23, 14, 30)).tzinfo is not None


def test_parse_csv_with_aliases_and_skips_blanks():
    data = b"name,phone,datetime\nAsha,9876543210,2026-06-23 14:30\nBob,98765,bad\n,,\n"
    rows = _parse_import_file("list.csv", data)
    assert len(rows) == 2                       # the blank row is dropped
    assert rows[0]["patient_name"] == "Asha"
    assert rows[0]["row"] == 2                   # 1-based incl. header
    assert rows[1]["row"] == 3


def test_parse_csv_combines_date_and_time_columns():
    data = b"patient,mobile,date,time\nCara,9000000001,2026-06-24,09:15\n"
    rows = _parse_import_file("x.csv", data)
    assert rows[0]["datetime"].strip() == "2026-06-24 09:15"


def test_parse_unsupported_extension_raises():
    import pytest
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        _parse_import_file("report.pdf", b"whatever")
    assert exc.value.status_code == 400
