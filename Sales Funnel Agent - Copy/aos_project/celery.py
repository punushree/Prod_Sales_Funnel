"""
celery.py — Celery Configuration for AOS Agent
────────────────────────────────────────────────
Save this as:  aos_project/celery.py
(same folder as settings.py)

Then in aos_project/__init__.py add:
    from .celery import app as celery_app
    __all__ = ("celery_app",)

Start worker with:
    celery -A aos_project worker --loglevel=info

Start beat scheduler (for periodic tasks) with:
    celery -A aos_project beat --loglevel=info
"""

import os
from project.celery import Celery

# Set default Django settings
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aos_project.settings")

app = Celery("aos_project")

# Load config from Django settings — all keys prefixed with CELERY_
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks from all INSTALLED_APPS
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    print(f"Request: {self.request!r}")