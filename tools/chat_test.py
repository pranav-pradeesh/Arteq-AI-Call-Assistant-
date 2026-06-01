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
from src.db.queries import HospitalContext, DeptInfo, DoctorInfo, BillingItem, EmergencyContact, FAQItem


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
            DeptInfo(name="OPD", name_ml="ഒ.പി.ഡി", floor=1, phone_ext="101"),
            DeptInfo(name="Emergency / Casualty", name_ml="അടിയന്തിര വിഭാഗം", floor=0, phone_ext="100"),
            DeptInfo(name="Cardiology", name_ml="ഹൃദ്രോഗ വിഭാഗം", floor=3, phone_ext="301"),
            DeptInfo(name="Orthopaedics", name_ml="അസ്ഥിരോഗ വിഭാഗം", floor=2, phone_ext="201"),
            DeptInfo(name="Radiology / Scan", name_ml="റേഡിയോളജി", floor=1, phone_ext="110"),
            DeptInfo(name="Laboratory", name_ml="ലാബ്", floor=1, phone_ext="105"),
            DeptInfo(name="Pharmacy", name_ml="മെഡിക്കൽ ഷോപ്പ്", floor=0, phone_ext="102"),
        ],
        doctors=[
            DoctorInfo(name="Rajan Nair", name_ml="ഡോ. രാജൻ നായർ", dept_name="Cardiology",
                       slots=[type('S', (), {'dow': 1, 'start': '09:00', 'end': '13:00'})(),
                              type('S', (), {'dow': 3, 'start': '10:00', 'end': '12:00'})()]),
            DoctorInfo(name="Priya Menon", name_ml="ഡോ. പ്രിയ മേനോൻ", dept_name="Orthopaedics",
                       slots=[type('S', (), {'dow': 2, 'start': '08:00', 'end': '12:00'})(),
                              type('S', (), {'dow': 5, 'start': '14:00', 'end': '17:00'})()]),
            DoctorInfo(name="Arun Kumar", name_ml="ഡോ. അരുൺ കുമാർ", dept_name="OPD",
                       slots=[type('S', (), {'dow': 1, 'start': '08:00', 'end': '14:00'})(),
                              type('S', (), {'dow': 2, 'start': '08:00', 'end': '14:00'})(),
                              type('S', (), {'dow': 3, 'start': '08:00', 'end': '14:00'})(),
                              type('S', (), {'dow': 4, 'start': '08:00', 'end': '14:00'})(),
                              type('S', (), {'dow': 5, 'start': '08:00', 'end': '14:00'})()]),
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
            FAQItem(question="Do you have parking?",
                    answer="Yes, free parking for patients in the basement."),
            FAQItem(question="What insurance do you accept?",
                    answer="We accept Star Health, New India, IFFCO Tokio, and CGHS cards."),
            FAQItem(question="How to book an appointment?",
                    answer="Call OPD reception (Ext 101) or visit our front desk."),
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
