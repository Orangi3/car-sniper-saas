"""
plans.py — subscription entitlements, enforced server-side.

Internal plan names are unchanged ("free", "starter", "pro") so billing,
Stripe price mapping (STRIPE_PRICE_STARTER / STRIPE_PRICE_PRO) and stored
users.plan values keep working. Customer-facing names:

    starter  →  "Lite"  ($2.99/mo target)
    pro      →  "Pro"   ($4.99/mo target)

Prices live in Stripe, not here. Limits can be overridden without a deploy
via PLAN_LIMITS_JSON, e.g. '{"starter": {"saved_searches": 5}}'.

Cost-control rationale: ingestion and valuation are shared across all
subscribers (run once per listing, not per user), so a subscriber's
marginal cost is their notifications — SMS above all, which is why it is
Pro-only and capped per month.
"""
from __future__ import annotations

import json
import os
from copy import deepcopy

DISPLAY_NAMES = {"free": "Free", "starter": "Lite", "pro": "Pro"}

_DEFAULTS: dict[str, dict] = {
    "free": {
        "analyses_per_day": 20,
        "opportunities": 10,
        "saved_searches": 0,
        "alerts_per_day": 0,
        "price_drop_alerts": False,
        "aggressive_mode": False,
        "profit_scenarios": False,
        "custom_profit_assumptions": False,
        "sms_per_month": 0,
        "webhook": False,
    },
    "starter": {   # Lite
        "analyses_per_day": 60,
        "opportunities": 100,
        "saved_searches": 3,
        "alerts_per_day": 25,
        "price_drop_alerts": False,
        "aggressive_mode": False,
        "profit_scenarios": False,
        "custom_profit_assumptions": True,
        "sms_per_month": 0,
        "webhook": True,
    },
    "pro": {
        "analyses_per_day": 200,
        "opportunities": 1000,
        "saved_searches": 15,
        "alerts_per_day": 100,
        "price_drop_alerts": True,
        "aggressive_mode": True,
        "profit_scenarios": True,
        "custom_profit_assumptions": True,
        "sms_per_month": 30,
        "webhook": True,
    },
}

# Approximate unit costs for usage instrumentation (USD cents). These are
# operator-set assumptions, NOT measured prices — set them from your actual
# provider invoices via env.
def _cents(name: str, default: float) -> float:
    # an empty value (as in deploy/env.staging.example) means "not set";
    # a bad value must not take down every request that imports plans
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


UNIT_COST_CENTS = {
    "sms": _cents("COST_SMS_CENTS", 1.0),
    "push": _cents("COST_PUSH_CENTS", 0),
    "webhook": _cents("COST_WEBHOOK_CENTS", 0),
    "comps_lookup": _cents("COST_COMPS_LOOKUP_CENTS", 0),
}


def _load() -> dict[str, dict]:
    out = deepcopy(_DEFAULTS)
    raw = os.environ.get("PLAN_LIMITS_JSON", "").strip()
    if raw:
        try:
            for plan, over in json.loads(raw).items():
                if plan in out and isinstance(over, dict):
                    out[plan].update({k: v for k, v in over.items() if k in out[plan]})
        except (ValueError, AttributeError):
            pass
    return out


def limits(plan: str | None) -> dict:
    return _load().get(plan or "free", _load()["free"])


def can_receive_alerts(plan: str | None) -> bool:
    return limits(plan)["alerts_per_day"] > 0


def display_name(plan: str | None) -> str:
    return DISPLAY_NAMES.get(plan or "free", (plan or "free").title())


def channel_allowed(plan: str | None, channel: str) -> bool:
    lim = limits(plan)
    if channel == "twilio":
        return lim["sms_per_month"] > 0
    if channel == "webhook":
        return bool(lim["webhook"])
    return can_receive_alerts(plan)


def public_table() -> list[dict]:
    """Plan comparison for the UI / docs."""
    return [{"plan": p, "name": display_name(p), **lim}
            for p, lim in _load().items()]
