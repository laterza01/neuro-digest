"""
Webhook endpoint for EasyCron (or Vercel Cron).
EasyCron calls this every day at 12:00 UTC to publish approved social posts.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler

# Add project root to path so we can import social_post
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import social_post


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Verify request came from EasyCron
        auth_header = self.headers.get("Authorization", "")
        webhook_secret = os.getenv("WEBHOOK_SECRET", "")

        if webhook_secret and auth_header != f"Bearer {webhook_secret}":
            self._respond(401, "Unauthorized")
            return

        print("[webhook_post] EasyCron triggered social_post publication")

        try:
            # Execute the main workflow by running social_post.py's logic
            if __name__ == "__main__":
                # Call the main block code directly
                import json, urllib.request, time
                from pathlib import Path
                from datetime import datetime, timezone
                from dotenv import load_dotenv

                load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)
                from supabase import create_client

                sb = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))

                # Fetch posts where at least one platform was EXPLICITLY approved by the user
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
                    self._respond(200, "✅ No approved posts to publish")
                    return

                post = rows.data[0]
                post_id    = post["id"]
                slide_urls = post["slide_urls"]
                fb_text    = post["fb_text"]

                print(f"[webhook_post] Processing post: {post['article_title'][:60]}")

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
                        print("[webhook_post] Posting to Instagram...")
                        caption    = social_post.build_caption(post)
                        ig_post_id = social_post.post_instagram_carousel(slide_urls, caption)
                        print(f"[webhook_post] Instagram post ID: {ig_post_id}")
                        something_posted = True
                    except Exception as e:
                        error_str = str(e)
                        if "403" in error_str and "limit" in error_str.lower():
                            print(f"[webhook_post] Instagram rate limited — retrying later")
                        else:
                            print(f"[webhook_post] Instagram error: {e}")

                # Post to Facebook (skip if already done or not approved)
                if not fb_post_id and do_facebook:
                    try:
                        print("[webhook_post] Posting to Facebook...")
                        fb_cover = post.get("fb_cover_url") or slide_urls[0]
                        fb_post_id = social_post.post_facebook(fb_cover, fb_text, post["article_url"], post.get("journal", ""))
                        print(f"[webhook_post] Facebook post ID: {fb_post_id}")
                        something_posted = True
                    except Exception as e:
                        print(f"[webhook_post] Facebook error: {e}")

                # Instagram Story (1 cover verticale) — skip if already posted
                if do_ig_story and not ig_story_id:
                    try:
                        print(f"[webhook_post] Posting Instagram Story...")
                        ig_story_id = social_post.post_instagram_story(story_cover_url)
                        print(f"[webhook_post] Instagram Story posted: {ig_story_id}")
                        sb.table("social_posts").update({"ig_story_id": ig_story_id}).eq("id", post_id).execute()
                        something_posted = True
                    except Exception as e:
                        print(f"[webhook_post] Instagram Story error: {e}")

                # Facebook Story (1 cover verticale) — skip if already posted
                if do_fb_story and not fb_story_id:
                    try:
                        print(f"[webhook_post] Posting Facebook Story...")
                        fb_story_id = social_post.post_facebook_story(story_cover_url)
                        print(f"[webhook_post] Facebook Story posted: {fb_story_id}")
                        sb.table("social_posts").update({"fb_story_id": fb_story_id}).eq("id", post_id).execute()
                        something_posted = True
                    except Exception as e:
                        print(f"[webhook_post] Facebook Story error: {e}")

                # Instagram Story Video (MP4)
                if do_ig_story_video and reel_url:
                    try:
                        print(f"[webhook_post] Posting Instagram Story Video...")
                        container = social_post.graph_post(f"{social_post.IG_ACCOUNT_ID}/media", {
                            "video_url":    reel_url,
                            "media_type":   "STORIES",
                            "access_token": social_post.FB_PAGE_TOKEN,
                        })
                        cid = container["id"]
                        for _ in range(15):
                            time.sleep(5)
                            status = social_post.graph_get(cid, {"fields": "status_code", "access_token": social_post.FB_PAGE_TOKEN})
                            if status.get("status_code") == "FINISHED":
                                break
                        result = social_post.graph_post(f"{social_post.IG_ACCOUNT_ID}/media_publish", {
                            "creation_id": cid, "access_token": social_post.FB_PAGE_TOKEN
                        })
                        print(f"[webhook_post] Instagram Story Video posted: {result['id']}")
                        something_posted = True
                    except Exception as e:
                        print(f"[webhook_post] Instagram Story Video error: {e}")

                # Facebook Story Video (MP4)
                if do_fb_story_video and reel_url:
                    try:
                        print(f"[webhook_post] Posting Facebook Story Video...")
                        result = social_post.graph_post(f"{social_post.FB_PAGE_ID}/video_stories", {
                            "file_url":    reel_url,
                            "video_state": "PUBLISHED",
                            "access_token": social_post.FB_PAGE_TOKEN,
                        })
                        print(f"[webhook_post] Facebook Story Video posted: {result.get('post_id','')}")
                        something_posted = True
                    except Exception as e:
                        print(f"[webhook_post] Facebook Story Video error: {e}")

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
                        print(f"[webhook_post] Notion updated: status=Used")
                    except Exception as e:
                        print(f"[webhook_post] Notion update failed: {e}")

                self._respond(200, "✅ Posts published successfully")

        except Exception as e:
            print(f"[webhook_post] Error: {e}")
            import traceback
            traceback.print_exc()
            self._respond(500, f"❌ Error: {str(e)}")

    def do_GET(self):
        # Health check
        self._respond(200, "Webhook is active")

    def _respond(self, code, body):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
