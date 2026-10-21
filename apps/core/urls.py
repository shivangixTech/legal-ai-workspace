"""Core app URLs."""
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("cases/new/", views.new_case, name="new_case"),
    path("cases/<uuid:pk>/", views.case_detail, name="case_detail"),
    path("cases/<uuid:pk>/upload/", views.upload_document, name="upload_document"),
    path("cases/<uuid:pk>/chat/", views.case_chat, name="case_chat"),
    path(
        "cases/<uuid:pk>/docs/<uuid:doc_id>/reindex/",
        views.reindex_document,
        name="reindex_document",
    ),
    path("cases/<uuid:pk>/research/", views.case_research, name="case_research"),
    path(
        "cases/<uuid:pk>/chronology/",
        views.case_chronology,
        name="case_chronology",
    ),
    path(
        "cases/<uuid:pk>/reconcile/",
        views.case_reconcile,
        name="case_reconcile",
    ),
]
