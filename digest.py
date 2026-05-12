"""
NeuroDigest — weekly neurology literature digest.
Fetches RSS feeds, synthesizes with Claude, sends personalized HTML emails.
"""

import json
import os
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import anthropic
import feedparser
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

# ── Config ────────────────────────────────────────────────────────────────────
FEEDS = [
    ("Neurology",                  "https://n.neurology.org/rss/current.xml"),
    ("JAMA Neurology",             "https://jamanetwork.com/rss/site_3/67.xml"),
    ("Lancet Neurology",           "https://www.thelancet.com/rssfeed/laneur_current.xml"),
    ("Brain",                      "https://academic.oup.com/rss/site_5504/3051.xml"),
    ("Annals of Neurology",        "https://onlinelibrary.wiley.com/feed/15318249/most-recent"),
    ("Stroke",                     "https://www.ahajournals.org/action/showFeed?type=etoc&feed=rss&jc=str"),
    ("Multiple Sclerosis Jour.",   "https://journals.sagepub.com/action/showFeed?ui=0&mi=ehikzz&ai=2dd&jc=msja&type=etoc&feed=rss"),
    ("Nature Rev. Neurology",      "https://www.nature.com/nrneurol.rss"),
    ("JAMA Neurol. (extra)",       "https://jamanetwork.com/rss/site_3/68.xml"),
    ("Epilepsia",                  "https://onlinelibrary.wiley.com/feed/15281167/most-recent"),
    ("Movement Disorders",         "https://onlinelibrary.wiley.com/feed/15318945/most-recent"),
    ("Alzheimer's & Dementia",     "https://onlinelibrary.wiley.com/feed/15525279/most-recent"),
    ("J. Neurology",               "https://link.springer.com/search.rss?facet-journal-id=415&query="),
    ("Neurological Sciences",      "https://link.springer.com/search.rss?facet-journal-id=10072&query="),
    ("Headache",                   "https://onlinelibrary.wiley.com/feed/15264610/most-recent"),
    ("J. Peripheral Nervous Sys.", "https://onlinelibrary.wiley.com/feed/15298027/most-recent"),
]

DAYS_BACK    = 14
MAX_ARTICLES = 60   # total cap across all feeds
OUTPUT_DIR   = Path("output") / datetime.now().strftime("%Y-%m-%d")
EDITION_FILE = Path(__file__).resolve().parent / "edition.txt"


# ── Edition counter ───────────────────────────────────────────────────────────
def get_edition() -> int:
    if EDITION_FILE.exists():
        n = int(EDITION_FILE.read_text().strip())
    else:
        n = 0
    n += 1
    EDITION_FILE.write_text(str(n))
    return n


# ── RSS fetching ──────────────────────────────────────────────────────────────
def fetch_feed(journal: str, url: str) -> list[dict]:
    try:
        feed = feedparser.parse(url)
        articles = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
        for entry in feed.entries:
            pub = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                pub = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                pub = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            if pub and pub < cutoff:
                continue
            title = entry.get("title", "").strip()
            summary = re.sub(r"<[^>]+>", " ", entry.get("summary", "") or "").strip()
            if title:
                articles.append({
                    "title":    title,
                    "summary":  summary[:1200],
                    "journal":  journal,
                    "pub_date": pub.strftime("%Y-%m-%d") if pub else "unknown",
                    "link":     entry.get("link", ""),
                })
        return articles
    except Exception as e:
        print(f"  Feed error [{journal}]: {e}")
        return []


def fetch_all_articles() -> list[dict]:
    all_articles = []
    for journal, url in FEEDS:
        arts = fetch_feed(journal, url)
        print(f"  {journal}: {len(arts)}")
        all_articles.extend(arts)
        time.sleep(0.3)
    return all_articles


def select_articles(articles: list[dict]) -> list[dict]:
    """Deduplicate by title and cap total."""
    seen, selected = set(), []
    for a in articles:
        key = a["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            selected.append(a)
    return selected[:MAX_ARTICLES]


# ── Claude synthesis ──────────────────────────────────────────────────────────
def synthesize_all(articles: list[dict], client: anthropic.Anthropic) -> dict:
    """Single synthesis pass across all neurology articles. Returns sections list."""
    if not articles:
        return {"sections": [], "bottom_line": "No articles found this week."}

    articles_text = ""
    for i, a in enumerate(articles, 1):
        abstract = a['summary'][:400] if a['summary'] else "(no abstract)"
        articles_text += (
            f"\n[{i}] {a['title']} | {a['journal']} | {a['pub_date']}\n"
            f"    URL: {a['link']}\n"
            f"    {abstract}\n"
        )

    prompt = f"""You are the editor of NeuroDigest, a weekly literature briefing for practicing neurologists.

Read all the articles below and produce a full weekly neurology digest.
Return ONLY valid JSON, no markdown, no text outside the JSON.

Format:
{{
  "sections": [
    {{
      "topic": "Clinical area name (e.g. Multiple Sclerosis, Stroke, Parkinson's Disease, Epilepsy, Dementia, Headache, Neuromuscular, Neuro-oncology, etc.)",
      "headline": "One sentence — the most clinically significant finding in this area this week",
      "themes": [
        {{
          "title": "THEME TITLE IN CAPS (4-6 words)",
          "body": "2-3 short paragraphs. Direct, specialist-level. No filler. Cite as (Journal, year).",
          "implication": "One sentence starting with an action verb."
        }}
      ],
      "sources": [
        {{"n": 1, "title": "Article title", "journal": "Journal name", "url": "https://..."}}
      ]
    }}
  ],
  "bottom_line": "Single most practice-changing sentence across ALL areas this week."
}}

Rules:
- Create one section per clinical area represented in the articles (3–7 sections)
- Only include areas that have at least 1 relevant article
- 1–3 themes per section
- Plain prose, no markdown inside strings, no bullet hyphens
- Implication: action-oriented
- Sources: only articles you actually cite

Articles this week:
{articles_text}"""

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    if not response.content:
        return {"sections": [], "bottom_line": "Synthesis unavailable this week."}
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"sections": [], "bottom_line": "Synthesis unavailable this week."}


# ── HTML email builder ────────────────────────────────────────────────────────
NAV   = "#1a1a2e"
ACC   = "#c0392b"
BODY  = "#222222"
BG    = "#f4f4f2"
WHITE = "#ffffff"

def topic_section_html(topic: str, data: dict) -> str:
    themes_html = ""
    for th in data.get("themes", []):
        body_paras = "".join(
            f'<p style="margin:0 0 12px 0;font-size:15px;line-height:1.75;color:{BODY};'
            f'font-family:Georgia,\'Times New Roman\',serif">{p.strip()}</p>'
            for p in th["body"].split("\n") if p.strip()
        )
        implication = th.get("implication", "")
        impl_html = ""
        if implication:
            impl_html = (
                f'<p style="margin:12px 0 0 0;padding:0 0 0 16px;'
                f'border-left:2px solid {ACC};font-size:14px;color:#444;'
                f'line-height:1.7;font-style:italic;'
                f'font-family:Georgia,\'Times New Roman\',serif">{implication}</p>'
            )
        themes_html += f"""
        <div style="margin-bottom:28px">
          <p style="margin:0 0 8px 0;font-size:10px;font-weight:700;letter-spacing:2px;
                    color:{ACC};text-transform:uppercase;
                    font-family:Helvetica,Arial,sans-serif">{th['title']}</p>
          {body_paras}
          {impl_html}
        </div>"""

    sources_html = ""
    sources = data.get("sources", [])
    if sources:
        items = "".join(
            f'<tr>'
            f'<td style="padding:5px 0;vertical-align:top">'
            f'<span style="font-size:10px;font-weight:700;letter-spacing:1px;'
            f'text-transform:uppercase;color:#aaa;font-family:Helvetica,Arial,sans-serif;'
            f'white-space:nowrap;padding-right:10px">{s["journal"]}</span>'
            f'</td>'
            f'<td style="padding:5px 0">'
            f'<a href="{s["url"]}" style="font-size:12px;color:#555;text-decoration:none;'
            f'font-family:Helvetica,Arial,sans-serif;line-height:1.5">{s["title"]}</a>'
            f'</td>'
            f'</tr>'
            for s in sources if s.get("url")
        )
        if items:
            sources_html = f"""
            <div style="margin-top:20px;padding-top:14px;border-top:1px solid #ebebeb">
              <table cellpadding="0" cellspacing="0" border="0" width="100%"
                     style="border-collapse:collapse">{items}</table>
            </div>"""

    return f"""
    <tr><td style="padding:32px 40px 0">
      <p style="margin:0 0 6px 0;font-size:10px;font-weight:700;letter-spacing:2px;
                text-transform:uppercase;color:{ACC};
                font-family:Helvetica,Arial,sans-serif">{topic}</p>
      <p style="margin:0 0 24px 0;font-size:22px;font-weight:700;color:{NAV};line-height:1.3;
                font-family:Georgia,'Times New Roman',serif">{data.get('headline','')}</p>
      {themes_html}
      {sources_html}
    </td></tr>
    <tr><td style="padding:28px 40px 0">
      <div style="border-top:1px solid #ebebeb"></div>
    </td></tr>"""


def build_html_email(digest: dict, edition: int,
                     preferences_token: str = "", site_url: str = "") -> str:
    date_str  = datetime.now().strftime("%B %d, %Y")
    single_action = digest.get("bottom_line", "")

    sections = ""
    for sec in digest.get("sections", []):
        sections += topic_section_html(sec["topic"], sec)

    action_html = ""
    if single_action:
        action_html = f"""
    <tr><td style="padding:32px 40px;background:{NAV}">
      <p style="margin:0 0 8px 0;font-size:10px;font-weight:700;letter-spacing:2px;
                text-transform:uppercase;color:{ACC};
                font-family:Helvetica,Arial,sans-serif">This Week's Take-Home</p>
      <p style="margin:0;font-size:17px;color:{WHITE};line-height:1.65;
                font-family:Georgia,'Times New Roman',serif;font-style:italic">{single_action}</p>
    </td></tr>"""

    manage_link = (
        f'&nbsp;&middot;&nbsp;'
        f'<a href="{site_url}/preferences?token={preferences_token}" '
        f'style="color:#888;text-decoration:none">Manage topics</a>'
        if preferences_token else ""
    )
    unsub_link = (
        f'&nbsp;&middot;&nbsp;'
        f'<a href="{site_url}/api/unsubscribe?token={preferences_token}" '
        f'style="color:#888;text-decoration:none">Unsubscribe</a>'
        if preferences_token else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NeuroDigest — {date_str}</title>
</head>
<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%">
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{BG};min-height:100%">
<tr><td align="center" style="padding:40px 16px">

  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="max-width:640px;background:{WHITE};
                border:1px solid #e0e0de;
                font-family:Georgia,'Times New Roman',serif">

    <!-- Header -->
    <tr><td style="padding:28px 40px 24px;border-bottom:3px solid {ACC}">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td>
            <p style="margin:0;font-size:26px;font-weight:700;color:{NAV};letter-spacing:-0.5px;
                      font-family:Georgia,'Times New Roman',serif">NeuroDigest</p>
            <p style="margin:4px 0 0;font-size:11px;letter-spacing:1.5px;
                      text-transform:uppercase;color:#888;
                      font-family:Helvetica,Arial,sans-serif">
              Weekly Neurology Literature Briefing
            </p>
          </td>
          <td align="right" style="vertical-align:middle">
            <p style="margin:0;font-size:12px;color:#aaa;
                      font-family:Helvetica,Arial,sans-serif">{date_str}</p>
          </td>
        </tr>
      </table>
    </td></tr>

    <!-- Topic sections -->
    {sections}

    <!-- breathing room before action block -->
    <tr><td style="padding:12px 0"></td></tr>

    <!-- Take-Home -->
    {action_html}

    <!-- Footer -->
    <tr><td style="padding:20px 40px;border-top:1px solid #ebebeb;background:#fafaf9">
      <p style="margin:0;font-size:11px;color:#aaa;
                font-family:Helvetica,Arial,sans-serif;line-height:1.8;text-align:center">
        <strong style="color:#888">NeuroDigest</strong>
        {manage_link}
        {unsub_link}
      </p>
    </td></tr>

  </table>
</td></tr>
</table>
</body>
</html>"""


def build_plain_text(digest: dict, edition: int) -> str:
    date_str = datetime.now().strftime("%B %d, %Y")
    out = f"NEURODIGEST — Edition #{edition} — {date_str}\n{'=' * 54}\n\n"
    for sec in digest.get("sections", []):
        topic = sec["topic"]
        out += f"{topic.upper()}\n{'-' * len(topic)}\n"
        out += f"{sec.get('headline', '')}\n\n"
        for th in sec.get("themes", []):
            out += f"{th['title']}\n{th['body']}\n"
            if th.get("implication"):
                out += f"→ {th['implication']}\n"
            out += "\n"
        if sec.get("sources"):
            out += "Sources:\n"
            for s in sec["sources"]:
                out += f"  {s['n']}. {s['title']} — {s['journal']}\n     {s.get('url','')}\n"
        out += "\n" + "—" * 54 + "\n\n"
    if digest.get("bottom_line"):
        out += f"THIS WEEK'S SINGLE ACTION\n{digest['bottom_line']}\n\n"
    out += f"neurodigest.io · Edition #{edition} · {date_str}\n"
    return out


# ── Supabase subscriber management ───────────────────────────────────────────
def fetch_supabase_subscribers() -> list[dict]:
    """Return confirmed subscribers [{email, topics}] from Supabase."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return []
    try:
        from supabase import create_client
        sb     = create_client(url, key)
        result = sb.table("subscribers").select("email,topics").eq("status", "confirmed").execute()
        return result.data or []
    except Exception as e:
        print(f"  Supabase fetch error: {e}")
        return []


def generate_preferences_token(email: str) -> str:
    """7-day JWT for the subscriber's Manage-topics link."""
    secret = os.getenv("JWT_SECRET", "")
    if not secret:
        return ""
    try:
        import jwt
        payload = {
            "sub":     email,
            "purpose": "preferences",
            "exp":     datetime.now(timezone.utc) + timedelta(days=7),
        }
        return jwt.encode(payload, secret, algorithm="HS256")
    except Exception:
        return ""


def filter_digest_for_subscriber(digest: dict, topics: list[str]) -> dict:
    """Return digest copy with sections limited to the subscriber's chosen topics."""
    if not topics:
        return digest
    lc = [t.lower() for t in topics]
    filtered = [
        sec for sec in digest.get("sections", [])
        if any(t in sec["topic"].lower() or sec["topic"].lower() in t for t in lc)
    ]
    return {**digest, "sections": filtered or digest.get("sections", [])}


def send_personalized_via_resend(subscribers: list[dict], digest: dict, edition: int) -> bool:
    """Send per-subscriber personalized emails via Resend."""
    api_key   = os.getenv("RESEND_API_KEY")
    site_url  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")
    from_addr = os.getenv("RESEND_FROM", "NeuroDigest <onboarding@resend.dev>")
    if not api_key:
        return False
    try:
        import resend
        resend.api_key = api_key
    except ImportError:
        print("  resend package not installed — run: pip install resend")
        return False

    date_str = datetime.now().strftime("%B %d, %Y")
    subject  = f"NeuroDigest — {date_str}"
    sent     = 0

    for sub in subscribers:
        email  = sub["email"]
        topics = sub.get("topics") or []
        token  = generate_preferences_token(email)

        personalized = filter_digest_for_subscriber(digest, topics)
        html         = build_html_email(personalized, edition,
                                        preferences_token=token, site_url=site_url)
        plain        = build_plain_text(personalized, edition)

        try:
            resend.Emails.send({
                "from":    from_addr,
                "to":      email,
                "subject": subject,
                "html":    html,
                "text":    plain,
            })
            sent += 1
            time.sleep(0.15)
        except Exception as e:
            print(f"  Resend error ({email}): {e}")

    print(f"  Sent {sent}/{len(subscribers)} personalized emails via Resend")
    return sent > 0


# ── Email sending ─────────────────────────────────────────────────────────────
def send_via_mailchimp(html_body: str, plain_body: str, edition: int) -> bool:
    """Create and send a Mailchimp campaign to all subscribers."""
    api_key = os.getenv("MAILCHIMP_API_KEY")
    list_id = os.getenv("MAILCHIMP_LIST_ID")
    dc      = os.getenv("MAILCHIMP_DC", "us14")
    if not all([api_key, list_id]):
        return False

    base    = f"https://{dc}.api.mailchimp.com/3.0"
    auth    = ("anystring", api_key)
    date_str = datetime.now().strftime("%B %d, %Y")
    subject = f"NeuroDigest — {date_str}"

    # 1. Create campaign
    campaign = requests.post(f"{base}/campaigns", auth=auth, json={
        "type": "regular",
        "recipients": {"list_id": list_id},
        "settings": {
            "subject_line": subject,
            "from_name":    "NeuroDigest",
            "reply_to":     os.getenv("EMAIL_SENDER", "vincenzolate95l@gmail.com"),
            "title":        subject,
        },
    }).json()

    campaign_id = campaign.get("id")
    if not campaign_id:
        print(f"  Mailchimp campaign creation failed: {campaign}")
        return False

    # 2. Set content
    requests.put(f"{base}/campaigns/{campaign_id}/content", auth=auth, json={
        "html":      html_body,
        "plain_text": plain_body,
    })

    # 3. Send
    r = requests.post(f"{base}/campaigns/{campaign_id}/actions/send", auth=auth)
    if r.status_code == 204:
        print(f"  Mailchimp campaign sent to all subscribers (edition #{edition})")
        return True
    else:
        print(f"  Mailchimp send failed: {r.status_code} {r.text[:200]}")
        return False


def send_email(html_body: str, plain_body: str, edition: int) -> None:
    if send_via_mailchimp(html_body, plain_body, edition):
        return
    # Fallback: Gmail SMTP to self
    sender   = os.getenv("EMAIL_SENDER")
    password = os.getenv("EMAIL_PASSWORD")
    if not all([sender, password]):
        print("  No email credentials configured.")
        return
    date_str = datetime.now().strftime("%B %d, %Y")
    msg = MIMEMultipart("alternative")
    msg["From"]    = f"NeuroDigest <{sender}>"
    msg["To"]      = sender
    msg["Subject"] = f"NeuroDigest #{edition} — {date_str}"
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body,  "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(sender, password)
        server.sendmail(sender, sender, msg.as_string())
    print(f"  Sent via Gmail to {sender}")


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in .env")

    client  = anthropic.Anthropic(api_key=api_key)
    edition = get_edition()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Fetching RSS feeds...")
    all_articles = fetch_all_articles()
    articles = select_articles(all_articles)
    print(f"Total: {len(articles)} articles selected for synthesis\n")

    print("Synthesizing full neurology digest with Claude...")
    digest = synthesize_all(articles, client)
    sections = digest.get("sections", [])
    print(f"  {len(sections)} clinical areas identified")
    for s in sections:
        print(f"  · {s['topic']} ({len(s.get('themes', []))} themes)")
    print(f"  Bottom line: {digest.get('bottom_line','')[:80]}...")

    html  = build_html_email(digest, edition)
    plain = build_plain_text(digest, edition)

    (OUTPUT_DIR / "neuro_digest.html").write_text(html)
    (OUTPUT_DIR / "neuro_digest.txt").write_text(plain)
    print(f"\nDigest saved → {OUTPUT_DIR.resolve()}")

    print("Fetching confirmed subscribers from Supabase...")
    subscribers = fetch_supabase_subscribers()

    if subscribers:
        print(f"  {len(subscribers)} subscriber(s) found — sending personalized emails...")
        send_personalized_via_resend(subscribers, digest, edition)
    else:
        print("  No Supabase subscribers yet — falling back to Mailchimp broadcast...")
        send_email(html, plain, edition)

    print(f"\nDone — Edition #{edition}")


if __name__ == "__main__":
    run()
