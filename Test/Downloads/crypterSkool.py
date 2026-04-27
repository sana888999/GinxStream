# crypterSkool - GUI for DASH download (MPD + license + Pallycon token).
# Downloads video+audio, converts to MP4. Default save: script_dir/Downloads; default names: 1.mp4, 2.mp4, ...

import os
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# Project root for StreamingCommunity (config expects cwd = project root)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
sys.path.insert(0, ROOT_DIR)
os.chdir(ROOT_DIR)

# Default save folder: root dir / Downloads
DEFAULT_SAVE_DIR = os.path.join(ROOT_DIR, "Downloads")
# skooltokenfetch (mitmproxy addon) writes captured MPD+license+token here
SKOOL_CAPTURED_FILE = os.path.join(SCRIPT_DIR, "skool_captured.txt")
SKOOL_POLL_MS = 1500  # poll capture file often so tokens are used while still valid

# Default headers (TagMango + Pallycon). User pastes only pallycon-customdata-v2 per video.
MPD_HEADERS = {
    "Accept": "*/*",
    "Origin": "https://learn.editingskool.com",
    "Referer": "https://learn.editingskool.com/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
}


def build_license_headers(pallycon_token: str):
    return {
        "Content-Type": "application/octet-stream",
        "Origin": "https://learn.editingskool.com",
        "Referer": "https://learn.editingskool.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0",
        "pallycon-customdata-v2": (pallycon_token or "").strip(),
    }


def next_default_number(save_dir: str) -> int:
    """Next number for 1.mp4, 2.mp4, ... in save_dir."""
    if not os.path.isdir(save_dir):
        return 1
    existing = set()
    for f in os.listdir(save_dir):
        m = re.match(r"^(\d+)\.mp4$", f, re.IGNORECASE)
        if m:
            existing.add(int(m.group(1)))
    n = 1
    while n in existing:
        n += 1
    return n


def mkv_to_mp4(ffmpeg_path: str, mkv_path: str, mp4_path: str) -> bool:
    """Convert mkv to mp4 with -c copy. Returns True on success."""
    if not os.path.isfile(mkv_path):
        return False
    cmd = [ffmpeg_path, "-i", mkv_path, "-c", "copy", "-y", mp4_path]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if r.returncode == 0 and os.path.isfile(mp4_path):
            try:
                os.remove(mkv_path)
            except Exception:
                pass
            return True
    except Exception:
        pass
    return False


class CrypterSkoolApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("crypterSkool")
        self.root.minsize(520, 420)
        self.root.geometry("680x520")

        self.queue = []  # list of {mpd_url, license_url, pallycon_token, name (optional)}
        self.default_counter = 1
        self.downloading = False
        self.ffmpeg_path = None
        self.auto_start_on_capture = tk.BooleanVar(value=True)  # start download when importing so token stays fresh

        self._build_ui()
        self._load_ffmpeg()
        self._start_skool_poll()

    def _load_ffmpeg(self):
        try:
            from StreamingCommunity.setup import get_ffmpeg_path
            self.ffmpeg_path = get_ffmpeg_path()
        except Exception:
            self.ffmpeg_path = "ffmpeg"

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=8)
        main.pack(fill=tk.BOTH, expand=True)

        # --- Inputs ---
        ttk.Label(main, text="MPD URL (master.mpd):").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.entry_mpd = ttk.Entry(main, width=70)
        self.entry_mpd.grid(row=1, column=0, columnspan=2, sticky=tk.EW, pady=2)

        ttk.Label(main, text="License URL (e.g. licenseManager.do):").grid(row=2, column=0, sticky=tk.W, pady=2)
        self.entry_license = ttk.Entry(main, width=70)
        self.entry_license.grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=2)

        ttk.Label(main, text="pallycon-customdata-v2 (paste Request Header value):").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.entry_token = tk.Text(main, height=3, width=70, wrap=tk.WORD)
        self.entry_token.grid(row=5, column=0, columnspan=2, sticky=tk.EW, pady=2)

        ttk.Label(main, text="Video name (optional; leave empty for 1.mp4, 2.mp4, ...):").grid(row=6, column=0, sticky=tk.W, pady=2)
        self.entry_name = ttk.Entry(main, width=50)
        self.entry_name.grid(row=7, column=0, sticky=tk.W, pady=2)

        ttk.Button(main, text="Add to queue", command=self._add_to_queue).grid(row=7, column=1, pady=2, padx=4)

        # --- Save path ---
        ttk.Label(main, text="Save folder (default: root/Downloads):").grid(row=8, column=0, sticky=tk.W, pady=(12, 2))
        self.entry_save = ttk.Entry(main, width=60)
        self.entry_save.insert(0, DEFAULT_SAVE_DIR)
        self.entry_save.grid(row=9, column=0, sticky=tk.EW, pady=2)
        ttk.Button(main, text="Browse...", command=self._browse_save).grid(row=9, column=1, padx=4)
        ttk.Checkbutton(main, text="Auto-start download when importing from capture (keeps token fresh)", variable=self.auto_start_on_capture).grid(row=10, column=0, columnspan=2, sticky=tk.W, pady=2)

        # --- Queue ---
        ttk.Label(main, text="Queue:").grid(row=11, column=0, sticky=tk.W, pady=(12, 2))
        self.queue_frame = ttk.Frame(main)
        self.queue_frame.grid(row=12, column=0, columnspan=2, sticky=tk.NSEW, pady=2)
        self.listbox_queue = tk.Listbox(self.queue_frame, height=6, selectmode=tk.SINGLE)
        scroll = ttk.Scrollbar(self.queue_frame)
        self.listbox_queue.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox_queue.config(yscrollcommand=scroll.set)
        scroll.config(command=self.listbox_queue.yview)

        btn_frame = ttk.Frame(main)
        btn_frame.grid(row=13, column=0, columnspan=2, pady=4)
        ttk.Button(btn_frame, text="Remove selected", command=self._remove_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="Clear queue", command=self._clear_queue).pack(side=tk.LEFT, padx=2)
        self.btn_start = ttk.Button(btn_frame, text="Start download", command=self._start_download)
        self.btn_start.pack(side=tk.LEFT, padx=8)

        # --- Log ---
        ttk.Label(main, text="Log:").grid(row=14, column=0, sticky=tk.W, pady=(8, 2))
        self.log_text = scrolledtext.ScrolledText(main, height=10, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.grid(row=15, column=0, columnspan=2, sticky=tk.NSEW, pady=2)

        self.status_var = tk.StringVar(value="Ready. Add MPD + license URL + token, or play videos with mitmproxy+skooltokenfetch to auto-import.")
        ttk.Label(main, textvariable=self.status_var).grid(row=16, column=0, columnspan=2, sticky=tk.W, pady=4)

        main.columnconfigure(0, weight=1)
        main.rowconfigure(15, weight=1)

    def _start_skool_poll(self):
        """Poll skool_captured.txt every SKOOL_POLL_MS; add new lines and optionally start download so token stays fresh."""
        def poll():
            if self.downloading:
                self.root.after(SKOOL_POLL_MS, poll)
                return
            path = SKOOL_CAPTURED_FILE
            if not os.path.isfile(path):
                self.root.after(SKOOL_POLL_MS, poll)
                return
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                self.root.after(SKOOL_POLL_MS, poll)
                return
            keep = []
            added = 0
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    mpd_url = parts[0].strip()
                    license_url = parts[1].strip()
                    token = parts[2].strip()
                    name = (parts[3].strip() or None) if len(parts) > 3 else None
                    if mpd_url and ".mpd" in mpd_url.lower() and license_url and token:
                        self.queue.append({"mpd_url": mpd_url, "license_url": license_url, "pallycon_token": token, "name": name})
                        display = name or "(default → 1.mp4, 2.mp4, ...)"
                        self.listbox_queue.insert(tk.END, display)
                        added += 1
                        self._log(f"Auto-imported: {name or mpd_url[:50]}...")
                    else:
                        keep.append(line + "\n")
                else:
                    keep.append(line + "\n")
            if added:
                self.status_var.set(f"Imported {added} from skooltokenfetch. Queue: {len(self.queue)} item(s).")
                if self.auto_start_on_capture.get() and (self.entry_save.get() or DEFAULT_SAVE_DIR).strip():
                    self.root.after(0, self._start_download)
            try:
                with open(path, "w", encoding="utf-8") as f:
                    f.writelines(keep)
            except Exception:
                pass
            self.root.after(SKOOL_POLL_MS, poll)
        self.root.after(SKOOL_POLL_MS, poll)

    def _browse_save(self):
        d = filedialog.askdirectory(initialdir=self.entry_save.get() or DEFAULT_SAVE_DIR, title="Save folder")
        if d:
            self.entry_save.delete(0, tk.END)
            self.entry_save.insert(0, d)

    def _log(self, msg: str):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _add_to_queue(self):
        mpd = (self.entry_mpd.get() or "").strip()
        license_url = (self.entry_license.get() or "").strip()
        token = self.entry_token.get("1.0", tk.END).strip()
        name = (self.entry_name.get() or "").strip() or None
        if not mpd or ".mpd" not in mpd.lower():
            messagebox.showwarning("Invalid input", "Enter a valid MPD URL (e.g. .../master.mpd)")
            return
        if not license_url:
            messagebox.showwarning("Invalid input", "Enter the license URL")
            return
        if not token:
            messagebox.showwarning("Invalid input", "Paste the pallycon-customdata-v2 value from the browser")
            return
        self.queue.append({"mpd_url": mpd, "license_url": license_url, "pallycon_token": token, "name": name})
        display = name or "(default → 1.mp4, 2.mp4, ...)"
        self.listbox_queue.insert(tk.END, display)
        self.entry_mpd.delete(0, tk.END)
        self.entry_license.delete(0, tk.END)
        self.entry_token.delete("1.0", tk.END)
        self.entry_name.delete(0, tk.END)
        self.status_var.set(f"Added. Queue: {len(self.queue)} item(s).")

    def _remove_selected(self):
        sel = self.listbox_queue.curselection()
        if sel:
            idx = sel[0]
            self.listbox_queue.delete(idx)
            self.queue.pop(idx)
        self.status_var.set(f"Queue: {len(self.queue)} item(s).")

    def _clear_queue(self):
        self.listbox_queue.delete(0, tk.END)
        self.queue.clear()
        self.status_var.set("Queue cleared.")

    def _start_download(self):
        if self.downloading:
            return
        if not self.queue:
            messagebox.showinfo("Queue empty", "Add at least one video to the queue.")
            return
        save_dir = (self.entry_save.get() or DEFAULT_SAVE_DIR).strip()
        if not save_dir:
            messagebox.showwarning("Save folder", "Choose a save folder.")
            return
        os.makedirs(save_dir, exist_ok=True)
        self.downloading = True
        self.btn_start.config(state=tk.DISABLED)
        threading.Thread(target=self._run_queue, args=(save_dir,), daemon=True).start()

    def _run_queue(self, save_dir: str):
        try:
            from StreamingCommunity.utils import config_manager
            from StreamingCommunity.core.downloader import DASH_Downloader
            ext = config_manager.config.get("PROCESS", "extension")  # usually mkv
        except Exception as e:
            self.root.after(0, lambda: self._log(f"Error: {e}"))
            self.root.after(0, self._download_done)
            return

        total = len(self.queue)
        for i, job in enumerate(list(self.queue)):
            self.root.after(0, lambda i=i, t=total: self.status_var.set(f"Downloading {i+1}/{t}..."))
            self.root.after(0, lambda j=job: self._log(f"Starting: {j.get('name') or '(default name)'}"))

            # Resolve output name: user name or next number
            name = job.get("name")
            if not name:
                num = next_default_number(save_dir)
                name = str(num)
            # Sanitize name for filename
            name = re.sub(r'[<>:"/\\|?*]', "_", name)[:80].strip() or "video"
            base_path = os.path.join(save_dir, name)
            # Downloader appends config extension -> base_path.mkv
            out_path_with_ext = base_path + "." + ext

            try:
                license_headers = build_license_headers(job.get("pallycon_token", ""))
                dash = DASH_Downloader(
                    mpd_url=job["mpd_url"],
                    mpd_headers=MPD_HEADERS,
                    license_url=job["license_url"],
                    license_headers=license_headers,
                    output_path=out_path_with_ext,
                    drm_preference="widevine",
                    ensure_audio=True,
                )
                result_path, need_stop = dash.start()
            except Exception as e:
                self.root.after(0, lambda e=e: self._log(f"Error: {e}"))
                continue

            if not result_path:
                self.root.after(0, lambda: self._log("Download failed or stopped."))
                continue

            # Convert to MP4 if current output is mkv
            if result_path.lower().endswith(".mkv"):
                mp4_path = result_path[:-4] + ".mp4"
                self.root.after(0, lambda: self._log("Converting to MP4..."))
                ok = mkv_to_mp4(self.ffmpeg_path, result_path, mp4_path)
                if ok:
                    result_path = mp4_path
                    self.root.after(0, lambda: self._log(f"Saved: {mp4_path}"))
                else:
                    self.root.after(0, lambda: self._log(f"Convert failed; MKV kept: {result_path}"))
            else:
                self.root.after(0, lambda: self._log(f"Saved: {result_path}"))

            # Remove this job from queue on success and refresh listbox
            if job in self.queue:
                self.queue.remove(job)
            self.root.after(0, self._refresh_listbox)

        self.root.after(0, self._download_done)

    def _refresh_listbox(self):
        self.listbox_queue.delete(0, tk.END)
        for j in self.queue:
            self.listbox_queue.insert(tk.END, j.get("name") or "(default → 1.mp4, 2.mp4, ...)")

    def _download_done(self):
        self.downloading = False
        self.btn_start.config(state=tk.NORMAL)
        self._refresh_listbox()
        self.status_var.set("Ready." if not self.queue else f"Queue: {len(self.queue)} item(s) remaining.")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = CrypterSkoolApp()
    app.run()
