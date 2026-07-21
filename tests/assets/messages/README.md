# messages — synthetic user messages (M6.2)

30 synthetic user messages used to exercise the Honcho deriver (M2.6 / T0.9):
feed them as a single peer's session, let the deriver extract atomic facts, then
probe the dialectic ("what is this person working on?", "what tools do they
prefer?").

## Format

`synthetic_messages.json` — array of 30:

```json
{"id": "msg_001", "role": "user", "content": "...", "tags": ["identity", "background"]}
```

Tags overlap across identity / project / tools / habits / problem / question
categories (16 distinct tags total).

## Persona

Fictional: **Jordan Lee**, Singapore-based backend engineer at **Northwind Pay**
(a fictional mid-size fintech), 3.5 years tenure, on the payments core. Works on
`acme-api` (fictional). This matches the sanitised Honcho deriver few-shots
(M2.3) — same persona density, zero real PII. No real names, employers,
projects, or locations.

## Privacy

Fully synthetic. Safe to ingest through any provider (local or cloud stand-in)
during dev. Consistent with handbook §0 ("test with synthetic data").
