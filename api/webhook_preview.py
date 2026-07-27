"""
Webhook endpoint for Vercel Cron (Monday 06:00 UTC).
Generates newsletter preview and sends to user — NO GitHub Actions dependency.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

import digest


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        auth_header = self.headers.get("Authorization", "")
        webhook_secret = os.getenv("WEBHOOK_SECRET", "")

        if webhook_secret and auth_header != f"Bearer {webhook_secret}":
            self._respond(401, "Unauthorized")
            return

        print("[webhook_preview] Vercel Cron triggered newsletter preview generation")

        try:
            digest.run(generate_only=True)
            self._respond(200, "✅ Newsletter preview generated")
        except Exception as e:
            print(f"[webhook_preview] Error: {e}")
            import traceback
            traceback.print_exc()
            self._respond(500, f"Error: {str(e)}")

    def do_GET(self):
        self._respond(200, "Webhook is active")

    def _respond(self, code, body):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
