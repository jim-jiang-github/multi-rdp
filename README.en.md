# MultiRDP · Multi-user Remote Desktop Manager for Windows

**English** | [简体中文](README.md)

Create multiple isolated local Windows accounts in one click, each with its own remote desktop
window, and manage everything from a **system-tray resident** app. Built for running several
isolated environments on a single PC — game multi-boxing, test environments, giving family or
colleagues their own desktop, and so on.

![Web UI screenshot](docs/screenshot_webui_en.png)

> The button at the top right switches between English and Chinese instantly
> (Chinese UI: [docs/screenshot_webui.png](docs/screenshot_webui.png)).

> UI overview: **Create user** generates a random account name + strong password automatically;
> **Login** opens a separate mstsc window (Windows' built-in client); **Reset** signs the session
> out (useful when the desktop freezes); **Delete** removes the account, profile folder and
> registry leftovers.

## Features

- **One-click user creation** — random username (`p` + 6 chars, ambiguous characters avoided),
  14-char strong password, automatically added to `Remote Desktop Users`, optionally to
  `Administrators` (so UAC only needs a click instead of a password)
- **One-click login** — launches the built-in `mstsc.exe` against the local machine, one window
  per user. As soon as the window exists it is raised to the top and activated (the client is
  spawned by a background process and has no foreground rights, so without this it stays behind
  other windows); clicking Login for a user that is already running simply brings that window to
  the front. mstsc cannot take a password on the command line, so the tool stores it with `cmdkey`
  and gives every account its own loopback alias (`127.0.0.x`) to keep the credentials apart,
  cleaning them up on exit;
  resolution is configurable: "auto" opens windowed at the largest common size that
  fits on the screen (1600x900 on a 1080p display); a manually picked size that does
  not fit is scaled down proportionally; only picking "fullscreen" goes fullscreen)
- **Hide instead of disconnect** — "Hide window" only hides the client window; the RDP
  connection stays up, so the session remains `Active`: programs keep rendering and keep their
  focus, while the window disappears from the taskbar and Alt+Tab. "Show window" brings it back
  unchanged. Clicking the window's X really does drop the connection (session becomes
  `Disc`, which has no input channel and no real focus); the tool then reconnects once in the
  background to put the session back to `Active`, keeping the window's previous visibility
  (visible stays visible, hidden stays hidden; disable with `keep_alive: false` in
  `config.json`). Only Reset signs the session out and ends those programs. Window ownership
  and the hidden flag are stored in `clients.json`, so after the manager restarts it re-adopts
  the existing windows and "Show window" still works
- **Session reset** — logs the user's session off to recover from a black/frozen screen; the
  next login starts a fresh desktop
- **Clean user deletion** — logoff → delete account → delete profile via WMI → force-remove
  leftover folders → clean the `ProfileList` registry key → verify; incomplete deletions stay
  listed as "leftover" and can be retried
- **Orphan folder scan** — leftover profile folders created by this tool are re-listed so they
  can be cleaned up
- **White-screen repair** — detects whether OpenStreamMS hijacked the RDP graphics pipeline
  (`RDP-Tcp\LoadableProtocol_Object`) and restores the Microsoft protocol with one click
- **Tray resident** — `MultiRDP.exe` runs in the tray with a menu: Open Web UI / default-admin
  toggle / Exit
- **Bilingual UI (English / Chinese)** — one-click switch at the top right; the choice is stored
  in `config.json` (`lang`) and API error messages follow the same language

## Quick start

Requirements: Windows 10/11 x64, Python 3.9+, administrator rights.

### Option 1 — double-click to run (debug)

```text
scripts\run.bat
```

Self-elevates via UAC, starts the web UI in console mode and opens
<http://127.0.0.1:8123>. Closing the window stops the server; logs are visible in the console.

### Option 2 — build a single exe (tray resident)

```text
scripts\build.bat
```

Produces `dist\MultiRDP.exe` (PyInstaller, one file, no console window). The remote client is
the `mstsc.exe` that ships with Windows, so nothing else has to be copied. First run:

1. Double-click `dist\MultiRDP.exe` (it requests administrator rights) — it stays in the tray
2. Right-click the tray icon → Open Web UI

### Option 3 — plain Python

```powershell
python src\webui\server.py
```

## Project layout

```text
multi-rdp/
├── src/
│   ├── tray_app.py          # tray app entry point (pystray)
│   └── webui/
│       ├── server.py        # web UI + user management core (127.0.0.1:8123)
│       ├── screencap.py     # pure-ctypes GDI screenshot helper
│       ├── config.json      # UI config (admin_default / lang / resolution)
│       └── users.json.example  # user DB template (real users.json is ignored)
├── 3rd/                     # prebuilt components (usable as-is, no build needed)
│   ├── TermWrap.dll         # multi-session patch (423 KB)
│   ├── patchtest.exe        # offline patch self-test (27 signature checks)
│   └── patchlive.exe        # live patcher (patches TermService without reboot)
├── scripts/
│   ├── run.bat              # double-click run/debug
│   ├── build.bat            # package single exe (icon -> PyInstaller)
│   ├── make_icon.py         # vector-drawn app icon, emits assets/*.ico|png
│   ├── fix_termwrap.bat     # persist ServiceDll (effective after reboot)
│   ├── rollback_termwrap.bat# restore the original ServiceDll
│   └── check_termwrap.ps1   # check TermWrap load state / concurrent sessions flag
├── assets/
│   ├── multirdp.ico         # multi-size app icon (16/24/32/48/64/128/256)
│   ├── multirdp.png         # 1024px master
│   └── tray.png             # simplified tray variant (readable at 16px)
├── MultiRDP.spec            # PyInstaller entry point (icon + bundled tray.png)
└── docs/                    # screenshots and docs assets
```

## Multi-session patch (TermWrap)

Windows client editions allow only one remote session. `TermWrap.dll` runtime-patches
`termsrv.dll` to lift that limit, enabling **several users online at the same time**.

- Prebuilt dll: `3rd/TermWrap.dll` — ready to use, **no local build required** (sources are not distributed with this repo)

Two ways to enable it:

| Mode | Action | Notes |
| --- | --- | --- |
| Persistent | `scripts\fix_termwrap.bat` | Points TermService `ServiceDll` at TermWrap.dll; effective after reboot. `rollback_termwrap.bat` reverts it |
| Live patch | `3rd\patchlive.exe` | Patches the running TermService process in memory — no reboot, no service restart |

Verify with `scripts/check_termwrap.ps1`: ServiceDll value, modules loaded by the TermService
process, the `EnableConcurrentSessions` flag, and the current session list.

> Key registry value: `HKLM\SYSTEM\CurrentControlSet\Services\TermService\Parameters\ServiceDll`.
> The original value is backed up to `C:\Users\Public\termwrap_backup.json` by
> `fix_termwrap.ps1`.

## About the remote client

The client is `mstsc.exe`, which ships with Windows - no extra files, no third-party components.

mstsc cannot take a password on the command line, so the tool does it in two steps:

1. `cmdkey /generic:TERMSRV/<alias> /user:<account> /pass:<password>` stores the password in the
   current user's Windows credential vault (DPAPI-encrypted, readable only by that user on that
   machine)
2. `mstsc /v:<alias> /w:<width> /h:<height>`

Windows keys credentials **by host name**, so pointing every account at `127.0.0.1` would make them
overwrite each other. Instead every account gets its own fixed loopback alias (`127.0.0.0/8`
all routes to the local machine). The mapping is persisted in `aliases.json`, which guarantees both
that an account always gets the same alias and that no two accounts ever share one - the earlier
`crc32 % 200` scheme collided with ~20% probability at just ten accounts, and a collision means
"you log in and land on somebody else's desktop".

On a clean exit the tool removes the credentials it wrote with `cmdkey /delete`. If the process was
killed, check for leftovers with:

```text
cmdkey /list | findstr TERMSRV/127.0.0.
```

### One certificate prompt on the first connection

The first loopback connection triggers mstsc's "cannot verify the identity of this remote computer"
warning. The tool ticks "Don't ask me again for connections to this computer" and clicks Yes, after
which it never appears again. If a localized dialog or changed wording ever defeats that automation,
confirm it manually once - it stays silent afterwards too.

### When the client exits / the window disappears

- when the client process exits (crashes on its own or the window is closed with X), the
  reconnect **preserves visibility**: a visible window comes back visible and activated, a hidden
  one stays hidden; repeated crashes within five minutes back off automatically
- every action is logged to `client.log` ("mstsc launch / client exited / keep-alive")

### Why not FreeRDP (history)

Earlier versions used FreeRDP (`sdl3-freerdp.exe`), but **3.32.0 crashes reliably on Windows 10
26H1 (build 28000)**: it cannot parse the fastpath bitmap updates that newer builds send.
`client.log` shows `Fastpath update Bitmap [1] failed, status 0`, the process then dies with
`0xc0000005` at the same offset `0x886e83` every time (upstream
[issue #10067](https://github.com/FreeRDP/FreeRDP/issues/10067),
[#13883](https://github.com/FreeRDP/FreeRDP/issues/13883), fixed only after 3.32).

Both workarounds failed:

- `/gfx:AVC444` (graphics pipeline instead of bitmaps) - worse: crashes went from every 5 minutes
  to every 40 seconds
- `/relax-order-checks` (skip the bad update instead of dropping the connection) - the bad data is
  skipped but the stream pointer never advances, so the same update is re-parsed forever: 13k log
  lines in one minute and a completely frozen window

So this version switched to mstsc and `3rd/FreeRDP/` is gone from the repo. To go back, the last
FreeRDP implementation is the commit before `0b756dc`.

## Security notes

- `users.json` stores managed users' **plaintext passwords** (needed for one-click login). It is
  excluded via `.gitignore` — never commit or share it. Only `users.json.example` is in the repo.
- The web UI binds to `127.0.0.1` only; it is not exposed to the LAN or internet.
- This tool creates/deletes local accounts and modifies profiles and the registry. **Use it only
  on machines you administer.**

## Tech stack

Python 3 (stdlib `http.server` + `ctypes`/`winreg`), pystray + Pillow (tray), PyInstaller
(packaging), mstsc (the remote desktop client built into Windows); the TermWrap multi-session patch
ships as a prebuilt dll.

## License

[MIT](LICENSE)
