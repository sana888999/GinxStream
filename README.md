<div align="center">

# SanaGinx
(build on top of Arrow)

**Web GUI + universal Firefox DRM capture + StreamingCommunity download core**

[![Version](https://img.shields.io/badge/version-1.0.8-5DE6FF?style=for-the-badge)](StreamingCommunity/upload/version.py)
[![Python](https://img.shields.io/badge/python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Firefox](https://img.shields.io/badge/Firefox-extension-FF7139?style=for-the-badge&logo=firefoxbrowser&logoColor=white)](firefox/extension/manifest.json)

Capture **DASH MPD + license** from any site you configure, send them to a local Python server, and download with Widevine / PlayReady key extraction.

</div>

---

## Table of contents

- [What you get](#what-you-get)
- [Requirements](#requirements)
- [Install](#install)
- [Firefox extension (load & configure)](#firefox-extension-load--configure)
- [Run the download server](#run-the-download-server)
- [CDM setup (DRM keys)](#cdm-setup-drm-keys)
- [Web GUI](#web-gui)
- [Cookies & login (`Conf/login.json`)](#cookies--login-confloginjson)
- [Other config files](#other-config-files)
- [Project layout](#project-layout)
- [Publish / backup checklist](#publish--backup-checklist)
- [Troubleshooting](#troubleshooting)

---

## What you get

| Piece | Role |
|--------|------|
| **SanaGinx web GUI** | Search and download from supported streaming sites (English UI) |
| **Firefox extension** | Configurable capture of MPD + license URL + headers on **any** site |
| **Download server** | `firefox/server/download_server.py` — receives capture and runs `DASH_Downloader` |
| **Core** | StreamingCommunity engine (HLS, DASH, DRM, mux, etc.) |

---

## Requirements

- **Windows / macOS / Linux** with Python **3.8+**
- **Firefox** (for the capture extension)
- **Widevine CDM** — `binary/device.wvd` *or* a working remote CDM in `Conf/remote_cdm.json`
- For site logins in the GUI: cookies / tokens in `Conf/login.json` (see below)

---

## Install

From the project root:

```powershell
git clone <your-repo-url> SanaGinx
cd SanaGinx
pip install -r requirements.txt
pip install -r GUI/requirements.txt
```

Optional: verify DRM setup before your first download:

```powershell
python tools/verify_cdm_setup.py
```

---

## Firefox extension (load & configure)

### 1. Load the extension (temporary)

Firefox does not ship this add-on from AMO yet — load it as a **temporary** extension while developing:

1. Open Firefox and go to **`about:debugging`**
2. Click **This Firefox** (left sidebar)
3. Click **Load Temporary Add-on…**
4. Select **`firefox/extension/manifest.json`** in this repo

> Temporary add-ons are removed when Firefox closes. Reload the same way after each browser restart (or use Firefox Developer Edition / `about:config` → `xpinstall.signatures.required` only if you know what you are doing for unsigned permanent installs).

### 2. Open extension settings

Any of these work:

- Toolbar puzzle icon → **SanaGinx DRM Capture** → **Preferences**
- Right-click the extension icon → **Manage Extension** → **Preferences**
- Extension popup → **Configure capture rules**

### 3. What to configure for your site

| Setting | Purpose |
|---------|---------|
| **Download server URL** | Default `http://127.0.0.1:47984` — must match the running Python server |
| **Origin / Referer** | Sent with MPD and license requests (match your player page if the CDN checks them) |
| **Capture rules (JSON)** | Tells the extension which URLs are licenses, segments, and `.mpd` files |

**Example preset (Pallycon + DASH):** in Options click **Load Pallycon + DASH preset**, or copy from:

`firefox/extension/examples/pallycon-dash-preset.json`

**Minimal custom rules shape:**

```json
{
  "licenseUrlIncludes": ["your-license-host"],
  "licenseHeader": "optional-header-name-for-token",
  "licenseWebRequestHosts": ["*://license.example.com/*"],
  "manifestWebRequestHosts": ["*://*/*"],
  "mpdUrlIncludes": [".mpd"],
  "segmentUrlIncludes": ["/assets/"],
  "deriveMpd": {
    "enabled": true,
    "pathMarker": "/assets/",
    "masterFile": "master.mpd"
  }
}
```

Rules are stored in Firefox as `sanaginx_config` (legacy `skool_*` keys are migrated automatically).

### 4. Download flow

1. Start the download server (next section)
2. Open your site and **play** the protected video (so license + MPD traffic happens)
3. Open the extension **popup** → **Download**
4. Files land in `videos/` (or the path from `Conf/config.json` → `OUTPUT.root_path`)

---

## Run the download server

From the **repo root**:

```powershell
python firefox/server/download_server.py
```

You should see:

```text
SanaGinx DRM download server
Listening on http://127.0.0.1:47984
```

| Variable | Effect |
|----------|--------|
| `SANAGINX_SAVE_DIR` | Override output folder |
| `STREAMINGCOMMUNITY_WVD_PATH` | Full path to `device.wvd` |
| `STREAMINGCOMMUNITY_PRD_PATH` | Full path to `device.prd` (PlayReady) |

The **Web GUI** can also start/stop this server from the DRM settings page.

---

## CDM setup (DRM keys)

Without a working CDM, capture succeeds but **key extraction fails**.

### Option A — Local `device.wvd` (recommended)

1. Place **`device.wvd`** in:
   - `binary/device.wvd` (inside this repo), **or**
   - `C:\binary\device.wvd` on Windows
2. Restart the download server
3. Confirm with:

```powershell
python tools/verify_cdm_setup.py
```

Look for **local Widevine device** in the output (not only remote CDM).

### Option B — Remote CDM on localhost

1. Run a **pywidevine serve**-compatible API on your machine
2. Copy `Conf/remote_cdm.localhost.EXAMPLE.json` → adjust `Conf/remote_cdm.json`:
   - `host` — your serve base URL (e.g. `http://127.0.0.1:8787`)
   - `secret` — matches serve `X-Secret-Key`
   - `device_name` — device alias from serve

### Option C — Third-party remote host

Edit `Conf/remote_cdm.json` only if the host is **reachable** from your network (timeouts mean downloads will fail).

> Never commit `device.wvd`, `device.prd`, or real secrets. They are listed in `.gitignore`.

---

## Web GUI

```powershell
cd GUI
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
```

Open **http://127.0.0.1:8000** — navbar brand **SanaGinx**, search and download cards in English.

Behind a reverse proxy, set `CSRF_TRUSTED_ORIGINS` and related Django env vars as needed.

---

## Cookies & login (`Conf/login.json`)

Built-in streaming sites (GUI / CLI) read credentials from **`Conf/login.json`**.

1. Copy the example:

```powershell
copy Conf\login.json.example Conf\login.json
```

2. Log in to the site in **Firefox** or Chrome
3. Open **Developer Tools** (F12) → **Storage** / **Application** → **Cookies**
4. Copy the values into the matching keys in `login.json`

| Service | Keys in `login.json` | How to get them |
|---------|----------------------|-----------------|
| **mappl** | `mappl.session_cookie` | DevTools → Network → any request → copy full **Cookie** header (or paste `name=value; ...` string) |
| **hydrahd** | `hydrahd.session_cookie` | Same — session cookies after login |
| **Crunchyroll** | `crunchyroll.etp_rt`, `crunchyroll.device_id` | Application → Cookies |
| **Mediaset Infinity** | `mediasetinfinity.beToken` | Cookie `beToken` |
| **Discovery+ EU** | `discoveryeu.st` | Cookie `st` |
| **Tubi** | `tubi.email`, `tubi.password` | Account credentials |
| **TMDB** | `TMDB.api_key` | [themoviedb.org](https://www.themoviedb.org/settings/api) API key (metadata) |

> `Conf/login.json` is **gitignored** — safe for your backup zip; do not publish it on GitHub.

More screenshots for legacy services: [`.github/doc/login.md`](.github/doc/login.md)

**Firefox extension DRM path** does not use `login.json` — you must be **logged in in the browser** when you play the video so the extension can capture license headers. Use Origin/Referer in extension options if the CDN requires them.

---

## Other config files

| File | Purpose |
|------|---------|
| `Conf/config.json` | Output folders, download threads, ffmpeg-style processing |
| `Conf/remote_cdm.json` | Remote Widevine / PlayReady CDM endpoints |
| `Conf/user_prefs.json` | GUI / user preferences |
| `Conf/domains.json` | Site domain list (auto-fetched if enabled) |

Output directory default: **`videos/`** (`OUTPUT.root_path` in `config.json`).

**Optional remote sync** (only if you host your own GitHub repo — not required):

| Environment variable | Purpose |
|---------------------|---------|
| `SANAGINX_GITHUB_REPO` | `youruser/SanaGinx` for release/update checks |
| `SANAGINX_RAW_BASE` | Raw URL base to download `Conf/*.json` when missing |
| `SANAGINX_DOMAINS_URL` | Full URL to a `domains.json` when `fetch_domain_online` is true |
| `SANAGINX_BINARY_RAW` | Raw base for ffmpeg/binary auto-download manifest |

By default SanaGinx uses **local `Conf/` files only** (no upstream Arrowar links).

---

## Project layout

```text
SanaGinx/
├── Conf/                    # config, login (local), remote CDM
├── GUI/                     # Django web GUI
├── firefox/
│   ├── extension/           # SanaGinx DRM Capture (load manifest.json)
│   │   ├── options.html     # capture rules UI
│   │   └── examples/        # pallycon-dash-preset.json
│   └── server/
│       └── download_server.py
├── StreamingCommunity/      # download + DRM core
├── binary/                  # put device.wvd here (not committed)
├── tools/
│   └── verify_cdm_setup.py
└── README.md                # you are here
```

---

## Publish / backup checklist

- [ ] Remove `Conf/login.json` from any public repo (use `login.json.example` only)
- [ ] Do not commit `binary/*.wvd` or `binary/*.prd`
- [ ] Exclude dev folders: `editingskool/`, `creators-club/`, `ai-cache/`, `.cursor/`
- [ ] Run `python tools/verify_cdm_setup.py` on a clean machine
- [ ] Load extension from `firefox/extension/` and test one DRM title

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Extension says server not running | Start `python firefox/server/download_server.py` on port **47984** |
| Capture works, download fails on keys | Add `device.wvd` or fix `Conf/remote_cdm.json` — run `verify_cdm_setup.py` |
| Remote CDM timeout | Host unreachable; use local CDM or localhost serve |
| GUI search in Italian | GUI forces English titles; clear old DB/cards if needed |
| `mappl: failed to fetch` / curl timeout | Network/DNS to site, not cookies — check connectivity |

---

<div align="center">

**SanaGinx** · built on the StreamingCommunity download stack · v1.0.8

</div>
