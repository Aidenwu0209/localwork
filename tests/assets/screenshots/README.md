# screenshots — synthetic OCR/perceive test set (M6.1)

20 fully-synthetic source screenshots for validating the OCR layer (ocrd / PP-OCRv6)
and the perceive semantic stage. Each PNG is paired with a ground-truth JSON
listing the entities a correct pipeline should extract — used by M5.2's
medium-vs-small OCR accuracy A/B and by M3.4's end-to-end frame test.

`code_01_p31_focus.png` is one additional P3.1 benchmark view derived only
from the synthetic `code_01.png`: crop `(left=0, top=0, right=960,
bottom=540)`, then nearest-neighbor resize to 1920×1080. It preserves the
active `parse.py` tab and visible code while keeping the filename legible after
VLM image resizing. P3.1 pins the derived PNG by SHA256; it contains no new
content or personal data.

## Contents

| category | files | what it tests |
|---|---|---|
| `code_01..05` | 5 | identifiers (functions, classes, imports) on a dark IDE theme |
| `terminal_01..05` | 5 | error codes + URLs (pg_trgm exact-substring retrieval targets) |
| `webpage_01..05` | 5 | mixed CN/EN article text, address-bar URLs, numbers |
| `chat_01..05` | 5 | chat identifiers (fictional usernames) |

All 1920×1080 RGB, rendered with system fonts (Menlo / Helvetica / PingFang).

## Ground-truth format

`<name>.json` next to each `<name>.png`:

```json
{
  "category": "terminal",
  "text_snippets": ["error: ROCM-4042: ...", "https://docs.demo-acme.io/errors/ROCM-4042"],
  "identifiers": ["dejaview-core", "acme-parser", "hip_alloc"],
  "urls": ["https://docs.demo-acme.io/errors/ROCM-4042"],
  "error_codes": ["ROCM-4042"],
  "numbers": ["2048", "1024", "142"],
  "image": "terminal_01.png"
}
```

- `error_codes` / `urls` — exact-substring retrieval targets (pg_trgm lane).
- `identifiers` — semantic + identifier search.
- `numbers` — typed-value extraction.

## Regenerate

```bash
python3 generate.py        # overwrites the 20 PNGs + JSONs deterministically
```

## Privacy

Fully synthetic. Fictional projects (`dejaview-core`, `acme-parser`,
`acme-api`), fictional hosts (`docs.demo-acme.io`, `northwind`), fictional error
codes (`ROCM-4042`), fictional usernames (`alex_w`, `morgan_dev`). No real code,
names, accounts, URLs, or screenshots of real screens.
