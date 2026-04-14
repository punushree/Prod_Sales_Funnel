"""
aos_project/urls.py  ← ROOT url config (same folder as settings.py)
"""
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    # path("admin/", admin.site.urls),
    # path("", include("aos_agent.urls")),   # ALL app routes, no prefix
    
    
    path("django-admin/", admin.site.urls),  # moves it out of the way
    path("", include("aos_agent.urls")),
]