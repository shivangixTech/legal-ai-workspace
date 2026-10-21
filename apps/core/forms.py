"""Day 3 — forms for case management.

The `Case` model maps the Supabase `cases` table (managed = False, so the
ORM can insert rows without trying to create the table).
"""
from django import forms

from .models import Case

STATUS_CHOICES = [
    ("open", "Open"),
    ("closed", "Closed"),
    ("archived", "Archived"),
]


class CaseForm(forms.ModelForm):
    status = forms.ChoiceField(choices=STATUS_CHOICES, initial="open")

    class Meta:
        model = Case
        fields = ["title", "description", "status"]
        labels = {
            "title": "Case title",
            "description": "Description",
            "status": "Status",
        }
        widgets = {
            "title": forms.TextInput(
                attrs={"placeholder": "e.g. Sharma vs State — Bail Application"}
            ),
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Short summary of the matter (optional)",
                }
            ),
        }


class DocumentUploadForm(forms.Form):
    """Single PDF upload for a case. Only PDFs are accepted."""

    file = forms.FileField(
        label="PDF document",
        help_text="PDF files only, up to 20 MB.",
    )

    def clean_file(self):
        f = self.cleaned_data["file"]
        name = (f.name or "").lower()
        if not name.endswith(".pdf"):
            raise forms.ValidationError("Only PDF files are allowed.")
        if f.size and f.size > 20 * 1024 * 1024:
            raise forms.ValidationError("File is too large (max 20 MB).")
        return f
