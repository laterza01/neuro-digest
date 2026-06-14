#!/usr/bin/env python3
"""Quick script to send today's newsletter preview with social links."""
import os, sys, json
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from supabase import create_client
import resend as resend_lib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest import build_html_email, build_plain_text, generate_preferences_token, ensure_unsubscribe

# Config
PREVIEW_TO = "vincenzolate95l@gmail.com"
APPROVE_SECRET = os.getenv("APPROVE_SECRET", "")
SITE_URL = os.getenv("SITE_URL", "https://www.neuro-digest.com")

sb = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
resend_lib.api_key = os.getenv("RESEND_API_KEY", "")
from_addr = os.getenv("RESEND_FROM", "NeuroDigest <digest@neurodigest.io>")

# Fetch latest digest
print("Fetching latest digest from Supabase...")
rows = sb.table("digests").select("id,edition_num,subject,digest_json,html").order("sent_at", desc=True).limit(1).execute()

if not rows.data:
    print("❌ No digest found in Supabase.")
    sys.exit(1)

row = rows.data[0]
edition = row.get("edition_num", 0)
subject = row.get("subject", f"NeuroDigest #{edition}")
html = row.get("html", "")

# Add APPROVE button
approve_url = f"{SITE_URL}/api/approve?token={APPROVE_SECRET}"
approve_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f0f7f0;border-top:3px solid #0e7c5a">
  <tr><td style="padding:24px 32px;text-align:center">
    <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;
              text-transform:uppercase;color:#0e7c5a;font-family:Helvetica,Arial,sans-serif">
      PREVIEW — Edition #{edition}
    </p>
    <p style="margin:0 0 16px;font-size:13px;color:#555;font-family:Helvetica,Arial,sans-serif">
      ✅ Social icons added at the bottom. Clicca APPROVA per inviarla a tutti.
    </p>
    <a href="{approve_url}"
       style="display:inline-block;background:#0e7c5a;color:#fff;
              font-family:Helvetica,Arial,sans-serif;font-size:14px;
              font-weight:700;letter-spacing:.5px;text-decoration:none;
              padding:14px 40px;border-radius:2px">
      ✅ &nbsp;APPROVA — Invia a tutti
    </a>
  </td></tr>
</table>
"""

# Inject approve button before closing body tag
html_with_approve = html.replace("</body>", f"{approve_html}\n</body>")

# Send
print(f"Sending preview to {PREVIEW_TO}...")
try:
    result = resend_lib.Emails.send({
        "from": from_addr,
        "to": PREVIEW_TO,
        "subject": f"[PREVIEW #{edition}] {subject}",
        "html": html_with_approve,
    })
    print(f"✅ Preview sent! Message ID: {result.get('id')}")
except Exception as e:
    print(f"❌ Error sending preview: {e}")
    sys.exit(1)
