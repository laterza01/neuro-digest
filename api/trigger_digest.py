"""
Vercel Cron — Monday 12:00 UTC (14:00 Italian).
Triggers the GitHub Actions digest workflow (digest.py).
"""
import os, json, urllib.request
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        cron_secret = os.getenv("CRON_SECRET", "")
        auth_header = self.headers.get("Authorization", "")
        if cron_secret and auth_header != f"Bearer {cron_secret}":
            self._respond(401, "Unauthorized")
            return

        url  = "https://api.github.com/repos/laterza01/neuro-digest/actions/workflows/digest.yml/dispatches"
        data = json.dumps({"ref": "main"}).encode()
        req  = urllib.request.Request(url, data=data)
        req.add_header("Authorization", f"Bearer {os.getenv('GH_TOKEN','')}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                ok = r.status == 204
        except Exception as e:
            print(f"Error: {e}")
            ok = False

        if ok:
            self._respond(200, "Digest workflow triggered ✓")
        else:
            self._respond(500, "Failed to trigger digest workflow")

    def _respond(self, code, body):
        encoded = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
