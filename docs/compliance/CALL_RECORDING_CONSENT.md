# Call Recording & Processing — Consent Capture

> Verbal consent and notice at the **start of every call**, satisfying the DPDP Act
> notice requirement and standard call-recording consent practice. Scripts are
> provided in the caller's language; the agent already greets in 11 languages.

---

## 1. Principle

Consent must be **informed, specific, and freely given**. At call start the patient is
told: (1) they've reached an AI assistant, (2) the call may be recorded/transcribed,
(3) the purpose, (4) how to decline. Declining recording must **not** deny service —
the call proceeds without recording.

## 2. Where this fits in the call flow

```
Call connects
  → Consent notice (one short sentence, caller's language)   ← THIS DOCUMENT
  → Greeting + "how can I help?"
  → Dialogue (book / reschedule / route / emergency)
```

- If recordings are **disabled** (`VOBIZ_RECORD_CALLS=false`), the notice still states
  the call is handled by an AI assistant and may be transcribed.
- If the caller **declines recording**, staff/automation set the call to no-record and
  the patient is served normally.

## 3. Consent notice scripts (recording enabled)

| Language | Notice |
|---|---|
| English | "You've reached <Hospital> assistant. This call may be recorded and transcribed to help with your appointment. Say 'no recording' if you'd prefer not to be recorded." |
| Malayalam | "ഇത് <Hospital> അസിസ്റ്റന്റ് ആണ്. നിങ്ങളുടെ appointment-ന് സഹായിക്കാൻ ഈ കോൾ റെക്കോർഡ് ചെയ്തേക്കാം. വേണ്ടെങ്കിൽ 'റെക്കോർഡ് വേണ്ട' എന്ന് പറയൂ." |
| Hindi | "यह <Hospital> असिस्टेंट है। आपकी अपॉइंटमेंट में मदद के लिए यह कॉल रिकॉर्ड की जा सकती है। न चाहें तो 'रिकॉर्ड नहीं' कहें।" |
| Tamil | "இது <Hospital> உதவியாளர். உங்கள் சந்திப்புக்கு உதவ இந்த அழைப்பு பதிவு செய்யப்படலாம். வேண்டாம் என்றால் 'பதிவு வேண்டாம்' எனச் சொல்லுங்கள்." |
| Telugu | "ఇది <Hospital> అసిస్టెంట్. మీ అపాయింట్‌మెంట్‌కు సహాయపడేందుకు ఈ కాల్ రికార్డ్ కావచ్చు. వద్దనుకుంటే 'రికార్డ్ వద్దు' అని చెప్పండి." |
| Kannada | "ಇದು <Hospital> ಸಹಾಯಕ. ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್‌ಗೆ ಸಹಾಯ ಮಾಡಲು ಈ ಕರೆ ರೆಕಾರ್ಡ್ ಆಗಬಹುದು. ಬೇಡವಾದರೆ 'ರೆಕಾರ್ಡ್ ಬೇಡ' ಎನ್ನಿ." |

> Other supported languages (Bengali, Gujarati, Marathi, Punjabi, Odia) follow the
> same structure; translations to be finalised with the hospital before go-live.

## 4. Recording the consent decision

- The patient's choice is logged against the call as a consent flag.
- A "declined" decision suppresses recording for that call and is retained as proof of
  the patient's preference.
- **[Roadmap]** A persisted `consent_recording` boolean on the call record so the
  decision is queryable and auditable per call.

## 5. Emergency exception

If the caller presents an emergency (chest pain, bleeding, unconscious, etc.), the
agent prioritises **immediate escalation** (`alert_emergency`) over the consent
preamble. Notice can follow once the patient is safe. This reflects the vital-interest
basis for processing in a medical emergency.

## 6. Operational checklist

- [ ] Consent notice enabled in every caller language used by the hospital
- [ ] `VOBIZ_RECORD_CALLS` set deliberately (with consent if `true`)
- [ ] "Decline recording" path tested end-to-end
- [ ] Consent decision retained and auditable
- [ ] Emergency-first behaviour verified
