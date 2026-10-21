"""Supabase Storage helper — private PDF uploads for cases.

Design:
  * One private bucket: `case-documents` (created once in the dashboard).
  * Key layout: `<org_id>/<case_id>/<uuid>_<filename>` — scoped per
    organization and case, unique per upload, no collisions.
  * Server-side upload with the SERVICE_ROLE key (never exposed to the
    browser). Files stay private; only our backend reads them.
"""
import os
import uuid

from django.core.exceptions import ImproperlyConfigured

BUCKET = "case-documents"


def _client():
    """Build a Supabase client. Raises a clear error if keys are missing."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url or not key or "your_" in key:
        raise ImproperlyConfigured(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY are not set in .env — "
            "copy the service_role key from Supabase dashboard → Settings → API."
        )
    from supabase import create_client

    return create_client(url, key)


def _safe_filename(name):
    """Strip path parts and keep a filesystem-safe name."""
    base = os.path.basename(name or "upload.pdf")
    return "".join(c if (c.isalnum() or c in "._-") else "_" for c in base) or "upload.pdf"


def upload_case_document(org_id, case_id, uploaded_file):
    """Upload a Django UploadedFile to the private bucket.

    Returns the storage key (path inside the bucket) to store in the
    `documents` table. Raises on failure — callers show a friendly message.
    """
    key = f"{org_id}/{case_id}/{uuid.uuid4().hex}_{_safe_filename(uploaded_file.name)}"
    data = uploaded_file.read()
    content_type = getattr(uploaded_file, "content_type", None) or "application/pdf"
    _client().storage.from_(BUCKET).upload(
        key, data, {"content-type": content_type}
    )
    return key


def download_case_document(storage_key):
    """Download a file's bytes from the private bucket.

    Day 6: the ingestion pipeline reads the PDF back through the same
    service-role client — files never leave the server.
    """
    return _client().storage.from_(BUCKET).download(storage_key)
