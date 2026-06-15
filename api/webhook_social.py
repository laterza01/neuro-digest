"""
Webhook endpoint for Google Cloud Scheduler.
Cloud Scheduler calls this every day at 6:00 UTC to generate social posts.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler

# Add project root to path so we can import social_daily
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import social_daily


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Verify request came from EasyCron
        auth_header = self.headers.get("Authorization", "")
        webhook_secret = os.getenv("WEBHOOK_SECRET", "")

        if webhook_secret and auth_header != f"Bearer {webhook_secret}":
            self._respond(401, "Unauthorized")
            return

        print("[webhook_social] EasyCron triggered social_daily generation")

        try:
            social_daily.main()
            self._respond(200, "✅ Social post generated successfully")
            print("[webhook_social] Webhook completed")

        except Exception as e:
            print(f"[webhook_social] Error: {e}")
            import traceback
            traceback.print_exc()
            self._respond(500, f"❌ Error: {str(e)}")

    def do_GET(self):
        # Health check
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
