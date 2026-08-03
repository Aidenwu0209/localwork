#!/usr/bin/env python3
"""Build the English-primary 3–5 minute DejaView submission video on macOS."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/assets/demo/dejaview-p34-six-act-20260802-en.mp4"
SOURCE_SHA256 = "4b326db90e38282abb8467d34e01b39989dc27dc6b1fb92807f0b56fcc81b4ed"
ACCEPTED = ROOT / "docs/assets/demo/dejaview-p34-six-act-20260802.mp4"
ACCEPTED_SHA256 = "5dc772cea426b215ce6a87c83b75f7dbf2c9f9ca5884e77686e449c2f3ae23ed"
DECK = ROOT / "docs/submission/DejaView-Track2-Presentation.pptx"
OUTPUT = ROOT / "docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.mp4"
CAPTIONS = ROOT / "docs/assets/demo/dejaview-p34-six-act-20260802-en-3m.srt"

CUES = (
    (0.0, 10.0, "DejaView is a sovereign digital memory for AMD AI DevMaster Track 2.\nPrivate pixels stay local, and every answer returns evidence."),
    (10.0, 20.0, "The privacy boundary is ordered: capture on the Mac, Sentinel before storage,\nand only allowed stateless requests reach Radeon compute."),
    (20.0, 30.0, "ROCm proof is measured, not claimed: eighteen brain cells, three perceive cells,\nand the MTP memory cost on a Radeon PRO W7900D."),
    (30.0, 37.5, "DejaView is not an ordinary retrieval-augmented chatbot.\nIt is a sovereign, local digital memory."),
    (37.5, 44.5, "Screens, text, timelines, vectors, and the user model stay on this Mac.\nThe Radeon server supplies stateless compute only."),
    (44.5, 52.5, "ACT 1 — Five logical roles run on an AMD Radeon PRO W7900D:\nbrain, perceive, sentinel, fast, and embed, routed through LiteLLM."),
    (52.5, 61.0, "rocm-smi identifies the gfx1100 GPU. Grafana shows utilization,\nVRAM, per-role throughput, and the memory-event rate on one screen."),
    (61.0, 71.5, "ACT 2 — Three safe synthetic windows enter the real pipeline.\nThe Sentinel decides first; PP-OCRv6 extracts verbatim text and boxes."),
    (71.5, 82.0, "Perceive creates specific semantics; embed stores 1,024-D vectors.\nThe timeline grows from one seed to four events, all on this Mac."),
    (82.0, 90.0, "ACT 3 — A bank-login screen is blocked at the first privacy gate.\nThe result is BLOCKED, with an audit reference."),
    (90.0, 98.0, "Zero screenshot files and zero timeline rows are created.\nRejected pixels never enter searchable memory."),
    (98.0, 106.5, "ACT 4 — The user asks which ROCm pull request was viewed\nlast Wednesday afternoon. DejaView answers: PR #1842."),
    (106.5, 115.0, "The answer includes event ID, time, application, source screenshot,\nand text bounding boxes: verifiable memory, not a model guess."),
    (115.0, 123.5, "ACT 5 — Honcho answers a different question:\nnot ‘What did I see?’, but ‘Which approach is more like me?’"),
    (123.5, 132.0, "Nine isolated synthetic conclusions favor local, inspectable,\nconfiguration-driven workflows. No real user data is used."),
    (132.0, 142.0, "ACT 6 — Planner, Retriever, Writer, and Reviewer generate today's report.\nRetriever is limited to today's three events."),
    (142.0, 152.0, "The brain model writes on Radeon ROCm; Reviewer validates every citation.\nThe first evidence-backed report completes successfully."),
    (152.0, 159.5, "Now the exact attested Radeon SSH compute link is disconnected visibly.\nWi-Fi stays connected; no physical cable pull is simulated."),
    (159.5, 167.0, "The product reports LINK DOWN and LOCAL READY.\nThe remote brain route is no longer available."),
    (167.0, 177.5, "The same report runs again on independently verified Local Metal fallback.\nPlanner, Retriever, Writer, and Reviewer all complete."),
    (177.5, 187.2, "All three citations pass again. Remote compute can disappear;\nlocal memory, agent workflow, and data sovereignty survive."),
    (187.2, 195.2, "DejaView proves the product, the privacy boundary, and the Radeon path.\nLocal memory survives when remote compute disappears."),
)


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_duration(path: Path) -> float:
    process = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(json.loads(process.stdout)["format"]["duration"])


def srt_clock(seconds: float) -> str:
    millis = round(seconds * 1000)
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    whole, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole:02d},{millis:03d}"


def write_captions() -> None:
    blocks = [
        f"{index}\n{srt_clock(start)} --> {srt_clock(end)}\n{text}\n"
        for index, (start, end, text) in enumerate(CUES, 1)
    ]
    CAPTIONS.write_text("\n".join(blocks), encoding="utf-8")


def atempo_chain(ratio: float) -> list[str]:
    filters: list[str] = []
    while ratio > 2.0:
        filters.append("atempo=2.0")
        ratio /= 2.0
    if ratio > 1.0005:
        filters.append(f"atempo={ratio:.6f}")
    return filters


def build_audio(work: Path, voice: str) -> Path:
    segments: list[Path] = []
    for index, (start, end, text) in enumerate(CUES, 1):
        duration = end - start
        words = re.findall(r"[A-Za-z0-9#]+(?:[-'][A-Za-z0-9]+)*", text)
        rate = max(155, min(290, math.ceil(len(words) * 60 / max(duration - 0.8, 1))))
        raw = work / f"voice-{index:02d}.aiff"
        exact = work / f"voice-{index:02d}.wav"
        run(["say", "-v", voice, "-r", str(rate), "-o", str(raw), text.replace("\n", " ")])
        raw_duration = probe_duration(raw)
        filters = atempo_chain(raw_duration / max(duration - 0.15, 0.1))
        filters.extend(("apad", f"atrim=duration={duration:.3f}", "asetpts=N/SR/TB"))
        run([
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(raw), "-af", ",".join(filters), "-ar", "48000", "-ac", "1",
            "-c:a", "pcm_s16le", str(exact),
        ])
        segments.append(exact)
    concat = work / "audio-concat.txt"
    concat.write_text("".join(f"file '{path.as_posix()}'\n" for path in segments), encoding="utf-8")
    narration = work / "narration.wav"
    run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat),
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-ar", "48000", "-ac", "1",
        "-c:a", "pcm_s16le", str(narration),
    ])
    return narration


def render_slides(work: Path) -> tuple[Path, Path, Path]:
    profile = (work / "libreoffice-profile").resolve().as_uri()
    run([
        "soffice", f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
        "--outdir", str(work), str(DECK),
    ])
    pdf = work / f"{DECK.stem}.pdf"
    if not pdf.is_file():
        raise RuntimeError("LibreOffice did not produce the expected PDF")
    prefix = work / "slide"
    run(["pdftoppm", "-png", "-r", "120", str(pdf), str(prefix)])
    slides = tuple(work / f"slide-{index}.png" for index in (1, 3, 6))
    if not all(slide.is_file() for slide in slides):
        raise RuntimeError("PPTX slide rasterization is incomplete")
    return slides


def build_video(work: Path, narration: Path, slides: tuple[Path, Path, Path]) -> None:
    video_only = work / "video-only.mp4"
    scale = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1,fps=30,format=yuv420p"
    graph = ";".join(
        (
            f"[0:v]{scale},trim=duration=10,setpts=PTS-STARTPTS[v0]",
            f"[1:v]{scale},trim=duration=10,setpts=PTS-STARTPTS[v1]",
            f"[2:v]{scale},trim=duration=10,setpts=PTS-STARTPTS[v2]",
            f"[3:v]{scale},trim=duration=157.2,setpts=PTS-STARTPTS[v3]",
            f"[4:v]{scale},trim=duration=8,setpts=PTS-STARTPTS[v4]",
            "[v0][v1][v2][v3][v4]concat=n=5:v=1:a=0[v]",
        )
    )
    run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-loop", "1", "-framerate", "30", "-t", "10", "-i", str(slides[0]),
        "-loop", "1", "-framerate", "30", "-t", "10", "-i", str(slides[1]),
        "-loop", "1", "-framerate", "30", "-t", "10", "-i", str(slides[2]),
        "-i", str(SOURCE),
        "-loop", "1", "-framerate", "30", "-t", "8", "-i", str(slides[0]),
        "-filter_complex", graph, "-map", "[v]", "-an", "-c:v", "libx264",
        "-preset", "medium", "-crf", "24", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(video_only),
    ])
    run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_only), "-i", str(narration), "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-t", "195.2", "-movflags", "+faststart", str(OUTPUT),
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--voice", default="Samantha")
    args = parser.parse_args()
    for executable in ("ffmpeg", "ffprobe", "say", "soffice", "pdftoppm"):
        if shutil.which(executable) is None:
            raise SystemExit(f"missing required executable: {executable}")
    for path, expected in ((ACCEPTED, ACCEPTED_SHA256), (SOURCE, SOURCE_SHA256)):
        if not path.is_file() or sha256(path) != expected:
            raise SystemExit(f"immutable source mismatch: {path}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    write_captions()
    with tempfile.TemporaryDirectory(prefix="dejaview-submission-video-") as raw:
        work = Path(raw)
        slides = render_slides(work)
        narration = build_audio(work, args.voice)
        build_video(work, narration, slides)
    duration = probe_duration(OUTPUT)
    if abs(duration - 195.2) > 0.1:
        raise SystemExit(f"unexpected output duration: {duration}")
    print(json.dumps({
        "artifact": str(OUTPUT.relative_to(ROOT)),
        "duration_seconds": duration,
        "size_bytes": OUTPUT.stat().st_size,
        "sha256": sha256(OUTPUT),
        "caption_source": str(CAPTIONS.relative_to(ROOT)),
        "caption_sha256": sha256(CAPTIONS),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
