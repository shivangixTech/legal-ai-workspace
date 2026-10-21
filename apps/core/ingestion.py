"""Day 6 — PDF ingestion pipeline (design lock: docs/day-06-plan.md).

Uploaded PDF -> page-safe chunks -> Gemini 768-dim embeddings -> pgvector.

Sync by design (Celery stays deferred): upload_document() calls
ingest_document() in the same request, right after the documents row is
created. The pipeline NEVER takes the upload down with it — any failure
marks the document `failed` with a short, friendly message instead.

Status machine (documents.upload_status):
    uploaded -> parsing -> chunking -> embedding -> indexed
    any step may end in `failed` with a friendly reason.

Page numbers stay citation-exact: pages are never merged, and no chunk
ever crosses a page boundary.
"""

import hashlib
import json
import os
import time
import uuid

# PyMuPDF is imported at module load, NOT inside the request: on slow
# shared CPUs (Render free tier) the first import alone can take longer
# than the web server's 30s worker timeout and kill the upload request.
# Paying the import once at app boot keeps uploads fast.
import fitz  # PyMuPDF

CHUNK_SIZE = 1000          # characters per chunk (fixed-size, Day-6 lock)
CHUNK_OVERLAP = 150        # characters of overlap between neighbours
EMBED_MODEL = "models/gemini-embedding-001"
EMBED_DIMS = 768           # must match document_chunks.embedding vector(768)
EMBED_PAUSE_EVERY = 10     # pause after this many embedding calls...
EMBED_PAUSE_SECS = 2       # ...for this long (free-tier rate-limit cushion)
MIN_TEXT_CHARS = 50        # below this, the PDF has no usable text layer


class IngestionError(Exception):
    """Friendly, user-safe failure reason (shown on the case detail page)."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _chunk_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    """Split ONE page's text into overlapping windows.

    Windows snap to word boundaries so words are never cut in half.
    Call once per page — chunks never cross a page boundary.
    """
    text = " ".join(text.split())  # collapse whitespace to single spaces
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + size, n)
        if end < n:
            snap = text.rfind(" ", start, end)
            if snap > start + size // 2:
                end = snap
        piece = text[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _embed_texts(texts):
    """Embed chunk texts with Gemini (768-dim).

    One call per text, with a short pause every EMBED_PAUSE_EVERY calls
    for the free tier. Raises IngestionError with a friendly message.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or "your_" in api_key:
        raise IngestionError(
            "GEMINI_API_KEY is not set in .env — create a free key at "
            "aistudio.google.com, put it in .env, and restart the server."
        )
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    vectors = []
    for i, text in enumerate(texts):
        if i and i % EMBED_PAUSE_EVERY == 0:
            time.sleep(EMBED_PAUSE_SECS)
        try:
            resp = genai.embed_content(
                model=EMBED_MODEL,
                content=text,
                output_dimensionality=EMBED_DIMS,
            )
        except Exception as exc:
            raise IngestionError(f"Embedding failed on chunk {i + 1}: {exc}")
        vec = resp["embedding"]
        if len(vec) != EMBED_DIMS:
            raise IngestionError(
                f"Embedding came back {len(vec)}-dim, schema needs {EMBED_DIMS}."
            )
        vectors.append(vec)
    return vectors


def ingest_document(document_id):
    """Run the full pipeline for one uploaded document.

    Returns the final status string ('indexed'). Raises IngestionError
    with a friendly message on failure (the document is marked 'failed').
    """
    # Lazy Django imports keep this module importable without settings
    # (useful for unit-testing the pure helpers above).
    from django.db import connection

    from .models import Document, DocumentChunk, DocumentVersion
    from .storage import download_case_document

    document = Document.objects.select_related("case").get(pk=document_id)

    def set_status(status):
        document.upload_status = status
        document.save(update_fields=["upload_status"])

    try:
        # --- parsing ---
        set_status("parsing")
        pdf_bytes = download_case_document(document.storage_key)
        content_hash = _sha256(pdf_bytes)

        # Same file already indexed for this document? No duplicate work.
        existing = DocumentVersion.objects.filter(
            document=document, content_hash=content_hash
        ).first()
        if existing is not None:
            if DocumentChunk.objects.filter(version=existing).exists():
                set_status("indexed")
                return "indexed"
            existing.delete()  # stale version from a failed run — start fresh

        pages = []
        with fitz.open(stream=pdf_bytes, filetype="pdf") as pdf:
            for page in pdf:
                pages.append(page.get_text() or "")
        if sum(len(p) for p in pages) < MIN_TEXT_CHARS:
            raise IngestionError(
                "No readable text found in this PDF — scanned PDFs need OCR, "
                "which comes in a later step. Try a text-based PDF."
            )

        version = DocumentVersion.objects.create(
            document=document,
            content_hash=content_hash,
            page_count=len(pages),
            parser_status="parsed",
        )

        # --- chunking ---
        set_status("chunking")
        chunk_rows = []  # (page_number, chunk_index, text)
        for page_number, page_text in enumerate(pages, start=1):
            for piece in _chunk_text(page_text):
                chunk_rows.append((page_number, len(chunk_rows), piece))
        if not chunk_rows:
            raise IngestionError("Could not split this PDF into chunks.")

        # --- embedding ---
        set_status("embedding")
        vectors = _embed_texts([text for _, _, text in chunk_rows])

        # --- insert (raw SQL: the ORM intentionally skips the pgvector column) ---
        with connection.cursor() as cur:
            for (page_number, chunk_index, text), vec in zip(chunk_rows, vectors):
                cur.execute(
                    """
                    INSERT INTO document_chunks
                        (id, version_id, document_id, case_id,
                         page_number, chunk_index, text, embedding, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                    """,
                    [
                        str(uuid.uuid4()),
                        str(version.id),
                        str(document.id),
                        str(document.case_id),
                        page_number,
                        chunk_index,
                        text,
                        "[" + ",".join(repr(float(v)) for v in vec) + "]",
                        json.dumps({"source": "day6-ingestion"}),
                    ],
                )

        set_status("indexed")
        return "indexed"
    except IngestionError:
        set_status("failed")
        raise
    except Exception as exc:  # never crash the request with a traceback
        set_status("failed")
        raise IngestionError(f"Ingestion failed: {exc}")
