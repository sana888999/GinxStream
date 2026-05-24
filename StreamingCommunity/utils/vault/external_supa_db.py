# 29.01.26

import socket
from typing import List, Optional


# External import
from rich.console import Console
from urllib.parse import urlparse
from StreamingCommunity.utils.http_client import create_client
from StreamingCommunity.utils.config import config_manager


# Variable
console = Console()

# After DNS/network failure, skip further Supabase calls this process (avoids red spam each download).
_supabase_network_unreachable: bool = False


def _is_dns_or_network_error(exc: BaseException) -> bool:
    """True if failure is DNS / no route (not HTTP 4xx/5xx)."""
    chain: list[BaseException] = []
    e: Optional[BaseException] = exc
    for _ in range(6):
        if e is None:
            break
        chain.append(e)
        e = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    for item in chain:
        if isinstance(item, socket.gaierror):
            return True
        if isinstance(item, OSError):
            en = getattr(item, "errno", None)
            if en in (11001, 11002):  # Windows: WSAHOST_NOT_FOUND / WSATRY_AGAIN
                return True
    msg = " ".join(str(c) for c in chain).lower()
    return (
        "getaddrinfo" in msg
        or "name or service not known" in msg
        or "temporary failure in name resolution" in msg
        or "network is unreachable" in msg
    )


class ExternalSupaDBVault:
    def __init__(self):
        self.base_url = f"{config_manager.remote_cdm.get('external_supa_db', 'url')}/functions/v1"
        self.headers = {
            "Content-Type": "application/json"
        }

    def _clean_license_url(self, license_url: str) -> str:
        """Extract base URL from license URL (remove query parameters and fragments)"""
        parsed = urlparse(license_url)
        base_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        return base_url.rstrip('/')

    def _post(self, endpoint: str, payload: dict) -> Optional[dict]:
        """Internal helper: POST to an endpoint, return parsed JSON or None on error."""
        global _supabase_network_unreachable
        if _supabase_network_unreachable:
            return None
        url = f"{self.base_url}/{endpoint}"
        try:
            response = create_client(headers=self.headers).post(url, json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            if _is_dns_or_network_error(e):
                _supabase_network_unreachable = True
                console.print(
                    "[yellow]Supabase vault unreachable (DNS/network). "
                    "Skipping remote key lookup for this session. "
                    "Fix internet/DNS or set [cyan]external_supa_db.enabled[/cyan] to [cyan]false[/cyan] in [cyan]Conf/remote_cdm.json[/cyan]."
                )
                return None
            console.print(f"[red]Supabase request error ({endpoint}): {e}")
            return None

    ################# SET ##################
    def set_keys(self, keys_list: List[str], drm_type: str, license_url: str, pssh: str, kid_to_label: Optional[dict] = None) -> int:
        """
        Add multiple keys to the vault in a single bulk request.

        Args:
            keys_list: List of "kid:key" strings
            drm_type: 'widevine' or 'playready'
            license_url: Full license URL (will be cleaned server-side)
            pssh: PSSH string
            kid_to_label: Optional dict mapping normalised KID → human label

        Returns:
            int: Number of keys successfully added
        """
        if not keys_list:
            return 0

        base_license_url = self._clean_license_url(license_url)
        keys_payload = []
        for key_str in keys_list:
            if ':' not in key_str:
                continue
            kid, key = key_str.split(':', 1)
            kid_clean = kid.strip()
            kid_norm = kid_clean.lower().replace('-', '')
            entry: dict = {"kid": kid_clean, "key": key.strip()}
            if kid_to_label:
                label = kid_to_label.get(kid_norm)
                if label:
                    entry["label"] = label
                    
            keys_payload.append(entry)

        if not keys_payload:
            return 0

        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
            "drm_type": drm_type,
            "keys": keys_payload,
        }

        result = self._post("set-key", payload)
        if result is None:
            return 0

        added = result.get('added', 0)
        return added

    ################# GET ##################
    def get_keys_by_pssh(self, license_url: str, pssh: str, drm_type: str) -> List[str]:
        """
        Retrieve all keys for a given license URL and PSSH (single request).

        Returns:
            List[str]: List of "kid:key" strings
        """
        base_license_url = self._clean_license_url(license_url)
        payload = {
            "license_url": base_license_url,
            "pssh": pssh,
            "drm_type": drm_type,
        }

        console.print(f"[dim]Supabase get_keys_by_pssh: pssh={pssh[:20]}…")
        result = self._post("get-keys", payload)
        if result is None:
            return []

        keys = result.get('keys', [])
        if keys:
            for k in keys:
                kid_val, key_val = k['kid_key'].split(':', 1)
                console.print(f"    - [red]{kid_val}[white]:[green]{key_val}")

        return [k['kid_key'] for k in keys]

    def get_keys_by_kids(self, license_url: Optional[str], kids: List[str], drm_type: str) -> List[str]:
        """
        Retrieve keys for one or more KIDs in a single bulk request.
        If license_url is None the search is global (all entries for that drm_type).

        Returns:
            List[str]: List of "kid:key" strings
        """
        if not kids:
            return []

        normalized_kids = [k.replace('-', '').strip().lower() for k in kids]
        base_license_url = self._clean_license_url(license_url) if license_url else None

        payload: dict = {"drm_type": drm_type, "kids": normalized_kids}
        if base_license_url:
            payload["license_url"] = base_license_url
        
        result = self._post("get-keys", payload)
        if result is None:
            return []

        keys = result.get('keys', [])
        if keys:
            console.print(f"[red]{drm_type} [cyan](KID lookup: {len(keys)} key(s) found)")
            for k in keys:
                kid_val, key_val = k['kid_key'].split(':', 1)
                console.print(f"    - [red]{kid_val}[white]:[green]{key_val}")

        return [k['kid_key'] for k in keys]

    def get_keys_by_kid(self, license_url: Optional[str], kid: str, drm_type: str) -> List[str]:
        """Convenience wrapper for a single KID lookup."""
        return self.get_keys_by_kids(license_url, [kid], drm_type)

    ################# UPDATE ##################
    def update_key_validity(self, kid: str, is_valid: bool, license_url: Optional[str] = None, drm_type: Optional[str] = None) -> bool:
        """
        Update validity status of a key.
        If license_url is provided the update is scoped to that license only, preventing accidental global corruption of keys used by other content.

        Returns:
            bool: True if updated successfully, False otherwise
        """
        payload: dict = {"kid": kid, "is_valid": is_valid}
        if license_url:
            payload["license_url"] = self._clean_license_url(license_url)
        if drm_type:
            payload["drm_type"] = drm_type.lower()
        result = self._post("update-key-validity", payload)
        return bool(result and result.get('success', False))


# Initialize (set external_supa_db.enabled to false in Conf/remote_cdm.json to disable completely)
_supa_url = (config_manager.remote_cdm.get("external_supa_db", "url", default="") or "").strip()
_supa_enabled = config_manager.remote_cdm.get_bool("external_supa_db", "enabled", default=True)
is_supa_external_db_valid = bool(_supa_url) and _supa_enabled
obj_externalSupaDbVault = ExternalSupaDBVault() if is_supa_external_db_valid else None
