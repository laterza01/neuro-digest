"""
Vercel endpoint — approva la newsletter.
Salva approved=true e triggera immediatamente l'invio via GitHub Actions.
"""
import os, json, hmac
import urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


def set_approved(supabase_url: str, supabase_key: str) -> bool:
    url = f"{supabase_url}/rest/v1/digests?select=id&order=sent_at.desc&limit=1"
    req = urllib.request.Request(url, headers={
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
    })
    try:
        with urllib.request.urlopen(req) as r:
            rows = json.loads(r.read())
        if not rows:
            return False
        digest_id = rows[0]["id"]
        patch_url = f"{supabase_url}/rest/v1/digests?id=eq.{digest_id}"
        data  = json.dumps({"approved": True}).encode()
        patch = urllib.request.Request(patch_url, data=data, method="PATCH")
        patch.add_header("apikey", supabase_key)
        patch.add_header("Authorization", f"Bearer {supabase_key}")
        patch.add_header("Content-Type", "application/json")
        patch.add_header("Prefer", "return=minimal")
        with urllib.request.urlopen(patch) as r:
            return r.status in (200, 204)
    except Exception as e:
        print(f"Supabase error: {e}")
        return False


def trigger_digest(gh_token: str) -> bool:
    """Triggera immediatamente il workflow digest su GitHub Actions."""
    url  = "https://api.github.com/repos/laterza01/neuro-digest/actions/workflows/digest.yml/dispatches"
    data = json.dumps({"ref": "main"}).encode()
    req  = urllib.request.Request(url, data=data)
    req.add_header("Authorization", f"Bearer {gh_token}")
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
        approve_secret = os.getenv("APPROVE_SECRET", "")
        supabase_url   = os.getenv("SUPABASE_URL", "")
        supabase_key   = os.getenv("SUPABASE_SERVICE_KEY", "")
        gh_token       = os.getenv("GH_TOKEN", "")

        params = parse_qs(urlparse(self.path).query)
        token  = params.get("token", [""])[0]

        if not approve_secret or not hmac.compare_digest(token, approve_secret):
            self._respond(403, "❌ Token non valido.")
            return

        ok = set_approved(supabase_url, supabase_key)
        if ok:
            trigger_digest(gh_token)
            self._respond(200, """
            <html><body style="font-family:Helvetica,Arial,sans-serif;
                               text-align:center;padding:60px 20px;background:#f4f3f0">
              <div style="max-width:480px;margin:0 auto;background:#fff;
                          padding:40px;border-top:4px solid #0e7c5a">
                <div style="font-size:48px;margin-bottom:16px">✅</div>
                <h2 style="color:#1a1a2e;margin:0 0 8px">Newsletter approvata</h2>
                <p style="color:#555;font-size:14px;margin:0 0 8px">
                  Invio in corso a tutti i subscriber.
                </p>
                <p style="color:#aaa;font-size:12px;margin:16px 0 0">
                  Puoi chiudere questa pagina.
                </p>
              </div>
            </body></html>
            """, content_type="text/html")
        else:
            self._respond(500, "❌ Errore nel salvataggio. Riprova.")

    def _respond(self, code, body, content_type="text/plain"):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
