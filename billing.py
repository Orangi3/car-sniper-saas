"""
billing.py — Stripe subscription glue (Phase 2A, TEST MODE).

Surface area kept deliberately small:
  * price_to_plan() / plan_to_price() — server-side allowlist read from
    STRIPE_PRICE_STARTER / STRIPE_PRICE_PRO env vars. The browser never
    chooses a Stripe price ID; it picks a plan name and the server maps.
  * stripe_client() — lazy Stripe SDK init using STRIPE_SECRET_KEY.
  * verify_webhook(payload, sig_header) — verifies the Stripe signature
    against the RAW request body using STRIPE_WEBHOOK_SECRET. Returns
    the parsed event dict or raises.
  * record_event_for_processing(conn, event) — atomic dedup. INSERTs
    the stripe_event_id; if it's a duplicate, returns False so the
    caller can return 200 without re-processing.
  * mark_event_processed(conn, event_id) — stamps processed_at.
  * upsert_subscription(conn, …) — writes the local mirror row.
  * refresh_entitlement(conn, user_id) — recomputes users.plan from the
    user's most-recent entitling subscription.
  * active_subscription_for(conn, user_id) — read helper.
  * dispatch_event(conn, event) — top-level event router.

Design rules enforced here (do NOT relax):
  - Browsers never set a plan/role/price_id/user_id we trust. Every
    decision uses the session's user.id (server-resolved) and the
    server-side price allowlist.
  - Plan changes only happen from inside dispatch_event(). The success-
    redirect handler is a UX courtesy that never touches users.plan.
  - Idempotency is provided by the stripe_webhook_events PK. The
    dispatcher refuses to process the same Stripe event ID twice even
    if the same payload arrives concurrently to two workers.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import db as _db


# ---------- Config (env-driven) ---------------------------------------

# The browser sends a PLAN name. The server maps to a Stripe price via
# this allowlist. Anything not in here is rejected at /api/billing/checkout.
def _env(k: str) -> str:
    return (os.environ.get(k) or "").strip()


def plan_to_price() -> dict[str, str]:
    """{'starter': 'price_…', 'pro': 'price_…'}  — empty entries dropped.
    Read on every call so tests can monkey-patch env without re-importing."""
    out = {}
    s = _env("STRIPE_PRICE_STARTER")
    p = _env("STRIPE_PRICE_PRO")
    if s:
        out["starter"] = s
    if p:
        out["pro"] = p
    return out


def price_to_plan() -> dict[str, str]:
    """Inverse of plan_to_price — used by webhook handlers to map back."""
    return {v: k for k, v in plan_to_price().items()}


def stripe_client():
    """Lazy import + key check. Raises a clear RuntimeError if STRIPE_SECRET_KEY
    is unset, so misconfigured prod fails fast rather than getting authn'd
    errors from Stripe later."""
    import stripe
    key = _env("STRIPE_SECRET_KEY")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY not set")
    stripe.api_key = key
    return stripe


# ---------- Webhook signature + idempotency ---------------------------

def verify_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify the Stripe signature against the RAW body bytes, then parse
    the JSON. Returns a plain dict.

    We intentionally do NOT use stripe.Webhook.construct_event here: that
    helper post-processes the payload through Event._construct_from, which
    on stripe-python 8.x raises AttributeError('object') when the payload
    is a minimal dict lacking the canonical 'object' field. Conflating that
    with a signature failure (since both bubble out of the same call) made
    legitimately-signed unsupported events look like tampering attempts.

    Splitting verification from parsing keeps the contract crisp:
      * stripe.SignatureVerificationError -> tampered/missing/invalid sig
      * ValueError (json)                  -> malformed body bytes
      * RuntimeError                       -> server misconfiguration
    The caller in server.py maps each exception class to its own HTTP code.
    """
    secret = _env("STRIPE_WEBHOOK_SECRET")
    if not secret:
        raise RuntimeError("STRIPE_WEBHOOK_SECRET not set")
    import stripe
    body_str = payload.decode("utf-8") if isinstance(payload, (bytes, bytearray)) \
               else str(payload)
    # Raises stripe.SignatureVerificationError on any signature problem
    # (missing header, no v1 sig, HMAC mismatch, stale timestamp).
    stripe.WebhookSignature.verify_header(
        body_str, sig_header, secret,
        tolerance=stripe.Webhook.DEFAULT_TOLERANCE,
    )
    # Verified. Now parse as plain JSON — no Event class side-effects.
    return json.loads(body_str)


def record_event_for_processing(conn, event: dict) -> bool:
    """INSERT the stripe_event_id atomically. Returns True if this is a
    new event (caller should process it) or False if it's a duplicate
    Stripe re-delivery (caller should 200 without doing work).

    The PK violation is the only sound dedup primitive across workers —
    SELECT-then-INSERT has a race window where two workers each insert."""
    cur = conn.cursor()
    ph = _db.placeholder()
    try:
        cur.execute(
            f"INSERT INTO stripe_webhook_events(stripe_event_id, type, payload) "
            f"VALUES ({ph},{ph},{ph})",
            (event["id"], event["type"], json.dumps(event)))
        return True
    except Exception:
        # The unique constraint on stripe_event_id fired — duplicate.
        # We deliberately catch broadly because the exception type differs
        # between sqlite3.IntegrityError and psycopg2.errors.UniqueViolation.
        return False


def mark_event_processed(conn, event_id: str) -> None:
    cur = conn.cursor()
    ph = _db.placeholder()
    cur.execute(
        f"UPDATE stripe_webhook_events SET processed_at = {ph} "
        f"WHERE stripe_event_id = {ph}",
        (_db.now_utc_iso(), event_id))


# ---------- Subscription row mirror -----------------------------------

def _epoch_to_iso(ts: Optional[int]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (ValueError, TypeError, OSError):
        return None


# ---------- Anomaly + ordering audit ----------------------------------
#
# Anything that the lifecycle code can't make a confident decision about
# (unknown price, out-of-order event, dispute against a missing sub,
# partial refund we can't pin to a subscription, reconciliation diff
# we just patched) is appended to billing_anomalies. NEVER includes raw
# payment data — we only store IDs, statuses, and short reason codes.

# Anomaly types — kept as constants so handlers / tests / future
# migrations can grep for the full set.
ANOMALY_UNKNOWN_PRICE          = "unknown_price"
ANOMALY_OUT_OF_ORDER_EVENT     = "out_of_order_event"
ANOMALY_PARTIAL_REFUND         = "partial_refund_unlinked"
ANOMALY_DISPUTE_UNKNOWN_SUB    = "dispute_unknown_sub"
ANOMALY_RECONCILIATION_PATCH   = "reconciliation_correction"
ANOMALY_SUBSCRIPTION_ORPHAN    = "subscription_orphan"
ANOMALY_REFUND_UNLINKED        = "refund_unlinked"


def record_anomaly(conn, *, anomaly_type: str, severity: str = "warn",
                   user_id: Optional[int] = None,
                   stripe_event_id: Optional[str] = None,
                   stripe_customer_id: Optional[str] = None,
                   stripe_subscription_id: Optional[str] = None,
                   details: Optional[dict] = None) -> None:
    """Append-only audit entry. Details is serialized as JSON — pass small
    summaries (status codes, IDs, counts), NOT the raw event body."""
    ph = _db.placeholder()
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO billing_anomalies(type, severity, user_id, "
        f"  stripe_event_id, stripe_customer_id, stripe_subscription_id, "
        f"  details) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
        (anomaly_type, severity, user_id, stripe_event_id,
         stripe_customer_id, stripe_subscription_id,
         json.dumps(details or {})))


def _existing_event_at(conn, sub_id: Optional[str]) -> Optional[str]:
    """Read the last_event_at timestamp we previously stamped on this
    subscription row. None if the row doesn't exist yet or wasn't stamped."""
    if not sub_id:
        return None
    ph = _db.placeholder()
    cur = conn.cursor()
    cur.execute(
        f"SELECT last_event_at FROM subscriptions WHERE stripe_subscription_id = {ph}",
        (sub_id,))
    row = cur.fetchone()
    if not row:
        return None
    return row["last_event_at"] if not _db.IS_PG else row["last_event_at"]


def is_out_of_order(conn, *, sub_id: Optional[str],
                     event_created_epoch: Optional[int]) -> bool:
    """Returns True if this event's `created` timestamp is older than the
    one we've already applied to the same subscription. Stripe doesn't
    promise in-order delivery; without this guard an older
    customer.subscription.updated could overwrite a newer one."""
    if not (sub_id and event_created_epoch):
        return False
    incoming = _epoch_to_iso(event_created_epoch)
    existing = _existing_event_at(conn, sub_id)
    if not (existing and incoming):
        return False
    # ISO 8601 strings with the same timezone suffix (we always emit +00:00)
    # sort lexicographically the same as their datetimes — no parsing needed.
    return incoming < existing


def _stamp_last_event(conn, sub_id: str, event: dict) -> None:
    """Mark the subscription row with the timestamp + id of the most-recent
    event we processed for it. Run AFTER the row is upserted."""
    if not sub_id:
        return
    ph = _db.placeholder()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE subscriptions SET last_event_at = {ph}, last_event_id = {ph} "
        f"WHERE stripe_subscription_id = {ph}",
        (_epoch_to_iso(event.get("created")), event.get("id"), sub_id))


def upsert_subscription(conn, *, user_id: int, stripe_customer_id: str,
                        stripe_subscription_id: str, stripe_price_id: str,
                        status: str, current_period_end: Optional[int],
                        cancel_at_period_end: bool) -> None:
    """Insert-or-update the local mirror. stripe_subscription_id is unique
    so two events for the same sub collapse to one row."""
    plan = price_to_plan().get(stripe_price_id, "free")
    now = _db.now_utc_iso()
    period_end_iso = _epoch_to_iso(current_period_end)
    cur = conn.cursor()
    ph = _db.placeholder()
    if _db.IS_PG:
        cur.execute(
            "INSERT INTO subscriptions(user_id, stripe_customer_id, "
            "  stripe_subscription_id, stripe_price_id, plan, status, "
            "  current_period_end, cancel_at_period_end, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT(stripe_subscription_id) DO UPDATE SET "
            "  stripe_price_id=excluded.stripe_price_id, "
            "  plan=excluded.plan, status=excluded.status, "
            "  current_period_end=excluded.current_period_end, "
            "  cancel_at_period_end=excluded.cancel_at_period_end, "
            "  updated_at=excluded.updated_at",
            (user_id, stripe_customer_id, stripe_subscription_id,
             stripe_price_id, plan, status, period_end_iso,
             bool(cancel_at_period_end), now))
    else:
        cur.execute(
            "INSERT INTO subscriptions(user_id, stripe_customer_id, "
            "  stripe_subscription_id, stripe_price_id, plan, status, "
            "  current_period_end, cancel_at_period_end, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(stripe_subscription_id) DO UPDATE SET "
            "  stripe_price_id=excluded.stripe_price_id, "
            "  plan=excluded.plan, status=excluded.status, "
            "  current_period_end=excluded.current_period_end, "
            "  cancel_at_period_end=excluded.cancel_at_period_end, "
            "  updated_at=excluded.updated_at",
            (user_id, stripe_customer_id, stripe_subscription_id,
             stripe_price_id, plan, status, period_end_iso,
             1 if cancel_at_period_end else 0, now))


def active_subscription_for(conn, user_id: int) -> Optional[dict]:
    """Returns the entitling subscription row for this user, or None.
    'Entitling' = status in {'active','trialing'}. Picks the most recent
    if (somehow) two exist."""
    cur = conn.cursor()
    ph = _db.placeholder()
    cur.execute(
        f"SELECT id, user_id, stripe_customer_id, stripe_subscription_id, "
        f"       stripe_price_id, plan, status, current_period_end, "
        f"       cancel_at_period_end "
        f"FROM subscriptions "
        f"WHERE user_id = {ph} AND status IN ('active','trialing') "
        f"ORDER BY updated_at DESC LIMIT 1",
        (user_id,))
    row = cur.fetchone()
    return dict(row) if row else None


# ---------- Entitlement evaluation ------------------------------------
#
# Single chokepoint: refresh_entitlement(conn, user_id) reads the user's
# subscription state and writes the resulting plan onto users.plan.
# Called from every event handler that could change effective access.

ENTITLING_STATUSES = {"active", "trialing"}


def _now_utc_iso() -> str:
    """Shorthand for the entitlement evaluator below — keeps the SQL
    in WHERE-clause-friendly text form."""
    return datetime.now(timezone.utc).isoformat()


def refresh_entitlement(conn, user_id: int) -> str:
    """Recompute the user's plan from their subscription rows and write
    to users.plan if changed. Returns the new plan name.

    Lifecycle rules baked in:
      * status IN ('active','trialing') -> ENTITLED, regardless of
        cancel_at_period_end (the whole point of "cancel at period end"
        is that the user keeps access until period_end actually arrives).
      * status='past_due' / 'unpaid' / 'canceled' / 'incomplete_expired'
        -> NOT entitled, plan drops to free.
      * status='active' but current_period_end is in the PAST and
        cancel_at_period_end was set -> defensive downgrade. Stripe will
        normally fire customer.subscription.deleted at period_end, but if
        that webhook gets lost we shouldn't keep granting access forever.

    Never touches users.role; admin promotions stay out of band."""
    cur = conn.cursor()
    ph = _db.placeholder()
    cur.execute(
        f"SELECT plan, status, current_period_end, cancel_at_period_end "
        f"FROM subscriptions WHERE user_id = {ph}",
        (user_id,))
    now_iso = _now_utc_iso()
    entitling: list[str] = []
    for r in cur.fetchall():
        row = dict(r)
        status = row.get("status") or ""
        if status not in ENTITLING_STATUSES:
            continue
        # Defensive: scheduled cancellation whose end date has passed and
        # we never got a subscription.deleted event. Drop access.
        cancel_eop = bool(row.get("cancel_at_period_end"))
        period_end = row.get("current_period_end") or ""
        if cancel_eop and period_end and period_end < now_iso:
            continue
        entitling.append(row.get("plan") or "")
    if "pro" in entitling:
        new_plan = "pro"
    elif "starter" in entitling:
        new_plan = "starter"
    else:
        new_plan = "free"

    cur.execute(f"SELECT plan, role FROM users WHERE id = {ph}", (user_id,))
    row = cur.fetchone()
    if not row:
        return new_plan
    current_plan = row["plan"] if not _db.IS_PG else row["plan"]
    if current_plan != new_plan:
        cur.execute(f"UPDATE users SET plan = {ph} WHERE id = {ph}",
                    (new_plan, user_id))
    return new_plan


# ---------- Event dispatch --------------------------------------------

def _user_id_from_event_obj(obj: dict) -> Optional[int]:
    """Pull the local user.id off a Stripe object. We require it to be
    set as metadata.user_id on the Subscription/Session — set there at
    checkout time. Stripe never sees the email-as-id, only an opaque int."""
    md = (obj.get("metadata") or {})
    uid = md.get("user_id") or md.get("local_user_id")
    if uid is None:
        # checkout.session.completed also carries client_reference_id
        uid = obj.get("client_reference_id")
    try:
        return int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None


def _find_user_id_for_subscription(conn, sub: dict) -> Optional[int]:
    """For subscription.updated/.deleted/invoice.* events, the inbound
    object may not carry our metadata. Fall back to the mirror row we
    created on checkout.session.completed."""
    uid = _user_id_from_event_obj(sub)
    if uid:
        return uid
    sub_id = sub.get("id") if sub.get("object") == "subscription" else \
             sub.get("subscription")
    if not sub_id:
        return None
    cur = conn.cursor()
    ph = _db.placeholder()
    cur.execute(
        f"SELECT user_id FROM subscriptions WHERE stripe_subscription_id = {ph}",
        (sub_id,))
    row = cur.fetchone()
    return int(row["user_id"]) if row else None


def _handle_checkout_completed(conn, event: dict, obj: dict) -> str:
    """checkout.session.completed — user finished Hosted Checkout. Provision
    the subscription row + bump entitlement. We trust ONLY the values in
    the Stripe object; never reach back to the client for plan/user."""
    if (obj.get("mode") or "") != "subscription":
        return "ignored_non_subscription"
    user_id = _user_id_from_event_obj(obj)
    if not user_id:
        # Either anonymous checkout or a missing metadata.user_id — record
        # for review, do not provision.
        record_anomaly(conn,
            anomaly_type=ANOMALY_SUBSCRIPTION_ORPHAN, severity="critical",
            stripe_event_id=event.get("id"),
            stripe_customer_id=obj.get("customer"),
            stripe_subscription_id=obj.get("subscription"),
            details={"reason": "no user_id binding on checkout session"})
        return "anomaly_no_user_binding"
    sub_id = obj.get("subscription")
    customer_id = obj.get("customer")
    if not (sub_id and customer_id):
        return "ignored_missing_ids"
    try:
        sub = stripe_client().Subscription.retrieve(sub_id)
    except Exception:
        return "stripe_retrieve_failed"  # Stripe will retry the webhook
    items = (sub.get("items") or {}).get("data") or []
    if not items:
        return "ignored_no_items"
    price_id = (items[0].get("price") or {}).get("id")
    if not price_id or price_id not in price_to_plan():
        record_anomaly(conn,
            anomaly_type=ANOMALY_UNKNOWN_PRICE, severity="critical",
            user_id=user_id,
            stripe_event_id=event.get("id"),
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub_id,
            details={"price_id": price_id,
                     "allowlist": sorted(price_to_plan().keys())})
        return "anomaly_unknown_price"
    if is_out_of_order(conn, sub_id=sub_id, event_created_epoch=event.get("created")):
        record_anomaly(conn,
            anomaly_type=ANOMALY_OUT_OF_ORDER_EVENT, severity="info",
            user_id=user_id,
            stripe_event_id=event.get("id"),
            stripe_subscription_id=sub_id,
            details={"event_type": event.get("type"),
                     "event_created": event.get("created")})
        return "out_of_order_skipped"
    upsert_subscription(
        conn,
        user_id=user_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=sub_id,
        stripe_price_id=price_id,
        status=sub.get("status") or "active",
        current_period_end=sub.get("current_period_end"),
        cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
    )
    _stamp_last_event(conn, sub_id, event)
    refresh_entitlement(conn, user_id)
    return "handled"


def _handle_subscription_updated(conn, event: dict, obj: dict) -> str:
    """customer.subscription.updated — plan switch, status change, cancel-
    at-period-end toggle, etc. The Stripe object is the source of truth;
    we just mirror it locally and let refresh_entitlement decide access."""
    sub_id = obj.get("id")
    user_id = _find_user_id_for_subscription(conn, obj)
    if not user_id:
        record_anomaly(conn,
            anomaly_type=ANOMALY_SUBSCRIPTION_ORPHAN, severity="warn",
            stripe_event_id=event.get("id"),
            stripe_customer_id=obj.get("customer"),
            stripe_subscription_id=sub_id,
            details={"event_type": event.get("type")})
        return "anomaly_orphan"
    items = (obj.get("items") or {}).get("data") or []
    if not items:
        return "ignored_no_items"
    price_id = (items[0].get("price") or {}).get("id")
    if not price_id:
        return "ignored_no_price"
    if price_id not in price_to_plan():
        record_anomaly(conn,
            anomaly_type=ANOMALY_UNKNOWN_PRICE, severity="critical",
            user_id=user_id,
            stripe_event_id=event.get("id"),
            stripe_customer_id=obj.get("customer"),
            stripe_subscription_id=sub_id,
            details={"price_id": price_id,
                     "allowlist": sorted(price_to_plan().keys())})
        return "anomaly_unknown_price"
    if is_out_of_order(conn, sub_id=sub_id, event_created_epoch=event.get("created")):
        record_anomaly(conn,
            anomaly_type=ANOMALY_OUT_OF_ORDER_EVENT, severity="info",
            user_id=user_id,
            stripe_event_id=event.get("id"),
            stripe_subscription_id=sub_id,
            details={"event_type": event.get("type"),
                     "event_created": event.get("created")})
        return "out_of_order_skipped"
    upsert_subscription(
        conn,
        user_id=user_id,
        stripe_customer_id=obj.get("customer") or "",
        stripe_subscription_id=sub_id,
        stripe_price_id=price_id,
        status=obj.get("status") or "active",
        current_period_end=obj.get("current_period_end"),
        cancel_at_period_end=bool(obj.get("cancel_at_period_end")),
    )
    _stamp_last_event(conn, sub_id, event)
    refresh_entitlement(conn, user_id)
    return "handled"


def _handle_subscription_deleted(conn, event: dict, obj: dict) -> str:
    """customer.subscription.deleted — the sub is gone for good. Mark
    canceled + drop plan to free (refresh_entitlement decides; other
    active subs would keep the user entitled)."""
    sub_id = obj.get("id")
    user_id = _find_user_id_for_subscription(conn, obj)
    if not user_id:
        return "anomaly_orphan"
    if is_out_of_order(conn, sub_id=sub_id, event_created_epoch=event.get("created")):
        # A late-arriving deletion of a sub that's since been re-instated
        # would clobber the reinstatement. Skip.
        record_anomaly(conn,
            anomaly_type=ANOMALY_OUT_OF_ORDER_EVENT, severity="info",
            user_id=user_id, stripe_event_id=event.get("id"),
            stripe_subscription_id=sub_id,
            details={"event_type": event.get("type")})
        return "out_of_order_skipped"
    cur = conn.cursor()
    ph = _db.placeholder()
    cur.execute(
        f"UPDATE subscriptions SET status = 'canceled', updated_at = {ph} "
        f"WHERE stripe_subscription_id = {ph}",
        (_db.now_utc_iso(), sub_id))
    _stamp_last_event(conn, sub_id, event)
    refresh_entitlement(conn, user_id)
    return "handled"


def _handle_invoice_paid(conn, event: dict, obj: dict) -> str:
    """invoice.paid — successful renewal. Refresh period end + reactivate
    status if Stripe says 'active' now."""
    sub_id = obj.get("subscription")
    if not sub_id:
        return "ignored_no_subscription"
    user_id = _find_user_id_for_subscription(conn, obj)
    if not user_id:
        return "anomaly_orphan"
    try:
        sub = stripe_client().Subscription.retrieve(sub_id)
    except Exception:
        return "stripe_retrieve_failed"
    items = (sub.get("items") or {}).get("data") or []
    if not items:
        return "ignored_no_items"
    price_id = (items[0].get("price") or {}).get("id")
    if not price_id:
        return "ignored_no_price"
    if price_id not in price_to_plan():
        record_anomaly(conn,
            anomaly_type=ANOMALY_UNKNOWN_PRICE, severity="critical",
            user_id=user_id, stripe_event_id=event.get("id"),
            stripe_subscription_id=sub_id,
            details={"price_id": price_id})
        return "anomaly_unknown_price"
    if is_out_of_order(conn, sub_id=sub_id, event_created_epoch=event.get("created")):
        return "out_of_order_skipped"
    upsert_subscription(
        conn,
        user_id=user_id,
        stripe_customer_id=sub.get("customer") or obj.get("customer") or "",
        stripe_subscription_id=sub_id,
        stripe_price_id=price_id,
        status=sub.get("status") or "active",
        current_period_end=sub.get("current_period_end"),
        cancel_at_period_end=bool(sub.get("cancel_at_period_end")),
    )
    _stamp_last_event(conn, sub_id, event)
    refresh_entitlement(conn, user_id)
    return "handled"


def _handle_invoice_failed(conn, event: dict, obj: dict) -> str:
    """invoice.payment_failed — payment didn't go through. Mirror Stripe's
    past_due status; refresh_entitlement will drop the plan."""
    sub_id = obj.get("subscription")
    if not sub_id:
        return "ignored_no_subscription"
    user_id = _find_user_id_for_subscription(conn, obj)
    if not user_id:
        return "anomaly_orphan"
    if is_out_of_order(conn, sub_id=sub_id, event_created_epoch=event.get("created")):
        return "out_of_order_skipped"
    cur = conn.cursor()
    ph = _db.placeholder()
    cur.execute(
        f"UPDATE subscriptions SET status = 'past_due', updated_at = {ph} "
        f"WHERE stripe_subscription_id = {ph}",
        (_db.now_utc_iso(), sub_id))
    _stamp_last_event(conn, sub_id, event)
    refresh_entitlement(conn, user_id)
    return "handled"


# ---------- Refund + dispute handlers ---------------------------------
#
# Policy (Phase 2B):
#   * Full refund   -> revoke paid access immediately.
#   * Partial refund-> do NOT guess. Record anomaly for admin review.
#   * Dispute opened-> suspend paid access immediately, regardless of
#                      eventual outcome. Treat as adversarial.
#   * Dispute closed-> rely on reconciliation / subscription.updated
#                      to restore access if the merchant won; we do
#                      nothing automatic here, because Stripe will refund
#                      on loss anyway.

def _subscription_id_for_charge(charge: dict) -> Optional[str]:
    """Charge -> subscription via invoice. Returns None for one-off or
    unlinkable charges (we never created any, so seeing one is itself
    an anomaly worth recording)."""
    inv_id = charge.get("invoice")
    if not inv_id:
        return None
    try:
        invoice = stripe_client().Invoice.retrieve(inv_id)
    except Exception:
        return None
    return invoice.get("subscription")


def _find_sub_row_for_subscription_id(conn, sub_id: str) -> Optional[dict]:
    if not sub_id:
        return None
    ph = _db.placeholder()
    cur = conn.cursor()
    cur.execute(
        f"SELECT user_id, stripe_customer_id, status FROM subscriptions "
        f"WHERE stripe_subscription_id = {ph}",
        (sub_id,))
    row = cur.fetchone()
    return dict(row) if row else None


def _handle_charge_refunded(conn, event: dict, charge: dict) -> str:
    """charge.refunded — Stripe split refunds into full vs partial.
    Full: amount_refunded == amount. Partial: < amount.

    Full refund of a subscription invoice -> revoke access now.
    Partial -> anomaly, no access change."""
    total = int(charge.get("amount") or 0)
    refunded = int(charge.get("amount_refunded") or 0)
    sub_id = _subscription_id_for_charge(charge)
    if not sub_id:
        # Refund of a charge we can't link to a subscription. Could be a
        # one-off or a refund of a deleted sub. Record for admin.
        record_anomaly(conn,
            anomaly_type=ANOMALY_REFUND_UNLINKED, severity="warn",
            stripe_event_id=event.get("id"),
            stripe_customer_id=charge.get("customer"),
            details={"charge_id": charge.get("id"),
                     "amount": total, "amount_refunded": refunded,
                     "is_full_refund": refunded >= total})
        return "anomaly_refund_unlinked"
    row = _find_sub_row_for_subscription_id(conn, sub_id)
    if not row:
        record_anomaly(conn,
            anomaly_type=ANOMALY_REFUND_UNLINKED, severity="warn",
            stripe_event_id=event.get("id"),
            stripe_subscription_id=sub_id,
            details={"charge_id": charge.get("id"),
                     "reason": "subscription_not_in_local_mirror"})
        return "anomaly_refund_unknown_sub"
    if refunded < total:
        # Partial refund — explicit policy is to record + not change access.
        record_anomaly(conn,
            anomaly_type=ANOMALY_PARTIAL_REFUND, severity="warn",
            user_id=row["user_id"],
            stripe_event_id=event.get("id"),
            stripe_subscription_id=sub_id,
            details={"charge_id": charge.get("id"),
                     "amount": total, "amount_refunded": refunded})
        return "partial_refund_recorded"
    # Full refund -> revoke access by canceling the local mirror NOW.
    # We don't wait for Stripe to also fire subscription.deleted; refunds
    # imply the user shouldn't have paid access until Stripe sorts itself.
    ph = _db.placeholder()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE subscriptions SET status = 'canceled', updated_at = {ph} "
        f"WHERE stripe_subscription_id = {ph}",
        (_db.now_utc_iso(), sub_id))
    _stamp_last_event(conn, sub_id, event)
    refresh_entitlement(conn, row["user_id"])
    return "full_refund_revoked"


def _handle_dispute_created(conn, event: dict, dispute: dict) -> str:
    """charge.dispute.created — adversarial. Suspend access immediately.

    'Suspending' = marking the local subscription status='past_due' so the
    entitlement evaluator drops the plan. We don't cancel — if the user
    wins the dispute, reconciliation / a later subscription.updated will
    restore the live status without us having to remember anything."""
    charge_id = dispute.get("charge")
    if not charge_id:
        record_anomaly(conn,
            anomaly_type=ANOMALY_DISPUTE_UNKNOWN_SUB, severity="critical",
            stripe_event_id=event.get("id"),
            details={"dispute_id": dispute.get("id"),
                     "reason": "no_charge_on_dispute"})
        return "anomaly_dispute_no_charge"
    # Need to fetch the charge to learn its invoice/subscription
    try:
        charge = stripe_client().Charge.retrieve(charge_id)
    except Exception:
        return "stripe_retrieve_failed"
    sub_id = _subscription_id_for_charge(charge)
    if not sub_id:
        record_anomaly(conn,
            anomaly_type=ANOMALY_DISPUTE_UNKNOWN_SUB, severity="critical",
            stripe_event_id=event.get("id"),
            stripe_customer_id=charge.get("customer"),
            details={"dispute_id": dispute.get("id"),
                     "charge_id": charge_id})
        return "anomaly_dispute_unlinked"
    row = _find_sub_row_for_subscription_id(conn, sub_id)
    if not row:
        record_anomaly(conn,
            anomaly_type=ANOMALY_DISPUTE_UNKNOWN_SUB, severity="critical",
            stripe_event_id=event.get("id"),
            stripe_subscription_id=sub_id,
            details={"dispute_id": dispute.get("id"),
                     "reason": "subscription_not_in_local_mirror"})
        return "anomaly_dispute_unknown_local_sub"
    ph = _db.placeholder()
    cur = conn.cursor()
    cur.execute(
        f"UPDATE subscriptions SET status = 'past_due', updated_at = {ph} "
        f"WHERE stripe_subscription_id = {ph}",
        (_db.now_utc_iso(), sub_id))
    _stamp_last_event(conn, sub_id, event)
    refresh_entitlement(conn, row["user_id"])
    # Record for admin visibility even though we acted automatically
    record_anomaly(conn,
        anomaly_type="dispute_opened_access_suspended", severity="critical",
        user_id=row["user_id"], stripe_event_id=event.get("id"),
        stripe_subscription_id=sub_id,
        details={"dispute_id": dispute.get("id"),
                 "reason": dispute.get("reason")})
    return "dispute_suspended"


def _handle_dispute_closed(conn, event: dict, dispute: dict) -> str:
    """charge.dispute.closed — informational. The user's eventual access
    state will be re-asserted by the next subscription.updated event or
    by reconciliation. We log for audit, don't touch entitlement."""
    record_anomaly(conn,
        anomaly_type="dispute_closed", severity="info",
        stripe_event_id=event.get("id"),
        details={"dispute_id": dispute.get("id"),
                 "status": dispute.get("status")})
    return "dispute_closed_logged"


_HANDLERS = {
    "checkout.session.completed":   _handle_checkout_completed,
    "customer.subscription.created": _handle_subscription_updated,
    "customer.subscription.updated": _handle_subscription_updated,
    "customer.subscription.deleted": _handle_subscription_deleted,
    "invoice.paid":                  _handle_invoice_paid,
    "invoice.payment_succeeded":     _handle_invoice_paid,   # alias
    "invoice.payment_failed":        _handle_invoice_failed,
    "charge.refunded":               _handle_charge_refunded,
    "charge.dispute.created":        _handle_dispute_created,
    "charge.dispute.closed":         _handle_dispute_closed,
}


def dispatch_event(conn, event: dict) -> str:
    """Route a verified Stripe event to its handler. Returns a short
    outcome string for logging — Stripe only cares about the HTTP 200 the
    caller returns next."""
    etype = event.get("type") or ""
    handler = _HANDLERS.get(etype)
    if handler is None:
        return "unknown_type"
    obj = (event.get("data") or {}).get("object") or {}
    return handler(conn, event, obj) or "handled"


# ---------- Stripe-backed reconciliation ------------------------------
#
# A scheduled job that fetches the LIVE state of each local subscription
# from Stripe and patches divergences. Source of truth is always Stripe;
# we are correcting our cache.
#
# Bounded: stops after `max_count` records OR `time_budget_s` seconds.
# Rate-conscious: small sleep between Stripe calls to stay well under any
# per-account RPS limit.
# Observable: writes one row to billing_reconciliation_runs.
# Idempotent: re-running immediately is a no-op.

def _local_sub_rows(conn, max_count: int) -> list[dict]:
    """Return the local subscription rows the reconciler should check.
    Skip 'canceled' rows older than X — they're terminal."""
    ph = _db.placeholder()
    cur = conn.cursor()
    cur.execute(
        f"SELECT user_id, stripe_customer_id, stripe_subscription_id, "
        f"       stripe_price_id, plan, status, current_period_end, "
        f"       cancel_at_period_end, last_event_at "
        f"FROM subscriptions "
        f"WHERE status NOT IN ('canceled') "
        f"ORDER BY updated_at ASC LIMIT {ph}",
        (int(max_count),))
    return [dict(r) for r in cur.fetchall()]


def _stripe_sub_diff(local: dict, remote: dict) -> dict:
    """Return a dict of {field: (local, remote)} for fields that differ.
    Only fields we actually mirror are compared."""
    diffs: dict = {}
    remote_status = remote.get("status") or ""
    remote_cap = bool(remote.get("cancel_at_period_end"))
    remote_period_end = _epoch_to_iso(remote.get("current_period_end"))
    remote_items = (remote.get("items") or {}).get("data") or []
    remote_price = (remote_items[0].get("price") or {}).get("id") if remote_items else None
    if local.get("status") != remote_status:
        diffs["status"] = (local.get("status"), remote_status)
    if bool(local.get("cancel_at_period_end")) != remote_cap:
        diffs["cancel_at_period_end"] = (bool(local.get("cancel_at_period_end")), remote_cap)
    if (local.get("current_period_end") or None) != (remote_period_end or None):
        diffs["current_period_end"] = (local.get("current_period_end"), remote_period_end)
    if remote_price and local.get("stripe_price_id") != remote_price:
        diffs["stripe_price_id"] = (local.get("stripe_price_id"), remote_price)
    return diffs


def reconcile_all(*, max_count: int = 200, time_budget_s: int = 60,
                  sleep_between_s: float = 0.05,
                  triggered_by: str = "cron") -> dict:
    """Walk local subscriptions, fetch each from Stripe, fix divergences,
    write one audit row. Returns a summary dict identical to what's
    persisted (caller can JSON it back to an admin response).

    Safe to retry; safe to run while webhooks are coming in (every row
    update goes through upsert_subscription's ON CONFLICT branch).

    Test-friendly: callers can monkey-patch stripe_client().Subscription
    .retrieve to a stub; no live network required."""
    started = datetime.now(timezone.utc).isoformat()
    t0 = time.time()
    checked = 0
    mismatches = 0
    corrections = 0
    errors = 0
    last_error: Optional[str] = None

    # Insert the run row up front so a crash mid-loop still leaves a
    # record. We patch finished_at + counters at the end.
    with _db.transaction() as conn:
        cur = conn.cursor()
        if _db.IS_PG:
            cur.execute(
                "INSERT INTO billing_reconciliation_runs(started_at, triggered_by) "
                "VALUES (%s,%s) RETURNING id",
                (started, triggered_by))
            run_id = cur.fetchone()["id"]
        else:
            cur.execute(
                "INSERT INTO billing_reconciliation_runs(started_at, triggered_by) "
                "VALUES (?,?)",
                (started, triggered_by))
            run_id = cur.lastrowid

    # Snapshot the rows to check (separate tx so the read isn't long-held)
    with _db.transaction() as conn:
        rows = _local_sub_rows(conn, max_count)

    sdk = None
    try:
        sdk = stripe_client()
    except RuntimeError as e:
        # No Stripe key configured — fail the whole run loudly.
        last_error = f"{type(e).__name__}: {e}"
        errors = 1

    if sdk is not None:
        for r in rows:
            if (time.time() - t0) >= time_budget_s:
                break
            sub_id = r.get("stripe_subscription_id")
            if not sub_id:
                continue
            try:
                remote = sdk.Subscription.retrieve(sub_id)
            except Exception as e:
                errors += 1
                last_error = f"{type(e).__name__}: {str(e)[:160]}"
                if sleep_between_s:
                    time.sleep(sleep_between_s)
                continue
            checked += 1
            diffs = _stripe_sub_diff(r, remote)
            if diffs:
                mismatches += 1
                # Apply the correction transactionally.
                try:
                    with _db.transaction() as conn:
                        remote_items = (remote.get("items") or {}).get("data") or []
                        price_id = (remote_items[0].get("price") or {}).get("id") \
                                   if remote_items else r.get("stripe_price_id")
                        upsert_subscription(
                            conn,
                            user_id=r["user_id"],
                            stripe_customer_id=remote.get("customer") or r["stripe_customer_id"],
                            stripe_subscription_id=sub_id,
                            stripe_price_id=price_id or "",
                            status=remote.get("status") or r["status"],
                            current_period_end=remote.get("current_period_end"),
                            cancel_at_period_end=bool(remote.get("cancel_at_period_end")),
                        )
                        record_anomaly(conn,
                            anomaly_type=ANOMALY_RECONCILIATION_PATCH,
                            severity="info",
                            user_id=r["user_id"],
                            stripe_subscription_id=sub_id,
                            details={"diffs": {k: list(v) for k, v in diffs.items()},
                                     "run_id": run_id})
                        refresh_entitlement(conn, r["user_id"])
                    corrections += 1
                except Exception as e:
                    errors += 1
                    last_error = f"{type(e).__name__}: {str(e)[:160]}"
            if sleep_between_s:
                time.sleep(sleep_between_s)

    finished = datetime.now(timezone.utc).isoformat()
    summary = {
        "id": run_id,
        "started_at": started,
        "finished_at": finished,
        "checked": checked,
        "mismatches_found": mismatches,
        "corrections_applied": corrections,
        "errors": errors,
        "last_error": last_error,
        "triggered_by": triggered_by,
        "elapsed_s": round(time.time() - t0, 3),
    }
    # Persist final counters
    with _db.transaction() as conn:
        ph = _db.placeholder()
        cur = conn.cursor()
        cur.execute(
            f"UPDATE billing_reconciliation_runs "
            f"SET finished_at = {ph}, checked = {ph}, "
            f"    mismatches_found = {ph}, corrections_applied = {ph}, "
            f"    errors = {ph}, last_error = {ph} "
            f"WHERE id = {ph}",
            (finished, checked, mismatches, corrections, errors,
             last_error, run_id))
    return summary


# ---------- DB-backed job lock ----------------------------------------
#
# Prevents two reconciliation runs (cron timer + manual `python -m
# jobs.reconcile_billing`, or two cron hosts) from racing each other
# against the same database. Works identically on SQLite and Postgres
# because the acquire is a single conditional UPDATE.

class JobLockNotAcquired(RuntimeError):
    """Raised when the named job lock is held by someone else and the
    caller asked NOT to wait or steal."""
    pass


def _job_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_iso(dt: datetime) -> str:
    return dt.isoformat()


def acquire_job_lock(job_name: str, *, holder: str,
                     lease_seconds: int = 600,
                     steal_after_seconds: int = 3600) -> bool:
    """Backwards-compatible facade over joblock.acquire — kept so the
    existing reconciliation tests + callers continue to work without
    touching billing.py's public surface."""
    import joblock
    return joblock.acquire(job_name, holder=holder,
                            lease_seconds=lease_seconds,
                            steal_after_seconds=steal_after_seconds)


def release_job_lock(job_name: str, *, holder: str) -> None:
    import joblock
    joblock.release(job_name, holder=holder)


def current_lock_holder(job_name: str) -> Optional[dict]:
    import joblock
    return joblock.current_holder(job_name)


def reconcile_billing_with_lock(*, holder: str,
                                 lease_seconds: int = 600,
                                 steal_after_seconds: int = 3600,
                                 dry_run: bool = False,
                                 **reconcile_kwargs) -> dict:
    """Wrap reconcile_all() with the DB job lock. Returns the same summary
    dict, plus a 'lock_acquired' field. If the lock is held, returns
    {'lock_acquired': False, ...} and does NOT run reconciliation.

    dry_run=True walks the same rows and reports mismatches but rolls
    back any corrections, so operators can sanity-check before scheduling
    a real run."""
    if not acquire_job_lock("reconcile_billing", holder=holder,
                             lease_seconds=lease_seconds,
                             steal_after_seconds=steal_after_seconds):
        existing = current_lock_holder("reconcile_billing") or {}
        return {
            "lock_acquired": False,
            "reason": "another reconciliation is already running",
            "existing_holder": existing.get("holder"),
            "existing_acquired_at": existing.get("acquired_at"),
            "existing_expires_at":  existing.get("expires_at"),
        }
    try:
        if dry_run:
            summary = reconcile_all_dry_run(**reconcile_kwargs)
        else:
            summary = reconcile_all(**reconcile_kwargs)
        summary["lock_acquired"] = True
        summary["dry_run"] = dry_run
        return summary
    finally:
        release_job_lock("reconcile_billing", holder=holder)


def reconcile_all_dry_run(*, max_count: int = 200,
                           time_budget_s: int = 60,
                           sleep_between_s: float = 0.05,
                           triggered_by: str = "dry-run") -> dict:
    """Compare-only variant of reconcile_all. Reports what WOULD be
    corrected without writing anything. No anomaly rows, no
    billing_reconciliation_runs entry — purely diagnostic."""
    t0 = time.time()
    checked = 0
    mismatches = 0
    errors = 0
    last_error: Optional[str] = None
    sample_diffs: list[dict] = []

    try:
        sdk = stripe_client()
    except RuntimeError as e:
        return {
            "checked": 0, "mismatches_found": 0,
            "corrections_applied": 0, "errors": 1,
            "last_error": f"{type(e).__name__}: {e}",
            "triggered_by": triggered_by, "elapsed_s": 0.0,
            "sample_diffs": [],
        }

    with _db.transaction() as conn:
        rows = _local_sub_rows(conn, max_count)

    for r in rows:
        if (time.time() - t0) >= time_budget_s:
            break
        sub_id = r.get("stripe_subscription_id")
        if not sub_id:
            continue
        try:
            remote = sdk.Subscription.retrieve(sub_id)
        except Exception as e:
            errors += 1
            last_error = f"{type(e).__name__}: {str(e)[:160]}"
            if sleep_between_s:
                time.sleep(sleep_between_s)
            continue
        checked += 1
        diffs = _stripe_sub_diff(r, remote)
        if diffs:
            mismatches += 1
            if len(sample_diffs) < 20:
                sample_diffs.append({
                    "stripe_subscription_id": sub_id,
                    "diffs": {k: list(v) for k, v in diffs.items()},
                })
        if sleep_between_s:
            time.sleep(sleep_between_s)

    return {
        "checked": checked, "mismatches_found": mismatches,
        "corrections_applied": 0, "errors": errors,
        "last_error": last_error,
        "triggered_by": triggered_by,
        "elapsed_s": round(time.time() - t0, 3),
        "sample_diffs": sample_diffs,
    }


def billing_health(conn) -> dict:
    """Snapshot for the admin health endpoint."""
    cur = conn.cursor()
    # Last reconciliation run
    cur.execute(
        "SELECT id, started_at, finished_at, checked, mismatches_found, "
        "       corrections_applied, errors, last_error, triggered_by "
        "FROM billing_reconciliation_runs "
        "ORDER BY started_at DESC LIMIT 1")
    last_row = cur.fetchone()
    last_run = dict(last_row) if last_row else None
    # Unresolved anomalies by severity
    cur.execute(
        "SELECT severity, COUNT(*) AS n FROM billing_anomalies "
        "WHERE resolved = 0 GROUP BY severity")
    by_sev = {(r["severity"]): int(r["n"]) for r in cur.fetchall()}
    # Top recent anomalies for quick triage
    cur.execute(
        "SELECT id, type, severity, user_id, stripe_subscription_id, "
        "       stripe_event_id, details, created_at "
        "FROM billing_anomalies "
        "WHERE resolved = 0 ORDER BY created_at DESC LIMIT 25")
    recent = []
    for r in cur.fetchall():
        d = dict(r)
        try:
            d["details"] = json.loads(d.get("details") or "{}")
        except Exception:
            pass
        recent.append(d)
    # Subscription state distribution
    cur.execute(
        "SELECT status, COUNT(*) AS n FROM subscriptions GROUP BY status")
    by_status = {r["status"]: int(r["n"]) for r in cur.fetchall()}
    return {
        "last_reconciliation": last_run,
        "unresolved_anomalies_by_severity": by_sev,
        "recent_unresolved_anomalies": recent,
        "subscriptions_by_status": by_status,
        "reconcile_job_lock": current_lock_holder("reconcile_billing"),
    }
