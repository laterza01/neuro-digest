"""
Vercel endpoint — approva il post social.
Sets approved=True on the specified social_post.
"""
import os, json, hmac
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


def set_approved(supabase_url: str, supabase_key: str, post_id: str) -> bool:
    patch_url = f"{supabase_url}/rest/v1/social_posts?id=eq.{post_id}"
    data      = json.dumps({"approved": True}).encode()
    req       = urllib.request.Request(patch_url, data=data, method="PATCH")
    req.add_header("apikey", supabase_key)
    req.add_header("Authorization", f"Bearer {supabase_key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Prefer", "return=minimal")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status in (200, 204)
    except Exception as e:
        print(f"Supabase error: {e}")
        return False


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        approve_secret = os.getenv("SOCIAL_APPROVE_SECRET", "")
        supabase_url   = os.getenv("SUPABASE_URL", "")
        supabase_key   = os.getenv("SUPABASE_SERVICE_KEY", "")

        params  = parse_qs(urlparse(self.path).query)
        token   = params.get("token",   [""])[0]
        post_id = params.get("post_id", [""])[0]

        if not approve_secret or not hmac.compare_digest(token, approve_secret):
            self._respond(403, "❌ Invalid token.")
            return

        if not post_id:
            self._respond(400, "❌ Missing post_id.")
            return

        ok = set_approved(supabase_url, supabase_key, post_id)
        if ok:
            self._respond(200, """
            <html><body style="font-family:Helvetica,Arial,sans-serif;
                               text-align:center;padding:60px 20px;background:#f4f3f0">
              <div style="max-width:480px;margin:0 auto;background:#fff;
                          padding:40px;border-top:4px solid #0e7c5a">
                <div style="font-size:48px;margin-bottom:16px">✅</div>
                <h2 style="color:#1a1a2e;margin:0 0 8px">Post approvato</h2>
                <p style="color:#555;font-size:14px;margin:0 0 8px">
                  Will be posted to Instagram and Facebook
                </p>
                <p style="color:#0e7c5a;font-size:18px;font-weight:700;margin:0">
                  Today at 14:00
                </p>
                <p style="color:#aaa;font-size:12px;margin:16px 0 0">
                  You can close this page.
                </p>
              </div>
            </body></html>
            """, content_type="text/html")
        else:
            self._respond(500, "❌ Error saving. Try again.")

    def _respond(self, code, body, content_type="text/plain"):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
