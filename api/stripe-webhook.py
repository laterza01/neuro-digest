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
      Benvenuto in NeuroDigest Plus.
    </h1>
    <p style="font-size:16px;color:#555;line-height:1.7;margin:0 0 24px">
      Il tuo accesso è ora attivo. Da questo momento hai accesso completo a tutto
      ciò che NeuroDigest Plus include — segni neurologici, triadi cliniche,
      dati di sensibilità e specificità, dimostrazioni video.
    </p>
    <hr style="border:none;border-top:1px solid #eee;margin:24px 0">
    <p style="font-size:14px;color:#888;line-height:1.7;margin:0">
      Riceverai subito una seconda email con il link di accesso alla piattaforma.<br>
      La prossima digest arriva <strong>lunedì alle 14:00</strong>.
    </p>
  </div>

  <p style="text-align:center;font-size:12px;color:#bbb;margin-top:24px;line-height:1.7">
    NeuroDigest · Curato da Vincenzo Laterza, MD, Neurologo<br>
    Domande? Rispondi a questa email.
  </p>
</div>
</body></html>
""",
            })

            # ── Email 2: Platform access link ──────────────────────────────────
            resend.Emails.send({
                "from":    FROM_ADDR,
                "to":      [email],
                "subject": "Il tuo accesso alla piattaforma NeuroDigest Plus",
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
    <h1 style="font-family:Georgia,serif;font-size:26px;color:#1a1a2e;
               margin:0 0 16px;line-height:1.2">
      La tua piattaforma è pronta.
    </h1>
    <p style="font-size:16px;color:#555;line-height:1.7;margin:0 0 28px">
      Accedi alla libreria completa di segni neurologici, triadi cliniche
      e materiale di riferimento clinico — direttamente dal link qui sotto.
    </p>

    <div style="text-align:center;margin:0 0 28px">
      <a href="{PLATFORM_URL}" style="display:inline-block;background:#1a1a2e;
         color:#fff;text-decoration:none;padding:16px 36px;
         font-size:15px;font-weight:700;letter-spacing:.3px;border-radius:3px">
        Accedi alla piattaforma →
      </a>
    </div>

    <div style="background:#f9f8f6;border:1px solid #ede9e3;border-radius:3px;
                padding:20px 24px;margin-bottom:24px">
      <p style="font-size:11px;font-weight:700;letter-spacing:1.4px;
                text-transform:uppercase;color:#aaa;margin:0 0 12px">
        Cosa trovi sulla piattaforma
      </p>
      <p style="font-size:14px;color:#555;line-height:1.8;margin:0">
        📋 &nbsp;<strong>30 Segni Neurologici</strong> — schede complete con tecnica,
        pathway anatomico, sensibilità &amp; specificità, varianti, video<br>
        🔺 &nbsp;<strong>Triadi Cliniche</strong> — in arrivo<br>
        📊 &nbsp;<strong>Grafici &amp; Diagrammi</strong> — in arrivo<br>
        🗂️ &nbsp;<strong>Archivio completo</strong> — in arrivo
      </p>
    </div>

    <hr style="border:none;border-top:1px solid #eee;margin:0 0 20px">
    <p style="font-size:13px;color:#999;line-height:1.7;margin:0">
      Salva questo link — è il tuo accesso diretto alla piattaforma.<br>
      Domande? Rispondi a questa email.
    </p>
  </div>

  <p style="text-align:center;font-size:12px;color:#bbb;margin-top:24px;line-height:1.7">
    NeuroDigest · Curato da Vincenzo Laterza, MD, Neurologo<br>
    Gestisci il tuo abbonamento tramite il portale Stripe.
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
