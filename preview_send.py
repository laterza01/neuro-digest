"""
Sends a preview of the latest digest to vincenzolate95l@gmail.com.
Includes an APPROVE button that triggers the main send via GitHub Actions.
Runs automatically every Monday at 06:00 UTC (08:00 Italian time).
"""
import os, json, sys
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

from supabase import create_client
import resend as resend_lib

sys.path.insert(0, str(Path(__file__).resolve().parent))
from digest import (
    build_html_email, build_guidelines_html_email, build_plain_text,
    generate_preferences_token, ensure_unsubscribe,
    save_articles_to_notion,
)

PREVIEW_TO     = "vincenzolate95l@gmail.com"
GH_REPO        = "laterza01/neuro-digest"
GH_TOKEN       = os.getenv("GH_TOKEN", "")
APPROVE_SECRET = os.getenv("APPROVE_SECRET", "")
SITE_URL_BASE  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app").rstrip("/")

if not APPROVE_SECRET:
    sys.exit("APPROVE_SECRET is not set — refusing to send a preview with a broken APPROVE link.")

sb             = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
resend_lib.api_key = os.getenv("RESEND_API_KEY", "")
from_addr      = os.getenv("RESEND_FROM", "NeuroDigest <digest@neurodigest.io>")
site_url       = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")

# ── Fetch most recent digest ──────────────────────────────────────────────────
print("Fetching latest digest from Supabase...")
rows = (
    sb.table("digests")
      .select("id,edition_num,subject,digest_json,sent_at")
      .order("sent_at", desc=True)
      .limit(1)
      .execute()
)
if not rows.data:
    print("No digest found in Supabase — skipping preview.")
    sys.exit(0)

row         = rows.data[0]

# Never preview a stale digest (generation failed today) or one that already went
# out (late backup run after the 12:00 send): the APPROVE button acts on the
# latest digest, so a wrong preview could re-approve old content.
from datetime import datetime, timezone
from digest import get_already_sent
if (row.get("sent_at") or "")[:10] != datetime.now(timezone.utc).strftime("%Y-%m-%d"):
    print(f"Latest digest ({row.get('subject')}) was not generated today — no fresh digest to preview. Exiting.")
    sys.exit(1)
if get_already_sent(sb, row["id"]):
    print("Latest digest was already sent to subscribers — no preview needed. Exiting.")
    sys.exit(0)
edition     = row.get("edition_num") or 0
subject     = row.get("subject", f"NeuroDigest #{edition}")
digest_data = json.loads(row.get("digest_json") or "{}")
print(f"  Using: {subject}")
is_guidelines = subject.startswith("NeuroDigest Guidelines")
edition_label = "Guidelines Edition" if is_guidelines else f"Edition #{edition}"
# Articles already saved to Notion by digest.py --generate-only step

# ── Approve button HTML ───────────────────────────────────────────────────────
approve_url = f"{SITE_URL_BASE}/api/approve?token={APPROVE_SECRET}"

approve_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f0f7f0;border-top:3px solid #0e7c5a">
  <tr><td style="padding:24px 32px;text-align:center">
    <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;
              text-transform:uppercase;color:#0e7c5a;font-family:Helvetica,Arial,sans-serif">
      Preview — {edition_label}
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
if is_guidelines:
    html  = build_guidelines_html_email(digest_data, token=token, site_url=site_url)
    plain = f"{subject}\n\n{digest_data.get('bottom_line', '')}"
else:
    html  = build_html_email(digest_data, edition, preferences_token=token, site_url=site_url)
    plain = build_plain_text(digest_data, edition)
html     = ensure_unsubscribe(html, token, site_url)

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
    "subject": f"[PREVIEW {edition_label}] {subject} — approva per inviare",
    "html":    html,
    "text":    f"PREVIEW {edition_label}\nApprova su: https://github.com/{GH_REPO}/actions/workflows/digest.yml\n\n{plain}",
})
print("✓ Preview sent.")
