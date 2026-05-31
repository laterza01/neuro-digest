"""
NeuroDigest — weekly neurology literature digest.
Fetches RSS feeds, synthesizes with Claude, sends personalized HTML emails.
Monthly Guidelines Edition runs on the last Monday of each month.

Timezone-aware delivery
─────────────────────────────────────────────────────────────────
The GitHub Actions cron fires every 30 min on Mon+Tue UTC.
Each run:
  1. Generates (or retrieves) today's digest — heavy work runs only once.
  2. Filters subscribers whose local clock shows Monday 14:xx.
  3. Skips anyone already in sends_log for this digest_id (deduplication).
  4. Sends to the eligible batch and logs them.
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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anthropic
import feedparser
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

# ── Config ────────────────────────────────────────────────────────────────────
FEEDS = [
    # ── Q1 ────────────────────────────────────────────────────────────────────
    ("Neurology",                   "https://n.neurology.org/rss/current.xml"),
    ("JAMA Neurology",              "https://jamanetwork.com/rss/site_3/67.xml"),
    ("Lancet Neurology",            "https://www.thelancet.com/rssfeed/laneur_current.xml"),
    ("Brain",                       "https://academic.oup.com/rss/site_5504/3051.xml"),
    ("Annals of Neurology",         "https://onlinelibrary.wiley.com/feed/15318249/most-recent"),
    ("Stroke",                      "https://www.ahajournals.org/action/showFeed?type=etoc&feed=rss&jc=str"),
    ("Nature Rev. Neurology",       "https://www.nature.com/nrneurol.rss"),
    ("Epilepsia",                   "https://onlinelibrary.wiley.com/feed/15281167/most-recent"),
    ("Movement Disorders",          "https://onlinelibrary.wiley.com/feed/15318945/most-recent"),
    ("Alzheimer's & Dementia",      "https://onlinelibrary.wiley.com/feed/15525279/most-recent"),
    ("Multiple Sclerosis Jour.",    "https://journals.sagepub.com/action/showFeed?ui=0&mi=ehikzz&ai=2dd&jc=msja&type=etoc&feed=rss"),
    ("Acta Neuropathologica",       "https://link.springer.com/search.rss?facet-journal-id=401&query="),
    # ── Q2 ────────────────────────────────────────────────────────────────────
    ("European J. Neurology",       "https://onlinelibrary.wiley.com/feed/14681331/most-recent"),
    ("Cephalalgia",                 "https://journals.sagepub.com/action/showFeed?ui=0&mi=ehikzz&ai=2dd&jc=cep&type=etoc&feed=rss"),
    ("Parkinsonism & Related Dis.", "https://rss.sciencedirect.com/publication/science/13538020"),
    ("MS & Related Disorders",      "https://rss.sciencedirect.com/publication/science/22110348"),
    ("Neurocritical Care",          "https://link.springer.com/search.rss?facet-journal-id=12028&query="),
    ("Muscle & Nerve",              "https://onlinelibrary.wiley.com/feed/10974598/most-recent"),
    ("Neuromuscular Disorders",     "https://rss.sciencedirect.com/publication/science/09608966"),
    ("J. Headache and Pain",        "https://link.springer.com/search.rss?facet-journal-id=10194&query="),
    ("J. Alzheimer's Disease",      "https://journals.sagepub.com/action/showFeed?jc=jadA&type=etoc&feed=rss"),
    ("J. Neuro-Oncology",           "https://link.springer.com/search.rss?facet-journal-id=11060&query="),
    ("Epilepsy Research",           "https://rss.sciencedirect.com/publication/science/09201211"),
    ("J. Peripheral Nervous Sys.",  "https://onlinelibrary.wiley.com/feed/15298027/most-recent"),
    ("J. Neurology",                "https://link.springer.com/search.rss?facet-journal-id=415&query="),
    ("Neurological Sciences",       "https://link.springer.com/search.rss?facet-journal-id=10072&query="),
    ("Headache",                    "https://onlinelibrary.wiley.com/feed/15264610/most-recent"),
]

DAYS_BACK    = 14
MAX_ARTICLES = 100  # total cap across all feeds
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


# ── DOI extraction ────────────────────────────────────────────────────────────
def extract_doi(url: str) -> str:
    """Extract DOI from common journal URL patterns."""
    patterns = [
        r'doi\.org/(10\.\d{4,}/\S+)',
        r'/doi/(?:abs/|full/|pdf/)?(10\.\d{4,}/\S+)',
        r'doi=(10\.\d{4,}/\S+)',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            doi = m.group(1)
            # Strip trailing query params and anchors
            doi = re.split(r'[?#&]', doi)[0].rstrip('.')
            return doi
    return ""


# ── Claude synthesis ──────────────────────────────────────────────────────────
def synthesize_all(articles: list[dict], client: anthropic.Anthropic) -> dict:
    """Single synthesis pass across all neurology articles. Returns sections list."""
    if not articles:
        return {"sections": [], "bottom_line": "No articles found this week."}

    articles_text = ""
    for i, a in enumerate(articles, 1):
        abstract = a['summary'][:250] if a['summary'] else "(no abstract)"
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
        {{"n": 1, "title": "Article title", "journal": "Journal name", "url": "https://...", "doi": "10.xxxx/yyyy"}}
      ]
    }}
  ],
  "bottom_line": "Single most practice-changing sentence across ALL areas this week."
}}

Rules:
- Create one section per clinical area that has genuinely noteworthy findings this week
- If it's a busy week with many strong results across areas, include more sections (up to 8–10)
- If it's a quiet week, 2–3 well-developed sections are better than padding with minor findings
- Quality over quantity: only include a section if there is something clinically meaningful to say
- Only include areas that have at least 1 relevant article worth citing
- 1–3 themes per section depending on how much there is to say
- Plain prose, no markdown inside strings, no bullet hyphens
- Implication: action-oriented
- Sources: only articles you actually cite; include doi when you can extract it from the URL or know it

Articles this week:
{articles_text}"""

    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=16000,
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

# Per-section accent palette — professional, muted, readable on white
SECTION_PALETTE = [
    "#c0392b",  # crimson      — Stroke / Neurocritical
    "#1a6b8a",  # cerulean     — MS / Neuroimmunology
    "#2d6a4f",  # forest       — Epilepsy / Genetics
    "#4a4080",  # indigo       — Dementia / Cognitive
    "#7b4500",  # amber-brown  — Movement Disorders
    "#1a5276",  # navy-teal    — Neurotrauma / Concussion
    "#6b3a6b",  # plum         — Neuroinfectious / Oncology
    "#3d6b41",  # sage         — Headache / Neuromuscular
]

# Guidelines Edition accent — dark gold, visually distinct from weekly digest
GUIDELINES_ACC = "#8b6914"

# ── Responsive CSS (injected into <head> of every email) ─────────────────────
# Classes used:
#   ep  → content cell          (padding 36px 48px → 20px sides on mobile)
#   eh  → header cell
#   ed  → date/edition td       (hidden on mobile)
#   hl  → section headline      (21px → 18px)
#   bt  → body paragraph        (15px → 16px, prevents iOS auto-zoom)
#   ta  → take-home dark block
#   ft  → footer cell
#   toc → table-of-contents cell
MOBILE_CSS = """
<style>
/* ── base reset for email clients ── */
img{max-width:100%!important;height:auto!important}
table{border-collapse:collapse}

@media only screen and (max-width:620px){
  /* outer wrapper */
  table[width="100%"]{width:100%!important}

  /* padding resets */
  .ep{padding-left:16px!important;padding-right:16px!important;padding-top:24px!important}
  .eh{padding:16px 16px!important}
  .ed{display:none!important}
  .ta{padding:24px 16px!important}
  .ft{padding:16px 16px!important}
  .toc{padding:14px 16px!important}

  /* typography */
  .hl{font-size:18px!important;line-height:1.35!important}
  .bt{font-size:16px!important;line-height:1.8!important}

  /* prevent text overflow on narrow screens */
  td,p,div,span{
    word-break:break-word!important;
    overflow-wrap:break-word!important;
    max-width:100%!important;
  }

  /* inner content table (color bar + text) */
  .ep table{width:100%!important}
  .ep table td:last-child{padding-left:14px!important}

  /* section headings inside guidelines */
  .sec-h{font-size:11px!important;letter-spacing:1.5px!important}
  .sec-b{font-size:14px!important;line-height:1.65!important}
  .rec-b{font-size:13px!important;line-height:1.65!important}
}
</style>"""


def ensure_unsubscribe(html: str, token: str, site_url: str) -> str:
    """
    Safety net: if the rendered HTML doesn't contain an unsubscribe link,
    inject one just before </body>. Called on every outbound email.
    """
    unsub_url  = f"{site_url}/api/unsubscribe?token={token}"
    manage_url = f"{site_url}/preferences?token={token}"
    if not token or unsub_url in html:
        return html
    inject = (
        f'<p style="text-align:center;font-size:13px;color:#555;'
        f'font-family:Helvetica,Arial,sans-serif;margin:0;padding:18px 48px;'
        f'background:#f5f5f3;border-top:2px solid #e0e0dc">'
        f'<strong style="color:#333;font-family:Georgia,serif">NeuroDigest</strong>'
        f' &nbsp;&middot;&nbsp; '
        f'<a href="{manage_url}" style="color:#555;text-decoration:none">Manage topics</a>'
        f' &nbsp;&middot;&nbsp; '
        f'<a href="{unsub_url}" style="color:#555;text-decoration:none">Unsubscribe</a>'
        f'</p>'
    )
    return html.replace("</body>", inject + "</body>")


def _preheader(text: str) -> str:
    """Hidden inbox-preview line. Soft-hyphen padding stops clients pulling body text."""
    safe   = (text or "")[:120]
    filler = "&#847;" * 60
    return (
        f'<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        f'font-size:1px;line-height:1px;color:#1a1a2e">'
        f'{safe}{filler}'
        f'</div>'
    )


def topic_section_html(topic: str, data: dict, color: str = ACC) -> str:
    """
    Render one clinical-area section in professional journal style:
      • 4 px left accent bar (topic colour) — email-safe table-cell trick
      • Category label in small-caps above the headline
      • Body prose + implication callout
      • Sources as a clean inline footnote line
    """
    # ── Themes ────────────────────────────────────────────────────────────────
    themes_html = ""
    for th in data.get("themes", []):
        body_paras = "".join(
            f'<p class="bt" style="margin:0 0 13px;font-size:15px;line-height:1.8;'
            f'color:#2c2c2c;font-family:Georgia,\'Times New Roman\',serif">'
            f'{p.strip()}</p>'
            for p in th["body"].split("\n") if p.strip()
        )
        implication = th.get("implication", "")
        impl_html   = ""
        if implication:
            # Callout box: light tinted background + left rule
            impl_html = f"""
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="margin:14px 0 4px">
              <tr>
                <td style="width:3px;background:{color}"></td>
                <td style="padding:10px 14px;background:#f7f7f5">
                  <p style="margin:0;font-size:13px;line-height:1.7;color:#444;
                            font-style:italic;
                            font-family:Georgia,'Times New Roman',serif">{implication}</p>
                </td>
              </tr>
            </table>"""

        theme_title = th.get("title", "")
        title_html  = ""
        if theme_title:
            title_html = (
                f'<p style="margin:0 0 8px;font-size:10px;font-weight:700;'
                f'letter-spacing:2px;text-transform:uppercase;color:{color};'
                f'font-family:Helvetica,Arial,sans-serif">{theme_title}</p>'
            )
        themes_html += f"""
        <div style="margin-bottom:24px">
          {title_html}
          {body_paras}
          {impl_html}
        </div>"""

    # ── Sources — clean inline footnote line ──────────────────────────────────
    sources_html = ""
    sources      = data.get("sources", [])
    if sources:
        links = []
        for s in sources:
            if not s.get("title") and not s.get("journal"):
                continue
            doi  = s.get("doi") or extract_doi(s.get("url", ""))
            href = f"https://doi.org/{doi}" if doi else s.get("url", "")
            label = s.get("journal") or s.get("title", "")
            if href:
                links.append(
                    f'<a href="{href}" style="color:{color};text-decoration:none;'
                    f'font-weight:600">{label}</a>'
                )
            else:
                links.append(
                    f'<span style="color:#999">{label}</span>'
                )
        if links:
            sources_html = (
                f'<p style="margin:18px 0 0;font-size:11px;color:#999;'
                f'font-family:Helvetica,Arial,sans-serif;line-height:1.6">'
                f'<span style="text-transform:uppercase;letter-spacing:1px;'
                f'font-size:9px;font-weight:700;color:#bbb">Sources&nbsp;&nbsp;</span>'
                + ' &nbsp;&middot;&nbsp; '.join(links)
                + '</p>'
            )

    # ── Section shell: left accent bar + content ──────────────────────────────
    return f"""
    <tr><td class="ep" style="padding:36px 48px 0">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <!-- Left accent bar (4 px, topic colour) -->
          <td style="width:4px;background:{color};border-radius:2px" width="4"></td>
          <!-- Content -->
          <td style="padding:2px 0 0 22px">
            <p style="margin:0 0 5px;font-size:10px;font-weight:700;letter-spacing:2.5px;
                      text-transform:uppercase;color:{color};
                      font-family:Helvetica,Arial,sans-serif">{topic}</p>
            <p class="hl" style="margin:0 0 20px;font-size:21px;font-weight:700;
                      color:{NAV};line-height:1.3;
                      font-family:Georgia,'Times New Roman',serif">
              {data.get('headline', '')}
            </p>
            {themes_html}
            {sources_html}
          </td>
        </tr>
      </table>
    </td></tr>
    <tr><td class="ep" style="padding:32px 48px 0">
      <div style="border-top:1px solid #e8e8e4"></div>
    </td></tr>"""


def build_html_email(digest: dict, edition: int,
                     preferences_token: str = "", site_url: str = "",
                     also_this_week: list[dict] | None = None) -> str:
    date_str      = datetime.now().strftime("%B %d, %Y")
    single_action = digest.get("bottom_line", "")
    preheader_txt = single_action or "Your weekly neurology literature briefing."
    section_list  = digest.get("sections", [])

    # ── Build section HTML ────────────────────────────────────────────────────
    sections = ""
    for i, sec in enumerate(section_list):
        color = SECTION_PALETTE[i % len(SECTION_PALETTE)]
        sections += topic_section_html(sec["topic"], sec, color=color)

    # ── Table of contents ─────────────────────────────────────────────────────
    toc_items = ""
    for i, sec in enumerate(section_list):
        color = SECTION_PALETTE[i % len(SECTION_PALETTE)]
        toc_items += (
            f'<tr>'
            f'<td style="padding:4px 12px 4px 0;vertical-align:top;width:1%;white-space:nowrap">'
            f'<span style="font-size:9px;font-weight:700;letter-spacing:1.5px;'
            f'text-transform:uppercase;color:{color};font-family:Helvetica,Arial,sans-serif">'
            f'{sec["topic"]}</span></td>'
            f'<td style="padding:4px 0;vertical-align:top">'
            f'<span style="font-size:12px;color:#555;font-family:Helvetica,Arial,sans-serif;'
            f'line-height:1.5">{sec.get("headline","")}</span></td>'
            f'</tr>'
        )

    toc_html = ""
    if toc_items:
        toc_html = f"""
    <tr><td class="toc" style="padding:20px 48px;background:#f5f5f3;
                               border-bottom:1px solid #e4e4e0">
      <p style="margin:0 0 12px;font-size:9px;font-weight:700;letter-spacing:2.5px;
                text-transform:uppercase;color:#aaa;
                font-family:Helvetica,Arial,sans-serif">In This Issue</p>
      <table cellpadding="0" cellspacing="0" border="0" width="100%"
             style="border-collapse:collapse">{toc_items}</table>
    </td></tr>"""

    # ── "Also this week" teasers (only for topic-filtered subscribers) ────────
    also_html = ""
    if also_this_week:
        rows = ""
        for i, sec in enumerate(also_this_week):
            color = SECTION_PALETTE[(len(section_list) + i) % len(SECTION_PALETTE)]
            rows += (
                f'<tr>'
                f'<td style="padding:7px 16px 7px 0;vertical-align:top;width:1%;white-space:nowrap">'
                f'<span style="font-size:9px;font-weight:700;letter-spacing:1.5px;'
                f'text-transform:uppercase;color:{color};font-family:Helvetica,Arial,sans-serif">'
                f'{sec["topic"]}</span></td>'
                f'<td style="padding:7px 0;vertical-align:top">'
                f'<span style="font-size:12px;color:#666;font-family:Helvetica,Arial,sans-serif;'
                f'line-height:1.5">{sec.get("headline","")}</span></td>'
                f'</tr>'
            )
        also_html = f"""
    <tr><td class="ep" style="padding:28px 48px 0">
      <div style="border-top:1px solid #e8e8e4"></div>
    </td></tr>
    <tr><td class="toc" style="padding:20px 48px 28px">
      <p style="margin:0 0 12px;font-size:9px;font-weight:700;letter-spacing:2.5px;
                text-transform:uppercase;color:#aaa;
                font-family:Helvetica,Arial,sans-serif">Also This Week</p>
      <table cellpadding="0" cellspacing="0" border="0" width="100%"
             style="border-collapse:collapse">{rows}</table>
    </td></tr>"""

    # ── Take-Home ─────────────────────────────────────────────────────────────
    action_html = ""
    if single_action:
        action_html = f"""
    <tr><td class="ta" style="padding:32px 48px;background:{NAV}">
      <p style="margin:0 0 10px;font-size:9px;font-weight:700;letter-spacing:2.5px;
                text-transform:uppercase;color:{ACC};
                font-family:Helvetica,Arial,sans-serif">This Week's Take-Home</p>
      <p class="bt" style="margin:0;font-size:17px;color:{WHITE};line-height:1.7;
                font-family:Georgia,'Times New Roman',serif;font-style:italic">
        {single_action}
      </p>
    </td></tr>"""

    # ── Footer links ──────────────────────────────────────────────────────────
    manage_link = (
        f'<a href="{site_url}/preferences?token={preferences_token}" '
        f'style="color:#999;text-decoration:none">Manage topics</a>'
        if preferences_token else ""
    )
    unsub_link = (
        f'<a href="{site_url}/api/unsubscribe?token={preferences_token}" '
        f'style="color:#999;text-decoration:none">Unsubscribe</a>'
        if preferences_token else ""
    )
    sep = ' &nbsp;&middot;&nbsp; ' if manage_link and unsub_link else ''

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NeuroDigest — {date_str}</title>
{MOBILE_CSS}
</head>
<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%">
{_preheader(preheader_txt)}
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{BG};min-height:100%">
<tr><td align="center" style="padding:24px 8px">

  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="max-width:640px;background:{WHITE};border:1px solid #ddddd8">

    <!-- ── Masthead ─────────────────────────────────────────────────────── -->
    <tr><td class="eh" style="padding:22px 48px 20px;background:{NAV}">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="vertical-align:bottom">
            <p style="margin:0;font-size:28px;font-weight:700;color:{WHITE};
                      letter-spacing:-0.5px;
                      font-family:Georgia,'Times New Roman',serif">NeuroDigest</p>
            <p style="margin:5px 0 0;font-size:10px;letter-spacing:2px;
                      text-transform:uppercase;color:{ACC};
                      font-family:Helvetica,Arial,sans-serif">
              Weekly Neurology Literature Briefing
            </p>
          </td>
          <td class="ed" align="right" style="vertical-align:bottom">
            <p style="margin:0;font-size:11px;color:#8888aa;
                      font-family:Helvetica,Arial,sans-serif">Edition #{edition}</p>
            <p style="margin:3px 0 0;font-size:11px;color:#8888aa;
                      font-family:Helvetica,Arial,sans-serif">{date_str}</p>
          </td>
        </tr>
      </table>
    </td></tr>
    <!-- Red rule below masthead -->
    <tr><td style="height:3px;background:{ACC};font-size:0;line-height:0">&nbsp;</td></tr>

    <!-- ── Table of contents ────────────────────────────────────────────── -->
    {toc_html}

    <!-- ── Clinical sections ────────────────────────────────────────────── -->
    {sections}

    <!-- ── Also this week ───────────────────────────────────────────────── -->
    {also_html}

    <tr><td style="padding:16px 0 0"></td></tr>

    <!-- ── Take-Home ────────────────────────────────────────────────────── -->
    {action_html}

    <!-- ── Footer ───────────────────────────────────────────────────────── -->
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
            for i, s in enumerate(sec["sources"], 1):
                doi  = s.get("doi") or extract_doi(s.get("url", ""))
                link = f"https://doi.org/{doi}" if doi else s.get("url", "")
                out += f"  {s.get('n', i)}. {s['title']} — {s['journal']}\n     {link}\n"
        out += "\n" + "—" * 54 + "\n\n"
    if digest.get("bottom_line"):
        out += f"THIS WEEK'S SINGLE ACTION\n{digest['bottom_line']}\n\n"
    out += f"neurodigest.io · Edition #{edition} · {date_str}\n"
    return out


# ── Supabase subscriber management ───────────────────────────────────────────
def fetch_supabase_subscribers() -> list[dict]:
    """Return confirmed subscribers [{email, topics, timezone}] from Supabase."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        return []
    try:
        from supabase import create_client
        sb = create_client(url, key)
        try:
            result = (
                sb.table("subscribers")
                  .select("email,topics,timezone")
                  .eq("status", "confirmed")
                  .execute()
            )
        except Exception:
            # timezone column might not exist yet — fall back without it
            result = (
                sb.table("subscribers")
                  .select("email,topics")
                  .eq("status", "confirmed")
                  .execute()
            )
        return result.data or []
    except Exception as e:
        print(f"  Supabase fetch error: {e}")
        return []


# ── Timezone-aware filtering ──────────────────────────────────────────────────

# Timezones considered "European" for the 14:00 delivery target.
# All others receive the digest any time during their local Monday.
_EUROPEAN_TZ_PREFIXES = ("Europe/", "Atlantic/Azores", "Atlantic/Canary",
                          "Atlantic/Madeira", "Africa/Ceuta")

def _is_european(tz_str: str) -> bool:
    return any(tz_str.startswith(p) for p in _EUROPEAN_TZ_PREFIXES)


def filter_for_monday_send(subscribers: list[dict]) -> list[dict]:
    """
    Return subscribers eligible to receive the digest in this cron run.

    Rules
    ─────
    European timezones  → it must be Monday AND local hour 13–15
                          (targets 14:00 delivery; 3-hour window absorbs
                          GitHub Actions timing jitter of up to ±1 h)
    All other timezones → it must be Monday (any hour 0–23 local)

    Timezone fallback: Europe/Rome for subscribers with no stored timezone
    (signed up before the timezone column was added).

    sends_log deduplication ensures each subscriber receives exactly one
    copy per digest regardless of how many cron runs hit their window.
    """
    now = datetime.now(timezone.utc)
    eligible: list[dict] = []
    for sub in subscribers:
        raw_tz = (sub.get("timezone") or "").strip() or "Europe/Rome"
        try:
            local_now = now.astimezone(ZoneInfo(raw_tz))
        except (ZoneInfoNotFoundError, Exception):
            local_now = now.astimezone(ZoneInfo("Europe/Rome"))
            raw_tz = "Europe/Rome"

        if local_now.weekday() != 0:   # not Monday locally → skip
            continue

        if _is_european(raw_tz):
            # Target 14:00 — accept 13:00–15:59 to absorb GitHub jitter
            if 13 <= local_now.hour <= 15:
                eligible.append(sub)
        else:
            # Non-European: any time on their local Monday
            eligible.append(sub)

    return eligible


# ── Deduplication helpers ─────────────────────────────────────────────────────

def get_already_sent(sb, digest_id: int) -> set[str]:
    """Return set of emails that already have a sends_log entry for this digest."""
    try:
        rows = (
            sb.table("sends_log")
              .select("email")
              .eq("digest_id", digest_id)
              .execute()
        )
        return {r["email"] for r in (rows.data or [])}
    except Exception as e:
        print(f"  Could not read sends_log: {e}")
        return set()


def log_sends(sb, emails: list[str], digest_id: int) -> None:
    """Insert sends_log rows; ignore conflicts (UNIQUE constraint is the guard)."""
    if not emails:
        return
    try:
        sb.table("sends_log").upsert(
            [{"email": e, "digest_id": digest_id} for e in emails],
            on_conflict="email,digest_id",
            ignore_duplicates=True,
        ).execute()
    except Exception as e:
        print(f"  Could not write sends_log: {e}")


# ── Digest persistence (generate once, reuse across hourly runs) ──────────────

def get_todays_digest(sb) -> dict | None:
    """
    Return the digest row already generated today, or None if not yet created.
    Returns: {id, edition_num, subject, html, plain, digest_json}
    """
    today_start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00+00:00")
    today_end   = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59+00:00")
    try:
        rows = (
            sb.table("digests")
              .select("id,edition_num,subject,html,plain,digest_json")
              .gte("sent_at", today_start)
              .lte("sent_at", today_end)
              .order("sent_at", desc=True)
              .limit(1)
              .execute()
        )
        if rows.data:
            return rows.data[0]
    except Exception as e:
        print(f"  Could not query today's digest: {e}")
    return None


def save_digest_to_supabase(sb, subject: str, html: str, plain: str,
                             edition_num: int, digest_data: dict) -> int | None:
    """
    Persist the generated digest (including structured JSON for per-subscriber
    topic filtering in subsequent hourly runs) and return its Supabase row id.
    """
    try:
        row = sb.table("digests").insert({
            "subject":     subject,
            "html":        html,
            "plain":       plain,
            "edition_num": edition_num,
            "digest_json": json.dumps(digest_data),   # enables personalisation on all runs
        }).execute()
        if row.data:
            return row.data[0]["id"]
    except Exception as e:
        print(f"  Could not save digest to Supabase: {e}")
    return None


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


def filter_digest_for_subscriber(
    digest: dict, topics: list[str]
) -> tuple[dict, list[dict]]:
    """
    Filter the digest to the subscriber's chosen topics.

    Returns:
        (personalized_digest, excluded_sections)
        excluded_sections — the sections not shown, used for "Also this week" teasers.
        If topics is empty (subscriber chose all), excluded is always [].
    """
    if not topics:
        return digest, []
    lc = [t.lower() for t in topics]
    all_sections = digest.get("sections", [])
    included = [
        sec for sec in all_sections
        if any(t in sec["topic"].lower() or sec["topic"].lower() in t for t in lc)
    ]
    if not included:
        # No match — send full digest, nothing to tease
        return digest, []
    excluded = [sec for sec in all_sections if sec not in included]
    return {**digest, "sections": included}, excluded


def send_personalized_via_resend(
    subscribers: list[dict],
    digest: dict,
    edition: int,
    *,
    sb=None,
    digest_id: int | None = None,
    already_sent: set[str] | None = None,
) -> bool:
    """
    Send per-subscriber personalized emails via Resend.

    When sb + digest_id are provided the function:
      - skips subscribers already in sends_log for this digest_id
      - logs newly sent addresses to sends_log after each successful send

    already_sent: pre-fetched set of emails to skip (avoids a DB round-trip per
                  subscriber; pass get_already_sent(sb, digest_id) before calling).
    """
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

    date_str   = datetime.now().strftime("%B %d, %Y")
    subject    = f"NeuroDigest — {date_str}"
    sent       = 0
    skipped    = 0
    sent_addrs: list[str] = []

    skip_set = already_sent or set()

    for sub in subscribers:
        email  = sub["email"]
        topics = sub.get("topics") or []

        # ── Deduplication: skip if already sent this digest ───────────────────
        if email in skip_set:
            skipped += 1
            continue

        token                  = generate_preferences_token(email)
        personalized, excluded = filter_digest_for_subscriber(digest, topics)
        # Show "Also This Week" teasers only when subscriber chose 1–2 topics
        # (email would otherwise feel thin); with 3+ topics the content is already rich
        show_also = excluded if len(topics) <= 2 else []
        html      = build_html_email(
                        personalized, edition,
                        preferences_token=token, site_url=site_url,
                        also_this_week=show_also,
                    )
        html      = ensure_unsubscribe(html, token, site_url)
        plain     = build_plain_text(personalized, edition)

        try:
            resend.Emails.send({
                "from":    from_addr,
                "to":      email,
                "subject": subject,
                "html":    html,
                "text":    plain,
            })
            sent += 1
            sent_addrs.append(email)
            skip_set.add(email)        # guard against duplicate entries in input list
            time.sleep(0.15)
        except Exception as e:
            print(f"  Resend error ({email}): {e}")

    # ── Log successful sends to deduplication table ───────────────────────────
    if sb and digest_id and sent_addrs:
        log_sends(sb, sent_addrs, digest_id)

    total = len(subscribers)
    print(f"  Sent {sent}/{total} personalized emails via Resend "
          f"(skipped {skipped} already-sent)")
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


# ── Monthly Guidelines Edition ────────────────────────────────────────────────

# Fixed monthly rotation — January = index 0, February = index 1, …
# 12 topics, one per month, cycling each year.
GUIDELINES_ROTATION = [
    "Stroke",                                    # January
    "Multiple Sclerosis",                         # February
    "Parkinson's Disease",                        # March
    "Epilepsy",                                   # April
    "Headache and Migraine",                      # May
    "Neuroinfectious Disease",                    # June  (dementia rimossa temporaneamente)
    "Neuromuscular Disease",                      # July
    "Neuro-oncology",                             # August
    "Neuroinflammation and Neuroimmunology",      # September
    "Movement Disorders",                         # October
    "Neurocritical Care",                         # November
    "Neurogenetics",                              # December
]


def is_last_monday_of_month() -> bool:
    """True if today (UTC) is the last Monday of the current calendar month."""
    today = datetime.now(timezone.utc)
    if today.weekday() != 0:   # 0 = Monday
        return False
    return (today + timedelta(days=7)).month != today.month


def guideline_sent_this_month(sb) -> bool:
    """Return True if a guidelines edition was already sent during the current month."""
    try:
        now   = datetime.now(timezone.utc)
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        rows  = (
            sb.table("guidelines_log")
              .select("id")
              .gte("sent_at", start)
              .limit(1)
              .execute()
        )
        return bool(rows.data)
    except Exception:
        return False


def guidelines_topic_this_month() -> str:
    """Return the guidelines topic scheduled for the current calendar month."""
    month_index = datetime.now().month - 1   # 0-based
    return GUIDELINES_ROTATION[month_index % len(GUIDELINES_ROTATION)]


def synthesize_guideline(
    macro_topic: str,
    client: anthropic.Anthropic,
    already_sent: list[str] | None = None,
) -> dict | None:
    """
    Ask Claude to:
      1. Pick a specific condition / syndrome within macro_topic that has NOT been
         covered before (already_sent list passed in from the guidelines_log table).
      2. Produce a focused, practice-ready guidelines synthesis for that specific topic.
    """
    exclusion_block = ""
    if already_sent:
        exclusion_list = "\n".join(f"  - {t}" for t in already_sent)
        exclusion_block = f"""
IMPORTANT — the following sub-topics have already been covered in previous editions.
Do NOT choose any of them. Pick something meaningfully different:
{exclusion_list}
"""

    prompt = f"""You are the editor of NeuroDigest, producing a special Monthly Guidelines Edition for practicing neurologists.

The broad area for this month is: {macro_topic}
{exclusion_block}
Step 1 — choose a SPECIFIC condition, syndrome, or clinical scenario within that area that:
  • has published clinical practice guidelines or consensus statements from an authoritative body
  • is genuinely encountered in neurology practice
  • is specific enough that a focused synthesis is meaningful
  • Examples of the right level of specificity: Parsonage-Turner syndrome, NMOSD-AQP4,
    convulsive status epilepticus, PSP, CADASIL, myasthenic crisis, GBS (AIDP),
    LGI1 encephalitis, Huntington's disease, hereditary spastic paraplegia, etc.

Step 2 — write a comprehensive, practice-ready synthesis of current guidelines for that specific topic.

Return ONLY valid JSON, no markdown, no text outside the JSON.

Format:
{{
  "macro_topic": "{macro_topic}",
  "specific_topic": "Name of the specific condition / syndrome you chose",
  "guideline_headline": "One sentence — the single most practice-relevant recommendation from current guidelines",
  "themes": [
    {{
      "title": "THEME TITLE IN CAPS (e.g. WHEN TO SUSPECT, DIAGNOSTIC WORKUP, FIRST-LINE TREATMENT, MONITORING, SPECIAL POPULATIONS)",
      "body": "2–3 short paragraphs synthesizing current guideline recommendations. Cite issuing body and year in parentheses, e.g. (AAN, 2023). Direct, specialist-level prose. No bullet points.",
      "implication": "One concrete clinical action starting with an action verb."
    }}
  ],
  "key_recommendations": [
    "Specific, actionable recommendation — cite guideline body and year"
  ],
  "sources": [
    {{
      "title": "Full guideline title",
      "issuing_body": "AAN / EAN / AHA-ASA / ECTRIMS / ESO / ENMC / etc.",
      "year": "2023",
      "doi": "10.xxxx/yyyy",
      "url": "https://..."
    }}
  ],
  "bottom_line": "The single most important practice change from current guidelines."
}}

Rules:
- Cite only real, published guidelines or consensus statements
- 3–5 themes covering recognition, workup, treatment, and monitoring
- Key recommendations: 4–6 items, concrete and numbered
- If the most recent guideline is from 2021 or earlier, note that updated guidance may be pending
- Write at the level of a senior neurology resident or consultant
- Include real DOIs and URLs where you know them; if uncertain, set to ""
"""
    response = client.messages.create(
        model="claude-opus-4-7",
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
    )
    if not response.content:
        return None
    raw = response.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def build_guidelines_html_email(
    guideline: dict,
    token: str = "",
    site_url: str = "",
    **kwargs,
) -> str:
    """Build HTML for a Monthly Guidelines Edition email (gold accent, distinct from weekly)."""
    date_str     = datetime.now().strftime("%B %d, %Y")
    macro_topic  = guideline.get("macro_topic", guideline.get("topic", "Neurology"))
    specific     = guideline.get("specific_topic", macro_topic)
    color        = GUIDELINES_ACC

    # Themes
    themes_html = ""
    for th in guideline.get("themes", []):
        body_paras = "".join(
            f'<p class="bt" style="margin:0 0 13px 0;font-size:15px;line-height:1.8;color:#2c2c2c;'
            f'font-family:Georgia,\'Times New Roman\',serif">{p.strip()}</p>'
            for p in th["body"].split("\n") if p.strip()
        )
        impl = th.get("implication", "")
        impl_html = ""
        if impl:
            impl_html = f"""
            <table width="100%" cellpadding="0" cellspacing="0" border="0"
                   style="margin:14px 0 4px">
              <tr>
                <td style="width:3px;background:{color}"></td>
                <td style="padding:10px 14px;background:#f7f7f5">
                  <p style="margin:0;font-size:13px;line-height:1.7;color:#444;
                            font-style:italic;
                            font-family:Georgia,'Times New Roman',serif">{impl}</p>
                </td>
              </tr>
            </table>"""
        themes_html += f"""
        <div style="margin-bottom:28px">
          <p class="sec-h" style="margin:0 0 10px 0;font-size:11px;font-weight:700;letter-spacing:2px;
                    color:{color};text-transform:uppercase;
                    font-family:Helvetica,Arial,sans-serif">{th['title']}</p>
          {body_paras}
          {impl_html}
        </div>"""

    # Key recommendations box
    recs = guideline.get("key_recommendations", [])
    recs_html = ""
    if recs:
        rec_items = "".join(
            f'<li class="rec-b" style="margin:0 0 10px 0;font-size:15px;color:#333;line-height:1.7;'
            f'font-family:Helvetica,Arial,sans-serif;word-break:break-word">{r}</li>'
            for r in recs
        )
        recs_html = f"""
        <div style="margin:24px 0 0 0;padding:20px 24px;background:#fdf8ee;
                    border:1px solid #dfc97a">
          <p style="margin:0 0 12px 0;font-size:10px;font-weight:700;letter-spacing:2px;
                    text-transform:uppercase;color:{color};
                    font-family:Helvetica,Arial,sans-serif">Key Recommendations</p>
          <ol style="margin:0;padding:0 0 0 20px">{rec_items}</ol>
        </div>"""

    # Sources — block layout (no width:1% table, avoids vertical-text on mobile)
    sources = guideline.get("sources", [])
    sources_html = ""
    if sources:
        items = ""
        for s in sources:
            doi    = s.get("doi", "")
            url    = s.get("url", "")
            href   = f"https://doi.org/{doi}" if doi else url
            issuer = f"{s.get('issuing_body', '')} {s.get('year', '')}".strip()
            title_text = s.get("title", "")
            if href:
                issuer_html = (
                    f'<a href="{href}" style="font-size:10px;font-weight:700;'
                    f'letter-spacing:1px;text-transform:uppercase;color:{color};'
                    f'font-family:Helvetica,Arial,sans-serif;text-decoration:none">'
                    f'{issuer}</a>'
                )
            else:
                issuer_html = (
                    f'<span style="font-size:10px;font-weight:700;letter-spacing:1px;'
                    f'text-transform:uppercase;color:#aaa;'
                    f'font-family:Helvetica,Arial,sans-serif">{issuer}</span>'
                )
            items += (
                f'<div style="margin:0 0 9px 0;line-height:1.5">'
                f'{issuer_html}'
                f'<span style="font-size:10px;color:#ccc;'
                f'font-family:Helvetica,Arial,sans-serif"> &mdash; </span>'
                f'<span style="font-size:12px;color:#666;'
                f'font-family:Helvetica,Arial,sans-serif">{title_text}</span>'
                f'</div>'
            )
        sources_html = f"""
        <div style="margin-top:20px;padding-top:14px;border-top:1px solid #e8e4d8">
          <p style="margin:0 0 10px;font-size:9px;font-weight:700;letter-spacing:2px;
                    text-transform:uppercase;color:#bbb;
                    font-family:Helvetica,Arial,sans-serif">Sources</p>
          {items}
        </div>"""

    manage_link   = (
        f'<a href="{site_url}/preferences?token={token}" '
        f'style="color:#999;text-decoration:none">Manage topics</a>'
        if token else ""
    )
    unsub_link    = (
        f'<a href="{site_url}/api/unsubscribe?token={token}" '
        f'style="color:#999;text-decoration:none">Unsubscribe</a>'
        if token else ""
    )
    sep           = ' &nbsp;&middot;&nbsp; ' if manage_link and unsub_link else ''
    visuals_block = kwargs.get("visuals_block", "")
    preheader_txt = guideline.get("bottom_line") or f"Guidelines Edition — {specific}"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NeuroDigest Guidelines — {specific}</title>
{MOBILE_CSS}
</head>
<body style="margin:0;padding:0;background:{BG};-webkit-text-size-adjust:100%">
{_preheader(preheader_txt)}
<table width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{BG};min-height:100%">
<tr><td align="center" style="padding:24px 8px">

  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="max-width:640px;background:{WHITE};border:1px solid #ddddd8">

    <!-- ── Masthead ─────────────────────────────────────────────────────── -->
    <tr><td class="eh" style="padding:22px 48px 20px;background:{NAV}">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="vertical-align:bottom">
            <p style="margin:0;font-size:28px;font-weight:700;color:{WHITE};
                      letter-spacing:-0.5px;
                      font-family:Georgia,'Times New Roman',serif">NeuroDigest</p>
            <p style="margin:5px 0 0;font-size:10px;letter-spacing:2px;
                      text-transform:uppercase;color:{color};
                      font-family:Helvetica,Arial,sans-serif">
              Guidelines Edition &nbsp;&middot;&nbsp; {macro_topic}
            </p>
          </td>
          <td class="ed" align="right" style="vertical-align:bottom">
            <p style="margin:0;font-size:11px;color:#8888aa;
                      font-family:Helvetica,Arial,sans-serif">{date_str}</p>
          </td>
        </tr>
      </table>
    </td></tr>
    <!-- Gold rule below masthead -->
    <tr><td style="height:3px;background:{color};font-size:0;line-height:0">&nbsp;</td></tr>

    <!-- ── Topic intro block ────────────────────────────────────────────── -->
    <tr><td class="toc" style="padding:20px 48px;background:#fdf8ee;
                               border-bottom:1px solid #e8e4d8">
      <p style="margin:0 0 4px;font-size:9px;font-weight:700;letter-spacing:2.5px;
                text-transform:uppercase;color:#c8a840;
                font-family:Helvetica,Arial,sans-serif">This Month's Focus</p>
      <p style="margin:0;font-size:13px;color:#555;font-family:Helvetica,Arial,sans-serif;
                line-height:1.6">{specific}</p>
    </td></tr>

    <!-- ── Headline + content ───────────────────────────────────────────── -->
    <tr><td class="ep" style="padding:36px 48px 0">
      <table width="100%" cellpadding="0" cellspacing="0" border="0">
        <tr>
          <td style="width:4px;background:{color};border-radius:2px" width="4"></td>
          <td style="padding:2px 0 0 22px">
            <p class="hl" style="margin:0 0 22px;font-size:21px;font-weight:700;color:{NAV};
                      line-height:1.3;font-family:Georgia,'Times New Roman',serif">
              {guideline.get('guideline_headline', '')}
            </p>
            {themes_html}
            {recs_html}
            {sources_html}
          </td>
        </tr>
      </table>
    </td></tr>

    <!-- ── Visual summary ───────────────────────────────────────────────── -->
    {visuals_block}

    <tr><td style="padding:16px 0 0"></td></tr>

    <!-- ── Take-Home ────────────────────────────────────────────────────── -->
    <tr><td class="ta" style="padding:32px 48px;background:{NAV}">
      <p style="margin:0 0 10px;font-size:9px;font-weight:700;letter-spacing:2.5px;
                text-transform:uppercase;color:{color};
                font-family:Helvetica,Arial,sans-serif">This Month's Guideline Take-Home</p>
      <p class="bt" style="margin:0;font-size:17px;color:{WHITE};line-height:1.7;
                font-family:Georgia,'Times New Roman',serif;font-style:italic">
        {guideline.get('bottom_line', '')}
      </p>
    </td></tr>

    <!-- ── Footer ───────────────────────────────────────────────────────── -->
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


def send_guidelines_edition(
    subscribers: list[dict],
    client: anthropic.Anthropic,
    sb,
) -> None:
    """
    Send the Monthly Guidelines Edition — same topic to all subscribers.
    Topic is determined by the current month (fixed rotation).
    """
    api_key   = os.getenv("RESEND_API_KEY")
    site_url  = os.getenv("SITE_URL", "https://neuro-digest-phi.vercel.app")
    from_addr = os.getenv("RESEND_FROM", "NeuroDigest <digest@neuro-digest.com>")
    if not api_key:
        print("  No RESEND_API_KEY — skipping Guidelines Edition")
        return

    import resend
    resend.api_key = api_key

    macro = guidelines_topic_this_month()
    print(f"  Guidelines macro topic this month: {macro}")

    # If this month's guidelines were already synthesized (e.g. the :00 cron
    # run already sent to European subscribers and now the :30 run is catching
    # UTC−1 / UTC−2 subscribers), reuse the existing digest instead of calling
    # Claude again.  sends_log dedup ensures no subscriber gets it twice.
    if guideline_sent_this_month(sb):
        print(f"  Guidelines already synthesized this month — "
              "resending to any unsent eligible subscribers.")
        try:
            now   = datetime.now(timezone.utc)
            start = now.replace(day=1, hour=0, minute=0,
                                second=0, microsecond=0).isoformat()
            rows  = (
                sb.table("digests")
                  .select("id,digest_json,subject")
                  .gte("sent_at", start)
                  .like("subject", "NeuroDigest Guidelines%")
                  .order("sent_at", desc=True)
                  .limit(1)
                  .execute()
            )
            if not rows.data:
                print("  No existing guidelines digest found — skipping.")
                return
            existing_gl   = rows.data[0]
            gl_digest_id  = existing_gl["id"]
            data          = json.loads(existing_gl.get("digest_json") or "{}")
            already_gl    = get_already_sent(sb, gl_digest_id)
            sent, addrs   = 0, []
            for sub in subscribers:
                if sub["email"] in already_gl:
                    continue
                token = generate_preferences_token(sub["email"])
                h     = build_guidelines_html_email(data, token=token,
                                                    site_url=site_url)
                h     = ensure_unsubscribe(h, token, site_url)
                try:
                    resend.Emails.send({
                        "from":    from_addr,
                        "to":      sub["email"],
                        "subject": existing_gl["subject"],
                        "html":    h,
                    })
                    sent += 1
                    addrs.append(sub["email"])
                    time.sleep(0.15)
                except Exception as e:
                    print(f"  Resend error ({sub['email']}): {e}")
            if addrs:
                log_sends(sb, addrs, gl_digest_id)
            print(f"  Guidelines resent: {sent} new subscriber(s)")
        except Exception as e:
            print(f"  Could not resend existing guidelines: {e}")
        return

    # Fresh synthesis for this month
    # Fetch sub-topics already sent under this macro area to avoid repeats
    already_sent_topics: list[str] = []
    try:
        log = sb.table("guidelines_log") \
                .select("specific_topic") \
                .eq("macro_topic", macro) \
                .execute()
        already_sent_topics = [r["specific_topic"] for r in (log.data or [])]
        if already_sent_topics:
            print(f"  Already sent for {macro}: {', '.join(already_sent_topics)}")
    except Exception as e:
        print(f"  Could not fetch guidelines log: {e}")

    print(f"  Synthesizing guidelines with Claude...")
    data = synthesize_guideline(macro, client, already_sent=already_sent_topics)
    if not data:
        print("  Synthesis failed — skipping Guidelines Edition")
        return

    specific = data.get("specific_topic", macro)
    print(f"  Specific topic chosen: {specific}")

    date_str = datetime.now().strftime("%B %d, %Y")
    subject  = f"NeuroDigest Guidelines — {specific}"
    html_gl  = build_guidelines_html_email(data, site_url=site_url)

    # Save to Supabase so new subscribers joining this month can also receive it.
    # digest_json is stored so future resend scripts can rebuild per-subscriber HTML
    # (with personalised tokens) without re-calling Claude.
    try:
        sb.table("digests").insert({
            "subject":     subject,
            "html":        html_gl,
            "plain":       f"NeuroDigest Guidelines — {specific}\n\n{data.get('bottom_line', '')}",
            "digest_json": json.dumps(data),
        }).execute()
    except Exception as e:
        print(f"  Could not save guidelines digest to Supabase: {e}")

    # Fetch which subscribers already received this guidelines edition
    # (use digest_id from the saved row so sends_log works consistently)
    gl_digest_id: int | None = None
    try:
        saved = sb.table("digests").select("id").eq("subject", subject) \
                  .order("sent_at", desc=True).limit(1).execute()
        if saved.data:
            gl_digest_id = saved.data[0]["id"]
    except Exception:
        pass
    already_sent_gl = get_already_sent(sb, gl_digest_id) if gl_digest_id else set()

    sent       = 0
    sent_addrs: list[str] = []
    for sub in subscribers:
        email = sub["email"]
        if email in already_sent_gl:
            continue
        token = generate_preferences_token(email)
        html  = build_guidelines_html_email(data, token=token, site_url=site_url)
        html  = ensure_unsubscribe(html, token, site_url)
        try:
            resend.Emails.send({
                "from":    from_addr,
                "to":      email,
                "subject": subject,
                "html":    html,
            })
            sent += 1
            sent_addrs.append(email)
            time.sleep(0.15)
        except Exception as e:
            print(f"  Resend error ({email}): {e}")

    if gl_digest_id and sent_addrs:
        log_sends(sb, sent_addrs, gl_digest_id)

    print(f"  Guidelines Edition sent: {sent}/{len(subscribers)}")

    # Log the specific sub-topic so it is never repeated
    if sent > 0:
        try:
            sb.table("guidelines_log").insert({
                "macro_topic":    macro,
                "specific_topic": specific,
            }).execute()
            print(f"  Logged: {macro} → {specific}")
        except Exception as e:
            print(f"  Could not log guidelines entry: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────
def run():
    """
    Cron entry point (fires every 30 min on Mon+Tue UTC: '0,30 * * * 1,2').

    Flow per invocation
    ──────────────────────────────────────────────────────────────────────────
    LAST MONDAY OF MONTH
      → Guidelines Edition ONLY (no weekly digest).
      → Sent exclusively to subscribers whose local clock shows Monday 14:xx.
      → sends_log dedup prevents any subscriber getting it twice.

    ALL OTHER MONDAYS
      → Weekly Digest.
      → Digest generated once per day (first run), cached in Supabase.
      → Sent only to subscribers whose local clock shows Monday 14:xx.
      → sends_log dedup prevents duplicates across the two :00/:30 cron runs.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("Set ANTHROPIC_API_KEY in .env")

    from supabase import create_client
    sb = create_client(
        os.getenv("SUPABASE_URL", ""),
        os.getenv("SUPABASE_SERVICE_KEY", ""),
    )

    # ── Subscriber list ───────────────────────────────────────────────────────
    print("Fetching confirmed subscribers from Supabase...")
    all_subscribers = fetch_supabase_subscribers()
    print(f"  {len(all_subscribers)} confirmed subscriber(s) total")

    if not all_subscribers:
        print("  No subscribers — nothing to send.")
        return

    now_utc  = datetime.now(timezone.utc).strftime("%H:%M UTC")
    eligible = filter_for_monday_send(all_subscribers)
    print(f"  {len(eligible)} eligible this run (UTC: {now_utc})"
          " — EU: Mon 13–15 local · Others: any Mon hour")
    if not eligible:
        print("  No eligible subscribers this run — nothing to send.")
        return

    # ── LAST MONDAY → Guidelines Edition only, no weekly digest ──────────────
    if is_last_monday_of_month():
        print("\nLast Monday of month — Guidelines Edition only (no weekly digest).")
        try:
            client = anthropic.Anthropic(api_key=api_key)
            send_guidelines_edition(eligible, client, sb)
        except Exception as e:
            print(f"  Guidelines Edition error: {e}")
        return

    # ── ALL OTHER MONDAYS → Weekly Digest ────────────────────────────────────

    # Step 1: generate digest once per day; reuse on subsequent runs
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
        print(f"  Digest already generated today "
              f"(edition #{edition}, id={digest_id}) — skipping synthesis.")
    else:
        client  = anthropic.Anthropic(api_key=api_key)
        edition = get_edition()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        print("Fetching RSS feeds...")
        all_articles = fetch_all_articles()
        articles     = select_articles(all_articles)
        print(f"Total: {len(articles)} articles selected for synthesis\n")

        print("Synthesizing full neurology digest with Claude...")
        digest_data = synthesize_all(articles, client)
        sections    = digest_data.get("sections", [])
        print(f"  {len(sections)} clinical areas identified")
        for s in sections:
            print(f"  · {s['topic']} ({len(s.get('themes', []))} themes)")
        print(f"  Bottom line: {digest_data.get('bottom_line','')[:80]}...")

        html  = build_html_email(digest_data, edition)
        plain = build_plain_text(digest_data, edition)

        (OUTPUT_DIR / "neuro_digest.html").write_text(html)
        (OUTPUT_DIR / "neuro_digest.txt").write_text(plain)
        print(f"\nDigest saved → {OUTPUT_DIR.resolve()}")

        date_str  = datetime.now().strftime("%B %d, %Y")
        subject   = f"NeuroDigest — {date_str}"
        digest_id = save_digest_to_supabase(sb, subject, html, plain, edition, digest_data)
        if digest_id:
            print(f"  Digest persisted to Supabase (id={digest_id})")
        else:
            print("  Warning: could not persist digest to Supabase")
            digest_id = None

    # Step 2: deduplication
    already_sent: set[str] = set()
    if digest_id:
        already_sent = get_already_sent(sb, digest_id)
        if already_sent:
            print(f"  {len(already_sent)} subscriber(s) already sent this digest — "
                  "skipping them.")

    # Step 3: send to eligible batch
    send_personalized_via_resend(
        eligible, digest_data, edition,
        sb=sb, digest_id=digest_id, already_sent=already_sent,
    )

    print(f"\nDone — Edition #{edition}")


if __name__ == "__main__":
    run()
