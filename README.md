# Legal AI Workspace (Advotac 2.0)

An evidence-first AI workspace for legal teams working with medical
records. Upload case PDFs and get source-cited answers, an automatic
case chronology, and a records-vs-bills reconciliation - every AI
output linked to the exact document and page it came from, so a human
can verify it in one click.

Built as a college project (an Advotac-style product) and also the
working prototype of **Shivru AI** - see https://shivru.tech

## Features

- **Google sign-in** (django-allauth OAuth) with per-user cases
- **PDF ingestion pipeline** - parse, chunk, embed (Gemini
  `gemini-embedding-001`, 768-dim) and store in Supabase pgvector
- **Cited case chat (RAG)** - answers use only the case's own
  documents, carry `[page N]` citations and clickable citation chips;
  the assistant refuses when the documents do not contain the answer
- **Follow-up questions** - short conversation memory on top of the
  document grounding
- **Web research** - Tavily search plus a short Gemini report with
  numbered source citations
- **Case chronology** - one click builds a dated timeline of the case
  (admission, procedures, discharge), each event cited to its page;
  dates are never invented (unclear dates are marked "undated")
- **Records vs Bills reconciliation** - compares the hospital bill
  with the treatment records and flags three buckets: matched,
  billed-but-not-in-records, and in-records-but-not-billed, with
  amounts and page evidence on both sides

## Tech stack

- **Django 5** - product shell: UI, auth, cases, admin
- **Supabase** - Postgres + pgvector + file storage (single data
  plane; business tables are `managed = False` in Django)
- **Google Gemini (free tier)** - chat and embeddings
- **Tavily** - web search for the research feature
- **Render** - production hosting (gunicorn + WhiteNoise)

## Run locally

```bash
pip install -r requirements.txt
# create .env next to manage.py with:
#   DATABASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY,
#   SUPABASE_SERVICE_ROLE_KEY, GOOGLE_CLIENT_ID,
#   GOOGLE_CLIENT_SECRET, DJANGO_SECRET_KEY,
#   GEMINI_API_KEY, TAVILY_API_KEY
python manage.py runserver
```

Database schema for the business tables lives in `supabase/schema.sql`.

## Deploy (Render)

The service is defined in `render.yaml`. Point a Render web service
at this repo, fill the env vars in the dashboard (never commit real
keys), and add the live callback URL
`https://<your-service>.onrender.com/accounts/google/login/callback/`
to the Google OAuth client's authorized redirect URIs.

## Project layout

- `apps/core/` - cases, ingestion, RAG chat, research, chronology,
  reconciliation (all features are synchronous in Django by design)
- `config/` - Django settings and URLs
- `templates/` - server-rendered UI
- `supabase/schema.sql` - business-table schema (15 tables)
