"""Screen Recording permission detection + user guidance (handbook §5.2).

On macOS 10.15+, Screen Recording permission gates whether `mss`/Quartz can
read pixel data. When permission is MISSING the API still "works" — it returns
a frame, but every pixel is black (or, on some setups, only the wallpaper is
visible). There is no clean synchronous API to ask "do I have permission?".

Reliable detection strategy used here:
  1. Capture a frame with mss.
  2. Decode it and compute the per-channel mean and the count of non-black
     pixels. A real desktop is never perfectly black; a denied frame is
     (near-)uniform black with zero meaningful variance.
  3. If the frame is essentially all black, conclude permission is missing and
     print the step-by-step guidance.

The check is cached per process after the first positive result: once we've
seen a non-black frame we know permission is granted, and re-testing every
capture would burn CPU for no benefit.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import mss
from PIL import Image, ImageChops, ImageStat


# A real desktop essentially never has < this many non-black pixels. A 2560x1600
# black frame has 0; even a dark theme has thousands of lit pixels (text cursor,
# window chrome, menu bar icons).
_NONBLACK_PIXEL_THRESHOLD = 500


@dataclass(frozen=True)
class PermissionCheck:
    granted: bool
    detail: str


_cached_granted: bool | None = None


def check_screen_recording_permission() -> PermissionCheck:
    """Return whether Screen Recording permission appears to be granted.

    Also prints guidance to stderr when permission looks missing. Subsequent
    calls after a positive result are cached and cheap.
    """
    global _cached_granted
    if _cached_granted is True:
        return PermissionCheck(True, "previously verified this process")

    try:
        with mss.mss() as sct:
            mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
            raw = sct.grab(mon)
        img = Image.frombytes("RGBA", raw.size, raw.bgra, "raw", "BGRA").convert("RGB")
    except Exception as exc:  # mss failure, no display, etc.
        # Can't even capture — treat as not granted and surface the error.
        return PermissionCheck(False, f"capture failed: {exc}")

    nonblack = _count_nonblack_pixels(img)
    stat = ImageStat.Stat(img)
    mean_rgb = stat.mean  # [R, G, B] averages 0..255

    if nonblack < _NONBLACK_PIXEL_THRESHOLD:
        _cached_granted = False
        msg = (
            f"frame appears all-black "
            f"(nonblack pixels={nonblack}, mean RGB=({mean_rgb[0]:.1f},"
            f" {mean_rgb[1]:.1f}, {mean_rgb[2]:.1f}))"
        )
        _print_permission_guidance(detail=msg)
        return PermissionCheck(False, msg)

    _cached_granted = True
    return PermissionCheck(
        True,
        f"frame has content (nonblack pixels={nonblack}, "
        f"mean RGB=({mean_rgb[0]:.1f},{mean_rgb[1]:.1f},{mean_rgb[2]:.1f}))",
    )


def _count_nonblack_pixels(img: Image.Image, *, tolerance: int = 8) -> int:
    """Count pixels whose RGB is not near-(0,0,0).

    `tolerance` absorbs JPEG-ish noise on an otherwise-black frame so a denied
    capture doesn't false-positive into "granted". A genuinely dark desktop
    still has thousands of brighter pixels.
    """
    black = Image.new("RGB", img.size, (0, 0, 0))
    # diff has the per-channel absolute difference; a pixel is "nonblack" if any
    # channel differs from 0 by more than the tolerance.
    diff = ImageChops.difference(img, black).convert("L")
    hist = diff.histogram()
    # hist[0..tolerance] are "black enough"; everything above is real content.
    return sum(hist[tolerance + 1 :])


def _print_permission_guidance(*, detail: str) -> None:
    """Print clear, actionable steps to grant Screen Recording permission."""
    lines = [
        "",
        "=" * 72,
        "DejaView capture: Screen Recording permission appears MISSING.",
        f"  reason: {detail}",
        "",
        "macOS blocks pixel capture until you grant Screen Recording to the",
        "terminal/IDE running this client. A black frame is captured instead.",
        "",
        "To fix, ONCE on this machine:",
        "  1. Open System Settings -> Privacy & Security -> Screen Recording.",
        "     (System Settings is in the Apple menu, or run: `open ",
        "      'x-apple.systempreferences:com.apple.preference.security'",
        "      '?Privacy_ScreenCapture'`)",
        "  2. Find the app you used to launch capture:",
        "       - If you ran `uv run python -m capture` in Terminal, add",
        "         'Terminal' (or iTerm2 / your terminal).",
        "       - If you ran it inside an IDE (VS Code, Cursor, ZCode, etc.),",
        "         add that IDE AND its integrated terminal host.",
        "  3. Toggle its switch ON. macOS will prompt to quit the app.",
        "  4. Quit the app fully (Cmd+Q) and relaunch, then run capture again.",
        "",
        "After granting, the first non-black frame proves it worked.",
        "Until then, capture will keep running but every frame is black and",
        "useless to OCR — no crash, just no data.",
        "=" * 72,
        "",
    ]
    sys.stderr.write("\n".join(lines) + "\n")
    sys.stderr.flush()
