"""
Force-send today's NeuroDigest to ALL confirmed subscribers.
Generates the digest if not yet in Supabase, then sends to everyone
(no timezone filter — used for manual/catch-up sends).
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))

import anthropic
from supabase import create_client
import resend as resend_lib

from digest import (
    fetch_all_articles, select_articles, synthesize_all,
    build_html_email, build_plain_text, save_digest_to_supabase,
    get_todays_digest, fetch_supabase_subscribers, get_already_sent,
    filter_digest_for_subscriber, generate_preferences_token,
    ensure_unsubscribe, get_edition, log_sends, OUTPUT_DIR,
)

sb        = create_client(os.getenv("SUPABASE_URL",""), os.getenv("SUPABASE_SERVICE_KEY",""))
site_url  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")
from_addr = os.getenv("RESEND_FROM", "NeuroDigest <digest@neuro-digest.com>")
resend_lib.api_key = os.getenv("RESEND_API_KEY", "")

# ── 1. Get or generate today's digest ────────────────────────────────────────
print("Checking for today's digest in Supabase...")
existing = get_todays_digest(sb)

if existing:
    digest_id   = existing["id"]
    edition     = existing.get("edition_num") or 0
    html        = existing["html"]
    plain       = existing["plain"]
    try:
        digest_data = json.loads(existing.get("digest_json") or "{}")
    except Exception:
        digest_data = {}
    print(f"  Found existing digest (id={digest_id}, edition #{edition})")
else:
    print("  No digest for today — generating now with Claude...")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in .env")

    client  = anthropic.Anthropic(api_key=api_key)
    edition = get_edition()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("  Fetching RSS feeds...")
    all_articles = fetch_all_articles()
    articles     = select_articles(all_articles)
    print(f"  {len(articles)} articles selected")

    print("  Synthesizing with Claude (this takes ~2-3 min)...")
    digest_data = synthesize_all(articles, client)
    sections    = digest_data.get("sections", [])
    print(f"  {len(sections)} clinical areas synthesized")

    html  = build_html_email(digest_data, edition)
    plain = build_plain_text(digest_data, edition)

    date_str  = datetime.now().strftime("%B %d, %Y")
    subject   = f"NeuroDigest — {date_str}"
    digest_id = save_digest_to_supabase(sb, subject, html, plain, edition, digest_data)
    print(f"  Saved to Supabase (id={digest_id})")

date_str = datetime.now().strftime("%B %d, %Y")
subject  = f"NeuroDigest — {date_str}"

# ── 2. Fetch all confirmed subscribers ───────────────────────────────────────
print("\nFetching subscribers...")
all_subs = fetch_supabase_subscribers()
print(f"  {len(all_subs)} confirmed subscribers")

# ── 3. Skip already-sent ──────────────────────────────────────────────────────
already_sent = set()
if digest_id:
    already_sent = get_already_sent(sb, digest_id)
    if already_sent:
        print(f"  {len(already_sent)} already sent — skipping them")

to_send = [s for s in all_subs if s["email"] not in already_sent]
print(f"  Sending to {len(to_send)} subscribers...")

# ── 4. Send ───────────────────────────────────────────────────────────────────
sent, errors = 0, 0
sent_addrs = []

for sub in to_send:
    email  = sub["email"]
    topics = sub.get("topics") or []
    token  = generate_preferences_token(email)

    personalized, excluded = filter_digest_for_subscriber(digest_data, topics)
    show_also = excluded if len(topics) <= 2 else []
    email_html = build_html_email(
        personalized, edition,
        preferences_token=token, site_url=site_url,
        also_this_week=show_also,
    )
    email_html  = ensure_unsubscribe(email_html, token, site_url)
    email_plain = build_plain_text(personalized, edition)

    try:
        resend_lib.Emails.send({
            "from":    from_addr,
            "to":      email,
            "subject": subject,
            "html":    email_html,
            "text":    email_plain,
        })
        sent += 1
        sent_addrs.append(email)
        print(f"  ✓ {email}")
        time.sleep(0.15)
    except Exception as e:
        errors += 1
        print(f"  ✗ {email}: {e}")

# ── 5. Log sends ──────────────────────────────────────────────────────────────
if digest_id and sent_addrs:
    log_sends(sb, sent_addrs, digest_id)
    print(f"\n  Logged {len(sent_addrs)} sends to Supabase")

print(f"\n✅ Done — sent {sent}/{len(to_send)}, errors: {errors}")
