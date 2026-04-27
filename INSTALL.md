# Run commands

From project root: `StreamingCommunity-main`

---

## Firefox extension (play video → Download)

**Install once:** Firefox → `about:debugging` → This Firefox → Load Temporary Add-on → select `firefox/extension/manifest.json`

**Run server:**
```bash
python firefox/server/download_server.py
```

---

## mitm + skooltokenfetch (capture MPD + token to file; crypterSkool auto-imports)

```bash
cd Test/Downloads
mitmdump -s skooltokenfetch.py --listen-host 127.0.0.1 --listen-port 8080
```

Set browser proxy to `127.0.0.1:8080`. Play video; capture goes to `Test/Downloads/skool_captured.txt`.

---

## crypterSkool (GUI: paste MPD + license + token, or use auto-import from skool_captured.txt)

```bash
cd Test/Downloads
python crypterSkool.py
```

Start the server first if using the Firefox extension. Start mitmdump first if using skooltokenfetch capture.
