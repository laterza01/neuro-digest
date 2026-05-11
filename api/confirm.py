"""GET /api/confirm?token=... — verify JWT, mark subscriber confirmed, redirect."""

from http.server import BaseHTTPRequestHandler
import os
import jwt
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs

SUPABASE_URL  = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY  = os.environ.get("SUPABASE_SERVICE_KEY", "")
JWT_SECRET    = os.environ.get("JWT_SECRET", "change-me-in-env")
SITE_URL      = os.environ.get("SITE_URL", "https://neurodigest.vercel.app")


def _fresh_token(email: str) -> str:
    payload = {
        "sub": email,
        "purpose": "preferences",
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        qs    = parse_qs(urlparse(self.path).query)
        token = (qs.get("token") or [""])[0]

        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            email   = payload.get("sub", "")
            purpose = payload.get("purpose", "")
        except jwt.ExpiredSignatureError:
            self._redirect(f"{SITE_URL}/?msg=link-expired")
            return
        except Exception:
            self._redirect(f"{SITE_URL}/?msg=invalid-link")
            return

        if purpose != "confirm" or not email:
            self._redirect(f"{SITE_URL}/?msg=invalid-link")
            return

        try:
            from supabase import create_client
            sb = create_client(SUPABASE_URL, SUPABASE_KEY)
            sb.table("subscribers").update({
                "status":       "confirmed",
                "confirmed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("email", email).execute()
        except Exception as e:
            self._redirect(f"{SITE_URL}/?msg=error")
            return

        prefs_token = _fresh_token(email)
        self._redirect(f"{SITE_URL}/preferences?token={prefs_token}")

    def _redirect(self, url: str):
        self.send_response(302)
        self.send_header("Location", url)
        self.end_headers()
