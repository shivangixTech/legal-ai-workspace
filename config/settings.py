"""
Legal AI Workspace — Django settings.

Notes:
- All secrets come from `.env` — nothing is hardcoded in this file.
- Database = Supabase Postgres (via DATABASE_URL). Business tables
  (cases, documents, ...) are Supabase-owned and mapped with
  `managed = False`, so `migrate` never touches them — it only creates
  Django's own auth/allauth tables.
"""

import os
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")  # .env must sit next to manage.py

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-unsafe-fallback")

# Production switches (Day 12 - Render deploy). Locally these env vars
# are unset, so behaviour stays exactly as before: DEBUG on, localhost
# only. On Render they are set in the dashboard (see render.yaml).
DEBUG = os.environ.get("DJANGO_DEBUG", "True") == "True"
ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost"
    ).split(",")
    if h.strip()
]
# Render tells the service its public hostname - trust it automatically.
_render_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
if _render_host and _render_host not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_render_host)
# Render terminates HTTPS at its proxy; without this, Django thinks
# requests are http and the Google OAuth callback URL comes out wrong.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [
    o.strip()
    for o in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if o.strip()
]
if _render_host:
    CSRF_TRUSTED_ORIGINS.append(f"https://{_render_host}")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",  # required by allauth
    # Google login
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    # project apps
    "apps.core",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # serve static files in production (Day 12)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",  # required by allauth
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",  # required by allauth
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# Supabase Postgres — single shared data plane.
# Parsed with the stdlib so no extra package install is needed.
_db = urlparse(os.environ.get("DATABASE_URL", ""))
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": (_db.path or "").lstrip("/"),
        "USER": unquote(_db.username or ""),
        "PASSWORD": unquote(_db.password or ""),  # handles encoded chars like %40
        "HOST": _db.hostname or "",
        "PORT": _db.port or 5432,
        # Reuse DB connections for 10 minutes instead of reconnecting on
        # every request — much faster page loads on slow/flaky networks.
        "CONN_MAX_AGE": 600,
        # Verify a reused connection is still alive (safe with the pooler).
        "CONN_HEALTH_CHECKS": True,
    }
}

AUTH_PASSWORD_VALIDATORS = []  # keep it simple for local dev

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"  # collectstatic target (production)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- django-allauth: Google login ----
SITE_ID = 1
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
LOGIN_REDIRECT_URL = "/"          # where to go after login
LOGOUT_REDIRECT_URL = "/"
ACCOUNT_EMAIL_VERIFICATION = "none"  # skip email verification in local dev
SOCIALACCOUNT_LOGIN_ON_GET = True  # skip the "Continue" page, go straight to Google

# Google OAuth credentials come from .env — no SocialApp needed in admin
SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "key": "",
        },
        "SCOPE": ["profile", "email"],
        "AUTH_PARAMS": {"access_type": "online"},
    }
}
