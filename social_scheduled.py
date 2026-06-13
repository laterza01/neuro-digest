"""
social_scheduled.py
Elabora articoli Notion con Status=Scheduled e li converte in post social.
Domani (2026-06-15): elabora "Acute Kidney Injury..." → invia preview email.
"""

import os, json, sys, tempfile, re
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

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

# ── Fetch Scheduled article from Notion ────────────────────────────────────
def fetch_scheduled_article(title_keyword: str = "Acute Kidney") -> dict:
    """Fetch first Scheduled article from Notion matching keyword."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    notion_db_id = os.getenv("NOTION_DATABASE_ID", "")

    payload = {
        "filter": {"property": "Status", "select": {"equals": "Scheduled"}},
        "sorts": [{"property": "Published", "direction": "descending"}],
        "page_size": 10,
    }
    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{notion_db_id}/query",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {notion_token}",
                 "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())

    for page in data.get("results", []):
        props = page["properties"]
        title = "".join(t["plain_text"] for t in props.get("Nome", {}).get("title", []))
        if title_keyword.lower() in title.lower():
            url = props.get("URL", {}).get("url") or ""
            journal = (props.get("Journal", {}).get("select") or {}).get("name", "")
            summary = "".join(t["plain_text"] for t in props.get("Summary", {}).get("rich_text", []))
            return {
                "title": title,
                "url": url,
                "journal": journal,
                "abstract": summary[:500] if summary else title,
                "notion_id": page["id"],
            }
    return None

# ── Generate content (same as social_daily.py) ────────────────────────────
def generate_content(article: dict) -> dict:
    prompt = f"""You are the editor of NeuroDigest, a professional weekly neurology newsletter.

Create a 7-slide Instagram carousel in English for this article. Be concise, professional, clinically relevant.

Article:
Title: {article['title']}
Abstract: {article['abstract'][:300]}

Return ONLY valid JSON with this exact structure:
{{
  "article_title": "{article['title']}",
  "article_url": "{article['url']}",
  "journal": "{article['journal']}",
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

# ── Render + Upload (copied from social_daily.py) ─────────────────────────
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
<div style="font-family:Helvetica,Arial,sans-serif;font-size:26px;color:#c0392b;font-weight:700;margin-bottom:24px;text-transform:uppercase;letter-spacing:2px">{slide.get("topic","Neurology")}</div>
<div style="flex:1;display:flex;align-items:center"><h1 style="font-size:48px;line-height:1.3;color:#fff;margin:0">{slide.get("headline","")}</h1></div>
<div style="text-align:center;color:#999;font-size:14px">NeuroDigest</div>
{d}
</body></html>"""
    elif slide["type"] == "stat":
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1080px;background:#c0392b;display:flex;flex-direction:column;justify-content:center;
     align-items:center;padding:80px;text-align:center;font-family:Helvetica,Arial,sans-serif}}
</style></head><body>
<div style="font-size:96px;font-weight:700;color:#fff;margin-bottom:20px">{slide.get("number","?")}</div>
<div style="font-size:24px;color:#fff;line-height:1.4;max-width:600px">{slide.get("label","")}</div>
{d}
</body></html>"""
    else:  # content, source, cta
        highlight = slide.get("highlight","")
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{width:1080px;height:1080px;background:#fff;display:flex;flex-direction:column;justify-content:space-between;
     padding:80px;font-family:Helvetica,Arial,sans-serif}}
</style></head><body>
<div><div style="font-size:13px;color:#999;text-transform:uppercase;letter-spacing:1px;margin-bottom:16px">{slide.get("label","")}</div>
<p style="font-size:20px;color:#1a1a2e;line-height:1.6;margin:0">{slide.get("text","")}</p>
{f'<div style="background:#f5f5f5;border-left:4px solid #c0392b;padding:16px;margin-top:24px;font-size:16px;color:#c0392b;font-weight:700;font-style:italic">{highlight}</div>' if highlight else ''}</div>
{d}
</body></html>"""

def render_slides(slides: list[dict], out_dir: Path) -> list[str]:
    paths = []
    total = len(slides)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1080})
        for i, slide in enumerate(slides):
            page.set_content(build_slide_html(slide, i, total, show_dots=True), wait_until="networkidle")
            path = out_dir / f"slide_{i+1:02d}.png"
            page.screenshot(path=str(path), full_page=False)
            paths.append(path)
        browser.close()
    return paths

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

# ── Send preview email ─────────────────────────────────────────────────────
def send_preview(content: dict, slide_urls: list[str], post_id: str):
    today_str = datetime.now().strftime("%A, %d %B %Y")
    title_short = content['article_title'][:55]
    ig_approve = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=ig"
    fb_approve = f"{SITE_URL}/api/social_approve?token={APPROVE_SECRET}&post_id={post_id}&platform=fb"

    slides_html = "\n".join(
        f'<img src="{url}" width="480" style="display:block;margin:0 auto 8px;border-radius:8px;max-width:100%">'
        for url in slide_urls
    )
    html_ig = f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;background:#f4f3f0;font-family:Helvetica,Arial,sans-serif">
<div style="max-width:600px;margin:0 auto">
  <table width="100%" cellpadding="0" cellspacing="0" border="0"
         style="background:#f0f7f0;border-top:3px solid #E1306C;border-radius:8px;margin-bottom:20px">
    <tr><td style="padding:24px;text-align:center">
      <p style="margin:0 0 6px;font-size:10px;font-weight:700;letter-spacing:2px;text-transform:uppercase;color:#E1306C">Instagram · {today_str}</p>
      <p style="margin:0 0 16px;font-size:13px;color:#555">{len(slide_urls)} slides · 1080×1080px</p>
      <a href="{ig_approve}" style="display:inline-block;background:#E1306C;color:#fff;font-size:14px;font-weight:700;text-decoration:none;padding:14px 40px;border-radius:2px">✅ APPROVE Carousel</a>
    </td></tr>
  </table>
  <div style="background:#fff;padding:20px 24px;border-radius:8px">
    <p style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin:0 0 8px">Article</p>
    <p style="font-size:15px;color:#1a1a2e;font-weight:700;margin:0 0 4px">{content['article_title']}</p>
    <a href="{content['article_url']}" style="font-size:12px;color:#c0392b">{content['article_url']}</a>
  </div>
  <div style="background:#fff;padding:20px 24px;border-radius:8px">
    <p style="font-size:11px;color:#888;letter-spacing:.1em;text-transform:uppercase;margin:0 0 16px">Carousel Slides</p>
    {slides_html}
  </div>
</div></body></html>"""

    resend_lib.Emails.send({
        "from": from_addr,
        "to": PREVIEW_TO,
        "subject": f"[Scheduled Post] {title_short}",
        "html": html_ig,
        "text": f"Scheduled Post Preview\n{content['article_title']}\nApprove: {ig_approve}",
    })
    print(f"✓ Preview email sent for Scheduled article")

def update_notion_status(notion_id: str, status: str):
    """Update Notion article status."""
    notion_token = os.getenv("NOTION_TOKEN", "")
    payload = {"properties": {"Status": {"select": {"name": status}}}}
    req = urllib.request.Request(
        f"https://api.notion.com/v1/pages/{notion_id}",
        data=json.dumps(payload).encode(),
        method="PATCH",
        headers={"Authorization": f"Bearer {notion_token}",
                 "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        r.read()

# ── Main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== NeuroDigest Scheduled Post Elaboration ===")

    article = fetch_scheduled_article("Acute Kidney")
    if not article:
        print("❌ No Scheduled article found matching 'Acute Kidney'")
        sys.exit(0)

    print(f"\n✓ Found Scheduled article: {article['title'][:70]}")
    print(f"  URL: {article['url']}")
    print(f"  Notion ID: {article['notion_id']}")

    print("\n[1/4] Generating carousel with Claude...")
    content = generate_content(article)
    print(f"      {content['article_title'][:70]}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        print("\n[2/4] Rendering slides to PNG...")
        slide_paths = render_slides(content["slides"], tmp_path)
        print(f"      {len(slide_paths)} slides rendered")

        print("\n[3/4] Saving to Supabase...")
        post_id = ""
        try:
            row = sb.table("social_posts").insert({
                "article_title": content["article_title"],
                "article_url": content["article_url"],
                "journal": content.get("journal", ""),
                "fb_text": content["fb_text"],
                "approved": False,
            }).execute()
            post_id = row.data[0]["id"]
            print(f"      Post ID: {post_id}")

            slide_urls = upload_images(slide_paths, post_id)
            sb.table("social_posts").update({
                "slide_urls": slide_urls,
                "notion_page_id": article["notion_id"],
            }).eq("id", post_id).execute()
            print(f"      {len(slide_urls)} slides uploaded")
        except Exception as e:
            print(f"❌ Supabase error: {e}")
            sys.exit(1)

        print("\n[4/4] Sending preview email...")
        send_preview(content, slide_urls, post_id)

    print("\n✓ Updating Notion...")
    try:
        update_notion_status(article["notion_id"], "Used")
        print("  Status updated to 'Used'")
    except Exception as e:
        print(f"  ⚠ Notion update failed: {e}")

    print("\n✓ Done!")
