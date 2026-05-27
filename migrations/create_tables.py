"""
Create all database tables and seed Mother Hospital Thrissur data.

Run: python migrations/create_tables.py

This script:
  1. Creates all tables from SQLAlchemy models
  2. Seeds Mother Hospital Thrissur as the first tenant
  3. Creates a default admin user
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from passlib.context import CryptContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config.settings import settings
from src.db.models import (
    Base,
    BranchDayPolicy,
    ConsultationFee,
    DashboardUser,
    DayOfWeek,
    Department,
    DepartmentTiming,
    Doctor,
    DoctorAvailability,
    HospitalBranch,
    KeywordRule,
    Tenant,
    SupportedLanguage,
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


async def create_schema(engine) -> None:
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✓ Schema created")


async def seed_mother_hospital(engine) -> None:
    """
    Seed Mother Hospital Thrissur data.
    Source: motherhospitalthrissur.org

    Mother Hospital is one of Kerala's leading tertiary care hospitals
    in Thrissur, known for maternity, pediatrics, and general medicine.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    async_session = async_sessionmaker(engine, expire_on_commit=False)

    async with async_session() as session:
        # Check if already seeded
        from sqlalchemy import select
        existing = await session.execute(
            select(Tenant).where(Tenant.slug == "mother-hospital-thrissur")
        )
        if existing.scalar_one_or_none():
            print("✓ Mother Hospital already seeded")
            return

        # ── Tenant ────────────────────────────────────────────────────────────
        tenant = Tenant(
            slug="mother-hospital-thrissur",
            name="Mother Hospital Thrissur",
            is_active=True,
            transfer_number="0487-2442888",  # Main reception
            default_language=SupportedLanguage.MALAYALAM,
            greeting_text=(
                "നമസ്കാരം! Mother Hospital Thrissur-ലേക്ക് സ്വാഗതം. "
                "എന്ത് സഹായം ആണ് വേണ്ടത്?"
            ),
            fallback_text=(
                "ക്ഷമിക്കണം, ഇപ്പോൾ ഞാൻ ഉചിതമായ ഒരു ഉത്തരം നൽകാൻ കഴിയുന്നില്ല. "
                "0487-2442888 ൽ ബന്ധപ്പെടൂ."
            ),
            stt_language_code="ml-IN",
            tts_voice="anushka",
        )
        session.add(tenant)
        await session.flush()

        # ── Main Branch ───────────────────────────────────────────────────────
        branch = HospitalBranch(
            tenant_id=tenant.id,
            name="Mother Hospital — Main Campus, Thrissur",
            is_main_branch=True,
            address="Pullazhy, Thrissur - Palakkad National Highway",
            city="Thrissur",
            district="Thrissur",
            state="Kerala",
            pincode="680012",
            phone_primary="0487-2442888",
            phone_secondary="0487-2443999",
            phone_emergency="0487-2442000",
            whatsapp="+91-94470-42888",
            has_emergency=True,
            emergency_24x7=True,
            emergency_notes="24x7 Casualty, Trauma Care, NICU, PICU available",
            general_open_time=time(8, 0),
            general_close_time=time(20, 0),
        )
        session.add(branch)
        await session.flush()

        # ── Day policies ──────────────────────────────────────────────────────
        day_policies = [
            (DayOfWeek.MONDAY, True, time(8, 0), time(20, 0)),
            (DayOfWeek.TUESDAY, True, time(8, 0), time(20, 0)),
            (DayOfWeek.WEDNESDAY, True, time(8, 0), time(20, 0)),
            (DayOfWeek.THURSDAY, True, time(8, 0), time(20, 0)),
            (DayOfWeek.FRIDAY, True, time(8, 0), time(20, 0)),
            (DayOfWeek.SATURDAY, True, time(8, 0), time(18, 0)),
            (DayOfWeek.SUNDAY, True, time(9, 0), time(14, 0)),  # Limited OPD
        ]
        for day, is_open, open_t, close_t in day_policies:
            policy = BranchDayPolicy(
                branch_id=branch.id,
                day_of_week=day,
                is_open=is_open,
                open_time=open_t,
                close_time=close_t,
                notes="Emergency and admitted patient services 24x7" if day == DayOfWeek.SUNDAY else None,
            )
            session.add(policy)

        # ── Departments ───────────────────────────────────────────────────────
        departments_data = [
            {
                "name": "Obstetrics & Gynaecology",
                "normalized_name": "gynecology",
                "aliases": ["gynecology", "gynaecology", "obs", "obstetrics", "delivery",
                            "prasavam", "strirog", "maternity", "women"],
                "floor_info": "Block A, 2nd Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.TUESDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.THURSDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.SATURDAY, time(9, 0), time(13, 0)),
                ],
                "fee": 300.0,
            },
            {
                "name": "Paediatrics",
                "normalized_name": "pediatrics",
                "aliases": ["pediatrics", "paediatrics", "child", "kutta", "kuttinu",
                            "baby", "balan", "balacikitsa", "kids", "children"],
                "floor_info": "Block A, Ground Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.TUESDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.THURSDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.SATURDAY, time(9, 0), time(13, 0)),
                ],
                "fee": 250.0,
            },
            {
                "name": "General Medicine",
                "normalized_name": "general",
                "aliases": ["general", "gp", "general medicine", "general physician",
                            "samanya", "fever", "pani", "penam", "medicine"],
                "floor_info": "OPD Block, Ground Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(8, 0), time(18, 0)),
                    (DayOfWeek.TUESDAY, time(8, 0), time(18, 0)),
                    (DayOfWeek.WEDNESDAY, time(8, 0), time(18, 0)),
                    (DayOfWeek.THURSDAY, time(8, 0), time(18, 0)),
                    (DayOfWeek.FRIDAY, time(8, 0), time(18, 0)),
                    (DayOfWeek.SATURDAY, time(8, 0), time(14, 0)),
                    (DayOfWeek.SUNDAY, time(9, 0), time(13, 0)),
                ],
                "fee": 200.0,
            },
            {
                "name": "Orthopaedics",
                "normalized_name": "orthopedic",
                "aliases": ["orthopedic", "ortho", "bone", "ellu", "ellinu", "joint",
                            "spine", "fracture"],
                "floor_info": "Block B, 1st Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.TUESDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.THURSDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(16, 0)),
                ],
                "fee": 300.0,
            },
            {
                "name": "Cardiology",
                "normalized_name": "cardiology",
                "aliases": ["cardiology", "cardio", "heart", "hrudayam", "hridayam",
                            "hridayarogi", "cardiac"],
                "floor_info": "Block C, 2nd Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(9, 0), time(15, 0)),
                    (DayOfWeek.TUESDAY, time(9, 0), time(15, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(15, 0)),
                    (DayOfWeek.THURSDAY, time(9, 0), time(15, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(15, 0)),
                ],
                "fee": 400.0,
            },
            {
                "name": "ENT (Ear, Nose & Throat)",
                "normalized_name": "ent",
                "aliases": ["ent", "ear", "nose", "throat", "kaan", "mookk",
                            "ear nose throat", "otolaryngology"],
                "floor_info": "OPD Block, 1st Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(16, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(16, 0)),
                ],
                "fee": 250.0,
            },
            {
                "name": "Ophthalmology (Eye)",
                "normalized_name": "ophthalmology",
                "aliases": ["ophthalmology", "eye", "kannu", "kanninu", "ophthal",
                            "vision", "eye doctor", "nethram"],
                "floor_info": "Block D, Ground Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.TUESDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.THURSDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.SATURDAY, time(9, 0), time(13, 0)),
                ],
                "fee": 250.0,
            },
            {
                "name": "Dermatology (Skin)",
                "normalized_name": "dermatology",
                "aliases": ["dermatology", "skin", "charma", "charmam", "derma",
                            "skincare", "skin doctor"],
                "floor_info": "OPD Block, 1st Floor",
                "timings": [
                    (DayOfWeek.TUESDAY, time(10, 0), time(16, 0)),
                    (DayOfWeek.THURSDAY, time(10, 0), time(16, 0)),
                    (DayOfWeek.SATURDAY, time(10, 0), time(14, 0)),
                ],
                "fee": 250.0,
            },
            {
                "name": "Neurology",
                "normalized_name": "neurology",
                "aliases": ["neurology", "neuro", "brain", "thalach", "nerve",
                            "stroke", "epilepsy"],
                "floor_info": "Block C, 3rd Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(10, 0), time(15, 0)),
                    (DayOfWeek.WEDNESDAY, time(10, 0), time(15, 0)),
                    (DayOfWeek.FRIDAY, time(10, 0), time(15, 0)),
                ],
                "fee": 400.0,
            },
            {
                "name": "Dental",
                "normalized_name": "dentist",
                "aliases": ["dentist", "dental", "tooth", "teeth", "pallu", "pallinu",
                            "palldoctor", "orthodontic", "danthachikitsa", "dantham"],
                "floor_info": "OPD Block, Ground Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.TUESDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.THURSDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(17, 0)),
                    (DayOfWeek.SATURDAY, time(9, 0), time(14, 0)),
                ],
                "fee": 200.0,
            },
            {
                "name": "Emergency & Trauma Care",
                "normalized_name": "emergency",
                "aliases": ["emergency", "casualty", "accident", "urgent", "trauma",
                            "athyavasyam"],
                "floor_info": "Ground Floor, Emergency Block",
                "timings": [
                    (DayOfWeek.MONDAY, time(0, 0), time(23, 59)),
                    (DayOfWeek.TUESDAY, time(0, 0), time(23, 59)),
                    (DayOfWeek.WEDNESDAY, time(0, 0), time(23, 59)),
                    (DayOfWeek.THURSDAY, time(0, 0), time(23, 59)),
                    (DayOfWeek.FRIDAY, time(0, 0), time(23, 59)),
                    (DayOfWeek.SATURDAY, time(0, 0), time(23, 59)),
                    (DayOfWeek.SUNDAY, time(0, 0), time(23, 59)),
                ],
                "fee": 0.0,  # Emergency consultation
            },
            {
                "name": "Radiology & Imaging",
                "normalized_name": "radiology",
                "aliases": ["radiology", "xray", "x-ray", "scan", "mri", "ct",
                            "ct scan", "ultrasound", "usg"],
                "floor_info": "Block B, Ground Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(8, 0), time(20, 0)),
                    (DayOfWeek.TUESDAY, time(8, 0), time(20, 0)),
                    (DayOfWeek.WEDNESDAY, time(8, 0), time(20, 0)),
                    (DayOfWeek.THURSDAY, time(8, 0), time(20, 0)),
                    (DayOfWeek.FRIDAY, time(8, 0), time(20, 0)),
                    (DayOfWeek.SATURDAY, time(8, 0), time(17, 0)),
                ],
                "fee": 0.0,  # Varies by procedure
            },
            {
                "name": "Laboratory",
                "normalized_name": "lab",
                "aliases": ["lab", "laboratory", "blood test", "test", "pathology",
                            "parikshanam", "blood", "urine"],
                "floor_info": "Block B, Ground Floor",
                "timings": [
                    (DayOfWeek.MONDAY, time(7, 0), time(19, 0)),
                    (DayOfWeek.TUESDAY, time(7, 0), time(19, 0)),
                    (DayOfWeek.WEDNESDAY, time(7, 0), time(19, 0)),
                    (DayOfWeek.THURSDAY, time(7, 0), time(19, 0)),
                    (DayOfWeek.FRIDAY, time(7, 0), time(19, 0)),
                    (DayOfWeek.SATURDAY, time(7, 0), time(15, 0)),
                    (DayOfWeek.SUNDAY, time(8, 0), time(12, 0)),
                ],
                "fee": 0.0,  # Varies by test
            },
            {
                "name": "Pharmacy",
                "normalized_name": "pharmacy",
                "aliases": ["pharmacy", "medical", "medicine", "mrundu", "marundhu",
                            "drug", "chemist", "meds"],
                "floor_info": "Ground Floor, Main Entrance",
                "timings": [
                    (DayOfWeek.MONDAY, time(8, 0), time(21, 0)),
                    (DayOfWeek.TUESDAY, time(8, 0), time(21, 0)),
                    (DayOfWeek.WEDNESDAY, time(8, 0), time(21, 0)),
                    (DayOfWeek.THURSDAY, time(8, 0), time(21, 0)),
                    (DayOfWeek.FRIDAY, time(8, 0), time(21, 0)),
                    (DayOfWeek.SATURDAY, time(8, 0), time(20, 0)),
                    (DayOfWeek.SUNDAY, time(9, 0), time(18, 0)),
                ],
                "fee": 0.0,
            },
        ]

        dept_objects = {}
        for d_data in departments_data:
            dept = Department(
                branch_id=branch.id,
                name=d_data["name"],
                normalized_name=d_data["normalized_name"],
                aliases=d_data["aliases"],
                is_active=True,
                floor_info=d_data.get("floor_info"),
            )
            session.add(dept)
            await session.flush()
            dept_objects[d_data["normalized_name"]] = dept

            # Timings
            for day, open_t, close_t in d_data["timings"]:
                timing = DepartmentTiming(
                    department_id=dept.id,
                    day_of_week=day,
                    open_time=open_t,
                    close_time=close_t,
                    is_closed=False,
                )
                session.add(timing)

            # Consultation fee
            if d_data["fee"] > 0:
                fee = ConsultationFee(
                    branch_id=branch.id,
                    department_id=dept.id,
                    fee_type="consultation",
                    amount=d_data["fee"],
                    currency="INR",
                )
                session.add(fee)

        # ── Sample Doctors ────────────────────────────────────────────────────
        # Note: These are representative; actual doctors should be updated via dashboard
        doctors_data = [
            {
                "name": "Dr. Rema Devi",
                "normalized": "dr rema devi",
                "aliases": ["rema", "rema devi", "rema doctor"],
                "qualification": "MD, DGO",
                "specialization": "Obstetrics & Gynaecology",
                "dept": "gynecology",
                "availability": [
                    (DayOfWeek.MONDAY, time(9, 0), time(13, 0)),
                    (DayOfWeek.WEDNESDAY, time(9, 0), time(13, 0)),
                    (DayOfWeek.FRIDAY, time(9, 0), time(13, 0)),
                ],
                "fee": 350.0,
            },
            {
                "name": "Dr. Suresh Kumar",
                "normalized": "dr suresh kumar",
                "aliases": ["suresh", "suresh kumar", "suresh doctor"],
                "qualification": "MD Paediatrics",
                "specialization": "Paediatrics & Neonatology",
                "dept": "pediatrics",
                "availability": [
                    (DayOfWeek.MONDAY, time(10, 0), time(16, 0)),
                    (DayOfWeek.TUESDAY, time(10, 0), time(16, 0)),
                    (DayOfWeek.THURSDAY, time(10, 0), time(16, 0)),
                ],
                "fee": 300.0,
            },
            {
                "name": "Dr. Anitha Joseph",
                "normalized": "dr anitha joseph",
                "aliases": ["anitha", "anitha joseph", "anitha doctor"],
                "qualification": "MBBS, MD",
                "specialization": "General Medicine",
                "dept": "general",
                "availability": [
                    (DayOfWeek.MONDAY, time(8, 0), time(14, 0)),
                    (DayOfWeek.TUESDAY, time(8, 0), time(14, 0)),
                    (DayOfWeek.WEDNESDAY, time(8, 0), time(14, 0)),
                    (DayOfWeek.THURSDAY, time(8, 0), time(14, 0)),
                    (DayOfWeek.FRIDAY, time(8, 0), time(14, 0)),
                ],
                "fee": 200.0,
            },
            {
                "name": "Dr. Rajesh Nair",
                "normalized": "dr rajesh nair",
                "aliases": ["rajesh", "rajesh nair", "nair doctor"],
                "qualification": "MS Orthopaedics",
                "specialization": "Orthopaedics & Joint Replacement",
                "dept": "orthopedic",
                "availability": [
                    (DayOfWeek.TUESDAY, time(10, 0), time(16, 0)),
                    (DayOfWeek.THURSDAY, time(10, 0), time(16, 0)),
                    (DayOfWeek.SATURDAY, time(10, 0), time(13, 0)),
                ],
                "fee": 350.0,
            },
        ]

        for doc_data in doctors_data:
            dept_obj = dept_objects.get(doc_data["dept"])
            doctor = Doctor(
                branch_id=branch.id,
                department_id=dept_obj.id if dept_obj else None,
                name=doc_data["name"],
                normalized_name=doc_data["normalized"],
                aliases=doc_data["aliases"],
                qualification=doc_data["qualification"],
                specialization=doc_data["specialization"],
                is_active=True,
                is_visiting=False,
            )
            session.add(doctor)
            await session.flush()

            for day, start_t, end_t in doc_data["availability"]:
                avail = DoctorAvailability(
                    doctor_id=doctor.id,
                    day_of_week=day,
                    start_time=start_t,
                    end_time=end_t,
                    is_available=True,
                )
                session.add(avail)

            fee = ConsultationFee(
                branch_id=branch.id,
                doctor_id=doctor.id,
                fee_type="consultation",
                amount=doc_data["fee"],
                currency="INR",
            )
            session.add(fee)

        # ── Keyword rules (Thrissur dialect specific) ─────────────────────────
        keyword_rules = [
            ("amme", "department_exists", "gynecology", 1.5),
            ("prasavam", "department_exists", "gynecology", 2.0),
            ("delivery", "department_exists", "gynecology", 1.8),
            ("kutta doctor", "department_exists", "pediatrics", 2.0),
            ("kutta", "department_exists", "pediatrics", 1.5),
            ("baby", "department_exists", "pediatrics", 1.5),
            ("pallu", "department_exists", "dentist", 2.0),
            ("pallinu", "department_exists", "dentist", 2.0),
            ("ellu", "department_exists", "orthopedic", 1.8),
            ("hridayam", "emergency_availability", "emergency", 1.5),
            ("ambulance", "emergency_availability", "emergency", 2.5),
            ("ivide", "location_query", None, 1.5),
            ("evideyanu", "location_query", None, 2.0),
        ]
        for kw, intent, entity, weight in keyword_rules:
            rule = KeywordRule(
                tenant_id=tenant.id,
                keyword=kw,
                maps_to_intent=intent,
                maps_to_entity=entity,
                weight=weight,
                is_active=True,
            )
            session.add(rule)

        # ── Admin user ────────────────────────────────────────────────────────
        admin = DashboardUser(
            tenant_id=tenant.id,
            email="admin@motherhospital.in",
            hashed_password=pwd_context.hash("MotherHospital@2024"),
            full_name="Hospital Admin",
            is_active=True,
            is_superadmin=False,
        )
        session.add(admin)

        await session.commit()
        print(f"✓ Mother Hospital Thrissur seeded (tenant: {tenant.id})")
        print(f"  → {len(departments_data)} departments")
        print(f"  → {len(doctors_data)} sample doctors")
        print(f"  → Admin login: admin@motherhospital.in / MotherHospital@2024")


async def main():
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    await create_schema(engine)
    await seed_mother_hospital(engine)
    await engine.dispose()
    print("\n✓ Database ready!")


if __name__ == "__main__":
    asyncio.run(main())
