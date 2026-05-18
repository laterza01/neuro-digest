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


def _welcome_html(token: str = "", site_url: str = "") -> str:
    manage_link = (
        f'<a href="{site_url}/preferences?token={token}" '
        f'style="color:#555;text-decoration:none">Manage topics</a>'
        if token else ""
    )
    unsub_link = (
        f'<a href="{site_url}/api/unsubscribe?token={token}" '
        f'style="color:#555;text-decoration:none">Unsubscribe</a>'
        if token else ""
    )
    sep = ' &nbsp;&middot;&nbsp; ' if manage_link and unsub_link else ''

    mobile_css = """<style>
@media only screen and (max-width:620px){
  .ep{padding-left:20px!important;padding-right:20px!important}
  .eh{padding:18px 20px!important}
  .ft{padding:16px 20px!important}
  .bt{font-size:16px!important;line-height:1.8!important}
}
</style>"""

    preheader = (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        'font-size:1px;line-height:1px;color:#1a1a2e">'
        'Welcome to NeuroDigest — your weekly neurology literature briefing starts Monday.'
        + '&#847;' * 60 +
        '</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Welcome to NeuroDigest</title>
{mobile_css}
</head>
<body style="margin:0;padding:0;background:#f4f4f2;-webkit-text-size-adjust:100%">
{preheader}
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f4f4f2;min-height:100%">
<tr><td align="center" style="padding:24px 8px">

  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="max-width:640px;background:#ffffff;border:1px solid #ddddd8">

    <!-- Masthead -->
    <tr><td class="eh" style="padding:22px 48px 20px;background:#1a1a2e">
      <p style="margin:0;font-size:28px;font-weight:700;color:#ffffff;
                letter-spacing:-0.5px;font-family:Georgia,'Times New Roman',serif">NeuroDigest</p>
      <p style="margin:5px 0 0;font-size:10px;letter-spacing:2px;text-transform:uppercase;
                color:#c0392b;font-family:Helvetica,Arial,sans-serif">
        Weekly Neurology Literature Briefing
      </p>
    </td></tr>
    <!-- Red rule -->
    <tr><td style="height:3px;background:#c0392b;font-size:0;line-height:0">&nbsp;</td></tr>

    <!-- Body -->
    <tr><td class="ep" style="padding:36px 48px 0">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="width:4px;background:#c0392b;border-radius:2px" width="4"></td>
          <td style="padding:2px 0 0 22px">
            <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2.5px;
                      text-transform:uppercase;color:#c0392b;
                      font-family:Helvetica,Arial,sans-serif">Welcome</p>
            <p style="margin:0 0 20px;font-size:21px;font-weight:700;color:#1a1a2e;
                      line-height:1.3;font-family:Georgia,'Times New Roman',serif">
              You're subscribed to NeuroDigest.
            </p>
            <p class="bt" style="margin:0 0 14px;font-size:15px;color:#2c2c2c;line-height:1.8;
                      font-family:Georgia,'Times New Roman',serif">
              Every Monday morning you'll receive a curated briefing of the most relevant
              neurology literature — synthesized from Q1 and Q2 journals across all major
              clinical areas.
            </p>
            <p class="bt" style="margin:0 0 14px;font-size:15px;color:#2c2c2c;line-height:1.8;
                      font-family:Georgia,'Times New Roman',serif">
              On the last Monday of each month you'll also receive a special
              <strong>Guidelines Edition</strong> — a focused, practice-ready synthesis
              of current guidelines for a specific neurological condition.
            </p>
            <p class="bt" style="margin:0;font-size:15px;color:#2c2c2c;line-height:1.8;
                      font-family:Georgia,'Times New Roman',serif">
              You can customise which topics appear in your edition at any time
              from the preferences page.
            </p>
          </td>
        </tr>
      </table>
    </td></tr>

    <tr><td style="padding:16px 0 0"></td></tr>

    <!-- Take-home block -->
    <tr><td style="padding:32px 48px;background:#1a1a2e">
      <p style="margin:0 0 10px;font-size:9px;font-weight:700;letter-spacing:2.5px;
                text-transform:uppercase;color:#c0392b;
                font-family:Helvetica,Arial,sans-serif">Your first issue</p>
      <p style="margin:0;font-size:17px;color:#ffffff;line-height:1.7;
                font-family:Georgia,'Times New Roman',serif;font-style:italic">
        Your next NeuroDigest arrives next Monday at 14:30 your local time.
      </p>
    </td></tr>

    <!-- Footer -->
    <tr><td class="ft" style="padding:22px 48px;background:#f5f5f3;
                               border-top:2px solid #e0e0dc">
      <p style="margin:0;font-size:13px;color:#555;
                font-family:Helvetica,Arial,sans-serif;line-height:2;text-align:center">
        <strong style="color:#333;font-family:Georgia,'Times New Roman',serif">
          NeuroDigest
        </strong>
        &nbsp;&middot;&nbsp;
        {manage_link}{sep}{unsub_link}
      </p>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>"""


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
                # Accept IANA timezone string auto-detected by the browser.
                # Fall back to 'Europe/Rome' if missing or clearly invalid.
                raw_tz   = body.get("timezone", "").strip()
                timezone = raw_tz if raw_tz and len(raw_tz) < 64 else "Europe/Rome"

                sb.table("subscribers").upsert(
                    {
                        "email":        email,
                        "status":       "confirmed",
                        "topics":       ALL_TOPICS,
                        "confirmed_at": datetime.now(tz.utc).isoformat(),
                        "timezone":     timezone,
                    },
                    on_conflict="email",
                ).execute()

                import resend, time
                resend.api_key = RESEND_API_KEY

                # Generate token once — used in all emails for manage/unsubscribe links
                signup_token = _make_token(email, "preferences")

                # Send welcome email
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                from digest import ensure_unsubscribe
                welcome_html = _welcome_html(token=signup_token, site_url=SITE_URL)
                welcome_html = ensure_unsubscribe(welcome_html, signup_token, SITE_URL)
                resend.Emails.send({
                    "from":    RESEND_FROM,
                    "to":      email,
                    "subject": "Welcome to NeuroDigest",
                    "html":    welcome_html,
                })

                # Send latest digest — personalised by the subscriber's topics
                time.sleep(0.2)
                latest = sb.table("digests").select("subject,html,plain,digest_json") \
                           .not_.ilike("subject", "%guideline%") \
                           .order("sent_at", desc=True).limit(1).execute()
                if latest.data:
                    d         = latest.data[0]
                    send_html = d["html"]
                    send_plain = d.get("plain", "")
                    if d.get("digest_json"):
                        try:
                            import json as _json, sys, os
                            sys.path.insert(0, os.path.dirname(os.path.dirname(
                                os.path.abspath(__file__))))
                            from digest import (filter_digest_for_subscriber,
                                                build_html_email, build_plain_text)
                            digest_data            = _json.loads(d["digest_json"])
                            sub_topics             = ALL_TOPICS
                            personalized, excluded = filter_digest_for_subscriber(
                                digest_data, sub_topics)
                            show_also  = excluded if len(sub_topics) <= 2 else []
                            send_html  = build_html_email(
                                personalized, edition=0,
                                preferences_token=signup_token,
                                site_url=SITE_URL,
                                also_this_week=show_also,
                            )
                            send_plain = build_plain_text(personalized, 0)
                        except Exception:
                            pass
                    # Safety: always inject unsubscribe link if missing
                    unsub_url = f"{SITE_URL}/api/unsubscribe?token={signup_token}"
                    manage_url = f"{SITE_URL}/preferences?token={signup_token}"
                    if unsub_url not in send_html:
                        inject = (
                            f'<p style="text-align:center;font-size:11px;color:#aaa;'
                            f'font-family:Helvetica,Arial,sans-serif;margin:16px 0">'
                            f'<a href="{manage_url}" style="color:#999;text-decoration:none">Manage topics</a>'
                            f' &nbsp;&middot;&nbsp; '
                            f'<a href="{unsub_url}" style="color:#999;text-decoration:none">Unsubscribe</a>'
                            f'</p>'
                        )
                        send_html = send_html.replace("</body>", inject + "</body>")
                    resend.Emails.send({
                        "from":    RESEND_FROM,
                        "to":      email,
                        "subject": d["subject"],
                        "html":    send_html,
                        "text":    send_plain,
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
