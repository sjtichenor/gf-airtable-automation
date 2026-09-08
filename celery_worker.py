import os
import sys
import requests
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

# Ensure the project root is always on the path so sub-module imports work
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


# ---------------------------------------------------------------------------
# Helper — fetch the full record from Airtable by record ID
# ---------------------------------------------------------------------------

def fetch_full_record(record_id: str) -> dict | None:
    """Fetch a complete post record from Airtable using just the record ID.

    This guarantees all fields (including linked records like Channel) are
    present in the exact format the sync classes expect — same as cron jobs.
    """
    airtable_token = os.getenv("AIRTABLE_PERSONAL_ACCESS_TOKEN")
    airtable_base_id = os.getenv("AIRTABLE_BASE_ID")
    posts_table_id = os.getenv("AIRTABLE_TABLE_ID", "tblMpYJQjbb5yuKfC")

    url = f"https://api.airtable.com/v0/{airtable_base_id}/{posts_table_id}/{record_id}"
    headers = {
        "Authorization": f"Bearer {airtable_token}",
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers)

    if response.status_code == 200:
        print(f"✅ Fetched full record from Airtable: {record_id}")
        return response.json()
    else:
        print(f"❌ Failed to fetch record {record_id}: {response.status_code} {response.text}")
        return None

# ---------------------------------------------------------------------------
# Celery app
# ---------------------------------------------------------------------------

celery_app = Celery(
    "gf_airtable",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Job is only removed from the queue after it finishes successfully.
    # If the worker crashes mid-task the job is re-queued automatically.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, name="sync_youtube_post")
def sync_youtube_post(self, post_record: dict):
    """Fetch metrics for a single YouTube post and write them to Airtable."""
    try:
        full_record = fetch_full_record(post_record["id"])
        if not full_record:
            raise ValueError(f"Could not fetch record {post_record['id']} from Airtable")
        from sync_app import PostsTableSync
        sync = PostsTableSync()
        sync.process_single_post(full_record)
    except Exception as exc:
        print(f"❌ [YouTube] Task failed: {exc}. Retrying ({self.request.retries}/{self.max_retries})…")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, name="sync_instagram_post")
def sync_instagram_post(self, post_record: dict):
    """Fetch metrics for a single Instagram post and write them to Airtable."""
    try:
        full_record = fetch_full_record(post_record["id"])
        if not full_record:
            raise ValueError(f"Could not fetch record {post_record['id']} from Airtable")
        from sync_app import PostsTableSync
        sync = PostsTableSync()
        sync.process_single_post(full_record)
    except Exception as exc:
        print(f"❌ [Instagram] Task failed: {exc}. Retrying ({self.request.retries}/{self.max_retries})…")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, name="sync_tiktok_post")
def sync_tiktok_post(self, post_record: dict):
    """Fetch metrics for a single TikTok post and write them to Airtable.

    TikTokSync requires a username → account mapping to be built before
    processing a single post, so we call build_username_mapping() first.
    This adds ~1-2 s but is unavoidable given the current design.
    """
    try:
        full_record = fetch_full_record(post_record["id"])
        if not full_record:
            raise ValueError(f"Could not fetch record {post_record['id']} from Airtable")
        from tiktok.sync_tiktok_posts import TikTokSync
        sync = TikTokSync()
        sync.build_username_mapping()
        sync.process_single_tiktok_post(full_record)
    except Exception as exc:
        print(f"❌ [TikTok] Task failed: {exc}. Retrying ({self.request.retries}/{self.max_retries})…")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, name="sync_facebook_post")
def sync_facebook_post(self, post_record: dict):
    """Fetch metrics for a single Facebook post and write them to Airtable."""
    try:
        full_record = fetch_full_record(post_record["id"])
        if not full_record:
            raise ValueError(f"Could not fetch record {post_record['id']} from Airtable")
        from fb.main import FacebookSync
        sync = FacebookSync()
        sync.process_single_facebook_post(full_record)
    except Exception as exc:
        print(f"❌ [Facebook] Task failed: {exc}. Retrying ({self.request.retries}/{self.max_retries})…")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, name="sync_twitter_post")
def sync_twitter_post(self, post_record: dict):
    """Sync Twitter metrics.

    TwitterSync does not have a single-post method, so this triggers a full
    Twitter sync. Only the new post will effectively be missing data, so in
    practice the full sync is fast and adds the metrics for the new post.
    """
    try:
        from twitter_sync import TwitterSync
        sync = TwitterSync()
        sync.sync_twitter_posts()
    except Exception as exc:
        print(f"❌ [Twitter] Task failed: {exc}. Retrying ({self.request.retries}/{self.max_retries})…")
        raise self.retry(exc=exc, countdown=60 * (self.request.retries + 1))
