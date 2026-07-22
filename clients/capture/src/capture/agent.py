"""Capture agent main loop (handbook §5.2).

Behavior:
  - Poll the frontmost window every `poll_interval` (default 3s).
  - When the app OR window title changes since the last capture, fire a frame
    with trigger="change".
  - Independently, every `periodic_interval` (default 30s) fire a frame with
    trigger="periodic" as a fallback even if nothing changed.
  - Enforce `min_capture_interval` (default 3s) between ANY two uploads so a
    window-title flicker can't flood memoryd.
  - While the display is asleep or the session is locked, PAUSE: don't capture,
    don't upload, and don't reset the change detector.

Lock/session detection: macOS does not expose a synchronous "is locked?" API
through pyobjc, so we subscribe to the distributed notifications
`com.apple.screenIsLocked` / `com.apple.screenIsUnlocked` (and the screen-saver
equivalents) via CoreFoundation and maintain an in-process flag. On startup we
assume unlocked and let the first frame correct that if needed.

All uploads are fire-and-forget in the sense that a dropped frame is simply
gone — no disk cache (handbook §5.2 privacy invariant).
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from typing import TYPE_CHECKING

import httpx
import imagehash
from PIL import Image

from capture.permissions import check_screen_recording_permission
from capture.screenshot import _scale_to_max_width
from capture.uploader import upload_frame
from capture.url_probe import probe_browser_url
from capture.windows import capture_window_png, get_active_window, list_windows

if TYPE_CHECKING:
    from capture.config import CaptureConfig


def _png_to_scaled_webp(png_bytes: bytes, config: "CaptureConfig") -> tuple[bytes, int, int]:
    """PNG bytes -> WebP (<=max_upload_width, quality=webp_quality). In memory."""
    with Image.open(io.BytesIO(png_bytes)) as img:
        img = img.convert("RGB")
        scaled = _scale_to_max_width(img, config.max_upload_width)
        buf = io.BytesIO()
        scaled.save(buf, format="WEBP", quality=config.webp_quality, method=4)
        return buf.getvalue(), scaled.width, scaled.height


log = logging.getLogger("capture.agent")

# Distributed-notification names broadcast by loginwindow / ScreenSaverEngine.
_SCREEN_LOCKED = "com.apple.screenIsLocked"
_SCREEN_UNLOCKED = "com.apple.screenIsUnlocked"
_SCREENSAVER_DID_START = "com.apple.screensaver.didstart"
_SCREENSAVER_DID_STOP = "com.apple.screensaver.didstop"


class _LockState:
    """In-process lock flag, updated by distributed-notification callbacks.

    Implemented as a plain Python holder; the NSObject observer below mutates
    `.locked` when loginwindow/ScreenSaverEngine broadcasts a state change.
    """

    def __init__(self) -> None:
        self.locked = False


def _install_lock_observer(state: _LockState) -> bool:
    """Subscribe to lock/unlock distributed notifications. Returns success.

    Uses NSDistributedNotificationCenter + a typed NSObject selector. On failure
    (older macOS, sandbox, missing pyobjc pieces) we simply proceed without
    pause-on-lock: a locked screen yields a black frame anyway, which the
    permission check / OCR sentinel will reject downstream. Capturing while
    locked wastes a few black frames but never crashes.
    """
    try:
        import objc
        from Foundation import NSObject, NSDistributedNotificationCenter
    except ImportError:
        log.warning("pyobjc distributed notifications unavailable; "
                    "cannot detect lock state")
        return False

    lock_names = {_SCREEN_LOCKED, _SCREENSAVER_DID_START}
    unlock_names = {_SCREEN_UNLOCKED, _SCREENSAVER_DID_STOP}

    class _Observer(NSObject):
        # Typed selector: -(void)onNotification:(NSNotification*)note
        # Signature "v@:@" = (void return, self, _cmd, takes-one-object).
        @objc.typedSelector(b"v@:@")
        def onNotification_(self, note):
            name = note.name()
            if name in lock_names:
                if not state.locked:
                    state.locked = True
                    log.info("session locked / screensaver started — pausing capture")
            elif name in unlock_names:
                if state.locked:
                    state.locked = False
                    log.info("session unlocked — resuming capture")

    try:
        center = NSDistributedNotificationCenter.defaultCenter()
        observer = _Observer.alloc().init()
        for name in lock_names | unlock_names:
            center.addObserver_selector_name_object_(
                observer, "onNotification:", name, None
            )
    except Exception as exc:
        log.warning("could not install lock observer: %s", exc)
        return False
    return True


def _pump_runloop(timeout: float) -> None:
    """Briefly run the CF run loop so distributed notifications fire callbacks.

    The agent loop is asyncio; the NSDistributedNotificationCenter callbacks
    are delivered on the main thread's run loop, so each loop iteration spins
    it briefly (sub-second) to drain any pending lock/unlock notifications.
    """
    try:
        from CoreFoundation import CFRunLoopRunInMode, kCFRunLoopDefaultMode
    except ImportError:
        return
    try:
        CFRunLoopRunInMode(kCFRunLoopDefaultMode, timeout, False)
    except Exception:
        pass


async def run_agent(config: "CaptureConfig") -> None:
    """Main capture loop. Runs until cancelled (Ctrl-C / SIGTERM)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info(
        "capture starting: device_id=%s memoryd=%s config=%s "
        "poll=%.1fs periodic=%.1fs",
        config.device_id, config.frame_endpoint, config.source,
        config.poll_interval, config.periodic_interval,
    )

    perm = check_screen_recording_permission()
    if not perm.granted:
        # The permission module already printed guidance. Keep running so the
        # user can grant permission and relaunch; the loop will keep producing
        # black frames (rejected downstream) but won't crash.
        log.warning(
            "running WITHOUT screen recording permission: frames will be black. "
            "Grant permission and relaunch to capture real content."
        )
    else:
        log.info("screen recording permission OK (%s)", perm.detail)

    lock_state = _LockState()
    _install_lock_observer(lock_state)

    last_fg: tuple[str | None, str | None] = (None, None)  # foreground (app,title)
    last_periodic_ts: float = time.monotonic()
    last_upload_ts: float = 0.0
    # Per-window dhash for dedup: keyed by "<owner>::<title>" so the same window
    # across cycles dedups against itself, but different windows never suppress
    # each other.
    window_hashes: dict[str, "imagehash.ImageHash"] = {}
    # Cap windows captured per cycle so a window-heavy desktop doesn't flood
    # memoryd (each frame runs the full sentinel/ocrd/perceive pipeline).
    MAX_WINDOWS_PER_CYCLE = 8

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
        while True:
            try:
                _pump_runloop(0.05)

                if lock_state.locked:
                    await asyncio.sleep(config.poll_interval)
                    continue

                now = time.monotonic()

                # Trigger: foreground change OR periodic timer. The window LIST
                # is re-enumerated every cycle regardless, but we only capture
                # when one of these fires (so a static desktop doesn't spin).
                fg = get_active_window()
                fg_changed = (
                    (fg[0] != last_fg[0] or fg[1] != last_fg[1])
                    and (fg[0] is not None or fg[1] is not None)
                )
                periodic_due = (now - last_periodic_ts) >= config.periodic_interval
                if not fg_changed and not periodic_due:
                    await asyncio.sleep(config.poll_interval)
                    continue
                if (now - last_upload_ts) < config.min_capture_interval:
                    await asyncio.sleep(config.poll_interval)
                    continue

                trigger = "change" if fg_changed else "periodic"
                last_fg = fg
                if trigger == "periodic":
                    last_periodic_ts = now

                # Enumerate all on-screen windows; capture each as its own frame.
                windows = list_windows()
                if not windows:
                    log.info("no capturable windows this cycle (trigger=%s)", trigger)
                    await asyncio.sleep(config.poll_interval)
                    continue

                captured = 0
                for wi in windows[:MAX_WINDOWS_PER_CYCLE]:
                    png_bytes = capture_window_png(wi.window_id)
                    if not png_bytes:
                        continue
                    # Re-encode to WebP for upload (handbook §5.2: <=2560px, q80).
                    try:
                        webp_bytes, w, h = _png_to_scaled_webp(png_bytes, config)
                    except Exception as exc:
                        log.warning("encode failed for %s/%s: %s", wi.owner, wi.title, exc)
                        continue
                    frame_hash = imagehash.dhash(
                        Image.open(io.BytesIO(png_bytes)).convert("RGB")
                    )
                    # Per-window dedup.
                    key = f"{wi.owner}::{wi.title}"
                    if config.dedup_distance > 0 and key in window_hashes:
                        dist = frame_hash - window_hashes[key]
                        if dist < config.dedup_distance:
                            log.info(
                                "dedup: %s/%s distance %d < %d (skipped)",
                                wi.owner, wi.title, dist, config.dedup_distance,
                            )
                            continue
                    url = probe_browser_url(wi.owner) if (config.probe_url and wi.is_foreground) else None
                    log.info(
                        "capturing window: %s/%s fg=%s size=%dx%d bytes=%d trigger=%s",
                        wi.owner, wi.title, wi.is_foreground, w, h, len(webp_bytes), trigger,
                    )
                    await upload_frame(
                        config,
                        webp_bytes=webp_bytes,
                        app=wi.owner,
                        window_title=wi.title,
                        url=url,
                        trigger=trigger,
                        client=client,
                    )
                    window_hashes[key] = frame_hash
                    last_upload_ts = time.monotonic()
                    captured += 1
                log.info("cycle done: %d/%d windows captured (trigger=%s)",
                         captured, len(windows), trigger)

            except asyncio.CancelledError:
                log.info("capture cancelled, exiting")
                raise
            except Exception as exc:
                # Never let an unexpected error kill the loop; log and continue.
                log.exception("agent iteration failed (continuing): %s", exc)

            await asyncio.sleep(config.poll_interval)
