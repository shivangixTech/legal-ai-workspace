"""Legal AI Workspace — main URL routing (Day 2)."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),  # login / logout / google callback
    path("", include("apps.core.urls")),         # hamara dashboard
]
