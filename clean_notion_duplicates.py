#!/usr/bin/env python3
"""
Clean up duplicate articles from Notion database.
Keeps first occurrence, removes subsequent duplicates.
"""
import os, json, sys
import urllib.request
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

notion_token = os.getenv("NOTION_TOKEN", "")
notion_db_id = os.getenv("NOTION_DATABASE_ID", "")

if not notion_token or not notion_db_id:
    print("❌ Missing NOTION_TOKEN or NOTION_DATABASE_ID in .env")
    sys.exit(1)

print("Fetching all articles from Notion...")
all_pages = []
has_more = True
start_cursor = None

while has_more:
    payload = {"page_size": 100}
    if start_cursor:
        payload["start_cursor"] = start_cursor

    req = urllib.request.Request(
        f"https://api.notion.com/v1/databases/{notion_db_id}/query",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {notion_token}",
                 "Notion-Version": "2022-06-28",
                 "Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as r:
        response = json.loads(r.read())

    all_pages.extend(response.get("results", []))
    has_more = response.get("has_more", False)
    start_cursor = response.get("next_cursor")

print(f"Total articles: {len(all_pages)}")

# Find duplicates
seen_urls = {}
seen_titles = {}
to_delete = []

for page in all_pages:
    page_id = page["id"]
    url = page["properties"].get("URL", {}).get("url", "")
    title = "".join(t["plain_text"] for t in page["properties"].get("Nome", {}).get("title", []))

    # Check for URL duplicate
    if url and url in seen_urls:
        print(f"❌ Duplicate URL: {url[:60]}... (keeping {seen_urls[url]}, deleting {page_id})")
        to_delete.append(page_id)
        continue

    # Check for title duplicate (first 50 chars)
    title_key = title[:50].lower() if title else ""
    if title_key and title_key in seen_titles:
        print(f"❌ Duplicate title: {title[:60]}... (keeping {seen_titles[title_key]}, deleting {page_id})")
        to_delete.append(page_id)
        continue

    # Mark as seen
    if url:
        seen_urls[url] = page_id
    if title:
        seen_titles[title_key] = page_id

if not to_delete:
    print("✅ No duplicates found!")
    sys.exit(0)

print(f"\nFound {len(to_delete)} duplicates to remove")
print(f"Delete these pages? (yes/no): ", end="")
confirm = input().strip().lower()

if confirm != "yes":
    print("Cancelled.")
    sys.exit(0)

# Delete duplicates
for page_id in to_delete:
    try:
        req = urllib.request.Request(
            f"https://api.notion.com/v1/pages/{page_id}",
            method="DELETE",
            headers={"Authorization": f"Bearer {notion_token}",
                     "Notion-Version": "2022-06-28"}
        )
        with urllib.request.urlopen(req) as r:
            r.read()
        print(f"✓ Deleted {page_id}")
    except Exception as e:
        print(f"✗ Failed to delete {page_id}: {e}")

print(f"\n✅ Cleaned {len(to_delete)} duplicates from Notion")
