"""Best-effort browser URL probe via osascript (handbook §5.1, optional).

Asks the frontmost browser (Safari or Chrome) for its active tab URL. Designed
to fail silently: any error (no browser running, browser not in the allow list,
AppleScript permissions missing, automation denied) returns None and the caller
fills `url: null` in the meta. This MUST NOT block or crash the main loop.

Automation permission: the first time this runs against a browser, macOS pops a
prompt asking the user to let the terminal control that browser. If the user
denies it, subsequent calls raise and we return None — that's fine.
"""

from __future__ import annotations

import subprocess


# Map app bundle name -> AppleScript expression that yields the active tab URL.
# Kept as plain strings to avoid quoting hell; each returns the URL on stdout.
_URL_SCRIPTS = {
    "Safari": (
        'tell application "Safari" to get URL of document 1'
    ),
    "Google Chrome": (
        'tell application "Google Chrome" to get URL of active tab of '
        'front window'
    ),
    "Chromium": (
        'tell application "Chromium" to get URL of active tab of front window'
    ),
    "Microsoft Edge": (
        'tell application "Microsoft Edge" to get URL of active tab of '
        'front window'
    ),
    "Brave Browser": (
        'tell application "Brave Browser" to get URL of active tab of '
        'front window'
    ),
    "Arc": (
        'tell application "Arc" to get URL of active tab of front window'
    ),
}


def probe_browser_url(app_name: str | None) -> str | None:
    """Return the active-tab URL of `app_name`, or None if unknown/unavailable.

    `app_name` is the localized name returned by get_active_window(). Unknown
    browsers and any osascript error yield None.
    """
    if not app_name:
        return None
    script = _URL_SCRIPTS.get(app_name)
    if script is None:
        return None
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    url = result.stdout.strip()
    if not url or url.lower().startswith(("missing value", "error")):
        return None
    return url
