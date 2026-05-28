"""
Voice map: BCP-47 language code → Google Cloud TTS voice name.

Neural2 voices are used where available (higher quality, more expressive).
Standard voices are used as fallback for languages without Neural2 support.

All 22 scheduled languages of India are included, plus English variants
and major international languages for multilingual hospital deployments.
"""
from __future__ import annotations

VOICE_MAP: dict[str, str] = {
    # ── Indian languages — 8th Schedule (Neural2 where available) ────────────
    "ml-IN": "ml-IN-Neural2-A",   # Malayalam — primary for Kerala hospitals
    "hi-IN": "hi-IN-Neural2-A",   # Hindi
    "ta-IN": "ta-IN-Neural2-A",   # Tamil
    "te-IN": "te-IN-Neural2-A",   # Telugu
    "kn-IN": "kn-IN-Neural2-A",   # Kannada
    "bn-IN": "bn-IN-Neural2-A",   # Bengali
    "mr-IN": "mr-IN-Neural2-A",   # Marathi
    "gu-IN": "gu-IN-Neural2-A",   # Gujarati
    "pa-IN": "pa-IN-Standard-A",  # Punjabi
    "or-IN": "or-IN-Standard-A",  # Odia (Oriya)
    "ur-IN": "ur-IN-Standard-A",  # Urdu
    "ne-IN": "ne-IN-Standard-A",  # Nepali (India)
    "as-IN": "as-IN-Standard-A",  # Assamese
    "sd-IN": "sd-IN-Standard-A",  # Sindhi
    # Scheduled languages with limited/no Google TTS support
    # — these fall back to nearest supported language via get_voice()
    "kok-IN": "mr-IN-Neural2-A",  # Konkani → Marathi (closest)
    "mai-IN": "hi-IN-Neural2-A",  # Maithili → Hindi (closest)
    "brx-IN": "as-IN-Standard-A", # Bodo → Assamese (Northeast India)
    "doi-IN": "hi-IN-Neural2-A",  # Dogri → Hindi (Jammu region)
    "ks-IN": "ur-IN-Standard-A",  # Kashmiri → Urdu (closest script)
    "mni-IN": "bn-IN-Neural2-A",  # Manipuri/Meitei → Bengali (closest)
    "sat-IN": "or-IN-Standard-A", # Santali → Odia (Jharkhand/Odisha)
    "sa-IN": "hi-IN-Neural2-A",   # Sanskrit → Hindi (closest)
    # ── English variants ─────────────────────────────────────────────────────
    "en-IN": "en-IN-Neural2-A",   # Indian English
    "en-US": "en-US-Neural2-F",   # American English
    "en-GB": "en-GB-Neural2-A",   # British English
    "en-AU": "en-AU-Neural2-A",   # Australian English
    # ── European ─────────────────────────────────────────────────────────────
    "de-DE": "de-DE-Neural2-F",   # German
    "fr-FR": "fr-FR-Neural2-E",   # French
    "es-ES": "es-ES-Neural2-E",   # Spanish (Spain)
    "es-US": "es-US-Neural2-A",   # Spanish (Latin America)
    "it-IT": "it-IT-Neural2-A",   # Italian
    "pt-BR": "pt-BR-Neural2-C",   # Portuguese (Brazil)
    "pt-PT": "pt-PT-Standard-A",  # Portuguese (Portugal)
    "nl-NL": "nl-NL-Standard-A",  # Dutch
    "pl-PL": "pl-PL-Standard-A",  # Polish
    "ru-RU": "ru-RU-Standard-A",  # Russian
    "sv-SE": "sv-SE-Standard-A",  # Swedish
    "nb-NO": "nb-NO-Standard-A",  # Norwegian
    "da-DK": "da-DK-Standard-A",  # Danish
    "fi-FI": "fi-FI-Standard-A",  # Finnish
    # ── Asian ─────────────────────────────────────────────────────────────────
    "zh-CN": "zh-CN-Neural2-A",   # Chinese Simplified
    "zh-TW": "zh-TW-Standard-A",  # Chinese Traditional
    "ja-JP": "ja-JP-Neural2-B",   # Japanese
    "ko-KR": "ko-KR-Neural2-A",   # Korean
    "vi-VN": "vi-VN-Standard-A",  # Vietnamese
    "th-TH": "th-TH-Standard-A",  # Thai
    "id-ID": "id-ID-Standard-A",  # Indonesian
    "ms-MY": "ms-MY-Standard-A",  # Malay
    "si-LK": "si-LK-Standard-A",  # Sinhala (Sri Lanka)
    "ne-NP": "ne-NP-Standard-A",  # Nepali (Nepal)
    # ── Middle East / Africa ──────────────────────────────────────────────────
    "ar-XA": "ar-XA-Standard-A",  # Arabic (generic)
    "tr-TR": "tr-TR-Standard-A",  # Turkish
    "he-IL": "he-IL-Standard-A",  # Hebrew
    "sw-KE": "sw-KE-Standard-A",  # Swahili
    "af-ZA": "af-ZA-Standard-A",  # Afrikaans
}

DEFAULT_VOICE = "ml-IN-Neural2-A"


def get_voice(language_code: str) -> str:
    """Return the best available voice for the given BCP-47 language code."""
    if not language_code:
        return DEFAULT_VOICE
    # Exact match first
    if language_code in VOICE_MAP:
        return VOICE_MAP[language_code]
    # Region fallback: "hi" → "hi-IN", "ml" → "ml-IN", etc.
    base = language_code.split("-")[0]
    for code, voice in VOICE_MAP.items():
        if code.startswith(base + "-"):
            return voice
    return DEFAULT_VOICE
