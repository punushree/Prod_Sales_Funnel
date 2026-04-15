"""
settings.py — AOS Agent Platform (MySQL)
"""

import os
from pathlib import Path
from datetime import timedelta

try:
    from dotenv import load_dotenv
    # Load .env from the project root AND from aos_agent app folder
    BASE_DIR_EARLY = Path(__file__).resolve().parent.parent
    load_dotenv(BASE_DIR_EARLY / ".env")                         # project root .env
    load_dotenv(BASE_DIR_EARLY / "aos_agent" / ".env")          # app-level .env (your file)
    load_dotenv(override=False)                                   # any other .env
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "change-this-in-production-use-env-var")
DEBUG      = os.environ.get("DEBUG", "True") == "True"
ALLOWED_HOSTS = [
    "localhost",
    "127.0.0.1",
    "unlaconic-liturgical-eleni.ngrok-free.dev",
    "*",  # ← set to specific host in production
]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "aos_agent",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "aos_project.urls"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {
        "context_processors": [
            "django.template.context_processors.debug",
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ],
    },
}]

WSGI_APPLICATION = "aos_project.wsgi.application"

# ── MySQL DATABASE ────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'aos_agent_new',
        'USER': 'root',
        'PASSWORD': '1234',
        'HOST': 'localhost',
        'PORT': '3306',
    }
}

AUTH_USER_MODEL = "aos_agent.User"

AUTHENTICATION_BACKENDS = [
    "aos_agent.backends.EmailBackend",
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME":  timedelta(hours=8),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS":  True,
    "AUTH_HEADER_TYPES":      ("Bearer",),
}

CORS_ALLOWED_ORIGINS = os.environ.get(
    "CORS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:8080"
).split(",")

CORS_ALLOW_HEADERS = [
    "accept", "accept-encoding", "authorization",
    "content-type", "dnt", "origin",
    "user-agent", "x-csrftoken", "x-requested-with",
]

CELERY_BROKER_URL         = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND     = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT     = ["json"]
CELERY_TASK_SERIALIZER    = "json"
CELERY_RESULT_SERIALIZER  = "json"
CELERY_TIMEZONE           = "Asia/Kolkata"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# ── Bolna settings ──────────────────────────────────────────────
# Keys must match exactly what's in your .env file (no dots, no quotes)
BOLNA_API_KEY          = os.environ.get("BOLNA_API_KEY", "")
BOLNA_SERVICE_AGENT_ID = os.environ.get("BOLNA_SERVICE_AGENT_ID", "")
BOLNA_BASE_URL         = os.environ.get("BOLNA_BASE_URL", "https://api.bolna.dev")
GROQ_API_KEY           = os.environ.get("GROQ_API_KEY", "")


STATIC_URL  = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL   = "/media/"
MEDIA_ROOT  = BASE_DIR / "media"

LANGUAGE_CODE = "en-us"
TIME_ZONE     = "Asia/Kolkata"
USE_I18N      = True
USE_TZ        = True

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL          = "/login/"
LOGIN_REDIRECT_URL = "/"
# LOGIN_REDIRECT_URL = "/dashboard/"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {"format": "{levelname} {asctime} {module} {message}", "style": "{"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "verbose"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "aos_agent": {"handlers": ["console"], "level": "DEBUG", "propagate": False},
    },
}

# ── Celery Beat: periodic tasks ──────────────────────────────────────────────
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # Fix calls stuck in 'in_progress' when Bolna webhook never fired
    "fix-stale-calls": {
        "task": "aos_agent.tasks.fix_stale_in_progress_calls",
        "schedule": crontab(minute="*/10"),   # runs every 10 minutes
    },
}