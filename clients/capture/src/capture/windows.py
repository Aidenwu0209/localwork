"""Platform window inventory and capture backends.

The macOS implementation uses Cocoa window metadata and ``screencapture``.
Windows uses the user32 APIs and an in-memory ``mss`` region capture.  The
module keeps the existing public functions stable for the capture agent.

macOS details:

The active app name comes from `NSWorkspace.frontmostApplication()`. The window
title is trickier: there is no clean Cocoa API for "title of app X's key
window", so the standard approach is to walk the on-screen window list from
`CGWindowListCopyWindowInfo` and find the topmost window whose owning process
matches the frontmost app's pid. That window's `kCGWindowName` is the title.
"""

from __future__ import annotations

import ctypes
import os
import sys
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

if sys.platform == "darwin":
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


if os.name == "nt":
    _GWL_EXSTYLE = -20
    _WS_EX_TOOLWINDOW = 0x00000080
    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _MAX_PATH = 32768

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p), ctypes.c_void_p]
    _user32.EnumWindows.restype = ctypes.c_bool
    _user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    _user32.IsWindowVisible.restype = ctypes.c_bool
    _user32.IsIconic.argtypes = [ctypes.c_void_p]
    _user32.IsIconic.restype = ctypes.c_bool
    _user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_long * 4)]
    _user32.GetWindowRect.restype = ctypes.c_bool
    _user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    _user32.GetWindowTextLengthW.restype = ctypes.c_int
    _user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    _user32.GetWindowTextW.restype = ctypes.c_int
    _user32.GetForegroundWindow.restype = ctypes.c_void_p
    _user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    _user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    _user32.OpenInputDesktop.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    _user32.OpenInputDesktop.restype = ctypes.c_void_p
    _user32.CloseDesktop.argtypes = [ctypes.c_void_p]
    _user32.CloseDesktop.restype = ctypes.c_bool
    _kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
    _kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _kernel32.CloseHandle.restype = ctypes.c_bool

    @dataclass
    class WindowInfo:
        """One visible Windows application window worth capturing."""

        window_id: int
        owner: str
        title: str
        bounds: dict
        is_foreground: bool

    def _process_name(pid: int) -> str:
        handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return "unknown"
        try:
            size = ctypes.c_ulong(_MAX_PATH)
            buf = ctypes.create_unicode_buffer(_MAX_PATH)
            if not _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return "unknown"
            return os.path.splitext(os.path.basename(buf.value))[0] or "unknown"
        finally:
            _kernel32.CloseHandle(handle)

    def _window_text(hwnd: int) -> str:
        length = _user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, len(buf))
        return buf.value.strip()

    def list_windows(*, include_offscreen: bool = False) -> list[WindowInfo]:
        """Enumerate visible titled application windows on Windows."""
        foreground = int(_user32.GetForegroundWindow() or 0)
        found: list[WindowInfo] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type
        def visit(hwnd_ptr: int, _lparam: int) -> bool:
            hwnd = int(hwnd_ptr)
            if not _user32.IsWindowVisible(hwnd) or _user32.IsIconic(hwnd):
                return True
            if _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & _WS_EX_TOOLWINDOW:
                return True
            title = _window_text(hwnd)
            if not title:
                return True
            rect = (ctypes.c_long * 4)()
            if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            left, top, right, bottom = (int(rect[i]) for i in range(4))
            width, height = right - left, bottom - top
            if width < _MIN_WINDOW_DIM or height < _MIN_WINDOW_DIM:
                return True
            pid = ctypes.c_ulong()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            owner = _process_name(int(pid.value))
            if owner.casefold() in {"capture", "python", "pythonw"}:
                return True
            found.append(WindowInfo(
                window_id=hwnd,
                owner=owner,
                title=title,
                bounds={"X": left, "Y": top, "Width": width, "Height": height},
                is_foreground=hwnd == foreground,
            ))
            return True

        _user32.EnumWindows(visit, 0)
        found.sort(key=lambda item: (not item.is_foreground, -(item.bounds["Width"] * item.bounds["Height"])))
        return found

    def capture_window_png(window_id: int, *, timeout: float = 5.0) -> bytes | None:
        """Capture a visible window region directly into PNG bytes."""
        del timeout
        try:
            windows = {item.window_id: item for item in list_windows()}
            item = windows.get(int(window_id))
            if item is None:
                return None
            import io
            import mss
            from PIL import Image

            with mss.mss() as sct:
                raw = sct.grab({
                    "left": item.bounds["X"], "top": item.bounds["Y"],
                    "width": item.bounds["Width"], "height": item.bounds["Height"],
                })
            image = Image.frombytes("RGBA", raw.size, raw.bgra, "raw", "BGRA").convert("RGB")
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()
        except (OSError, ValueError, RuntimeError):
            return None

    def get_active_window() -> tuple[str | None, str | None]:
        """Return the foreground process name and title, if available."""
        foreground = int(_user32.GetForegroundWindow() or 0)
        for item in list_windows():
            if item.window_id == foreground:
                return item.owner, item.title
        return None, None
