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

# ── 1a. Fetch from PubMed E-utilities (fallback) ─────────────────────────────
ESEARCH_URL = (
    "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    "?db=pubmed"
    "&term=(neurology[MeSH]+OR+stroke[MeSH]+OR+multiple+sclerosis[MeSH]"
    "+OR+epilepsy[MeSH]+OR+Parkinson+disease[MeSH]+OR+dementia[MeSH])"
    "+AND+(clinical+trial[pt]+OR+review[pt]+OR+guideline[pt]+OR+meta-analysis[pt])"
    "&retmax=20&sort=date&retmode=json&datetype=pdat&reldate=14"
)

def fetch_pubmed_articles() -> list[dict]:
    with urllib.request.urlopen(ESEARCH_URL, timeout=30) as r:
        data = json.loads(r.read())
    ids = data["esearchresult"]["idlist"]
    if not ids:
        return []
    ids_str = ",".join(ids[:15])
    summary_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id={ids_str}"
    with urllib.request.urlopen(summary_url, timeout=30) as r:
        summary = json.loads(r.read())
    articles = []
    for uid in ids[:15]:
        if uid not in summary["result"]:
            continue
        art = summary["result"][uid]
        title = art.get("title", "").strip().rstrip(".")
        if title:
            articles.append({
                "title":    title,
                "url":      f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                "journal":  art.get("source", "PubMed"),
                "abstract": title,
                "notion_id": "",
            })
    return articles

# ── 1b. Fetch articles from Notion (primary source) ───────────────────────────
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

# ── 4. Render PNG ─────────────────────────────────────────────────────────────
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

        # Facebook cover — first slide WITHOUT dots
        page.set_content(build_slide_html(slides[0], 0, total, show_dots=False), wait_until="networkidle")
        fb_cover = out_dir / "fb_cover.png"
        page.screenshot(path=str(fb_cover), full_page=False)
        print(f"  FB cover rendered (no dots)")

        browser.close()
    return paths, fb_cover

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

# ── 6. Update Notion article → mark as Scheduled for Social ──────────────────
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

    ig_approve = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=ig"
    fb_approve = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=fb"

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
                font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px">
        ✅ &nbsp;APPROVE — Post on Instagram at 14:00
      </a>
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
                font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px">
        ✅ &nbsp;APPROVE — Post on Facebook at 14:00
      </a>
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

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== NeuroDigest Social Daily ===")

    print("\n[1/6] Fetching articles from Notion...")
    articles = fetch_articles()
    print(f"      {len(articles)} articles found in Notion")
    if not articles:
        print("      Notion empty — falling back to PubMed...")
        articles = fetch_pubmed_articles()
        print(f"      {len(articles)} articles from PubMed")
    if not articles:
        print("No articles found — exiting.")
        sys.exit(0)

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
        slide_paths, fb_cover_path = render_slides(content["slides"], tmp_path)

        print("\n[4/6] Saving post to Supabase...")
        row = sb.table("social_posts").insert({
            "article_title": content["article_title"],
            "article_url":   content["article_url"],
            "journal":       content.get("journal", ""),
            "fb_text":       content["fb_text"],
            "ig_text":       content.get("ig_text", content["fb_text"]),
            "approved":      False,
        }).execute()
        post_id = row.data[0]["id"]
        print(f"      Post ID: {post_id}")

        print("\n[5/6] Uploading images to Supabase Storage...")
        slide_urls  = upload_images(slide_paths, post_id)
        # Upload FB cover separately
        fb_cover_url = upload_images([fb_cover_path], post_id)[0]
        sb.table("social_posts").update({
            "slide_urls":   slide_urls,
            "fb_cover_url": fb_cover_url,
        }).eq("id", post_id).execute()
        print(f"      {len(slide_urls)} IG slides + 1 FB cover uploaded")

    print("\n[5b] Updating Notion article...")
    try:
        notion_id = content.get("notion_id", "")
        if notion_id:
            update_notion_article(notion_id)
            sb.table("social_posts").update({"notion_page_id": notion_id}).eq("id", post_id).execute()
            print(f"      Notion page updated: {notion_id}")
    except Exception as e:
        print(f"      Notion warning: {e}")

    print("\n[6/6] Sending preview email...")
    send_preview(content, slide_urls, post_id)

    print("\n✓ Done!")
