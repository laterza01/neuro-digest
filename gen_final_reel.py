import os, json, subprocess, sys, tempfile
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(".env", override=True)
sys.path.insert(0, str(Path.cwd()))

import anthropic, requests
from supabase import create_client
from playwright.sync_api import sync_playwright
from social_daily import build_slide_html

sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
ai = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
api_key = os.getenv("ELEVENLABS_API_KEY")

post_id = "b7d245b7-835a-4d16-aedf-415a942148b3"

print("=== Generating Final Reel ===\n")

result = sb.table("social_posts").select("article_title,journal,fb_text").eq("id", post_id).execute()
post = result.data[0]

print("[1/5] Regenerating slide content...")
response = ai.messages.create(model="claude-opus-4-8", max_tokens=1500, messages=[{"role": "user", "content": f"""Generate 7-slide carousel JSON. Output ONLY valid JSON array:
[{{"type": "cover", "topic": "...", "headline": "..."}}, {{"type": "content", "label": "...", "text": "..."}}, {{"type": "content", "label": "...", "text": "..."}}, {{"type": "stat", "number": "...", "label": "..."}}, {{"type": "content", "label": "...", "text": "..."}}, {{"type": "source", "journal": "...", "year": "2026", "url": "..."}}, {{"type": "cta"}}]

Article: {post['article_title']}
Journal: {post['journal']}
Summary: {post['fb_text']}"""}])
slides = json.loads(response.content[0].text)
print("✓ 7 slides\n")

with tempfile.TemporaryDirectory() as tmpdir:
    tmppath = Path(tmpdir)

    print("[2/5] Rendering slides (NO DOTS)...")
    slide_paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1080, "height": 1080})

        for i, slide in enumerate(slides):
            html = build_slide_html(slide, i, 7, show_dots=False)
            page.set_content(html, wait_until="networkidle")
            path = tmppath / f"slide_{i+1:02d}.png"
            page.screenshot(path=str(path), full_page=False)
            slide_paths.append(path)

        # Add CTA slide (red)
        cta_html = """<html><head><style>body{width:1080px;height:1080px;display:flex;flex-direction:column;align-items:center;justify-content:center;background:linear-gradient(135deg, #c0392b 0%, #e74c3c 100%);margin:0;padding:80px;text-align:center;font-family:Arial,sans-serif;color:#fff}</style></head><body><div style='font-size:72px;font-weight:bold;margin-bottom:40px'>NeuroDigest</div><div style='font-size:32px;margin-bottom:60px;line-height:1.4'>Free Weekly<br>Neurology Research</div><div style='font-size:36px;font-weight:bold;border-bottom:3px solid #fff;padding-bottom:20px'>neuro-digest.com</div></body></html>"""
        page.set_content(cta_html, wait_until="networkidle")
        cta_path = tmppath / f"slide_08_cta.png"
        page.screenshot(path=str(cta_path), full_page=False)
        slide_paths.append(cta_path)

        browser.close()

    print(f"✓ {len(slide_paths)} slides\n")

    print("[3/5] Generating concise narration...")
    response = ai.messages.create(model="claude-opus-4-8", max_tokens=700, messages=[{"role": "user", "content": f"""7 CONCISE narrations (12-15 sec max each, 80 words each). Output ONLY JSON:
[{{"n": "..."}}, {{"n": "..."}}, {{"n": "..."}}, {{"n": "..."}}, {{"n": "..."}}, {{"n": "..."}}, {{"n": "..."}}]

Article: {post['article_title']}
Summary: {post['fb_text']}"""}])
    narrations = [n["n"] for n in json.loads(response.content[0].text)]

    # Add CTA at the end
    cta_text = "Join our community for exclusive neurological insights. Subscribe to our free weekly newsletter at neuro-digest.com and follow us for daily updates on cutting-edge research and clinical advances."
    narrations.append(cta_text)

    print("✓ 8 narrations (7 content + CTA)\n")

    print("[4/5] Generating audio...")
    audio_files, durations = [], []
    for i, text in enumerate(narrations):
        resp = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/iP95p4xoKVk53GoZ742B",
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            json={"text": text, "model_id": "eleven_turbo_v2", "voice_settings": {"stability": 0.5, "similarity_boost": 1.0, "style": 1, "use_speaker_boost": True}, "speed": 1.05}, timeout=30)

        audio_file = tmppath / f"audio_{i+1:02d}.mp3"
        with open(audio_file, "wb") as f:
            f.write(resp.content)
        audio_files.append(str(audio_file))

        result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_file)], capture_output=True, text=True, timeout=10)
        duration = float(result.stdout.strip()) if result.stdout else 14.0
        durations.append(duration)

    print(f"Total: {sum(durations):.0f}s\n")

    print("[5/5] Assembling video...")
    concat_file = tmppath / "concat.txt"
    with open(concat_file, "w") as f:
        for slide, dur in zip(slide_paths, durations):
            f.write(f"file '{slide}'\nduration {dur:.2f}\n")

    video_temp = tmppath / "video.mp4"
    subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-vf", "format=yuv420p", "-y", str(video_temp)], capture_output=True, timeout=120)

    audio_concat = tmppath / "audio.txt"
    with open(audio_concat, "w") as f:
        for af in audio_files:
            f.write(f"file '{af}'\n")

    full_audio = tmppath / "audio_full.mp3"
    subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", str(audio_concat), "-c", "copy", "-y", str(full_audio)], capture_output=True, timeout=60)

    final = Path("/Users/vincenzo/Downloads") / "reel_final.mp4"
    subprocess.run(["ffmpeg", "-i", str(video_temp), "-i", str(full_audio), "-c:v", "copy", "-c:a", "aac", "-map", "0:v:0", "-map", "1:a:0", "-shortest", "-y", str(final)], capture_output=True, timeout=120)

    print(f"✅ /Downloads/reel_final.mp4 ({sum(durations):.0f}s)")
