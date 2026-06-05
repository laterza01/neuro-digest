"""
Vercel endpoint called by Vercel Cron at 6:00 UTC (8:00 Italian).
Triggers the GitHub Actions social_daily workflow via workflow_dispatch.
"""
import os, json, urllib.request
from http.server import BaseHTTPRequestHandler


def trigger_github_workflow(repo: str, workflow: str, token: str) -> bool:
    url = f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches"
    data = json.dumps({"ref": "main"}).encode()
    req = urllib.request.Request(url, data=data)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 204
    except Exception as e:
        print(f"GitHub trigger error: {e}")
        return False


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        # Validate cron secret to prevent unauthorized calls
        cron_secret = os.getenv("CRON_SECRET", "")
        auth_header = self.headers.get("Authorization", "")

        if cron_secret and auth_header != f"Bearer {cron_secret}":
            self._respond(401, "Unauthorized")
            return

        gh_token  = os.getenv("GH_TOKEN", "")
        repo      = "laterza01/neuro-digest"
        workflow  = "social_daily.yml"

        ok = trigger_github_workflow(repo, workflow, gh_token)
        if ok:
            self._respond(200, "Social Daily workflow triggered ✓")
        else:
            self._respond(500, "Failed to trigger workflow")

    def _respond(self, code, body):
        encoded = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
