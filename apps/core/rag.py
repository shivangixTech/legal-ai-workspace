"""Day 7 — RAG cited chat (Phase 3 of the roadmap).

Flow for one question:
    question -> Gemini query embedding (768-dim) -> pgvector cosine search
    over THIS case's indexed chunks -> top-k chunks become the context ->
    gemini-3.8-flash answers ONLY from that context, citing [page N] ->
    citations are parsed back into structured rows for the UI.

Case-scoping is the security rule here: retrieval filters by case_id AND
only touches documents whose upload_status = 'indexed'. A question can
never see another case's documents.

Sync by design (same as Day 6 ingestion): the chat view calls ask_case()
in the same request. Any failure raises RagError with a friendly,
user-safe message — the page never crashes.
"""

import os
import re

CHAT_MODEL = "models/gemini-3.8-flash"
EMBED_MODEL = "models/gemini-embedding-001"
EMBED_DIMS = 768  # must match document_chunks.embedding vector(768)
TOP_K = 5  # chunks fed to the model per question


class RagError(Exception):
    """Friendly, user-safe failure reason (shown on the case detail page)."""


def _genai():
    """Configure the Gemini client from .env (raises RagError if missing)."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or "your_" in api_key:
        raise RagError(
            "GEMINI_API_KEY is not set in .env — create a free key at "
            "aistudio.google.com, put it in .env, and restart the server."
        )
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    return genai


def _embed_query(text):
    """Embed one question with Gemini (768-dim)."""
    genai = _genai()
    try:
        resp = genai.embed_content(
            model=EMBED_MODEL,
            content=text,
            output_dimensionality=EMBED_DIMS,
        )
    except Exception as exc:
        raise RagError(f"Question embedding failed: {exc}")
    vec = resp["embedding"]
    if len(vec) != EMBED_DIMS:
        raise RagError(
            f"Embedding came back {len(vec)}-dim, schema needs {EMBED_DIMS}."
        )
    return vec


def retrieve_chunks(case_id, query, top_k=TOP_K):
    """Top-k most similar chunks for this case (cosine distance, pgvector).

    Returns a list of dicts: chunk_id, document_id, document_title, page,
    chunk_index, text, distance. Only indexed documents are searched.
    """
    from django.db import connection

    vec = _embed_query(query)
    vec_literal = "[" + ",".join(repr(float(v)) for v in vec) + "]"
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT c.id, c.document_id, d.title,
                   c.page_number, c.chunk_index, c.text,
                   c.embedding <=> %s::vector AS distance
              FROM document_chunks c
              JOIN documents d ON d.id = c.document_id
             WHERE c.case_id = %s
               AND d.upload_status = 'indexed'
             ORDER BY distance ASC
             LIMIT %s
            """,
            [vec_literal, str(case_id), top_k],
        )
        rows = cur.fetchall()
    return [
        {
            "chunk_id": str(r[0]),
            "document_id": str(r[1]),
            "document_title": r[2],
            "page": r[3],
            "chunk_index": r[4],
            "text": r[5],
            "distance": float(r[6]),
        }
        for r in rows
    ]


_CITE_RE = re.compile(r"\[\s*(?:page|p\.?)\s*(\d+)\s*\]", re.IGNORECASE)


def _build_citations(answer, chunks):
    """Turn the model's [page N] markers into structured citation rows."""
    cited_pages = {int(m.group(1)) for m in _CITE_RE.finditer(answer)}
    by_page = {}
    for ch in chunks:
        by_page.setdefault(ch["page"], ch)
    citations = []
    for page in sorted(cited_pages):
        ch = by_page.get(page)
        if ch is None:
            continue
        citations.append(
            {
                "document_id": ch["document_id"],
                "document_title": ch["document_title"],
                "page": page,
                "chunk_id": ch["chunk_id"],
                "quote": ch["text"][:300],
            }
        )
    return citations


def ask_case(question, case_id, history=None):
    """Answer one question from the case's indexed documents.

    `history` is a list of (role, content) tuples from this conversation
    (oldest first) — it lets the model resolve follow-ups like "aur
    batao" or "uske baare me aur". History never overrides the context:
    facts still come only from the retrieved chunks.

    Returns (answer_text, citations). Raises RagError with a friendly
    message when there is nothing to answer from or the model fails.
    """
    chunks = retrieve_chunks(case_id, question)
    if not chunks:
        raise RagError(
            "Is case me abhi koi indexed document nahi hai — pehle PDF "
            "upload karo aur 'indexed' status ka wait karo."
        )
    genai = _genai()
    context_parts = []
    for ch in chunks:
        context_parts.append(
            f"[Document: {ch['document_title']} | page {ch['page']}]\n{ch['text']}"
        )
    context = "\n\n---\n\n".join(context_parts)
    history_block = ""
    if history:
        lines = []
        for role, content in history[-6:]:  # last 3 exchanges are enough
            who = "User" if role == "user" else "Assistant"
            lines.append(f"{who}: {content[:600]}")
        history_block = (
            "CONVERSATION SO FAR (use it to resolve follow-up questions "
            "like 'tell me more' or 'uske baare me aur'):\n"
            + "\n".join(lines)
            + "\n\n"
        )
    prompt = (
        "You are a legal-document assistant. Answer the user's question "
        "using ONLY the context below.\n"
        "Rules:\n"
        "- Every factual claim must end with a citation like [page 3], "
        "matching the page label shown in the context.\n"
        "- If the context does not contain the answer, say so plainly "
        "instead of guessing.\n"
        "- Keep the answer short and in plain language.\n\n"
        f"{history_block}"
        f"CONTEXT:\n{context}\n\nQUESTION: {question}\n\nANSWER:"
    )
    try:
        model = genai.GenerativeModel(CHAT_MODEL)
        resp = model.generate_content(prompt)
        answer = (resp.text or "").strip()
    except Exception as exc:
        raise RagError(f"Answer generation failed: {exc}")
    if not answer:
        raise RagError("Model ne jawab nahi diya — dobara try karo.")
    citations = _build_citations(answer, chunks)
    return answer, citations
