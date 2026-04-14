"""
ASGI config for aos_project.

It exposes the ASGI callable as a module-level variable named ``application``.
Used by Daphne / Uvicorn for async / WebSocket support.

    uvicorn aos_project.asgi:application --host 0.0.0.0 --port 8000
"""

import os
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aos_project.settings")

application = get_asgi_application()