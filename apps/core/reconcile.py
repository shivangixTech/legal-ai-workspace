"""Day 11 - records-vs-bills reconciliation.

The second headline feature of the demo: what the records say was done
vs what the bill charges for, side by side, with mismatches flagged.

Flow:
    case -> indexed documents classified by title
            (bill / invoice / statement / receipt -> BILL,
             everything else -> RECORD)
         -> gemini-3.8-flash extracts bill line items and record
            services, then matches them
         -> result stored in the existing `case_memories` table
            (source='reconciliation', importance='high').

Statuses:
    matched                 - billed AND documented in the records
    billed_not_in_records   - charged, but no record entry (flag)
    in_records_not_billed   - documented, but never charged (flag)

Like Day 10, `case_memories` is reused on purpose: no Supabase schema
change, and the page always shows the latest run. Sync by design;
failures raise ReconcileError with a friendly message.
"""

import json
import os
import re

CHAT_MODEL = "models/gemini-3.8-flash"
MAX_CONTEXT_CHARS_PER_SIDE = 14000
BILL_KEYWORDS = ("bill", "invoice", "statement", "receipt")


class ReconcileError(Exception):
    """Friendly, user-safe failure reason (shown on the case detail page)."""


def classify_document(title):
    """'bill' when the title looks like a bill, else 'record'."""
    t = (title or "").lower()
    return "bill" if any(k in t for k in BILL_KEYWORDS) else "record"


def _genai():
    """Configure the Gemini client from .env (raises ReconcileError if missing)."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or "your_" in api_key:
        raise ReconcileError(
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
        {"document": r[0], "page": r[1], "chunk_index": r[2], "text": r[3]}
        for r in rows
    ]


def _build_context(chunks):
    """Join chunk texts with page labels, capped per side."""
    parts = []
    used = 0
    for ch in chunks:
        block = f"[Document: {ch['document']} | page {ch['page']}]\n{ch['text']}"
        if used + len(block) > MAX_CONTEXT_CHARS_PER_SIDE:
            break
        parts.append(block)
        used += len(block)
    return "\n\n---\n\n".join(parts)


def _prompt(bill_context, record_context):
    return (
        "You are a medical-records auditor. Below are a case's BILLS and "
        "its TREATMENT RECORDS. Extract every billed line item and every "
        "documented service, then match them.\n\n"
        "Return ONLY a JSON object:\n"
        '{"items": [{"description": "service or item name", '
        '"amount": "amount as written in the bill, or null", '
        '"status": "matched" | "billed_not_in_records" | '
        '"in_records_not_billed", '
        '"bill_document": "exact bill title or null", '
        '"bill_page": <page or null>, '
        '"record_document": "exact record title or null", '
        '"record_page": <page or null>}]}\n\n'
        "Rules:\n"
        "- Copy document titles and page numbers exactly from the "
        "[Document: ... | page N] labels.\n"
        "- 'matched' only when the same service appears in BOTH the bill "
        "and the records. Same service in different words still matches "
        "(e.g. 'CBC test' vs 'complete blood count').\n"
        "- 'billed_not_in_records': charged in a bill, absent from records.\n"
        "- 'in_records_not_billed': documented in records, absent from bills.\n"
        "- Never invent amounts, services, or pages. Skip totals, taxes, "
        "headers and boilerplate - only real line items and services.\n\n"
        f"BILLS:\n{bill_context}\n\nRECORDS:\n{record_context}\n\nJSON:"
    )


def _parse_result(raw):
    """Parse the model's JSON object into a validated result dict."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ReconcileError("Reconciliation parse nahi ho payi - dobara try karo.")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        raise ReconcileError("Reconciliation parse nahi ho payi - dobara try karo.")
    items = []
    for item in data.get("items", []):
        if not isinstance(item, dict):
            continue
        description = str(item.get("description") or "").strip()
        status = str(item.get("status") or "").strip()
        if not description or status not in (
            "matched",
            "billed_not_in_records",
            "in_records_not_billed",
        ):
            continue
        amount = item.get("amount")
        items.append(
            {
                "description": description,
                "amount": str(amount).strip() if amount else None,
                "status": status,
                "bill_document": (str(item.get("bill_document")).strip()
                                  if item.get("bill_document") else None),
                "bill_page": item.get("bill_page"),
                "record_document": (str(item.get("record_document")).strip()
                                    if item.get("record_document") else None),
                "record_page": item.get("record_page"),
            }
        )
    if not items:
        raise ReconcileError(
            "Bills aur records me compare karne layak items nahi mile - "
            "check karo ki documents me itemised charges aur services hain."
        )
    summary = {
        "matched": sum(1 for i in items if i["status"] == "matched"),
        "billed_not_in_records": sum(
            1 for i in items if i["status"] == "billed_not_in_records"
        ),
        "in_records_not_billed": sum(
            1 for i in items if i["status"] == "in_records_not_billed"
        ),
    }
    return {"items": items, "summary": summary}


def reconcile_case(case_id):
    """Run the reconciliation for one case.

    Returns {"items": [...], "summary": {...}, "bill_docs": [...],
    "record_docs": [...]}. Raises ReconcileError on any failure.
    """
    chunks = _fetch_chunks(case_id)
    if not chunks:
        raise ReconcileError(
            "Is case me abhi koi indexed document nahi hai - pehle PDF "
            "upload karo aur 'indexed' status ka wait karo."
        )
    bill_chunks = [c for c in chunks if classify_document(c["document"]) == "bill"]
    record_chunks = [c for c in chunks if classify_document(c["document"]) == "record"]
    bill_docs = sorted({c["document"] for c in bill_chunks})
    record_docs = sorted({c["document"] for c in record_chunks})
    if not bill_docs:
        raise ReconcileError(
            "Koi bill document nahi mila - bill wali PDF ke file name me "
            "'Bill' ya 'Invoice' hona chahiye (jaise 'Hospital Bill.pdf')."
        )
    if not record_docs:
        raise ReconcileError(
            "Koi record document nahi mila - bill ke alawa kam se kam ek "
            "treatment record PDF chahiye."
        )
    try:
        genai = _genai()
        model = genai.GenerativeModel(CHAT_MODEL)
        resp = model.generate_content(
            _prompt(_build_context(bill_chunks), _build_context(record_chunks))
        )
        raw = (resp.text or "").strip()
    except ReconcileError:
        raise
    except Exception as exc:
        raise ReconcileError(f"Reconciliation failed: {exc}")
    if not raw:
        raise ReconcileError("Model ne result nahi banaya - dobara try karo.")
    result = _parse_result(raw)
    result["bill_docs"] = bill_docs
    result["record_docs"] = record_docs
    return result
