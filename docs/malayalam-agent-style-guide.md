# Malayalam Agent Style Guide

This document is the canonical style guide for the Malayalam conversational agent used in the Arteq AI Call Assistant project.

Principles (short)
- Greetings: Always use English greetings exactly: "Good morning!", "Good afternoon!", "Good evening!" followed by Malayalam text.
- Medical/technical nouns & names: keep in English (Cardiology, appointment, slot, Dr. [Name]). Use hyphenated Malayalam case endings as needed: e.g., `Dr. Ranjith Menon-ന്`.
- Default polite pronoun: use "നിങ്ങൾ" for outward-facing messages.
- Default time format: use `11:00 AM` style (AM/PM) in mixed messages. Keep consistent across UI.

Suffix rule for English names
- Default: use `-ന്` appended to the English name when you need a short dative suffix.
  - Example: `Dr. Ranjith Menon-ന് available slot ഇല്ല.`
- Exception: if the English name ends with a vowel letter (a, e, i, o, u, case-insensitive) treat as vowel-ending and use `-യ്ക്ക്`.
  - Example: `Dr. Lakshmi-യ്ക്ക് available slot ഇല്ല.`, `Dr. Anita-യ്ക്ക് appointment confirm ചെയ്തു.`
- Possessives: use `-യുടെ` or `-ന്റെ` for "Dr. X's" as appropriate: `Dr. Lakshmiയുടെ timings` or `Dr. Ranjith Menon-ന്റെ timings`.
- Hyphenation: we prefer the hyphen `Dr. Name-ന്` for clarity in templates. The UI renderer may remove the hyphen if needed.

Heuristic notes & edge cases
- Basic orthographic heuristic (recommended): check the final ASCII letter: vowels = [a,e,i,o,u,A,E,I,O,U]. This handles most names but not all pronunciation cases.
- For names with silent final letters (e.g., names ending with 'e' where pronunciation differs) or non-English names, consider maintaining a small exception list or using a pronunciation lookup.
- Strip punctuation and titles before checking the final character (trim trailing periods, commas, parentheses).

Sandhi & Samasam guidance
- Use Sandhi and Samasam in formal/written confirmations and SMS/receipts (e.g., `വഴിയരികിൽ`, `ജലപാതം`).
- In conversational UI messages, prefer slightly simpler forms for clarity but use Sandhi where it shortens the text elegantly.

Participles, complex verbs & contractions
- Use participles naturally to sound native: `നിങ്ങൾ പറഞ്ഞ തീയതി`, `ഞാൻ കണ്ട ഡോക്ടർ`.
- Conditional/concessive/purpose forms are fine: `വന്നാൽ`, `വന്നാലും`, `പഠിക്കാൻ`.
- Use spoken contractions in chat flows: `എന്താണ്` -> `എന്താ`, `എങ്ങനെയാണ്` -> `എങ്ങനെയാ`. Avoid contractions in formal SMS.

Emphasis particles
- Use `തന്നെ`, `പോലും`, `മാത്രമല്ല`, `അല്ലേ` sparingly to sound natural but keep clarity.

Respect levels & regional defaults
- Default to Central Kerala polite style (`നിങ്ങൾ`). Provide casual variants using `നീ` only if the persona is intentionally informal.
- Avoid heavy regional markers unless the target audience is region-specific.

Templates and files
- templates/agent_responses.json — contains intent → response templates (placeholders included). Use these in the bot.
- utils/suffix_logic.py — small helper implementing the vowel-ending heuristic.

Testing
- Review messages with 2–3 native speakers from different Kerala regions (Malabar, Central, Trivandrum) to ensure no unintended regional markers.
- Validate SMS/Email copies use more formal/literary Malayam (Sandhi/Samasam) if desired.


---

Revision: initial commit
