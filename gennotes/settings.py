"""Production-ish base settings.

Localhost-only by design. Dev tweaks live in ``settings_dev.py`` and only
flip ``DEBUG`` and a couple of related toggles.
"""
from __future__ import annotations

import os
from pathlib import Path

from gennotes.data_dir import db_path, ensure_layout, media_dir

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ensure_layout()

# --- Security / hosting ------------------------------------------------------

# Required by Django; for a single-user localhost app we generate a per-install
# key on first run and persist it under DATA_DIR. The user can rotate the file
# to invalidate sessions.
_secret_key_file = DATA_DIR / "secret_key"
if not _secret_key_file.exists():
    import secrets

    _secret_key_file.write_text(secrets.token_urlsafe(64))
    _secret_key_file.chmod(0o600)
SECRET_KEY = _secret_key_file.read_text().strip()

DEBUG = False
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

# Hard guard: refuse to bind to a non-loopback host unless explicitly opted in.
# Stage 3 will tighten this further; for now we just fail loud if a deployer
# tries to widen ALLOWED_HOSTS without the env flag.
if os.environ.get("GENNOTES_ALLOW_LAN") != "1":
    _bad = [h for h in ALLOWED_HOSTS if h not in {"127.0.0.1", "localhost"}]
    if _bad:
        raise RuntimeError(
            f"ALLOWED_HOSTS contains non-loopback entries {_bad!r}; set "
            "GENNOTES_ALLOW_LAN=1 to allow this."
        )

# Localhost-only security headers. HTTPS-specific flags (HSTS, SECURE_SSL_*)
# are intentionally off — we don't terminate TLS on 127.0.0.1.
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Strict"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"

# --- Auth --------------------------------------------------------------------

# Argon2id is the chosen hash. Keep PBKDF2 in the list as a fallback so
# existing sessions (if any) can be upgraded silently on next login.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

LOGIN_URL = "/login/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/login/"

# Idle session timeout — see `recordings.middleware.IdleTimeoutMiddleware`.
GENNOTES_IDLE_TIMEOUT_SECONDS = int(os.environ.get("GENNOTES_IDLE_TIMEOUT", 30 * 60))
SESSION_COOKIE_AGE = GENNOTES_IDLE_TIMEOUT_SECONDS
SESSION_SAVE_EVERY_REQUEST = True

# --- Content-Security-Policy -------------------------------------------------

# Hard-coded localhost-only CSP. Strict default-src 'self' with no allowed
# external origins. Scripts must come from the package (no inline). Inline
# style attributes are allowed (style-src 'unsafe-inline') as a pragmatic
# trade-off: CSS-based exfiltration on a single-user localhost app is a
# theoretical risk, and keeping it open avoids per-template style-class
# churn. Scripts are the load-bearing protection.
GENNOTES_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "media-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# --- Apps --------------------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "recordings",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "recordings.middleware.CSPMiddleware",
    "recordings.middleware.IdleTimeoutMiddleware",
    "recordings.middleware.LoginRequiredMiddleware",
]

ROOT_URLCONF = "gennotes.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "gennotes" / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "gennotes.wsgi.application"

# --- Database (SQLite at DATA_DIR) ------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": str(db_path()),
        "OPTIONS": {"timeout": 30},
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Uploads -----------------------------------------------------------------

# Always stream uploads to a temp file on disk; never buffer in memory.
FILE_UPLOAD_MAX_MEMORY_SIZE = 0
# 2 GiB cap on the request body. Hard upload-size enforcement also happens at
# the view layer via `recordings.ingest.max_upload_bytes()`.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 100
FILE_UPLOAD_PERMISSIONS = 0o600

# --- Static / media ----------------------------------------------------------

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "gennotes" / "static"]
# WhiteNoise serves the in-package /static dir directly in DEBUG=False, so
# `collectstatic` is only needed for one-folder builds.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}
WHITENOISE_USE_FINDERS = True

# Media files are user-private — never served by URL. All downloads go
# through view handlers that enforce auth and stream from disk.
MEDIA_ROOT = str(media_dir())
MEDIA_URL = ""

# --- I18N --------------------------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Logging -----------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {"class": "logging.StreamHandler"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
