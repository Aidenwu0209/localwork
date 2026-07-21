"""OCR engine abstraction for ocrd (handbook §6.1).

Two backends are supported behind a single :class:`OcrEngine` protocol:

* ``rapidocr`` (default on Mac/ARM dev): ``rapidocr-onnxruntime`` is an ONNX
  packaging of the PP-OCR series (v4 at the time of writing) with a much
  smaller dependency footprint than the full PaddleOCR stack. On Apple
  Silicon (M5) it runs ~60x faster than PaddleOCR 3.7's own ONNX runtime path
  while being at least as accurate on the synthetic test set (see
  ``docs/verification-log.md`` 2026-07-21 (M5.1)).
* ``paddleocr``: PaddleOCR >=3.7.0 driving the PP-OCRv6 pipeline via
  ``engine='onnxruntime'``. This is the production target on the dual-socket
  EPYC CPU box, where the larger PP-OCRv6_medium model outperforms v4. On Mac
  it is kept as the comparison/production-parity path.

Both backends share the same PP-OCR det+rec philosophy; only the model
generation and ONNX packaging differ. Preprocessing is fully disabled
(``use_doc_orientation_classify`` / ``use_doc_unwarping`` /
``use_textline_orientation`` all off — screenshots are always upright and
flat, so this is pure speedup, handbook §6.1).

The contract returned by :meth:`OcrEngine.ocr` matches
``memoryd.models.OcrResult``: ``full_text`` + ``blocks`` where each block is
``{text, bbox:[x1,y1,x2,y2], conf}``.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass, field
from io import BytesIO
from typing import Protocol

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# Backend is chosen once via env var; defaults to ``rapidocr`` because that is
# the only path that is both fast and dependency-light on Apple Silicon dev
# machines. Set OCR_BACKEND=paddleocr to exercise PP-OCRv6 directly (the
# production target on EPYC).
DEFAULT_BACKEND = os.environ.get("OCR_BACKEND", "rapidocr").strip().lower()


@dataclass
class OcrBlock:
    """One detected text line.

    ``bbox`` is ``[x1, y1, x2, y2]`` (top-left, bottom-right) in pixel
    coordinates of the input image — identical layout to
    ``memoryd.models.OcrBlock``.
    """

    text: str
    bbox: list[float] = field(metadata={"length": 4})
    conf: float


@dataclass
class OcrResult:
    full_text: str
    blocks: list[OcrBlock]


class OcrEngine(Protocol):
    """Stable interface every backend implements."""

    backend_name: str

    def ocr(self, image: np.ndarray) -> OcrResult:  # pragma: no cover - protocol
        ...


# ---------------------------------------------------------------------------
# Backend: rapidocr-onnxruntime (PP-OCRv4 ONNX, default on Mac)
# ---------------------------------------------------------------------------
class RapidOcrEngine:
    """``rapidocr-onnxruntime`` backend.

    RapidOCR returns, per line, a 4-point polygon ``[[x1,y1],[x2,y2],[x3,y3],
    [x4,y4]]`` (clockwise from top-left), the recognised text and a confidence
    in ``[0,1]``. We collapse the polygon to its axis-aligned bounding box so
    the output is the ``[x1,y1,x2,y2]`` shape the memoryd contract expects.

    Preprocessing knobs: RapidOCR has no document-orientation / unwarping /
    textline-orientation stages in the default det+rec pipeline, so "all off"
    is the default and there is nothing to disable — we pass the image through
    unmodified.
    """

    backend_name = "rapidocr"

    def __init__(self) -> None:
        # Imported lazily so the service can boot without the heavy OCR
        # dependency until the first request arrives.
        from rapidocr_onnxruntime import RapidOCR

        # RapidOCR's defaults already disable orientation/unwarping; we rely
        # on them. ``use_cls=False`` would also skip the 0/180 text-line
        # classifier, but RapidOCR's default classifier is cheap and the
        # handbook's "all off" rule targets the heavier PaddleOCR document
        # preprocessor, so we keep RapidOCR's defaults.
        self._ocr = RapidOCR()
        logger.info("RapidOCR engine initialised (backend=rapidocr)")

    def ocr(self, image: np.ndarray) -> OcrResult:
        result, _elapse = self._ocr(image)
        blocks: list[OcrBlock] = []
        if result:
            for box, text, score in result:
                # box: 4x2 polygon -> axis-aligned [x1,y1,x2,y2]
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                blocks.append(
                    OcrBlock(
                        text=text,
                        bbox=[min(xs), min(ys), max(xs), max(ys)],
                        conf=float(score),
                    )
                )
        full_text = "\n".join(b.text for b in blocks)
        return OcrResult(full_text=full_text, blocks=blocks)


# ---------------------------------------------------------------------------
# Backend: PaddleOCR >=3.7 (PP-OCRv6, production target on EPYC)
# ---------------------------------------------------------------------------
class PaddleOcrEngine:
    """PaddleOCR 3.7+ backend driving PP-OCRv6 via the ONNX Runtime engine.

    Notes on the API (verified 2026-07-21, paddleocr 3.7.0):

    * The default engine is ``paddle_static`` which requires the full
      ``paddlepaddle`` package. To run without it on ARM we pass
      ``engine='onnxruntime'`` (the literal; ``'onnx'`` is rejected). The
      PP-OCRv6 ONNX weights are auto-fetched into
      ``~/.paddlex/official_models/PP-OCRv6_*_onnx`` on first use.
    * The three preprocessing switches from the handbook map 1:1 to
      ``use_doc_orientation_classify`` / ``use_doc_unwarping`` /
      ``use_textline_orientation`` — all set to ``False``.
    * ``predict()`` returns a list of :class:`OCRResult` objects (one per
      input image). Each result's ``.json['res']`` carries ``rec_polys``
      (4x2 polygon per line), ``rec_texts`` and ``rec_scores``.
    """

    backend_name = "paddleocr"

    def __init__(self) -> None:
        # Connectivity check to the model hoster is slow and not needed when
        # the models are already cached; disable it for deterministic startup.
        os.environ.setdefault(
            "PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True"
        )
        from paddleocr import PaddleOCR

        self._ocr = PaddleOCR(
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            lang="ch",  # PP-OCRv6 single-model, 50 langs, CN+EN mixed
            engine="onnxruntime",
            device="cpu",
        )
        logger.info("PaddleOCR engine initialised (backend=paddleocr, PP-OCRv6)")

    def ocr(self, image: np.ndarray) -> OcrResult:
        result = self._ocr.predict(image)
        if not result:
            return OcrResult(full_text="", blocks=[])
        res = result[0].json["res"]
        polys = res.get("rec_polys") or res.get("dt_polys") or []
        texts = res.get("rec_texts", [])
        scores = res.get("rec_scores", [])
        blocks: list[OcrBlock] = []
        for poly, text, score in zip(polys, texts, scores):
            xs = [float(p[0]) for p in poly]
            ys = [float(p[1]) for p in poly]
            blocks.append(
                OcrBlock(
                    text=str(text),
                    bbox=[min(xs), min(ys), max(xs), max(ys)],
                    conf=float(score),
                )
            )
        full_text = "\n".join(b.text for b in blocks)
        return OcrResult(full_text=full_text, blocks=blocks)


# ---------------------------------------------------------------------------
# Singleton lazy loader
# ---------------------------------------------------------------------------
class EngineRegistry:
    """Process-wide lazy singleton for the OCR engine.

    OCR model initialisation is expensive (RapidOCR ~0.1s to load the ONNX
    sessions, PaddleOCR ~9s plus a one-time model download). We build the
    engine on first use and reuse it for the lifetime of the process. A lock
    guards construction so concurrent first-requests don't double-build.
    """

    def __init__(self) -> None:
        self._engine: OcrEngine | None = None
        self._lock = threading.Lock()

    def get(self, backend: str | None = None) -> OcrEngine:
        if self._engine is not None:
            return self._engine
        with self._lock:
            if self._engine is not None:
                return self._engine
            name = (backend or DEFAULT_BACKEND)
            logger.info("Building OCR engine backend=%r", name)
            if name == "rapidocr":
                self._engine = RapidOcrEngine()
            elif name == "paddleocr":
                self._engine = PaddleOcrEngine()
            else:
                raise ValueError(
                    f"Unknown OCR backend: {name!r}. "
                    "Set OCR_BACKEND to 'rapidocr' or 'paddleocr'."
                )
            return self._engine


# Module-level singleton; imported by server.py.
_registry = EngineRegistry()


def get_engine() -> OcrEngine:
    """Return the process-wide OCR engine, building it on first call."""
    return _registry.get()


def reset_engine() -> None:
    """Drop the cached engine (test helper; not used in production paths)."""
    global _registry
    with _registry._lock:  # noqa: SLF001 - intentional internal access
        _registry._engine = None  # noqa: SLF001


# ---------------------------------------------------------------------------
# Image loading helper
# ---------------------------------------------------------------------------
def load_image(image_bytes: bytes) -> np.ndarray:
    """Decode raw image bytes into an RGB ``ndarray`` for the engine."""
    with Image.open(BytesIO(image_bytes)) as im:
        im = im.convert("RGB")
        return np.asarray(im)


__all__ = [
    "DEFAULT_BACKEND",
    "OcrBlock",
    "OcrResult",
    "OcrEngine",
    "RapidOcrEngine",
    "PaddleOcrEngine",
    "get_engine",
    "reset_engine",
    "load_image",
]
