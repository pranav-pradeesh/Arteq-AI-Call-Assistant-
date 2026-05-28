"""
Voice map: BCP-47 language code → Google Cloud TTS Neural2 voice name.

Defaults to the best female Neural2 voice for the hospital "Arya" persona.
Falls back to Standard voices for languages without Neural2 support.
"""
from __future__ import annotations

VOICE_MAP: dict[str, str] = {
    # Indian languages
    "ml-IN": "ml-IN-Neural2-A",   # Malayalam - best for Kerala hospital
    "hi-IN": "hi-IN-Neural2-A",   # Hindi
    "ta-IN": "ta-IN-Neural2-A",   # Tamil
    "bn-IN": "bn-IN-Neural2-A",   # Bengali
    "mr-IN": "mr-IN-Neural2-A",   # Marathi
    "gu-IN": "gu-IN-Neural2-A",   # Gujarati
    "kn-IN": "kn-IN-Neural2-A",   # Kannada
    "te-IN": "te-IN-Neural2-A",   # Telugu
    "pa-IN": "pa-IN-Standard-A",  # Punjabi (no Neural2)
    "ur-IN": "ur-IN-Standard-A",  # Urdu (no Neural2)
    # English variants
    "en-IN": "en-IN-Neural2-A",   # Indian English
    "en-US": "en-US-Neural2-F",   # American English
    "en-GB": "en-GB-Neural2-A",   # British English
    # European
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
    # Asian
    "zh-CN": "zh-CN-Neural2-A",   # Chinese Simplified
    "zh-TW": "zh-TW-Standard-A",  # Chinese Traditional
    "ja-JP": "ja-JP-Neural2-B",   # Japanese
    "ko-KR": "ko-KR-Neural2-A",   # Korean
    # Middle East / Africa
    "ar-XA": "ar-XA-Standard-A",  # Arabic
    "tr-TR": "tr-TR-Standard-A",  # Turkish
    "id-ID": "id-ID-Standard-A",  # Indonesian
    "vi-VN": "vi-VN-Standard-A",  # Vietnamese
    "th-TH": "th-TH-Standard-A",  # Thai
}

DEFAULT_VOICE = "ml-IN-Neural2-A"


def get_voice(language_code: str) -> str:
    """Return the best voice for the given BCP-47 language code."""
    if not language_code:
        return DEFAULT_VOICE
    # Exact match first
    if language_code in VOICE_MAP:
        return VOICE_MAP[language_code]
    # Region fallback: try base language
    base = language_code.split("-")[0]
    for code, voice in VOICE_MAP.items():
        if code.startswith(base + "-"):
            return voice
    return DEFAULT_VOICE
