"""
social_post.py
Runs every day at 12:00 UTC (14:00 Italian time).
Checks for approved social posts and publishes to Instagram + Facebook.
"""

import os, json, sys, time
import urllib.request, urllib.error, urllib.parse
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from supabase import create_client

# ── Config ────────────────────────────────────────────────────────────────────
sb            = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
FB_PAGE_TOKEN = os.getenv("FB_PAGE_TOKEN", "")
FB_PAGE_ID    = os.getenv("FB_PAGE_ID", "")
IG_ACCOUNT_ID = os.getenv("IG_ACCOUNT_ID", "")
GRAPH_V       = "v22.0"

def graph_post(path: str, payload: dict) -> dict:
    url  = f"https://graph.facebook.com/{GRAPH_V}/{path}"
    data = json.dumps(payload).encode()
    req  = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Graph API error {e.code}: {body}")

def graph_get(path: str, params: dict) -> dict:
    qs  = "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    url = f"https://graph.facebook.com/{GRAPH_V}/{path}?{qs}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Graph API error {e.code}: {body}")

# ── Instagram carousel ────────────────────────────────────────────────────────
def post_instagram_carousel(slide_urls: list[str], caption: str) -> str:
    """Upload images, create carousel container, publish. Returns IG media ID."""

    # 1. Create image containers
    print("  Creating image containers...")
    item_ids = []
    for i, url in enumerate(slide_urls):
        result = graph_post(f"{IG_ACCOUNT_ID}/media", {
            "image_url":    url,
            "is_carousel_item": True,
            "access_token": FB_PAGE_TOKEN,
        })
        item_ids.append(result["id"])
        print(f"    Image {i+1}/{len(slide_urls)}: {result['id']}")
        time.sleep(1)

    # 2. Create carousel container
    print("  Creating carousel container...")
    carousel = graph_post(f"{IG_ACCOUNT_ID}/media", {
        "media_type":   "CAROUSEL",
        "children":     item_ids,
        "caption":      caption,
        "access_token": FB_PAGE_TOKEN,
    })
    carousel_id = carousel["id"]
    print(f"  Carousel container: {carousel_id}")

    # 3. Wait for processing
    print("  Waiting for processing...")
    for attempt in range(12):
        time.sleep(5)
        status = graph_get(carousel_id, {
            "fields":       "status_code",
            "access_token": FB_PAGE_TOKEN,
        })
        code = status.get("status_code", "IN_PROGRESS")
        print(f"    Status: {code}")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError("Instagram media processing failed")
    else:
        raise RuntimeError("Instagram media processing timed out")

    # 4. Publish
    print("  Publishing carousel...")
    result = graph_post(f"{IG_ACCOUNT_ID}/media_publish", {
        "creation_id":  carousel_id,
        "access_token": FB_PAGE_TOKEN,
    })
    return result["id"]

# ── Facebook post ─────────────────────────────────────────────────────────────
def build_fb_message(text: str, article_url: str, journal: str = "") -> str:
    journal_line = f"📋 {journal}" if journal else "📋 PubMed"
    return (
        f"{text}\n"
        f"{journal_line}\n"
        f"{article_url}\n"
        f"🔗 Newsletter: https://www.neuro-digest.com"
    )

def post_facebook(cover_url: str, text: str, article_url: str, journal: str = "") -> str:
    """Post to Facebook Page with cover image."""
    print("  Posting to Facebook...")
    message = build_fb_message(text, article_url, journal)
    result = graph_post(f"{FB_PAGE_ID}/photos", {
        "url":          cover_url,
        "message":      message,
        "access_token": FB_PAGE_TOKEN,
    })
    return result.get("post_id", result.get("id", ""))

# ── Build Instagram caption ───────────────────────────────────────────────────
def build_caption(content: dict) -> str:
    return (
        f"{content['article_title']}\n\n"
        f"{content['fb_text']}\n\n"
        f"#neurology #neurodigest #neurologia #neuroscience #medicaleducation"
    )

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== NeuroDigest Social Post ===")

    # Fetch approved pending post
    rows = (
        sb.table("social_posts")
          .select("*")
          .eq("approved", True)
          .is_("posted_at", "null")
          .order("created_at", desc=True)
          .limit(1)
          .execute()
    )

    if not rows.data:
        print("No approved posts to publish — exiting.")
        sys.exit(0)

    post = rows.data[0]
    post_id    = post["id"]
    slide_urls = post["slide_urls"]
    fb_text    = post["fb_text"]

    print(f"\nPost: {post['article_title'][:60]}")
    print(f"Slides: {len(slide_urls)}")

    content = {
        "article_title": post["article_title"],
        "article_url":   post["article_url"],
        "fb_text":       fb_text,
    }

    ig_post_id = post.get("ig_post_id")
    fb_post_id = post.get("fb_post_id")

    # Post to Instagram (skip if already done)
    if not ig_post_id:
        try:
            print("\n[1/2] Posting to Instagram...")
            caption    = build_caption(content)
            ig_post_id = post_instagram_carousel(slide_urls, caption)
            print(f"  ✓ Instagram post ID: {ig_post_id}")
        except Exception as e:
            print(f"  ✗ Instagram error: {e}")
    else:
        print(f"\n[1/2] Instagram already posted ({ig_post_id}) — skipping")

    # Post to Facebook (skip if already done)
    if not fb_post_id:
        try:
            print("\n[2/2] Posting to Facebook...")
            fb_post_id = post_facebook(slide_urls[0], fb_text, post["article_url"], post.get("journal", ""))
            print(f"  ✓ Facebook post ID: {fb_post_id}")
        except Exception as e:
            print(f"  ✗ Facebook error: {e}")
    else:
        print(f"\n[2/2] Facebook already posted ({fb_post_id}) — skipping")

    # Update Supabase
    sb.table("social_posts").update({
        "posted_at":  datetime.now(timezone.utc).isoformat(),
        "ig_post_id": ig_post_id,
        "fb_post_id": fb_post_id,
    }).eq("id", post_id).execute()

    # Update Notion status → Used
    if post.get("notion_page_id"):
        notion_token = os.getenv("NOTION_TOKEN", "")
        payload = {"properties": {"Status": {"select": {"name": "Used"}}}}
        req = urllib.request.Request(
            f"https://api.notion.com/v1/pages/{post['notion_page_id']}",
            data=json.dumps(payload).encode(),
            method="PATCH",
            headers={
                "Authorization":  f"Bearer {notion_token}",
                "Notion-Version": "2022-06-28",
                "Content-Type":   "application/json",
            }
        )
        with urllib.request.urlopen(req) as r:
            pass
        print("\n✓ Notion article marked as Used")

    print("\n✓ Done!")
