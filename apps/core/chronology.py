"""Day 10 - case chronology (source-cited timeline).

One click on the case page turns the indexed documents into a dated
timeline: every event carries the document title and page number it
came from, so each row is verifiable - the core promise of the
Advotac-style demo.

Flow:
    case -> all indexed chunks (reading order: document, page) ->
    gemini-3.8-flash extracts dated events as JSON ->
    stored in the existing `case_memories` table
    (source='chronology', importance='high').

The `case_memories` table is reused on purpose: no Supabase schema
change is needed, and the ORM insert works because the embedding
column is nullable. The case page always shows the latest run.

Sync by design, like Day 6/7/8/9: the view calls build_chronology()
in the same request. Failures raise ChronologyError with a friendly,
user-safe message - the page never crashes.
"""

import json
import os
import re

CHAT_MODEL = "models/gemini-3.8-flash"
MAX_CONTEXT_CHARS = 30000  # ~30-40 chunks; keeps the call fast and cheap


class ChronologyError(Exception):
    """Friendly, user-safe failure reason (shown on the case detail page)."""


def _genai():
    """Configure the Gemini client from .env (raises ChronologyError if missing)."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or "your_" in api_key:
        raise ChronologyError(
            "GEMINI_API_KEY is not set in .env - create a free key at "
            "aistudio.google.com, put it in .env, and restart the server."
        )
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    return genai


def _fetch_chunks(case_id):
    """All indexed chunks for this case, in document reading order."""
    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT d.title, c.page_number, c.chunk_index, c.text
              FROM document_chunks c
              JOIN documents d ON d.id = c.document_id
             WHERE c.case_id = %s
               AND d.upload_status = 'indexed'
             ORDER BY d.created_at ASC, c.page_number ASC, c.chunk_index ASC
            """,
            [str(case_id)],
        )
        rows = cur.fetchall()
    return [
        {
            "document": r[0],
            "page": r[1],
            "chunk_index": r[2],
            "text": r[3],
        }
        for r in rows
    ]


def _build_context(chunks):
    """Join chunk texts with page labels, capped at MAX_CONTEXT_CHARS."""
    parts = []
    used = 0
    for ch in chunks:
        block = f"[Document: {ch['document']} | page {ch['page']}]\n{ch['text']}"
        if used + len(block) > MAX_CONTEXT_CHARS:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)


def _prompt(context):
    return (
        "You are a legal-document assistant. Read the case documents below "
        "and extract a TIMELINE of events.\n\n"
        "Return ONLY a JSON array. Each item:\n"
        '  {"date": "YYYY-MM-DD or "undated"", '
        '"event": "one-line description", '
        '"document": "<exact document title from the labels>", '
        '"page": <page number from the labels>}\n\n'
        "Rules:\n"
        "- Copy the document title and page number exactly as shown in "
        "the [Document: ... | page N] labels.\n"
        "- Use ISO date YYYY-MM-DD when the document states an explicit "
        "date. If only month/year is given, use YYYY-MM-01. If no date "
        "is stated, use \"undated\".\n"
        "- Never invent dates. Skip boilerplate (headers, disclaimers, "
        "page numbers, table of contents).\n"
        "- Order: dated events oldest first, then undated at the end.\n"
        "- One line per event, plain language.\n\n"
        f"DOCUMENTS:\n{context}\n\nJSON:"
    )


def _parse_events(raw):
    """Parse the model's JSON array into a validated event list."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        raise ChronologyError(
            "The timeline could not be parsed - please try again."
        )
    try:
        items = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        raise ChronologyError(
            "The timeline could not be parsed - please try again."
        )
    events = []
    for item in items:
        if not isinstance(item, dict):
            continue
        event = str(item.get("event") or "").strip()
        if not event:
            continue
        events.append(
            {
                "date": str(item.get("date") or "undated").strip()
                or "undated",
                "event": event,
                "document": str(item.get("document") or "").strip(),
                "page": item.get("page"),
            }
        )
    if not events:
        raise ChronologyError(
            "No timeline events were found in the documents - "
            "please check that the PDFs contain dated content."
        )
    return events


def build_chronology(case_id):
    """Build the case timeline.

    Returns a list of dicts: date, event, document, page.
    Raises ChronologyError with a friendly message on any failure.
    """
    chunks = _fetch_chunks(case_id)
    if not chunks:
        raise ChronologyError(
            "This case has no indexed document yet - please upload a PDF "
            "and wait for its status to become 'indexed'."
        )
    context = _build_context(chunks)
    genai = _genai()
    try:
        model = genai.GenerativeModel(CHAT_MODEL)
        resp = model.generate_content(_prompt(context))
        raw = (resp.text or "").strip()
    except Exception as exc:
        raise ChronologyError(f"Timeline generation failed: {exc}")
    if not raw:
        raise ChronologyError("The model did not return a timeline - please try again.")
    return _parse_events(raw)
