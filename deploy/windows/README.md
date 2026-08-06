# Windows capture client

The repository includes a Windows capture backend and PowerShell lifecycle
wrapper. It keeps frame pixels in memory, pauses on the secure desktop, and
uses a local `sentinel` role before an allowed request can reach the stateless
Radeon host.

The contest-verified product path is still macOS. Treat this page as the
Windows setup and capture contract, not as a claim that the full Windows stack
has passed final live acceptance. The wrapper uses the logical SSH alias
`radeon-cloud`; it never stores a public host, port, or credential in the repo.

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
