# sentinel — privacy sentinel test set (M6.3)

40 fully-synthetic screenshots for validating the privacy sentinel's recall
(block rate on sensitive frames) and precision (no false-blocks on normal
frames). Used by T2.1 to measure interception rate vs. false-positive rate.

## Contents

| prefix | n | category | expected |
|---|---|---|---|
| `banking_01..05` | 5 | `banking_finance` | **block** |
| `password_01..05` | 5 | `password_prompt` | **block** |
| `private_chat_01..05` | 5 | `private_chat` | **block** |
| `id_document_01..05` | 5 | `id_document` | **block** |
| `normal_code_01..05` | 5 | `normal` | allow |
| `normal_doc_01..05` | 5 | `normal` | allow |
| `normal_terminal_01..05` | 5 | `normal` | allow |
| `normal_web_01..05` | 5 | `normal` | allow |

**Sensitive 20 (block) / normal 20 (allow).** Each PNG is paired with a JSON
giving the expected category, decision, and minimum confidence.

## Ground-truth format

```json
{
  "category": "banking_finance",
  "expected_decision": "block",
  "confidence_min": 0.7,
  "notes": "fictional banking_finance — no real PII",
  "image": "banking_01.png"
}
```

The category names match the sentinel's JSON output schema (handbook §6.2.1):
`password_prompt | banking_finance | private_chat | id_document | adult | normal`.

## What each sensitive class contains (the features a real sentinel keys on)

- **banking_finance** — fictional bank ("Acme Bank" / "Demo Credit Union"),
  masked PANs (`****1234`), `$` balances, login form.
- **password_prompt** — dot-masked password fields, "Master Password" / vault UI,
  masked vault entries.
- **private_chat** — DM thread with private content (contract draft, salary band
  "140-160k", "don't share externally").
- **id_document** — fake ID card ("DEMO REPUBLIC NATIONAL ID SPECIMEN"), fake
  name "JORDAN LEE", fake number "S-1234567-D", KYC upload form.

The normal frames (code / docs / terminal / public webpage) deliberately
contain **none** of: passwords, account numbers, balances, DMs, ID numbers.

## Regenerate

```bash
python3 generate.py        # overwrites the 40 PNGs + JSONs deterministically
```

## Privacy

Fully synthetic. Fictional bank ("Acme Bank"), fictional names ("Jordan Lee"),
fictional ID numbers ("S-1234567-D"), fictional hosts ("demo-acme.io"). No real
accounts, passwords, names, IDs, or chat content. Safe for the privacy narrative.
