"""POST /api/stripe-webhook — receive Stripe events and update Supabase."""

import json
import os
from http.server import BaseHTTPRequestHandler

import stripe
from supabase import create_client

STRIPE_SECRET_KEY      = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET  = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
SUPABASE_URL           = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY           = os.environ.get("SUPABASE_SERVICE_KEY", "")


class handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        try:
            length  = int(self.headers.get("Content-Length", 0))
            payload = self.rfile.read(length)                       # raw bytes — required for sig check
            sig     = self.headers.get("Stripe-Signature", "")

            stripe.api_key = STRIPE_SECRET_KEY

            # Verify signature — raises if tampered or wrong secret
            try:
                event = stripe.Webhook.construct_event(
                    payload, sig, STRIPE_WEBHOOK_SECRET
                )
            except stripe.errors.SignatureVerificationError:
                self._respond(401, "Invalid signature")
                return

            # ── Handle events ─────────────────────────────────────────────────
            if event["type"] == "checkout.session.completed":
                self._handle_checkout_completed(event["data"]["object"])

            elif event["type"] in (
                "customer.subscription.deleted",
                "customer.subscription.updated",
            ):
                self._handle_subscription_change(event["data"]["object"])

            self._respond(200, "ok")

        except Exception as e:
            print(f"[stripe-webhook] error: {e}")
            self._respond(500, str(e))

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_checkout_completed(self, session):
        """Mark subscriber as premium when payment succeeds."""
        email    = (session.get("customer_email") or
                    (session.get("customer_details") or {}).get("email", ""))
        cust_id  = session.get("customer", "")
        sub_id   = session.get("subscription", "")

        if not email:
            print("[stripe-webhook] checkout.session.completed — no email found")
            return

        sb = create_client(SUPABASE_URL, SUPABASE_KEY)

        # Update existing subscriber → premium
        result = sb.table("subscribers").update({
            "is_premium":             True,
            "stripe_customer_id":     cust_id,
            "stripe_subscription_id": sub_id,
        }).eq("email", email).execute()

        if not result.data:
            # Subscriber not found — create a minimal confirmed+premium record
            sb.table("subscribers").upsert({
                "email":                  email,
                "status":                 "confirmed",
                "is_premium":             True,
                "stripe_customer_id":     cust_id,
                "stripe_subscription_id": sub_id,
            }, on_conflict="email").execute()

        print(f"[stripe-webhook] ✓ premium activated: {email}")

    def _handle_subscription_change(self, subscription):
        """Revoke premium if subscription cancelled or payment failed."""
        status  = subscription.get("status", "")
        cust_id = subscription.get("customer", "")
        sub_id  = subscription.get("id", "")

        if not cust_id:
            return

        # Active/trialling → keep premium; cancelled/unpaid → revoke
        is_premium = status in ("active", "trialing")

        sb = create_client(SUPABASE_URL, SUPABASE_KEY)
        sb.table("subscribers").update({
            "is_premium":             is_premium,
            "stripe_subscription_id": sub_id,
        }).eq("stripe_customer_id", cust_id).execute()

        print(f"[stripe-webhook] subscription {sub_id} → is_premium={is_premium} (status={status})")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _respond(self, status, text):
        body = text.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
