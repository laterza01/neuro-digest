"""POST /api/create-checkout — create a Stripe Checkout Session for NeuroDigest Plus."""

import json
import os
from http.server import BaseHTTPRequestHandler

import stripe

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
SITE_URL          = os.environ.get("SITE_URL", "https://neuro-digest.com")

# Valid Price IDs — reject anything else
VALID_PRICE_IDS = {
    "price_1Td8PXLUNGp42dWtdAHLDt1D",  # Monthly €4.99
    "price_1Td8QDLUNGp42dWteiAvbfJX",  # Annual  €39
}


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_POST(self):
        try:
            length   = int(self.headers.get("Content-Length", 0))
            body     = json.loads(self.rfile.read(length) or b"{}")
            price_id = body.get("priceId", "").strip()
            email    = body.get("email", "").strip().lower()

            if not price_id or price_id not in VALID_PRICE_IDS:
                self._json({"error": "Invalid price ID"}, 400)
                return

            stripe.api_key = STRIPE_SECRET_KEY

            params = {
                "mode":                 "subscription",
                "line_items":           [{"price": price_id, "quantity": 1}],
                "success_url":          f"{SITE_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
                "cancel_url":           f"{SITE_URL}/premium",
                "allow_promotion_codes": True,
                "billing_address_collection": "auto",
            }

            # Pre-fill email if provided (subscriber already known)
            if email and "@" in email:
                params["customer_email"] = email

            session = stripe.checkout.Session.create(**params)
            self._json({"url": session.url})

        except stripe.StripeError as e:
            self._json({"error": str(e.user_message or e)}, 502)
        except Exception as e:
            self._json({"error": str(e)}, 500)

    def _json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
