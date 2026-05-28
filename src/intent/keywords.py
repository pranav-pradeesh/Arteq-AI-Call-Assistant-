"""
Malayalam/Manglish keyword dictionary for intent and entity detection.

Design:
  - Each intent has a weighted keyword list
  - Keywords include standard Malayalam, Manglish, and common variants
  - Department synonyms are separate for entity extraction
  - Weights are higher for unambiguous keywords
  - Scoring is O(k) where k = number of tokens in transcript

This is the single source of truth for intent vocabulary.
Tenant-level overrides (from KeywordRule table) are merged at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# Intent constants
# ─────────────────────────────────────────────────────────────────────────────

INTENT_DOCTOR_AVAILABILITY = "doctor_availability"
INTENT_DOCTOR_TIMING = "doctor_timing"
INTENT_CONSULTATION_FEE = "consultation_fee"
INTENT_DEPARTMENT_EXISTS = "department_exists"
INTENT_HOSPITAL_TIMING = "hospital_timing"
INTENT_EMERGENCY = "emergency_availability"
INTENT_LOCATION = "location_query"
INTENT_CONTACT = "contact_query"
INTENT_HUMAN_TRANSFER = "human_transfer"
INTENT_REPEAT = "repeat_request"
INTENT_GOODBYE = "goodbye"
INTENT_UNKNOWN = "unknown"
INTENT_SYMPTOM = "symptom_query"

ALL_INTENTS = [
    INTENT_DOCTOR_AVAILABILITY,
    INTENT_DOCTOR_TIMING,
    INTENT_CONSULTATION_FEE,
    INTENT_DEPARTMENT_EXISTS,
    INTENT_HOSPITAL_TIMING,
    INTENT_EMERGENCY,
    INTENT_LOCATION,
    INTENT_CONTACT,
    INTENT_HUMAN_TRANSFER,
    INTENT_REPEAT,
    INTENT_GOODBYE,
    INTENT_SYMPTOM,
    INTENT_UNKNOWN,
]


# ─────────────────────────────────────────────────────────────────────────────
# Keyword → intent mapping
# Format: (keyword, weight)  —  weight 1.0 = baseline, 2.0 = strong signal
# ─────────────────────────────────────────────────────────────────────────────

INTENT_KEYWORDS: Dict[str, List[Tuple[str, float]]] = {

    # ── Doctor availability ──────────────────────────────────────────────────
    INTENT_DOCTOR_AVAILABILITY: [
        # English / Manglish
        ("doctor", 1.2),
        ("docter", 1.0),
        ("doctar", 0.9),
        ("vaidyan", 1.5),
        ("chikitsan", 1.2),
        ("available", 1.5),
        ("undo", 1.3),
        ("und", 1.0),
        ("undu", 1.2),
        ("undakum", 1.0),
        ("varum", 1.0),
        ("varu", 0.9),
        ("vann", 0.9),
        ("irikkum", 1.0),
        ("irikkunundo", 1.5),
        ("annu", 0.8),
        ("innu", 1.0),
        ("innale", 0.7),
        ("nale", 0.9),
        ("consult", 1.2),
        ("consultation", 1.2),
        ("op", 1.0),
        ("opd", 1.2),
        # Malayalam script
        ("ഡോക്ടർ", 1.8),
        ("ഡോക്ടറുണ്ടോ", 2.0),
        ("ഡോക്ടറു", 1.5),
        ("വൈദ്യൻ", 1.8),
        ("ചികിത്സകൻ", 1.5),
        ("ഉണ്ടോ", 1.3),
        ("ഉണ്ട്", 1.0),
        ("ഇന്ന്", 1.0),
        ("നാളെ", 1.0),
    ],

    # ── Doctor / Department timing ───────────────────────────────────────────
    INTENT_DOCTOR_TIMING: [
        ("time", 1.5),
        ("timing", 2.0),
        ("timings", 2.0),
        ("samayam", 2.0),
        ("samaym", 1.5),
        ("eppo", 1.8),
        ("eppozha", 1.8),
        ("eppol", 1.8),
        ("epozhanu", 1.5),
        ("ethra", 1.0),
        ("mathiyo", 1.0),
        ("morning", 1.0),
        ("evening", 1.0),
        ("noon", 0.8),
        ("night", 0.8),
        ("rathri", 1.0),
        ("uduppu", 0.7),
        ("vare", 0.8),
        ("mute", 0.8),
        ("op", 1.2),
        ("opd", 1.2),
        ("schedule", 1.5),
        ("slots", 1.2),
        ("slot", 1.2),
        ("session", 1.0),
        # Malayalam script
        ("ടൈമിംഗ്", 2.0),
        ("ടൈം", 1.8),
        ("സമയം", 2.0),
        ("എപ്പോഴാ", 2.0),
        ("എപ്പോഴാണ്", 2.0),
        ("എപ്പോൾ", 2.0),
        ("എപ്പോഴ്", 2.0),
        ("എത്ര", 1.0),
        ("രാവിലെ", 1.0),
        ("വൈകുന്നേരം", 1.0),
        ("രാത്രി", 1.0),
        ("തുറക്കും", 1.8),
        ("തുറന്ന", 1.5),
    ],

    # ── Consultation fee ─────────────────────────────────────────────────────
    INTENT_CONSULTATION_FEE: [
        ("fee", 2.0),
        ("fees", 2.0),
        ("charge", 2.0),
        ("charges", 2.0),
        ("rate", 1.5),
        ("rates", 1.5),
        ("cost", 1.5),
        ("amount", 1.5),
        ("cash", 1.2),
        ("money", 1.2),
        ("pay", 1.2),
        ("payment", 1.2),
        ("consultation fee", 3.0),
        ("varadakshina", 2.0),
        ("chellam", 1.0),
        ("panam", 1.8),
        ("ethra", 1.2),
        ("ethraya", 1.5),
        ("kada", 0.8),
        ("rupee", 1.2),
        ("rupees", 1.2),
        ("rs", 1.0),
        # Malayalam script
        ("ഫീ", 2.0),
        ("ഫീസ്", 2.0),
        ("ചാർജ്", 2.0),
        ("രൂപ", 1.5),
        ("പണം", 1.8),
        ("എത്രയാ", 1.8),
        ("എത്രയാണ്", 1.8),
        ("എത്ര", 1.0),
    ],

    # ── Department existence ─────────────────────────────────────────────────
    INTENT_DEPARTMENT_EXISTS: [
        ("department", 1.5),
        ("dept", 1.2),
        ("undakum", 1.0),
        ("undo", 1.2),
        ("und", 0.9),
        ("undu", 1.2),
        ("available", 1.2),
        ("section", 1.0),
        ("vibhagam", 1.5),        # department in Malayalam
        ("speciality", 1.2),
        ("specialty", 1.2),
        ("treat", 1.0),           # "do you treat X"
        ("chikitsa", 1.5),        # treatment/department
    ],

    # ── Hospital timing (general open/close) ─────────────────────────────────
    INTENT_HOSPITAL_TIMING: [
        ("hospital", 1.2),
        ("thirakkum", 1.5),
        ("thurakkunnu", 1.5),
        ("open", 1.5),
        ("opens", 1.5),
        ("close", 1.5),
        ("closes", 1.5),
        ("adakkunnu", 1.5),
        ("adakkum", 1.5),
        ("sunday", 2.0),
        ("saturday", 1.8),
        ("holiday", 2.0),
        ("bandh", 1.5),
        ("avadhi", 1.5),
        ("avasaram", 1.0),
        ("weekly", 1.5),
        ("weekend", 2.0),
        ("njayar", 2.0),
        ("njayarazcha", 2.0),
        ("saniazcha", 1.8),
        ("saniyazcha", 1.8),
        ("budhanazhcha", 1.0),
        ("working", 1.5),
        ("off", 1.2),
        ("closed", 1.8),
        # Malayalam script
        ("ഹോസ്പിറ്റൽ", 1.2),
        ("ആശുപത്രി", 1.5),
        ("തുറക്കും", 1.8),
        ("തുറന്നു", 1.8),
        ("തുറന്നിരിക്കും", 1.8),
        ("അടയ്ക്കും", 1.8),
        ("അവധി", 1.8),
        ("ഞായർ", 2.0),
        ("ഞായറാഴ്ച", 2.0),
        ("ശനി", 1.8),
        ("ശനിയാഴ്ച", 1.8),
    ],

    # ── Emergency ────────────────────────────────────────────────────────────
    INTENT_EMERGENCY: [
        ("emergency", 2.5),
        ("emergancy", 2.0),
        ("urgent", 2.0),
        ("accident", 2.0),
        ("casualty", 2.0),
        ("icu", 2.0),
        ("trauma", 1.8),
        ("ambulance", 2.0),
        ("24", 1.5),
        ("24x7", 2.0),
        ("24hours", 2.0),
        ("night", 1.0),
        ("rathri", 0.8),
        ("athyavasyam", 2.0),
        ("parisrama", 1.0),
        ("prasavam", 1.5),
        ("heart", 1.8),
        ("chest", 1.2),
        ("breathing", 1.5),
        ("blood", 1.2),
        # Malayalam script
        ("എമർജൻസി", 2.5),
        ("അത്യാവശ്യം", 2.5),
        ("ആംബുലൻസ്", 2.0),
        ("അപകടം", 2.0),
    ],

    # ── Location ─────────────────────────────────────────────────────────────
    INTENT_LOCATION: [
        ("address", 2.0),
        ("location", 2.0),
        ("located", 1.8),
        ("where", 1.8),
        ("how to reach", 2.0),
        ("how to come", 1.8),
        ("ethi", 1.5),
        ("evide", 2.0),
        ("evideyanu", 2.0),
        ("varthamanathil", 0.8),
        ("map", 1.5),
        ("google", 1.2),
        ("way", 1.2),
        ("road", 1.0),
        ("route", 1.2),
        ("varo", 1.2),
        ("varuvaan", 1.2),
        ("naattu", 0.8),
        ("district", 1.0),
        ("near", 1.0),
        ("landmark", 1.2),
        ("pincode", 1.0),
        ("pin", 0.8),
        # Malayalam script
        ("എവിടെ", 2.0),
        ("എവിടെയാ", 2.0),
        ("എവിടെയാണ്", 2.0),
        ("വിലാസം", 2.0),
        ("സ്ഥലം", 1.5),
        ("അഡ്രസ്", 1.8),
    ],

    # ── Contact ──────────────────────────────────────────────────────────────
    INTENT_CONTACT: [
        ("number", 1.8),
        ("phone", 1.8),
        ("contact", 2.0),
        ("call", 1.5),
        ("telephone", 1.5),
        ("mobile", 1.2),
        ("ethelpanikku", 1.5),
        ("direct", 1.0),
        ("whatsapp", 1.5),
        ("reception", 1.8),
        ("helpline", 2.0),
        ("hotline", 2.0),
        # Malayalam script
        ("നമ്പർ", 2.0),
        ("ഫോൺ", 2.0),
        ("ഫോൺനമ്പർ", 2.5),
        ("വിളിക്കാൻ", 1.5),
    ],

    # ── Human transfer ───────────────────────────────────────────────────────
    INTENT_HUMAN_TRANSFER: [
        ("staff", 2.0),
        ("nurse", 1.8),
        ("receptionist", 2.0),
        ("human", 2.0),
        ("person", 1.5),
        ("transfer", 2.0),
        ("connect", 1.8),
        ("talk to", 2.0),
        ("speak to", 2.0),
        ("oru aaline", 2.0),       # "a person" in Malayalam
        ("aalkku", 1.5),
        ("aale", 1.5),
        ("aalu", 1.5),
        ("operator", 2.0),
    ],

    # ── Repeat ───────────────────────────────────────────────────────────────
    INTENT_REPEAT: [
        ("repeat", 2.0),
        ("again", 1.8),
        ("paranju", 1.5),         # "say again" in Malayalam
        ("onnum", 1.0),
        ("veedum", 1.5),          # "again"
        ("vendiyum", 1.5),        # "again"
        ("again paranjalo", 1.8),
        ("keto", 1.5),            # "heard?" / colloquial for repeat
        ("ketto", 1.5),
        ("clear", 1.2),
        ("understand", 1.2),
        ("manasilayi", 1.5),      # "understood" (confirming?)
        ("manassilaayilla", 1.8), # "didn't understand"
    ],

    # ── Symptom / health complaint (route to right dept) ─────────────────────
    # These keywords fire when the caller describes their condition rather than
    # naming a specific department or asking about schedules/fees.
    # Bigrams like "chest pain", "stomach ache" score higher than unigrams
    # so they reliably beat INTENT_EMERGENCY's "chest" (w=1.2).
    INTENT_SYMPTOM: [
        # Strong unambiguous signals
        ("symptom", 2.0),
        ("symptoms", 2.0),
        ("vedana", 1.8),          # pain (Malayalam)
        ("kayanam", 1.8),         # ache (Malayalam)
        ("thalavedana", 2.0),     # headache
        ("thalakayanam", 2.0),    # headache
        ("vayar vedana", 2.2),    # stomach pain (bigram)
        ("vayarkayanam", 2.0),    # stomach ache
        ("ellu vedana", 2.2),     # bone pain (bigram)
        ("muzhu vedana", 2.2),    # knee pain (bigram)
        ("kaan vedana", 2.2),     # ear pain (bigram)
        ("kannu vedana", 2.2),    # eye pain (bigram)
        ("pallu vedana", 2.2),    # tooth pain (bigram)
        ("neriv vedana", 2.2),    # chest pain (bigram)
        # English symptom bigrams — trigger the structured handler which asks
        # the right clarifying question for ambiguous cases (chest pain, etc.)
        ("chest pain", 2.2),
        ("stomach pain", 2.2),
        ("stomach ache", 2.2),
        ("knee pain", 2.2),
        ("back pain", 2.2),
        ("head pain", 2.2),
        ("ear pain", 2.2),
        ("eye pain", 2.2),
        ("tooth pain", 2.2),
        ("joint pain", 2.2),
        ("body pain", 2.0),
        ("chest ache", 2.0),
        # Specific conditions / symptoms
        ("migraine", 2.0),
        ("seizure", 1.8),
        ("palpitation", 1.8),
        ("palpitations", 1.8),
        ("dizziness", 1.8),
        ("swelling", 1.8),
        ("bleeding", 1.5),
        ("infection", 1.5),
        ("rash", 1.8),
        ("itching", 1.8),
        ("poochuvili", 2.0),      # itching (Malayalam)
        ("ithira", 1.8),          # breathlessness (Malayalam)
        ("jwaram", 1.8),          # fever (Malayalam)
        ("sukhamilla", 2.0),      # not well (Malayalam)
        ("rogam", 1.5),           # disease (Malayalam)
        # Moderate – combined with body-part term they become symptom queries
        ("pain", 1.2),
        ("ache", 1.2),
        ("aching", 1.2),
        ("hurt", 1.2),
        ("hurting", 1.2),
        ("burning", 1.0),
        ("soreness", 1.2),
        ("sore", 1.0),
        ("injured", 1.5),
        ("injury", 1.5),
        ("wound", 1.5),
        ("fracture", 1.5),
        ("suffering", 1.5),
        ("sick", 1.0),
        ("unwell", 1.5),
        # Malayalam script
        ("വേദന", 2.0),
        ("കായനം", 2.0),
        ("അസുഖം", 1.8),
        ("രോഗം", 1.5),
        ("സുഖമില്ല", 2.0),
        ("ചൊറിച്ചിൽ", 2.0),
        ("ഒമ", 1.8),
        # ── Additional English symptom terms ────────────────────────────────
        ("shortness of breath", 2.0), ("difficulty breathing", 2.0),
        ("coughing blood", 2.5), ("blood in urine", 2.5), ("blood in stool", 2.5),
        ("chest tightness", 2.2), ("chest heaviness", 2.2),
        ("frequent urination", 2.0), ("painful urination", 2.0),
        ("blurred vision", 2.0), ("double vision", 2.0),
        ("numbness", 1.8), ("tingling", 1.8),
        ("paralysis", 2.0), ("weakness one side", 2.0),
        ("memory loss", 2.0), ("confusion", 1.8),
        ("loss of appetite", 1.8), ("weight loss sudden", 2.0),
        ("night sweats", 1.8), ("high fever", 2.0),
        ("rash", 1.5), ("discharge", 1.5),
        ("lump", 2.0), ("lump breast", 2.2), ("lump neck", 2.2),
        # ── Manglish symptom terms ────────────────────────────────────────
        ("maarpu vedana", 2.2), ("maarpu kayanam", 2.2),
        ("kaal vedana", 2.0), ("kai vedana", 2.0),
        ("kaan kayanam", 2.2), ("mookku ozhukal", 2.0),
        ("kaan therippu", 2.0), ("mookku adakkam", 1.8),
        ("gala vedana", 2.0), ("thallu vedana", 2.0),
        ("kannu charayan", 2.0), ("kannu vedana", 2.0),
        ("mutra vedana", 2.2), ("mutrathakku", 2.0),
        ("omi undu", 2.0), ("chavardi", 2.0),
        ("vayar ilittu", 2.0), ("vayar pothuka", 2.0),
        ("raktham varunnu", 2.5), ("raktham potti", 2.5),
        ("thalarcha", 1.8), ("alukkam", 1.8),
        ("mathiram", 1.5), ("pramadam", 1.8),
        ("uthkanda", 1.8), ("vishaadam", 1.8),
        ("thal tharakan", 2.0), ("kaikalil tarippu", 2.0),
        ("ellu moluvu", 2.0), ("sandi kayanam", 2.0),
        ("poochuvili undu", 2.0), ("tol cherayan", 2.0),
        ("chuma", 1.5), ("kapha chuma", 1.8),
        ("nerippu", 1.8), ("neerupppu", 1.8),
    ],

    # ── Goodbye ──────────────────────────────────────────────────────────────
    INTENT_GOODBYE: [
        ("bye", 2.0),
        ("goodbye", 2.0),
        ("thank you", 1.8),
        ("thanks", 1.5),
        ("nanni", 1.5),
        ("okay bye", 2.0),
        ("ok bye", 2.0),
        ("ayi", 1.2),
        ("sheriyayi", 1.5),
        ("end", 1.5),
        ("cut", 1.2),
        ("finish", 1.2),
        ("done", 1.2),
        ("kazhinju", 1.5),
        # Malayalam script
        ("നന്ദി", 1.8),
        ("വിട", 2.0),
        ("ശരി", 1.5),
        ("കഴിഞ്ഞു", 1.5),
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Department entity synonyms
# Used for entity extraction after intent is known
# ─────────────────────────────────────────────────────────────────────────────

DEPARTMENT_SYNONYMS: Dict[str, List[str]] = {
    "dentist": [
        "dentist", "dental", "tooth", "teeth", "pallu", "pallinu",
        "palldoctor", "orthodontic", "orthodontist", "danthachikitsa",
        "dantham", "palluvaidyan",
        # expanded
        "cavity", "decay", "caries", "root canal", "crown", "filling",
        "extraction", "implant", "scaling", "sensitivity", "grinding",
        "tmj", "jaw pain", "gum bleed", "gum pain", "gingivitis",
        "periodontitis", "wisdom tooth", "danthavaidyan", "dantachikitsa",
        "pallu poyi", "pallu kothikkunnu", "pallu alichal", "pallu undu",
        "pal vedana", "oralrogam",
    ],
    "orthopedic": [
        "orthopedic", "ortho", "bone", "ellu", "ellinu", "joint",
        "spine", "fracture", "fracchar",
        # expanded
        "orthopaedic", "acl", "mcl", "meniscus", "ligament", "tendon",
        "dislocation", "sprain", "strain", "spondylosis", "spondylitis",
        "disc problem", "lumbar", "cervical", "thoracic", "sciatica",
        "osteoporosis", "osteoarthritis", "gout", "rheumatism", "rheumatoid",
        "physiotherapy", "plaster", "cast", "hip", "knee", "shoulder",
        "elbow", "ankle", "wrist", "muzhu", "iduppu", "thal", "natu",
        "thal kayanam", "kaal kayanam", "kai kayanam", "ellu kayanam",
        "ellu moluvu", "sandi vedana", "nattu kayanam", "kazhuthu kayanam",
        "iduppu kayanam", "mottu", "kaal ottiyathu",
    ],
    "cardiology": [
        "cardiology", "cardio", "heart", "hrudayam", "hridayam",
        "hridayarogi", "cardiac",
        # expanded
        "angina", "coronary", "artery", "pacemaker", "stent", "bypass",
        "angiogram", "ecg", "echo", "echocardiogram", "valve", "mitral",
        "aortic", "arrhythmia", "atrial", "fibrillation", "flutter",
        "heart failure", "heart block", "left side pain", "hrudayarogi",
        "hridayastambhanam", "uchha rakthasammardam", "kunja rakthasammardam",
        "bp", "blood pressure", "rektha", "sammardam", "maarpu vedana",
        "maarpu kayanam", "maarpu irakka", "hridrogam", "hridayam",
    ],
    "ent": [
        "ent", "ear", "nose", "throat", "kaan", "mookk", "thallu",
        "ear nose throat", "otolaryngology",
        # expanded
        "allergy", "allergic", "rhinitis", "polyp", "deviated septum",
        "turbinate", "vertigo", "bppv", "tinnitus", "ringing in ear",
        "blocked ear", "blocked nose", "runny nose", "adenoid", "snoring",
        "sleep apnea", "hoarseness", "voice change", "voice problem",
        "pharyngitis", "laryngitis", "hearing aid", "audiogram", "cochlear",
        "eardrum", "kaan tool", "kaan vazhukkam", "kaan ketta", "kaan adi",
        "mookku adakku", "mookku tharakkuka", "thallu adakkam", "gala vedana",
        "sinus kayanam", "thalakkatika", "dysphonia",
    ],
    "gynecology": [
        "gynecology", "gynecologist", "gynaecology", "obs", "obstetrics",
        "delivery", "prasavam", "strirog", "streerog", "women",
        "maternity",
        # STT transcription variants (Sarvam sometimes drops trailing nasal)
        "prasava", "prasava vibagham", "prasava ward",
        "gyne", "gyna", "gynec",
        # Malayalam/Manglish variants
        "stree roga", "sthree roga", "sthreeroga", "sthree rogam",
        "mahila vibagham", "mahila ward", "mahila rogam", "mahila",
        "ladies ward", "ladies doctor", "ladies",
        "women doctor", "women ward",
        # expanded
        "uterus", "ovary", "fallopian", "cervix", "menstruation", "period",
        "dysmenorrhea", "amenorrhea", "menopause", "hysterectomy",
        "c-section", "normal delivery", "fertility", "infertility", "ivf",
        "iud", "contraception", "breast lump", "mammogram", "pap smear",
        "leucorrhea", "white discharge", "vaginal discharge", "pcos", "pcod",
        "endometriosis", "fibroid", "cyst", "ovarian cyst", "garbhakosham",
        "masam kuthi", "artavam", "artava kramakettu", "stree rogam",
        "mahilarogam", "pregnant aano", "kutti undu",
        # phonetic STT variants
        "jinecology", "ginecology", "gynacology",
    ],
    "ophthalmology": [
        "ophthalmology", "eye", "kannu", "kanninu", "ophthal",
        "vision", "eye doctor", "nethram",
        # expanded
        "cataract", "glaucoma", "retina", "cornea", "conjunctivitis",
        "uveitis", "dry eye", "watery eye", "red eye", "itchy eye",
        "blurred vision", "double vision", "floaters", "flashes",
        "night blindness", "squint", "strabismus", "ptosis", "eyelid",
        "color blind", "netra rogam", "kannu charayan", "kannu cholinja",
        "kannu chuvakkunnu", "kannu titippu", "kannu velicham kuranju",
        "kazhcha kuranju", "kann vedana", "kannilninnu water varunu",
    ],
    "pediatrics": [
        "pediatrics", "paediatrics", "child", "kutta", "kuttinu",
        "baby", "balan", "balacikitsa", "kids",
        # expanded
        "neonatal", "vaccination", "immunization", "growth problem",
        "developmental delay", "autism", "adhd", "cerebral palsy",
        "jaundice in newborn", "colic", "teething", "diaper rash",
        "convulsion in child", "febrile seizure", "child care", "bachanu",
        "kunninu", "kuttiku pani", "kutti dokthar", "bala rogam",
        "balya rogam", "shishu poshana", "kutti vomiting",
        "kutti loose motion", "kutti kayanam",
    ],
    "dermatology": [
        "dermatology", "skin", "charma", "charmam", "rog",
        "derma", "skincare",
        # expanded
        "eczema", "atopic", "psoriasis", "vitiligo", "urticaria", "hives",
        "contact dermatitis", "fungal infection", "ringworm", "scabies",
        "impetigo", "cellulitis", "abscess", "boil", "carbuncle", "warts",
        "molluscum", "alopecia", "dandruff", "hair fall", "hair loss",
        "rosacea", "hyperpigmentation", "dark spots", "keloid",
        "sebaceous cyst", "daadru", "theechil", "cholachil", "tol rogam",
        "tol kayanam", "nootha", "kariyappu", "therippu", "charmarogam",
        "charma kayanam", "seborrhea", "miliaria", "prickly heat",
        "heatrash", "tinea",
    ],
    "neurology": [
        "neurology", "neuro", "brain", "thalach", "nerve",
        "stroke", "epilepsy",
        # expanded
        "cerebral", "meningitis", "encephalitis", "alzheimer", "dementia",
        "parkinson", "tremor", "balance", "coordination", "paralysis",
        "hemiplegia", "paraplegia", "facial palsy", "bells palsy",
        "trigeminal neuralgia", "neuropathy", "multiple sclerosis",
        "blackout", "fainting", "loss of consciousness", "memory loss",
        "confusion", "cognitive decline", "convulsion", "fit", "numbness",
        "tingling", "one side weakness", "one side pain", "oru bhagam",
        "kaikal tarippu", "thal tharakan", "manasikarogam", "madhi kuranju",
        "neramppu", "medu",
    ],
    "general": [
        "general", "gp", "general medicine", "general physician",
        "samanya", "fever", "pani", "penam",
        # expanded
        "checkup", "annual physical", "health checkup", "routine checkup",
        "master health", "flu", "influenza", "viral", "cold", "cough",
        "low grade fever", "mild fever", "weakness", "fatigue",
        "dehydration", "allergy", "food allergy", "obesity", "weight gain",
        "weight loss", "loss of appetite", "nausea", "general weakness",
        "thalarcha", "alukkam", "sukhamilla", "mithyam", "thabhathi",
        "bukhar", "jwaram", "ushnam kooduthal", "thandal", "pani undu",
    ],
    "psychiatry": [
        "psychiatry", "mental", "psychology", "manassastra",
        "depression", "anxiety",
        # expanded
        "mdd", "anxiety disorder", "ocd", "ptsd", "schizophrenia",
        "psychosis", "bipolar", "mania", "personality disorder",
        "eating disorder", "anorexia", "bulimia", "addiction", "alcoholism",
        "insomnia", "sleep disorder", "panic disorder", "phobia", "burnout",
        "self harm", "suicidal", "attention disorder", "vishaadam",
        "utkandha", "bhranthu", "manasiku vedana", "manassu kayanam",
        "nervous breakdown", "mental stress", "manasika rogam",
        "uyir thalavariche",
    ],
    "urology": [
        "urology", "kidney", "mutra", "mootram", "bladder",
        # expanded
        "prostate", "bph", "prostatitis", "uti", "kidney stone",
        "renal calculi", "kidney failure", "dialysis", "nephritis",
        "cystitis", "urethritis", "hematuria", "blood in urine",
        "incontinence", "frequent urination", "painful urination",
        "dysuria", "impotence", "ed", "erectile dysfunction",
        "male infertility", "scrotal pain", "testicular", "epididymitis",
        "hydrocele", "varicocele", "mutra vedana", "mutrathakku",
        "mutra pidutham", "mootram varunnilla", "mutra rogam",
        "kidni stone", "renal",
    ],
    "gastroenterology": [
        "gastro", "stomach", "vayar", "digestive", "liver",
        "kalal", "intestine",
        # expanded
        "gerd", "acid reflux", "acidity", "heartburn", "gastritis",
        "peptic ulcer", "ibs", "ibd", "crohn", "colitis", "celiac",
        "pancreatitis", "gallstone", "cholecystitis", "hepatitis",
        "fatty liver", "cirrhosis", "jaundice", "appendicitis", "hernia",
        "anal fissure", "hemorrhoids", "piles", "rectal bleeding",
        "blood in stool", "dysentery", "food poisoning", "dyspepsia",
        "bloating", "kiral rogam", "kiral vedana", "karal rogam",
        "vayar ilittukayanam", "vayar pothuka", "malabandham",
        "muthal rogam", "mala rogam", "raktham potti", "vayar ketti",
    ],
    "pulmonology": [
        "pulmonology", "lung", "chest", "respiration", "asthma",
        "breathing",
        # expanded
        "copd", "emphysema", "chronic bronchitis", "pneumonia",
        "tuberculosis", "tb", "bronchiectasis", "pulmonary fibrosis",
        "hemoptysis", "coughing blood", "wheezing", "stridor", "dyspnea",
        "breathless", "inhaler", "nebulizer", "spirometry", "lung function",
        "oxygen level", "spo2", "uksham", "shwasam", "kashi", "kapanam",
        "kapha chuma", "swaasam ellatha", "maarpu irakka vedana",
    ],
    "oncology": [
        "oncology", "cancer", "tumor", "arbudham",
        # expanded
        "malignant", "benign", "biopsy", "staging", "chemotherapy",
        "radiation", "radiotherapy", "immunotherapy", "lymphoma",
        "leukemia", "myeloma", "blood cancer", "breast cancer",
        "lung cancer", "colon cancer", "prostate cancer", "liver cancer",
        "skin cancer", "metastasis", "lymph node", "arbhutham", "kansar",
        "katina rogam",
    ],
    "radiology": [
        "radiology", "xray", "x-ray", "scan", "mri", "ct", "ct scan",
        "ultrasound",
        # expanded
        "mammography", "fluoroscopy", "angiography", "bone density",
        "dexa", "doppler", "nuclear scan", "pet scan", "image guided",
    ],
    "physiotherapy": [
        "physiotherapy", "physio", "rehabilitation", "rehab",
        "exercise",
        # expanded
        "post surgery", "stroke rehab", "sports injury", "back exercise",
        "mobility", "pain management", "muscle strength", "balance training",
        "gait training", "occupational therapy", "speech therapy",
    ],
    "emergency": [
        "emergency", "casualty", "accident", "urgent",
    ],
    "icu": [
        "icu", "intensive care", "critical",
    ],
    "lab": [
        "lab", "laboratory", "blood test", "test", "pathology",
        "parikshanam",
        # expanded
        "cbc", "complete blood count", "urine test", "stool test",
        "culture", "sensitivity", "esr", "crp", "liver function", "lft",
        "kidney function", "kft", "rft", "thyroid test", "tsh", "t3", "t4",
        "blood sugar", "hba1c", "lipid", "cholesterol", "triglycerides",
        "uric acid", "creatinine", "serum", "plasma", "electrolytes",
        "rektha parikshanam",
    ],
    "pharmacy": [
        "pharmacy", "medical", "medicine", "mrundu", "marundhu",
        "drug", "chemist",
        # expanded
        "tablet", "capsule", "syrup", "injection", "ointment", "cream",
        "drops", "prescription refill", "generic", "brand", "side effects",
        "dosage", "marundhu", "medicine name", "tablet name",
        "injection edukkanam",
    ],
    # ── New departments ──────────────────────────────────────────────────────
    "endocrinology": [
        "endocrinology", "endocrine", "endocrinologist",
        "diabetes", "type 1", "type 2", "blood sugar", "insulin", "hba1c",
        "thyroid", "hypothyroid", "hyperthyroid", "goiter", "cushing",
        "addison", "hormone imbalance", "hormonal problem", "pcod related",
        "praameham", "sugar rogam", "thyroid rogam", "thyroid kayanam",
        "thyroid undakkam", "madhu rogam", "blood sugar kooduthal",
        "blood sugar kuranju",
    ],
    "nephrology": [
        "nephrology", "nephrologist",
        "kidney disease", "ckd", "chronic kidney", "nephrotic syndrome",
        "nephritis", "renal failure", "creatinine", "proteinuria",
        "kidney function", "vrikka rogam", "kidni rogam", "kidni failure",
        "dialysis cheyyaan", "vrikka",
    ],
    "rheumatology": [
        "rheumatology", "rheumatologist",
        "rheumatoid arthritis", "ra", "lupus", "sle", "fibromyalgia",
        "ankylosing spondylitis", "auto-immune", "joint inflammation",
        "myositis", "vasculitis", "scleroderma", "uric acid high",
        "podagra", "rheumatism", "kallu", "mootrathil kallu",
    ],
    "hematology": [
        "hematology", "haematology", "hematologist",
        "anemia", "anaemia", "thalassemia", "sickle cell", "hemophilia",
        "blood disorder", "low hemoglobin", "low platelets",
        "bleeding disorder", "blood clot", "dvt", "thrombosis",
        "cbc abnormal", "iron deficiency", "b12 deficiency", "hemoglobin",
        "rektha rogam", "blood count kurachu",
    ],
}


# Build reverse lookup: keyword → canonical department name
# O(1) lookup after build
_DEPT_KEYWORD_INDEX: Dict[str, str] = {}

for _dept, _keywords in DEPARTMENT_SYNONYMS.items():
    for _kw in _keywords:
        _DEPT_KEYWORD_INDEX[_kw.lower()] = _dept


def resolve_department(text: str) -> Optional[str]:
    """
    Map a text token to a canonical department name.
    Returns None if no match found.
    O(1) lookup.
    """
    return _DEPT_KEYWORD_INDEX.get(text.lower())


def get_department_synonyms(dept: str) -> List[str]:
    """Get all synonyms for a canonical department name."""
    return DEPARTMENT_SYNONYMS.get(dept.lower(), [])


# ─────────────────────────────────────────────────────────────────────────────
# Day reference mapping
# ─────────────────────────────────────────────────────────────────────────────

DAY_KEYWORDS: Dict[str, str] = {
    # English
    "monday": "monday",
    "tuesday": "tuesday",
    "wednesday": "wednesday",
    "thursday": "thursday",
    "friday": "friday",
    "saturday": "saturday",
    "sunday": "sunday",
    "today": "today",
    "tomorrow": "tomorrow",
    "yesterday": "yesterday",
    # Malayalam
    "njayarazcha": "sunday",
    "njayar": "sunday",
    "thinkal": "monday",
    "thinkalazcha": "monday",
    "chowwa": "tuesday",
    "chowwaazcha": "tuesday",
    "budhanazhcha": "wednesday",
    "budhan": "wednesday",
    "vyazham": "thursday",
    "vyaazhazhcha": "thursday",
    "velli": "friday",
    "velliazcha": "friday",
    "saniazcha": "saturday",
    "saniyazcha": "saturday",
    "sanji": "saturday",
    "innu": "today",
    "innale": "yesterday",
    "nale": "tomorrow",
}


def resolve_day(text: str) -> Optional[str]:
    """Map a day keyword to a normalized day name."""
    return DAY_KEYWORDS.get(text.lower())
