"""GET/POST /api/preferences?token=... — fetch or save subscriber topic preferences."""

from http.server import BaseHTTPRequestHandler
import json
import os
import jwt
from urllib.parse import urlparse, parse_qs

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
JWT_SECRET   = os.environ.get("JWT_SECRET", "change-me-in-env")

ALL_TOPICS = [
    "Multiple Sclerosis", "Stroke", "Parkinson's Disease", "Epilepsy",
    "Dementia", "Headache", "Neuromuscular", "Neuro-oncology",
    "Neuroinflammation", "Movement Disorders", "Neurocritical Care", "Neurogenetics",
]


def _decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload.get("purpose") not in ("preferences", "confirm"):
            return None
        return payload.get("sub")
    except Exception:
        return None


def _get_token(path: str) -> str:
    qs = parse_qs(urlparse(path).query)
    return (qs.get("token") or [""])[0]


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        email = _decode_token(_get_token(self.path))
        if not email:
            self._json({"error": "Invalid or expired token"}, 401)
            return

        try:
            from supabase import create_client
            sb     = create_client(SUPABASE_URL, SUPABASE_KEY)
            result = sb.table("subscribers").select("topics,status").eq("email", email).maybe_single().execute()
        except Exception as e:
            self._json({"error": str(e)}, 500)
            return

        if not result.data:
            self._json({"error": "Subscriber not found"}, 404)
            return

        self._json({
            "email":      email,
            "topics":     result.data.get("topics") or ALL_TOPICS,
            "all_topics": ALL_TOPICS,
            "status":     result.data.get("status"),
        })

    def do_POST(self):
        email = _decode_token(_get_token(self.path))
        if not email:
            self._json({"error": "Invalid or expired token"}, 401)
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = json.loads(self.rfile.read(length) or b"{}")
        except Exception:
            self._json({"error": "Invalid JSON"}, 400)
            return

        topics = body.get("topics", [])
        if not isinstance(topics, list) or len(topics) == 0:
            self._json({"error": "Select at least one topic"}, 400)
            return

        valid = [t for t in topics if t in ALL_TOPICS]
        if not valid:
            self._json({"error": "No valid topics provided"}, 400)
            return

        try:
            from supabase import create_client
            sb = create_client(SUPABASE_URL, SUPABASE_KEY)
            sb.table("subscribers").update({"topics": valid}).eq("email", email).execute()
        except Exception as e:
            self._json({"error": str(e)}, 500)
            return

        self._json({"ok": True, "topics": valid})

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
