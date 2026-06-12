"""
utils/suffix_logic.py

Small helper functions to decide whether to append the Malayalam dative suffix "-ന്" or "-യ്ക്ക്"
based on the final character of an English name (a simple orthographic heuristic).

Usage:
  from utils.suffix_logic import format_doctor
  print(format_doctor("Lakshmi"))  # Dr. Lakshmi-യ്ക്ക്
  print(format_doctor("Ranjith Menon"))  # Dr. Ranjith Menon-ന്

"""

import re

VOWELS = set("aeiouAEIOU")


def choose_suffix(name: str) -> str:
    """Return the appropriate suffix string, either "-യ്ക്ക്" or "-ന്".

    Heuristic: if the cleaned name ends with a vowel letter (a/e/i/o/u), return "-യ്ക്ക്",
    otherwise return "-ന്".
    """
    if not name:
        return "-ന്"

    # strip trailing punctuation and whitespace
    cleaned = re.sub(r"[\W_]+$", "", name.strip())
    if not cleaned:
        return "-ന്"

    last_char = cleaned[-1]
    return "-യ്ക്ക്" if last_char in VOWELS else "-ന്"


def format_doctor(name: str) -> str:
    """Return a formatted doctor string like: 'Dr. Name-ന്' or 'Dr. Name-യ്ക്ക്'."""
    suffix = choose_suffix(name)
    return f"Dr. {name}{suffix}"


if __name__ == "__main__":
    examples = ["Lakshmi", "Anita", "Ranjith Menon", "Rae", "Jose"]
    for ex in examples:
        print(format_doctor(ex))
