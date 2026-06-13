"""
TTS text normalisation helpers — no livekit dependency.

Extracted so they can be imported and unit-tested without the full agent stack.
livekit_agent.py imports from here; tests import from here too.
"""
from __future__ import annotations

import re

# Unicode script ranges → Sarvam Bulbul v3 target_language_code.
_SCRIPT_RANGES = [
    ("ml-IN", 0x0D00, 0x0D7F),
    ("ta-IN", 0x0B80, 0x0BFF),
    ("te-IN", 0x0C00, 0x0C7F),
    ("kn-IN", 0x0C80, 0x0CFF),
    ("hi-IN", 0x0900, 0x097F),
    ("bn-IN", 0x0980, 0x09FF),
    ("gu-IN", 0x0A80, 0x0AFF),
    ("pa-IN", 0x0A00, 0x0A7F),
    ("od-IN", 0x0B00, 0x0B7F),
]


def split_mixed_script(text: str, fallback: str) -> list:
    """Split text into [(segment, lang_code)] pairs at script boundaries.

    Neutral chars (ASCII, punctuation, spaces) before the first native-script
    char are tagged 'en-IN'; those after a native block stay with that block.
    Single-script or pure-Latin text returns a single-item list.

    Use this to synthesize each segment with the correct TTS language so that
    English words in an otherwise-Indic utterance are pronounced in English.
    """
    if not text:
        return []

    # Per-char: detected native lang or None (Latin/punctuation)
    char_langs: list = []
    for ch in text:
        cp = ord(ch)
        detected = None
        for code, lo, hi in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                detected = code
                break
        char_langs.append(detected)

    # If no native chars found → pure Latin → single en-IN segment
    if not any(l is not None for l in char_langs):
        return [(text, "en-IN")]

    # Resolve each position: leading neutral → en-IN; neutral after native → that native lang
    resolved: list = []
    last_native = None
    for lc in char_langs:
        if lc is not None:
            last_native = lc
        resolved.append(last_native if last_native else "en-IN")

    # Group into maximal same-lang runs
    segments: list = []
    seg_chars: list = [text[0]]
    seg_lang: str = resolved[0]
    for i in range(1, len(text)):
        if resolved[i] == seg_lang:
            seg_chars.append(text[i])
        else:
            seg_text = "".join(seg_chars).strip()
            if seg_text:
                segments.append((seg_text, seg_lang))
            seg_chars = [text[i]]
            seg_lang = resolved[i]
    seg_text = "".join(seg_chars).strip()
    if seg_text:
        segments.append((seg_text, seg_lang))

    return segments or [(text, fallback)]


def detect_tts_lang(text: str, fallback: str) -> str:
    """Pick target_language_code from the dominant Indic script in `text`.

    Counts chars per script; the script with the most chars wins. Pure Latin
    (no Indic chars) → returns `fallback` so Manglish tenants (fallback=ml-IN)
    get Malayalam phonetics and English tenants (fallback=en-IN) are unchanged.
    Empty/unknown → fallback.
    """
    counts: dict[str, int] = {}
    for ch in text:
        cp = ord(ch)
        for code, lo, hi in _SCRIPT_RANGES:
            if lo <= cp <= hi:
                counts[code] = counts.get(code, 0) + 1
                break
    if counts:
        return max(counts, key=counts.get)
    return fallback


# Bulbul v3 speaker roster (Sarvam). All speakers are multilingual, so any voice
# can speak any of the 11 supported languages via the language code.
BULBUL_V3_SPEAKERS = {
    "shubh", "aditya", "ritu", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
    "manan", "sumit", "roopa", "kabir", "aayan", "ashutosh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali",
}

# Per-language voice selection. Bulbul v3 voices are multilingual, but matching a
# voice whose timbre/accent best fits each language makes every reply feel native
# instead of one voice straining across scripts. Sarvam publishes NO official
# per-language quality matrix, so these are warm female receptionist voices chosen
# by linguistic fit — this dict is the single place to audition and tune them.
# Unmapped languages fall back to the constructor's default speaker.
LANG_VOICE = {
    "ml-IN": "kavitha",   # Malayalam
    "ta-IN": "shruti",    # Tamil
    "te-IN": "roopa",     # Telugu
    "kn-IN": "kavya",     # Kannada
    "hi-IN": "priya",     # Hindi
    "mr-IN": "rupali",    # Marathi
    "bn-IN": "ishita",    # Bengali
    "gu-IN": "pooja",     # Gujarati
    "pa-IN": "simran",    # Punjabi
    "od-IN": "suhani",    # Odia
    "en-IN": "tanya",     # English
}


def voice_for_lang(lang: str, default: str) -> str:
    """Return the best-fit Bulbul v3 speaker for a language code, else `default`."""
    return LANG_VOICE.get(lang, default)


# Languages that use a non-Latin script natively — for these we prefer name_ml
# (native script) over the romanized Latin name so TTS phonetics are correct.
_INDIC_LANGS = {
    "ml-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN",
    "bn-IN", "gu-IN", "pa-IN", "od-IN", "mr-IN",
}


def name_for_lang(name: str, name_ml: str, lang: str) -> str:
    """Return the native-script hospital name when TTS lang is Indic, else Latin.

    Bulbul mispronounces romanized Indic words (e.g. "Kirali") when the TTS
    target is an Indic language because the phoneme inventory is wrong for Latin
    chars. Passing the native-script name (name_ml) fixes pronunciation.
    Falls back to `name` when name_ml is empty or lang is Latin/Manglish.
    """
    if lang in _INDIC_LANGS and name_ml:
        return name_ml
    return name


_TTS_EN_MAP = {
    "ഡോക്ടര്‍": "Doctor", "ഡോക്ടർ": "Doctor", "ഡോക്ടര്": "Doctor", "ഡോ.": "Doctor",
    "അപ്പോയിന്റ്മെന്റ്": "appointment", "അപ്പോയിന്റ്മെൻറ്": "appointment",
    "കാർഡിയോളജി": "cardiology", "ന്യൂറോളജി": "neurology",
    "ഓകെ": "OK", "ഇയെസ്": "Yes", "ഇയേസ്": "Yes", "നോ": "No",
    "സ്കാനിംഗ്": "scanning", "സ്കാനിങ്": "scanning",
    "റിപ്പോർട്ട്": "report", "ടോക്കൺ": "token", "കൗണ്ടർ": "counter",
}
_TTS_EN_RE = re.compile("|".join(re.escape(k) for k in sorted(_TTS_EN_MAP, key=len, reverse=True)))

_TTS_EN_STEMS = {
    "അപ്പോയിന്റ്മെന്റ": "appointment", "അപ്പോയിന്റ്മെൻറ": "appointment",
    "കാർഡിയോളജി": "cardiology", "ന്യൂറോളജി": "neurology",
    "സ്കാനി": "scanning", "റിപ്പോർട്ട": "report", "ഡോക്ടറ": "Doctor",
}
_TTS_EN_STEM_RE = re.compile(
    "(?:" + "|".join(re.escape(s) for s in sorted(_TTS_EN_STEMS, key=len, reverse=True)) + r")[ഀ-ൿ]*"
)
_TTS_EN_STEM_LOOKUP = sorted(_TTS_EN_STEMS, key=len, reverse=True)


def _stem_repl(m: "re.Match") -> str:
    word = m.group(0)
    for stem in _TTS_EN_STEM_LOOKUP:
        if word.startswith(stem):
            return " " + _TTS_EN_STEMS[stem] + " "
    return word


def normalize_for_tts(text: str) -> str:
    out = _TTS_EN_STEM_RE.sub(_stem_repl, text)
    out = _TTS_EN_RE.sub(lambda m: " " + _TTS_EN_MAP[m.group(0)] + " ", out)
    out = re.sub(r"\s{2,}", " ", out)
    out = re.sub(r"\s+([,.?!।])", r"\1", out)
    return out.strip()
