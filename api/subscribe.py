"""POST /api/subscribe — create pending subscriber and send confirmation email."""

from http.server import BaseHTTPRequestHandler
import json
import os
import jwt
from datetime import datetime, timedelta, timezone

SUPABASE_URL     = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY     = os.environ.get("SUPABASE_SERVICE_KEY", "")
RESEND_API_KEY   = os.environ.get("RESEND_API_KEY", "")
JWT_SECRET       = os.environ.get("JWT_SECRET", "change-me-in-env")
SITE_URL         = os.environ.get("SITE_URL", "https://neurodigest.vercel.app")
RESEND_FROM      = os.environ.get("RESEND_FROM", "NeuroDigest <onboarding@resend.dev>")

ALL_TOPICS = [
    "Multiple Sclerosis", "Stroke", "Parkinson's Disease", "Epilepsy",
    "Dementia", "Headache", "Neuromuscular", "Neuro-oncology",
    "Neuroinflammation", "Movement Disorders", "Neurocritical Care", "Neurogenetics",
]


def _make_token(email: str, purpose: str = "confirm") -> str:
    payload = {
        "sub": email,
        "purpose": purpose,
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _confirmation_html(confirm_url: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f7f7f5">
<div style="font-family:Georgia,'Times New Roman',serif;max-width:520px;margin:48px auto;
            background:#fff;border-radius:4px;overflow:hidden;
            box-shadow:0 2px 12px rgba(0,0,0,.08)">
  <div style="background:#1a1a2e;padding:24px 32px">
    <p style="margin:0;font-size:20px;font-weight:700;color:#fff">NeuroDigest</p>
    <p style="margin:4px 0 0;font-size:12px;color:#8899bb;font-family:Helvetica,Arial,sans-serif">
      Weekly Neurology Literature Briefing
    </p>
  </div>
  <div style="padding:32px">
    <p style="margin:0 0 20px;font-size:16px;color:#1a1a2e;line-height:1.6">
      Confirm your subscription to start receiving the weekly neurology digest.
    </p>
    <p style="margin:0 0 28px;font-size:15px;color:#555;line-height:1.7">
      After confirming you can choose which clinical areas to include in your edition.
    </p>
    <a href="{confirm_url}"
       style="display:inline-block;background:#c0392b;color:#fff;padding:13px 28px;
              border-radius:3px;text-decoration:none;font-family:Helvetica,Arial,sans-serif;
              font-size:14px;font-weight:600;letter-spacing:.3px">
      Confirm subscription
    </a>
    <p style="margin:28px 0 0;font-size:11px;color:#bbb;font-family:Helvetica,Arial,sans-serif">
      Link valid for 7 days. If you didn't request this, ignore this email.
    </p>
  </div>
</div>
</body></html>"""


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # suppress default request log

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b"{}")
            email  = body.get("email", "").strip().lower()

            if not email or "@" not in email or "." not in email.split("@")[-1]:
                self._json({"error": "Invalid email address"}, 400)
                return

            # Upsert subscriber (pending, all topics by default)
            from supabase import create_client
            sb = create_client(SUPABASE_URL, SUPABASE_KEY)
            existing = sb.table("subscribers").select("status").eq("email", email).execute()
            if existing.data and existing.data[0]["status"] == "confirmed":
                # Already confirmed — just send them to preferences
                token = _make_token(email, "preferences")
                self._json({"ok": True, "already_confirmed": True,
                            "preferences_url": f"{SITE_URL}/preferences?token={token}"})
                return

            sb.table("subscribers").upsert(
                {"email": email, "status": "pending", "topics": ALL_TOPICS},
                on_conflict="email",
            ).execute()

            # Send confirmation email via Resend
            confirm_token = _make_token(email, "confirm")
            confirm_url   = f"{SITE_URL}/api/confirm?token={confirm_token}"

            import resend
            resend.api_key = RESEND_API_KEY
            resend.Emails.send({
                "from":    RESEND_FROM,
                "to":      email,
                "subject": "Confirm your NeuroDigest subscription",
                "html":    _confirmation_html(confirm_url),
            })

            # Return a preferences token so the frontend can redirect immediately
            prefs_token = _make_token(email, "preferences")
            self._json({"ok": True,
                        "preferences_url": f"{SITE_URL}/preferences?token={prefs_token}"})

        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
