#!/usr/bin/env python3
"""
Delete specific Notion page IDs (the 4 duplicates of Neuroinflammation stroke).
"""
import os
import urllib.request
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

notion_token = os.getenv("NOTION_TOKEN", "")

if not notion_token:
    print("❌ Missing NOTION_TOKEN in .env")
    exit(1)

# The 4 duplicates to delete with proper UUID format
duplicates = [
    "37ab1120-c3cf-81a0-9221-e04b8cb023d3",
    "37bb1120-c3cf-814f-b35c-eee385c0d14b",
    "37ab1120-c3cf-816f-82cc-c8b47cbe2ead",
    "379b1120-c3cf-8160-ace7-fe480d9e11c7",
]

deleted_count = 0

for page_id in duplicates:
    try:
        # Notion API expects PUT with archived: true, not DELETE
        import json
        req = urllib.request.Request(
            f"https://api.notion.com/v1/pages/{page_id}",
            method="PATCH",
            data=json.dumps({"archived": True}).encode(),
            headers={"Authorization": f"Bearer {notion_token}",
                     "Notion-Version": "2022-06-28",
                     "Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req) as r:
            r.read()
        print(f"✓ Archived {page_id}")
        deleted_count += 1
    except Exception as e:
        print(f"✗ Failed to archive {page_id}: {e}")

print(f"\n✅ Successfully archived {deleted_count}/4 duplicates")
