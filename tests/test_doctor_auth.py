"""Unit tests for the doctor-login auth foundation (no DB required)."""
import asyncio

import pytest
from fastapi import HTTPException
from jose import jwt

from additions.deps import JWT_ALGORITHM, JWT_SECRET
from additions.routes import doctor_api
from additions.routes.users_api import VALID_ROLES, _issue_token


def test_valid_roles_includes_doctor():
    assert "doctor" in VALID_ROLES


def test_token_carries_doctor_claims():
    tok = _issue_token("dr@x.com", "doctor", doctor_id="D1", hospital_id="H1")
    p = jwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert p["role"] == "doctor"
    assert p["doctor_id"] == "D1"
    assert p["hospital_id"] == "H1"
    assert p["sub"] == "dr@x.com"


def test_token_omits_doctor_claims_for_non_doctor():
    tok = _issue_token("admin@x.com", "viewer")
    p = jwt.decode(tok, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert "doctor_id" not in p
    assert "hospital_id" not in p


def test_doctor_ctx_accepts_doctor_token():
    did, hid = asyncio.run(
        doctor_api._doctor_ctx({"role": "doctor", "doctor_id": "D1", "hospital_id": "H1"})
    )
    assert did == "D1"
    assert hid == "H1"


def test_doctor_ctx_rejects_non_doctor():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(doctor_api._doctor_ctx({"role": "viewer"}))
    assert exc.value.status_code == 403


def test_doctor_ctx_rejects_doctor_without_id():
    with pytest.raises(HTTPException) as exc:
        asyncio.run(doctor_api._doctor_ctx({"role": "doctor"}))
    assert exc.value.status_code == 403
