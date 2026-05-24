# 29.01.26

import base64

# Unwrap Pallycon-style protobuf wrapper and return all candidate license payloads to try
def _read_varint(raw, pos):
    length, shift = 0, 0
    while pos < len(raw):
        b = raw[pos]
        pos += 1
        length |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return pos, length
        shift += 7
        if shift > 21:
            return pos, None
    return pos, None


def _pallycon_license_candidates(raw: bytes):
    """Yield candidate byte strings to pass to cdm.parse_license (Pallycon wraps in protobuf)."""
    if not raw or len(raw) < 2:
        return
    # Pattern 1: 0x08 0x02 0x12 <varint> <payload> (field 1=2, field 2=length-delimited)
    if len(raw) >= 5 and raw[:3] == b'\x08\x02\x12':
        pos, length = _read_varint(raw, 3)
        if length is not None and pos + length <= len(raw):
            inner = raw[pos:pos + length]
            yield inner
            if len(inner) >= 2 and inner[0] == 0x0A:
                pos2, length2 = _read_varint(inner, 1)
                if length2 is not None and pos2 + length2 <= len(inner):
                    yield inner[pos2:pos2 + length2]
            return
    # Pattern 2: 0x0a <varint> <payload> (field 1 length-delimited) at start
    if raw[0] == 0x0A:
        pos, length = _read_varint(raw, 1)
        if length is not None and pos + length <= len(raw):
            yield raw[pos:pos + length]
    # Pattern 3: 0x12 <varint> <payload> (field 2 length-delimited) at start
    if raw[0] == 0x12:
        pos, length = _read_varint(raw, 1)
        if length is not None and pos + length <= len(raw):
            yield raw[pos:pos + length]
    # Pattern 4: scan for 0x08 0x02 0x12 anywhere (wrap might have leading bytes)
    idx = raw.find(b'\x08\x02\x12')
    if idx >= 0 and idx + 5 <= len(raw):
        pos, length = _read_varint(raw, idx + 3)
        if length is not None and pos + length <= len(raw):
            yield raw[pos:pos + length]


# External libraries
from rich.console import Console

try:
    import requests as _requests
except ImportError:
    _requests = None
from pywidevine.cdm import Cdm
from pywidevine.device import Device
from pywidevine.device import DeviceTypes
from pywidevine.remotecdm import RemoteCdm
from pywidevine.pssh import PSSH


# Internal utilities
from StreamingCommunity.setup import get_info_wvd
from StreamingCommunity.utils.http_client import create_client_curl
from StreamingCommunity.source.utils.object import KeysManager


# Variable
console = Console()


def _print_remote_cdm_network_hint(exc: Exception) -> None:
    """Explain common remote CDM failures (timeouts, DNS, firewall)."""
    if _requests is None:
        return
    if not isinstance(
        exc,
        (
            _requests.exceptions.ConnectTimeout,
            _requests.exceptions.ConnectionError,
            _requests.exceptions.ReadTimeout,
            _requests.exceptions.Timeout,
        ),
    ):
        # urllib3 / httpx sometimes wrap the same problem
        msg = str(exc).lower()
        if "timeout" not in msg and "connection" not in msg and "max retries" not in msg:
            return
    console.print(
        "[yellow]"
        "Remote Widevine CDM could not be reached (network timeout or blocked). "
        "Try: different network/VPN, disable aggressive firewall/ad-block for the CDM host, "
        "or point [cyan]remote_cdm.widevine.host[/cyan] in [cyan]Conf/remote_cdm.json[/cyan] "
        "to your own pywidevine-serve instance. "
        "If you use a local [cyan]device.wvd[/cyan] in the binaries folder, the app prefers it over remote CDM."
        "[/yellow]"
    )
    try:
        from StreamingCommunity.setup.binary_paths import binary_paths

        bdir = binary_paths.get_binary_directory()
        console.print(f"[dim]Local Widevine file location: [cyan]{bdir}[/cyan] — place [cyan]device.wvd[/cyan] there, then restart the download.[/dim]")
    except Exception:
        pass


def _is_session_expired_error(exc: Exception) -> bool:
    """True if the remote CDM error indicates session invalid/expired (retry with new session)."""
    msg = str(exc).lower()
    return "invalid session" in msg or "expired" in msg or "[400]" in msg


def get_widevine_keys(pssh_list: list[dict], license_url: str, cdm_device_path: str = None, cdm_remote_api: list[str] = None, headers: dict = None, key: str = None):
    """
    Extract Widevine CONTENT keys (KID/KEY) from a license.

    Args:
        - pssh_list (list[dict]): List of dicts {'pssh': ..., 'kid': ..., 'type': ...}
        - license_url (str): Widevine license URL.
        - cdm_device_path (str): Path to local CDM file (device.wvd). Optional if using remote.
        - cdm_remote_api (list[str]): Remote CDM API config. Optional if using local device.
        - headers (dict): Optional HTTP headers for the license request (from fetch).
        - key (str): Optional raw license data to bypass HTTP request.

    Returns:
        list: List of strings "KID:KEY" (only CONTENT keys) or None if error.
    """
    # Handle pre-existing key
    if key:
        k_split = key.split(':')
        if len(k_split) == 2:
            return KeysManager([f"{k_split[0].replace('-', '').strip()}:{k_split[1].replace('-', '').strip()}"])
        return None

    # Check if we have either local or remote CDM
    if cdm_device_path is None and cdm_remote_api is None:
        console.print("[red]Error: Must provide either cdm_device_path or cdm_remote_api.")
        return None
    
    return _get_widevine_keys(pssh_list, license_url, cdm_device_path, cdm_remote_api, headers)


def _get_widevine_keys(pssh_list: list[dict], license_url: str, cdm_device_path: str, cdm_remote_api: list[str], headers: dict = None):
    """Extract Widevine keys using local or remote CDM device."""
    device = None
    cdm = None
    
    # Create a set of all expected KIDs (normalized)
    expected_kids = set()
    for item in pssh_list:
        kid = str(item.get('kid', '')).replace('-', '').lower().strip()
        if kid and kid != 'n/a':
            expected_kids.add(kid)
    
    # Initialize device
    if cdm_device_path is not None:
        console.print(get_info_wvd(cdm_device_path))
        try:
            device = Device.load(cdm_device_path)
            cdm = Cdm.from_device(device)

        except Exception as e:
            console.print(f"[red]Error loading local CDM device: {e}")
            return None
    else:
        console.print("[cyan]Using remote CDM.")
        try:
            dt = cdm_remote_api.get('device_type')
            if dt in ('ANDROID', DeviceTypes.ANDROID):
                cdm_remote_api['device_type'] = DeviceTypes.ANDROID
            elif dt in ('CHROME', DeviceTypes.CHROME):
                cdm_remote_api['device_type'] = DeviceTypes.CHROME
            else:
                console.print(f"[red]Unsupported remote CDM device type: {dt}")
                return None
            from StreamingCommunity.core.drm.remote_cdm_http import remote_cdm_init_timeouts

            with remote_cdm_init_timeouts():
                cdm = RemoteCdm(**cdm_remote_api)
        except Exception as e:
            console.print(f"[red]Error initializing remote CDM: {e}")
            _print_remote_cdm_network_hint(e)
            return None

    use_remote = cdm_device_path is None
    all_content_keys = []
    extracted_kids = set()
    session_id = None  # for local CDM: one session for all; for remote: new session per PSSH

    if not use_remote:
        session_id = cdm.open()

    try:
        for i, item in enumerate(pssh_list):
            pssh = item['pssh']
            kid_info = str(item.get('kid', 'N/A')).replace('-', '').lower().strip()
            type_info = item.get('type', 'unknown')
            console.print(f"[red]{type_info} [cyan](PSSH: [yellow]{pssh[:30]}...[cyan] KID: [red]{kid_info})")

            if use_remote:
                session_id = cdm.open()

            try:
                # Create license challenge (retry once with new session if remote CDM says session expired)
                challenge = None
                for attempt in range(2):
                    try:
                        challenge = cdm.get_license_challenge(session_id, PSSH(pssh))
                        break
                    except Exception as e:
                        if use_remote and attempt == 0 and _is_session_expired_error(e):
                            try:
                                cdm.close(session_id)
                            except Exception:
                                pass
                            console.print("[yellow]Remote CDM session expired, opening new session and retrying...[/yellow]")
                            session_id = cdm.open()
                            continue
                        console.print(f"[red]Error creating challenge for PSSH {pssh[:30]}...: {e}")
                        break
                if challenge is None:
                    continue
            except Exception as e:
                console.print(f"[red]Error creating challenge for PSSH {pssh[:30]}...: {e}")
                continue
            
            # Prepare headers (use original headers from fetch)
            req_headers = headers.copy() if headers else {}
            if 'Content-Type' not in req_headers:
                req_headers['Content-Type'] = 'application/octet-stream'

            if license_url is None:
                console.print("\n[red]License URL is None.")
                continue

            # Make license request
            try:
                response = create_client_curl(headers=req_headers).post(license_url, data=challenge)
            except Exception as e:
                console.print(f"[red]License request error: {e}")
                continue

            if response.status_code != 200:
                console.print(f"[red]License error: {response.status_code}\nResponse: {response.content.decode('latin-1')[:200]}\nUrl: {license_url}\nHeaders: {req_headers}")
                continue

            # Parse license response (server may use 'license', 'data', 'response', etc.)
            content_type = response.headers.get('content-type', '').lower()
            license_bytes = None
            raw_content = response.content

            if 'application/json' in content_type:
                try:
                    data = response.json()
                    # Try common JSON keys used by license servers
                    for key in ('license', 'data', 'response', 'licenseData', 'body', 'message', 'payload', 'licenseResponse'):
                        val = data.get(key) if isinstance(data, dict) else None
                        if val is None:
                            continue
                        if isinstance(val, dict):
                            for k in ('license', 'data', 'response', 'body'):
                                if k in val and val[k]:
                                    val = val[k]
                                    break
                        if isinstance(val, str):
                            try:
                                license_bytes = base64.b64decode(val)
                                break
                            except Exception:
                                pass
                        elif isinstance(val, (bytes, bytearray)):
                            license_bytes = bytes(val)
                            break
                    if license_bytes is None and isinstance(data, dict):
                        # Some servers wrap raw base64 in a different structure; try raw body as base64
                        try:
                            license_bytes = base64.b64decode(raw_content)
                        except Exception:
                            pass
                    if license_bytes is None:
                        keys_preview = ", ".join(data.keys())[:80] if isinstance(data, dict) else type(data).__name__
                        console.print("[red]'license' field not found in JSON response. Keys present: " + keys_preview)
                        continue
                except Exception as e:
                    console.print(f"[red]Error parsing JSON license response: {e}")
                    license_bytes = None
            if license_bytes is None:
                # Non-JSON response: use raw body (binary or base64)
                license_bytes = raw_content
                if license_bytes and not (license_bytes[:4] in (b'\x00\x00\x00', b'\x08\x01\x12') or license_bytes[0:1] == b'{'):
                    try:
                        license_bytes = base64.b64decode(license_bytes)
                    except Exception:
                        pass

            if not license_bytes:
                console.print("[red]License data is empty.")
                continue

            # Parse license (try raw bytes first; then Pallycon-style protobuf wrap; then JSON)
            license_parsed = False
            try:
                cdm.parse_license(session_id, license_bytes)
                license_parsed = True
            except Exception as e:
                # Fallback 1: Pallycon wraps Widevine license in protobuf — unwrap from the same bytes we tried (decoded binary), not raw_content
                for candidate in _pallycon_license_candidates(license_bytes or b""):
                    try:
                        cdm.parse_license(session_id, candidate)
                        license_parsed = True
                        break
                    except Exception:
                        pass
                if not license_parsed and raw_content and raw_content is not license_bytes:
                    for candidate in _pallycon_license_candidates(raw_content):
                        try:
                            cdm.parse_license(session_id, candidate)
                            license_parsed = True
                            break
                        except Exception:
                            pass
                # Fallback 2: response might be JSON with license in a key
                if not license_parsed and raw_content and raw_content[0:1] == b'{':
                    try:
                        import json as _json
                        data = _json.loads(raw_content.decode('utf-8', errors='ignore'))
                        for key in ('license', 'data', 'response', 'licenseData', 'body', 'message', 'payload'):
                            val = data.get(key) if isinstance(data, dict) else None
                            if isinstance(val, str):
                                try:
                                    license_bytes = base64.b64decode(val)
                                    cdm.parse_license(session_id, license_bytes)
                                    license_parsed = True
                                    break
                                except Exception:
                                    pass
                    except Exception:
                        pass
                if not license_parsed:
                    console.print(f"[red]Error parsing license: {e}")
                    if raw_content and raw_content[0:1] == b'{':
                        try:
                            import json as _json
                            err_data = _json.loads(raw_content.decode('utf-8', errors='ignore'))
                            if isinstance(err_data, dict) and ('errorCode' in err_data or 'error' in err_data or 'message' in err_data):
                                msg = err_data.get('message') or err_data.get('error') or str(err_data.get('errorCode', ''))
                                console.print("[yellow]License server returned an error (likely wrong or expired pallycon-customdata-v2). Play this video in the browser, copy a fresh token from the license request, and try again.[/yellow]")
                                console.print(f"[dim]Server said: {msg}[/dim]")
                        except Exception:
                            pass
                    continue

            # Extract CONTENT keys
            try:
                for key_obj in cdm.get_keys(session_id):
                    if key_obj.type != 'CONTENT':
                        continue

                    # Get KID and normalize
                    kid = key_obj.kid.hex.lower().strip()
                    formatted_key = f"{kid}:{key_obj.key.hex()}"
                    if formatted_key not in all_content_keys:
                        all_content_keys.append(formatted_key)
                        extracted_kids.add(kid)

            except Exception as e:
                console.print(f"[red]Error extracting keys: {e}")
                continue
            finally:
                if use_remote and session_id is not None:
                    try:
                        cdm.close(session_id)
                    except Exception:
                        pass

        if all_content_keys:
            for i, k in enumerate(all_content_keys):
                kid, key_val = k.split(':')
                console.print(f"    - [red]{kid}[white]:[green]{key_val}")
        else:
            console.print("[yellow]No keys extracted")
        
        return KeysManager(all_content_keys) if all_content_keys else None
    
    except Exception as e:
        console.print(f"[red]Unexpected error during key extraction: {e}")
        return None
    
    finally:
        if not use_remote and session_id is not None:
            try:
                cdm.close(session_id)
            except Exception:
                pass