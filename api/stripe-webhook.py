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
RESEND_API_KEY         = os.environ.get("RESEND_API_KEY", "")
FROM_ADDR              = "NeuroDigest Plus <digest@neuro-digest.com>"
PLATFORM_URL           = "https://neurodigest-lab.netlify.app"


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
            event_type = event.get("type") or getattr(event, "type", "")

            if event_type == "checkout.session.completed":
                # Retrieve full session (handles both thin and classic events)
                obj = (event.get("data") or {}).get("object") or {}
                session_id = (obj.get("id") if isinstance(obj, dict) else getattr(obj, "id", None))
                if not session_id:
                    # v2 thin event — get id from related_object
                    related = event.get("related_object") or getattr(event, "related_object", None)
                    session_id = (related.get("id") if isinstance(related, dict) else getattr(related, "id", None))
                if session_id:
                    session = stripe.checkout.Session.retrieve(session_id)
                    self._handle_checkout_completed(session)
                else:
                    print("[stripe-webhook] checkout.session.completed — no session_id found")

            elif event_type in (
                "customer.subscription.deleted",
                "customer.subscription.updated",
            ):
                obj = (event.get("data") or {}).get("object") or {}
                if not obj:
                    related = event.get("related_object") or getattr(event, "related_object", None)
                    sub_id = (related.get("id") if isinstance(related, dict) else getattr(related, "id", None))
                    if sub_id:
                        obj = stripe.Subscription.retrieve(sub_id)
                self._handle_subscription_change(obj)

            self._respond(200, "ok")

        except Exception as e:
            print(f"[stripe-webhook] error: {e}")
            self._respond(500, str(e))

    # ── Handlers ──────────────────────────────────────────────────────────────

    def _handle_checkout_completed(self, session):
        """Mark subscriber as premium when payment succeeds."""
        def _get(obj, key, default=""):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default) or default

        customer_details = _get(session, "customer_details")
        cd_email = (_get(customer_details, "email") if customer_details else "")
        email    = _get(session, "customer_email") or cd_email
        cust_id  = _get(session, "customer", "")
        sub_id   = _get(session, "subscription", "")

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
        self._send_premium_welcome(email)

    def _send_premium_welcome(self, email):
        """Send 2 emails: welcome + platform access link."""
        if not RESEND_API_KEY:
            print("[stripe-webhook] no RESEND_API_KEY — skipping welcome emails")
            return
        try:
            import resend
            resend.api_key = RESEND_API_KEY

            # ── Email 1: Welcome ───────────────────────────────────────────────
            resend.Emails.send({
                "from":    FROM_ADDR,
                "to":      [email],
                "subject": "Welcome to NeuroDigest Plus ✦",
                "html": f"""
<!DOCTYPE html><html><head><meta charset="utf-8"></head><body
  style="margin:0;padding:0;background:#f7f7f5;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif">
<div style="max-width:600px;margin:0 auto;padding:40px 24px">

  <div style="text-align:center;margin-bottom:32px">
    <div style="display:inline-block;background:#1a1a2e;color:#c8a840;
                font-size:11px;font-weight:700;letter-spacing:2px;
                text-transform:uppercase;padding:6px 16px;border-radius:2px">
      NeuroDigest Plus ✦
    </div>
  </div>

  <div style="background:#fff;border:1px solid #ddd;border-top:3px solid #c8a840;padding:40px 36px">
    <h1 style="font-family:Georgia,serif;font-size:28px;color:#1a1a2e;
               margin:0 0 16px;line-height:1.2">
      Welcome to NeuroDigest Plus.
    </h1>
    <p style="font-size:16px;color:#555;line-height:1.7;margin:0 0 24px">
      Your access is now active. From this moment you have full access to everything
      NeuroDigest Plus includes — neurological signs, clinical triads,
      sensitivity &amp; specificity data, video demonstrations.
    </p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
    <p style="font-size:14px;color:#888;line-height:1.7;margin:0">
      You'll receive a second email shortly with your access link to <strong>NeuroAtlas</strong>.<br>
      Your next digest arrives <strong>Monday at 14:00</strong>.
    </p>
  </div>

  <p style="text-align:center;font-size:12px;color:#bbb;margin-top:24px;line-height:1.7">
    NeuroDigest · Curated by Vincenzo Laterza, MD, Neurologist<br>
    Questions? Reply to this email.
  </p>
</div>
</body></html>
""",
            })

            # ── Email 2: NeuroAtlas access link ───────────────────────────────
            resend.Emails.send({
                "from":    FROM_ADDR,
                "to":      [email],
                "subject": "Your NeuroAtlas access is ready",
                "html": f"""
<!DOCTYPE html><html><head><meta charset="utf-8"></head><body
  style="margin:0;padding:0;background:#f7f7f5;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif">
<div style="max-width:600px;margin:0 auto;padding:40px 24px">

  <div style="text-align:center;margin-bottom:32px">
    <div style="display:inline-block;background:#1a1a2e;color:#c8a840;
                font-size:11px;font-weight:700;letter-spacing:2px;
                text-transform:uppercase;padding:6px 16px;border-radius:2px">
      NeuroAtlas ✦
    </div>
  </div>

  <div style="background:#fff;border:1px solid #ddd;border-top:3px solid #c8a840;padding:40px 36px">
    <h1 style="font-family:Georgia,serif;font-size:26px;color:#1a1a2e;
               margin:0 0 16px;line-height:1.2">
      NeuroAtlas is ready.
    </h1>
    <p style="font-size:16px;color:#555;line-height:1.7;margin:0 0 28px">
      The complete neurological signs library — bedside clinical reference cards
      for ward rounds, outpatient clinics, and everything in between.
    </p>

    <div style="text-align:center;margin:0 0 28px">
      <a href="{PLATFORM_URL}" style="display:inline-block;background:#1a1a2e;
         color:#fff;text-decoration:none;padding:16px 36px;
         font-size:15px;font-weight:700;letter-spacing:.3px;border-radius:3px">
        Open NeuroAtlas →
      </a>
    </div>

    <div style="background:#f9f8f6;border:1px solid #ede9e3;border-radius:3px;
                padding:20px 24px;margin-bottom:24px">
      <p style="font-size:11px;font-weight:700;letter-spacing:1.4px;
                text-transform:uppercase;color:#aaa;margin:0 0 12px">
        What you'll find on NeuroAtlas
      </p>
      <p style="font-size:14px;color:#555;line-height:1.8;margin:0">
        📋 &nbsp;<strong>30 Neurological Signs</strong> — step-by-step technique,
        anatomical pathway, sensitivity &amp; specificity, variants, video<br>
        🔺 &nbsp;<strong>Clinical Triads</strong> — coming soon<br>
        📊 &nbsp;<strong>Graphs &amp; Diagrams</strong> — coming soon<br>
        🗂️ &nbsp;<strong>Full Archive</strong> — coming soon
      </p>
    </div>

    <hr style="border:none;border-top:1px solid #eee;margin:0 0 20px">
    <p style="font-size:13px;color:#999;line-height:1.7;margin:0">
      Save this link — it's your direct access to NeuroAtlas.<br>
      Questions? Reply to this email.
    </p>
  </div>

  <p style="text-align:center;font-size:12px;color:#bbb;margin-top:24px;line-height:1.7">
    NeuroDigest · Curated by Vincenzo Laterza, MD, Neurologist<br>
    Manage your subscription anytime via the Stripe billing portal.
  </p>
</div>
</body></html>
""",
            })

            print(f"[stripe-webhook] ✓ welcome emails sent to {email}")

        except Exception as e:
            print(f"[stripe-webhook] email error: {e}")

    def _handle_subscription_change(self, subscription):
        """Revoke premium if subscription cancelled or payment failed."""
        def _get(obj, key, default=""):
            if isinstance(obj, dict):
                return obj.get(key, default)
            return getattr(obj, key, default) or default

        status  = _get(subscription, "status", "")
        cust_id = _get(subscription, "customer", "")
        sub_id  = _get(subscription, "id", "")

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
