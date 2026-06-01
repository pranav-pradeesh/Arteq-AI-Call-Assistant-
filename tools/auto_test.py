"""
Automated conversation test — runs predefined turns through GroqBrain
and prints Arya's responses + detected issues.

Usage:
    python tools/auto_test.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv(dotenv_path=".env", override=False)

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from src.ai.groq_brain import GroqBrain, GroqBrainResult
from src.db.queries import (
    HospitalContext, DeptInfo, DoctorInfo, SlotInfo, BillingRow, EmergencyContact, FaqRow
)
BillingItem = BillingRow
FAQItem = FaqRow


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
            DeptInfo(id="d5", name="Gynaecology", name_ml="സ്ത്രീരോഗ വിഭാഗം", floor="2", location_hint="Block B, 2nd floor", phone_ext="202"),
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
            EmergencyContact(label="Emergency / Ambulance", label_ml="അടിയന്തിരം",
                             phone="1800-425-7012"),
        ],
        faqs=[
            FAQItem(category="facilities", question="Do you have parking?",
                    answer="Yes, free parking for patients in the basement.",
                    answer_ml="", tags=["parking"]),
            FAQItem(category="billing", question="What insurance do you accept?",
                    answer="We accept Star Health, New India, IFFCO Tokio, and CGHS cards.",
                    answer_ml="", tags=["insurance"]),
        ],
        knowledge_base=(
            "Visiting hours: 10am–12pm and 5pm–7pm. No visiting on ICU floor.\n"
            "Blood bank open 24x7. ATM in lobby.\n"
            "For lab reports, WhatsApp 9400001234 after 6 hours.\n"
            "Canteen on ground floor, open 7am–9pm.\n"
            "Covid ward: Floor 4, separate entrance from east side.\n"
            "Dialysis unit: Floor 2, appointment needed.\n"
        ),
    )


SCENARIOS = [
    # (label, turns_with_language)
    ("GREETING", []),
    ("BASIC TIMINGS QUERY", [
        ("ml-IN", "ആശുപത്രി എത്ര മണിക്ക് തുറക്കും?"),
    ]),
    ("DOCTOR AVAILABILITY", [
        ("ml-IN", "ഹൃദ്രോഗ ഡോക്ടർ ഉണ്ടോ?"),
    ]),
    ("APPOINTMENT BOOKING FLOW", [
        ("ml-IN", "Dr. Rajan Nair നെ കാണണം, appointment എടുക്കണം"),
        ("ml-IN", "ഞാൻ Suresh Kumar, ഫോൺ 9876543210"),
        ("ml-IN", "തിങ്കളാഴ്ച രാവിലെ 10 മണിക്ക്"),
    ]),
    ("EMERGENCY DETECTION", [
        ("ml-IN", "എനിക്ക് നെഞ്ചുവേദന ഉണ്ട്, വളരെ കഠിനമായ വേദന"),
    ]),
    ("MANGLISH / MIXED LANGUAGE", [
        ("en-IN", "Cardiology doctor available aano? Appointment venam"),
    ]),
    ("ENGLISH QUERY", [
        ("en-IN", "What is the OPD consultation fee?"),
    ]),
    ("UNKNOWN QUESTION (should transfer)", [
        ("ml-IN", "ആശുപത്രിയിൽ IVF ചെയ്യുമോ? ചെലവ് എത്രയാണ്?"),
    ]),
    ("CALLBACK REQUEST", [
        ("ml-IN", "എനിക്ക് ഒരു call back വേണം, ഡോക്ടർ availability confirm ചെയ്യണം"),
    ]),
    ("REPEAT REQUEST", [
        ("ml-IN", "ഒന്നുകൂടി പറഞ്ഞോ?"),
    ]),
    ("INSURANCE / BILLING", [
        ("ml-IN", "Star Health insurance ഇവിടെ accept ചെയ്യുമോ?"),
    ]),
    ("GOODBYE", [
        ("ml-IN", "ശരി, നന്ദി"),
    ]),
]


PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"


def _check(label: str, result: GroqBrainResult, user_text: str) -> list[str]:
    issues = []

    if not result.text or len(result.text) < 5:
        issues.append("EMPTY/VERY SHORT response")

    # Should respond in Malayalam for ml-IN turns
    if "ml-IN" in label or "MALAYALAM" in label:
        has_ml = any('ഀ' <= c <= 'ൿ' for c in result.text)
        if not has_ml and not result.should_transfer and not result.should_end:
            issues.append("No Malayalam script in response (expected Malayalam)")

    # Emergency should always transfer
    if "EMERGENCY" in label and not result.should_transfer:
        issues.append("Emergency detected but should_transfer=False")

    # Booking flow should emit action_type
    if "BOOKING" in label and "appointment" in user_text.lower():
        pass  # just observe

    # Response shouldn't contain raw JSON leaked
    if result.text.strip().startswith("{") and '"text"' in result.text:
        issues.append("Raw JSON leaked into response text")

    # Latency check
    if result.latency_ms > 8000:
        issues.append(f"HIGH LATENCY: {result.latency_ms}ms")

    return issues


async def run_scenario(
    name: str,
    turns: list[tuple[str, str]],
    brain: GroqBrain,
) -> dict:
    all_issues = []
    all_results = []

    if not turns:
        # Greeting only
        t0 = time.perf_counter()
        result = await brain.generate_greeting()
        latency = int((time.perf_counter() - t0) * 1000)
        all_results.append(("(greeting)", result))
        return {"name": name, "results": all_results, "issues": all_issues}

    for lang, text in turns:
        try:
            result = await brain.process(text, language_detected=lang)
            issues = _check(name, result, text)
            all_issues.extend(issues)
            all_results.append((text, result))
        except Exception as exc:
            all_issues.append(f"EXCEPTION: {exc}")
            all_results.append((text, None))

    return {"name": name, "results": all_results, "issues": all_issues}


async def main() -> None:
    ctx = _mock_hospital()

    print(f"\n{'='*70}")
    print("  ARYA BRAIN AUTOMATED TEST SUITE")
    print(f"{'='*70}\n")

    brain = GroqBrain(hospital_context=ctx, agent_name="Arya")

    if not brain.is_available():
        print("ERROR: No API key found. Set SARVAM_API_KEY or GROQ_API_KEY in .env")
        return

    total_issues = []
    scenario_results = []

    for name, turns in SCENARIOS:
        # Fresh brain per scenario so history doesn't bleed
        fresh_brain = GroqBrain(hospital_context=ctx, agent_name="Arya")

        print(f"{'─'*70}")
        print(f"  SCENARIO: {name}")
        print(f"{'─'*70}")

        scenario = await run_scenario(name, turns, fresh_brain)
        scenario_results.append(scenario)

        for user_text, result in scenario["results"]:
            if result is None:
                print(f"  You : {user_text}")
                print(f"  Arya: [ERROR — no result]")
                continue

            if isinstance(result, GroqBrainResult):
                print(f"  You : {user_text}")
                print(f"  Arya: {result.text}")
                flags = []
                if result.should_transfer:
                    flags.append(f"→ transfer:{result.transfer_destination}")
                if result.should_end:
                    flags.append("END")
                if result.action_type:
                    flags.append(f"action:{result.action_type}")
                if result.language:
                    flags.append(f"lang:{result.language}")
                flags.append(f"{result.latency_ms}ms")
                print(f"         [{' | '.join(flags)}]")
            else:
                # Greeting result
                print(f"  Arya: {result.text if hasattr(result, 'text') else result}")

        if scenario["issues"]:
            for issue in scenario["issues"]:
                print(f"  {FAIL} ISSUE: {issue}")
            total_issues.extend([(name, i) for i in scenario["issues"]])
        else:
            print(f"  {PASS} OK")

        print()

    print(f"{'='*70}")
    print(f"  SUMMARY")
    print(f"{'='*70}")
    if not total_issues:
        print(f"  {PASS} All {len(SCENARIOS)} scenarios passed with no issues!\n")
    else:
        print(f"  {FAIL} {len(total_issues)} issue(s) found across {len(SCENARIOS)} scenarios:\n")
        for scenario_name, issue in total_issues:
            print(f"    [{scenario_name}] {issue}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
