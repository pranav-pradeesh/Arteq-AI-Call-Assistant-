"""
Database models for Arteq Hospital Voice Agent.

Schema design:
  - tenants → hospital_branches → departments/doctors/fees/timings
  - Each tenant is fully isolated — no cross-tenant data leakage
  - Audit log is append-only for every data change
  - All indexes placed on lookup-critical columns
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from enum import Enum as PyEnum
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Interval,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.sql import func


class Base(AsyncAttrs, DeclarativeBase):
    """Shared base with created_at/updated_at timestamps."""

    __allow_unmapped__ = True

    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────


class DayOfWeek(PyEnum):
    MONDAY = "monday"
    TUESDAY = "tuesday"
    WEDNESDAY = "wednesday"
    THURSDAY = "thursday"
    FRIDAY = "friday"
    SATURDAY = "saturday"
    SUNDAY = "sunday"


class CallOutcome(PyEnum):
    ANSWERED = "answered"
    CLARIFIED = "clarified"
    TRANSFERRED = "transferred"
    DROPPED = "dropped"
    UNKNOWN = "unknown"


class SupportedLanguage(PyEnum):
    MALAYALAM = "ml"
    ENGLISH = "en"
    HINDI = "hi"
    TAMIL = "ta"


# ─────────────────────────────────────────────────────────────────────────────
# Tenants
# ─────────────────────────────────────────────────────────────────────────────


class Tenant(Base):
    """
    Top-level tenant (hospital group or standalone hospital).
    All runtime data is scoped under a tenant_id.
    """

    __tablename__ = "tenants"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    slug = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(256), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    # Call transfer number when human agent is needed
    transfer_number = Column(String(32), nullable=True)

    # Default response language
    default_language = Column(
        Enum(SupportedLanguage),
        default=SupportedLanguage.MALAYALAM,
        nullable=False,
    )

    # Custom greeting text (optional, falls back to generic)
    greeting_text = Column(Text, nullable=True)
    fallback_text = Column(Text, nullable=True)  # when all else fails

    # STT / TTS overrides (None → use global env defaults)
    stt_language_code = Column(String(16), nullable=True)  # e.g. "ml-IN"
    tts_voice = Column(String(64), nullable=True)

    # Relationships
    branches = relationship("HospitalBranch", back_populates="tenant", lazy="selectin")
    keyword_rules = relationship("KeywordRule", back_populates="tenant")

    def __repr__(self) -> str:
        return f"<Tenant {self.slug}>"


# ─────────────────────────────────────────────────────────────────────────────
# Hospital Branches
# ─────────────────────────────────────────────────────────────────────────────


class HospitalBranch(Base):
    """
    A physical branch of a hospital.
    A tenant may have one or multiple branches.
    """

    __tablename__ = "hospital_branches"
    __table_args__ = (Index("ix_branch_tenant", "tenant_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False, index=True
    )
    name = Column(String(256), nullable=False)
    is_main_branch = Column(Boolean, default=False, nullable=False)

    # Location
    address = Column(Text, nullable=True)
    city = Column(String(128), nullable=True)
    district = Column(String(128), nullable=True)
    state = Column(String(64), default="Kerala", nullable=False)
    pincode = Column(String(10), nullable=True)
    google_maps_url = Column(Text, nullable=True)

    # Contact
    phone_primary = Column(String(32), nullable=True)
    phone_secondary = Column(String(32), nullable=True)
    phone_emergency = Column(String(32), nullable=True)
    whatsapp = Column(String(32), nullable=True)

    # Emergency
    has_emergency = Column(Boolean, default=False, nullable=False)
    emergency_24x7 = Column(Boolean, default=False, nullable=False)
    emergency_notes = Column(Text, nullable=True)

    # General timings
    general_open_time = Column(Time, nullable=True)
    general_close_time = Column(Time, nullable=True)

    # Relationships
    tenant = relationship("Tenant", back_populates="branches")
    departments = relationship("Department", back_populates="branch")
    doctors = relationship("Doctor", back_populates="branch")
    holiday_overrides = relationship("HolidayOverride", back_populates="branch")
    day_policies = relationship("BranchDayPolicy", back_populates="branch")

    def __repr__(self) -> str:
        return f"<Branch {self.name}>"


# ─────────────────────────────────────────────────────────────────────────────
# Departments
# ─────────────────────────────────────────────────────────────────────────────


class Department(Base):
    """
    A medical department within a branch.
    Lookup is by normalized name or aliases.
    """

    __tablename__ = "departments"
    __table_args__ = (
        Index("ix_dept_branch", "branch_id"),
        Index("ix_dept_normalized", "normalized_name"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    branch_id = Column(
        UUID(as_uuid=True), ForeignKey("hospital_branches.id"), nullable=False
    )
    name = Column(String(128), nullable=False)
    normalized_name = Column(String(64), nullable=False)  # lowercase slug

    # Aliases for dialect-tolerant lookup (stored as array)
    # e.g. ["pallu", "tooth", "dental", "dentist", "palldoctor"]
    aliases = Column(ARRAY(Text), nullable=False, server_default="{}")

    is_active = Column(Boolean, default=True, nullable=False)
    floor_info = Column(String(64), nullable=True)
    room_number = Column(String(32), nullable=True)
    notes = Column(Text, nullable=True)

    # Relationships
    branch = relationship("HospitalBranch", back_populates="departments")
    doctors = relationship("Doctor", back_populates="department")
    timings = relationship("DepartmentTiming", back_populates="department")
    fees = relationship("ConsultationFee", back_populates="department")

    def __repr__(self) -> str:
        return f"<Department {self.name}>"


# ─────────────────────────────────────────────────────────────────────────────
# Department Timings
# ─────────────────────────────────────────────────────────────────────────────


class DepartmentTiming(Base):
    """
    OP timing for a department on a specific day.
    Multiple rows per department (one per operating day).
    """

    __tablename__ = "department_timings"
    __table_args__ = (Index("ix_timing_dept_day", "department_id", "day_of_week"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    department_id = Column(
        UUID(as_uuid=True), ForeignKey("departments.id"), nullable=False
    )
    day_of_week = Column(Enum(DayOfWeek), nullable=False)
    open_time = Column(Time, nullable=False)
    close_time = Column(Time, nullable=False)
    is_closed = Column(Boolean, default=False, nullable=False)
    session_label = Column(String(64), nullable=True)  # e.g. "Morning OP", "Evening"

    department = relationship("Department", back_populates="timings")


# ─────────────────────────────────────────────────────────────────────────────
# Branch Day Policies (open/closed per day)
# ─────────────────────────────────────────────────────────────────────────────


class BranchDayPolicy(Base):
    """
    Per-day open/close policy for a branch.
    Overrides the default general timings.
    """

    __tablename__ = "branch_day_policies"
    __table_args__ = (
        UniqueConstraint("branch_id", "day_of_week"),
        Index("ix_policy_branch_day", "branch_id", "day_of_week"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    branch_id = Column(
        UUID(as_uuid=True), ForeignKey("hospital_branches.id"), nullable=False
    )
    day_of_week = Column(Enum(DayOfWeek), nullable=False)
    is_open = Column(Boolean, nullable=False)
    open_time = Column(Time, nullable=True)
    close_time = Column(Time, nullable=True)
    notes = Column(String(256), nullable=True)  # e.g. "Emergency only"

    branch = relationship("HospitalBranch", back_populates="day_policies")


# ─────────────────────────────────────────────────────────────────────────────
# Doctors
# ─────────────────────────────────────────────────────────────────────────────


class Doctor(Base):
    """
    A doctor at a branch. May belong to a department.
    Availability is stored in DoctorAvailability.
    """

    __tablename__ = "doctors"
    __table_args__ = (
        Index("ix_doctor_branch", "branch_id"),
        Index("ix_doctor_dept", "department_id"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    branch_id = Column(
        UUID(as_uuid=True), ForeignKey("hospital_branches.id"), nullable=False
    )
    department_id = Column(
        UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True
    )
    name = Column(String(256), nullable=False)
    normalized_name = Column(String(128), nullable=False)  # for matching
    qualification = Column(String(256), nullable=True)
    specialization = Column(String(256), nullable=True)

    # Aliases for fuzzy matching
    aliases = Column(ARRAY(Text), nullable=False, server_default="{}")

    is_active = Column(Boolean, default=True, nullable=False)
    is_visiting = Column(Boolean, default=False, nullable=False)

    # Relationships
    branch = relationship("HospitalBranch", back_populates="doctors")
    department = relationship("Department", back_populates="doctors")
    availability = relationship("DoctorAvailability", back_populates="doctor")
    fees = relationship("ConsultationFee", back_populates="doctor")

    def __repr__(self) -> str:
        return f"<Doctor {self.name}>"


# ─────────────────────────────────────────────────────────────────────────────
# Doctor Availability
# ─────────────────────────────────────────────────────────────────────────────


class DoctorAvailability(Base):
    """
    When a doctor is available at the branch (per weekday).
    Multiple sessions per day possible.
    """

    __tablename__ = "doctor_availability"
    __table_args__ = (Index("ix_avail_doctor_day", "doctor_id", "day_of_week"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doctor_id = Column(
        UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False
    )
    day_of_week = Column(Enum(DayOfWeek), nullable=False)
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)
    is_available = Column(Boolean, default=True, nullable=False)
    slot_notes = Column(String(256), nullable=True)

    doctor = relationship("Doctor", back_populates="availability")


# ─────────────────────────────────────────────────────────────────────────────
# Consultation Fees
# ─────────────────────────────────────────────────────────────────────────────


class ConsultationFee(Base):
    """
    Fee for a doctor or department consultation.
    doctor_id XOR department_id (not both).
    """

    __tablename__ = "consultation_fees"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    branch_id = Column(
        UUID(as_uuid=True), ForeignKey("hospital_branches.id"), nullable=False, index=True
    )
    doctor_id = Column(
        UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=True, index=True
    )
    department_id = Column(
        UUID(as_uuid=True), ForeignKey("departments.id"), nullable=True, index=True
    )

    fee_type = Column(String(64), default="consultation")  # consultation|review|procedure
    amount = Column(Float, nullable=False)
    currency = Column(String(8), default="INR", nullable=False)
    notes = Column(String(256), nullable=True)

    doctor = relationship("Doctor", back_populates="fees")
    department = relationship("Department", back_populates="fees")


# ─────────────────────────────────────────────────────────────────────────────
# Holiday Overrides
# ─────────────────────────────────────────────────────────────────────────────


class HolidayOverride(Base):
    """
    Date-specific closure or modified timing.
    Takes precedence over regular day policies.
    """

    __tablename__ = "holiday_overrides"
    __table_args__ = (Index("ix_holiday_branch_date", "branch_id", "override_date"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    branch_id = Column(
        UUID(as_uuid=True), ForeignKey("hospital_branches.id"), nullable=False
    )
    override_date = Column(DateTime(timezone=True), nullable=False)
    is_closed = Column(Boolean, default=True, nullable=False)
    reason = Column(String(256), nullable=True)  # e.g. "Onam", "State Holiday"
    emergency_only = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    branch = relationship("HospitalBranch", back_populates="holiday_overrides")


# ─────────────────────────────────────────────────────────────────────────────
# Keyword Rules (per tenant — dialect tuning)
# ─────────────────────────────────────────────────────────────────────────────


class KeywordRule(Base):
    """
    Tenant-level keyword → intent mapping overrides.
    Allows hospital admins to add local dialect keywords
    without code changes.
    """

    __tablename__ = "keyword_rules"
    __table_args__ = (Index("ix_kw_tenant", "tenant_id"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False
    )
    keyword = Column(String(128), nullable=False)
    maps_to_intent = Column(String(64), nullable=False)  # intent enum string
    maps_to_entity = Column(String(128), nullable=True)  # e.g. "dentist"
    weight = Column(Float, default=1.0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

    tenant = relationship("Tenant", back_populates="keyword_rules")


# ─────────────────────────────────────────────────────────────────────────────
# Response Templates
# ─────────────────────────────────────────────────────────────────────────────


class ResponseTemplate(Base):
    """
    Pre-composed response text for common cases.
    Stored per tenant in the configured language.
    Falls back to system defaults when absent.
    """

    __tablename__ = "response_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "intent", "language"),
        Index("ix_tmpl_tenant_intent", "tenant_id", "intent"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True  # null = global
    )
    intent = Column(String(64), nullable=False)
    language = Column(String(8), default="ml", nullable=False)
    template_text = Column(Text, nullable=False)
    # Template variables: {doctor_name}, {department}, {timings}, {fee}

    def __repr__(self) -> str:
        return f"<Template {self.intent} [{self.language}]>"


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Users
# ─────────────────────────────────────────────────────────────────────────────


class DashboardUser(Base):
    """
    Hospital staff user who can update hospital data.
    Scoped to their tenant (or super-admin with no tenant).
    """

    __tablename__ = "dashboard_users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True  # null = super-admin
    )
    email = Column(String(256), unique=True, nullable=False)
    hashed_password = Column(String(256), nullable=False)
    full_name = Column(String(256), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    is_superadmin = Column(Boolean, default=False, nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
# Call Audit Logs
# ─────────────────────────────────────────────────────────────────────────────


class CallLog(Base):
    """
    Immutable call audit record.
    Written asynchronously — must NOT block the call path.
    One row per completed call.
    """

    __tablename__ = "call_logs"
    __table_args__ = (
        Index("ix_log_tenant", "tenant_id"),
        Index("ix_log_call_id", "call_id"),
        Index("ix_log_created", "created_at"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    call_id = Column(String(128), nullable=False, unique=True)
    tenant_id = Column(
        UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True
    )
    branch_id = Column(
        UUID(as_uuid=True), ForeignKey("hospital_branches.id"), nullable=True
    )

    caller_number = Column(String(32), nullable=True)
    call_start = Column(DateTime(timezone=True), nullable=False)
    call_end = Column(DateTime(timezone=True), nullable=True)
    duration_ms = Column(Integer, nullable=True)

    # Intent / outcome summary
    detected_intent = Column(String(64), nullable=True)
    intent_confidence = Column(Float, nullable=True)
    outcome = Column(Enum(CallOutcome), nullable=True)
    clarification_count = Column(Integer, default=0)
    transferred_to_human = Column(Boolean, default=False)

    # Latency breakdown (ms)
    stt_latency_ms = Column(Integer, nullable=True)
    intent_latency_ms = Column(Integer, nullable=True)
    knowledge_latency_ms = Column(Integer, nullable=True)
    tts_latency_ms = Column(Integer, nullable=True)
    total_latency_ms = Column(Integer, nullable=True)

    # Debug / trace data (not shown to end users)
    transcript_fragments = Column(JSONB, nullable=True)
    entities_extracted = Column(JSONB, nullable=True)
    errors_encountered = Column(JSONB, nullable=True)

    # STT diagnostics
    stt_provider = Column(String(64), nullable=True)
    stt_fallback_used = Column(Boolean, default=False)
