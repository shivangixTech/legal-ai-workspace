"""Login mirror signal.

Design decision: Django's `auth_user` vs Supabase's `users` table.
We keep allauth's default login (into `auth_user`) and mirror the first
Google signup into the Supabase `users` table via this signal, linked by
`google_subject`. A custom AUTH_USER_MODEL is overkill for now — the
mirror is enough for the demo, and the trade-off is easy to explain.
"""
from allauth.account.signals import user_signed_up
from django.dispatch import receiver

from .models import AppUser


@receiver(user_signed_up)
def mirror_user_to_supabase(request, user, **kwargs):
    """Create a row in the `users` table on first Google signup (idempotent)."""
    social = user.socialaccount_set.filter(provider="google").first()
    AppUser.objects.update_or_create(
        email=user.email,
        defaults={
            "name": user.get_full_name() or user.username,
            "google_subject": social.uid if social else None,
        },
    )
