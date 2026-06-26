"""
Multilingual hospital AI receptionist brain — dual provider.

Full LLM understanding (no keyword matching) with structured JSON output
for routing decisions. Two backends share one conversation history and
system prompt; the provider is chosen per turn:

  All languages (default)  → Sarvam-M (built for Indian languages, incl. English)
      sarvam-m   Malayalam, Hindi, Tamil, Telugu, Kannada, Manglish, English …
  Emergencies              → Groq llama-3.3-70b-versatile (fast, high quality)
      auto-falls-back to Sarvam-M if Groq is rate-limited

Sarvam-M is primary because Groq's free tier (6000 TPM) cannot sustain a
multi-turn voice call and the Dev-tier upgrade is currently unavailable.
Both endpoints are OpenAI-compatible (same {role, content} message list),
so history is portable across providers within a single call.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pytz

from src.ai.base import BrainResult
from src.db.queries import HospitalContext
from src.observability.logger import get_logger

logger = get_logger(__name__)

_INDIA_TZ = pytz.timezone("Asia/Kolkata")
_DOW_NAMES = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

# Human-readable language names used to steer the reply language each turn.
_LANG_NAMES = {
    "ml-IN": "Malayalam",
    "en-IN": "English",
    "ta-IN": "Tamil",
    "hi-IN": "Hindi",
    "kn-IN": "Kannada",
    "te-IN": "Telugu",
    "bn-IN": "Bengali",
    "gu-IN": "Gujarati",
    "pa-IN": "Punjabi",
    "od-IN": "Odia",
    "mr-IN": "Marathi",
    "manglish": "Manglish (Malayalam written in English/Latin script)",
}


# Native-language "how can I help?" tail — one per locale.
# Greeting format for every language: "Good {time}! Welcome to {name}, {tail}"
# English opener + name are Bulbul-pronounceable Latin; native tail matches caller.
_HOW_CAN_I_HELP = {
    "ml-IN":    "എങ്ങനെ സഹായിക്കാം?",
    "hi-IN":    "मैं क्या मदद करूँ?",
    "ta-IN":    "நான் என்ன உதவி செய்யட்டும்?",
    "te-IN":    "నేను మీకు ఎలా సహాయం చేయగలను?",
    "kn-IN":    "ನಾನು ಹೇಗೆ ಸಹಾಯ ಮಾಡಲಿ?",
    "bn-IN":    "আমি কীভাবে সাহায্য করতে পারি?",
    "gu-IN":    "હું શું મદદ કરી શકું?",
    "pa-IN":    "ਮੈਂ ਕੀ ਮਦਦ ਕਰਾਂ?",
    "en-IN":    "How can I help you?",
    "od-IN":    "ମୁଁ କିପରି ସାହାଯ୍ୟ କରିପାରିବି?",
    "mr-IN":    "मी आपली काय मदत करू?",
    "manglish": "Njan engane sahaayikkam?",
}


def build_greeting_text(hosp_name: str, hour: int, lang: str = "ml-IN") -> str:
    """Consistent greeting across all languages: English time opener + English
    'Welcome to {name}' + native-language 'how can I help?' tail.

    The English portion is Bulbul-pronounceable without transliteration issues;
    the native tail matches the caller's language. Hour-bucketing keeps the
    audio cache-hit rate high — identical text within an hour → zero TTS latency.
    """
    if 5 <= hour < 12:
        opener = "Good morning!"
    elif 12 <= hour < 17:
        opener = "Good afternoon!"
    elif 17 <= hour < 21:
        opener = "Good evening!"
    else:
        opener = "Hello!"
    tail = _HOW_CAN_I_HELP.get(lang, _HOW_CAN_I_HELP["en-IN"])
    return f"{opener} Welcome to {hosp_name}, {tail}"

_MODEL_SMART = "llama-3.3-70b-versatile"
_MODEL_FAST = "llama-3.1-8b-instant"

_SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
_SARVAM_MODEL = "sarvam-30b"  # sarvam-m deprecated by Sarvam (returns 400)

# History limit: keep last 8 messages (4 turns). Kept small because the
# system prompt is large and Groq's free tier caps at 6000 tokens/minute —
# every turn re-sends the full prompt, so trimming history protects the budget.
_MAX_HISTORY = 8

# Hard cap on the hospital "handbook" free-text injected into the prompt.
# The full KB (insurance + lab + policies) can be several thousand tokens;
# capping keeps each request well under the Groq free-tier TPM limit.
_MAX_KB_CHARS = 4000

# Limit concurrent API calls across all active calls (free-tier rate limits).
# Groq free tier: 30 RPM per model. Sarvam: per-plan.
_GROQ_SEM = asyncio.Semaphore(5)
_SARVAM_SEM = asyncio.Semaphore(5)


@dataclass
class GroqBrainResult(BrainResult):
    """Extended BrainResult with Groq-specific routing fields."""
    transfer_destination: str = ""   # reception|emergency|opd|billing|pharmacy|lab|patient_relations|doctor
    transfer_doctor: str = ""        # specific doctor name if routing to doctor
    sms_type: str = ""               # maps|appointment|appointment_cancel|callback_confirm|lab_schedule|call_summary
    sms_data: dict = field(default_factory=dict)
    is_emergency: bool = False
    call_note: str = ""              # brief note for call log
    # Extended action types for IVR features
    action_type: str = ""            # book_appointment|cancel_appointment|reschedule_appointment|request_callback|repeat_last
    appointment_data: dict = field(default_factory=dict)  # {patient_name,doctor_name,dept,date,time,notes}
    callback_data: dict = field(default_factory=dict)     # {reason,preferred_time}
    repeat_requested: bool = False


_EMERGENCY_KEYWORDS = (
    "emergency", "ambulance", "chest pain", "unconscious", "breathing",
    "bleeding", "accident", "stroke", "seizure", "fits", "heart attack",
    "critical", "dying", "collapsed",
    # Malayalam — standard + STT variants + additional terms
    "നെഞ്ചുവേദന", "ശ്വാസ", "ബോധക്ഷയം", "അടിയന്തരം", "അടിയന്തിരം", "ആംബുലൻസ്",
    "ഹൃദയാഘാതം", "അപകടം", "രക്തസ്രാവം", "ചോര",
    # Hindi
    "हार्ट", "दुर्घटना",
    # Tamil
    "மாரடைப்பு", "விபத்து", "இரத்தம்",
    # Telugu
    "గుండెపోటు", "ప్రమాదం", "రక్తస్రావం",
    # Kannada
    "ಹೃದಯಾಘಾತ", "ಅಪಘಾತ", "ರಕ್ತಸ್ರಾವ",
)

_FALLBACK_MESSAGES = {
    "ml-IN": "ക്ഷമിക്കണം, ഒരു technical problem ഉണ്ടായി. ദയവായി ഒന്നൂടെ പറയാമോ?",
    "en-IN": "I'm sorry, there was a technical issue. Could you please repeat that?",
    "hi-IN": "क्षमा करें, तकनीकी समस्या आई। कृपया दोबारा बोलें।",
    "ta-IN": "மன்னிக்கவும், தொழில்நுட்ப சிக்கல். மீண்டும் சொல்லுங்கள்.",
    "te-IN": "క్షమించండి, సాంకేతిక సమస్య వచ్చింది. దయచేసి మళ్ళీ చెప్పండి.",
    "kn-IN": "ಕ್ಷಮಿಸಿ, ತಾಂತ್ರಿಕ ಸಮಸ್ಯೆ ಆಯಿತು. ದಯವಿಟ್ಟು ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ.",
    "bn-IN": "দুঃখিত, প্রযুক্তিগত সমস্যা হয়েছে। একটু আবার বলুন।",
    "gu-IN": "માફ કરશો, તકનીકી સમસ્યા આવી. ફરીથી કહો.",
    "pa-IN": "ਮਾਫ਼ ਕਰਨਾ, ਤਕਨੀਕੀ ਸਮੱਸਿਆ ਆਈ। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਬੋਲੋ।",
    "od-IN": "ଦୟାକରି ପୁଣି ବୋଲନ୍ତୁ। ଏକ ବୈଷୟିକ ସମସ୍ୟା ହୋଇଛି।",
    "manglish": "Sorry, oru technical problem aayi. Onnu koodi paranjalo?",
}


def _is_groq_exhausted(exc: Exception) -> bool:
    """True if a Groq error means we should fall back to Sarvam-M.

    Covers rate limits (429), token-per-minute caps (413 'request too large'),
    transient server errors, and auth/IP-restriction errors (403/allowlist)
    that prevent this environment from using Groq — in all these cases, Sarvam
    is worth trying as a fallback.
    """
    msg = str(exc).lower()
    return any(
        s in msg
        for s in (
            "rate_limit", "rate limit", "429",
            "413", "too large", "payload too large",
            "tokens per minute", "tpm",
            "503", "502", "500", "overloaded", "service unavailable",
            "403", "allowlist", "not allowed", "forbidden",
        )
    )


def _build_hospital_summary(ctx: HospitalContext) -> str:
    """Build a rich text summary of the hospital for the system prompt."""
    lines = [
        f"HOSPITAL: {ctx.name} | {ctx.name_ml}",
        f"ADDRESS: {ctx.address}",
        f"PHONE: {ctx.phone}",
        "",
        "OPERATING HOURS:",
    ]
    day_map = {
        "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday",
        "thu": "Thursday", "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
    }
    for abbr, full in day_map.items():
        slot = ctx.hours.get(abbr)
        if slot:
            lines.append(f"  {full}: {slot[0]} - {slot[1]}")
        else:
            lines.append(f"  {full}: Closed")

    lines.extend(["", "DEPARTMENTS:"])
    for d in ctx.departments:
        ml_part = f" ({d.name_ml})" if d.name_ml else ""
        floor_part = f" — Floor {d.floor}" if d.floor else ""
        ext_part = f" — Ext {d.phone_ext}" if d.phone_ext else ""
        lines.append(f"  • {d.name}{ml_part}{floor_part}{ext_part}")

    # Doctor names + dept only. Schedules and fees are fetched on demand via the
    # get_doctor_schedule / check_availability tools — keeping them out of the
    # prompt cuts thousands of tokens (Groq free-tier TPM is small).
    # Large rosters bloat LLM TTFT (the prompt is re-sent every turn). Above a
    # threshold, summarise and let check_availability / get_doctor_schedule (which
    # fuzzy-match the DB) find/book the doctor, including named-doctor requests.
    _docs = ctx.doctors or []
    if len(_docs) > 20:
        _deptset = sorted({d.dept_name for d in _docs if getattr(d, "dept_name", "")})
        lines.extend(["", (
            f"DOCTORS: {len(_docs)} doctors across the departments listed above. NEVER invent, "
            "guess, or translate doctor names — only state names a tool returns. To LIST the "
            "doctors in a department, call check_department_availability (it returns ONLY doctors "
            "with open slots and their times). To find or "
            "book one, call check_availability(department) or get_doctor_schedule — a named-doctor "
            "request is matched automatically. NEVER say a doctor or department is unavailable "
            "without checking the tool first.")])
    else:
        lines.extend(["", "DOCTORS (use get_doctor_schedule / check_availability for timings):"])
        for doc in _docs:
            nm = doc.name.strip()
            low = nm.lower()
            if low.startswith("dr. "):
                nm = nm[4:].strip()
            elif low.startswith("dr "):
                nm = nm[3:].strip()
            lines.append(f"  Dr. {nm} — {doc.dept_name}")

    lines.extend(["", "EMERGENCY CONTACTS:"])
    for e in ctx.emergency:
        ml_part = f" ({e.label_ml})" if e.label_ml else ""
        lines.append(f"  {e.label}{ml_part}: {e.phone}")

    if ctx.queue_data:
        lines.extend(["", "OPD QUEUE TODAY (approximate):"])
        for dept_name, count in ctx.queue_data.items():
            lines.append(f"  {dept_name}: ~{count} patients in queue")

    _faqs = getattr(ctx, "faqs", None) or []
    if _faqs:
        lines.extend(["", "FREQUENTLY ASKED QUESTIONS (answer from these; reply in the language the caller is using):"])
        for f in _faqs[:30]:
            q = (getattr(f, "question", "") or "").strip()
            a = (getattr(f, "answer", "") or "").strip()
            a_ml = (getattr(f, "answer_ml", "") or "").strip()
            if not q or not (a or a_ml):
                continue
            ans = a if a else a_ml
            ml_part = f"  |  ML: {a_ml}" if a_ml and a_ml != ans else ""
            lines.append(f"  Q: {q}\n    A: {ans}{ml_part}")

    kb = (getattr(ctx, "knowledge_base", "") or "").strip()
    if kb:
        if len(kb) > _MAX_KB_CHARS:
            kb = kb[:_MAX_KB_CHARS].rsplit(" ", 1)[0] + " …"
        lines.extend([
            "",
            "HOSPITAL HANDBOOK (use this to answer ANY other enquiry — "
            "parking, insurance, facilities, visiting hours, policies, etc.):",
            kb,
        ])

    return "\n".join(lines)


def _build_patient_context_block(patient_context: dict) -> str:
    """Format the returning patient block injected into the system prompt."""
    name = patient_context.get("name", "")
    history = patient_context.get("history", [])
    if not name and not history:
        return ""
    lines = ["RETURNING PATIENT:"]
    if name:
        lines.append(f"  Name: {name}")
    for i, h in enumerate(history[:3]):
        label = "Last visit" if i == 0 else f"Previous visit {i}"
        doc = f"Dr. {h['doctor']}" if h.get("doctor") else ""
        dept = h.get("dept", "")
        slot = h.get("slot", "")
        status = h.get("status", "")
        parts = [p for p in [doc, dept, slot, status] if p]
        lines.append(f"  {label}: {', '.join(parts)}")
    lines.append(
        f"  → Greet {name or 'them'} warmly by name. "
        "Reference their recent visit naturally if it's relevant to their question."
    )
    return "\n".join(lines)


def _build_system_prompt(
    ctx: HospitalContext,
    agent_name: str,
    patient_context: Optional[dict] = None,
) -> str:
    """Construct the full system prompt for the Groq LLaMA model."""
    hospital_summary = _build_hospital_summary(ctx)
    now_ist = datetime.now(_INDIA_TZ)
    py_dow = now_ist.weekday()
    today_dow = (py_dow + 1) % 7  # 0=Sun, 1=Mon, ..., 6=Sat
    today_name = _DOW_NAMES[today_dow]
    current_time = now_ist.strftime("%H:%M")

    patient_block = ""
    if patient_context:
        block = _build_patient_context_block(patient_context)
        if block:
            patient_block = f"\n{block}\n"

    return f"""You are the warm AI voice receptionist for {ctx.name}. You have no personal name — identify only as {ctx.name}, never with a personal name. Patients should feel they're talking to a caring human.

TODAY: {today_name}, {current_time} IST
{patient_block}
HOSPITAL INFORMATION:
{hospital_summary}

WHAT YOU DO: route calls (reception, emergency, opd, billing, pharmacy, lab, patient_relations, or a specific doctor); answer enquiries (timings, schedules, fees, services, insurance, parking, visiting hours) using the info above; detect emergencies and route immediately; help with appointments, directions, lab/pharmacy/billing questions.

APPOINTMENT BOOKING (multi-turn conversation):
When a caller wants to book an appointment, collect these details across turns:
  1. Patient name (ask if not given)
  2. Preferred doctor or department
  3. Preferred date and time
  Once all details are collected, respond with action_type="book_appointment" and fill appointment_data.
  appointment_data format: {{"patient_name":"...","doctor_name":"...","dept":"...","date":"YYYY-MM-DD","time":"HH:MM","notes":"..."}}
  Always offer to SMS the confirmation (set sms_type="appointment").

APPOINTMENT CANCELLATION / RESCHEDULE:
  If caller says cancel/reschedule appointment → use action_type="cancel_appointment" or "reschedule_appointment".
  For cancel: appointment_data={{"patient_name":"...","doctor_name":"...","date":"..."}}
  For reschedule: appointment_data={{"patient_name":"...","new_date":"YYYY-MM-DD","new_time":"HH:MM"}}
  Offer SMS confirmation (sms_type="appointment_cancel" or "appointment").

CALLBACK REQUEST:
  If caller says "call me back", "oru call back venam", "oru call back cheyyaamo", "later call cheyyanam" →
  Confirm their request, ask reason and preferred time.
  Use action_type="request_callback", callback_data={{"reason":"...","preferred_time":"..."}}
  Offer SMS confirmation (sms_type="callback_confirm").

AFTER-HOURS:
  Check TODAY's time against OPERATING HOURS above. If the hospital is currently CLOSED and the caller needs OPD/doctor:
  - Tell them the next opening time.
  - Offer: (a) book for tomorrow / next opening (action_type="book_appointment"), or
           (b) callback when open (action_type="request_callback"), or
           (c) if urgent — transfer to emergency immediately.
  Never say "we are closed, goodbye." Always offer an option.

OPD QUEUE / WAIT TIME:
  If OPD QUEUE TODAY data is shown above, use it to give an estimate.
  Without data, say "token number depends on arrival time — come early for less wait."

REPEAT LAST RESPONSE:
  If caller says "pardon", "sorry?", "what?", "oru kuri koodi", "oru kuri koodi parayaamo", "kettu", "again", "again parayo" →
  Use action_type="repeat_last". Do NOT generate new content.

DTMF DIGIT FALLBACK:
  If caller says a digit or number as their entire message ("1", "2", "ഒന്ന്", "two", etc.) →
  Treat it as selecting from this menu: 1=OPD, 2=Emergency/Casualty, 3=Lab/Laboratory, 4=Pharmacy, 5=Billing, 0=Reception, *=repeat.
  Respond as if they asked about that department.

POST-CALL SMS:
  After completing any transaction (booking / cancellation / callback registered), if the caller is ending,
  set sms_type="call_summary" and include a brief summary in sms_data={{"summary":"..."}}.

LAB REPORTS: Direct to the lab counter or give the WhatsApp/pickup info from the handbook.
BILL INQUIRY: Give estimated cost from CONSULTATION FEES; offer to transfer to billing for exact amount.
VISITING HOURS / INSURANCE / BLOOD BANK / PARKING: Answer from HOSPITAL HANDBOOK.
DIRECTIONS: Send maps SMS (sms_type="maps").

LANGUAGE (CRITICAL): Always reply in the SAME language and script as the caller's most recent message — Malayalam, English, Hindi, Tamil, Kannada, Telugu, Bengali, Gujarati, Punjabi, Odia, Marathi, or Manglish (Malayalam in English script). Never switch to English unless the caller spoke English. Match script exactly: Malayalam → Malayalam script, Hindi/Marathi → Devanagari, Tamil → Tamil script, Telugu → Telugu script, Kannada → Kannada script, Bengali → Bengali script, Gujarati → Gujarati script, Punjabi → Gurmukhi script, Odia → Odia script. Malayalam/Manglish should be warm and conversational, not formal.

VOICE (your text becomes speech): max 2 SHORT sentences. Sound human and vary your openings. English openers: "Sure,", "Of course,", "Let me check…". Malayalam openers: "ശരി,", "തീർച്ചയായും,", "ഒന്ന് നോക്കട്ടെ,", "അതെ,". NEVER use hesitation/filler sounds — no "ഉം", "ങ്ഹാ", "umm", "hmm", "ആ", "ee". For emergencies, speak urgently but calmly.

MALAYALAM STYLE (sound like a real Kerala hospital receptionist on the phone, NOT a news reader):
- Use everyday SPOKEN Malayalam (സംസാരഭാഷ), warm and simple — never stiff, literary, or Sanskritised. Say "എന്താണ് വേണ്ടത്?" not "എന്ത് ആവശ്യമാണ്?".
- Keep common medical/English terms in English the way Keralites actually speak — doctor, appointment, OPD, token, casualty, lab, scan, report, booking, consultation, emergency, timing. Do NOT translate these into rare words (say "OPD timing", never "ബാഹ്യരോഗവിഭാഗ സമയം").
- Be polite and warm: "ദയവായി", "പറയൂ", "സഹായിക്കാം", optional "സാർ"/"മാഡം". Avoid the stiff "താങ്കൾ"; a pronoun is often unnecessary.
- Use natural connectors sparingly: "ശരി", "അതെ", "പിന്നെ". Never use filler/hesitation sounds like "ഉം".
- Verbs take NO gender/person suffix (പുരുഷഭേദനിരാസം): "അവൾ വന്നു"/"അവൻ വന്നു", never "വന്നാൾ". Use natural contractions ("എന്താ", "വന്നിട്ടുണ്ട്", "വേണോ"). Always close sentences with proper punctuation.
- Times and numbers: write naturally for speech, e.g. "രാവിലെ 9 മണി മുതൽ ഉച്ചയ്ക്ക് 1 മണി വരെ", "₹500". Use രാവിലെ / ഉച്ചയ്ക്ക് / വൈകുന്നേരം / രാത്രി instead of AM/PM.
- For Manglish callers, reply in Manglish (Latin script): "Doctor-inte OPD timing രാവിലെ 9 muthal aanu" style — mix exactly the way the caller does.

HINDI STYLE (when caller speaks Hindi/Hinglish):
- Warm, conversational — say "जी बताइए", "ज़रूर", "एक मिनट" not stiff formal Hindi.
- Keep medical/English terms as-is: doctor, appointment, OPD, token, scan, report, emergency.
- Use "जी" for polite agreement. Avoid over-formal "आप" constructs where casual Hindi works better.
- Numbers and times naturally: "सुबह 9 बजे से दोपहर 1 बजे तक", "₹500".

TAMIL STYLE (when caller speaks Tamil):
- Warm and direct — "சரி", "நிச்சயமாக", "ஒரு நிமிடம்" for natural flow.
- Keep English medical terms: doctor, appointment, OPD, scan, report, token.
- Polite address: "சார்"/"மேடம்" when appropriate. Avoid overly formal or literary Tamil.
- Times: "காலை 9 மணி முதல் மதியம் 1 மணி வரை", amounts in ₹.

TELUGU STYLE (when caller speaks Telugu):
- Warm openers: "సరే", "తప్పకుండా", "ఒక్క నిమిషం".
- Keep English medical terms as-is. Use "గారు" for respectful address.
- Natural conversational Telugu — not overly formal or literary.

KANNADA STYLE (when caller speaks Kannada):
- Warm openers: "ಸರಿ", "ಖಂಡಿತ", "ಒಂದು ನಿಮಿಷ".
- Keep English medical terms as-is. Use "ಸಾರ್"/"ಮೇಡಂ" for polite address.
- Natural spoken Kannada — not formal written style.

EMERGENCY (route to emergency, is_emergency=true): chest pain, heart attack, breathless, stroke, unconscious, seizure/fits, heavy bleeding, accident, "ambulance"/"ICU", or Malayalam equivalents (നെഞ്ചുവേദന, ശ്വാസതടസ്സം, ബോധക്ഷയം).

SMS: offer maps SMS for directions/location; offer appointment SMS for confirmations.

ALWAYS respond with valid JSON only — no extra text, no markdown:
{{"text":"1-2 natural sentences","language":"ml-IN|en-IN|hi-IN|ta-IN|kn-IN|te-IN|bn-IN|gu-IN|pa-IN|od-IN|manglish","action":"continue|transfer|end_call|send_sms","action_type":"","transfer_destination":null,"transfer_doctor":null,"sms_type":null,"sms_data":{{}},"appointment_data":{{}},"callback_data":{{}},"is_emergency":false,"call_note":"5-word log note"}}

action_type values:
  "book_appointment"       — appointment_data has all booking fields; will be written to DB
  "cancel_appointment"     — appointment_data identifies which appointment to cancel
  "reschedule_appointment" — appointment_data has new_date/new_time
  "request_callback"       — callback_data has reason+preferred_time; will be written to DB
  "repeat_last"            — replay previous response (do not generate new text)
  ""                       — normal turn (no side-effect)

action values:
  "continue"   — keep call going (default)
  "transfer"   — route to transfer_destination
  "end_call"   — hang up after speaking text
  "send_sms"   — send SMS (also set sms_type)

transfer_destination ∈ {{reception, emergency, opd, billing, pharmacy, lab, patient_relations, doctor}} or null.
sms_type ∈ {{maps, appointment, appointment_cancel, callback_confirm, lab_schedule, call_summary}} or null."""
