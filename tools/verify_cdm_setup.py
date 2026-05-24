#!/usr/bin/env python3
"""
Quick check: local CDM files + (optional) reachability of remote_cdm hosts.
Run from repo root: python tools/verify_cdm_setup.py
"""
from __future__ import annotations

import json
import os
import sys


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _load_remote_hosts(conf_path: str) -> tuple[str | None, str | None]:
    try:
        with open(conf_path, encoding="utf-8") as f:
            data = json.load(f)
        rc = data.get("remote_cdm") or {}
        wv = (rc.get("widevine") or {}).get("host")
        pr = (rc.get("playready") or {}).get("host")
        return (str(wv) if wv else None, str(pr) if pr else None)
    except OSError:
        return (None, None)


def main() -> int:
    root = _repo_root()
    os.chdir(root)
    if root not in sys.path:
        sys.path.insert(0, root)

    print("=== CDM setup check ===\n")
    print("Repo:", root)
    print()

    env_wvd = (os.environ.get("STREAMINGCOMMUNITY_WVD_PATH") or "").strip()
    env_prd = (os.environ.get("STREAMINGCOMMUNITY_PRD_PATH") or "").strip()
    if env_wvd:
        ok = os.path.isfile(env_wvd) and os.path.getsize(env_wvd) > 0
        print("STREAMINGCOMMUNITY_WVD_PATH:", env_wvd, "OK" if ok else "MISSING/EMPTY")
    else:
        print("STREAMINGCOMMUNITY_WVD_PATH: (not set)")
    if env_prd:
        ok = os.path.isfile(env_prd) and os.path.getsize(env_prd) > 0
        print("STREAMINGCOMMUNITY_PRD_PATH:", env_prd, "OK" if ok else "MISSING/EMPTY")
    else:
        print("STREAMINGCOMMUNITY_PRD_PATH: (not set)")
    print()

    for label, sub in (
        ("Project binary folder", os.path.join(root, "binary")),
        ("Project binaries folder", os.path.join(root, "binaries")),
    ):
        if os.path.isdir(sub):
            wvds = [f for f in os.listdir(sub) if f.lower().endswith(".wvd")]
            prds = [f for f in os.listdir(sub) if f.lower().endswith(".prd")]
            print(f"{label}: {sub}")
            print(f"  .wvd files: {wvds or '(none)'}")
            print(f"  .prd files: {prds or '(none)'}")
        else:
            print(f"{label}: (missing - create it and drop device.wvd here)")
        print()

    try:
        from StreamingCommunity.setup.binary_paths import binary_paths

        bdir = binary_paths.get_binary_directory()
        print("OS-wide binary folder (e.g. C:\\binary):", bdir)
        if os.path.isdir(bdir):
            wvds = [f for f in os.listdir(bdir) if f.lower().endswith(".wvd")]
            prds = [f for f in os.listdir(bdir) if f.lower().endswith(".prd")]
            print("  .wvd:", wvds or "(none)")
            print("  .prd:", prds or "(none)")
    except Exception as e:
        print("Could not read binary_paths:", e)
    print()

    try:
        from StreamingCommunity.setup import get_wvd_path, get_prd_path

        wvd = get_wvd_path()
        prd = get_prd_path()
        print("Resolved by app:")
        print("  Widevine (.wvd):", wvd or "(none - will use remote Widevine if configured)")
        print("  PlayReady (.prd):", prd or "(none - will use remote PlayReady if configured)")
    except Exception as e:
        print("Could not resolve paths via StreamingCommunity.setup:", e)
    print()

    conf = os.path.join(root, "Conf", "remote_cdm.json")
    wv_host, pr_host = _load_remote_hosts(conf)
    print("Conf/remote_cdm.json hosts (for fallback when no local file):")
    print("  widevine:", wv_host or "(missing)")
    print("  playready:", pr_host or "(missing)")
    print()

    # Optional quick probe (no hard dependency on urllib succeeding)
    try:
        import urllib.request

        for name, url in (("Widevine remote", wv_host), ("PlayReady remote", pr_host)):
            if not url or not url.startswith("http"):
                continue
            try:
                req = urllib.request.Request(url, method="HEAD")
                urllib.request.urlopen(req, timeout=5)
                print(f"{name} HEAD {url[:48]}... -> OK (reachable)")
            except Exception as e:
                print(f"{name} HEAD -> failed ({type(e).__name__}). Local .wvd/.prd still works if present.")
    except Exception:
        pass

    print()
    print("Next steps:")
    print("  - Easiest: put device.wvd in", os.path.join(root, "binary"), "then restart download_server.py")
    print("  - Or set STREAMINGCOMMUNITY_WVD_PATH to the full path of your .wvd file.")
    print("  - Remote CDM: use your own pywidevine serve on 127.0.0.1 - see README.md (CDM setup)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
