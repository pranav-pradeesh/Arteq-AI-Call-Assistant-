"""
Hospital Knowledge Service — queries the real Supabase schema.

All answers come from structured DB data. No hallucination.

Query strategy:
  - department / doctor info  → departments + doctors + schedules tables
  - fees                      → billing_info table
  - emergency                 → emergency_contacts table
  - location / timing         → hospitals table
  - general FAQ               → faqs table (tag-based lookup)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

import pytz

from src.config.settings import settings
from src.db.queries import (
    DoctorInfo, HospitalContext,
    _DB_DOW_NAMES, _DAY_ML, named_dow_to_db, today_db_dow,
)
from src.intent.keywords import (
    INTENT_CONSULTATION_FEE, INTENT_CONTACT, INTENT_DEPARTMENT_EXISTS,
    INTENT_DOCTOR_AVAILABILITY, INTENT_DOCTOR_TIMING, INTENT_EMERGENCY,
    INTENT_HOSPITAL_TIMING, INTENT_LOCATION, INTENT_SYMPTOM,
)

INDIA_TZ = pytz.timezone("Asia/Kolkata")


@dataclass
class KnowledgeResult:
    intent: str
    found: bool
    text_ml: str = ""      # Ready-to-speak Malayalam answer (primary)
    text_en: str = ""      # English fallback
    data: dict = field(default_factory=dict)
    missing: Optional[str] = None   # what we couldn't resolve
    # Legacy alias kept for backward compat with older tests
    missing_entity: Optional[str] = None


# ── Department keyword → dept_name normaliser ─────────────────────────────────
#
# Two purposes:
#   1. Map dialect terms ("kaan", "hridayam") to canonical names so we can
#      look them up in the actual department list.
#   2. Cover services the hospital DOESN'T offer (dentist, derma, etc.) so
#      we can recognise them and deny clearly instead of falling through to
#      "I didn't understand → connect to receptionist".

_DEPT_KEYWORDS: dict[str, str] = {
    # ── General medicine ──────────────────────────────────────────────────────
    "general": "general medicine", "gp": "general medicine",
    "fever": "general medicine", "medicine": "general medicine",
    "general medicine": "general medicine", "general physician": "general medicine",
    "samanya": "general medicine", "pani": "general medicine",
    "checkup": "general medicine", "annual physical": "general medicine",
    "health checkup": "general medicine", "routine checkup": "general medicine",
    "master health": "general medicine", "flu": "general medicine",
    "influenza": "general medicine", "viral": "general medicine",
    "cold": "general medicine", "cough": "general medicine",
    "low grade fever": "general medicine", "mild fever": "general medicine",
    "weakness": "general medicine", "fatigue": "general medicine",
    "dehydration": "general medicine", "food allergy": "general medicine",
    "obesity": "general medicine", "weight gain": "general medicine",
    "weight loss": "general medicine", "loss of appetite": "general medicine",
    "nausea": "general medicine", "general weakness": "general medicine",
    "thalarcha": "general medicine", "alukkam": "general medicine",
    "sukhamilla": "general medicine", "mithyam": "general medicine",
    "thabhathi": "general medicine", "bukhar": "general medicine",
    "jwaram": "general medicine", "ushnam kooduthal": "general medicine",
    "thandal": "general medicine", "pani undu": "general medicine",
    # ── Cardiology ────────────────────────────────────────────────────────────
    "cardio": "cardiology", "cardiology": "cardiology",
    "heart": "cardiology", "hridayam": "cardiology", "hrudayam": "cardiology",
    "cardiac": "cardiology", "hridayarogi": "cardiology",
    "angina": "cardiology", "coronary": "cardiology", "artery": "cardiology",
    "pacemaker": "cardiology", "stent": "cardiology", "bypass": "cardiology",
    "angiogram": "cardiology", "ecg": "cardiology",
    "echocardiogram": "cardiology", "valve": "cardiology",
    "mitral": "cardiology", "aortic": "cardiology", "arrhythmia": "cardiology",
    "atrial": "cardiology", "fibrillation": "cardiology", "flutter": "cardiology",
    "heart failure": "cardiology", "heart block": "cardiology",
    "left side pain": "cardiology", "hridayastambhanam": "cardiology",
    "uchha rakthasammardam": "cardiology", "kunja rakthasammardam": "cardiology",
    "bp": "cardiology", "blood pressure": "cardiology",
    "high blood pressure": "cardiology", "hypertension": "cardiology",
    "rektha": "cardiology", "sammardam": "cardiology",
    "maarpu vedana": "cardiology", "maarpu kayanam": "cardiology",
    "maarpu irakka": "cardiology", "hridrogam": "cardiology",
    "hridayam": "cardiology", "maarpu": "cardiology",
    "palpitation": "cardiology", "palpitations": "cardiology",
    "chest tightness": "cardiology", "chest heaviness": "cardiology",
    "left arm pain": "cardiology", "jaw pain with chest": "cardiology",
    # ── ENT ───────────────────────────────────────────────────────────────────
    "ent": "ent", "ear": "ent", "nose": "ent", "throat": "ent",
    "kaan": "ent", "mookku": "ent", "thallu": "ent",
    "otolaryngology": "ent", "ear nose throat": "ent",
    "allergy": "ent", "allergic": "ent", "rhinitis": "ent",
    "polyp": "ent", "deviated septum": "ent", "turbinate": "ent",
    "vertigo": "ent", "bppv": "ent", "tinnitus": "ent",
    "ringing in ear": "ent", "blocked ear": "ent", "blocked nose": "ent",
    "runny nose": "ent", "adenoid": "ent", "snoring": "ent",
    "sleep apnea": "ent", "hoarseness": "ent", "voice change": "ent",
    "voice problem": "ent", "pharyngitis": "ent", "laryngitis": "ent",
    "hearing aid": "ent", "audiogram": "ent", "cochlear": "ent",
    "eardrum": "ent", "kaan tool": "ent", "kaan vazhukkam": "ent",
    "kaan ketta": "ent", "kaan adi": "ent", "mookku adakku": "ent",
    "mookku tharakkuka": "ent", "thallu adakkam": "ent", "gala vedana": "ent",
    "sinus kayanam": "ent", "thalakkatika": "ent", "dysphonia": "ent",
    "sinusitis": "ent", "tonsil": "ent", "tonsils": "ent",
    "nose bleed": "ent", "nosebleed": "ent", "hearing loss": "ent",
    "hearing problem": "ent", "mookku ozhukal": "ent", "mookku adakkam": "ent",
    "kaan kayanam": "ent", "kaan therippu": "ent", "thallu vedana": "ent",
    "thallu kayanam": "ent", "gala kayanam": "ent",
    # ── Orthopedics ───────────────────────────────────────────────────────────
    "ortho": "orthopedics", "orthopedic": "orthopedics",
    "orthopaedic": "orthopedics", "orthopedics": "orthopedics",
    "bone": "orthopedics", "ellu": "orthopedics", "joint": "orthopedics",
    "fracture": "orthopedics", "spine": "orthopedics",
    "acl": "orthopedics", "mcl": "orthopedics", "meniscus": "orthopedics",
    "ligament": "orthopedics", "tendon": "orthopedics",
    "dislocation": "orthopedics", "sprain": "orthopedics",
    "strain": "orthopedics", "spondylosis": "orthopedics",
    "spondylitis": "orthopedics", "disc problem": "orthopedics",
    "lumbar": "orthopedics", "cervical": "orthopedics",
    "thoracic": "orthopedics", "sciatica": "orthopedics",
    "osteoporosis": "orthopedics", "osteoarthritis": "orthopedics",
    "arthritis": "orthopedics", "rheumatism": "orthopedics",
    "plaster": "orthopedics", "cast": "orthopedics",
    "hip": "orthopedics", "knee": "orthopedics", "shoulder": "orthopedics",
    "elbow": "orthopedics", "ankle": "orthopedics", "wrist": "orthopedics",
    "muzhu": "orthopedics", "iduppu": "orthopedics", "thal": "orthopedics",
    "natu": "orthopedics", "thal kayanam": "orthopedics",
    "kaal kayanam": "orthopedics", "kai kayanam": "orthopedics",
    "ellu kayanam": "orthopedics", "ellu moluvu": "orthopedics",
    "sandi vedana": "orthopedics", "nattu kayanam": "orthopedics",
    "kazhuthu kayanam": "orthopedics", "iduppu kayanam": "orthopedics",
    "mottu": "orthopedics", "kaal ottiyathu": "orthopedics",
    "sandi kayanam": "orthopedics", "kaal vedana": "orthopedics",
    "ellu vedana": "orthopedics", "muzhu vedana": "orthopedics",
    # ── Pediatrics ────────────────────────────────────────────────────────────
    "pediatrics": "pediatrics", "paediatrics": "pediatrics",
    "child": "pediatrics", "kutta": "pediatrics", "kuttinu": "pediatrics",
    "baby": "pediatrics", "balan": "pediatrics", "kids": "pediatrics",
    "neonatal": "pediatrics", "vaccination": "pediatrics",
    "immunization": "pediatrics", "growth problem": "pediatrics",
    "developmental delay": "pediatrics", "autism": "pediatrics",
    "cerebral palsy": "pediatrics", "jaundice in newborn": "pediatrics",
    "colic": "pediatrics", "teething": "pediatrics", "diaper rash": "pediatrics",
    "convulsion in child": "pediatrics", "febrile seizure": "pediatrics",
    "child care": "pediatrics", "bachanu": "pediatrics",
    "kunninu": "pediatrics", "kuttiku pani": "pediatrics",
    "kutti dokthar": "pediatrics", "bala rogam": "pediatrics",
    "balya rogam": "pediatrics", "shishu poshana": "pediatrics",
    "kutti vomiting": "pediatrics", "kutti loose motion": "pediatrics",
    "kutti kayanam": "pediatrics", "newborn": "pediatrics",
    "infant": "pediatrics",
    # ── Gynecology ────────────────────────────────────────────────────────────
    "gynaecology": "gynaecology", "gynecology": "gynaecology",
    "gynae": "gynaecology", "obs": "gynaecology", "delivery": "gynaecology",
    "prasavam": "gynaecology", "maternity": "gynaecology", "women": "gynaecology",
    "obstetrics": "gynaecology", "gynecologist": "gynaecology",
    "uterus": "gynaecology", "ovary": "gynaecology", "fallopian": "gynaecology",
    "cervix": "gynaecology", "menstruation": "gynaecology", "period": "gynaecology",
    "dysmenorrhea": "gynaecology", "amenorrhea": "gynaecology",
    "menopause": "gynaecology", "hysterectomy": "gynaecology",
    "c-section": "gynaecology", "normal delivery": "gynaecology",
    "fertility": "gynaecology", "infertility": "gynaecology",
    "ivf": "gynaecology", "iud": "gynaecology", "contraception": "gynaecology",
    "breast lump": "gynaecology", "mammogram": "gynaecology",
    "pap smear": "gynaecology", "leucorrhea": "gynaecology",
    "white discharge": "gynaecology", "vaginal discharge": "gynaecology",
    "pcos": "gynaecology", "pcod": "gynaecology",
    "endometriosis": "gynaecology", "fibroid": "gynaecology",
    "cyst": "gynaecology", "ovarian cyst": "gynaecology",
    "garbhakosham": "gynaecology", "masam kuthi": "gynaecology",
    "artavam": "gynaecology", "artava kramakettu": "gynaecology",
    "stree rogam": "gynaecology", "mahilarogam": "gynaecology",
    "pregnant aano": "gynaecology", "kutti undu": "gynaecology",
    "pregnancy": "gynaecology", "pregnant": "gynaecology",
    "menstrual problem": "gynaecology", "period problem": "gynaecology",
    "garba": "gynaecology",
    # ── Ophthalmology ─────────────────────────────────────────────────────────
    "eye": "ophthalmology", "ophthal": "ophthalmology",
    "ophthalmology": "ophthalmology", "kannu": "ophthalmology",
    "nethram": "ophthalmology", "vision": "ophthalmology",
    "cataract": "ophthalmology", "glaucoma": "ophthalmology",
    "retina": "ophthalmology", "cornea": "ophthalmology",
    "conjunctivitis": "ophthalmology", "uveitis": "ophthalmology",
    "dry eye": "ophthalmology", "watery eye": "ophthalmology",
    "red eye": "ophthalmology", "itchy eye": "ophthalmology",
    "blurred vision": "ophthalmology", "double vision": "ophthalmology",
    "floaters": "ophthalmology", "flashes": "ophthalmology",
    "night blindness": "ophthalmology", "squint": "ophthalmology",
    "strabismus": "ophthalmology", "ptosis": "ophthalmology",
    "eyelid": "ophthalmology", "color blind": "ophthalmology",
    "netra rogam": "ophthalmology", "kannu charayan": "ophthalmology",
    "kannu cholinja": "ophthalmology", "kannu chuvakkunnu": "ophthalmology",
    "kannu titippu": "ophthalmology", "kannu velicham kuranju": "ophthalmology",
    "kazhcha kuranju": "ophthalmology", "kann vedana": "ophthalmology",
    "kannilninnu water varunu": "ophthalmology",
    "eye pain": "ophthalmology", "eye problem": "ophthalmology",
    "blurry vision": "ophthalmology", "vision problem": "ophthalmology",
    "eye redness": "ophthalmology", "watery eyes": "ophthalmology",
    "kannu vedana": "ophthalmology", "kanninu": "ophthalmology",
    # ── Dermatology ───────────────────────────────────────────────────────────
    "derma": "dermatology", "skin": "dermatology", "dermatology": "dermatology",
    "charma": "dermatology", "charmam": "dermatology", "skincare": "dermatology",
    "eczema": "dermatology", "atopic": "dermatology", "psoriasis": "dermatology",
    "vitiligo": "dermatology", "urticaria": "dermatology", "hives": "dermatology",
    "contact dermatitis": "dermatology", "fungal infection": "dermatology",
    "ringworm": "dermatology", "scabies": "dermatology",
    "impetigo": "dermatology", "cellulitis": "dermatology",
    "abscess": "dermatology", "boil": "dermatology", "carbuncle": "dermatology",
    "warts": "dermatology", "molluscum": "dermatology",
    "alopecia": "dermatology", "dandruff": "dermatology",
    "hair fall": "dermatology", "hair loss": "dermatology",
    "rosacea": "dermatology", "hyperpigmentation": "dermatology",
    "dark spots": "dermatology", "keloid": "dermatology",
    "sebaceous cyst": "dermatology", "daadru": "dermatology",
    "theechil": "dermatology", "cholachil": "dermatology",
    "tol rogam": "dermatology", "tol kayanam": "dermatology",
    "nootha": "dermatology", "kariyappu": "dermatology",
    "therippu": "dermatology", "charmarogam": "dermatology",
    "charma kayanam": "dermatology", "seborrhea": "dermatology",
    "miliaria": "dermatology", "prickly heat": "dermatology",
    "heatrash": "dermatology", "tinea": "dermatology",
    "acne": "dermatology", "skin rash": "dermatology",
    "skin problem": "dermatology", "skin disease": "dermatology",
    "skin infection": "dermatology", "itching": "dermatology",
    "poochuvili": "dermatology", "charma rogam": "dermatology",
    "rash": "dermatology", "tol cherayan": "dermatology",
    # ── Neurology ─────────────────────────────────────────────────────────────
    "neuro": "neurology", "neurology": "neurology", "brain": "neurology",
    "stroke": "neurology", "thalach": "neurology", "nerve": "neurology",
    "epilepsy": "neurology", "cerebral": "neurology",
    "meningitis": "neurology", "encephalitis": "neurology",
    "alzheimer": "neurology", "dementia": "neurology",
    "parkinson": "neurology", "tremor": "neurology",
    "balance": "neurology", "coordination": "neurology",
    "paralysis": "neurology", "hemiplegia": "neurology",
    "paraplegia": "neurology", "facial palsy": "neurology",
    "bells palsy": "neurology", "trigeminal neuralgia": "neurology",
    "neuropathy": "neurology", "multiple sclerosis": "neurology",
    "blackout": "neurology", "fainting": "neurology",
    "loss of consciousness": "neurology", "memory loss": "neurology",
    "confusion": "neurology", "cognitive decline": "neurology",
    "convulsion": "neurology", "fit": "neurology",
    "numbness": "neurology", "tingling": "neurology",
    "one side weakness": "neurology", "one side pain": "neurology",
    "oru bhagam": "neurology", "kaikal tarippu": "neurology",
    "thal tharakan": "neurology", "manasikarogam": "neurology",
    "madhi kuranju": "neurology", "neramppu": "neurology",
    "medu": "neurology", "headache": "neurology", "migraine": "neurology",
    "seizure": "neurology", "dizziness": "neurology",
    "thalavedana": "neurology", "thalakayanam": "neurology",
    "thalakkayanam": "neurology", "thalav": "neurology",
    "kaikalil tarippu": "neurology",
    # ── Pulmonology ───────────────────────────────────────────────────────────
    "pulmonology": "pulmonology", "lung": "pulmonology", "asthma": "pulmonology",
    "breathing": "pulmonology", "chest": "pulmonology",
    "copd": "pulmonology", "emphysema": "pulmonology",
    "chronic bronchitis": "pulmonology", "pneumonia": "pulmonology",
    "tuberculosis": "pulmonology", "tb": "pulmonology",
    "bronchiectasis": "pulmonology", "pulmonary fibrosis": "pulmonology",
    "hemoptysis": "pulmonology", "coughing blood": "pulmonology",
    "wheezing": "pulmonology", "stridor": "pulmonology",
    "dyspnea": "pulmonology", "breathless": "pulmonology",
    "inhaler": "pulmonology", "nebulizer": "pulmonology",
    "spirometry": "pulmonology", "lung function": "pulmonology",
    "oxygen level": "pulmonology", "spo2": "pulmonology",
    "uksham": "pulmonology", "shwasam": "pulmonology",
    "kashi": "pulmonology", "kapanam": "pulmonology",
    "kapha chuma": "pulmonology", "swaasam ellatha": "pulmonology",
    "maarpu irakka vedana": "pulmonology",
    "breathing problem": "pulmonology", "shortness of breath": "pulmonology",
    "breathlessness": "pulmonology", "difficulty breathing": "pulmonology",
    "lung problem": "pulmonology", "persistent cough": "pulmonology",
    "chronic cough": "pulmonology", "ithira": "pulmonology",
    "niswasam": "pulmonology",
    # ── Psychiatry ────────────────────────────────────────────────────────────
    "psychiatry": "psychiatry", "mental": "psychiatry",
    "psychology": "psychiatry", "manassastra": "psychiatry",
    "depression": "psychiatry", "anxiety": "psychiatry",
    "mdd": "psychiatry", "anxiety disorder": "psychiatry",
    "ocd": "psychiatry", "ptsd": "psychiatry",
    "schizophrenia": "psychiatry", "psychosis": "psychiatry",
    "bipolar": "psychiatry", "mania": "psychiatry",
    "personality disorder": "psychiatry", "eating disorder": "psychiatry",
    "anorexia": "psychiatry", "bulimia": "psychiatry",
    "addiction": "psychiatry", "alcoholism": "psychiatry",
    "insomnia": "psychiatry", "sleep disorder": "psychiatry",
    "panic disorder": "psychiatry", "phobia": "psychiatry",
    "burnout": "psychiatry", "self harm": "psychiatry",
    "suicidal": "psychiatry", "attention disorder": "psychiatry",
    "vishaadam": "psychiatry", "utkandha": "psychiatry",
    "bhranthu": "psychiatry", "manasiku vedana": "psychiatry",
    "manassu kayanam": "psychiatry", "nervous breakdown": "psychiatry",
    "mental stress": "psychiatry", "manasika rogam": "psychiatry",
    "uyir thalavariche": "psychiatry",
    "mental health": "psychiatry", "panic attack": "psychiatry",
    "sleep problem": "psychiatry", "stress problem": "psychiatry",
    "mental problem": "psychiatry", "adhd": "psychiatry",
    # ── Urology ───────────────────────────────────────────────────────────────
    "urology": "urology", "kidney": "urology", "mutra": "urology",
    "mootram": "urology", "bladder": "urology",
    "prostate": "urology", "bph": "urology", "prostatitis": "urology",
    "uti": "urology", "kidney stone": "urology",
    "renal calculi": "urology", "kidney failure": "urology",
    "nephritis": "urology", "cystitis": "urology",
    "urethritis": "urology", "hematuria": "urology",
    "blood in urine": "urology", "incontinence": "urology",
    "frequent urination": "urology", "painful urination": "urology",
    "dysuria": "urology", "impotence": "urology", "ed": "urology",
    "erectile dysfunction": "urology", "male infertility": "urology",
    "scrotal pain": "urology", "testicular": "urology",
    "epididymitis": "urology", "hydrocele": "urology",
    "varicocele": "urology", "mutra vedana": "urology",
    "mutrathakku": "urology", "mutra pidutham": "urology",
    "mootram varunnilla": "urology", "mutra rogam": "urology",
    "kidni stone": "urology", "renal": "urology",
    "urinary problem": "urology", "urine problem": "urology",
    "kidney pain": "urology", "kidney problem": "urology",
    "kidni": "urology",
    # ── Gastroenterology ──────────────────────────────────────────────────────
    "gastro": "gastroenterology", "stomach": "gastroenterology",
    "vayar": "gastroenterology", "digestive": "gastroenterology",
    "liver": "gastroenterology", "kalal": "gastroenterology",
    "intestine": "gastroenterology", "gastroenterology": "gastroenterology",
    "gerd": "gastroenterology", "acid reflux": "gastroenterology",
    "acidity": "gastroenterology", "heartburn": "gastroenterology",
    "gastritis": "gastroenterology", "peptic ulcer": "gastroenterology",
    "ibs": "gastroenterology", "ibd": "gastroenterology",
    "crohn": "gastroenterology", "colitis": "gastroenterology",
    "celiac": "gastroenterology", "pancreatitis": "gastroenterology",
    "gallstone": "gastroenterology", "cholecystitis": "gastroenterology",
    "hepatitis": "gastroenterology", "fatty liver": "gastroenterology",
    "cirrhosis": "gastroenterology", "jaundice": "gastroenterology",
    "appendicitis": "gastroenterology", "hernia": "gastroenterology",
    "anal fissure": "gastroenterology", "hemorrhoids": "gastroenterology",
    "piles": "gastroenterology", "rectal bleeding": "gastroenterology",
    "blood in stool": "gastroenterology", "dysentery": "gastroenterology",
    "food poisoning": "gastroenterology", "dyspepsia": "gastroenterology",
    "bloating": "gastroenterology", "kiral rogam": "gastroenterology",
    "kiral vedana": "gastroenterology", "karal rogam": "gastroenterology",
    "vayar ilittukayanam": "gastroenterology", "vayar pothuka": "gastroenterology",
    "malabandham": "gastroenterology", "muthal rogam": "gastroenterology",
    "mala rogam": "gastroenterology", "raktham potti": "gastroenterology",
    "vayar ketti": "gastroenterology",
    "stomach pain": "gastroenterology", "stomach ache": "gastroenterology",
    "abdominal pain": "gastroenterology", "abdomen pain": "gastroenterology",
    "loose motion": "gastroenterology", "loose motions": "gastroenterology",
    "diarrhea": "gastroenterology", "diarrhoea": "gastroenterology",
    "constipation": "gastroenterology", "indigestion": "gastroenterology",
    "vomiting": "gastroenterology", "liver problem": "gastroenterology",
    "vayar vedana": "gastroenterology", "vayarkayanam": "gastroenterology",
    "omi": "gastroenterology", "omanarekkayanam": "gastroenterology",
    "liver jaundice": "gastroenterology",
    # ── Oncology ──────────────────────────────────────────────────────────────
    "oncology": "oncology", "cancer": "oncology", "arbudham": "oncology",
    "tumor": "oncology", "malignant": "oncology", "benign": "oncology",
    "biopsy": "oncology", "staging": "oncology", "chemotherapy": "oncology",
    "radiation": "oncology", "radiotherapy": "oncology",
    "immunotherapy": "oncology", "lymphoma": "oncology",
    "leukemia": "oncology", "myeloma": "oncology",
    "blood cancer": "oncology", "breast cancer": "oncology",
    "lung cancer": "oncology", "colon cancer": "oncology",
    "prostate cancer": "oncology", "liver cancer": "oncology",
    "skin cancer": "oncology", "metastasis": "oncology",
    "lymph node": "oncology", "arbhutham": "oncology",
    "kansar": "oncology", "katina rogam": "oncology",
    # ── Dental ────────────────────────────────────────────────────────────────
    "dentist": "dental", "dental": "dental", "tooth": "dental",
    "teeth": "dental", "pallu": "dental", "danthavaidyan": "dental",
    "orthodontic": "dental", "orthodontist": "dental",
    "danthachikitsa": "dental", "dantham": "dental",
    "palluvaidyan": "dental", "cavity": "dental", "decay": "dental",
    "caries": "dental", "root canal": "dental", "crown": "dental",
    "filling": "dental", "extraction": "dental", "implant": "dental",
    "scaling": "dental", "sensitivity": "dental", "grinding": "dental",
    "tmj": "dental", "jaw pain": "dental", "gum bleed": "dental",
    "gum pain": "dental", "gingivitis": "dental", "periodontitis": "dental",
    "wisdom tooth": "dental", "dantachikitsa": "dental",
    "pallu poyi": "dental", "pallu kothikkunnu": "dental",
    "pallu alichal": "dental", "pallu undu": "dental",
    "pal vedana": "dental", "oralrogam": "dental",
    "tooth pain": "dental", "toothache": "dental",
    "tooth ache": "dental", "gum problem": "dental",
    "tooth problem": "dental", "pallu vedana": "dental",
    "pallu kayanam": "dental",
    # ── Radiology ─────────────────────────────────────────────────────────────
    "radiology": "radiology", "xray": "radiology", "x-ray": "radiology",
    "scan": "radiology", "mri": "radiology", "ct": "radiology",
    "ct scan": "radiology", "ultrasound": "radiology",
    "mammography": "radiology", "fluoroscopy": "radiology",
    "angiography": "radiology", "bone density": "radiology",
    "dexa": "radiology", "doppler": "radiology",
    "nuclear scan": "radiology", "pet scan": "radiology",
    "image guided": "radiology",
    # ── Physiotherapy ─────────────────────────────────────────────────────────
    "physio": "physiotherapy", "physiotherapy": "physiotherapy",
    "rehabilitation": "physiotherapy", "rehab": "physiotherapy",
    "exercise": "physiotherapy", "post surgery": "physiotherapy",
    "stroke rehab": "physiotherapy", "sports injury": "physiotherapy",
    "back exercise": "physiotherapy", "mobility": "physiotherapy",
    "pain management": "physiotherapy", "muscle strength": "physiotherapy",
    "balance training": "physiotherapy", "gait training": "physiotherapy",
    "occupational therapy": "physiotherapy", "speech therapy": "physiotherapy",
    # ── Lab ───────────────────────────────────────────────────────────────────
    "lab": "lab", "laboratory": "lab", "blood test": "lab",
    "test": "lab", "pathology": "lab", "parikshanam": "lab",
    "cbc": "lab", "complete blood count": "lab", "urine test": "lab",
    "stool test": "lab", "culture": "lab", "sensitivity": "lab",
    "esr": "lab", "crp": "lab", "liver function": "lab",
    "lft": "lab", "kidney function": "lab", "kft": "lab",
    "rft": "lab", "thyroid test": "lab", "tsh": "lab",
    "t3": "lab", "t4": "lab", "blood sugar": "lab",
    "hba1c": "lab", "lipid": "lab", "cholesterol": "lab",
    "triglycerides": "lab", "uric acid": "lab", "creatinine": "lab",
    "serum": "lab", "plasma": "lab", "electrolytes": "lab",
    "rektha parikshanam": "lab",
    # ── Pharmacy ──────────────────────────────────────────────────────────────
    "pharmacy": "pharmacy", "medical": "pharmacy", "medicine": "general medicine",
    "mrundu": "pharmacy", "marundhu": "pharmacy", "drug": "pharmacy",
    "chemist": "pharmacy", "tablet": "pharmacy", "capsule": "pharmacy",
    "syrup": "pharmacy", "injection": "pharmacy", "ointment": "pharmacy",
    "cream": "pharmacy", "drops": "pharmacy",
    "prescription refill": "pharmacy", "generic": "pharmacy",
    "brand": "pharmacy", "side effects": "pharmacy", "dosage": "pharmacy",
    "medicine name": "pharmacy", "tablet name": "pharmacy",
    "injection edukkanam": "pharmacy",
    # ── Emergency & ICU ───────────────────────────────────────────────────────
    "emergency": "emergency", "casualty": "emergency",
    "accident": "emergency", "urgent": "emergency",
    "icu": "icu", "intensive care": "icu", "critical": "icu",
    "ambulance": "emergency", "trauma": "emergency",
    # ── Ayurveda ─────────────────────────────────────────────────────────────
    "ayurveda": "ayurveda", "ayurvedam": "ayurveda",
    # ── Endocrinology (new) ────────────────────────────────────────────────────
    "endocrinology": "endocrinology", "endocrine": "endocrinology",
    "endocrinologist": "endocrinology", "diabetic": "endocrinology",
    "type 1": "endocrinology", "type 2": "endocrinology",
    "insulin": "endocrinology", "thyroid": "endocrinology",
    "hypothyroid": "endocrinology", "hyperthyroid": "endocrinology",
    "goiter": "endocrinology", "cushing": "endocrinology",
    "addison": "endocrinology", "hormone imbalance": "endocrinology",
    "hormonal problem": "endocrinology", "pcod related": "endocrinology",
    "praameham": "endocrinology", "sugar rogam": "endocrinology",
    "thyroid rogam": "endocrinology", "thyroid kayanam": "endocrinology",
    "thyroid undakkam": "endocrinology", "madhu rogam": "endocrinology",
    "blood sugar kooduthal": "endocrinology",
    "blood sugar kuranju": "endocrinology",
    "hormone": "endocrinology", "hormonal": "endocrinology",
    "praameha rogi": "endocrinology", "sugar patient": "endocrinology",
    "madhurogam": "endocrinology", "thyroid problem": "endocrinology",
    "diabetes": "endocrinology",
    # ── Nephrology (new) ──────────────────────────────────────────────────────
    "nephrology": "nephrology", "nephrologist": "nephrology",
    "ckd": "nephrology", "chronic kidney": "nephrology",
    "nephrotic syndrome": "nephrology", "renal failure": "nephrology",
    "proteinuria": "nephrology", "vrikka rogam": "nephrology",
    "kidni rogam": "nephrology", "kidni failure": "nephrology",
    "dialysis cheyyaan": "nephrology", "vrikka": "nephrology",
    "kidney disease": "nephrology", "dialysis": "nephrology",
    "creatinine high": "nephrology",
    # ── Rheumatology (new) ────────────────────────────────────────────────────
    "rheumatology": "rheumatology", "rheumatologist": "rheumatology",
    "rheumatoid arthritis": "rheumatology", "ra": "rheumatology",
    "lupus": "rheumatology", "sle": "rheumatology",
    "fibromyalgia": "rheumatology", "ankylosing spondylitis": "rheumatology",
    "auto-immune": "rheumatology", "joint inflammation": "rheumatology",
    "myositis": "rheumatology", "vasculitis": "rheumatology",
    "scleroderma": "rheumatology", "uric acid high": "rheumatology",
    "podagra": "rheumatology", "rheumatoid": "rheumatology",
    "kallu": "rheumatology", "mootrathil kallu": "rheumatology",
    "joint swelling": "rheumatology", "gout": "rheumatology",
    # ── Hematology (new) ─────────────────────────────────────────────────────
    "hematology": "hematology", "haematology": "hematology",
    "hematologist": "hematology", "anemia": "hematology",
    "anaemia": "hematology", "thalassemia": "hematology",
    "sickle cell": "hematology", "hemophilia": "hematology",
    "blood disorder": "hematology", "low hemoglobin": "hematology",
    "low platelets": "hematology", "bleeding disorder": "hematology",
    "blood clot": "hematology", "dvt": "hematology",
    "thrombosis": "hematology", "cbc abnormal": "hematology",
    "iron deficiency": "hematology", "b12 deficiency": "hematology",
    "hemoglobin": "hematology", "rektha rogam": "hematology",
    "blood count kurachu": "hematology", "blood count low": "hematology",
}


def resolve_dept_keyword(keyword: str) -> Optional[str]:
    return _DEPT_KEYWORDS.get(keyword.lower())


# ── Symptom description → canonical department map ────────────────────────────
# Sorted longest-first at build time so _map_symptom_to_dept() can do a single
# linear scan without worrying about shorter substrings matching first.
# Keys are lowercased substrings to search in the caller's transcript.

_SYMPTOM_DEPT_MAP_RAW: dict[str, str] = {
    # ── Cardiology ───────────────────────────────────────────────────────────
    "chest pain": "cardiology", "heart pain": "cardiology",
    "heart problem": "cardiology", "heart attack": "cardiology",
    "palpitation": "cardiology", "palpitations": "cardiology",
    "high blood pressure": "cardiology", "hypertension": "cardiology",
    "hridayam": "cardiology", "hrudayam": "cardiology",
    "neriv vedana": "cardiology", "cardiac": "cardiology",
    "maarpu vedana": "cardiology", "maarpu kayanam": "cardiology",
    "maarpu irakka": "cardiology", "hridayarogi": "cardiology",
    "chest tightness": "cardiology", "chest heaviness": "cardiology",
    "left arm pain": "cardiology", "jaw pain with chest": "cardiology",
    "angina": "cardiology", "coronary artery disease": "cardiology",
    "heart failure": "cardiology", "heart block": "cardiology",
    "arrhythmia": "cardiology", "atrial fibrillation": "cardiology",
    "stent": "cardiology", "bypass surgery": "cardiology",
    "angiogram": "cardiology", "echocardiogram": "cardiology",
    "hridayastambhanam": "cardiology", "hrudaya rogam": "cardiology",
    "rakthasammardam": "cardiology", "bp problem": "cardiology",
    "low blood pressure": "cardiology", "heart pounding": "cardiology",
    "irregular heartbeat": "cardiology", "heart racing": "cardiology",
    "heart murmur": "cardiology", "valve problem": "cardiology",
    # ── Neurology ────────────────────────────────────────────────────────────
    "headache": "neurology", "head pain": "neurology",
    "head ache": "neurology", "migraine": "neurology",
    "dizziness": "neurology", "dizzy": "neurology",
    "seizure": "neurology", "epilepsy": "neurology",
    "memory loss": "neurology", "tremor": "neurology",
    "numbness": "neurology", "paralysis": "neurology",
    "thalavedana": "neurology", "thalakayanam": "neurology",
    "thalakkayanam": "neurology", "thalav": "neurology",
    "one side weakness": "neurology", "one side paralysis": "neurology",
    "facial weakness": "neurology", "speech problem": "neurology",
    "loss of consciousness": "neurology", "blackout": "neurology",
    "fainting": "neurology", "confusion": "neurology",
    "cognitive decline": "neurology", "dementia": "neurology",
    "alzheimer": "neurology", "parkinson": "neurology",
    "thal tharakan": "neurology", "kaikalil tarippu": "neurology",
    "oru bhagam": "neurology", "kaikal tarippu": "neurology",
    "madhi kuranju": "neurology", "tingling": "neurology",
    "weakness one side": "neurology", "face drooping": "neurology",
    "multiple sclerosis": "neurology", "meningitis": "neurology",
    "encephalitis": "neurology", "cerebral stroke": "neurology",
    "brain stroke": "neurology", "neuropathy": "neurology",
    "bells palsy": "neurology", "facial palsy": "neurology",
    "trigeminal neuralgia": "neurology", "nerve pain": "neurology",
    "manasikarogam": "neurology",
    # ── Orthopedics ───────────────────────────────────────────────────────────
    "knee pain": "orthopedics", "back pain": "orthopedics",
    "joint pain": "orthopedics", "shoulder pain": "orthopedics",
    "neck pain": "orthopedics", "hip pain": "orthopedics",
    "ankle pain": "orthopedics", "wrist pain": "orthopedics",
    "elbow pain": "orthopedics", "bone pain": "orthopedics",
    "fracture": "orthopedics", "sprain": "orthopedics",
    "spine": "orthopedics", "arthritis": "orthopedics",
    "ellu vedana": "orthopedics", "muzhu vedana": "orthopedics",
    "kazhuthu vedana": "orthopedics", "mottu vedana": "orthopedics",
    "ellu kayanam": "orthopedics",
    "spondylosis": "orthopedics", "spondylitis": "orthopedics",
    "disc problem": "orthopedics", "sciatica": "orthopedics",
    "osteoarthritis": "orthopedics", "osteoporosis": "orthopedics",
    "ligament tear": "orthopedics", "tendon injury": "orthopedics",
    "dislocation": "orthopedics", "acl tear": "orthopedics",
    "ellu moluvu": "orthopedics", "sandi vedana": "orthopedics",
    "sandi kayanam": "orthopedics", "nattu kayanam": "orthopedics",
    "kaal kayanam": "orthopedics", "kai kayanam": "orthopedics",
    "iduppu kayanam": "orthopedics", "kazhuthu kayanam": "orthopedics",
    "muzhu kayanam": "orthopedics", "kaal ottiyathu": "orthopedics",
    "thal kayanam": "orthopedics", "hand pain": "orthopedics",
    "leg pain": "orthopedics", "foot pain": "orthopedics",
    "low back pain": "orthopedics", "lumbar pain": "orthopedics",
    "cervical pain": "orthopedics",
    # ── Gastroenterology ──────────────────────────────────────────────────────
    "stomach pain": "gastroenterology", "stomach ache": "gastroenterology",
    "abdominal pain": "gastroenterology", "abdomen pain": "gastroenterology",
    "loose motion": "gastroenterology", "loose motions": "gastroenterology",
    "diarrhea": "gastroenterology", "diarrhoea": "gastroenterology",
    "constipation": "gastroenterology", "indigestion": "gastroenterology",
    "acidity": "gastroenterology", "acid reflux": "gastroenterology",
    "nausea": "gastroenterology", "vomiting": "gastroenterology",
    "liver problem": "gastroenterology", "jaundice": "gastroenterology",
    "vayar vedana": "gastroenterology", "vayarkayanam": "gastroenterology",
    "omi": "gastroenterology", "omanarekkayanam": "gastroenterology",
    "vayar": "gastroenterology",
    "gerd": "gastroenterology", "heartburn": "gastroenterology",
    "gastritis": "gastroenterology", "peptic ulcer": "gastroenterology",
    "ibs": "gastroenterology", "ibd": "gastroenterology",
    "crohn disease": "gastroenterology", "colitis": "gastroenterology",
    "pancreatitis": "gastroenterology", "gallstone": "gastroenterology",
    "cholecystitis": "gastroenterology", "hepatitis": "gastroenterology",
    "fatty liver": "gastroenterology", "cirrhosis": "gastroenterology",
    "appendicitis": "gastroenterology", "hernia": "gastroenterology",
    "hemorrhoids": "gastroenterology", "piles": "gastroenterology",
    "rectal bleeding": "gastroenterology", "blood in stool": "gastroenterology",
    "dysentery": "gastroenterology", "food poisoning": "gastroenterology",
    "bloating": "gastroenterology", "dyspepsia": "gastroenterology",
    "kiral vedana": "gastroenterology", "karal rogam": "gastroenterology",
    "vayar ilittu": "gastroenterology", "vayar pothuka": "gastroenterology",
    "malabandham": "gastroenterology", "raktham potti": "gastroenterology",
    "vayar ketti": "gastroenterology", "liver jaundice": "gastroenterology",
    "mala rogam": "gastroenterology", "anal fissure": "gastroenterology",
    "rectal pain": "gastroenterology", "black stool": "gastroenterology",
    "stomach gas": "gastroenterology", "belching": "gastroenterology",
    "burping": "gastroenterology", "flatulence": "gastroenterology",
    # ── ENT ───────────────────────────────────────────────────────────────────
    "ear pain": "ent", "earache": "ent",
    "hearing loss": "ent", "hearing problem": "ent",
    "sore throat": "ent", "throat pain": "ent",
    "nose bleed": "ent", "nosebleed": "ent",
    "sinusitis": "ent", "tonsil": "ent", "tonsils": "ent",
    "kaan vedana": "ent", "kaan kayanam": "ent",
    "thallu vedana": "ent", "mookku": "ent",
    "blocked ear": "ent", "blocked nose": "ent",
    "ringing in ear": "ent", "tinnitus": "ent",
    "runny nose": "ent", "nasal congestion": "ent",
    "snoring": "ent", "sleep apnea": "ent",
    "voice change": "ent", "voice problem": "ent",
    "hoarseness": "ent", "hoarse voice": "ent",
    "throat infection": "ent", "pharyngitis": "ent",
    "laryngitis": "ent", "adenoids": "ent",
    "vertigo": "ent", "bppv": "ent",
    "mookku ozhukal": "ent", "mookku adakkam": "ent",
    "kaan therippu": "ent", "kaan tool": "ent",
    "kaan vazhukkam": "ent", "gala kayanam": "ent",
    "gala vedana": "ent", "thallu kayanam": "ent",
    "ear discharge": "ent", "ear wax": "ent",
    "deviated septum": "ent", "nasal polyp": "ent",
    "allergic rhinitis": "ent",
    # ── Ophthalmology ─────────────────────────────────────────────────────────
    "eye pain": "ophthalmology", "eye problem": "ophthalmology",
    "blurry vision": "ophthalmology", "vision problem": "ophthalmology",
    "eye redness": "ophthalmology", "watery eyes": "ophthalmology",
    "kannu vedana": "ophthalmology", "kanninu": "ophthalmology",
    "nethram": "ophthalmology",
    "blurred vision": "ophthalmology", "double vision": "ophthalmology",
    "eye infection": "ophthalmology", "conjunctivitis": "ophthalmology",
    "cataract": "ophthalmology", "glaucoma": "ophthalmology",
    "retina problem": "ophthalmology", "itchy eye": "ophthalmology",
    "dry eyes": "ophthalmology", "floaters": "ophthalmology",
    "night blindness": "ophthalmology", "squint": "ophthalmology",
    "kannu charayan": "ophthalmology", "kazhcha kuranju": "ophthalmology",
    "kannu chuvakkunnu": "ophthalmology", "kannu titippu": "ophthalmology",
    "kannu velicham kuranju": "ophthalmology",
    "kannilninnu water varunu": "ophthalmology",
    "kann vedana": "ophthalmology",
    "loss of vision": "ophthalmology", "sudden blindness": "ophthalmology",
    "eye swelling": "ophthalmology", "eyelid problem": "ophthalmology",
    # ── Dermatology ───────────────────────────────────────────────────────────
    "skin rash": "dermatology", "skin problem": "dermatology",
    "skin disease": "dermatology", "skin infection": "dermatology",
    "itching": "dermatology", "eczema": "dermatology",
    "psoriasis": "dermatology", "acne": "dermatology",
    "hair fall": "dermatology", "hair loss": "dermatology",
    "poochuvili": "dermatology", "charma rogam": "dermatology",
    "fungal infection": "dermatology", "ringworm": "dermatology",
    "vitiligo": "dermatology", "hives": "dermatology",
    "urticaria": "dermatology", "dermatitis": "dermatology",
    "tol rogam": "dermatology", "tol kayanam": "dermatology",
    "tol cherayan": "dermatology", "poochuvili undu": "dermatology",
    "therippu": "dermatology", "cholachil": "dermatology",
    "theechil": "dermatology", "daadru": "dermatology",
    "kariyappu": "dermatology", "nootha": "dermatology",
    "scabies": "dermatology", "warts": "dermatology",
    "boil": "dermatology", "abscess": "dermatology",
    "dark spots": "dermatology", "hyperpigmentation": "dermatology",
    "dandruff": "dermatology", "seborrhea": "dermatology",
    "prickly heat": "dermatology", "miliaria": "dermatology",
    "rash": "dermatology", "skin allergy": "dermatology",
    "contact dermatitis": "dermatology",
    # ── Pulmonology ───────────────────────────────────────────────────────────
    "breathing problem": "pulmonology",
    "shortness of breath": "pulmonology",
    "breathlessness": "pulmonology",
    "difficulty breathing": "pulmonology",
    "asthma": "pulmonology", "lung problem": "pulmonology",
    "persistent cough": "pulmonology", "chronic cough": "pulmonology",
    "ithira": "pulmonology", "niswasam": "pulmonology",
    "copd": "pulmonology", "emphysema": "pulmonology",
    "chronic bronchitis": "pulmonology", "pneumonia": "pulmonology",
    "tuberculosis": "pulmonology", "tb": "pulmonology",
    "coughing blood": "pulmonology", "blood cough": "pulmonology",
    "wheezing": "pulmonology", "inhaler needed": "pulmonology",
    "oxygen low": "pulmonology", "spo2 low": "pulmonology",
    "kapha chuma": "pulmonology", "kashi": "pulmonology",
    "swaasam ellatha": "pulmonology", "maarpu irakka vedana": "pulmonology",
    "lung infection": "pulmonology", "chest infection": "pulmonology",
    "bronchitis": "pulmonology", "pleurisy": "pulmonology",
    "pulmonary fibrosis": "pulmonology",
    # ── Gynecology ────────────────────────────────────────────────────────────
    "pregnancy": "gynaecology", "pregnant": "gynaecology",
    "delivery": "gynaecology", "maternity": "gynaecology",
    "menstrual problem": "gynaecology", "period problem": "gynaecology",
    "pcod": "gynaecology", "pcos": "gynaecology",
    "prasavam": "gynaecology", "garba": "gynaecology",
    "irregular periods": "gynaecology", "heavy periods": "gynaecology",
    "painful periods": "gynaecology", "dysmenorrhea": "gynaecology",
    "amenorrhea": "gynaecology", "infertility": "gynaecology",
    "vaginal discharge": "gynaecology", "white discharge": "gynaecology",
    "ovarian cyst": "gynaecology", "fibroid": "gynaecology",
    "endometriosis": "gynaecology", "menopause": "gynaecology",
    "breast lump": "gynaecology", "masam kuthi": "gynaecology",
    "artava kramakettu": "gynaecology", "stree rogam": "gynaecology",
    "mahilarogam": "gynaecology", "pregnant aano": "gynaecology",
    "kutti undu": "gynaecology", "garbhakosham": "gynaecology",
    "artavam": "gynaecology",
    # ── Pediatrics ────────────────────────────────────────────────────────────
    "child fever": "pediatrics", "baby fever": "pediatrics",
    "child problem": "pediatrics", "baby problem": "pediatrics",
    "infant": "pediatrics", "newborn": "pediatrics",
    "kutta vedana": "pediatrics", "kuttinu": "pediatrics",
    "child cough": "pediatrics", "baby cough": "pediatrics",
    "child vomiting": "pediatrics", "baby vomiting": "pediatrics",
    "vaccination": "pediatrics", "immunization": "pediatrics",
    "growth problem": "pediatrics", "developmental delay": "pediatrics",
    "autism": "pediatrics", "febrile seizure": "pediatrics",
    "kutti vomiting": "pediatrics", "kutti loose motion": "pediatrics",
    "kutti kayanam": "pediatrics", "kutti pani": "pediatrics",
    "kuttiku pani": "pediatrics", "bala rogam": "pediatrics",
    "jaundice in newborn": "pediatrics", "neonatal": "pediatrics",
    "diaper rash": "pediatrics", "colic baby": "pediatrics",
    # ── Urology ───────────────────────────────────────────────────────────────
    "urinary problem": "urology", "urine problem": "urology",
    "kidney stone": "urology", "kidney pain": "urology",
    "kidney problem": "urology", "bladder": "urology",
    "mutra rogam": "urology", "kidni": "urology",
    "frequent urination": "urology", "painful urination": "urology",
    "blood in urine": "urology", "burning urination": "urology",
    "urine retention": "urology", "unable to urinate": "urology",
    "prostate problem": "urology", "uti": "urology",
    "mutra vedana": "urology", "mutrathakku": "urology",
    "mutra pidutham": "urology", "mootram varunnilla": "urology",
    "kidni stone": "urology", "kidney failure": "urology",
    "dialysis": "urology", "erectile dysfunction": "urology",
    "male infertility": "urology", "scrotal pain": "urology",
    "hydrocele": "urology", "varicocele": "urology",
    "epididymitis": "urology",
    # ── Dental ────────────────────────────────────────────────────────────────
    "tooth pain": "dental", "toothache": "dental",
    "tooth ache": "dental", "gum pain": "dental",
    "gum problem": "dental", "tooth problem": "dental",
    "pallu vedana": "dental", "pallu kayanam": "dental",
    "cavity": "dental", "root canal": "dental",
    "tooth extraction": "dental", "wisdom tooth pain": "dental",
    "gum bleeding": "dental", "gum swelling": "dental",
    "pallu poyi": "dental", "pallu kothikkunnu": "dental",
    "pallu alichal": "dental", "pal vedana": "dental",
    "jaw pain": "dental", "broken tooth": "dental",
    # ── Psychiatry ────────────────────────────────────────────────────────────
    "depression": "psychiatry", "anxiety": "psychiatry",
    "mental health": "psychiatry", "panic attack": "psychiatry",
    "insomnia": "psychiatry", "sleep problem": "psychiatry",
    "stress problem": "psychiatry", "mental problem": "psychiatry",
    "manassastra": "psychiatry",
    "vishaadam": "psychiatry", "utkandha": "psychiatry",
    "bhranthu": "psychiatry", "manasiku vedana": "psychiatry",
    "manassu kayanam": "psychiatry", "nervous breakdown": "psychiatry",
    "manasika rogam": "psychiatry", "uyir thalavariche": "psychiatry",
    "bipolar disorder": "psychiatry", "schizophrenia": "psychiatry",
    "ocd": "psychiatry", "ptsd": "psychiatry",
    "anxiety disorder": "psychiatry", "phobia": "psychiatry",
    "addiction problem": "psychiatry", "alcohol addiction": "psychiatry",
    "self harm": "psychiatry", "suicidal thoughts": "psychiatry",
    "mental breakdown": "psychiatry", "burnout": "psychiatry",
    "sleep insomnia": "psychiatry", "sleep difficulty": "psychiatry",
    # ── General medicine ──────────────────────────────────────────────────────
    "fever": "general medicine", "pani": "general medicine",
    "jwaram": "general medicine", "cold": "general medicine",
    "flu": "general medicine", "weakness": "general medicine",
    "fatigue": "general medicine", "tiredness": "general medicine",
    "sukhamilla": "general medicine", "alukkam": "general medicine",
    "thalarcha": "general medicine", "body ache": "general medicine",
    "weight gain": "general medicine", "weight loss": "general medicine",
    "loss of appetite": "general medicine", "general checkup": "general medicine",
    "viral infection": "general medicine", "common cold": "general medicine",
    "dehydration": "general medicine", "food allergy": "general medicine",
    "mild fever": "general medicine", "low grade fever": "general medicine",
    "high fever": "general medicine", "night sweats": "general medicine",
    "thabhathi": "general medicine", "mithyam": "general medicine",
    "sukham illa": "general medicine", "pani undu": "general medicine",
    "bukhar": "general medicine",
    # ── Endocrinology ─────────────────────────────────────────────────────────
    "diabetes": "endocrinology", "diabetic": "endocrinology",
    "blood sugar high": "endocrinology", "sugar high": "endocrinology",
    "blood sugar low": "endocrinology", "hypoglycemia": "endocrinology",
    "hyperglycemia": "endocrinology", "insulin problem": "endocrinology",
    "thyroid problem": "endocrinology", "hypothyroidism": "endocrinology",
    "hyperthyroidism": "endocrinology", "thyroid swelling": "endocrinology",
    "goiter": "endocrinology", "hormone problem": "endocrinology",
    "hormonal imbalance": "endocrinology", "praameham": "endocrinology",
    "sugar rogam": "endocrinology", "thyroid rogam": "endocrinology",
    "sugar patient": "endocrinology", "praameha rogi": "endocrinology",
    "madhu rogam": "endocrinology", "madhurogam": "endocrinology",
    "thyroid kayanam": "endocrinology", "blood sugar kooduthal": "endocrinology",
    "blood sugar kuranju": "endocrinology",
    "type 2 diabetes": "endocrinology", "type 1 diabetes": "endocrinology",
    "diabetes checkup": "endocrinology", "sugar checkup": "endocrinology",
    "hba1c high": "endocrinology", "pcod hormonal": "endocrinology",
    "weight problem hormonal": "endocrinology",
    # ── Nephrology ────────────────────────────────────────────────────────────
    "kidney disease": "nephrology", "chronic kidney disease": "nephrology",
    "ckd": "nephrology", "renal failure": "nephrology",
    "creatinine high": "nephrology", "creatinine elevated": "nephrology",
    "protein in urine": "nephrology", "proteinuria": "nephrology",
    "nephrotic syndrome": "nephrology", "glomerulonephritis": "nephrology",
    "kidney failure": "nephrology", "dialysis needed": "nephrology",
    "vrikka rogam": "nephrology", "kidni rogam": "nephrology",
    "kidni failure": "nephrology", "vrikka": "nephrology",
    "renal disease": "nephrology", "kidney transplant": "nephrology",
    "swollen kidneys": "nephrology",
    # ── Rheumatology ──────────────────────────────────────────────────────────
    "rheumatoid arthritis": "rheumatology", "lupus": "rheumatology",
    "joint swelling": "rheumatology", "joint inflammation": "rheumatology",
    "sle": "rheumatology", "fibromyalgia": "rheumatology",
    "ankylosing spondylitis": "rheumatology", "auto immune": "rheumatology",
    "auto-immune disease": "rheumatology", "uric acid high": "rheumatology",
    "gout attack": "rheumatology", "podagra": "rheumatology",
    "vasculitis": "rheumatology", "scleroderma": "rheumatology",
    "myositis": "rheumatology",
    # ── Hematology ────────────────────────────────────────────────────────────
    "anemia": "hematology", "anaemia": "hematology",
    "low hemoglobin": "hematology", "hemoglobin low": "hematology",
    "low blood count": "hematology", "blood count low": "hematology",
    "thalassemia": "hematology", "sickle cell": "hematology",
    "hemophilia": "hematology", "bleeding disorder": "hematology",
    "blood clot": "hematology", "dvt": "hematology",
    "thrombosis": "hematology", "low platelets": "hematology",
    "platelet low": "hematology", "iron deficiency": "hematology",
    "b12 deficiency": "hematology", "blood cancer": "hematology",
    "leukemia": "hematology", "lymphoma": "hematology",
    "rektha rogam": "hematology", "blood disorder": "hematology",
    "blood count kurachu": "hematology",
    # ── Oncology ──────────────────────────────────────────────────────────────
    "cancer": "oncology", "tumor": "oncology", "lump": "oncology",
    "lump breast": "oncology", "lump neck": "oncology",
    "malignant tumor": "oncology", "chemotherapy": "oncology",
    "radiation therapy": "oncology", "cancer treatment": "oncology",
    "metastasis": "oncology", "lymph node swelling": "oncology",
    "breast cancer": "oncology", "lung cancer": "oncology",
    "colon cancer": "oncology", "prostate cancer": "oncology",
    "liver cancer": "oncology", "skin cancer": "oncology",
    "kansar": "oncology", "arbudham": "oncology",
    "arbhutham": "oncology", "katina rogam": "oncology",
    "weight loss sudden": "oncology", "unexplained weight loss": "oncology",
}

# Sort once at module load; longest key first → more specific match wins
_SYMPTOM_DEPT_MAP: list[tuple[str, str]] = sorted(
    _SYMPTOM_DEPT_MAP_RAW.items(), key=lambda kv: -len(kv[0])
)

# ── Ambiguous symptoms ────────────────────────────────────────────────────────
# Symptoms that can belong to multiple departments. For these, routing to one
# department without asking first would be wrong (e.g., "chest pain" is more
# often gastric / acidity in Kerala context than cardiac). The bot asks a
# single clarifying question so the caller can self-triage.
_AMBIGUOUS_SYMPTOM_CLARIFICATIONS: dict[str, str] = {
    # Chest — most common: gastric/acid reflux, then cardiac, then pulmonary
    "chest pain": (
        "Chest pain-ന് Gastroenterology (acidity/gastric), Cardiology (heart), "
        "അല്ലെങ്കിൽ Pulmonology (breathing) — ഏതെങ്കിലും ആകാം. "
        "Eating-ന് ശേഷം burning ആണോ, അതോ sudden severe pain ആണോ?"
    ),
    "chest": (
        "Chest area-ൽ ഉള്ള problem — Gastroenterology, Cardiology, "
        "അല്ലെങ്കിൽ Pulmonology ആകാം. "
        "കൂടുതൽ describe ചെയ്യാമോ — burning ആണോ, pain ആണോ, breathlessness ആണോ?"
    ),
    # Stomach / abdomen — often gastric, but could be general medicine for mild fever+stomach
    "stomach pain": (
        "Stomach pain-ന് Gastroenterology department ആണ് relevant. "
        "Fever കൂടി ഉണ്ടോ, അതോ vomiting ഉണ്ടോ?"
    ),
    # Head — migraine / neurology vs. fever / general medicine
    "headache": (
        "Headache-ന് Neurology (severe/migraine) അല്ലെങ്കിൽ General Medicine (mild/fever) "
        "ആകാം. Fever കൂടി ഉണ്ടോ, അതോ head-ൽ severe pulsating pain ആണോ?"
    ),
    "head pain": (
        "Head pain-ന് Neurology അല്ലെങ്കിൽ General Medicine ആകാം. "
        "Severe pain ആണോ, fever ഉണ്ടോ?"
    ),
    # Back — orthopedic vs. kidney (urology)
    "back pain": (
        "Back pain-ന് Orthopedics (spine/muscle) അല്ലെങ്കിൽ Urology (kidney-related) "
        "ആകാം. Upper back ആണോ, lower back ആണോ?"
    ),
    # Vayar in Malayalam is often both "stomach" and "intestines"
    "vayar vedana": (
        "Vayar vedana-ന് Gastroenterology ആണ് relevant. "
        "Vomiting ഉണ്ടോ, fever ഉണ്ടോ, അതോ acidity/gas ആണോ?"
    ),
    "vayarkayanam": (
        "Vayar kayanam-ന് Gastroenterology consult ചെയ്യണം. "
        "Loose motion, vomiting, fever — ഏതെങ്കിലും ഉണ്ടോ?"
    ),
    # Thalavedana in Malayalam
    "thalavedana": (
        "Thalavedana-ന് Neurology (severe/migraine) അല്ലെങ്കിൽ General Medicine (mild) "
        "ആകാം. Pani (fever) ഉണ്ടോ?"
    ),
}


# ── Main service ──────────────────────────────────────────────────────────────

_DOW_EN = {0: "Sun", 1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat"}


def build_hospital_summary(ctx: HospitalContext) -> str:
    """
    Build a compact English summary of the hospital for the LLM.
    Used as context for free-form caller questions instead of FAQ matching.
    """
    lines: list[str] = []
    lines.append(f"HOSPITAL: {ctx.name} ({ctx.name_ml}).")
    if ctx.address:
        lines.append(f"ADDRESS: {ctx.address}.")
    if ctx.phone:
        lines.append(f"MAIN PHONE: {ctx.phone}.")

    # Hours
    if ctx.hours:
        h = ctx.hours
        order = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        hr_parts = []
        for d in order:
            if d in h and h[d]:
                hr_parts.append(f"{d.capitalize()} {h[d][0]}-{h[d][1]}")
        lines.append("OPENING HOURS: " + "; ".join(hr_parts) + ". Emergency 24x7.")

    # Departments
    if ctx.departments:
        dept_lines = []
        for d in ctx.departments:
            extra = f" ({d.floor}, {d.location_hint})" if d.floor else ""
            dept_lines.append(f"{d.name}{extra} ext {d.phone_ext}")
        lines.append("DEPARTMENTS AVAILABLE: " + " | ".join(dept_lines) + ".")

    # Services NOT available (anything in _DEPT_KEYWORDS that doesn't map to a real dept)
    available_canonical = {d.name.lower() for d in ctx.departments}
    not_offered = set()
    for kw, canon in _DEPT_KEYWORDS.items():
        if canon.lower() not in available_canonical and not any(
            canon.lower() in d.name.lower() for d in ctx.departments
        ):
            not_offered.add(canon)
    if not_offered:
        lines.append("SERVICES NOT OFFERED HERE: " + ", ".join(sorted(not_offered)) + ".")

    # Doctors with schedules
    if ctx.doctors:
        lines.append("DOCTORS:")
        for d in ctx.doctors:
            sched_parts = [
                f"{_DOW_EN.get(s.dow, '?')} {s.start}-{s.end}" for s in d.slots
            ]
            sched = "; ".join(sched_parts) if sched_parts else "no schedule"
            qual = f", {d.qualifications}" if d.qualifications else ""
            lines.append(f"- {d.name} ({d.dept_name}{qual}): {sched}")

    # Billing
    if ctx.billing:
        lines.append("PRICING:")
        for b in ctx.billing:
            price = (f"₹{int(b.price_min)}"
                     if b.price_min == b.price_max
                     else f"₹{int(b.price_min)}-{int(b.price_max)}")
            lines.append(f"- {b.item} = {price}"
                         + (f" ({b.notes})" if b.notes else ""))

    # Emergency
    if ctx.emergency:
        em_parts = [f"{e.label} {e.phone}" for e in ctx.emergency]
        lines.append("EMERGENCY CONTACTS: " + " | ".join(em_parts) + ".")

    # FAQs — included so the LLM can answer questions about parking, insurance,
    # appointment booking, facilities, and anything else the admin has documented.
    if ctx.faqs:
        lines.append("ADDITIONAL INFORMATION:")
        for faq in ctx.faqs:
            lines.append(f"Q: {faq.question}")
            lines.append(f"A: {faq.answer}")

    # Instruction so the LLM knows where to redirect unknown questions.
    if ctx.phone:
        lines.append(
            f"FALLBACK: For anything not covered above, direct the caller to "
            f"reception at {ctx.phone}."
        )

    return "\n".join(lines)


class HospitalKnowledgeService:

    def __init__(self, ctx: HospitalContext):
        self.ctx = ctx
        self._summary = build_hospital_summary(ctx)

    def answer(
        self,
        intent: str,
        entities: dict,
        state_context: Optional[dict] = None,
    ) -> KnowledgeResult:
        """Route to the right handler. state_context fills missing entities."""
        # Inherit from conversation context if entity missing
        if state_context:
            if not entities.get("department") and state_context.get("last_department"):
                entities = {**entities, "department": state_context["last_department"]}
            if not entities.get("doctor_name") and state_context.get("last_doctor_name"):
                entities = {**entities, "doctor_name": state_context["last_doctor_name"]}
            if not entities.get("day") and state_context.get("last_day"):
                entities = {**entities, "day": state_context["last_day"]}

        handlers = {
            INTENT_DOCTOR_AVAILABILITY: self._doctor_availability,
            INTENT_DOCTOR_TIMING: self._doctor_timing,
            INTENT_CONSULTATION_FEE: self._fee,
            INTENT_DEPARTMENT_EXISTS: self._dept_exists,
            INTENT_HOSPITAL_TIMING: self._hospital_timing,
            INTENT_EMERGENCY: self._emergency,
            INTENT_LOCATION: self._location,
            INTENT_CONTACT: self._contact,
            INTENT_SYMPTOM: self._symptom_recommendation,
        }
        handler = handlers.get(intent)
        if handler:
            return handler(entities)
        return KnowledgeResult(intent=intent, found=False,
                               text_ml="ഇതിനെ കുറിച്ച് ഞാൻ ഉചിതമായ ഉത്തരം നൽകാൻ കഴിയില്ല.",
                               missing="unsupported_intent")

    # ── Doctor availability ───────────────────────────────────────────────────

    def _doctor_availability(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")
        doc_name = entities.get("doctor_name")
        day_name = entities.get("day")  # "today", "monday", etc.

        dow = self._resolve_day(day_name)
        day_label = _DAY_ML.get(dow, "ഇന്ന്")

        # By doctor name
        if doc_name:
            doc = self._find_doctor_by_name(doc_name)
            if not doc:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=False,
                    text_ml=f"ക്ഷമിക്കണം, ആ doctor-നെ ഞങ്ങളുടെ list-ൽ കണ്ടെത്താൻ കഴിഞ്ഞില്ല.",
                    missing="doctor_not_found",
                )
            slot = self._slot_for_dow(doc, dow)
            if slot:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{doc.name_ml or doc.name} doctor {day_label}-ൽ "
                             f"{slot.start} മുതൽ {slot.end} വരെ available ആണ്."),
                    data={"doctor": doc.name, "start": slot.start, "end": slot.end},
                )
            # Doctor exists but not today — find next available slot
            next_slot = self._next_slot_after(doc, dow)
            if next_slot:
                next_dow, ns = next_slot
                next_label = _DAY_ML.get(next_dow, "")
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{doc.name_ml or doc.name} doctor {day_label}-ൽ "
                             f"available അല്ല. അടുത്തത് {next_label} "
                             f"{ns.start} മുതൽ {ns.end} വരെ available ആണ്."),
                    data={"doctor": doc.name, "available": False,
                          "next_day": next_label, "next_start": ns.start},
                )
            return KnowledgeResult(
                intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                text_ml=f"ക്ഷമിക്കണം, {doc.name_ml or doc.name} doctor {day_label}-ൽ available അല്ല.",
                data={"doctor": doc.name, "available": False},
            )

        # By department
        if dept_kw:
            resolved = resolve_dept_keyword(dept_kw) or dept_kw
            dept = self.ctx.find_dept(resolved)
            if not dept:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=False,
                    text_ml=f"ക്ഷമിക്കണം, {dept_kw} department ഈ hospital-ൽ ലഭ്യമല്ല.",
                    missing="dept_not_found",
                )
            dept_docs = self.ctx.doctors_for_dept(dept.name)
            avail = [d for d in dept_docs if self._slot_for_dow(d, dow)]
            if avail:
                names = ", ".join(d.name_ml or d.name for d in avail[:3])
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{dept.name_ml or dept.name}-ൽ {day_label}-ൽ "
                             f"{len(avail)} doctor available ആണ്: {names}."),
                    data={"dept": dept.name, "count": len(avail)},
                )
            # No doctors today — find earliest upcoming slot across the dept
            earliest_next: Optional[tuple[int, SlotInfo, DoctorInfo]] = None
            for d in dept_docs:
                ns = self._next_slot_after(d, dow)
                if ns and (earliest_next is None or ns[0] < earliest_next[0]):
                    earliest_next = (ns[0], ns[1], d)
            if earliest_next:
                ndow, ns, doc = earliest_next
                next_label = _DAY_ML.get(ndow, "")
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                    text_ml=(f"{dept.name_ml or dept.name}-ൽ {day_label}-ൽ "
                             f"doctors available അല്ല. {next_label}-ൽ "
                             f"{doc.name_ml or doc.name} doctor "
                             f"{ns.start} മുതൽ {ns.end} വരെ ഉണ്ടാകും."),
                )
            return KnowledgeResult(
                intent=INTENT_DOCTOR_AVAILABILITY, found=True,
                text_ml=(f"ക്ഷമിക്കണം, {dept.name_ml or dept.name}-ൽ "
                         f"ഇപ്പോൾ doctors-ന്റെ schedule ലഭ്യമല്ല."),
            )

        return KnowledgeResult(
            intent=INTENT_DOCTOR_AVAILABILITY, found=False,
            text_ml="ഏത് department-ലേക്കോ doctor-ലേക്കോ ആണ് enquiry?",
            missing="no_dept_or_doctor",
        )

    # ── Timing ───────────────────────────────────────────────────────────────

    def _doctor_timing(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")
        doc_name = entities.get("doctor_name")
        day_name = entities.get("day")
        dow = self._resolve_day(day_name)
        day_label = _DAY_ML.get(dow, "ഇന്ന്")

        if dept_kw:
            resolved = resolve_dept_keyword(dept_kw) or dept_kw
            dept = self.ctx.find_dept(resolved)
            if not dept:
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_TIMING, found=False,
                    text_ml=f"{dept_kw} department-ന്റെ timing ഞങ്ങൾക്ക് ലഭ്യമല്ല.",
                    missing="dept_not_found",
                )
            # Aggregate earliest start and latest end across all docs in dept
            docs = self.ctx.doctors_for_dept(dept.name)
            slots = [self._slot_for_dow(d, dow) for d in docs if self._slot_for_dow(d, dow)]
            if slots:
                earliest = min(s.start for s in slots)
                latest = max(s.end for s in slots)
                return KnowledgeResult(
                    intent=INTENT_DOCTOR_TIMING, found=True,
                    text_ml=(f"{dept.name_ml or dept.name} OP {day_label}-ൽ "
                             f"{earliest} മുതൽ {latest} വരെ ആണ്."),
                    data={"dept": dept.name, "start": earliest, "end": latest},
                )
            return KnowledgeResult(
                intent=INTENT_DOCTOR_TIMING, found=True,
                text_ml=(f"ക്ഷമിക്കണം, {dept.name_ml or dept.name}-ൽ "
                         f"{day_label}-ൽ OP ഇല്ല."),
            )

        if doc_name:
            doc = self._find_doctor_by_name(doc_name)
            if doc:
                slot = self._slot_for_dow(doc, dow)
                if slot:
                    return KnowledgeResult(
                        intent=INTENT_DOCTOR_TIMING, found=True,
                        text_ml=(f"{doc.name_ml or doc.name} doctor {day_label}-ൽ "
                                 f"{slot.start} മുതൽ {slot.end} വരെ {slot.room or ''} ൽ ആണ്."),
                    )

        # General hospital OP
        hours = self.ctx.hours_for_day(dow)
        if hours:
            return KnowledgeResult(
                intent=INTENT_DOCTOR_TIMING, found=True,
                text_ml=(f"Hospital {day_label}-ൽ {hours[0]} മുതൽ {hours[1]} വരെ open ആണ്. "
                         f"Emergency 24 മണിക്കൂറും ഉണ്ട്."),
            )
        return KnowledgeResult(
            intent=INTENT_DOCTOR_TIMING, found=False,
            text_ml="Timing information ലഭ്യമല്ല. Reception-ൽ ബന്ധപ്പെടൂ.",
        )

    # ── Fee ───────────────────────────────────────────────────────────────────

    def _fee(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")

        if dept_kw:
            resolved = resolve_dept_keyword(dept_kw) or dept_kw
            # Try billing_info
            billing = self.ctx.billing_for_dept(resolved)
            if billing:
                if billing.price_min == billing.price_max:
                    price_str = f"₹{int(billing.price_min)}"
                else:
                    price_str = f"₹{int(billing.price_min)}–{int(billing.price_max)}"
                return KnowledgeResult(
                    intent=INTENT_CONSULTATION_FEE, found=True,
                    text_ml=f"{billing.item_ml or dept_kw} consultation fee {price_str} ആണ്.",
                    data={"item": billing.item, "min": billing.price_min, "max": billing.price_max},
                )

        # No dept — give general consultation fee
        gen = self.ctx.billing_for_dept("general")
        if gen:
            return KnowledgeResult(
                intent=INTENT_CONSULTATION_FEE, found=True,
                text_ml=(f"General consultation fee ₹{int(gen.price_min)} ആണ്. "
                         f"Specialty departments ₹500 മുതൽ ₹800 വരെ ആണ്."),
            )
        return KnowledgeResult(
            intent=INTENT_CONSULTATION_FEE, found=False,
            text_ml="Fee-ന്റെ കൃത്യമായ വിവരം reception-ൽ confirm ചെയ്യൂ.",
        )

    # ── Department exists ─────────────────────────────────────────────────────

    def _dept_exists(self, entities: dict) -> KnowledgeResult:
        dept_kw = entities.get("department")
        if not dept_kw:
            return KnowledgeResult(
                intent=INTENT_DEPARTMENT_EXISTS, found=False,
                text_ml="ഏത് department-ന്റെ കാര്യമാണ്?",
                missing="no_department",
            )
        resolved = resolve_dept_keyword(dept_kw) or dept_kw
        dept = self.ctx.find_dept(resolved)
        if dept:
            floor_hint = f" ({dept.floor}-ൽ ആണ്, {dept.location_hint})" if dept.floor else ""
            return KnowledgeResult(
                intent=INTENT_DEPARTMENT_EXISTS, found=True,
                text_ml=f"ആം, ഞങ്ങൾക്ക് {dept.name_ml or dept.name} department ഉണ്ട്{floor_hint}.",
                data={"dept": dept.name, "floor": dept.floor},
            )
        return KnowledgeResult(
            intent=INTENT_DEPARTMENT_EXISTS, found=False,
            text_ml=f"ക്ഷമിക്കണം, {dept_kw} department ഇവിടെ ലഭ്യമല്ല.",
        )

    # ── Hospital timing ───────────────────────────────────────────────────────

    def _hospital_timing(self, entities: dict) -> KnowledgeResult:
        day_name = entities.get("day")
        dow = self._resolve_day(day_name)
        day_label = _DAY_ML.get(dow, "ഇന്ന്")
        hours = self.ctx.hours_for_day(dow)
        if hours:
            return KnowledgeResult(
                intent=INTENT_HOSPITAL_TIMING, found=True,
                text_ml=(f"Hospital {day_label}-ൽ {hours[0]} മുതൽ {hours[1]} വരെ open ആണ്. "
                         f"Emergency 24 മണിക്കൂറും open ആണ്."),
            )
        # Sunday / no hours = check if listed
        if dow == 0:
            return KnowledgeResult(
                intent=INTENT_HOSPITAL_TIMING, found=True,
                text_ml="ഞായർ 9 AM മുതൽ 1 PM വരെ OP open ആണ്. Emergency 24x7 ഉണ്ട്.",
            )
        return KnowledgeResult(
            intent=INTENT_HOSPITAL_TIMING, found=False,
            text_ml="Hospital timing-ന്റെ കൃത്യമായ വിവരം +914841234567 ൽ confirm ചെയ്യൂ.",
        )

    # ── Emergency ─────────────────────────────────────────────────────────────

    def _emergency(self, entities: dict) -> KnowledgeResult:
        if self.ctx.emergency:
            ec = self.ctx.emergency[0]
            phones = " / ".join(e.phone for e in self.ctx.emergency[:2])
            return KnowledgeResult(
                intent=INTENT_EMERGENCY, found=True,
                text_ml=(f"Emergency 24 മണിക്കൂറും available ആണ്. "
                         f"{ec.label_ml or ec.label}: {phones}."),
                data={"phones": phones},
            )
        return KnowledgeResult(
            intent=INTENT_EMERGENCY, found=True,
            text_ml="Emergency 24x7 available ആണ്. 108 ൽ വിളിക്കൂ.",
        )

    # ── Location ──────────────────────────────────────────────────────────────

    def _location(self, entities: dict) -> KnowledgeResult:
        return KnowledgeResult(
            intent=INTENT_LOCATION, found=True,
            text_ml=f"Hospital address: {self.ctx.address}.",
            data={"address": self.ctx.address},
        )

    # ── Contact ───────────────────────────────────────────────────────────────

    def _contact(self, entities: dict) -> KnowledgeResult:
        return KnowledgeResult(
            intent=INTENT_CONTACT, found=True,
            text_ml=f"Hospital phone number: {self.ctx.phone}.",
            data={"phone": self.ctx.phone},
        )

    # ── Symptom recommendation ────────────────────────────────────────────────

    def _symptom_recommendation(self, entities: dict) -> KnowledgeResult:
        """
        Map symptom description to right department and recommend available doctors.
        For ambiguous symptoms (e.g., chest pain could be cardiac OR gastric),
        asks a clarifying question instead of routing immediately to one department.
        Called when INTENT_SYMPTOM fires; requires entities["transcript"].
        """
        transcript = entities.get("transcript", "")
        t_lower = transcript.lower()

        # ── Ambiguous check first ─────────────────────────────────────────────
        # Sort by length descending so "chest pain" matches before "chest"
        for phrase in sorted(_AMBIGUOUS_SYMPTOM_CLARIFICATIONS, key=lambda p: -len(p)):
            if phrase in t_lower:
                clarification = _AMBIGUOUS_SYMPTOM_CLARIFICATIONS[phrase]
                return KnowledgeResult(
                    intent=INTENT_SYMPTOM, found=True,
                    text_ml=clarification,
                    data={"ambiguous": True, "phrase": phrase},
                )

        dept_name = self._map_symptom_to_dept(transcript)

        if not dept_name:
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=False,
                text_ml="ക്ഷമിക്കണം, ഏത് problem ആണ് ഉള്ളതെന്ന് ഒന്നുകൂടി പറഞ്ഞു തരാമോ?",
                missing="no_symptom_match",
            )

        dept = self.ctx.find_dept(dept_name)
        dow = today_db_dow()
        day_label = _DAY_ML.get(dow, "ഇന്ന്")

        if not dept:
            # Department not available at this hospital
            phone = self.ctx.phone or "reception"
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=True,
                text_ml=(f"ആ problem-ന് {dept_name.title()} specialist ആണ് ആവശ്യം. "
                         f"ഞങ്ങളുടെ hospital-ൽ ആ department ലഭ്യമല്ല. "
                         f"കൂടുതൽ വിവരത്തിന് {phone}-ൽ ബന്ധപ്പെടൂ."),
                missing="dept_not_found",
            )

        dept_docs = self.ctx.doctors_for_dept(dept.name)
        avail_today = [d for d in dept_docs if self._slot_for_dow(d, dow)]

        if avail_today:
            doc = avail_today[0]
            slot = self._slot_for_dow(doc, dow)
            names = ", ".join(d.name_ml or d.name for d in avail_today[:2])
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=True,
                text_ml=(f"ആ problem-ന് {dept.name_ml or dept.name} department-ൽ "
                         f"consult ചെയ്യണം. {day_label} {names} doctor available ആണ്, "
                         f"{slot.start} മുതൽ {slot.end} വരെ."),
                data={"dept": dept.name, "doctors": [d.name for d in avail_today]},
            )

        # Dept exists but no doctors today — find next available
        earliest_next = None
        for d in dept_docs:
            ns = self._next_slot_after(d, dow)
            if ns and (earliest_next is None or ns[0] < earliest_next[0]):
                earliest_next = (ns[0], ns[1], d)

        if earliest_next:
            ndow, ns, doc = earliest_next
            next_label = _DAY_ML.get(ndow, "")
            return KnowledgeResult(
                intent=INTENT_SYMPTOM, found=True,
                text_ml=(f"ആ problem-ന് {dept.name_ml or dept.name} department-ൽ "
                         f"consult ചെയ്യണം. {day_label} doctors ലഭ്യമല്ല. "
                         f"{next_label}-ൽ {doc.name_ml or doc.name} doctor "
                         f"{ns.start} മുതൽ {ns.end} വരെ available ആണ്."),
            )

        phone = self.ctx.phone or "reception"
        return KnowledgeResult(
            intent=INTENT_SYMPTOM, found=True,
            text_ml=(f"ആ problem-ന് {dept.name_ml or dept.name} department-ൽ "
                     f"consult ചെയ്യണം. Appointment-ന് {phone}-ൽ ബന്ധപ്പെടൂ."),
        )

    @staticmethod
    def _map_symptom_to_dept(text: str) -> Optional[str]:
        """Search transcript for symptom phrases; return canonical dept name."""
        t = text.lower()
        for symptom, dept in _SYMPTOM_DEPT_MAP:
            if symptom in t:
                return dept
        return None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _resolve_day(self, day_name: Optional[str]) -> int:
        if not day_name or day_name in ("today", None):
            return today_db_dow()
        db_dow = named_dow_to_db(day_name)
        return db_dow if db_dow is not None else today_db_dow()

    def _find_doctor_by_name(self, query: str) -> Optional[DoctorInfo]:
        q = query.lower()
        for doc in self.ctx.doctors:
            if q in doc.name.lower() or q in (doc.name_ml or "").lower():
                return doc
        return None

    @staticmethod
    def _slot_for_dow(doc: DoctorInfo, dow: int):
        """Return first slot matching dow, or None."""
        for s in doc.slots:
            if s.dow == dow:
                return s
        return None

    # ── Free-form LLM answer using hospital summary ───────────────────────────

    def answer_freeform(self, question: str) -> KnowledgeResult:
        """
        Answer any caller question by giving the LLM (Groq llama-3.1-8b)
        the full hospital summary and the user's question. Used when the
        structured intent path didn't match — the LLM reads the summary
        and answers from those details, refusing if data isn't there.

        On Groq failure, the fallback embeds the question text so we
        don't return identical audio for every miss (which would otherwise
        be served from the TTS cache).
        """
        import logging
        log = logging.getLogger(__name__)

        try:
            from groq import Groq
            client = Groq(api_key=settings.GROQ_API_KEY)
            reception = self.ctx.phone or "the hospital"
            prompt = (
                "You are an AI voice assistant for a Kerala hospital. "
                "A caller has already dialed in and is speaking with you. "
                "Answer using ONLY the facts in the HOSPITAL SUMMARY below.\n\n"
                "Rules:\n"
                "1. NEVER ask 'ആരാണ് സംസാരിക്കുന്നത്' or 'who is speaking'. "
                "The caller is already on the line.\n"
                "2. If the caller's message is unclear or seems like a greeting, "
                "respond: 'ഹലോ! Doctor timing, fees, departments — ഏത് കാര്യം enquire ചെയ്യണം?'\n"
                "3. Only say a service is NOT available if it is explicitly listed under "
                "'SERVICES NOT OFFERED HERE'. For anything not mentioned in the summary, "
                f"say: 'Please contact our reception at {reception} for details.'\n"
                "4. Match the caller's language: Malayalam-Manglish for Malayalam/Manglish "
                "input, English for English input.\n"
                "5. Keep the reply to ONE or TWO short sentences — this is a voice call.\n"
                "6. Do not start with 'Sorry' unless you are genuinely denying something.\n"
                "7. Do not invent facts. If you are unsure, direct to reception.\n\n"
                f"HOSPITAL SUMMARY:\n{self._summary}\n\n"
                f"Caller: {question}\nReceptionist:"
            )
            log.info(f"freeform_groq_call question={question!r}")
            resp = client.chat.completions.create(
                model=settings.GROQ_MODEL_FAST,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=settings.GROQ_MAX_TOKENS,
                timeout=settings.GROQ_TIMEOUT_S,
                temperature=0.2,
            )
            text = resp.choices[0].message.content.strip()
            log.info(f"freeform_groq_ok answer={text!r}")
            return KnowledgeResult(
                intent="freeform",
                found=bool(text),
                text_ml=text or "",
                data={"source": "groq_summary"},
            )
        except Exception as e:
            log.error(f"freeform_groq_failed error={e!r}")
            # Vary the fallback by question so the TTS cache doesn't collapse
            # every failed turn into the exact same audio.
            short_q = (question or "")[:30].strip()
            return KnowledgeResult(
                intent="freeform", found=False,
                text_ml=(f"ക്ഷമിക്കണം, '{short_q}' എന്നതിനെ കുറിച്ച് "
                         "ഇപ്പോൾ കൃത്യമായ വിവരം എനിക്ക് നൽകാൻ കഴിയില്ല. "
                         "Reception-ൽ ബന്ധപ്പെടൂ."),
            )

    @staticmethod
    def _next_slot_after(doc: DoctorInfo, dow: int):
        """
        Find the doctor's earliest upcoming slot in the next 7 days
        starting from (dow + 1). Returns (dow, slot) or None.
        DB DOW convention: 0=Sun, 6=Sat.
        """
        for offset in range(1, 8):
            check_dow = (dow + offset) % 7
            for s in doc.slots:
                if s.dow == check_dow:
                    return (check_dow, s)
        return None
