# MultiRDP · Multi-user Remote Desktop Manager for Windows

**English** | [简体中文](README.md)

Create multiple isolated local Windows accounts in one click, each with its own remote desktop
window, and manage everything from a **system-tray resident** app. Built for running several
isolated environments on a single PC — game multi-boxing, test environments, giving family or
colleagues their own desktop, and so on.

## ☕ Buy me a coffee

If this tool made your life easier, feel free to scan a QR code and buy the author a coffee
(any amount is appreciated — it's the thought that counts):

<p align="center">
  <img src="docs/donate_wechat.jpg" width="300" alt="WeChat Pay QR code">
  &nbsp;&nbsp;&nbsp;
  <img src="docs/donate_alipay.jpg" width="300" alt="Alipay QR code">
</p>

![Web UI screenshot](docs/screenshot_webui_en.png)

> The button at the top right switches between English and Chinese instantly
> (Chinese UI: [docs/screenshot_webui.png](docs/screenshot_webui.png)).

> UI overview: the first card in the grid is **New account** - click the plus (or the Create button
> inside it) to generate a random account name + strong password automatically;
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
  `Disc`, which has no input channel and no real focus) and the tool **will not bring it back** -
  click Login to reopen it. Only Reset signs the session out and ends those programs. Window
  ownership is stored in `clients.json`; even if that record is stale (mstsc was closed and
  reopened, the manager moved to another folder), on restart the window is re-adopted by
  matching the loopback address in its title, so "Show window" still works
- **Session reset** — logs the user's session off to recover from a black/frozen screen; the
  next login starts a fresh desktop
- **Clean user deletion** — logoff → delete account → delete profile via WMI → force-remove
  leftover folders → clean the `ProfileList` registry key → verify; incomplete deletions stay
  listed as "leftover" and can be retried
- **Card panel with live-ish previews** — one card per account, the top half is a thumbnail of that
  remote desktop. No video streaming: while the page is in the foreground it grabs one frame every
  6 seconds (`PrintWindow`, so an occluded window still captures), and it stops entirely when the
  tab goes to the background. The frame is scaled down as a whole and letterboxed instead of being
  cropped or stretched. Click a thumbnail to bring that remote window to the front; a hidden window
  keeps the last frame, and a disconnected session keeps showing the frame from just before it
  dropped (cached in `%LOCALAPPDATA%\MultiRDP\thumbs\`, so it survives a restart)
- **Orphan folder scan** — leftover profile folders created by this tool are re-listed so they
  can be cleaned up
- **White-screen repair** — detects whether OpenStreamMS hijacked the RDP graphics pipeline
  (`RDP-Tcp\LoadableProtocol_Object`) and restores the Microsoft protocol with one click
- **Tray resident** — `MultiRDP.exe` runs in the tray with a menu: Open Web UI / default-admin
  toggle / Check for updates / Exit
- **Auto-update** — polls GitHub for a new release, shows a banner in the web UI, then downloads,
  verifies SHA256 and swaps itself in place on one click (version numbers map 1:1 to tags, see
  "Auto-update" below)
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

Runtime data (`users.json`, `config.json`, `aliases.json`, `clients.json`, `thumbs/`) lives in
`%LOCALAPPDATA%\MultiRDP`, not next to the exe: you can move the exe around or overwrite it when
upgrading without touching your accounts. The tray menu has **Open data folder** for it. Files left
next to an exe by an older version are moved over automatically on first start (existing files are
never overwritten).

### Option 3 — plain Python

```powershell
python src\webui\server.py
```

### Option 4 — download a release

Every tag is built and published to
[Releases](https://github.com/jim-jiang-github/multi-rdp/releases) by GitHub Actions. Grab
`MultiRDP-vX.Y.Z-win64.zip` (unzip and run) or just `MultiRDP.exe` — no installer, no Python.
`SHA256SUMS.txt` in the release carries the matching checksums.

## Releasing

Cutting a release is a tag push; nothing is packaged locally. `.github/workflows/release.yml`
listens for `v*` tags, and on `windows-latest` it installs the deps, regenerates the icon,
derives the exe version resource from the tag, builds with PyInstaller, runs `--selftest`,
zips + hashes, and publishes the GitHub Release.

```bash
git tag v1.2.0
git push origin v1.2.0
```

| Event | Result |
| --- | --- |
| Tag `v1.2.0` pushed | Builds and publishes a Release; body lists commits since the previous tag |
| Tag `v1.2.0-rc.1` pushed | Same, flagged as a prerelease (installed clients won't auto-update to it) |
| Malformed tag (`v1.2`) pushed | Build fails — no release with an unparsable version ever ships |
| Actions → Release → Run workflow | Build only, artifact kept, no Release (smoke test) |

**The tag format is strict** because the version number comes entirely from the tag. The rule is
defined once (`TAG_RE` in `src/updater.py`) and shared by the build (`make_version.py`) and the
runtime auto-updater:

```
v<major>.<minor>.<patch>[-alpha.N | -beta.N | -rc.N]     e.g. v0.2.0, v1.3.0-rc.1
```

Tags without the `v` prefix, with a missing segment (`v1.2`) or an extra one (`v1.2.3.4`) are
rejected; CI runs `make_version.py --check-tag` first and fails loudly.

Assets: `MultiRDP.exe` (single file), `MultiRDP-vX.Y.Z-win64.zip` (exe + docs) and
`SHA256SUMS.txt`. The exe version comes from the tag (right-click → Properties → Details);
PyInstaller is pinned via `PYINSTALLER_VERSION` in the workflow to match `build.bat`.
CI builds are unsigned, so SmartScreen warns "unknown publisher" on first run.

## Auto-update

Installed exes check GitHub for a newer release (on by default; toggle in the tray menu, or click
the banner in the web UI):

- comparison = the exe's own version resource vs the latest release tag, both `vX.Y.Z`
- checks go through the GitHub API; downloads use the API asset endpoint, which survives proxies
  better than the `releases/download` URL
- every download is verified against `SHA256SUMS.txt`; on mismatch it is deleted, not installed
- replacement happens in place: the running exe is renamed to `.old` (Windows allows renaming a
  running exe), the new one is written to the original path, and the new process is started with
  `--after-update <pid>` so it waits for the old one to exit, then cleans up `.old`
- no version resource (local `build.bat` output, or plain `python`) = dev build, auto-update off

## Project layout

```text
multi-rdp/
├── src/
│   ├── tray_app.py          # tray app entry point (pystray)
│   ├── updater.py           # auto-update: check Release -> download -> SHA256 -> in-place swap
│   └── webui/
│       ├── server.py        # web UI + user management core (127.0.0.1:8123)
│       ├── screencap.py     # pure-ctypes GDI screenshot helper
│       ├── thumb.py         # capture a remote window -> JPEG thumbnail (card preview)
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
│   ├── make_version.py      # exe version resource from the tag (used by CI)
│   ├── fix_termwrap.bat     # persist ServiceDll (effective after reboot)
│   ├── rollback_termwrap.bat# restore the original ServiceDll
│   └── check_termwrap.ps1   # check TermWrap load state / concurrent sessions flag
├── assets/
│   ├── multirdp.ico         # multi-size app icon (16/24/32/48/64/128/256)
│   ├── multirdp.png         # 1024px master
│   └── tray.png             # simplified tray variant (readable at 16px)
├── MultiRDP.spec            # PyInstaller entry point (icon + bundled tray.png)
├── .github/workflows/
│   └── release.yml          # tag push -> build -> GitHub Release
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

- when the client process exits (window closed with X, or the process dies) only the bookkeeping is
  dropped - there is **no automatic reconnect** (an earlier keep-alive resurrected closed windows
  and was removed on request)
- the session itself may still be alive: click "Login" on the card to reopen the window, or "Reset"
  to end it for good
- every action is logged to `client.log` ("mstsc launch / client exited")

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
