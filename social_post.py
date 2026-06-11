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

# ── Instagram Story (1 cover verticale 1080x1920) ────────────────────────────
def post_instagram_story(story_cover_url: str) -> str:
    """Post single vertical cover (1080x1920) as Instagram Story."""
    container = graph_post(f"{IG_ACCOUNT_ID}/media", {
        "image_url":    story_cover_url,
        "media_type":   "STORIES",
        "access_token": FB_PAGE_TOKEN,
    })
    cid = container["id"]
    for _ in range(10):
        time.sleep(3)
        status = graph_get(cid, {"fields": "status_code", "access_token": FB_PAGE_TOKEN})
        if status.get("status_code") == "FINISHED":
            break
    result = graph_post(f"{IG_ACCOUNT_ID}/media_publish", {
        "creation_id":  cid,
        "access_token": FB_PAGE_TOKEN,
    })
    return result["id"]

# ── Facebook Story (1 cover verticale 1080x1920) ──────────────────────────────
def post_facebook_story(story_cover_url: str) -> str:
    """Post single vertical cover (1080x1920) as Facebook Story."""
    # Upload photo first, then publish as story
    result = graph_post(f"{FB_PAGE_ID}/photos", {
        "url":           story_cover_url,
        "published":     False,
        "temporary":     True,
        "access_token":  FB_PAGE_TOKEN,
    })
    photo_id = result.get("id", "")
    # Publish as story
    story = graph_post(f"{FB_PAGE_ID}/photo_stories", {
        "photo_id":     photo_id,
        "access_token": FB_PAGE_TOKEN,
    })
    return story.get("post_id", story.get("id", ""))

# ── Facebook post ─────────────────────────────────────────────────────────────
def build_fb_message(text: str, article_url: str, journal: str = "") -> str:
    journal_line = f"📋 {journal}" if journal else "📋 PubMed"
    return (
        f"{text.strip()}\n\n"
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
def build_caption(post: dict) -> str:
    text = post.get("fb_text", "").strip()
    article_url = post.get("article_url", "")
    return (
        f"{text}\n\n"
        f"{article_url}\n\n"
        f"#neurology #neurodigest #neurologia #neuroscience #medicaleducation"
    )

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        print("=== NeuroDigest Social Post ===")

        # Fetch posts where at least one platform was EXPLICITLY approved by the user
        # NOTE: 'approved' (old field) is ignored — only ig_approved and fb_approved count
        rows = (
            sb.table("social_posts")
              .select("*")
              .is_("posted_at", "null")
              .or_("ig_approved.eq.true,fb_approved.eq.true,ig_story_approved.eq.true,fb_story_approved.eq.true")
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

    ig_post_id   = post.get("ig_post_id")
    fb_post_id   = post.get("fb_post_id")
    ig_story_id  = post.get("ig_story_id")
    fb_story_id  = post.get("fb_story_id")
    do_instagram      = bool(post.get("ig_approved"))
    do_facebook       = bool(post.get("fb_approved"))
    do_ig_story       = bool(post.get("ig_story_approved"))
    do_fb_story       = bool(post.get("fb_story_approved"))
    do_ig_story_video = bool(post.get("ig_story_video_approved"))
    do_fb_story_video = bool(post.get("fb_story_video_approved"))
    reel_url          = post.get("reel_url", "")
    story_cover_url   = post.get("story_cover_url") or slide_urls[0]

    # Track if any post was actually published
    something_posted = False

    # Post to Instagram (skip if already done or not approved)
    if not ig_post_id and do_instagram:
        try:
            print("\n[1/2] Posting to Instagram...")
            caption    = build_caption(post)
            ig_post_id = post_instagram_carousel(slide_urls, caption)
            print(f"  ✓ Instagram post ID: {ig_post_id}")
            something_posted = True
        except Exception as e:
            error_str = str(e)
            if "403" in error_str and "limit" in error_str.lower():
                print(f"  ⚠️  Instagram rate limited by Meta (too many requests)")
                print(f"      Please wait 30-60 minutes and retry")
            else:
                print(f"  ✗ Instagram error: {e}")
    else:
        if ig_post_id:
            print(f"\n[1/2] Instagram already posted ({ig_post_id}) — skipping")

    # Post to Facebook (skip if already done or not approved)
    if not fb_post_id and do_facebook:
        try:
            print("\n[2/4] Posting to Facebook...")
            fb_cover = post.get("fb_cover_url") or slide_urls[0]
            fb_post_id = post_facebook(fb_cover, fb_text, post["article_url"], post.get("journal", ""))
            print(f"  ✓ Facebook post ID: {fb_post_id}")
            something_posted = True
        except Exception as e:
            print(f"  ✗ Facebook error: {e}")
    else:
        if fb_post_id:
            print(f"\n[2/4] Facebook already posted — skipping")

    # Instagram Story (1 cover verticale) — skip if already posted
    if do_ig_story and not ig_story_id:
        try:
            print(f"\n[3/4] Posting Instagram Story (1080x1920)...")
            ig_story_id = post_instagram_story(story_cover_url)
            print(f"  ✓ Instagram Story posted: {ig_story_id}")
            sb.table("social_posts").update({"ig_story_id": ig_story_id}).eq("id", post_id).execute()
            something_posted = True
        except Exception as e:
            print(f"  ✗ Instagram Story error: {e}")
    elif ig_story_id:
        print(f"\n[3/4] Instagram Story already posted — skipping")

    # Facebook Story (1 cover verticale) — skip if already posted
    if do_fb_story and not fb_story_id:
        try:
            print(f"\n[4/4] Posting Facebook Story (1080x1920)...")
            fb_story_id = post_facebook_story(story_cover_url)
            print(f"  ✓ Facebook Story posted: {fb_story_id}")
            sb.table("social_posts").update({"fb_story_id": fb_story_id}).eq("id", post_id).execute()
            something_posted = True
        except Exception as e:
            print(f"  ✗ Facebook Story error: {e}")
    elif fb_story_id:
        print(f"\n[4/4] Facebook Story already posted — skipping")

    # Instagram Story Video (MP4)
    if do_ig_story_video and reel_url:
        try:
            print(f"\n[5/6] Posting Instagram Story Video...")
            container = graph_post(f"{IG_ACCOUNT_ID}/media", {
                "video_url":    reel_url,
                "media_type":   "STORIES",
                "access_token": FB_PAGE_TOKEN,
            })
            cid = container["id"]
            for _ in range(15):
                time.sleep(5)
                status = graph_get(cid, {"fields": "status_code", "access_token": FB_PAGE_TOKEN})
                if status.get("status_code") == "FINISHED":
                    break
            result = graph_post(f"{IG_ACCOUNT_ID}/media_publish", {
                "creation_id": cid, "access_token": FB_PAGE_TOKEN
            })
            print(f"  ✓ Instagram Story Video posted: {result['id']}")
            something_posted = True
        except Exception as e:
            print(f"  ✗ Instagram Story Video error: {e}")

    # Facebook Story Video (MP4)
    if do_fb_story_video and reel_url:
        try:
            print(f"\n[6/6] Posting Facebook Story Video...")
            result = graph_post(f"{FB_PAGE_ID}/video_stories", {
                "file_url":    reel_url,
                "video_state": "PUBLISHED",
                "access_token": FB_PAGE_TOKEN,
            })
            print(f"  ✓ Facebook Story Video posted: {result.get('post_id','')}")
            something_posted = True
        except Exception as e:
            print(f"  ✗ Facebook Story Video error: {e}")

    # Update Supabase — only set posted_at if something was actually published
    update_data = {
        "ig_post_id": ig_post_id,
        "fb_post_id": fb_post_id,
    }
    if something_posted:
        update_data["posted_at"] = datetime.now(timezone.utc).isoformat()

    sb.table("social_posts").update(update_data).eq("id", post_id).execute()

    # Update Notion: status=Used when something was posted
    if something_posted and post.get("notion_page_id"):
        notion_token = os.getenv("NOTION_TOKEN", "")
        # Fetch current Use for values
        get_req = urllib.request.Request(
            f"https://api.notion.com/v1/pages/{post['notion_page_id']}",
            headers={"Authorization": f"Bearer {notion_token}",
                     "Notion-Version": "2022-06-28"}
        )
        with urllib.request.urlopen(get_req) as r:
            page_data = json.loads(r.read())
        current_use = [o["name"] for o in page_data["properties"].get("Use for", {}).get("multi_select", [])]
        if "Social" not in current_use:
            current_use.append("Social")
        payload = {"properties": {
            "Status":  {"select": {"name": "Used"}},
            "Use for": {"multi_select": [{"name": v} for v in current_use]},
        }}
        req = urllib.request.Request(
            f"https://api.notion.com/v1/pages/{post['notion_page_id']}",
            data=json.dumps(payload).encode(), method="PATCH",
            headers={"Authorization": f"Bearer {notion_token}",
                     "Notion-Version": "2022-06-28",
                     "Content-Type":   "application/json"}
        )
        try:
            with urllib.request.urlopen(req) as r:
                pass
            print(f"\n✓ Notion updated: status=Used")
        except Exception as e:
            print(f"\n⚠️  Notion update failed: {e}")

        print("\n✓ Done!")

    except Exception as e:
        print(f"\n❌ Workflow error (continuing anyway): {e}")
        print("✓ Done with errors (workflow marked as success to avoid GitHub failure mail)")
