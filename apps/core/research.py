"""Day 9 — Research-lite (Phase 4 simplified, per the 13-day crash replan).

Single-shot web research: Tavily search (up to 5 sources) -> Gemini
synthesizes a short cited report. The LangGraph planner/judge loop was
cut in the replan — this covers demo step 5 with one clean flow.

Runs sync in the request (same as Day 6/7/8). Uses `requests`
(stdlib-adjacent, already installed for allauth) against Tavily's REST
API directly, so no new dependency can break the install.

Failures raise ResearchError with a friendly message; the research_runs
row is marked `failed`, and the page never crashes.
"""

import os

TAVILY_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5
CHAT_MODEL = "models/gemini-3.8-flash"


class ResearchError(Exception):
    """Friendly, user-safe failure reason (shown on the case detail page)."""


def _tavily_key():
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key or "your_" in api_key:
        raise ResearchError(
            "TAVILY_API_KEY is not set in .env — create a free key at "
            "tavily.com, put it in .env, and restart the server."
        )
    return api_key


def _tavily_search(query):
    """Web search via Tavily REST API. Returns [{title, url, snippet}]."""
    import requests

    try:
        resp = requests.post(
            TAVILY_URL,
            json={
                "api_key": _tavily_key(),
                "query": query,
                "max_results": MAX_RESULTS,
                "search_depth": "basic",
            },
            timeout=30,
        )
    except Exception as exc:
        raise ResearchError(f"Web search failed: {exc}")
    if resp.status_code != 200:
        raise ResearchError(
            f"Web search returned HTTP {resp.status_code} — check "
            "TAVILY_API_KEY in .env."
        )
    results = []
    for r in resp.json().get("results", [])[:MAX_RESULTS]:
        results.append(
            {
                "title": r.get("title") or r.get("url") or "Untitled",
                "url": r.get("url") or "",
                "snippet": (r.get("content") or "")[:500],
            }
        )
    if not results:
        raise ResearchError(
            "Web search returned no results — try a different question."
        )
    return results


def _synthesize(question, sources):
    """Turn sources into a short cited report with Gemini."""
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key or "your_" in api_key:
        raise ResearchError(
            "GEMINI_API_KEY is not set in .env — create a free key at "
            "aistudio.google.com, put it in .env, and restart the server."
        )
    import google.generativeai as genai

    genai.configure(api_key=api_key)
    numbered = []
    for i, s in enumerate(sources, start=1):
        numbered.append(f"[{i}] {s['title']}\n{s['url']}\n{s['snippet']}")
    sources_block = "\n\n".join(numbered)
    prompt = (
        "You are a legal research assistant. Write a short research report "
        "answering the question below, using ONLY the web sources given.\n"
        "Rules:\n"
        "- Cite every factual claim with the source number like [1], [2].\n"
        "- If the sources disagree or lack the answer, say so plainly.\n"
        "- Keep it under 250 words, plain language, with a 2-3 line "
        "summary first.\n\n"
        f"SOURCES:\n{sources_block}\n\nQUESTION: {question}\n\nREPORT:"
    )
    try:
        model = genai.GenerativeModel(CHAT_MODEL)
        resp = model.generate_content(prompt)
        report = (resp.text or "").strip()
    except Exception as exc:
        raise ResearchError(f"Report generation failed: {exc}")
    if not report:
        raise ResearchError("Model ne report nahi banayi — dobara try karo.")
    return report


def run_research(question):
    """One research run: web search -> cited report.

    Returns (report_text, sources). Raises ResearchError on any failure.
    """
    sources = _tavily_search(question)
    report = _synthesize(question, sources)
    return report, sources
