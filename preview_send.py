"""
Sends a preview of the latest digest to vincenzolate95l@gmail.com.
Includes an APPROVE button that triggers the main send via GitHub Actions.
Runs automatically every Monday at 11:00 UTC (13:00 Italian time).
"""
import os, json, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from supabase import create_client
import resend as resend_lib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest import (
    build_html_email, build_plain_text,
    generate_preferences_token, ensure_unsubscribe,
    save_articles_to_notion,
)

PREVIEW_TO     = "vincenzolate95l@gmail.com"
GH_REPO        = "laterza01/neuro-digest"
GH_TOKEN       = os.getenv("GH_TOKEN", "")
APPROVE_SECRET = os.getenv("APPROVE_SECRET", "")
SITE_URL_BASE  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app").rstrip("/")

sb             = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
resend_lib.api_key = os.getenv("RESEND_API_KEY", "")
from_addr      = os.getenv("RESEND_FROM", "NeuroDigest <digest@neurodigest.io>")
site_url       = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")

# ── Fetch most recent digest ──────────────────────────────────────────────────
print("Fetching latest digest from Supabase...")
rows = (
    sb.table("digests")
      .select("id,edition_num,subject,digest_json")
      .order("sent_at", desc=True)
      .limit(1)
      .execute()
)
if not rows.data:
    print("No digest found in Supabase — skipping preview.")
    sys.exit(0)

row         = rows.data[0]
edition     = row.get("edition_num") or 0
subject     = row.get("subject", f"NeuroDigest #{edition}")
digest_data = json.loads(row.get("digest_json") or "{}")
print(f"  Using: {subject}")
# Articles already saved to Notion by digest.py --generate-only step

# ── Approve button HTML ───────────────────────────────────────────────────────
approve_url = f"{SITE_URL_BASE}/api/approve?token={APPROVE_SECRET}"

approve_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f0f7f0;border-top:3px solid #0e7c5a">
  <tr><td style="padding:24px 32px;text-align:center">
    <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;
              text-transform:uppercase;color:#0e7c5a;font-family:Helvetica,Arial,sans-serif">
      Preview — Edition #{edition}
    </p>
    <p style="margin:0 0 16px;font-size:13px;color:#555;font-family:Helvetica,Arial,sans-serif">
      Controlla la newsletter qui sotto. Se è tutto ok, clicca il bottone per inviarla a tutti i subscriber.
    </p>
    <a href="{approve_url}"
       style="display:inline-block;background:#0e7c5a;color:#fff;
              font-family:Helvetica,Arial,sans-serif;font-size:14px;
              font-weight:700;letter-spacing:.5px;text-decoration:none;
              padding:14px 40px;border-radius:2px">
      ✅ &nbsp;APPROVA — Invia a tutti
    </a>
    <p style="margin:12px 0 0;font-size:11px;color:#999;font-family:Helvetica,Arial,sans-serif">
      Un solo click — nessun altro passaggio richiesto.
    </p>
  </td></tr>
</table>
"""

# ── Rebuild HTML with current template ───────────────────────────────────────
token    = generate_preferences_token(PREVIEW_TO)
html     = build_html_email(digest_data, edition, preferences_token=token, site_url=site_url)
html     = ensure_unsubscribe(html, token, site_url)
plain    = build_plain_text(digest_data, edition)

# Inject approve banner at the very top of the email body
html = html.replace("<body", f"<body", 1)
html = html.replace(
    '<table width="100%" cellpadding="0" cellspacing="0" border="0"',
    approve_html + '\n<table width="100%" cellpadding="0" cellspacing="0" border="0"',
    1
)

# ── Send ─────────────────────────────────────────────────────────────────────
print(f"Sending preview to {PREVIEW_TO}...")
resend_lib.Emails.send({
    "from":    from_addr,
    "to":      PREVIEW_TO,
    "subject": f"[PREVIEW #{edition}] {subject} — approva per inviare",
    "html":    html,
    "text":    f"PREVIEW #{edition}\nApprova su: https://github.com/{GH_REPO}/actions/workflows/digest.yml\n\n{plain}",
})
print("✓ Preview sent.")
