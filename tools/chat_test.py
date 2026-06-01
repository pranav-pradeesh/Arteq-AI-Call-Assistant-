"""
Interactive text-based chat with the Arya brain (no audio layer).
Load .env, build a mock hospital context, and run a conversation loop.

Usage:
    cd /home/user/Arteq-AI-Call-Assistant-
    python tools/chat_test.py
"""
from __future__ import annotations

import asyncio
import os
import sys

# Load .env before importing anything from src/
from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=False)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.ai.groq_brain import GroqBrain
from src.db.queries import (
    HospitalContext, DeptInfo, DoctorInfo, SlotInfo, BillingRow as BillingItem,
    EmergencyContact, FaqRow as FAQItem,
)


def _mock_hospital() -> HospitalContext:
    return HospitalContext(
        hospital_id="00000000-0000-0000-0000-000000000001",
        name="Kairali Multi-Speciality Hospital",
        name_ml="കൈരളി മൾട്ടി-സ്പെഷ്യാലിറ്റി ആശുപത്രി",
        address="MG Road, Ernakulam, Kochi 682011",
        phone="04844267012",
        hours={
            "mon": ["08:00", "20:00"], "tue": ["08:00", "20:00"],
            "wed": ["08:00", "20:00"], "thu": ["08:00", "20:00"],
            "fri": ["08:00", "20:00"], "sat": ["08:00", "18:00"],
            "sun": ["09:00", "14:00"],
        },
        departments=[
            DeptInfo(id="d1", name="OPD", name_ml="ഒ.പി.ഡി", floor="1", location_hint="Ground floor, main entrance", phone_ext="101"),
            DeptInfo(id="d2", name="Emergency / Casualty", name_ml="അടിയന്തിര വിഭാഗം", floor="G", location_hint="24×7, separate entrance", phone_ext="100"),
            DeptInfo(id="d3", name="Cardiology", name_ml="ഹൃദ്രോഗ വിഭാഗം", floor="3", location_hint="Block B, 3rd floor", phone_ext="301"),
            DeptInfo(id="d4", name="Orthopaedics", name_ml="അസ്ഥിരോഗ വിഭാഗം", floor="2", location_hint="Block A, 2nd floor", phone_ext="201"),
            DeptInfo(id="d6", name="Radiology / Scan", name_ml="റേഡിയോളജി", floor="1", location_hint="Near main entrance", phone_ext="110"),
            DeptInfo(id="d7", name="Laboratory", name_ml="ലാബ്", floor="1", location_hint="Opposite pharmacy", phone_ext="105"),
            DeptInfo(id="d8", name="Pharmacy", name_ml="മെഡിക്കൽ ഷോപ്പ്", floor="G", location_hint="Ground floor", phone_ext="102"),
        ],
        doctors=[
            DoctorInfo(id="doc1", name="Dr. Rajan Nair", name_ml="ഡോ. രാജൻ നായർ",
                       specialty="Cardiologist", qualifications="MD, DM Cardiology",
                       dept_name="Cardiology", dept_name_ml="ഹൃദ്രോഗ വിഭാഗം",
                       slots=[SlotInfo(dow=1, start="09:00", end="13:00", room="301"),
                              SlotInfo(dow=3, start="10:00", end="12:00", room="301")]),
            DoctorInfo(id="doc2", name="Dr. Priya Menon", name_ml="ഡോ. പ്രിയ മേനോൻ",
                       specialty="Orthopaedic Surgeon", qualifications="MS Orthopaedics",
                       dept_name="Orthopaedics", dept_name_ml="അസ്ഥിരോഗ വിഭാഗം",
                       slots=[SlotInfo(dow=2, start="08:00", end="12:00", room="201"),
                              SlotInfo(dow=5, start="14:00", end="17:00", room="201")]),
            DoctorInfo(id="doc3", name="Dr. Arun Kumar", name_ml="ഡോ. അരുൺ കുമാർ",
                       specialty="General Physician", qualifications="MBBS, MD",
                       dept_name="OPD", dept_name_ml="ഒ.പി.ഡി",
                       slots=[SlotInfo(dow=i, start="08:00", end="14:00", room="101") for i in range(1, 6)]),
        ],
        billing=[
            BillingItem(item="consultation:OPD", item_ml="ഒ.പി.ഡി കൺസൾട്ടേഷൻ",
                        price_min=300, price_max=500, notes="token required"),
            BillingItem(item="consultation:Cardiology", item_ml="ഹൃദ്രോഗം",
                        price_min=700, price_max=900, notes=""),
            BillingItem(item="consultation:Orthopaedics", item_ml="അസ്ഥിരോഗം",
                        price_min=600, price_max=800, notes=""),
        ],
        emergency=[
            EmergencyContact(label="Emergency / Ambulance", label_ml="അടിയന്തിരം / ആംബുലൻസ്", phone="1800-425-7012"),
        ],
        faqs=[
            FAQItem(category="facilities", question="Do you have parking?",
                    answer="Yes, free parking for patients in the basement.",
                    answer_ml="", tags=[]),
            FAQItem(category="billing", question="What insurance do you accept?",
                    answer="We accept Star Health, New India, IFFCO Tokio, and CGHS cards.",
                    answer_ml="", tags=[]),
            FAQItem(category="booking", question="How to book an appointment?",
                    answer="Call OPD reception (Ext 101) or visit our front desk.",
                    answer_ml="", tags=[]),
        ],
        knowledge_base=(
            "Visiting hours: 10am–12pm and 5pm–7pm. No visiting on ICU floor.\n"
            "Blood bank open 24×7. ATM in lobby.\n"
            "For lab reports, WhatsApp 9400001234 after 6 hours.\n"
            "Canteen on ground floor, open 7am–9pm."
        ),
    )


async def main() -> None:
    ctx = _mock_hospital()
    brain = GroqBrain(hospital_context=ctx, agent_name="Arya")

    if not brain.is_available():
        print("❌  No API key found. Set SARVAM_API_KEY or GROQ_API_KEY in .env")
        return

    greeting = await brain.generate_greeting()
    print(f"\n{'─'*60}")
    print(f"  Arya: {greeting.text}")
    print(f"{'─'*60}")
    print("  (type your message in Malayalam/English/Manglish — 'quit' to exit)\n")

    detected_lang = "ml-IN"

    while True:
        try:
            user_input = input("  You : ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  [call ended]")
            break

        if user_input.lower() in ("quit", "exit", "bye", "q"):
            print("  Arya: ശരി, വിളിച്ചതിന് നന്ദി. Good bye!")
            break

        if not user_input:
            continue

        result = await brain.process(user_input, language_detected=detected_lang)

        if result.language and result.language != detected_lang:
            detected_lang = result.language

        print(f"\n  Arya: {result.text}")
        if result.should_transfer:
            print(f"  [→ transfer to: {result.transfer_destination}]")
        if result.should_end:
            print("  [call ended by agent]")
            break
        print()


if __name__ == "__main__":
    asyncio.run(main())
