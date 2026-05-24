# 18.07.25

import os
from typing import Optional


# External library
from rich.console import Console


# Internal utilities
from .binary_paths import binary_paths


# Variable
console = Console()


def workspace_root() -> str:
    """Repository root (StreamingCommunity-main): parent of the `StreamingCommunity` package directory."""
    setup_dir = os.path.dirname(os.path.abspath(__file__))
    streamingcommunity_pkg = os.path.dirname(setup_dir)
    return os.path.abspath(os.path.dirname(streamingcommunity_pkg))


def _is_nonempty_file(path: str) -> bool:
    try:
        return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def _first_matching_file_in_dir(directory: str, suffix: str) -> Optional[str]:
    if not os.path.isdir(directory):
        return None
    try:
        for name in sorted(os.listdir(directory)):
            if name.lower().endswith(suffix):
                path = os.path.join(directory, name)
                if _is_nonempty_file(path):
                    return os.path.abspath(path)
    except OSError:
        return None
    return None


def _looks_like_repo_root(root: str) -> bool:
    """Avoid treating site-packages parents as the workspace when the package is installed flat."""
    if not root or not os.path.isdir(root):
        return False
    if os.path.isfile(os.path.join(root, "Conf", "remote_cdm.json")):
        return True
    if os.path.isdir(os.path.join(root, "StreamingCommunity")) and os.path.isdir(os.path.join(root, "Conf")):
        return True
    return False


class DeviceSearcher:
    def __init__(self):
        self.base_dir = binary_paths.ensure_binary_directory()

    def _check_existing(self, ext: str) -> Optional[str]:
        """Check for existing files with given extension in binary directory."""
        try:
            for file in os.listdir(self.base_dir):
                if file.lower().endswith(ext):
                    path = os.path.join(self.base_dir, file)
                    return path

            return None

        except Exception as e:
            console.print(f"[red]Error checking existing {ext} files: {e}")
            return None

    def _find_recursively(self, ext: str = None, start_dir: str = ".", filename: str = None) -> Optional[str]:
        """
        Find file recursively by extension or exact filename starting from start_dir.
        If filename is provided, search for that filename. Otherwise, search by extension.
        """
        try:
            for root, dirs, files in os.walk(start_dir):
                for file in files:
                    if filename:
                        if file == filename:
                            path = os.path.join(root, file)
                            return path

                    elif ext:
                        if file.lower().endswith(ext):
                            path = os.path.join(root, file)
                            return path

            return None
        except Exception as e:
            console.print(f"[red]Error during recursive search for filename {filename}: {e}")
            return None

    def search(self, ext: str = None, filename: str = None) -> Optional[str]:
        """
        Search for file with given extension or exact filename in binary directory or recursively.
        If filename is provided, search for that filename. Otherwise, search by extension.
        """
        if filename:
            try:
                target_path = os.path.join(self.base_dir, filename)
                if os.path.exists(target_path) and os.path.getsize(target_path) > 0:
                    return target_path

            except Exception as e:
                console.print(f"[red]Error checking for existing file {filename}: {e}")
                return None

            return self._find_recursively(filename=filename)

        else:
            path = self._check_existing(ext)
            if path:
                return path
            return self._find_recursively(ext=ext)


def check_device_wvd_path() -> Optional[str]:
    """
    Resolve path to a Widevine device file.

    Order: STREAMINGCOMMUNITY_WVD_PATH -> project binary/ or binaries/ -> device.wvd in repo or Conf/
    -> OS-wide binary folder (e.g. C:\\binary) -> legacy recursive search from cwd.
    """
    env = (os.environ.get("STREAMINGCOMMUNITY_WVD_PATH") or "").strip()
    if env and _is_nonempty_file(env):
        return os.path.abspath(env)

    root = workspace_root()
    if _looks_like_repo_root(root):
        for sub in ("binary", "binaries"):
            hit = _first_matching_file_in_dir(os.path.join(root, sub), ".wvd")
            if hit:
                return hit
        for rel in ("device.wvd", os.path.join("Conf", "device.wvd")):
            p = os.path.join(root, rel)
            if _is_nonempty_file(p):
                return os.path.abspath(p)

    try:
        searcher = DeviceSearcher()
        return searcher.search(".wvd")
    except Exception:
        return None


def check_device_prd_path() -> Optional[str]:
    """Same resolution order as Widevine, for PlayReady `.prd` files."""
    env = (os.environ.get("STREAMINGCOMMUNITY_PRD_PATH") or "").strip()
    if env and _is_nonempty_file(env):
        return os.path.abspath(env)

    root = workspace_root()
    if _looks_like_repo_root(root):
        for sub in ("binary", "binaries"):
            hit = _first_matching_file_in_dir(os.path.join(root, sub), ".prd")
            if hit:
                return hit
        for rel in ("device.prd", os.path.join("Conf", "device.prd")):
            p = os.path.join(root, rel)
            if _is_nonempty_file(p):
                return os.path.abspath(p)

    try:
        searcher = DeviceSearcher()
        return searcher.search(".prd")
    except Exception:
        return None
