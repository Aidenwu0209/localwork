"""Frontmost app name + window title on macOS (handbook §5.1).

The active app name comes from `NSWorkspace.frontmostApplication()`. The window
title is trickier: there is no clean Cocoa API for "title of app X's key
window", so the standard approach is to walk the on-screen window list from
`CGWindowListCopyWindowInfo` and find the topmost window whose owning process
matches the frontmost app's pid. That window's `kCGWindowName` is the title.
"""

from __future__ import annotations

from AppKit import NSWorkspace
from Quartz import (
    CGWindowListCopyWindowInfo,
    kCGNullWindowID,
    kCGWindowListOptionOnScreenOnly,
)


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
