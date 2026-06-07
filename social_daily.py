"""
social_daily.py
Runs every day at 6:00 UTC (8:00 Italian time).
1. Fetches latest neurology articles from PubMed RSS
2. Claude picks the best + generates carousel content (7 slides)
3. Renders slides to PNG 1080x1080 via Playwright
4. Uploads to Supabase Storage
5. Saves to social_posts table + Notion
6. Sends preview email with APPROVE button
"""

import os, json, sys, re, tempfile
from pathlib import Path
from datetime import datetime, timezone
import urllib.request

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

import anthropic
from supabase import create_client
import resend as resend_lib
from playwright.sync_api import sync_playwright

# ── Config ────────────────────────────────────────────────────────────────────
PREVIEW_TO     = "vincenzolate95l@gmail.com"
SITE_URL       = os.getenv("SITE_URL", "https://www.neuro-digest.com").rstrip("/")
APPROVE_SECRET = os.getenv("SOCIAL_APPROVE_SECRET", "")

sb = create_client(os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SERVICE_KEY", ""))
resend_lib.api_key = os.getenv("RESEND_API_KEY", "")
from_addr = os.getenv("RESEND_FROM", "NeuroDigest <digest@neuro-digest.com>")
ai = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))

# ── 1. Fetch fresh articles from PubMed (always daily) ───────────────────────
ESEARCH_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    "?db=pubmed"
    "&term=(neurology[MeSH]+OR+stroke[MeSH]+OR+multiple+sclerosis[MeSH]"
    "+OR+epilepsy[MeSH]+OR+Parkinson+disease[MeSH]+OR+dementia[MeSH])"
    "+AND+(clinical+trial[pt]+OR+review[pt]+OR+guideline[pt]+OR+meta-analysis[pt])"
    "&retmax=20&sort=date&retmode=json&datetype=pdat&reldate=3"
)

def fetch_fresh_articles() -> list[dict]:
    """Fetch last 3 days of neurology articles from PubMed."""
    with urllib.request.urlopen(ESEARCH_URL, timeout=30) as r:
        data = json.loads(r.read())
    ids = data["esearchresult"]["idlist"]
    if not ids:
        return []
    ids_str = ",".join(ids[:20])
    summary_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id={ids_str}"
    with urllib.request.urlopen(summary_url, timeout=30) as r:
        summary = json.loads(r.read())
    articles = []
    for uid in ids[:20]:
        if uid not in summary["result"]:
            continue
        art = summary["result"][uid]
        title = art.get("title", "").strip().rstrip(".")
        if title:
            articles.append({
                "title":   title,
                "url":     f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                "journal": art.get("source", "PubMed"),
                "abstract": title,
            })
    return articles

def get_existing_notion_urls() -> set:
    """Get all URLs already saved in Notion to avoid duplicates."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DATABASE_ID", "")
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{notion_db_id}/query",
        data=json.dumps({"page_size": 100}).encode(),
        headers={"Authorization": f"Bearer {notion_token}",
                 "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        results = json.loads(r.read()).get("results", [])
    return {p["properties"].get("URL", {}).get("url", "") for p in results if p["properties"].get("URL", {}).get("url")}

def save_all_to_notion(articles: list[dict], existing_urls: set) -> list[dict]:
    """Save all new articles to Notion. Returns articles with notion_id filled in."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DATABASE_ID", "")
    today = datetime.now(timezone.utc).date().isoformat()
    saved = []
    for art in articles:
        if art["url"] in existing_urls:
            continue  # already in Notion — skip
        payload = {
            "parent": {"database_id": notion_db_id},
            "properties": {
                "Nome":      {"title": [{"text": {"content": art["title"][:200]}}]},
                "URL":       {"url": art["url"]},
                "Journal":   {"select": {"name": art.get("journal", "PubMed")[:100]}},
                "Status":    {"select": {"name": "New"}},
                "Use for":   {"multi_select": [{"name": "Social"}]},
                "Published": {"date": {"start": today}},
            }
        }
        req = urllib.request.Request(
            "https://api.notion.com/v1/pages",
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Bearer {notion_token}",
                     "Notion-Version": "2022-06-28",
                     "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as r:
            page_id = json.loads(r.read())["id"]
        art["notion_id"] = page_id
        saved.append(art)
    return saved

# ── Kept for compatibility ────────────────────────────────────────────────────
def fetch_articles() -> list[dict]:
    """Read New articles from Notion not yet used for Social."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DATABASE_ID", "")

    payload = {
        "filter": {
            "and": [
                {"property": "Status", "select": {"equals": "New"}},
            ]
        },
        "sorts": [{"property": "Published", "direction": "descending"}],
        "page_size": 20,
    }
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{notion_db_id}/query",
        data=json.dumps(payload).encode(),
        headers={
            "Authorization":  f"Bearer {notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type":   "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    articles = []
    for page in data.get("results", []):
        props = page["properties"]
        title   = "".join(t["plain_text"] for t in props.get("Nome", {}).get("title", []))
        url     = props.get("URL", {}).get("url") or ""
        journal = (props.get("Journal", {}).get("select") or {}).get("name", "")
        summary = "".join(t["plain_text"] for t in props.get("Summary", {}).get("rich_text", []))
        # Skip if already used for Social
        use_for = [o["name"] for o in props.get("Use for", {}).get("multi_select", [])]
        if "Social" in use_for:
            continue
        if title:
            articles.append({
                "title":        title,
                "url":          url,
                "journal":      journal,
                "abstract":     summary[:500] if summary else title,
                "notion_id":    page["id"],
            })
    return articles

# ── 2. Claude: pick best + generate carousel ──────────────────────────────────
def generate_content(articles: list[dict]) -> dict:
    articles_text = "\n".join(
        f"{i+1}. {a['title']}\n   Abstract: {a['abstract'][:250]}"
        for i, a in enumerate(articles)
    )
    prompt = f"""You are the editor of NeuroDigest, a professional weekly neurology newsletter.

Choose the single most clinically interesting and impactful article from the list below.
Create a 7-slide Instagram carousel in English. Be concise, professional, clinically relevant.

Articles:
{articles_text}

Return ONLY valid JSON with this exact structure:
{{
  "article_title": "...",
  "article_url": "...",
  "journal": "...",
  "slides": [
    {{"type": "cover",   "topic": "2-3 word topic tag", "headline": "engaging question or statement (max 12 words)"}},
    {{"type": "content", "label": "The Context",        "text": "2-3 sentences of background (max 50 words)", "highlight": "key quote or stat (max 20 words)"}},
    {{"type": "content", "label": "The Study",          "text": "what they did and found (max 50 words)",      "highlight": "main result (max 20 words)"}},
    {{"type": "stat",    "number": "key number or acronym", "label": "what this number means (max 15 words)"}},
    {{"type": "content", "label": "Clinical Takeaway",  "text": "practical implication (max 40 words)",        "highlight": "1 sentence for the clinician (max 20 words)"}},
    {{"type": "source",  "journal": "journal name",     "year": "2025 or 2026", "url": "short URL"}},
    {{"type": "cta"}}
  ],
  "fb_text": "3-4 sentences about the clinical finding and its implication, written as a single flowing paragraph (no line breaks). Direct, informative, English. Do NOT include URLs, journal name, hashtags, or 'Follow NeuroDigest' — these are added automatically."
}}"""

    msg = ai.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    text = msg.content[0].text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())

# ── 3. Build slide HTML ───────────────────────────────────────────────────────
def dots_html(current: int, total: int, light: bool = False, show: bool = True) -> str:
    if not show:
        return ""
    base = "rgba(255,255,255,.2)" if light else "#ddd"
    on   = "#c0392b"
    return (
        '<div style="display:flex;gap:10px;margin-top:48px">'
        + "".join(
            f'<div style="width:12px;height:12px;border-radius:50%;'
            f'background:{on if i == current else base}"></div>'
            for i in range(total)
        )
        + "</div>"
    )

def build_slide_html(slide: dict, idx: int, total: int, show_dots: bool = True) -> str:
    d = dots_html(idx, total, light=(slide["type"] in ("cover", "stat", "cta")), show=show_dots)

    if slide["type"] == "cover":
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1080px;background:#1a1a2e;display:flex;flex-direction:column;
     justify-content:space-between;padding:72px 64px;font-family:Georgia,serif}}
</style></head><body>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:26px;color:#c0392b;
            letter-spacing:.15em;text-transform:uppercase">NeuroDigest</div>
<div>
  <div style="font-family:Helvetica,Arial,sans-serif;font-size:18px;letter-spacing:.2em;
              text-transform:uppercase;color:rgba(255,255,255,.4);margin-bottom:20px">
    {slide['topic']}</div>
  <div style="font-size:52px;line-height:1.35;color:#fff">{slide['headline']}</div>
</div>
<div>
  {d}
  <div style="font-family:Helvetica,Arial,sans-serif;font-size:20px;
              color:rgba(255,255,255,.25);margin-top:16px">neuro-digest.com · {datetime.now().year}</div>
</div>
</body></html>"""

    if slide["type"] == "content":
        hl = (
            f'<div style="margin-top:32px;padding:24px 32px;border-left:5px solid #c0392b;background:#f8f8f6">'
            f'<p style="font-size:28px;line-height:1.6;color:#444;font-style:italic">'
            f'{slide.get("highlight","")}</p></div>'
            if slide.get("highlight") else ""
        )
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1080px;background:#fff;display:flex;flex-direction:column;
     justify-content:center;padding:80px;font-family:Georgia,serif}}
</style></head><body>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:18px;font-weight:700;
            letter-spacing:.25em;text-transform:uppercase;color:#c0392b;margin-bottom:28px">
  {slide['label']}</div>
<div style="font-size:34px;line-height:1.8;color:#2c2c2c">{slide['text']}</div>
{hl}
{d}
</body></html>"""

    if slide["type"] == "stat":
        num = slide['number']
        font_size = "160px" if len(num) <= 4 else "100px" if len(num) <= 8 else "72px"
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1080px;background:#1a1a2e;display:flex;flex-direction:column;
     justify-content:center;align-items:center;text-align:center;padding:80px;font-family:Georgia,serif}}
</style></head><body>
<div style="font-size:{font_size};color:#fff;line-height:1;margin-bottom:40px">{num}</div>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:28px;letter-spacing:.12em;
            text-transform:uppercase;color:rgba(255,255,255,.5);max-width:700px;line-height:1.6">
  {slide['label']}</div>
{d}
</body></html>"""

    if slide["type"] == "source":
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1080px;background:#f5f5f3;border-top:8px solid #c0392b;
     display:flex;flex-direction:column;justify-content:center;padding:80px;font-family:Georgia,serif}}
</style></head><body>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:18px;letter-spacing:.25em;
            text-transform:uppercase;color:#aaa;margin-bottom:24px">Source</div>
<div style="font-size:52px;color:#1a1a2e;margin-bottom:12px">{slide['journal']}</div>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:28px;color:#888;margin-bottom:40px">
  {slide['year']}</div>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:22px;color:#c0392b;
            word-break:break-all;line-height:1.5">{slide['url']}</div>
{d}
</body></html>"""

    if slide["type"] == "cta":
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1080px;background:#c0392b;display:flex;flex-direction:column;
     justify-content:center;align-items:center;text-align:center;padding:80px;font-family:Georgia,serif}}
</style></head><body>
<div style="font-size:72px;color:#fff;margin-bottom:24px">NeuroDigest</div>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:22px;letter-spacing:.18em;
            text-transform:uppercase;color:rgba(255,255,255,.7);margin-bottom:60px;
            line-height:1.7;max-width:500px">Free weekly neurology newsletter</div>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:36px;color:#fff;font-weight:700;
            border-bottom:2px solid rgba(255,255,255,.5);padding-bottom:6px">neuro-digest.com</div>
</body></html>"""

    return ""

# ── Story cover HTML 1080x1920 ────────────────────────────────────────────────
def build_story_cover_html(cover_slide: dict) -> str:
    """Vertical 1080x1920 cover for Instagram + Facebook Stories."""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1920px;background:#1a1a2e;display:flex;flex-direction:column;
     justify-content:space-between;padding:120px 80px;font-family:Georgia,serif}}
</style></head><body>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:32px;color:#c0392b;
            letter-spacing:.15em;text-transform:uppercase">NeuroDigest</div>
<div>
  <div style="font-family:Helvetica,Arial,sans-serif;font-size:24px;letter-spacing:.2em;
              text-transform:uppercase;color:rgba(255,255,255,.4);margin-bottom:32px">
    {cover_slide.get('topic','')}</div>
  <div style="font-size:72px;line-height:1.3;color:#fff">
    {cover_slide.get('headline','')}</div>
</div>
<div style="font-family:Helvetica,Arial,sans-serif;font-size:28px;
            color:rgba(255,255,255,.25)">neuro-digest.com</div>
</body></html>"""

# ── 4a. Generate MP4 Reel from PNG slides ────────────────────────────────────
def generate_reel_mp4(slide_paths: list[Path], out_dir: Path, seconds_per_slide: int = 3) -> Path:
    """Stitch PNG slides into an MP4 video at 3 sec/slide for Instagram Reel."""
    from moviepy import ImageClip, concatenate_videoclips
    clips = [ImageClip(str(p)).with_duration(seconds_per_slide) for p in slide_paths]
    video  = concatenate_videoclips(clips, method="compose")
    output = out_dir / "reel.mp4"
    video.write_videofile(str(output), fps=30, codec="libx264",
                          audio=False, logger=None)
    print(f"  Reel MP4 generated: {output.name} ({len(slide_paths)} slides × {seconds_per_slide}s)")
    return output

# ── 4b. Render PNG ────────────────────────────────────────────────────────────
def render_slides(slides: list[dict], out_dir: Path) -> tuple[list[Path], Path]:
    """Returns (ig_slide_paths, fb_cover_path)"""
    paths = []
    total = len(slides)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page    = browser.new_page(viewport={"width": 1080, "height": 1080})

        # Instagram slides — WITH dots
        for i, slide in enumerate(slides):
            page.set_content(build_slide_html(slide, i, total, show_dots=True), wait_until="networkidle")
            path = out_dir / f"slide_{i+1:02d}.png"
            page.screenshot(path=str(path), full_page=False)
            paths.append(path)
            print(f"  Slide {i+1}/{total} rendered")

        # Facebook/post cover — first slide WITHOUT dots (1080x1080)
        page.set_content(build_slide_html(slides[0], 0, total, show_dots=False), wait_until="networkidle")
        fb_cover = out_dir / "fb_cover.png"
        page.screenshot(path=str(fb_cover), full_page=False)
        print(f"  FB cover rendered (no dots)")

        # Story cover — first slide vertical 1080x1920 (no dots)
        page.set_viewport_size({"width": 1080, "height": 1920})
        story_html = build_story_cover_html(slides[0])
        page.set_content(story_html, wait_until="networkidle")
        story_cover = out_dir / "story_cover.png"
        page.screenshot(path=str(story_cover), full_page=False)
        print(f"  Story cover rendered (1080x1920)")

        browser.close()
    return paths, fb_cover, story_cover

# ── 5. Upload to Supabase Storage ────────────────────────────────────────────
def upload_images(paths: list[Path], post_id: str) -> list[str]:
    urls = []
    for path in paths:
        storage_path = f"{post_id}/{path.name}"
        with open(path, "rb") as f:
            data = f.read()
        sb.storage.from_("social-images").upload(
            storage_path, data,
            file_options={"content-type": "image/png", "upsert": "true"}
        )
        public_url = sb.storage.from_("social-images").get_public_url(storage_path)
        urls.append(public_url)
    return urls

# ── 6. Save or update article in Notion ──────────────────────────────────────
def create_notion_article(article: dict) -> str:
    """Create a new Notion page for an article coming from PubMed fallback."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DATABASE_ID", "")
    today = datetime.now(timezone.utc).date().isoformat()
    payload = {
        "parent": {"database_id": notion_db_id},
        "properties": {
            "Nome":      {"title": [{"text": {"content": article["title"][:200]}}]},
            "URL":       {"url": article["url"]},
            "Journal":   {"select": {"name": article.get("journal", "PubMed")[:100]}},
            "Status":    {"select": {"name": "Scheduled"}},
            "Use for":   {"multi_select": [{"name": "Social"}]},
            "Published": {"date": {"start": today}},
        }
    }
    req = urllib.request.Request(
        "https://api.notion.com/v1/pages",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {notion_token}",
                 "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())["id"]

def update_notion_article(notion_id: str) -> None:
    """Mark existing Notion article as Scheduled + add Social to Use for."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    payload = {
        "properties": {
            "Status":  {"select": {"name": "Scheduled"}},
            "Use for": {"multi_select": [{"name": "Mail"}, {"name": "Social"}]},
        }
    }
    req = urllib.request.Request(
        f"https://api.notion.com/v1/pages/{notion_id}",
        data=json.dumps(payload).encode(),
        method="PATCH",
        headers={
            "Authorization":  f"Bearer {notion_token}",
            "Notion-Version": "2022-06-28",
            "Content-Type":   "application/json",
        }
    )
    with urllib.request.urlopen(req) as r:
        r.read()

# ── 7. Send two preview emails (Instagram + Facebook) ────────────────────────
def send_preview(content: dict, slide_urls: list[str], post_id: str):
    today_str  = datetime.now().strftime("%A, %d %B %Y")
    title_short = content['article_title'][:55]

    ig_approve             = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=ig"
    fb_approve             = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=fb"
    ig_story_approve       = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=ig_story"
    fb_story_approve       = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=fb_story"
    ig_story_video_approve = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=ig_story_video"
    fb_story_video_approve = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=fb_story_video"

    fb_full = (
        f"{content['fb_text']}\n"
        f"📋 {content.get('journal','')}\n"
        f"{content['article_url']}\n"
        f"🔗 Newsletter: https://www.neuro-digest.com"
    )

    # ── Email 1: Instagram carousel ───────────────────────────────────────────
    slides_html = "\n".join(
        f'<img src="{url}" width="480" style="display:block;margin:0 auto 8px;'
        f'border-radius:8px;max-width:100%">'
        for url in slide_urls
    )
    html_ig = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background:#f4f3f0;font-family:Helvetica,Arial,sans-serif">
<div style="max-width:600px;margin:0 auto">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f0f7f0;border-top:3px solid #E1306C;border-radius:8px;margin-bottom:20px">
    <tr><td style="padding:24px;text-align:center">
      <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;
                text-transform:uppercase;color:#E1306C">Instagram · {today_str}</p>
      <p style="margin:0 0 16px;font-size:13px;color:#555">{len(slide_urls)} slides · 1080×1080px</p>
      <a href="{ig_approve}"
         style="display:inline-block;background:#E1306C;color:#fff;font-size:14px;
                font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px;margin-bottom:10px">
        ✅ &nbsp;APPROVE Carousel
      </a>
      <br>
      <a href="{ig_story_approve}"
         style="display:inline-block;background:#833AB4;color:#fff;font-size:14px;
                font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px;margin-top:8px">
        ✅ &nbsp;APPROVE Story — Immagini (tocca per avanzare)
      </a>
      <br>
    </td></tr>
  </table>
  <div style="background:#fff;padding:20px 24px;margin-bottom:16px;border-radius:8px">
    <p style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin:0 0 8px">Article</p>
    <p style="font-size:15px;color:#1a1a2e;font-weight:700;margin:0 0 4px">{content['article_title']}</p>
    <a href="{content['article_url']}" style="font-size:12px;color:#c0392b">{content['article_url']}</a>
  </div>
  <div style="background:#fff;padding:20px 24px;border-radius:8px">
    <p style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin:0 0 16px">
      Carousel Slides</p>
    {slides_html}
  </div>
</div></body></html>"""

    resend_lib.Emails.send({
        "from":    from_addr,
        "to":      PREVIEW_TO,
        "subject": f"[Instagram Preview] {title_short}",
        "html":    html_ig,
        "text":    f"Instagram Preview\n{content['article_title']}\nApprove: {ig_approve}",
    })
    print(f"✓ Instagram preview sent")

    # ── Email 2: Facebook post ────────────────────────────────────────────────
    html_fb = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background:#f4f3f0;font-family:Helvetica,Arial,sans-serif">
<div style="max-width:600px;margin:0 auto">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#e8f0fe;border-top:3px solid #1877f2;border-radius:8px;margin-bottom:20px">
    <tr><td style="padding:24px;text-align:center">
      <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;
                text-transform:uppercase;color:#1877f2">Facebook · {today_str}</p>
      <p style="margin:0 0 16px;font-size:13px;color:#555">Cover image + post text</p>
      <a href="{fb_approve}"
         style="display:inline-block;background:#1877f2;color:#fff;font-size:14px;
                font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px;margin-bottom:10px">
        ✅ &nbsp;APPROVE Post
      </a>
      <br>
      <a href="{fb_story_approve}"
         style="display:inline-block;background:#0e7c5a;color:#fff;font-size:14px;
                font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px;margin-top:8px">
        ✅ &nbsp;APPROVE Story — Immagini (tocca per avanzare)
      </a>
      <br>
    </td></tr>
  </table>
  <div style="background:#fff;padding:20px 24px;margin-bottom:16px;border-radius:8px">
    <p style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin:0 0 12px">Cover Image</p>
    <img src="{slide_urls[0]}" width="480" style="display:block;margin:0 auto;border-radius:8px;max-width:100%">
  </div>
  <div style="background:#fff;padding:20px 24px;border-radius:8px">
    <p style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin:0 0 12px">Post Text</p>
    <div style="background:#f5f6f7;border-radius:8px;padding:20px;white-space:pre-wrap;
                font-size:15px;color:#1c1e21;line-height:1.7">{fb_full}</div>
  </div>
</div></body></html>"""

    resend_lib.Emails.send({
        "from":    from_addr,
        "to":      PREVIEW_TO,
        "subject": f"[Facebook Preview] {title_short}",
        "html":    html_fb,
        "text":    f"Facebook Preview\n\n{fb_full}\n\nApprove: {fb_approve}",
    })
    print(f"✓ Facebook preview sent")

def send_reel_email(content: dict, reel_url: str):
    """Email 3: Reel MP4 download link — user adds music and posts manually."""
    title_short = content['article_title'][:55]
    html_reel = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background:#f4f3f0;font-family:Helvetica,Arial,sans-serif">
<div style="max-width:600px;margin:0 auto">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#1a1a2e;border-top:3px solid #c0392b;border-radius:8px;margin-bottom:20px">
    <tr><td style="padding:24px;text-align:center">
      <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;
                text-transform:uppercase;color:#c0392b">Instagram Reel</p>
      <p style="margin:0 0 16px;font-size:13px;color:rgba(255,255,255,.7)">
        7 slides · 3 sec each · 21 sec total · 1080×1080px<br>
        Scarica e usalo come <strong>Reel</strong> (aggiungi musica) o come <strong>Story video</strong> (scorre da solo).
      </p>
      <a href="{reel_url}"
         style="display:inline-block;background:#c0392b;color:#fff;font-size:14px;
                font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px">
        ⬇️ &nbsp;SCARICA MP4
      </a>
    </td></tr>
  </table>
  <div style="background:#fff;padding:20px 24px;border-radius:8px">
    <p style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin:0 0 12px">
      Come pubblicare</p>
    <p style="font-size:13px;color:#888;margin:0 0 8px"><strong>Come Reel:</strong></p>
    <ol style="font-size:14px;color:#333;line-height:2;margin:0 0 16px;padding-left:20px">
      <li>Scarica l'MP4</li>
      <li>Instagram → + → Reel → carica video → aggiungi musica → pubblica</li>
    </ol>
    <p style="font-size:13px;color:#888;margin:0 0 8px"><strong>Come Story video:</strong></p>
    <ol style="font-size:14px;color:#333;line-height:2;margin:0;padding-left:20px">
      <li>Scarica l'MP4</li>
      <li>Instagram → + → Storia → carica video → pubblica</li>
    </ol>
  </div>
</div></body></html>"""

    resend_lib.Emails.send({
        "from":    from_addr,
        "to":      PREVIEW_TO,
        "subject": f"[Reel MP4] {title_short}",
        "html":    html_reel,
        "text":    f"Reel MP4 pronto.\nScarica: {reel_url}\nAggiungi musica su Instagram e pubblica come Reel.",
    })
    print(f"✓ Reel email sent")

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== NeuroDigest Social Daily ===")

    print("\n[1/6] Fetching fresh articles from PubMed...")
    fresh = fetch_fresh_articles()
    print(f"      {len(fresh)} articles found")
    if not fresh:
        print("No articles found — exiting.")
        sys.exit(0)

    print("      Checking existing Notion URLs (no duplicates)...")
    existing_urls = get_existing_notion_urls()
    new_articles = [a for a in fresh if a["url"] not in existing_urls]
    print(f"      {len(new_articles)} new articles to save to Notion")

    if new_articles:
        saved = save_all_to_notion(new_articles, existing_urls)
        print(f"      {len(saved)} articles saved to Notion")
    else:
        print("      All articles already in Notion")

    # All fresh articles (with notion_id if newly saved, empty if already existed)
    articles = fresh
    for a in articles:
        if not a.get("notion_id"):
            a["notion_id"] = ""  # will be set properly after matching

    print("\n[2/6] Generating carousel with Claude...")
    content = generate_content(articles)
    # Match generated content back to original article to get REAL url + notion_id
    matched = None
    for a in articles:
        if a["title"][:40].lower() in content.get("article_title","").lower() or \
           content.get("article_title","")[:40].lower() in a["title"].lower():
            matched = a
            break
    if not matched and articles:
        matched = articles[0]  # fallback: first article
    if matched:
        content["article_url"] = matched["url"]   # always use real URL
        content["journal"]     = matched.get("journal", content.get("journal", ""))
        content["notion_id"]   = matched.get("notion_id", "")
    print(f"      {content['article_title'][:70]}")
    print(f"      URL: {content['article_url']}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        print("\n[3/6] Rendering slides to PNG...")
        slide_paths, fb_cover_path, story_cover_path = render_slides(content["slides"], tmp_path)

        print("\n[3b] Generating Reel MP4...")
        reel_path = generate_reel_mp4(slide_paths, tmp_path)

        print("\n[4/6] Saving post to Supabase...")
        row = sb.table("social_posts").insert({
            "article_title": content["article_title"],
            "article_url":   content["article_url"],
            "journal":       content.get("journal", ""),
            "fb_text":       content["fb_text"],
            "approved":      False,
        }).execute()
        post_id = row.data[0]["id"]
        print(f"      Post ID: {post_id}")

        print("\n[5/6] Uploading images + reel to Supabase Storage...")
        slide_urls      = upload_images(slide_paths, post_id)
        fb_cover_url    = upload_images([fb_cover_path], post_id)[0]
        story_cover_url = upload_images([story_cover_path], post_id)[0]
        # Upload reel MP4
        with open(reel_path, "rb") as f:
            reel_data = f.read()
        sb.storage.from_("social-images").upload(
            f"{post_id}/reel.mp4", reel_data,
            file_options={"content-type": "video/mp4", "upsert": "true"}
        )
        reel_url = sb.storage.from_("social-images").get_public_url(f"{post_id}/reel.mp4")
        sb.table("social_posts").update({
            "slide_urls":      slide_urls,
            "fb_cover_url":    fb_cover_url,
            "story_cover_url": story_cover_url,
            "reel_url":        reel_url,
        }).eq("id", post_id).execute()
        print(f"      {len(slide_urls)} IG slides + 1 FB cover + 1 Reel MP4 uploaded")

    print("\n[5b] Saving to Notion...")
    try:
        notion_id = content.get("notion_id", "")
        if notion_id:
            # Article already in Notion — mark as Scheduled + Social
            update_notion_article(notion_id)
            print(f"      Notion article updated: {notion_id}")
        else:
            # Article from PubMed fallback — create in Notion first
            matched_article = next(
                (a for a in articles if a["url"] == content.get("article_url")), articles[0]
            )
            notion_id = create_notion_article(matched_article)
            print(f"      Notion article created: {notion_id}")
        sb.table("social_posts").update({"notion_page_id": notion_id}).eq("id", post_id).execute()
    except Exception as e:
        print(f"      Notion warning: {e}")

    print("\n[6/6] Sending preview emails...")
    send_preview(content, slide_urls, post_id)
    send_reel_email(content, reel_url)

    print("\n✓ Done!")
