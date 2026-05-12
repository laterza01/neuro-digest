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


def _welcome_html() -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#f4f4f2">
<div style="font-family:Georgia,'Times New Roman',serif;max-width:540px;margin:48px auto;
            background:#fff;border:1px solid #e0e0de">
  <div style="padding:28px 40px 24px;border-bottom:3px solid #c0392b">
    <p style="margin:0;font-size:24px;font-weight:700;color:#1a1a2e">NeuroDigest</p>
    <p style="margin:4px 0 0;font-size:11px;letter-spacing:1.5px;text-transform:uppercase;
              color:#888;font-family:Helvetica,Arial,sans-serif">
      Weekly Neurology Literature Briefing
    </p>
  </div>
  <div style="padding:32px 40px">
    <p style="margin:0 0 16px;font-size:16px;color:#1a1a2e;line-height:1.6">
      You're subscribed to NeuroDigest.
    </p>
    <p style="margin:0 0 28px;font-size:15px;color:#444;line-height:1.75">
      Every Monday morning you'll receive a curated briefing of the most relevant
      neurology literature — synthesized from Q1 and Q2 journals across all major
      clinical areas.
    </p>
    <p style="margin:0 0 8px;font-size:14px;color:#444;line-height:1.6">
      You can customise which topics appear in your edition at any time from the
      preferences page you just visited.
    </p>
    <p style="margin:28px 0 0;font-size:11px;color:#bbb;font-family:Helvetica,Arial,sans-serif">
      neuro-digest.com · If you didn't subscribe, you can safely ignore this email.
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

            from supabase import create_client
            from datetime import datetime, timezone as tz
            sb = create_client(SUPABASE_URL, SUPABASE_KEY)

            existing = sb.table("subscribers").select("status").eq("email", email).execute()
            already  = existing.data and existing.data[0]["status"] == "confirmed"

            if not already:
                sb.table("subscribers").upsert(
                    {
                        "email":        email,
                        "status":       "confirmed",
                        "topics":       ALL_TOPICS,
                        "confirmed_at": datetime.now(tz.utc).isoformat(),
                    },
                    on_conflict="email",
                ).execute()

                import resend, time
                resend.api_key = RESEND_API_KEY

                # Send welcome email
                resend.Emails.send({
                    "from":    RESEND_FROM,
                    "to":      email,
                    "subject": "Welcome to NeuroDigest",
                    "html":    _welcome_html(),
                })

                # Send latest digest if available
                time.sleep(0.2)
                latest = sb.table("digests").select("subject,html,plain") \
                           .order("sent_at", desc=True).limit(1).execute()
                if latest.data:
                    d = latest.data[0]
                    resend.Emails.send({
                        "from":    RESEND_FROM,
                        "to":      email,
                        "subject": d["subject"],
                        "html":    d["html"],
                        "text":    d["plain"],
                    })

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
