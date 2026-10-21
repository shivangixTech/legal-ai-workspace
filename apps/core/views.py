"""Core views.

@login_required is our first security gate: without a Google login the
dashboard never renders - the user is sent to /accounts/login/ instead.
This is the "product shell" doing its job.

Day 3 additions:
  * `new_case` - create a case via a Django ModelForm. The form only asks
    for title/description/status; organization and created_by are filled
    in from the logged-in user automatically.
  * The dashboard now lists ONLY the user's own cases (org membership
    filter) - a first, simple RBAC step for the demo.

Day 4 additions:
  * `case_detail` - one page per case (`/cases/<id>/`). Object-level
    access check: the case must belong to one of the user's organizations,
    otherwise 404. Placeholder sections for Documents / Chat / Research
    mark where Phase 2-3 features will plug in.

Day 6 additions:
  * `upload_document` now runs the ingestion pipeline (ingestion.py) in
    the same request, right after the documents row is created: PDF is
    parsed, page-safe chunks are embedded with Gemini, and rows land in
    document_chunks. A pipeline failure only marks the document `failed`
    with a friendly message - the upload itself is never broken.

Day 7 additions (RAG cited chat):
  * `case_chat` - POST a question; the question is embedded, the top-k
    chunks for THIS case are retrieved from pgvector (rag.py), and
    gemini-3.8-flash answers only from that context with [page N]
    citations. Both messages are stored in `conversations`/`messages`.
  * `case_detail` now renders the chat history with citations under each
    assistant message.

Day 8 additions:
  * Follow-up questions: `case_chat` passes the last few messages as
    history into `ask_case()`, so "tell me more" / "uske baare me aur"
    resolves against the conversation.
  * `reindex_document` - a Re-index button for documents stuck at
    `uploaded` (uploaded before the Day 6 code was active). The pipeline
    is idempotent, so re-running never duplicates chunks.

Day 9 additions (Research-lite):
  * `case_research` - POST a legal question; Tavily web search returns up
    to 5 sources, Gemini synthesizes a short cited report. Runs are stored
    in `research_runs` (query, status, sources, report) and listed on the
    case page newest-first.

Day 10 additions (Chronology):
  * `case_chronology` - POST builds a dated, page-cited timeline from the
    case's indexed documents (chronology.py) and stores it in the
    existing `case_memories` table (source='chronology'). The case page
    always shows the latest run - no Supabase schema change needed.

Day 11 additions (Reconciliation + wiring fix):
  * `case_reconcile` - POST runs records-vs-bills reconciliation
    (reconcile.py) and stores the result in `case_memories`
    (source='reconciliation').
  * `_research_context()` - every case-page render now passes
    `research_runs` (before, only the research/chronology views did, so
    runs vanished on a normal page open - the Day 9 bug pattern).
  * `_reconcile_context()` - latest reconciliation on every render.
"""
import json

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ImproperlyConfigured
from django.shortcuts import get_object_or_404, redirect, render

from .chronology import ChronologyError, build_chronology
from .forms import CaseForm, DocumentUploadForm
from .ingestion import IngestionError, ingest_document
from .models import (
    AppUser,
    Case,
    CaseMemory,
    Conversation,
    Document,
    Message,
    Organization,
    OrganizationMember,
    ResearchRun,
)
from .rag import RagError, ask_case
from .reconcile import ReconcileError, reconcile_case
from .research import ResearchError, run_research
from .storage import upload_case_document


def _app_user(request):
    """Map the Django auth user to the Supabase `users` row.

    The login-mirror signal (signals.py) creates this row on first Google
    signup. If it is missing, something is off with the mirror - callers
    treat None as "not set up".
    """
    return AppUser.objects.filter(email=request.user.email).first()


def _get_workspace(app_user):
    """Return the user's workspace organization.

    First organization they belong to (as owner/admin/member). If none
    exists yet - e.g. first login - create a personal workspace org and
    make them its owner. This runs lazily so signup never breaks.
    """
    member = (
        OrganizationMember.objects.filter(user=app_user)
        .select_related("organization")
        .first()
    )
    if member:
        return member.organization
    org = Organization.objects.create(
        name=f"{app_user.name or app_user.email} - Workspace"
    )
    OrganizationMember.objects.create(
        organization=org, user=app_user, role="owner"
    )
    return org


@login_required
def dashboard(request):
    app_user = _app_user(request)
    org_ids = (
        list(
            OrganizationMember.objects.filter(user=app_user).values_list(
                "organization_id", flat=True
            )
        )
        if app_user
        else []
    )
    cases = (
        Case.objects.filter(organization_id__in=org_ids)
        .select_related("organization")
        .order_by("-created_at")[:20]
    )
    return render(
        request,
        "dashboard.html",
        {"cases": cases, "case_count": len(cases)},
    )


@login_required
def new_case(request):
    """Create a case (GET = empty form, POST = validate + save)."""
    app_user = _app_user(request)
    if app_user is None:
        # Mirror signal hasn't run for this user yet - don't crash, just
        # send them back. Re-login fixes it.
        return redirect("dashboard")
    if request.method == "POST":
        form = CaseForm(request.POST)
        if form.is_valid():
            case = form.save(commit=False)
            case.organization = _get_workspace(app_user)
            case.created_by = app_user
            case.save()
            return redirect("dashboard")
    else:
        form = CaseForm()
    return render(request, "case_form.html", {"form": form})


def _org_ids(app_user):
    """IDs of organizations the user belongs to (empty list if none)."""
    if app_user is None:
        return []
    return list(
        OrganizationMember.objects.filter(user=app_user).values_list(
            "organization_id", flat=True
        )
    )


def _case_chat_context(case, app_user):
    """Shared chat context for the case detail page.

    One conversation per case (created lazily); messages oldest-first.
    """
    conversation, _ = Conversation.objects.get_or_create(
        case=case,
        defaults={"user": app_user, "title": f"Chat - {case.title}"},
    )
    messages = Message.objects.filter(conversation=conversation).order_by(
        "created_at"
    )
    return {"conversation": conversation, "chat_messages": messages}


def _chronology_context(case):
    """Latest generated timeline for the case page.

    Stored in `case_memories` with source='chronology' (Day 10). Returns
    (events list, generated_at) - or (None, None) if never generated.
    """
    row = (
        CaseMemory.objects.filter(case=case, source="chronology")
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return {"chronology": None, "chronology_at": None}
    try:
        events = json.loads(row.text)
    except (TypeError, ValueError):
        events = None
    if not isinstance(events, list):
        events = None
    return {"chronology": events, "chronology_at": row.created_at}


def _research_context(case):
    """Research runs for the case page (Day 9, wiring fixed Day 11).

    Every case-page render must include this: before Day 11 only the
    research and chronology views passed `research_runs`, so the runs
    vanished whenever the page was opened normally.
    """
    runs = ResearchRun.objects.filter(case=case).order_by("-created_at")[:10]
    return {"research_runs": runs}


def _reconcile_context(case):
    """Latest records-vs-bills reconciliation for the case page (Day 11).

    Stored in `case_memories` with source='reconciliation'. Returns
    (result dict, generated_at) - or (None, None) if never run.
    """
    row = (
        CaseMemory.objects.filter(case=case, source="reconciliation")
        .order_by("-created_at")
        .first()
    )
    if row is None:
        return {"reconciliation": None, "reconciliation_at": None}
    try:
        result = json.loads(row.text)
    except (TypeError, ValueError):
        result = None
    if not isinstance(result, dict):
        result = None
    return {"reconciliation": result, "reconciliation_at": row.created_at}


@login_required
def case_detail(request, pk):
    """One page per case.

    Object-level access check: the lookup itself is scoped to the user's
    organizations, so guessing another case's URL gives a 404 - never a leak.
    """
    app_user = _app_user(request)
    case = get_object_or_404(
        Case.objects.select_related("organization"),
        pk=pk,
        organization_id__in=_org_ids(app_user),
    )
    documents = Document.objects.filter(case=case).order_by("-created_at")
    context = {
        "case": case,
        "documents": documents,
        "upload_form": DocumentUploadForm(),
    }
    context.update(_case_chat_context(case, app_user))
    context.update(_chronology_context(case))
    context.update(_research_context(case))
    context.update(_reconcile_context(case))
    return render(request, "case_detail.html", context)


@login_required
def case_chat(request, pk):
    """Answer one question about the case's documents (RAG).

    POST only: stores the user message, runs retrieval + generation
    (rag.py), stores the assistant message with structured citations,
    then redirects back to the case page. A RAG failure shows a friendly
    message on the page - the chat history is never lost.
    """
    app_user = _app_user(request)
    case = get_object_or_404(
        Case.objects.select_related("organization"),
        pk=pk,
        organization_id__in=_org_ids(app_user),
    )
    conversation, _ = Conversation.objects.get_or_create(
        case=case,
        defaults={"user": app_user, "title": f"Chat - {case.title}"},
    )
    chat_error = None
    if request.method == "POST":
        question = (request.POST.get("question") or "").strip()
        if question:
            # History BEFORE this question, so follow-ups resolve.
            prior = Message.objects.filter(conversation=conversation).order_by(
                "-created_at"
            )[:6]
            history = [(m.role, m.content) for m in reversed(prior)]
            Message.objects.create(
                conversation=conversation, role="user", content=question
            )
            try:
                answer, citations = ask_case(question, case.id, history)
            except RagError as exc:
                chat_error = str(exc)
            else:
                Message.objects.create(
                    conversation=conversation,
                    role="assistant",
                    content=answer,
                    citations=citations,
                    model_metadata={"model": "gemini-3.8-flash", "top_k": 5},
                )
                return redirect("case_detail", pk=case.pk)
    documents = Document.objects.filter(case=case).order_by("-created_at")
    context = {
        "case": case,
        "documents": documents,
        "upload_form": DocumentUploadForm(),
        "chat_error": chat_error,
    }
    context.update(
        {
            "conversation": conversation,
            "chat_messages": Message.objects.filter(
                conversation=conversation
            ).order_by("created_at"),
        }
    )
    context.update(_chronology_context(case))
    context.update(_research_context(case))
    context.update(_reconcile_context(case))
    return render(request, "case_detail.html", context)


@login_required
def case_chronology(request, pk):
    """Generate the case timeline (Day 10).

    POST only: extracts dated events from the case's indexed documents
    (chronology.py) and stores the result in `case_memories`
    (source='chronology', importance='high'). The case page always shows
    the latest run. A failure shows a friendly message - the page never
    crashes.
    """
    app_user = _app_user(request)
    case = get_object_or_404(
        Case.objects.select_related("organization"),
        pk=pk,
        organization_id__in=_org_ids(app_user),
    )
    chronology_error = None
    if request.method == "POST":
        try:
            events = build_chronology(case.pk)
        except ChronologyError as exc:
            chronology_error = str(exc)
        else:
            CaseMemory.objects.create(
                case=case,
                text=json.dumps(events, ensure_ascii=False),
                importance="high",
                source="chronology",
            )
            return redirect("case_detail", pk=case.pk)
    documents = Document.objects.filter(case=case).order_by("-created_at")
    runs = ResearchRun.objects.filter(case=case).order_by("-created_at")[:10]
    context = {
        "case": case,
        "documents": documents,
        "upload_form": DocumentUploadForm(),
        "research_runs": runs,
        "chronology_error": chronology_error,
    }
    context.update(_case_chat_context(case, app_user))
    context.update(_chronology_context(case))
    context.update(_research_context(case))
    context.update(_reconcile_context(case))
    return render(request, "case_detail.html", context)


@login_required
def case_reconcile(request, pk):
    """Run records-vs-bills reconciliation (Day 11).

    POST only: reconcile.py classifies the case's indexed documents by
    title (bill vs record), extracts bill items and record services with
    Gemini, matches them, and stores the JSON result in `case_memories`
    (source='reconciliation', importance='high'). The page shows the
    latest run. A failure shows a friendly message - never a crash.
    """
    app_user = _app_user(request)
    case = get_object_or_404(
        Case.objects.select_related("organization"),
        pk=pk,
        organization_id__in=_org_ids(app_user),
    )
    reconcile_error = None
    if request.method == "POST":
        try:
            result = reconcile_case(case.pk)
        except ReconcileError as exc:
            reconcile_error = str(exc)
        else:
            CaseMemory.objects.create(
                case=case,
                text=json.dumps(result, ensure_ascii=False),
                importance="high",
                source="reconciliation",
            )
            return redirect("case_detail", pk=case.pk)
    documents = Document.objects.filter(case=case).order_by("-created_at")
    context = {
        "case": case,
        "documents": documents,
        "upload_form": DocumentUploadForm(),
        "reconcile_error": reconcile_error,
    }
    context.update(_case_chat_context(case, app_user))
    context.update(_chronology_context(case))
    context.update(_research_context(case))
    context.update(_reconcile_context(case))
    return render(request, "case_detail.html", context)


@login_required
def case_research(request, pk):
    """Web research for a case (Research-lite, Day 9).

    POST a question: creates a research_runs row, runs Tavily search +
    Gemini synthesis (research.py), stores the cited report + sources,
    then redirects back. A failure marks the run `failed` and shows a
    friendly message - the page never crashes.
    """
    app_user = _app_user(request)
    case = get_object_or_404(
        Case.objects.select_related("organization"),
        pk=pk,
        organization_id__in=_org_ids(app_user),
    )
    research_error = None
    if request.method == "POST":
        question = (request.POST.get("question") or "").strip()
        if question:
            run = ResearchRun.objects.create(
                case=case, user=app_user, query=question, status="running"
            )
            try:
                report, sources = run_research(question)
            except ResearchError as exc:
                run.status = "failed"
                run.save(update_fields=["status"])
                research_error = str(exc)
            else:
                run.report = report
                run.sources = sources
                run.status = "completed"
                run.save(update_fields=["report", "sources", "status"])
                return redirect("case_detail", pk=case.pk)
    runs = ResearchRun.objects.filter(case=case).order_by("-created_at")[:10]
    documents = Document.objects.filter(case=case).order_by("-created_at")
    context = {
        "case": case,
        "documents": documents,
        "upload_form": DocumentUploadForm(),
        "research_runs": runs,
        "research_error": research_error,
    }
    context.update(_case_chat_context(case, app_user))
    context.update(_chronology_context(case))
    context.update(_research_context(case))
    context.update(_reconcile_context(case))
    return render(request, "case_detail.html", context)


@login_required
def reindex_document(request, pk, doc_id):
    """Re-run the Day 6 ingestion pipeline for an already-uploaded document.

    For documents stuck at `uploaded` (uploaded before Day 6 code was
    active). ingest_document() is idempotent - same file twice never
    duplicates chunks. POST only; redirects back to the case page, where
    the status column shows `indexed` (or `failed` with a reason).
    """
    app_user = _app_user(request)
    case = get_object_or_404(
        Case.objects.select_related("organization"),
        pk=pk,
        organization_id__in=_org_ids(app_user),
    )
    doc = get_object_or_404(Document, pk=doc_id, case=case)
    error = None
    if request.method == "POST":
        try:
            ingest_document(doc.pk)
        except IngestionError as exc:
            error = str(exc)
        else:
            return redirect("case_detail", pk=case.pk)
    documents = Document.objects.filter(case=case).order_by("-created_at")
    context = {
        "case": case,
        "documents": documents,
        "upload_form": DocumentUploadForm(),
        "upload_error": error,
    }
    context.update(_case_chat_context(case, app_user))
    context.update(_chronology_context(case))
    context.update(_research_context(case))
    context.update(_reconcile_context(case))
    return render(request, "case_detail.html", context)


@login_required
def upload_document(request, pk):
    """Upload a PDF into a case.

    Flow: browser → Django (validates) → Supabase Storage (private bucket)
    → `documents` row in Postgres → ingestion pipeline (Day 6) in the same
    request. The service_role key never leaves the server. Same org-scoped
    404 guard as case_detail.
    """
    app_user = _app_user(request)
    case = get_object_or_404(
        Case.objects.select_related("organization"),
        pk=pk,
        organization_id__in=_org_ids(app_user),
    )
    error = None
    if request.method == "POST":
        form = DocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            uploaded = form.cleaned_data["file"]
            try:
                key = upload_case_document(
                    case.organization_id, case.id, uploaded
                )
                doc = Document.objects.create(
                    case=case,
                    title=uploaded.name,
                    storage_key=key,
                    mime_type=getattr(uploaded, "content_type", "application/pdf"),
                    size_bytes=getattr(uploaded, "size", None),
                    upload_status="uploaded",
                    uploaded_by=app_user,
                )
            except ImproperlyConfigured as exc:
                error = str(exc)
            except Exception as exc:  # storage/network failure - don't crash
                error = f"Upload failed: {exc}"
            else:
                # Day 6: the upload already succeeded - a pipeline failure
                # only marks the document `failed`, never breaks the upload.
                try:
                    ingest_document(doc.pk)
                except IngestionError as exc:
                    error = str(exc)
                else:
                    return redirect("case_detail", pk=case.pk)
    else:
        form = DocumentUploadForm()
    documents = Document.objects.filter(case=case).order_by("-created_at")
    context = {
        "case": case,
        "documents": documents,
        "upload_form": form,
        "upload_error": error,
    }
    context.update(_case_chat_context(case, app_user))
    context.update(_chronology_context(case))
    context.update(_research_context(case))
    context.update(_reconcile_context(case))
    return render(request, "case_detail.html", context)
