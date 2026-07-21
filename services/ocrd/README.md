# ocrd

Deterministic verbatim OCR microservice for DejaView (handbook §6.1). Runs
the PP-OCR series via ONNX Runtime and exposes a single `POST /ocr` endpoint.
It is **not an LLM** and does **not** go through the LiteLLM gateway —
`memoryd` connects to it directly via `OCR_URL`.

```
memoryd  --POST /ocr (image)-->  ocrd :8006  -->  {"full_text","blocks":[...]}
```

## Contract (handbook §6.1)

```
POST /ocr          multipart/form-data, field "file" = image (png/jpg/webp/...)
  -> 200 {
        "full_text": "...",                      # newline-joined block texts
        "blocks":   [ { "text": "...",
                        "bbox": [x1,y1,x2,y2],   # axis-aligned, pixel coords
                        "conf": 0.987 } ],       # [0,1]
        "backend":    "rapidocr" | "paddleocr",  # diagnostic, safe to ignore
        "elapsed_ms": 825.3,
        "n_blocks":   20
      }

GET  /health        -> { "status":"ok", "backend": "...", "engine_loaded": bool }
```

`bbox` is `[x1, y1, x2, y2]` (top-left, bottom-right) and is layout-compatible
with `memoryd.models.OcrBlock`. `full_text` joins block texts with `\n`.

Error paths: empty upload -> `400`, unsupported `Content-Type` -> `415`,
payload >25 MiB -> `413`, undecodable image bytes -> `422`.

## Backend choice

Two PP-OCR-series backends ship behind one protocol in `engine.py`:

| backend              | model         | when to use                                  |
|----------------------|---------------|----------------------------------------------|
| `rapidocr` (default) | PP-OCRv4 ONNX | **Mac / Apple Silicon dev** - fast, light.   |
| `paddleocr`          | PP-OCRv6 ONNX | **Production on EPYC CPU** - newer, higher-accuracy. |

Select with the `OCR_BACKEND` env var. The default is `rapidocr` because on
this Mac (Apple M5) PaddleOCR 3.7's ONNX path is unusably slow
(20-116 s/image vs ~0.8-1.3 s for rapidocr), while accuracy on the synthetic
test set is at least as good. See `docs/verification-log.md`
"2026-07-21 (M5.1)" for the full benchmark and the reasoning. The EPYC
production target should switch to `paddleocr` (re-verify in T0.5/T1.8 A/B).

Both backends run the handbook's "preprocessing all-off" rule: document
orientation classify, document unwarping and textline orientation are all
disabled (screenshots are always upright and flat - pure speedup).

## Run

```bash
cd services/ocrd
uv run python -m ocrd            # serves 127.0.0.1:8006 (loopback only)
# or, to force the production backend on a box that has the PaddleOCR weights:
OCR_BACKEND=paddleocr uv run python -m ocrd
```

The service boots in under a second; the OCR model is built lazily on the
first `POST /ocr` (RapidOCR ~0.1 s to load; PaddleOCR ~9 s + one-time model
download into `~/.paddlex/official_models/`). `/health` returns before the
model is loaded so it stays cheap.

## Smoke test

```bash
# health (engine not yet built)
curl -s http://127.0.0.1:8006/health
# -> {"status":"ok","backend":"rapidocr","engine_loaded":false}

# OCR a screenshot
curl -s -F "file=@../../tests/assets/screenshots/terminal_01.png" \
     http://127.0.0.1:8006/ocr | python3 -m json.tool
```

The three acceptance images from `tests/assets/screenshots/` (each ships with
a ground-truth `.json`):

| image         | n blocks | median latency | key entities recognised                                    |
|---------------|---------:|---------------:|------------------------------------------------------------|
| terminal_01   |       12 |        ~780 ms | `docs.demo-acme.io/errors/...` URL, `hip_alloc.rs:142`, `acme-parser` (ROCM-4042 read as `R0cM-4042` - single-glyph noise) |
| webpage_01    |       20 |        ~870 ms | all three `docs.demo-acme.io/{zh,en}/...` URLs exact; mixed CN+EN paragraph transcribed |
| code_01       |       54 |       ~1200 ms | `def parse_timeline`, `class TimelineParser`, `from acme_parser import ...` exact |

Latencies are **Mac M5 reference values, pending EPYC re-test**. Production on
dual-socket EPYC should be substantially faster (the handbook targets
single-frame P95 <1 s).

## Mac vs production

|                       | Mac (dev)                  | EPYC (prod)                       |
|-----------------------|----------------------------|-----------------------------------|
| backend               | `rapidocr` (PP-OCRv4)      | `paddleocr` (PP-OCRv6_medium)     |
| typical per-frame     | 0.8-1.3 s                  | target <1 s P95 (T1.8 to confirm) |
| model storage         | RapidOCR bundled weights   | `~/.paddlex/official_models/`     |
| privacy               | loopback only, no content logging, image discarded post-OCR (same both sides) |

## Layout

```
src/ocrd/
  engine.py    # OcrEngine protocol + RapidOcrEngine + PaddleOcrEngine + lazy singleton
  server.py    # create_app() -> FastAPI with /ocr and /health
  __init__.py  # create_app + main() (uvicorn 127.0.0.1:8006)
  __main__.py  # python -m ocrd entry
```

No content is logged and no image bytes are retained past the request
(handbook §6.1 privacy rule).
