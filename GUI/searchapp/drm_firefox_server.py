# Firefox Skool DRM download server — process + log buffer for Crypter GUI.
# Does not modify firefox/server/download_server.py or the extension.

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Project root = parent of GUI/
_THIS = Path(__file__).resolve()
PROJECT_ROOT = _THIS.parent.parent.parent
SERVER_SCRIPT = PROJECT_ROOT / "firefox" / "server" / "download_server.py"
DEFAULT_PORT = 47984
LOG_MAX_LINES = 8000
_LOCK = threading.Lock()

_proc: Optional[subprocess.Popen] = None
_reader_thread: Optional[threading.Thread] = None
_log_lines: deque = deque(maxlen=LOG_MAX_LINES)
_seq = 0
_paused_process = False


def _append_log(text: str) -> None:
    global _seq
    with _LOCK:
        _seq += 1
        _log_lines.append({"seq": _seq, "line": text})


def _clear_logs() -> None:
    global _seq
    with _LOCK:
        _log_lines.clear()
        _seq = 0


def _is_port_open(host: str = "127.0.0.1", port: int = DEFAULT_PORT, timeout: float = 0.25) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        s.connect((host, port))
        s.close()
        return True
    except OSError:
        return False


def _http_status_json() -> Optional[Dict[str, Any]]:
    try:
        import urllib.request

        req = urllib.request.Request(f"http://127.0.0.1:{DEFAULT_PORT}/status")
        with urllib.request.urlopen(req, timeout=1.5) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def get_status() -> Dict[str, Any]:
    """Return server state for the DRM UI."""
    with _LOCK:
        managed = _proc is not None and _proc.poll() is None
        pid = _proc.pid if managed else None

    port_listening = _is_port_open()
    remote = _http_status_json()
    save_dir = os.environ.get("SKOOL_SAVE_DIR")
    if remote and isinstance(remote.get("save_dir"), str):
        save_dir = remote["save_dir"]

    external = port_listening and not managed

    return {
        "managed": managed,
        "external": external,
        "running": port_listening,
        "pid": pid,
        "paused": _paused_process,
        "port": DEFAULT_PORT,
        "save_dir": save_dir or str(PROJECT_ROOT / "Downloads"),
        "script_exists": SERVER_SCRIPT.is_file(),
        "script_path": str(SERVER_SCRIPT),
        "log_line_count": len(_log_lines),
        "last_seq": _seq,
    }


def _reader_loop(proc: subprocess.Popen) -> None:
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        while proc.poll() is None:
            line = proc.stdout.readline()
            if not line:
                break
            try:
                text = line.decode(enc, errors="replace").rstrip("\r\n")
            except Exception:
                text = str(line).rstrip("\r\n")
            _append_log(text)
        # Drain remainder
        if proc.stdout:
            rest = proc.stdout.read()
            if rest:
                try:
                    for ln in rest.decode(enc, errors="replace").splitlines():
                        _append_log(ln.rstrip("\r\n"))
                except Exception:
                    pass
    except Exception as e:
        _append_log(f"[DRM] Log reader ended: {e}")
    finally:
        _append_log("[DRM] --- server process ended ---")


def start_server() -> Tuple[bool, str]:
    global _proc, _reader_thread, _paused_process
    if not SERVER_SCRIPT.is_file():
        return False, f"Server script not found: {SERVER_SCRIPT}"

    with _LOCK:
        if _proc is not None and _proc.poll() is None:
            return False, "Firefox DRM server is already running (managed by Crypter)."
    # Avoid starting a second listener on the same port (e.g. manual `python firefox/server/download_server.py`).
    if _is_port_open():
        return (
            False,
            f"Port {DEFAULT_PORT} is already in use. Stop the other firefox server "
            "(or close the terminal that runs it), then click Run.",
        )

    env = os.environ.copy()
    # Ensure extension POST can reach local server; cwd must be project root (same as CLI).
    try:
        proc = subprocess.Popen(
            [sys.executable, str(SERVER_SCRIPT)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception as e:
        return False, str(e)

    with _LOCK:
        _proc = proc
        _paused_process = False

    _append_log(f"[DRM] Started firefox/server/download_server.py (pid={proc.pid})")
    _reader_thread = threading.Thread(target=_reader_loop, args=(proc,), daemon=True)
    _reader_thread.start()
    return True, "Server started."


def stop_server() -> Tuple[bool, str]:
    global _proc, _paused_process
    with _LOCK:
        proc = _proc
        _proc = None
        _paused_process = False

    if proc is None:
        return False, "No managed server process."

    if proc.poll() is not None:
        return True, "Process already exited."

    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as e:
        return False, str(e)
    _append_log("[DRM] Server stopped.")
    return True, "Server stopped."


def refresh_server() -> Tuple[bool, str]:
    """Stop managed process (if any) and start a new one (rerun server only)."""
    stop_server()
    time.sleep(0.4)
    # If something else still holds the port, start_server will fail clearly.
    ok, msg = start_server()
    return ok, msg


def pause_server() -> Tuple[bool, str]:
    """Suspend the managed child process (extension requests will hang until resume)."""
    global _paused_process
    with _LOCK:
        proc = _proc
    if proc is None or proc.poll() is not None:
        return False, "No running managed server to pause."
    try:
        import psutil

        psutil.Process(proc.pid).suspend()
        _paused_process = True
        _append_log("[DRM] Server process suspended (pause).")
        return True, "Server paused."
    except ImportError:
        return False, "Pause requires the `psutil` package: pip install psutil"
    except Exception as e:
        return False, str(e)


def resume_server() -> Tuple[bool, str]:
    global _paused_process
    with _LOCK:
        proc = _proc
    if proc is None or proc.poll() is not None:
        return False, "No managed server process."
    try:
        import psutil

        psutil.Process(proc.pid).resume()
        _paused_process = False
        _append_log("[DRM] Server process resumed.")
        return True, "Server resumed."
    except ImportError:
        return False, "Resume requires the `psutil` package: pip install psutil"
    except Exception as e:
        return False, str(e)


def fetch_logs_after(after_seq: int, limit: int = 500) -> List[Dict[str, Any]]:
    """Return log rows with seq > after_seq (for live polling)."""
    out: List[Dict[str, Any]] = []
    with _LOCK:
        for row in _log_lines:
            if row["seq"] > after_seq:
                out.append(dict(row))
                if len(out) >= limit:
                    break
    return out


def clear_logs() -> None:
    _clear_logs()
    _append_log("[DRM] Log buffer cleared.")
