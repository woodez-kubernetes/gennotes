from __future__ import annotations

from django.urls import path

from . import auth_views, views

urlpatterns = [
    # Auth
    path("setup/", auth_views.setup_view, name="setup"),
    path("login/", auth_views.login_view, name="login"),
    path("logout/", auth_views.logout_view, name="logout"),

    # Recordings
    path("", views.recording_list, name="recording-list"),
    path("upload/", views.upload, name="upload"),
    path("doctor/", views.doctor, name="doctor"),
    path("r/<int:pk>/", views.recording_detail, name="recording-detail"),
    path("r/<int:pk>/status/", views.recording_status, name="recording-status"),
    path("r/<int:pk>/retry/", views.recording_retry, name="recording-retry"),
    path(
        "r/<int:pk>/resummarize/",
        views.recording_resummarize,
        name="recording-resummarize",
    ),
    path("r/<int:pk>/delete/", views.recording_delete, name="recording-delete"),
    path("r/<int:pk>/export/", views.recording_export, name="recording-export"),

    # Audit
    path("audit/", views.audit_log, name="audit-log"),
    path("audit/truncate/", views.audit_log_truncate, name="audit-log-truncate"),
]
