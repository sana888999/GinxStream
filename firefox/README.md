# Skool Video Downloader – Firefox extension

Download DRM videos from **learn.editingskool.com** (TagMango) from the browser: play the video, then click **Download** in the extension. If the token expires, click **Refresh token**, play the video again, and Download.

## What you need

- **Firefox** (desktop)
- **Python** with the StreamingCommunity project set up (same as for crypterSkool: config, CDM, etc.)
- The download **server** must be running so the extension can send the captured data to your PC.

## 1. Install the extension

1. Open Firefox and go to `about:debugging`.
2. Click **This Firefox** → **Load Temporary Add-on**.
3. Open the `firefox/extension` folder and select **manifest.json**.
4. The extension is loaded until you close Firefox. To keep it after restart, use **Load Temporary Add-on** again, or package the extension and install it unsigned (see [Firefox docs](https://extensionworkshop.com/documentation/develop/temporary-installation-in-firefox/)).

## 2. Start the download server

From the **project root** (StreamingCommunity-main), run:

```bash
python firefox/server/download_server.py
```

Or from anywhere, with the project root on `PYTHONPATH` and cwd:

```bash
cd path/to/StreamingCommunity-main
python firefox/server/download_server.py
```

You should see:

- `Listening on http://127.0.0.1:47984`
- Default save folder: `StreamingCommunity-main/Downloads`

To use another folder:

- **Windows (PowerShell):** `$env:SKOOL_SAVE_DIR="C:\Videos"; python firefox/server/download_server.py`
- **Linux/macOS:** `SKOOL_SAVE_DIR=/path/to/folder python firefox/server/download_server.py`

Leave this terminal open while you use the extension.

## 3. Use the extension

1. Go to **learn.editingskool.com**, open a course and a video.
2. **Play the video** (or let the player load). The extension captures MPD URL, license URL, and token in the background.
3. Click the **extension icon** in the toolbar. The popup shows **Ready: &lt;name&gt;** when a capture is available.
4. Click **Download**. The server runs the download (same as crypterSkool). When it finishes, the popup shows the path or an error.
5. If you see an error (e.g. expired token):
   - Click **Refresh token**.
   - Play the video again on the page.
   - When the popup shows **Ready** again, click **Download**.

Videos are saved under the server’s save folder (by default `StreamingCommunity-main/Downloads`) as **MP4** (downloaded as MKV then converted with ffmpeg).

## Layout

- **firefox/extension/** – Firefox add-on (manifest, background script, popup, content script).
- **firefox/server/download_server.py** – Local HTTP server that receives captures from the extension and runs the DASH download (StreamingCommunity).
- **firefox/README.md** – This file.

## Troubleshooting

- **“Is the download server running?”** – Start `python firefox/server/download_server.py` from the project root and keep it running.
- **Download fails (token / license error)** – Use **Refresh token**, then play the video again and **Download**.
- **No “Ready” in popup** – Play the video on the page first; the extension only captures when the license and segment requests go through (same as the mitmproxy addon).
- Save folder is `StreamingCommunity-main/Downloads` unless you set `SKOOL_SAVE_DIR`.
