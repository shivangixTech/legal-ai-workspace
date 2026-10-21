"""Admin panel — inspect the Supabase tables from here.

Note: these models are `managed = False`, yet admin read/write still
works — Django only skips CREATE TABLE for unmanaged models.
"""
from django.contrib import admin

from .models import AppUser, AuditLog, Case, Conversation, Document, Organization


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "created_at")


@admin.register(AppUser)
class AppUserAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "last_login")
    search_fields = ("email",)


@admin.register(Case)
class CaseAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "status", "created_at")
    list_filter = ("status",)


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "case", "upload_status", "created_at")
    list_filter = ("upload_status",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "case", "created_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("action", "object_type", "created_at")
