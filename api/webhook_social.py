"""
Webhook endpoint called by EasyCron every day at 6:00 UTC.
Triggers GitHub Actions social_daily workflow — Playwright runs there, not on Vercel.
"""
import os, json, urllib.request
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        auth_header = self.headers.get("Authorization", "")
        webhook_secret = os.getenv("WEBHOOK_SECRET", "")

        if webhook_secret and auth_header != f"Bearer {webhook_secret}":
            self._respond(401, "Unauthorized")
            return

        gh_token = os.getenv("GH_TOKEN", "")
        if not gh_token:
            self._respond(500, "GH_TOKEN not configured")
            return

        url  = "https://api.github.com/repos/laterza01/neuro-digest/actions/workflows/social_daily.yml/dispatches"
        data = json.dumps({"ref": "main"}).encode()
        req  = urllib.request.Request(url, data=data)
        req.add_header("Authorization", f"Bearer {gh_token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status == 204:
                    print("[webhook_social] ✓ GitHub Actions social_daily triggered")
                    self._respond(200, "✅ social_daily workflow triggered")
                else:
                    self._respond(500, f"GitHub returned {r.status}")
        except Exception as e:
            print(f"[webhook_social] ✗ Error: {e}")
            self._respond(500, f"Error: {str(e)}")

    def do_GET(self):
        self._respond(200, "Webhook active")

    def _respond(self, code, body):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
