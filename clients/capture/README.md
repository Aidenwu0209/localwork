# DejaView capture client (macOS MVP)

Continuously senses the user's screen: captures a frame whenever the frontmost
window or its title changes (with a 30s periodic fallback) and POSTs it
**in memory** to memoryd's `/v1/ingest/frame` endpoint. Pixels never touch
disk on the client side — a frame is captured, encoded to WebP in RAM, uploaded,
then dropped (handbook §5.2 privacy invariant).

## What it does

- Captures the primary display at native (Retina) resolution via `mss`.
- Scales the frame down to width ≤ 2560px and encodes it to WebP quality 80,
  all in memory.
- Reads the frontmost app name + window title via `NSWorkspace` +
  `CGWindowListCopyWindowInfo`.
- Optionally probes the active browser tab URL via `osascript` (best effort).
- POSTs `{file, meta}` to memoryd as `multipart/form-data`.
- Pauses while the session is locked or the screensaver is running.
- Detects a missing Screen Recording permission (all-black frame) and prints
  step-by-step guidance instead of crashing.

## Install & run

```bash
cd clients/capture
uv run python -m capture
```

The first run uses defaults (`memoryd_url=http://127.0.0.1:8090`,
`device_id=<hostname>`). For anything else, copy the example config and edit:

```bash
cp capture.yaml.example capture.yaml
$EDITOR capture.yaml
```

memoryd must be running first:

```bash
cd services/memoryd && uv run python -m memoryd   # listens on 127.0.0.1:8090
```

## Screen Recording permission (required, one-time grant)

macOS 10.15+ blocks pixel capture until you grant Screen Recording permission.
Without it, `mss` returns a valid frame object whose pixels are all black — so
the client detects this on startup and prints guidance. To grant:

1. Open **System Settings → Privacy & Security → Screen Recording**.
   (One-liner from the terminal:)
   ```bash
   open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
   ```
2. Find the app you used to launch capture:
   - Ran it in **Terminal**? Add `Terminal` (or iTerm2 / your terminal app).
   - Ran it inside an **IDE** (VS Code, Cursor, ZCode, …)? Add that IDE **and**
     the integrated-terminal host it spawned from.
3. Toggle its switch **ON**. macOS will prompt to quit the app.
4. **Quit the app fully (⌘Q)** and relaunch it, then run capture again.

The first non-black frame confirms it worked. Until then, capture keeps
running (it does not crash) but every frame is black and useless to OCR.

> Note: AppleScript automation (for the optional browser-URL probe) needs a
> separate one-time grant under **Privacy & Security → Automation**. If you
> deny it, the URL field is simply `null` — the main capture loop is unaffected.

## Config reference (`capture.yaml`)

| key                   | default                  | meaning                                                 |
|-----------------------|--------------------------|---------------------------------------------------------|
| `memoryd_url`         | `http://127.0.0.1:8090`  | memoryd base URL; `/v1/ingest/frame` is appended.       |
| `device_id`           | short hostname           | Stable identifier for this machine in the timeline.     |
| `poll_interval`       | `3.0`                    | Seconds between frontmost-window checks.                |
| `min_capture_interval`| `3.0`                    | Minimum seconds between any two uploads (anti-flood).   |
| `periodic_interval`   | `30.0`                   | Fallback frame cadence when nothing changed.            |
| `max_upload_width`    | `2560`                   | Scale frames to width ≤ this (px); keeps OCR detail.    |
| `webp_quality`        | `80`                     | WebP encode quality (0–100).                            |
| `probe_url`           | `true`                   | Best-effort browser URL probe via osascript.            |

Environment overrides (take precedence over the file):
`CAPTURE_CONFIG`, `CAPTURE_MEMORYD_URL`, `CAPTURE_DEVICE_ID`, `DEJAVIEW_DEVICE_ID`.

Config file search order (first hit wins):
1. `$CAPTURE_CONFIG`
2. `./capture.yaml`
3. `~/.config/dejaview/capture.yaml`

## Module layout

```
src/capture/
  __init__.py      entry point (python -m capture)
  __main__.py      thin wrapper for `python -m capture`
  config.py        capture.yaml loader + defaults (device_id <- hostname)
  screenshot.py    mss capture -> scale to <=2560px -> WebP q80 (in memory)
  windows.py       get_active_window() via NSWorkspace + CGWindowListCopyWindowInfo
  permissions.py   Screen Recording permission detection (black-frame heuristic) + guidance
  url_probe.py     osascript probe for Safari/Chrome active-tab URL (best effort)
  uploader.py      async httpx POST /v1/ingest/frame (multipart); drops on failure
  agent.py         main loop: 3s change detect, 30s periodic, lock/screensaver pause
```

## Reporting contract

`POST /v1/ingest/frame` (multipart/form-data):

- `file`: WebP image bytes (filename `frame.webp`, type `image/webp`)
- `meta`: JSON matching memoryd's `FrameMeta`
  (`device_id`, `ts` ISO-8601 UTC, `app`, `window_title`, `url|null`,
  `trigger: "change" | "periodic"`)

On any network/HTTP error the frame is dropped silently — nothing is cached
to disk (privacy invariant).
