# 06-06-25 By @FrancescoGrazioso -> "https://github.com/FrancescoGrazioso"


import os
import sys
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str = "") -> list[str]:
    raw_value = os.environ.get(name, default)
    normalized = raw_value.replace(",", " ")
    return [item.strip() for item in normalized.split() if item.strip()]


BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# Windows-only safety net: rich's LegacyWindowsTerm path encodes output via
# cp1252 and crashes on characters like U+2192 ("→"). Force UTF-8 stdout and
# disable rich's legacy-windows branch globally so background download threads
# can safely print Unicode through the StreamingCommunity console helpers.
if sys.platform.startswith("win"):
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("PYTHONLEGACYWINDOWSSTDIO", "0")

    for _stream_name in ("stdout", "stderr"):
        _stream = getattr(sys, _stream_name, None)
        if _stream is not None and hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    try:
        from rich import console as _rich_console

        if not getattr(_rich_console.Console, "_legacy_windows_patched", False):
            _orig_console_init = _rich_console.Console.__init__

            def _patched_console_init(self, *args, **kwargs):
                kwargs.setdefault("legacy_windows", False)
                return _orig_console_init(self, *args, **kwargs)

            _rich_console.Console.__init__ = _patched_console_init  # type: ignore[assignment]
            _rich_console.Console._legacy_windows_patched = True  # type: ignore[attr-defined]
    except Exception:
        pass

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key")
DEBUG = _env_flag("DJANGO_DEBUG", True)
ALLOWED_HOSTS = _env_list("ALLOWED_HOSTS", "*") or ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "searchapp.apps.SearchappConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "webgui.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "searchapp.context_processors.version_context",
                "searchapp.context_processors.active_downloads_context",
            ],
        },
    },
]

WSGI_APPLICATION = "webgui.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.environ.get("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"
STATICFILES_DIRS = [BASE_DIR / "assets"] if (BASE_DIR / "assets").exists() else []

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CSRF_TRUSTED_ORIGINS = _env_list("CSRF_TRUSTED_ORIGINS")
USE_X_FORWARDED_HOST = _env_flag("USE_X_FORWARDED_HOST", False)
CSRF_COOKIE_SECURE = _env_flag("CSRF_COOKIE_SECURE", False)
SESSION_COOKIE_SECURE = _env_flag("SESSION_COOKIE_SECURE", False)

if _env_flag("SECURE_PROXY_SSL_HEADER_ENABLED", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
