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
