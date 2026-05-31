"""
Save Alzheimer guideline to Supabase and send to the last 10 subscribers
who haven't received it yet.
"""
import json, os, sys, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resend_alzheimer_guideline import GUIDELINE, SUBJECT
from digest import build_guidelines_html_email, generate_preferences_token

from supabase import create_client
import resend as resend_lib

sb        = create_client(os.getenv("SUPABASE_URL",""), os.getenv("SUPABASE_SERVICE_KEY",""))
site_url  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")
from_addr = os.getenv("RESEND_FROM", "NeuroDigest <digest@neuro-digest.com>")
resend_lib.api_key = os.getenv("RESEND_API_KEY","")

# ── 1. Save to Supabase ───────────────────────────────────────────────────────
existing = sb.table("digests").select("id").eq("subject", SUBJECT).execute()
if existing.data:
    digest_id = existing.data[0]["id"]
    print(f"Already in Supabase (id={digest_id})")
else:
    html = build_guidelines_html_email(GUIDELINE, site_url=site_url)
    row  = sb.table("digests").insert({
        "subject":     SUBJECT,
        "html":        html,
        "plain":       f"NeuroDigest Guidelines — Alzheimer's Disease\n\n{GUIDELINE['bottom_line']}",
        "digest_json": json.dumps(GUIDELINE),
    }).execute()
    digest_id = row.data[0]["id"]
    print(f"✅  Saved to Supabase (id={digest_id})")

# ── 2. Send to last 10 subscribers who haven't received it ───────────────────
all_subs = (
    sb.table("subscribers")
      .select("email,topics")
      .eq("status", "confirmed")
      .order("created_at", desc=True)
      .limit(10)
      .execute()
).data or []

already_sent = {
    r["email"] for r in
    (sb.table("sends_log").select("email").eq("digest_id", digest_id).execute().data or [])
}

to_send = [s for s in all_subs if s["email"] not in already_sent]
print(f"  {len(to_send)} of last 10 subscribers haven't received it")

sent_addrs = []
for sub in to_send:
    email = sub["email"]
    token = generate_preferences_token(email)
    html  = build_guidelines_html_email(GUIDELINE, token=token, site_url=site_url)
    try:
        resend_lib.Emails.send({
            "from": from_addr, "to": email,
            "subject": SUBJECT, "html": html,
        })
        sent_addrs.append(email)
        print(f"  ✓ {email}")
        time.sleep(0.15)
    except Exception as e:
        print(f"  ✗ {email}: {e}")

if sent_addrs:
    sb.table("sends_log").upsert(
        [{"email": e, "digest_id": digest_id} for e in sent_addrs],
        on_conflict="email,digest_id", ignore_duplicates=True,
    ).execute()
    try:
        sb.table("guidelines_log").insert({
            "macro_topic":    GUIDELINE["macro_topic"],
            "specific_topic": GUIDELINE["specific_topic"],
        }).execute()
    except Exception:
        pass

print(f"\n✅  Done — sent to {len(sent_addrs)} subscribers")
