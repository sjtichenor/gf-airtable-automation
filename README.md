# gf-airtable-automation

Social-metrics syncs (Facebook, Instagram, YouTube, TikTok, X) plus the Airtable webhook server and the Celery worker.

Imported 2026-09-08 from the deployed tree on Render (`webhook` service, branch `whook`). No history was carried over. The two Airtable-token fallback defaults that were in the original were removed before this first commit; every secret now comes from the environment.

See `OPERATIONS.md` in `sjtichenor/gf-episode-sync` for the takeover plan and what each service does.
