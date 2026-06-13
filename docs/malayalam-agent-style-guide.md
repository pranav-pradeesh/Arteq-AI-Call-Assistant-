# Malayalam Agent Style Guide

This document is the canonical style guide for the Malayalam conversational agent used in the Arteq AI Call Assistant project.

Principles (short)
- Greetings: Always use English greetings exactly: "Good morning!", "Good afternoon!", "Good evening!" followed by Malayalam text.
- Medical/technical nouns & names: keep in English (Cardiology, appointment, slot, Dr. [Name]). Use hyphenated Malayalam case endings as needed: e.g., `Dr. Ranjith Menon-ന്` or `Dr. Lakshmi-യ്ക്ക്` per suffix rule.
- Default polite pronoun: use "നിങ്ങൾ" for outward-facing messages.
- Default time format: use `11:00 AM` style (AM/PM) in written/UI/SMS text. In SPOKEN replies (voice agent) prefer natural Malayalam time words: രാവിലെ / ഉച്ചയ്ക്ക് / വൈകുന്നേരം / രാത്രി with the hour ("രാവിലെ 10 മണി").

Suffix rule for English names
- Default: use `-ന്` appended to the English name when you need a short dative suffix.
  - Example: `Dr. Ranjith Menon-ന് available slot ഇല്ല.`
- Exception: if the English name ends with a vowel letter (a, e, i, o, u, case-insensitive) treat as vowel-ending and use `-യ്ക്ക്`.
  - Example: `Dr. Lakshmi-യ്ക്ക് available slot ഇല്ല.`, `Dr. Anita-യ്ക്ക് appointment confirm ചെയ്തു.`
- Possessives: same vowel rule — vowel-ending → `-യുടെ`, otherwise `-ന്റെ`: `Dr. Lakshmi-യുടെ timings`, `Dr. Ranjith Menon-ന്റെ timings`. Use `choose_possessive()` from `utils/suffix_logic.py`.
- IMPORTANT — no suffix at all for a plain subject: "Dr. X ഇന്ന് ലഭ്യമല്ല." The dative (`Dr. X-ന് ലഭ്യമല്ല`) is wrong — it means "not available *to* Dr. X".

Native phrasing corrections (places where native Malayalam speakers prefer different wording)
- Reception opening:
  - Avoid: `എന്താ വേണ്ടത്?` (colloquial, abrupt)
  - Prefer: `എന്താണ് വേണ്ടത്?` or `എങ്ങനെ സഹായിക്കാം?`
- Patient request:
  - Avoid: `ഡോക്ടറിനായിട്ട്`.
  - Prefer: `ഡോക്ടറെ കാണാൻ` (e.g., `എനിക്ക് Cardiology വിഭാഗത്തിലെ ഏതെങ്കിലും ഡോക്ടറെ കാണാൻ appointment വേണം.`)
- Doctor availability:
  - Avoid: `ഡോ. X-ക്ക് അപ്പോയിന്റ്മെന്റ് ഇല്ല.` (sounds like the doctor lacks an appointment)
  - Avoid: `ഡോ. X-ന് ഇന്ന് ലഭ്യമല്ല.` (dative on the subject — means "not available *to* Dr. X")
  - Prefer: `ഡോ. X ഇന്ന് ലഭ്യമല്ല.` or `ഡോ. X-ന്റെ ഇന്നത്തെ appointments എല്ലാം ബുക്ക് ആയിട്ടുണ്ട്.` (possessive suffix per the vowel rule; `ഇന്നത്തെ` — adjectival "today's" — not a floating `ഇന്ന്`)
- Follow-up question:
  - Avoid: `മറ്റൊരു ഡോക്ടർക്ക് ആഗ്രഹമുണ്ടോ?` (awkward)
  - Prefer: `മറ്റൊരു ദിവസത്തേക്കോ മറ്റൊരു ഡോക്ടറുടെയോ അപ്പോയിന്റ്മെന്റ് വേണോ?` or `മറ്റൊരു ഡോക്ടറെ കാണാൻ താല്പര്യമുണ്ടോ?`
- Asking about other doctors:
  - Prefer plural: `വേറെ ഏത് ഡോക്ടർമാരാണ് ലഭ്യമായിരിക്കുന്നത്?` or `വേറെ ഏത് ഡോക്ടർമാർ ലഭ്യമാണ്?`
- Final response:
  - Prefer complete polite phrasing: `Dr. X-യെ കാണാൻ ഏത് സമയമാണ് സൗകര്യം?` or `Dr. Xക്ക് available slots: ... ഏത് സമയം വേണം?`

Sample native-sounding conversation (greetings in English; medical/English words kept in English)

Arya: Good morning! Kairali Multi-Speciality Hospital-ലേക്ക് സ്വാഗതം. ഞാൻ Arya. എങ്ങനെ സഹായിക്കാം?
You: എനിക്ക് Cardiology വിഭാഗത്തിലെ ഏതെങ്കിലും ഡോക്ടറെ കാണാൻ ഒരു appointment വേണം.
Arya: Dr. Lakshmi Nair ഇന്ന് ലഭ്യമല്ല. (Alternative formal:) Dr. Lakshmi Nair-ന്റെ ഇന്നത്തെ appointments എല്ലാം ബുക്ക് ആയിട്ടുണ്ട്. മറ്റൊരു ഡോക്ടറെ കാണണോ, അതോ വേറൊരു ദിവസത്തേക്ക് നോക്കണോ?
You: വേറെ ഏത് ഡോക്ടർമാരാണ് ലഭ്യമായിരിക്കുന്നത്?
Arya: ഇന്ന് Dr. Suresh Pillai ലഭ്യമാണ്. Available slots: രാവിലെ 10 മണി, ഉച്ച കഴിഞ്ഞ് 2 മണി. ഏത് സമയമാണ് സൗകര്യം?
You: രാവിലെ 10 മണി മതി.
Arya: Patient-ന്റെ പേര് പറയാമോ? ഞാൻ appointment register ചെയ്ത് confirmation number SMS ആയി അയക്കാം.
You: (details)
Arya: Appointment confirmed: Dr. Suresh Pillai, [date], 10:00 AM. Confirmation number [XXXX]. SMS അയക്കണോ?

Templates and files
- templates/agent_responses.json — contains intent → response templates (placeholders included).
- utils/suffix_logic.py — vowel-ending heuristic for both dative (`choose_suffix`) and possessive (`choose_possessive`) suffixes. Templates use the `{{poss_suffix}}` placeholder; fill it with `choose_possessive(doctor_name)` at render time.

Testing & notes
- Review messages with 2–3 native speakers from different Kerala regions to ensure no unintended regional markers.
- For SMS/receipts you can use slightly more literary forms (Sandhi/Samasam) if desired.

---

Revision: improve natural Malayalam phrasing; add native-sounding dialogue and updated templates
