"""Frontmost app name + window title on macOS (handbook §5.1).

The active app name comes from `NSWorkspace.frontmostApplication()`. The window
title is trickier: there is no clean Cocoa API for "title of app X's key
window", so the standard approach is to walk the on-screen window list from
`CGWindowListCopyWindowInfo` and find the topmost window whose owning process
matches the frontmost app's pid. That window's `kCGWindowName` is the title.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from AppKit import NSWorkspace
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListOptionOnScreenOnly,
)

# Windows smaller than this (in either dimension) are dropped — they're usually
# tooltips, palettes, or floating inspectors whose content isn't worth a frame.
_MIN_WINDOW_DIM = 240
# Apps that report on-screen windows we never want to capture (menu bars,
# overlays, the capture process itself).
_OWNER_BLOCKLIST = {"Window Server", "SystemUIServer", "Control Center",
                    "Dock", "loginwindow", "Capture", "Python"}


@dataclass
class WindowInfo:
    """One on-screen window worth capturing."""
    window_id: int          # kCGWindowNumber — used by screencapture -l
    owner: str              # app name (kCGWindowOwnerName)
    title: str              # window title (kCGWindowName)
    bounds: dict            # {X, Y, Width, Height} in display coords
    is_foreground: bool     # matches the frontmost app's pid


def list_windows(*, include_offscreen: bool = False) -> list[WindowInfo]:
    """Enumerate on-screen application windows worth capturing.

    Returns the foreground window first, then others sorted by area descending.
    Filters: layer==0 (no menu bars/overlays), owner not in blocklist, has a
    title, width AND height >= _MIN_WINDOW_DIM (drops tooltips/palettes).

    This is the per-window capture inventory — the agent iterates this list,
    captures each window via :func:`capture_window_png`, and uploads each as a
    separate frame so the memory system sees every open window's content (not
    just the foreground app).
    """
    try:
        front_pid = (NSWorkspace.sharedWorkspace().frontmostApplication()
                     .processIdentifier())
    except Exception:
        front_pid = -1
    try:
        info = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly,
                                          kCGNullWindowID)
    except Exception:
        return []
    if info is None:
        return []
    out: list[WindowInfo] = []
    for w in info:
        if int(w.get("kCGWindowLayer", 0)) != 0:
            continue
        owner = w.get("kCGWindowOwnerName") or ""
        if owner in _OWNER_BLOCKLIST:
            continue
        title = w.get("kCGWindowName") or ""
        if not title:
            continue
        b = w.get("kCGWindowBounds") or {}
        if b.get("Width", 0) < _MIN_WINDOW_DIM or b.get("Height", 0) < _MIN_WINDOW_DIM:
            continue
        wid = int(w.get("kCGWindowNumber", 0))
        if wid <= 0:
            continue
        out.append(WindowInfo(
            window_id=wid, owner=owner, title=title, bounds=dict(b),
            is_foreground=(w.get("kCGWindowOwnerPID", -1) == front_pid),
        ))
    # Foreground first, then by area (largest first) so we capture the most
    # informative windows before any timeout/cap kicks in.
    out.sort(key=lambda wi: (not wi.is_foreground,
                             -(wi.bounds.get("Width", 0) * wi.bounds.get("Height", 0))))
    return out


def capture_window_png(window_id: int, *, timeout: float = 5.0) -> bytes | None:
    """Capture one window by id, return PNG bytes, or None on failure.

    Uses Apple's `screencapture -l <wid>` rather than pyobjc's
    CGWindowListCreateImage: the latter returns None for many cross-app windows
    even WITH Screen Recording permission (Quartz restricts per-window pixel
    reads of other processes' windows more tightly than whole-screen reads).
    `screencapture` is Apple's own tool and has the full entitlement. Output
    goes to a temp file (the only disk write this client does — cleaned up
    immediately after read; never the working dir, so the zero-disk invariant
    on the capture dir holds).
    """
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        r = subprocess.run(
            ["screencapture", "-l", str(window_id), "-x", "-C", tmp_path],
            capture_output=True, timeout=timeout,
        )
        if r.returncode != 0 or not Path(tmp_path).exists():
            return None
        from PIL import Image
        with Image.open(tmp_path) as img:
            img = img.convert("RGB")
            import io
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError:
            pass


def get_active_window() -> tuple[str | None, str | None]:
    """Return (app_name, window_title) for the frontmost application.

    Either field may be `None` if it cannot be determined (e.g. a fullscreen
    app with no window list entry, or a transiently-nil frontmost app). Never
    raises — the caller treats a `(None, None)` result as "nothing to report".
    """
    try:
        ws = NSWorkspace.sharedWorkspace()
        front = ws.frontmostApplication()
        if front is None:
            return None, None
        app_name = front.localizedName()
        pid = front.processIdentifier()
    except Exception:
        # pyobjc can throw on headless / locked sessions; degrade to "unknown".
        return None, None

    title = _window_title_for_pid(pid) if pid and pid > 0 else None
    return app_name, title


def _window_title_for_pid(pid: int) -> str | None:
    """Topmost on-screen window title owned by `pid`, or None."""
    try:
        info = CGWindowListCopyWindowInfo(
            kCGWindowListOptionOnScreenOnly, kCGNullWindowID
        )
    except Exception:
        return None
    if info is None:
        return None
    for win in info:
        owner_pid = win.get("kCGWindowOwnerPID", -1)
        if owner_pid != pid:
            continue
        # Skip layer>0 (menu bars, overlays). Frontmost app windows sit at 0.
        if int(win.get("kCGWindowLayer", 0)) != 0:
            continue
        name = win.get("kCGWindowName")
        if name:
            return str(name)
        # Some apps report no window name on the first entry; keep scanning.
    return None
