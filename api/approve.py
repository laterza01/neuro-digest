"""
Vercel serverless endpoint — approve newsletter send.
Called when Vincenzo clicks APPROVE in the preview email.
Validates the secret token then triggers the GitHub Actions workflow.
"""
import os, json, hmac, hashlib
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


GH_REPO   = "laterza01/neuro-digest"
WORKFLOW  = "digest.yml"


def trigger_workflow(gh_token: str) -> bool:
    url  = f"https://api.github.com/repos/{GH_REPO}/actions/workflows/{WORKFLOW}/dispatches"
    data = json.dumps({"ref": "main"}).encode()
    req  = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {gh_token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status == 204
    except urllib.error.HTTPError as e:
        print(f"GitHub API error: {e.code} {e.read()}")
        return False


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        approve_secret = os.getenv("APPROVE_SECRET", "")
        gh_token       = os.getenv("GH_TOKEN", "")

        params = parse_qs(urlparse(self.path).query)
        token  = params.get("token", [""])[0]

        # Validate secret
        if not approve_secret or not hmac.compare_digest(token, approve_secret):
            self._respond(403, "❌ Token non valido.")
            return

        # Trigger workflow
        ok = trigger_workflow(gh_token)
        if ok:
            self._respond(200, """
            <html><body style="font-family:Helvetica,Arial,sans-serif;
                               text-align:center;padding:60px 20px;background:#f4f3f0">
              <div style="max-width:480px;margin:0 auto;background:#fff;
                          padding:40px;border-top:4px solid #0e7c5a">
                <div style="font-size:48px;margin-bottom:16px">✅</div>
                <h2 style="color:#1a1a2e;margin:0 0 8px">Newsletter approvata</h2>
                <p style="color:#888;font-size:14px;margin:0">
                  L'invio a tutti i subscriber è partito.<br>
                  Puoi chiudere questa pagina.
                </p>
              </div>
            </body></html>
            """, content_type="text/html")
        else:
            self._respond(500, "❌ Errore nel trigger del workflow. Controlla GitHub Actions.")

    def _respond(self, code, body, content_type="text/plain"):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
