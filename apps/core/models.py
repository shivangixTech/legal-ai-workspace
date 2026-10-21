"""
Legal AI Workspace — Django models.

KEY DESIGN POINT:
  The 14 tables were already created in Supabase via schema.sql (with the
  pgvector index). That is why every model here is `managed = False`:

  * Django will NEVER try to CREATE these tables (`migrate` stays safe).
  * The ORM (filter/get/create/save) works fully — only table creation is skipped.
  * Django's own tables (auth_user, socialaccount_*) are NOT in schema.sql,
    so `migrate` creates them normally — no conflicts.

  If managed were left True, the first `migrate` would fail with
  "relation already exists".

Vector columns (document_chunks.embedding, case_memories.embedding):
  The Django ORM needs an extra package for pgvector fields. In Phase 2/3
  embeddings are written/read via raw SQL (psycopg), so those columns are
  intentionally left out of the models. All other columns are mapped here,
  with table names exactly matching schema.sql.

Login mapping:
  The `users` table maps to Google OAuth. Simplest workable path: keep
  Django's default login (allauth -> auth_user) and mirror each signup
  into `users` via a signal (see signals.py), linked by google_subject.
  A custom AUTH_USER_MODEL is advanced — the mirror is enough for the demo.
"""

import uuid

from django.db import models


class SupabaseModel(models.Model):
    """Base class: Supabase-owned table — Django never runs CREATE/ALTER on it."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        managed = False


class Organization(SupabaseModel):
    name = models.TextField()

    class Meta(SupabaseModel.Meta):
        db_table = "organizations"

    def __str__(self):
        return self.name


class AppUser(SupabaseModel):
    """Supabase `users` table. (Named AppUser to avoid clashing with Django's auth User.)"""

    email = models.TextField(unique=True)
    name = models.TextField(null=True, blank=True)
    picture = models.TextField(null=True, blank=True)
    google_subject = models.TextField(unique=True, null=True, blank=True)
    last_login = models.DateTimeField(null=True, blank=True)

    class Meta(SupabaseModel.Meta):
        db_table = "users"

    def __str__(self):
        return self.email


class OrganizationMember(SupabaseModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, db_column="organization_id",
        related_name="members",
    )
    user = models.ForeignKey(
        AppUser, on_delete=models.CASCADE, db_column="user_id",
        related_name="org_memberships",
    )
    role = models.TextField()  # owner | admin | lawyer | paralegal | viewer

    class Meta(SupabaseModel.Meta):
        db_table = "organization_members"
        unique_together = ("organization", "user")


class Case(SupabaseModel):
    organization = models.ForeignKey(
        Organization, on_delete=models.CASCADE, db_column="organization_id",
        related_name="cases",
    )
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    status = models.TextField(default="open")  # open | closed | archived
    created_by = models.ForeignKey(
        AppUser, on_delete=models.SET_NULL, null=True, blank=True,
        db_column="created_by", related_name="created_cases",
    )

    class Meta(SupabaseModel.Meta):
        db_table = "cases"

    def __str__(self):
        return self.title


class CaseMember(SupabaseModel):
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, db_column="case_id",
        related_name="members",
    )
    user = models.ForeignKey(
        AppUser, on_delete=models.CASCADE, db_column="user_id",
        related_name="case_memberships",
    )
    permission = models.TextField()  # owner | editor | viewer

    class Meta(SupabaseModel.Meta):
        db_table = "case_members"
        unique_together = ("case", "user")


class Document(SupabaseModel):
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, db_column="case_id",
        related_name="documents",
    )
    title = models.TextField()
    storage_key = models.TextField()  # path inside the Supabase Storage bucket
    mime_type = models.TextField(null=True, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    upload_status = models.TextField(default="uploaded")
    uploaded_by = models.ForeignKey(
        AppUser, on_delete=models.SET_NULL, null=True, blank=True,
        db_column="uploaded_by", related_name="uploaded_documents",
    )

    class Meta(SupabaseModel.Meta):
        db_table = "documents"


class DocumentVersion(SupabaseModel):
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, db_column="document_id",
        related_name="versions",
    )
    content_hash = models.TextField()
    page_count = models.IntegerField(null=True, blank=True)
    parser_status = models.TextField(default="pending")

    class Meta(SupabaseModel.Meta):
        db_table = "document_versions"


class DocumentChunk(SupabaseModel):
    # NOTE: the embedding vector(768) column is excluded from the ORM —
    # pgvector read/write happens via raw SQL in the FastAPI service (Phase 3).
    # Only metadata fields are mapped here.
    version = models.ForeignKey(
        DocumentVersion, on_delete=models.CASCADE, db_column="version_id",
        related_name="chunks",
    )
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, db_column="document_id",
        related_name="chunks",
    )
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, db_column="case_id",
        related_name="chunks",
    )
    page_number = models.IntegerField(null=True, blank=True)
    chunk_index = models.IntegerField(null=True, blank=True)
    text = models.TextField()
    chunk_metadata = models.JSONField(default=dict, db_column="metadata")

    class Meta(SupabaseModel.Meta):
        db_table = "document_chunks"


class Conversation(SupabaseModel):
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, db_column="case_id",
        related_name="conversations",
    )
    user = models.ForeignKey(
        AppUser, on_delete=models.SET_NULL, null=True, blank=True,
        db_column="user_id", related_name="conversations",
    )
    title = models.TextField(null=True, blank=True)

    class Meta(SupabaseModel.Meta):
        db_table = "conversations"


class Message(SupabaseModel):
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, db_column="conversation_id",
        related_name="messages",
    )
    role = models.TextField()  # user | assistant | system
    content = models.TextField()
    citations = models.JSONField(default=list)  # [{document_id, page, chunk_id, quote}]
    model_metadata = models.JSONField(default=dict)

    class Meta(SupabaseModel.Meta):
        db_table = "messages"


class CaseMemory(SupabaseModel):
    # NOTE: embedding vector(768) excluded from the ORM here as well —
    # raw SQL in Phase 4.
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, db_column="case_id",
        related_name="memories",
    )
    text = models.TextField()
    importance = models.TextField(default="medium")  # low | medium | high
    source = models.TextField(null=True, blank=True)

    class Meta(SupabaseModel.Meta):
        db_table = "case_memories"


class ResearchRun(SupabaseModel):
    case = models.ForeignKey(
        Case, on_delete=models.CASCADE, db_column="case_id",
        related_name="research_runs",
    )
    user = models.ForeignKey(
        AppUser, on_delete=models.SET_NULL, null=True, blank=True,
        db_column="user_id", related_name="research_runs",
    )
    query = models.TextField()
    plan = models.JSONField(default=dict)
    status = models.TextField(default="running")
    sources = models.JSONField(default=list)
    report = models.TextField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=10, decimal_places=4, default=0)

    class Meta(SupabaseModel.Meta):
        db_table = "research_runs"


class AuditLog(SupabaseModel):
    actor = models.ForeignKey(
        AppUser, on_delete=models.SET_NULL, null=True, blank=True,
        db_column="actor_id", related_name="audit_logs",
    )
    action = models.TextField()
    object_type = models.TextField(null=True, blank=True)
    object_id = models.UUIDField(null=True, blank=True)
    ip_hash = models.TextField(null=True, blank=True)

    class Meta(SupabaseModel.Meta):
        db_table = "audit_logs"


class Feedback(SupabaseModel):
    message = models.ForeignKey(
        Message, on_delete=models.CASCADE, db_column="message_id",
        related_name="feedback",
    )
    user = models.ForeignKey(
        AppUser, on_delete=models.SET_NULL, null=True, blank=True,
        db_column="user_id", related_name="feedback",
    )
    rating = models.IntegerField(null=True, blank=True)  # -1 | 1
    reason = models.TextField(null=True, blank=True)

    class Meta(SupabaseModel.Meta):
        db_table = "feedback"
