"""Lightweight user-preference store for the web GUI.

Preferences live in ``Conf/user_prefs.json`` so both the GUI and the core
downloader (``StreamingCommunity.source.N_m3u8.wrapper``) can read them.

Schema::

    {
      "use_original_titles": true,        # show/download with original (English) titles
      "additional_dubs": ["hin", "urd"]   # extra audio languages to pull when available
    }

Supported dub language codes match what N_m3u8DL-RE accepts. Built-in shortcuts:

    hin -> Hindi, urd -> Urdu, pan -> Punjabi,
    spa -> Spanish, fra -> French, ger -> German,
    ita -> Italian, jpn -> Japanese, kor -> Korean,
    por -> Portuguese, ara -> Arabic, rus -> Russian,
    zho/chi -> Chinese, tur -> Turkish
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List


_PREFS_LOCK = threading.Lock()


def _project_root() -> Path:
    """Return the StreamingCommunity project root (parent of ``Conf/``)."""
    # This file lives at <root>/GUI/searchapp/user_prefs.py
    return Path(__file__).resolve().parents[2]


def _prefs_path() -> Path:
    override = os.environ.get("STREAMINGCOMMUNITY_USER_PREFS")
    if override:
        return Path(override)
    return _project_root() / "Conf" / "user_prefs.json"


DUB_LANGUAGE_OPTIONS: List[Dict[str, str]] = [
    {"code": "hin", "label": "Hindi", "tokens": "hin|Hin|hi"},
    {"code": "urd", "label": "Urdu", "tokens": "urd|Urd|ur"},
    {"code": "pan", "label": "Punjabi", "tokens": "pan|Pan|pa|pnb|pun"},
    {"code": "spa", "label": "Spanish", "tokens": "spa|Spa|es|esp"},
    {"code": "fra", "label": "French", "tokens": "fra|Fra|fr|fre"},
    {"code": "ger", "label": "German", "tokens": "ger|Ger|de|deu"},
    {"code": "ita", "label": "Italian", "tokens": "ita|Ita|it"},
    {"code": "jpn", "label": "Japanese", "tokens": "jpn|Jpn|ja"},
    {"code": "kor", "label": "Korean", "tokens": "kor|Kor|ko"},
    {"code": "por", "label": "Portuguese", "tokens": "por|Por|pt"},
    {"code": "ara", "label": "Arabic", "tokens": "ara|Ara|ar"},
    {"code": "rus", "label": "Russian", "tokens": "rus|Rus|ru"},
    {"code": "zho", "label": "Chinese", "tokens": "zho|chi|zh|cmn"},
    {"code": "tur", "label": "Turkish", "tokens": "tur|Tur|tr"},
]

_DUB_TOKENS: Dict[str, str] = {opt["code"]: opt["tokens"] for opt in DUB_LANGUAGE_OPTIONS}


DEFAULT_PREFS: Dict[str, Any] = {
    "use_original_titles": True,
    "additional_dubs": ["hin"],
}


def _normalize(prefs: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(DEFAULT_PREFS)
    if isinstance(prefs, dict):
        if "use_original_titles" in prefs:
            merged["use_original_titles"] = bool(prefs["use_original_titles"])
        if "additional_dubs" in prefs and isinstance(prefs["additional_dubs"], list):
            merged["additional_dubs"] = [
                str(code).lower() for code in prefs["additional_dubs"]
                if str(code).lower() in _DUB_TOKENS
            ]
    return merged


def load_prefs() -> Dict[str, Any]:
    """Load the user preferences (creating the file with defaults if missing)."""
    path = _prefs_path()
    with _PREFS_LOCK:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(DEFAULT_PREFS, indent=2), encoding="utf-8")
            return dict(DEFAULT_PREFS)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return dict(DEFAULT_PREFS)
    return _normalize(raw)


def save_prefs(prefs: Dict[str, Any]) -> Dict[str, Any]:
    """Persist *prefs* (after normalization) and return the normalized result."""
    normalized = _normalize(prefs)
    path = _prefs_path()
    with _PREFS_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, indent=2), encoding="utf-8")
    return normalized


def dub_tokens_for(codes: List[str]) -> str:
    """Return the pipe-joined N_m3u8DL-RE language token set for *codes*."""
    tokens: List[str] = []
    seen: set[str] = set()
    for code in codes:
        token_group = _DUB_TOKENS.get(str(code).lower())
        if not token_group:
            continue
        for token in token_group.split("|"):
            if token and token not in seen:
                seen.add(token)
                tokens.append(token)
    return "|".join(tokens)
