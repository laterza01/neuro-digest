"""Test: send the new-subscriber sequence (welcome + digest) to vincenzo."""
import json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "api"))

TEST_EMAIL = "vincenzolate95l@gmail.com"
ALL_TOPICS = [
    "Multiple Sclerosis", "Stroke", "Parkinson's Disease", "Epilepsy",
    "Dementia", "Headache", "Neuromuscular", "Neuro-oncology",
    "Neuroinflammation", "Movement Disorders", "Neurocritical Care", "Neurogenetics",
]
SITE_URL  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")
RESEND_FROM = os.getenv("RESEND_FROM", "NeuroDigest <digest@neuro-digest.com>")

import resend as resend_lib
import jwt
resend_lib.api_key = os.getenv("RESEND_API_KEY", "")

from subscribe import _welcome_html
from digest import (filter_digest_for_subscriber, build_html_email,
                    build_plain_text, ensure_unsubscribe)
from supabase import create_client
sb = create_client(os.getenv("SUPABASE_URL",""), os.getenv("SUPABASE_SERVICE_KEY",""))

def make_token(email):
    secret = os.getenv("JWT_SECRET", "")
    payload = {"sub": email, "purpose": "preferences",
               "exp": datetime.now(timezone.utc) + timedelta(days=7)}
    return jwt.encode(payload, secret, algorithm="HS256")

token = make_token(TEST_EMAIL)

# ── 1. Welcome ────────────────────────────────────────────────────────────────
print("Sending 1/2: Welcome email...")
resend_lib.Emails.send({
    "from": RESEND_FROM, "to": TEST_EMAIL,
    "subject": "Welcome to NeuroDigest",
    "html": ensure_unsubscribe(_welcome_html(token=token, site_url=SITE_URL), token, SITE_URL),
})
print("  ✓ Sent")
time.sleep(0.3)

# ── 2. Latest digest (personalized) ──────────────────────────────────────────
print("Sending 2/2: Latest digest...")
latest = sb.table("digests").select("subject,html,plain,digest_json") \
           .not_.ilike("subject", "%guideline%") \
           .order("sent_at", desc=True).limit(1).execute()

if latest.data:
    d          = latest.data[0]
    send_html  = d["html"]
    send_plain = d.get("plain", "")
    if d.get("digest_json"):
        try:
            digest_data = json.loads(d["digest_json"])
            personalized, excluded = filter_digest_for_subscriber(digest_data, ALL_TOPICS)
            show_also  = excluded if len(ALL_TOPICS) <= 2 else []
            send_html  = build_html_email(personalized, edition=0,
                                          preferences_token=token, site_url=SITE_URL,
                                          also_this_week=show_also)
            send_plain = build_plain_text(personalized, 0)
        except Exception as e:
            print(f"  Warning: {e}")
    send_html = ensure_unsubscribe(send_html, token, SITE_URL)
    resend_lib.Emails.send({
        "from": RESEND_FROM, "to": TEST_EMAIL,
        "subject": d["subject"], "html": send_html, "text": send_plain,
    })
    print("  ✓ Sent")
else:
    print("  No digest found in Supabase")

print("\n✅ Done — check your inbox for 2 emails")
