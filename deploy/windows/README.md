# Windows local data-sovereignty client

Windows supports the DejaView daily client, local service lifecycle, and a
managed SSH tunnel to the stateless Radeon compute plane. Raw frames still pass
through a local `sentinel` role before any allowed text or embeddings can use
Radeon. The script never embeds a public host or credential; configure the
logical SSH alias `radeon-cloud` in the user's SSH config.

## Prerequisites

- Windows 10/11 with an interactive desktop session
- PowerShell 5.1 or newer
- Python 3.12, `uv`, Node.js, Git, OpenSSH
- Docker Desktop with the Compose plugin
- a local loopback LiteLLM gateway exposing the owned `sentinel` model
- `.env` and `deploy/mac/honcho.env` created from their examples

The SSH endpoint belongs only in the user's SSH configuration. If it is kept
in a dedicated file rather than the default config, set the user environment
variable `DEJAVIEW_SSH_CONFIG` to that file. Repository files and commands use
only the logical alias `radeon-cloud`.

## Commands

```powershell
.\deploy\windows\dejaview.cmd doctor
.\deploy\windows\dejaview.cmd tunnel-up
.\deploy\windows\dejaview.cmd product-up
.\deploy\windows\dejaview.cmd product-status
.\deploy\windows\dejaview.cmd capture
.\deploy\windows\dejaview.cmd product-down
.\deploy\windows\dejaview.cmd tunnel-down
```

`product-up` refuses occupied unowned ports and validates local Sentinel before
starting infrastructure. PID records bind the executable, command line, and
creation time; `product-down` stops only matching managed processes. Capture
uses Win32 window enumeration and in-memory `mss` pixels, writes no frame cache,
and pauses when the secure desktop is active.
