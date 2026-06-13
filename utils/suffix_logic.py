"""
utils/suffix_logic.py

Small helper functions to decide which Malayalam case suffix to append to an
English name, based on a simple orthographic heuristic (does the name end in a
vowel letter?).

Dative ("to/for Dr. X"):      vowel-ending → "-യ്ക്ക്", otherwise "-ന്"
Possessive ("Dr. X's"):       vowel-ending → "-യുടെ",  otherwise "-ന്റെ"

Usage:
  from utils.suffix_logic import format_doctor, choose_possessive
  print(format_doctor("Lakshmi"))        # Dr. Lakshmi-യ്ക്ക്
  print(format_doctor("Ranjith Menon"))  # Dr. Ranjith Menon-ന്
  print(choose_possessive("Lakshmi"))    # -യുടെ
  print(choose_possessive("Menon"))      # -ന്റെ

"""

import re

VOWELS = set("aeiouAEIOU")


def _ends_with_vowel(name: str) -> bool:
    """True if the cleaned name ends with a vowel letter (a/e/i/o/u)."""
    if not name:
        return False
    cleaned = re.sub(r"[\W_]+$", "", name.strip())
    return bool(cleaned) and cleaned[-1] in VOWELS


def choose_suffix(name: str) -> str:
    """Return the dative suffix: "-യ്ക്ക്" (vowel-ending) or "-ന്" (otherwise)."""
    return "-യ്ക്ക്" if _ends_with_vowel(name) else "-ന്"


def choose_possessive(name: str) -> str:
    """Return the possessive suffix: "-യുടെ" (vowel-ending) or "-ന്റെ" (otherwise)."""
    return "-യുടെ" if _ends_with_vowel(name) else "-ന്റെ"


def format_doctor(name: str) -> str:
    """Return a formatted doctor string like: 'Dr. Name-ന്' or 'Dr. Name-യ്ക്ക്'."""
    return f"Dr. {name}{choose_suffix(name)}"


def format_doctor_possessive(name: str) -> str:
    """Return a possessive doctor string like: 'Dr. Name-ന്റെ' or 'Dr. Name-യുടെ'."""
    return f"Dr. {name}{choose_possessive(name)}"


if __name__ == "__main__":
    examples = ["Lakshmi", "Anita", "Ranjith Menon", "Rae", "Jose", "Suresh Pillai"]
    for ex in examples:
        print(format_doctor(ex), "|", format_doctor_possessive(ex))
