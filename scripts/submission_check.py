#!/usr/bin/env python3
"""Fail-closed, machine-executable contract for the DejaView submission bundle."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import math
import os
import posixpath
import re
import stat
import subprocess
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional
from urllib.parse import unquote
from xml.etree import ElementTree


MANIFEST_PATH = "docs/assets/demo/p34-video-manifest.json"
ACCEPTED_ORIGINAL_PATH = "docs/assets/demo/dejaview-p34-six-act-20260802.mp4"
ACCEPTED_ORIGINAL_SHA = "5dc772cea426b215ce6a87c83b75f7dbf2c9f9ca5884e77686e449c2f3ae23ed"
DOCX_PATH = "docs/submission/DejaView-Project-Specification.docx"
PPTX_PATH = "docs/submission/DejaView-Track2-Presentation.pptx"
P31_DIR = "docs/benchmark-evidence/p31/p31-w7900d-20260728T075653Z/"
MAX_OFFICE_UNCOMPRESSED = 100 * 1024 * 1024
REL_SLIDE_TYPES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/slide",
}
REL_NOTES_TYPES = {
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide",
    "http://purl.oclc.org/ooxml/officeDocument/relationships/notesSlide",
}

REQUIRED_FILES = (
    "README.md",
    "README.zh.md",
    "LICENSE",
    "NOTICE",
    "docs/licenses.md",
    "docs/model-manifest.md",
    "docs/benchmarks.md",
    "docs/verification-log.md",
    f"{P31_DIR}SHA256SUMS",
    f"{P31_DIR}p31-summary.md",
    "docs/submission/PROJECT_SPECIFICATION.md",
    DOCX_PATH,
    PPTX_PATH,
    MANIFEST_PATH,
    ACCEPTED_ORIGINAL_PATH,
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _trusted_root(root: Path) -> tuple[Optional[Path], str]:
    lexical = Path(os.path.abspath(os.fspath(root)))
    try:
        mode = lexical.lstat().st_mode
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        return None, f"repository root unavailable: {exc}"
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        return None, "repository root must be a real directory, not a symlink"
    if lexical != resolved:
        return None, "repository root or one of its ancestors is a symlink"
    return lexical, str(lexical)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(raw: object) -> Optional[str]:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        return None
    pure = PurePosixPath(raw)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        return None
    normalized = pure.as_posix()
    return normalized if normalized == raw else None


def _safe_regular(root: Path, raw: object) -> tuple[Optional[Path], str]:
    relative = _safe_relative(raw)
    if relative is None:
        return None, f"unsafe repository path: {raw!r}"
    trusted, detail = _trusted_root(root)
    if trusted is None:
        return None, detail
    root = trusted
    root_real = root
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        try:
            mode = current.lstat().st_mode
        except OSError as exc:
            return None, f"missing path {relative}: {exc.strerror or exc}"
        if stat.S_ISLNK(mode):
            return None, f"symlink is forbidden: {relative}"
    try:
        resolved = current.resolve(strict=True)
        resolved.relative_to(root_real)
    except (OSError, ValueError) as exc:
        return None, f"path escapes repository: {relative}: {exc}"
    if not current.is_file():
        return None, f"not a regular file: {relative}"
    return current, relative


def _number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _clock(value: object) -> Optional[float]:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(?:(\d{2}):)?(\d{2}):(\d{2}(?:\.\d+)?)", value)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = float(match.group(3))
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _srt_clock(value: str) -> Optional[float]:
    match = re.fullmatch(r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", value)
    if not match:
        return None
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _parse_srt(text: str) -> tuple[list[tuple[float, float, str]], list[str]]:
    cues: list[tuple[float, float, str]] = []
    errors: list[str] = []
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip()) if text.strip() else []
    for expected, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0].strip() != str(expected):
            errors.append(f"cue {expected} has a non-contiguous index")
            continue
        match = re.fullmatch(r"(.+?)\s+-->\s+(.+?)", lines[1].strip())
        if not match:
            errors.append(f"cue {expected} has an invalid time row")
            continue
        start, end = _srt_clock(match.group(1)), _srt_clock(match.group(2))
        if start is None or end is None or start >= end:
            errors.append(f"cue {expected} has invalid timestamps")
            continue
        cues.append((start, end, "\n".join(lines[2:]).strip()))
    return cues, errors


def _zip_safety(archive: zipfile.ZipFile) -> tuple[bool, str]:
    names: set[str] = set()
    total = 0
    for info in archive.infolist():
        name = info.filename
        pure = PurePosixPath(name)
        canonical_name = pure.as_posix() + ("/" if name.endswith("/") else "")
        if (
            not name
            or "\\" in name
            or pure.is_absolute()
            or any(part in ("", ".", "..") for part in pure.parts)
            or canonical_name != name
            or name in names
        ):
            return False, f"unsafe or duplicate ZIP member: {name!r}"
        names.add(name)
        total += info.file_size
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and stat.S_ISLNK(mode):
            return False, f"symlink ZIP member: {name}"
        file_type = stat.S_IFMT(mode)
        if file_type and file_type not in (stat.S_IFREG, stat.S_IFDIR):
            return False, f"non-regular ZIP member: {name}"
        if file_type == stat.S_IFDIR and not name.endswith("/"):
            return False, f"required-looking ZIP member is a directory: {name}"
    if total > MAX_OFFICE_UNCOMPRESSED:
        return False, f"OOXML expands to {total} bytes"
    bad = archive.testzip()
    return (False, f"corrupt ZIP member: {bad}") if bad else (True, "safe ZIP members")


def _xml_text(archive: zipfile.ZipFile, members: Iterable[str]) -> tuple[Optional[str], str]:
    chunks: list[str] = []
    for member in members:
        try:
            data = archive.read(member)
            root = ElementTree.fromstring(data)
        except (KeyError, ElementTree.ParseError, OSError) as exc:
            return None, f"invalid XML member {member}: {exc}"
        chunks.extend(piece for piece in root.itertext() if piece)
    return " ".join(chunks), "XML parsed"


def _office_check(path: Path, kind: str) -> tuple[bool, str, str]:
    if not zipfile.is_zipfile(path):
        return False, "not an OOXML ZIP", ""
    try:
        with zipfile.ZipFile(path) as archive:
            safe, detail = _zip_safety(archive)
            if not safe:
                return False, detail, ""
            names = set(archive.namelist())
            if kind == "docx":
                required = {
                    "[Content_Types].xml",
                    "_rels/.rels",
                    "word/document.xml",
                    "word/styles.xml",
                    "docProps/core.xml",
                }
                missing = sorted(required - names)
                if missing:
                    return False, f"missing DOCX members: {', '.join(missing)}", ""
                xml_members = sorted(name for name in names if name.endswith(".xml") or name.endswith(".rels"))
                text, detail = _xml_text(archive, xml_members)
                if text is None:
                    return False, detail, ""
                folded = text.casefold()
                anchors = ("dejaview", "rocm", "privacy", "limitations")
                track = "track 2" in folded or "track2" in folded
                latin = len(re.findall(r"[A-Za-z]{2,}", text)) >= 5
                if not all(anchor in folded for anchor in anchors) or not track or not latin:
                    return False, "DOCX English specification anchors are incomplete", text
                return True, "valid DOCX structure and English anchors", text
            required = {"[Content_Types].xml", "_rels/.rels", "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels"}
            missing = sorted(required - names)
            if missing:
                return False, f"missing PPTX members: {', '.join(missing)}", ""
            slides = sorted(name for name in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", name))
            if not slides:
                return False, "PPTX has no slides", ""
            notes = sorted(name for name in names if re.fullmatch(r"ppt/notesSlides/notesSlide\d+\.xml", name))
            slide_indexes = {int(re.search(r"(\d+)", name).group(1)) for name in slides}
            note_indexes = {int(re.search(r"(\d+)", name).group(1)) for name in notes}
            if note_indexes != slide_indexes:
                return False, "each PPTX slide must have a notes slide", ""
            try:
                relationships = ElementTree.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
            except (KeyError, ElementTree.ParseError) as exc:
                return False, f"invalid presentation relationships: {exc}", ""
            targets = {
                element.attrib.get("Target", "").lstrip("/")
                for element in relationships.iter()
                if element.attrib.get("Target") and element.attrib.get("Type") in REL_SLIDE_TYPES
            }
            referenced_slides = {target if target.startswith("ppt/") else f"ppt/{target}" for target in targets}
            if not set(slides).issubset(referenced_slides):
                return False, "presentation relationships do not reference every slide", ""
            mapped_notes: set[str] = set()
            for slide in slides:
                match = re.search(r"slide(\d+)\.xml$", slide)
                assert match is not None
                index = int(match.group(1))
                relationship_name = f"ppt/slides/_rels/slide{index}.xml.rels"
                if relationship_name not in names:
                    return False, f"missing slide relationships: {relationship_name}", ""
                try:
                    slide_relationships = ElementTree.fromstring(archive.read(relationship_name))
                except (KeyError, ElementTree.ParseError) as exc:
                    return False, f"invalid slide relationships: {relationship_name}: {exc}", ""
                note_targets: list[str] = []
                for element in slide_relationships.iter():
                    target = element.attrib.get("Target", "")
                    relationship_type = element.attrib.get("Type", "")
                    if not target or relationship_type not in REL_NOTES_TYPES:
                        continue
                    resolved = (
                        posixpath.normpath(target.lstrip("/"))
                        if target.startswith("/")
                        else posixpath.normpath(posixpath.join(posixpath.dirname(slide), target))
                    )
                    if resolved.startswith("ppt/notesSlides/notesSlide") and resolved.endswith(".xml"):
                        note_targets.append(resolved)
                if len(note_targets) != 1 or note_targets[0] not in names:
                    return False, f"{slide} must map to exactly one existing notes slide", ""
                if note_targets[0] in mapped_notes:
                    return False, f"multiple slides map to {note_targets[0]}", ""
                mapped_notes.add(note_targets[0])
            if mapped_notes != set(notes):
                return False, "PPTX has orphan or unmapped notes slides", ""
            xml_members = sorted(name for name in names if name.endswith(".xml") or name.endswith(".rels"))
            text, detail = _xml_text(archive, xml_members)
            if text is None:
                return False, detail, ""
            for note in notes:
                note_text, note_detail = _xml_text(archive, (note,))
                if note_text is None or "[sources]" not in note_text.casefold():
                    return False, f"{note} lacks [Sources]: {note_detail}", text
            folded = text.casefold()
            anchors = ("dejaview", "rocm", "privacy", "radeon", "evidence")
            track = "track 2" in folded or "track2" in folded
            if not all(anchor in folded for anchor in anchors) or not track:
                return False, "PPTX English evidence anchors are incomplete", text
            return True, f"valid PPTX with {len(slides)} sourced slides", text
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, RuntimeError, ValueError) as exc:
        return False, f"cannot inspect {kind}: {exc}", ""


def _tracked_paths(root: Path) -> tuple[list[str], Optional[str]]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        return [raw.decode("utf-8") for raw in result.stdout.split(b"\0") if raw], None
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        return [], f"git ls-files failed; tracked-file privacy cannot be proven: {type(exc).__name__}"


def _ooxml_raw_text(path: Optional[Path]) -> str:
    if path is None or not zipfile.is_zipfile(path):
        return ""
    chunks: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            safe, _detail = _zip_safety(archive)
            if not safe:
                return ""
            for info in archive.infolist():
                name = info.filename.casefold()
                if name.endswith((".xml", ".rels")) or "notes" in name or "comment" in name:
                    chunks.append(archive.read(info).decode("utf-8", errors="replace"))
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, NotImplementedError, RuntimeError, ValueError):
        return ""
    return "\n".join(chunks)


def _secret_filename(relative: str) -> bool:
    base = PurePosixPath(relative).name.casefold()
    if base in {".env.example", ".env.sample", ".env.template"}:
        return False
    if base == ".env" or base.startswith(".env."):
        return True
    if base in {"auth.json", "tokens.json", "id_rsa", "id_ed25519", ".netrc", "credentials", "credentials.json"}:
        return True
    return base.endswith((".pem", ".key"))


def _privacy_findings(text: str) -> list[str]:
    findings: list[str] = []
    for candidate in re.findall(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])", text):
        try:
            address = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if address.version == 4 and address.is_global:
            findings.append(f"public IPv4 {candidate}")
    patterns = (
        (r"\broot@\d{1,3}(?:\.\d{1,3}){3}\b", "numeric root SSH coordinate"),
        (r"\bu-\d{4,}-[0-9a-f]{6,}\b", "ephemeral instance id"),
        (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key material"),
        (r"\b(?:ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b", "GitHub token"),
        (r"\bAKIA[0-9A-Z]{16}\b", "AWS access key"),
        (r"\bsk-[A-Za-z0-9_-]{20,}\b", "API token"),
        (r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", "Bearer credential"),
    )
    for pattern, label in patterns:
        if re.search(pattern, text, re.IGNORECASE):
            findings.append(label)
    assignment = re.compile(
        r"(?im)\b[A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PRIVATE_KEY|ACCESS_KEY|CLIENT_SECRET)\b\s*[:=]\s*([^\s,;#]+)"
    )
    allowed = re.compile(
        r"^(?:['\"]?(?:|none|null|true|false|redacted|changeme|dummy|example|placeholder|your[_-].*)['\"]?|\$\{?[A-Z0-9_]+\}?|os\.environ.*|<[^>]+>)$",
        re.IGNORECASE,
    )
    for match in assignment.finditer(text):
        value = match.group(1).strip()
        if not allowed.fullmatch(value):
            findings.append("credential assignment")
            break
    return sorted(set(findings))


def _release_text_paths(root: Path, dynamic_caption: Optional[Path]) -> list[Path]:
    relatives = [
        "README.md", "README.zh.md", "STATUS.md", "TASKBOARD.json",
        "deploy/server/DEPLOY.md", "docs/AGENT_KICKOFF_PROMPT.md",
        "docs/EXECUTION_HANDBOOK.md", "docs/benchmarks.md",
        "docs/verification-log.md", "docs/licenses.md", "docs/model-manifest.md",
        "docs/submission/PROJECT_SPECIFICATION.md", MANIFEST_PATH,
        f"{P31_DIR}SHA256SUMS", f"{P31_DIR}p31-summary.md",
    ]
    paths: list[Path] = []
    for relative in dict.fromkeys(relatives):
        safe, _detail = _safe_regular(root, relative)
        if safe is not None:
            paths.append(safe)
    if dynamic_caption is not None:
        paths.append(dynamic_caption)
    return list(dict.fromkeys(paths))


def _visible_markdown(text: str) -> str:
    """Return prose and links that are rendered outside comments/code blocks."""
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    visible: list[str] = []
    fence_char = ""
    fence_length = 0
    for line in text.splitlines():
        if fence_char:
            if re.fullmatch(rf"\s{{0,3}}{re.escape(fence_char)}{{{fence_length},}}\s*", line):
                fence_char = ""
                fence_length = 0
            continue
        opening = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if opening:
            marker = opening.group(1)
            fence_char = marker[0]
            fence_length = len(marker)
            continue
        if line.startswith("\t") or line.startswith("    "):
            continue
        visible.append(line)
    return re.sub(r"`+[^`\n]*`+", "", "\n".join(visible))


def check_submission(root: Path, *, ffprobe: str = "ffprobe") -> list[CheckResult]:
    trusted, root_detail = _trusted_root(Path(root))
    if trusted is None:
        return [CheckResult("required-release-files", False, root_detail)]
    root = trusted
    results: list[CheckResult] = []
    add = lambda name, ok, detail: results.append(CheckResult(name, bool(ok), str(detail)))

    required_errors: list[str] = []
    for relative in REQUIRED_FILES:
        path, detail = _safe_regular(root, relative)
        if path is None:
            required_errors.append(detail)
    add("required-release-files", not required_errors, "; ".join(required_errors) or f"{len(REQUIRED_FILES)} required files are regular and non-symlinked")

    manifest: dict[str, object] = {}
    manifest_file, manifest_detail = _safe_regular(root, MANIFEST_PATH)
    if manifest_file is not None:
        try:
            loaded = json.loads(manifest_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                manifest = loaded
            else:
                manifest_detail = "manifest root is not an object"
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            manifest_detail = f"invalid manifest JSON: {exc}"

    top_required = {
        "artifact", "recorded_at", "duration_seconds", "resolution", "video_codec",
        "audio_codec", "size_bytes", "sha256", "submission_artifact",
        "capture_method", "fault_injection", "timeline",
    }
    sub = manifest.get("submission_artifact") if isinstance(manifest, dict) else None
    sub_required = {
        "artifact", "language", "duration_seconds", "resolution", "video_codec",
        "audio_codec", "size_bytes", "sha256", "caption_source", "caption_sha256",
    }
    schema_errors: list[str] = []
    if not manifest:
        schema_errors.append(manifest_detail)
    else:
        schema_errors.extend(f"missing {key}" for key in sorted(top_required - set(manifest)))
        if not isinstance(sub, dict):
            schema_errors.append("submission_artifact is not an object")
            sub = {}
        else:
            schema_errors.extend(f"missing submission_artifact.{key}" for key in sorted(sub_required - set(sub)))
        if not isinstance(manifest.get("timeline"), list):
            schema_errors.append("timeline is not a list")
        for label, value in (("duration_seconds", manifest.get("duration_seconds")), ("submission duration", sub.get("duration_seconds"))):
            if _number(value) is None:
                schema_errors.append(f"{label} is not finite numeric")
        for label, value in (("size_bytes", manifest.get("size_bytes")), ("submission size_bytes", sub.get("size_bytes"))):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                schema_errors.append(f"{label} is not a positive integer")
        for label, value in (("sha256", manifest.get("sha256")), ("submission sha256", sub.get("sha256")), ("caption_sha256", sub.get("caption_sha256"))):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                schema_errors.append(f"{label} is not lowercase SHA-256")
        for label, value in (("artifact", manifest.get("artifact")), ("capture_method", manifest.get("capture_method")), ("fault_injection", manifest.get("fault_injection")), ("submission artifact", sub.get("artifact")), ("caption source", sub.get("caption_source")), ("language", sub.get("language"))):
            if not isinstance(value, str) or not value.strip():
                schema_errors.append(f"{label} is empty or non-string")
        recorded_at = manifest.get("recorded_at")
        try:
            if not isinstance(recorded_at, str) or date.fromisoformat(recorded_at).isoformat() != recorded_at:
                raise ValueError
        except ValueError:
            schema_errors.append("recorded_at is not YYYY-MM-DD")
        for label, value, expected in (
            ("resolution", manifest.get("resolution"), "1920x1080"),
            ("video_codec", manifest.get("video_codec"), "h264"),
            ("audio_codec", manifest.get("audio_codec"), "aac"),
            ("submission resolution", sub.get("resolution"), "1920x1080"),
            ("submission video_codec", sub.get("video_codec"), "h264"),
            ("submission audio_codec", sub.get("audio_codec"), "aac"),
        ):
            if value != expected:
                schema_errors.append(f"{label} must be {expected}")
    add("submission-manifest-schema", not schema_errors, "; ".join(schema_errors) or "manifest schema is complete")
    sub = sub if isinstance(sub, dict) else {}

    original, original_path_detail = _safe_regular(root, manifest.get("artifact"))
    original_ok = (
        manifest.get("artifact") == ACCEPTED_ORIGINAL_PATH
        and manifest.get("sha256") == ACCEPTED_ORIGINAL_SHA
        and _number(manifest.get("duration_seconds")) == 157.2
        and manifest.get("resolution") == "1920x1080"
        and manifest.get("video_codec") == "h264"
        and manifest.get("audio_codec") == "aac"
        and original is not None
    )
    original_detail = original_path_detail
    if original_ok and original is not None:
        actual_sha = _sha256(original)
        actual_size = original.stat().st_size
        original_ok = actual_sha == ACCEPTED_ORIGINAL_SHA and manifest.get("size_bytes") == actual_size
        original_detail = f"accepted original sha={actual_sha}, size={actual_size}"
    add("accepted-demo-immutability", original_ok, original_detail)

    video, video_detail = _safe_regular(root, sub.get("artifact"))
    caption, caption_detail = _safe_regular(root, sub.get("caption_source"))
    video_relative = sub.get("artifact") if isinstance(sub.get("artifact"), str) else ""
    caption_relative = sub.get("caption_source") if isinstance(sub.get("caption_source"), str) else ""
    paths_ok = (
        video is not None and caption is not None and video_relative != caption_relative
        and video_relative.startswith("docs/assets/demo/") and video_relative.endswith(".mp4")
        and caption_relative.startswith("docs/assets/demo/") and caption_relative.endswith(".srt")
    )
    add("submission-artifact-paths", paths_ok, f"video: {video_detail}; captions: {caption_detail}")

    video_hash_ok = False
    if video is not None:
        actual_hash = _sha256(video)
        video_hash_ok = sub.get("sha256") == actual_hash and sub.get("size_bytes") == video.stat().st_size
        video_hash_detail = f"manifest={sub.get('sha256')} actual={actual_hash} size={video.stat().st_size}"
    else:
        video_hash_detail = video_detail
    add("submission-video-hash", video_hash_ok, video_hash_detail)

    probe_duration: Optional[float] = None
    probe_streams: list[object] = []
    probe_format_name = ""
    probe_error = "video unavailable"
    if video is not None:
        try:
            process = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=format_name,duration:stream=codec_type,codec_name,width,height", "-of", "json", str(video)],
                check=True, capture_output=True, text=True, timeout=30,
            )
            probe = json.loads(process.stdout)
            probe_duration = float(probe.get("format", {}).get("duration"))
            probe_format_name = str(probe.get("format", {}).get("format_name", ""))
            if not math.isfinite(probe_duration):
                raise ValueError("non-finite duration")
            streams = probe.get("streams")
            probe_streams = streams if isinstance(streams, list) else []
            probe_error = "ffprobe parsed"
        except (OSError, subprocess.SubprocessError, ValueError, TypeError, json.JSONDecodeError) as exc:
            probe_error = f"ffprobe failed: {exc}"
            probe_format_name = ""

    declared_duration = _number(sub.get("duration_seconds"))
    duration_ok = (
        probe_duration is not None
        and declared_duration is not None
        and 180.0 <= probe_duration <= 300.0
        and 180.0 <= declared_duration <= 300.0
        and abs(probe_duration - declared_duration) <= 0.1
    )
    add("submission-video-duration", duration_ok, f"actual={probe_duration}, manifest={declared_duration}; required 180–300s")
    format_tokens = {token.strip().casefold() for token in probe_format_name.split(",") if token.strip()}
    container_ok = "mp4" in format_tokens
    add("submission-video-container", container_ok, f"ffprobe format_name={probe_format_name or 'missing'}; required mp4")
    videos = [item for item in probe_streams if isinstance(item, dict) and item.get("codec_type") == "video"]
    audios = [item for item in probe_streams if isinstance(item, dict) and item.get("codec_type") == "audio"]
    streams_ok = (
        len(videos) == 1 and len(audios) == 1
        and videos[0].get("codec_name") == "h264" and videos[0].get("width") == 1920 and videos[0].get("height") == 1080
        and audios[0].get("codec_name") == "aac"
        and sub.get("resolution") == "1920x1080" and sub.get("video_codec") == "h264" and sub.get("audio_codec") == "aac"
    )
    add("submission-video-streams", streams_ok, probe_error if not probe_streams else f"video={videos}; audio={audios}")

    language = str(sub.get("language", "")).casefold()
    language_ok = (
        language.startswith("english") and "primary" in language and "narration" in language
        and "english captions" in language and "chinese narration" not in language
    )
    add("submission-language", language_ok, str(sub.get("language", "missing language declaration")))

    caption_hash_ok = False
    caption_text = ""
    if caption is not None:
        actual_caption_hash = _sha256(caption)
        caption_hash_ok = sub.get("caption_sha256") == actual_caption_hash
        try:
            caption_text = caption.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            caption_text = ""
        caption_hash_detail = f"manifest={sub.get('caption_sha256')} actual={actual_caption_hash}"
    else:
        caption_hash_detail = caption_detail
    add("submission-captions-hash", caption_hash_ok, caption_hash_detail)

    cues, cue_errors = _parse_srt(caption_text)
    timing_errors = list(cue_errors)
    if cues:
        if cues[0][0] > 0.05:
            timing_errors.append(f"first cue starts at {cues[0][0]:.3f}s")
        for previous, current in zip(cues, cues[1:]):
            if current[0] < previous[1]:
                timing_errors.append("captions overlap")
            elif current[0] - previous[1] > 0.25:
                timing_errors.append("caption gap exceeds 0.25s")
        if probe_duration is None or abs(cues[-1][1] - probe_duration) > 0.25:
            timing_errors.append("last cue does not cover the video end")
        if probe_duration is not None and any(end > probe_duration + 0.25 for _, end, _ in cues):
            timing_errors.append("caption extends beyond video")
    else:
        timing_errors.append("no valid cues")
    add("submission-captions-timing", not timing_errors, "; ".join(timing_errors) or f"{len(cues)} contiguous cues cover the video")
    cue_text = " ".join(text for _, _, text in cues)
    cue_folded = cue_text.casefold()
    content_errors = [anchor for anchor in ("radeon", "rocm", "link down", "local ready", "local metal", "fallback") if anchor not in cue_folded]
    caption_acts = [int(value) for value in re.findall(r"\bact\s+([1-6])\b", cue_folded)]
    if caption_acts != list(range(1, 7)):
        content_errors.append("caption act sequence must be exactly 1–6")
    latin_count = len(re.findall(r"[A-Za-z]{2,}", cue_text))
    chinese_count = len(re.findall(r"[\u3400-\u9fff]", cue_text))
    if latin_count < 12 or chinese_count > latin_count:
        content_errors.append("captions are not English-primary")
    add("submission-captions-content", not content_errors, "missing: " + ", ".join(content_errors) if content_errors else "English six-act Radeon/fallback anchors present")

    timeline = manifest.get("timeline") if isinstance(manifest.get("timeline"), list) else []
    timeline_errors: list[str] = []
    parsed_rows: list[tuple[float, float, int, str]] = []
    for index, row in enumerate(timeline):
        if not isinstance(row, dict):
            timeline_errors.append(f"row {index + 1} is not an object")
            continue
        start, end = _clock(row.get("start")), _clock(row.get("end"))
        act, evidence = row.get("act"), row.get("evidence")
        if start is None or end is None or start >= end or type(act) is not int or act not in range(1, 7) or not isinstance(evidence, str) or not evidence.strip():
            timeline_errors.append(f"row {index + 1} is invalid")
            continue
        parsed_rows.append((start, end, act, evidence))
    if parsed_rows:
        if parsed_rows[0][0] > 0.05:
            timeline_errors.append("timeline does not start at zero")
        for previous, current in zip(parsed_rows, parsed_rows[1:]):
            if current[0] < previous[1] or current[0] - previous[1] > 0.25:
                timeline_errors.append("timeline overlaps or has a gap")
        if declared_duration is None or abs(parsed_rows[-1][1] - declared_duration) > 0.25:
            timeline_errors.append("timeline does not end at declared duration")
        acts = [row[2] for row in parsed_rows]
        if acts != list(range(1, 7)):
            timeline_errors.append("timeline act sequence must be exactly 1–6")
        act6 = " ".join(row[3] for row in parsed_rows if row[2] == 6).casefold()
        if not all(anchor in act6 for anchor in ("radeon", "local metal", "fallback")) or not ("disconnect" in act6 or "link down" in act6):
            timeline_errors.append("Act 6 lacks Radeon disconnect and Local Metal fallback")
    else:
        timeline_errors.append("timeline is empty")
    add("submission-timeline", not timeline_errors, "; ".join(timeline_errors) or "timeline continuously covers acts 1–6")

    docx, docx_detail = _safe_regular(root, DOCX_PATH)
    pptx, pptx_detail = _safe_regular(root, PPTX_PATH)
    docx_ok, docx_check_detail, docx_text = _office_check(docx, "docx") if docx else (False, docx_detail, "")
    pptx_ok, pptx_check_detail, pptx_text = _office_check(pptx, "pptx") if pptx else (False, pptx_detail, "")
    add("submission-docx", docx_ok, docx_check_detail)
    add("submission-pptx", pptx_ok, pptx_check_detail)

    tracked, tracked_error = _tracked_paths(root)
    forbidden_names = sorted(relative for relative in tracked if _secret_filename(relative))
    tracked_ok = tracked_error is None and not forbidden_names
    tracked_detail = tracked_error or ("forbidden tracked names: " + ", ".join(forbidden_names) if forbidden_names else f"{len(tracked)} tracked paths checked without reading .env")
    add("submission-privacy-tracked-files", tracked_ok, tracked_detail)
    privacy_errors: list[str] = []
    for path in _release_text_paths(root, caption):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            privacy_errors.append(f"{path.relative_to(root)} unreadable: {exc}")
            continue
        for finding in _privacy_findings(text):
            privacy_errors.append(f"{path.relative_to(root)}: {finding}")
    for label, text in (
        (DOCX_PATH, "\n".join((docx_text, _ooxml_raw_text(docx)))),
        (PPTX_PATH, "\n".join((pptx_text, _ooxml_raw_text(pptx)))),
    ):
        for finding in _privacy_findings(text):
            privacy_errors.append(f"{label}: {finding}")
    add("submission-privacy-content", not privacy_errors, "; ".join(privacy_errors[:12]) or "release-facing text and OOXML contain no public coordinates or credentials")

    readme, readme_detail = _safe_regular(root, "README.md")
    link_errors: list[str] = []
    links: set[str] = set()
    readme_text = ""
    if readme is not None:
        try:
            readme_text = readme.read_text(encoding="utf-8")
            visible_readme = _visible_markdown(readme_text)
            for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", visible_readme):
                target = unquote(raw.strip().strip("<>").split("#", 1)[0].split("?", 1)[0])
                if target and not re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                    links.add(target)
        except (OSError, UnicodeError) as exc:
            link_errors.append(str(exc))
    else:
        link_errors.append(readme_detail)
    exact_links = {
        "docs/submission/PROJECT_SPECIFICATION.md", DOCX_PATH, PPTX_PATH,
        MANIFEST_PATH, "docs/benchmarks.md", "docs/licenses.md",
    }
    for dynamic in (sub.get("artifact"), sub.get("caption_source")):
        if isinstance(dynamic, str):
            exact_links.add(dynamic)
    for required in sorted(exact_links):
        if required not in links:
            link_errors.append(f"README lacks {required}")
        elif _safe_regular(root, required)[0] is None:
            link_errors.append(f"README target invalid: {required}")
    if P31_DIR not in links:
        link_errors.append(f"README lacks {P31_DIR}")
    visible_readme = _visible_markdown(readme_text)
    readme_folded = re.sub(r"\s+", " ", visible_readme.casefold())
    instruction_patterns = (
        (r"\bofficial competition repository\b", "official competition repository"),
        (r"\bfork\b", "fork"),
        (r"\benglish pull request\b", "english pull request"),
        (r"\bdoes not claim\b", "does not claim"),
    )
    for pattern, label in instruction_patterns:
        if not re.search(pattern, readme_folded):
            link_errors.append(f"README lacks submission instruction: {label}")
    add("submission-readme-links", not link_errors, "; ".join(link_errors) or f"{len(exact_links) + 1} required local links resolve")
    verification, verification_detail = _safe_regular(root, "docs/verification-log.md")
    boundary_ok = False
    boundary_detail = "verification log unavailable"
    try:
        if verification is None:
            raise OSError(verification_detail)
        boundary_text = verification.read_text(encoding="utf-8").casefold()
        boundary_ok = all(anchor in boundary_text for anchor in ("human-only boundary", "official-repository fork/english pr", "does not claim"))
        boundary_detail = "human-only official fork/English PR boundary is explicit" if boundary_ok else "verification log lacks the human-only official fork/English PR boundary"
    except (OSError, UnicodeError) as exc:
        boundary_detail = str(exc)
    add("submission-human-boundaries", boundary_ok, boundary_detail)
    return results


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ffprobe", default="ffprobe")
    args = parser.parse_args(argv)
    results = check_submission(args.root, ffprobe=args.ffprobe)
    for result in results:
        print(f"{'PASS' if result.ok else 'FAIL'} {result.name}: {result.detail}")
    passed = sum(result.ok for result in results)
    print(f"SUMMARY {passed}/{len(results)} passed; {len(results) - passed} failed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
