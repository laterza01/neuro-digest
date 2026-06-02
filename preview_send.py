"""
Sends a preview of the latest digest to vincenzolate95l@gmail.com.
Triggered automatically by GitHub Actions when digest.py changes.
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
)

PREVIEW_TO = "vincenzolate95l@gmail.com"

sb            = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
resend_lib.api_key = os.getenv("RESEND_API_KEY", "")
from_addr     = os.getenv("RESEND_FROM", "NeuroDigest <digest@neurodigest.io>")
site_url      = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")

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

# ── Rebuild HTML with current template ───────────────────────────────────────
token = generate_preferences_token(PREVIEW_TO)
html  = build_html_email(digest_data, edition, preferences_token=token, site_url=site_url)
html  = ensure_unsubscribe(html, token, site_url)
plain = build_plain_text(digest_data, edition)

# ── Send ─────────────────────────────────────────────────────────────────────
print(f"Sending preview to {PREVIEW_TO}...")
resend_lib.Emails.send({
    "from":    from_addr,
    "to":      PREVIEW_TO,
    "subject": f"[PREVIEW] {subject}",
    "html":    html,
    "text":    plain,
})
print("✓ Preview sent.")
