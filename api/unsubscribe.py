"""GET /api/unsubscribe?token=... — mark subscriber as unsubscribed."""

from http.server import BaseHTTPRequestHandler
import os
import jwt
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
JWT_SECRET   = os.environ.get("JWT_SECRET", "change-me-in-env")
SITE_URL     = os.environ.get("SITE_URL", "https://neurodigest.vercel.app")

_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Unsubscribed — NeuroDigest</title>
</head>
<body style="margin:0;padding:0;background:#f7f7f5;font-family:Georgia,'Times New Roman',serif">
<div style="max-width:480px;margin:80px auto;text-align:center;padding:0 24px">
  <p style="font-size:24px;font-weight:700;color:#1a1a2e;margin:0 0 12px">NeuroDigest</p>
  <p style="font-size:16px;color:#555;line-height:1.7;margin:0 0 32px">
    You've been unsubscribed.<br>You won't receive any more emails from us.
  </p>
  <a href="/" style="font-size:13px;color:#c0392b;text-decoration:none">← Back to neurodigest.io</a>
</div>
</body></html>"""

_ERROR_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>NeuroDigest</title>
</head>
<body style="margin:0;padding:0;background:#f7f7f5;font-family:Georgia,'Times New Roman',serif">
<div style="max-width:480px;margin:80px auto;text-align:center;padding:0 24px">
  <p style="font-size:16px;color:#555">This unsubscribe link has expired or is invalid.
  <br><a href="/" style="color:#c0392b">Go to neurodigest.io</a></p>
</div>
</body></html>"""


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        qs    = parse_qs(urlparse(self.path).query)
        token = (qs.get("token") or [""])[0]

        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            email   = payload.get("sub", "")
        except Exception:
            self._html(_ERROR_PAGE, 400)
            return

        if not email:
            self._html(_ERROR_PAGE, 400)
            return

        try:
            from supabase import create_client
            sb = create_client(SUPABASE_URL, SUPABASE_KEY)
            sb.table("subscribers").update({"status": "unsubscribed"}).eq("email", email).execute()
        except Exception:
            self._html(_ERROR_PAGE, 500)
            return

        self._html(_PAGE, 200)

    def _html(self, content: str, status: int):
        body = content.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
