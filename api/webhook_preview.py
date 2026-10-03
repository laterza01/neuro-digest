"""
Vercel Cron endpoint (Monday 06:00 UTC).
Dispatches the preview.yml GitHub Actions workflow, which generates the digest
(weekly or Guidelines Edition) and emails the preview to the owner. Dispatch is
async, so it never hits the Vercel function timeout.
"""
import hmac
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler

WORKFLOW = "preview.yml"


def is_authorized(auth_header: str) -> bool:
    # Vercel Cron sends "Authorization: Bearer $CRON_SECRET"; external schedulers
    # (EasyCron) send WEBHOOK_SECRET. Accept either, and refuse if none is set.
    for name in ("CRON_SECRET", "WEBHOOK_SECRET"):
        secret = os.getenv(name, "")
        if secret and hmac.compare_digest(auth_header, f"Bearer {secret}"):
            return True
    return False


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not is_authorized(self.headers.get("Authorization", "")):
            self._respond(401, "Unauthorized")
            return

        gh_token = os.getenv("GH_TOKEN", "")
        if not gh_token:
            self._respond(500, "GH_TOKEN not configured")
            return

        url = f"https://api.github.com/repos/laterza01/neuro-digest/actions/workflows/{WORKFLOW}/dispatches"
        req = urllib.request.Request(url, data=json.dumps({"ref": "main"}).encode())
        req.add_header("Authorization", f"Bearer {gh_token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if r.status == 204:
                    print(f"[webhook_preview] ✓ {WORKFLOW} dispatched")
                    self._respond(200, f"✅ {WORKFLOW} dispatched")
                    return
                self._respond(500, f"GitHub returned {r.status}")
        except Exception as e:
            print(f"[webhook_preview] ✗ Error: {e}")
            self._respond(500, f"Error: {e}")

    do_POST = do_GET

    def _respond(self, code, body):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
