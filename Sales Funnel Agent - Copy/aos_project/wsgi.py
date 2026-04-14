"""
WSGI config for aos_project.

It exposes the WSGI callable as a module-level variable named ``application``.
Used by gunicorn / uWSGI for production deployment.

    gunicorn aos_project.wsgi:application --bind 0.0.0.0:8000
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aos_project.settings")

application = get_wsgi_application()