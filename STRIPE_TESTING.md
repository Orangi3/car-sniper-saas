# Stripe billing — local test-mode setup

Phase 2A only wires up **TEST mode**. No live keys, no real charges,
no live customers. Everything happens in your Stripe test sandbox.

## 1. Create the two TEST products

In the Stripe Dashboard, top-left toggle = **Test mode**.

Products → **+ Add product**:

| Product name      | Pricing model       | Recurring | Amount  |
|-------------------|---------------------|-----------|---------|
| Sniper Starter    | Standard pricing    | Monthly   | $19.00  |
| Sniper Pro        | Standard pricing    | Monthly   | $49.00  |

After saving each, copy the **price ID** (starts with `price_…`) shown
under "Pricing".

## 2. Get your test secret key

Dashboard → Developers → API keys → reveal **Secret key** (starts with
`sk_test_…`). This is server-only; never embed it in the browser.

## 3. Install + run the Stripe CLI

Forwards Stripe events to your local server and prints the signing
secret you need.

```bash
brew install stripe/stripe-cli/stripe        # macOS
stripe login                                  # opens a browser auth
stripe listen --forward-to localhost:8765/api/billing/webhook
```

The CLI prints something like:

```
Ready! Your webhook signing secret is whsec_abc123…
```

Copy that. It's only valid for this `stripe listen` session.

## 4. Fill in `.env`

```bash
cp .env.example .env
$EDITOR .env
```

Set:

```
STRIPE_SECRET_KEY=sk_test_…
STRIPE_WEBHOOK_SECRET=whsec_…
STRIPE_PRICE_STARTER=price_…   # from step 1
STRIPE_PRICE_PRO=price_…       # from step 1
BILLING_ORIGIN=                # leave blank in local dev
```

## 5. Apply migrations and start the server

```bash
source .venv/bin/activate
set -a; source .env; set +a
python3 -m migrations.runner   # creates subscriptions + stripe_webhook_events
python3 server.py
```

## 6. Trigger a test checkout

Two ways:

**a) Via the API directly** (any signed-in user):

```bash
curl -s -X POST http://localhost:8765/api/billing/checkout \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{"plan":"starter"}'
```

Returns `{"url": "https://checkout.stripe.com/c/…"}`. Open the URL,
pay with test card `4242 4242 4242 4242`, any future expiry, any CVC.

**b) Via the Stripe CLI synthetic event** (no Checkout flow needed):

```bash
stripe trigger checkout.session.completed
```

The CLI sends a synthetic event to your `stripe listen` forwarder.
Your server signature-verifies, dedups, and routes it through
`billing.dispatch_event`. You should see the user's plan flip in the
DB (the row gets created by the handler; refresh_entitlement bumps
`users.plan`).

## 7. Verify entitlement

```bash
curl -s http://localhost:8765/api/billing/me -b cookies.txt | jq
```

Expected after a successful Starter checkout:

```json
{
  "user": {"id": 7, "email": "you@…", "role": "user", "plan": "starter", "is_admin": false},
  "subscription": {
    "stripe_subscription_id": "sub_…",
    "stripe_price_id":        "price_…",
    "plan":                   "starter",
    "status":                 "active",
    "current_period_end":     "2026-07-20T05:45:55+00:00"
  }
}
```

## 8. Cancel / failed-payment / renewal tests

```bash
stripe trigger invoice.payment_failed                 # → status='past_due', plan='free'
stripe trigger customer.subscription.deleted          # → status='canceled', plan='free'
stripe trigger invoice.paid                           # → renews period
```

After each, hit `/api/billing/me` and confirm `subscription.status` /
`user.plan` update as expected.

## Safety rails wired into the code

- The browser only ever sends a `plan` name. The server maps to the price
  ID via the env-var allowlist; an unknown plan returns `400`.
- The `user_id` for the checkout always comes from the signed session
  cookie. Any `user_id` in the request body is ignored.
- `client_reference_id` AND `metadata.user_id` are set on the Checkout
  session and propagate to the Subscription. The webhook handler reads
  these to bind the event back to a local user.
- Webhook calls without a `Stripe-Signature` header → `400`. Bad signature
  → `400`. The handler never trusts an unsigned payload.
- Duplicate webhook deliveries (same `evt_…` ID) → `200` immediately, no
  re-processing. The `stripe_webhook_events.stripe_event_id` PK is the
  idempotency primitive.
- `users.plan` is mutated only inside `billing.refresh_entitlement`, which
  is only called from inside verified webhook handlers. The success
  redirect (`/billing/success`) is UX only — it never grants access.
- A user with an already-active subscription gets `409` from
  `/api/billing/checkout` to prevent accidental double-billing.

## Stopping

`Ctrl+C` the `stripe listen` process and the server. The webhook secret
becomes invalid; start a new `stripe listen` session next time.
