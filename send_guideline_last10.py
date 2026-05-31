"""Send Alzheimer guideline to last 10 subscribers."""
import os, sys, time
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resend_alzheimer_guideline import GUIDELINE, SUBJECT
from digest import build_guidelines_html_email, generate_preferences_token

from supabase import create_client
import resend as resend_lib

sb        = create_client(os.getenv("SUPABASE_URL",""), os.getenv("SUPABASE_SERVICE_KEY",""))
site_url  = os.getenv("SITE_URL","https://neuro-digest-phi.vercel.app")
from_addr = os.getenv("RESEND_FROM","NeuroDigest <digest@neuro-digest.com>")
resend_lib.api_key = os.getenv("RESEND_API_KEY","")

subs = (
    sb.table("subscribers")
      .select("email")
      .eq("status","confirmed")
      .order("created_at", desc=True)
      .limit(10)
      .execute()
).data or []

print(f"Sending to {len(subs)} subscribers...")
for sub in subs:
    email = sub["email"]
    token = generate_preferences_token(email)
    html  = build_guidelines_html_email(GUIDELINE, token=token, site_url=site_url)
    try:
        resend_lib.Emails.send({
            "from": from_addr, "to": email,
            "subject": SUBJECT, "html": html,
        })
        print(f"  ✓ {email}")
        time.sleep(0.15)
    except Exception as e:
        print(f"  ✗ {email}: {e}")

print("\n✅ Done")
