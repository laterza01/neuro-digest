"""
Vercel endpoint — rifiuta il post social.
Deletes the rejected post + immediately generates a new article + sends new preview email (SAME DAY).
"""
import os, json, hmac, subprocess
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


def delete_post(supabase_url: str, supabase_key: str, post_id: str) -> bool:
    """Delete the post from social_posts table."""
    delete_url = f"{supabase_url}/rest/v1/social_posts?id=eq.{post_id}"
    req = urllib.request.Request(delete_url, method="DELETE")
    req.add_header("apikey", supabase_key)
    req.add_header("Authorization", f"Bearer {supabase_key}")
    req.add_header("Prefer", "return=minimal")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status in (200, 204)
    except Exception as e:
        print(f"Supabase error: {e}")
        return False


def trigger_new_article() -> bool:
    """Launch social_daily.py to generate a new article immediately."""
    try:
        result = subprocess.run(
            ["python3", "/var/task/social_daily.py"],
            capture_output=True,
            timeout=120,
            text=True
        )
        print(f"[trigger_new_article] exit code: {result.returncode}")
        print(f"[trigger_new_article] stdout: {result.stdout[:500]}")
        if result.stderr:
            print(f"[trigger_new_article] stderr: {result.stderr[:500]}")
        return result.returncode == 0
    except Exception as e:
        print(f"Failed to trigger new article: {e}")
        return False


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        approve_secret = os.getenv("SOCIAL_APPROVE_SECRET", "")
        supabase_url   = os.getenv("SUPABASE_URL", "")
        supabase_key   = os.getenv("SUPABASE_SERVICE_KEY", "")

        params  = parse_qs(urlparse(self.path).query)
        token    = params.get("token", [""])[0]
        post_id  = params.get("post_id", [""])[0]

        if not approve_secret or not hmac.compare_digest(token, approve_secret):
            self._respond(403, "❌ Invalid token.")
            return

        if not post_id:
            self._respond(400, "❌ Missing post_id.")
            return

        ok = delete_post(supabase_url, supabase_key, post_id)
        if ok:
            # Trigger new article generation immediately (same day)
            print("[social_reject] Post deleted, triggering new article generation...")
            trigger_ok = trigger_new_article()

            status_msg = "A new article will be sent shortly" if trigger_ok else "A new article will be generated tomorrow"
            status_color = "#0e7c5a" if trigger_ok else "#c0392b"

            self._respond(200, f"""
            <html><body style="font-family:Helvetica,Arial,sans-serif;
                               text-align:center;padding:60px 20px;background:#f4f3f0">
              <div style="max-width:480px;margin:0 auto;background:#fff;
                          padding:40px;border-top:4px solid {status_color}">
                <div style="font-size:48px;margin-bottom:16px">✋</div>
                <h2 style="color:#1a1a2e;margin:0 0 8px">Post rejected</h2>
                <p style="color:#555;font-size:14px;margin:0 0 8px">
                  This article won't be posted.
                </p>
                <p style="color:{status_color};font-size:18px;font-weight:700;margin:0">
                  {status_msg}
                </p>
                <p style="color:#aaa;font-size:12px;margin:16px 0 0">
                  You can close this page.
                </p>
              </div>
            </body></html>
            """, content_type="text/html")
        else:
            self._respond(500, "❌ Error deleting. Try again.")

    def _respond(self, code, body, content_type="text/plain"):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
