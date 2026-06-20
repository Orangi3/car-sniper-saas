# Phase 2 — Stripe Subscriptions

Planning doc. Nothing in this file is implemented yet.

## Goal

Replace the temporary `auth.set_plan()` bootstrap path with a real
Stripe-driven entitlement system: paying customers move from `free` to
`starter` / `pro` automatically; cancellations and failed payments
move them back; all plan changes are gated by signed Stripe webhooks,
never by the client.

## Non-goals (deferred to Phase 3)

- Annual plans, coupons, multi-seat / team plans
- In-product upgrade UI beyond a single "Manage subscription" button
  that opens Stripe's hosted Billing Portal
- Tax / VAT collection (use Stripe Tax once revenue justifies it)

## Server-side surface

| Endpoint                     | Auth          | Purpose                              |
|------------------------------|---------------|--------------------------------------|
| `POST /api/billing/checkout` | login_required | Create a Stripe Checkout session, return its URL |
| `POST /api/billing/portal`   | login_required | Create a Stripe Billing Portal session for self-serve cancel / payment-method update |
| `POST /api/billing/webhook`  | Stripe sig    | Receive Stripe events. NEVER trust without signature verification. |
| `GET  /api/billing/me`       | login_required | Return current subscription state (plan, period_end, cancel_at) — purely a read against our DB, never a live Stripe API call on every request |

## Migrations

`migrations/0003_billing.sql` (+ `.postgres.sql`):

```sql
CREATE TABLE subscriptions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,  -- BIGSERIAL on PG
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stripe_customer_id  TEXT NOT NULL,
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    stripe_price_id     TEXT NOT NULL,
    status              TEXT NOT NULL,          -- 'active' | 'trialing' | 'past_due' | 'canceled' | 'unpaid' | 'incomplete'
    current_period_end  TEXT NOT NULL,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_subscriptions_user ON subscriptions(user_id);

CREATE TABLE webhook_events (
    stripe_event_id     TEXT PRIMARY KEY,        -- the Stripe `evt_…` ID
    type                TEXT NOT NULL,           -- 'customer.subscription.updated' etc.
    payload             TEXT NOT NULL,           -- full JSON for forensics
    received_at         TEXT NOT NULL DEFAULT (datetime('now')),
    processed_at        TEXT
);
CREATE INDEX idx_webhook_events_received ON webhook_events(received_at DESC);
```

Idempotency hinges on `stripe_event_id` PK: an INSERT that hits the
unique constraint means we've seen this event — return 200 immediately
without re-processing. Stripe retries identical events on 5xx.

## Entitlement mapping

A single dict in `billing.py` maps Stripe price IDs to plans:

```python
PRICE_TO_PLAN = {
    "price_starter_monthly_id": "starter",
    "price_pro_monthly_id":     "pro",
}
```

Only `customer.subscription.updated` / `customer.subscription.deleted`
events flip `users.plan`. The dashboard NEVER posts a plan change; the
server NEVER reads a plan from a client cookie or query param.

## Webhook handler skeleton

```python
@app.post("/api/billing/webhook")
def billing_webhook():
    payload = request.get_data()                # raw bytes — signature uses these
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = stripe.Webhook.construct_event(
            payload, sig, os.environ["STRIPE_WEBHOOK_SECRET"]
        )
    except (ValueError, stripe.SignatureVerificationError):
        return jsonify({"error": "invalid signature"}), 400

    # Idempotency — INSERT…OR IGNORE on the event ID; if it's a dup, skip.
    if _already_processed(event["id"]):
        return jsonify({"ok": True, "duplicate": True}), 200
    _record_event(event["id"], event["type"], payload)

    h = _HANDLERS.get(event["type"])
    if h:
        h(event["data"]["object"])
    _mark_processed(event["id"])
    return jsonify({"ok": True}), 200
```

`_HANDLERS` dispatches on event type:

| Event                                  | Action                                                              |
|----------------------------------------|---------------------------------------------------------------------|
| `checkout.session.completed`           | Read `client_reference_id` (= user.id), insert subscription row, set users.plan from PRICE_TO_PLAN |
| `customer.subscription.updated`        | Update status, current_period_end, cancel_at_period_end; recompute plan |
| `customer.subscription.deleted`        | Set status='canceled', users.plan='free'                            |
| `invoice.payment_failed`               | Set status='past_due'; if grace period elapsed (subscription terminal), users.plan='free' |
| `invoice.payment_succeeded`            | Refresh current_period_end if changed; no plan change on its own    |

Plan recompute logic (`entitlement.refresh_for(user_id)`):
1. Look up most-recent non-terminal subscription for user
2. If status in ('active','trialing'), plan = PRICE_TO_PLAN[price_id]
3. Else, plan = 'free'
4. Update `users.plan` only if changed (avoid no-op writes)

Cancellation grace: when `cancel_at_period_end=1`, leave plan unchanged
until `customer.subscription.deleted` fires at period end. Stripe handles
the timer.

## Checkout flow

```python
@app.post("/api/billing/checkout")
@auth.login_required
def billing_checkout():
    u = auth.current_user()
    body = request.get_json() or {}
    plan = body.get("plan")               # 'starter' | 'pro'
    price_id = _PLAN_TO_PRICE.get(plan)
    if not price_id: return jsonify({"error":"unknown plan"}), 400
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer_email=u.email,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=ORIGIN + "/billing/success?cs={CHECKOUT_SESSION_ID}",
        cancel_url=ORIGIN + "/billing/cancel",
        client_reference_id=str(u.id),     # so the webhook can find the user
        allow_promotion_codes=True,
    )
    return jsonify({"url": session.url})
```

`client_reference_id` is the only secure user-binding mechanism — the
webhook reads it from Stripe (not the client) to attach the new sub
to the right user.

## Testing strategy

**Never hit live Stripe in CI.** Three test layers:

1. **Webhook signature & idempotency** — feed `stripe.WebhookSignature`
   a known payload + computed signature using a fixed `whsec_test_…`
   secret. Verify:
    - Valid signature → 200 + event row recorded
    - Tampered payload → 400, no DB write
    - Replayed `evt_…` ID → 200 but no second processing
2. **Plan transitions** — fixture-driven. For each event type build a
   minimal JSON payload (using fixtures pulled from Stripe's own
   `stripe-mock` if running locally, or hand-crafted dicts in CI),
   construct a signed envelope, POST it, assert `users.plan` and
   `subscriptions.status` after.
3. **Checkout session creation** — mock `stripe.checkout.Session.create`
   to return a fake URL; verify the request body sent to Stripe matches
   expectations (correct price_id, client_reference_id, no plan in body).

Test fixtures live in `tests/fixtures/stripe/*.json` so the canonical
event shapes are version-controlled and editable.

## Env vars

| Var                       | Purpose                                          |
|---------------------------|--------------------------------------------------|
| `STRIPE_SECRET_KEY`       | `sk_live_…` — server-side API calls              |
| `STRIPE_WEBHOOK_SECRET`   | `whsec_…` — signature verification              |
| `STRIPE_PRICE_STARTER`    | Stripe price ID for the Starter plan            |
| `STRIPE_PRICE_PRO`        | Stripe price ID for the Pro plan                |
| `BILLING_PORTAL_RETURN_URL` | Where Stripe sends users back after the portal |

## File plan (when implementing)

| File                                 | Purpose                                  |
|--------------------------------------|------------------------------------------|
| `billing.py`                         | Stripe client, PRICE_TO_PLAN, event handlers, idempotency, entitlement.refresh |
| `migrations/0003_billing.sql` + `.postgres.sql` | Schema for subscriptions, webhook_events |
| `server.py` (edit)                   | 4 new routes: /api/billing/{checkout,portal,webhook,me} |
| `tests/test_billing.py`              | Signature, idempotency, plan-transition tests |
| `tests/fixtures/stripe/*.json`       | Recorded sample events                   |
| `requirements.txt` (edit)            | Add `stripe>=8.0`                        |
| `DEPLOY.md` (edit)                   | Document STRIPE_* env vars + portal-return URL |

## Order of work

1. `migrations/0003_billing.{sql,postgres.sql}` + tests for schema
2. `billing.py` skeleton + signature verification path (no handlers yet)
3. Webhook idempotency tests against signature path
4. `_HANDLERS` for `checkout.session.completed` and
   `customer.subscription.updated` — happy path
5. `entitlement.refresh_for(user_id)` + tests proving plan ratchet
6. Cancellation + failed-payment handlers + grace-period semantics
7. `POST /api/billing/checkout` + `POST /api/billing/portal`
8. Frontend "Upgrade" CTAs that POST to `/api/billing/checkout` and redirect to the returned URL
9. Stripe CLI smoke test against a local server (no live charges) before
   first prod deploy

## Risks to flag early

- **Webhook ordering.** Stripe doesn't guarantee in-order delivery. The
  handlers must be order-independent — always recompute the user's plan
  from the CURRENT subscription state, never apply a relative diff.
- **Idempotency under concurrent webhooks.** If the same event arrives
  twice in rapid succession (Stripe retries), two workers might race the
  INSERT into webhook_events. The PK guarantees only one wins; the
  loser should treat the unique-violation as success.
- **Subscription expiry without an event.** Belt-and-suspenders: a
  nightly cron that calls `entitlement.refresh_for` on every user with
  `current_period_end < now()` catches anything the webhook missed.
