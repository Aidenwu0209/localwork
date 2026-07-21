# frame-pairs — adjacent-frame novelty gate test set (M6.2)

50 pairs of adjacent screen frames (as OCR text + window metadata) for testing
the two-tier novelty gate (handbook §6.2 step 3): Jaccard token-set similarity
first (free), then `fast` (cheap) for the borderline band.

## Format

`frame_pairs.json` — array of 50 entries:

```json
{
  "id": "pair_001",
  "category": "high_overlap",       // auto-bucketed from _audit_jaccard
  "frame_a": {"app": "VS Code", "title": "main.py", "ocr_text": "..."},
  "frame_b": {"app": "VS Code", "title": "main.py", "ocr_text": "..."},
  "expected_jaccard_min": 0.85,
  "expected_novelty_max": 0.2,
  "expected_decision": "merge",
  "_audit_jaccard": 0.971
}
```

`expected_decision`: `merge` (high_overlap) · `uncertain` (borderline, needs
`fast`) · `new` (novel).

## Bands

Each pair is bucketed by its **computed** whitespace-token Jaccard (lowercased),
so the label always matches the band the numbers fall in:

| band | Jaccard | expected decision |
|---|---|---|
| `high_overlap` | ≥ 0.85 | merge into previous event |
| `borderline` | 0.5 – 0.85 | `fast` model decides |
| `novel` | < 0.5 | new event |

Run `python3 _build_pairs.py` to regenerate; it re-buckets every pair and prints
the per-band ranges. The cut points (0.85 merge / 0.5 new) match the novelty
gate's configurable defaults; `_audit_jaccard` lets a test assert exact overlap.

## Distribution note

The build script auto-classifies each pair from its real Jaccard rather than
trusting the source label, so the per-band counts reflect genuine overlap:
**13 high / 16 borderline / 21 novel** at the current cut points. This is
intentional — every pair's label is provably consistent with its numbers, which
matters more for gate testing than hitting an exact 20/20/10 split. To rebalance,
edit the `*_pairs` lists in `_build_pairs.py` (make `high_pairs` edits smaller,
`novel_pairs` more overlapping).

## Privacy

All content is fictional — `acme-api`, `northwind`, `jordan@northwind-pay`,
`dejaview-demo`. No real names, projects, hosts, or tokens. Consistent with the
Honcho deriver's sanitised synthetic persona (M2.3).
