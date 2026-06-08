"""
Vercel endpoint — unsubscribe by email.
Removes the subscriber from Supabase.
"""
import os, json
import urllib.request
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):

    def do_POST(self):
        supabase_url = os.getenv("SUPABASE_URL", "")
        supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            data  = json.loads(body)
            email = data.get("email", "").strip().lower()
        except Exception:
            self._respond(400, {"error": "Invalid request."})
            return

        if not email or "@" not in email:
            self._respond(400, {"error": "Invalid email address."})
            return

        # Check subscriber exists
        check_url = (
            f"{supabase_url}/rest/v1/subscribers"
            f"?email=eq.{urllib.parse.quote(email)}&select=id,email,confirmed"
        )
        req = urllib.request.Request(check_url, headers={
            "apikey":        supabase_key,
            "Authorization": f"Bearer {supabase_key}",
        })
        try:
            with urllib.request.urlopen(req) as r:
                rows = json.loads(r.read())
        except Exception as e:
            self._respond(500, {"error": "Server error. Please try again."})
            return

        if not rows:
            self._respond(404, {"error": "Email not found in our list."})
            return

        # Delete subscriber
        del_url = (
            f"{supabase_url}/rest/v1/subscribers"
            f"?email=eq.{urllib.parse.quote(email)}"
        )
        req = urllib.request.Request(del_url, method="DELETE", headers={
            "apikey":        supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Prefer":        "return=minimal",
        })
        try:
            with urllib.request.urlopen(req) as r:
                pass
            self._respond(200, {"ok": True, "message": f"{email} has been unsubscribed."})
        except Exception as e:
            self._respond(500, {"error": "Could not remove email. Please try again."})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *args):
        pass


# Fix missing import
import urllib.parse
