# core app — business models mapped to Supabase tables.
# IMPORTANT: all models are managed=False — the tables already exist in
# Supabase (created via schema.sql), so Django maps them without
# ever trying to recreate them.
default_app_config = "apps.core.apps.CoreConfig"
