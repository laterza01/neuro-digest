"""
Vercel Cron endpoint - esegue social_daily.py direttamente, SENZA GitHub Actions.
Triggerato ogni giorno a 6:00 UTC dal cron Vercel.
"""
import os
import sys
import subprocess
from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Validate cron secret
        cron_secret = os.getenv("CRON_SECRET", "")
        auth_header = self.headers.get("Authorization", "")

        if cron_secret and auth_header != f"Bearer {cron_secret}":
            self._respond(401, "Unauthorized")
            return

        print("[cron_social] Starting social_daily.py...")

        try:
            # Esegui social_daily.py direttamente
            result = subprocess.run(
                [sys.executable, "/var/task/social_daily.py"],
                capture_output=True,
                timeout=300,  # 5 minuti max
                text=True,
                cwd="/var/task"
            )

            print(f"[cron_social] Exit code: {result.returncode}")
            print(f"[cron_social] Output:\n{result.stdout}")

            if result.stderr:
                print(f"[cron_social] Errors:\n{result.stderr}")

            if result.returncode == 0:
                self._respond(200, "✅ Social daily generated successfully")
            else:
                self._respond(500, f"❌ social_daily.py failed with code {result.returncode}")

        except subprocess.TimeoutExpired:
            print("[cron_social] Timeout - took too long")
            self._respond(500, "❌ Timeout: social_daily.py took too long")
        except Exception as e:
            print(f"[cron_social] Exception: {e}")
            self._respond(500, f"❌ Error: {str(e)}")

    def _respond(self, code, body):
        encoded = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass
