"""
economics/model.py — reproducible unit-economics model for Car Sniper.

    python economics/model.py > economics/tables.md

Every input is tagged SOURCED (vendor page or third-party pricing summary,
retrieved 2026-10-28 by web search — verify before relying on it) or
ASSUMPTION (a modeling choice). Data costs depend on how many territories we
poll and how often — NOT on subscriber count — because ingestion is central
and matching is shared.
"""
from __future__ import annotations

import math

# ---------------- SOURCED (verify) ----------------
STRIPE_PCT, STRIPE_FIXED = 0.029, 0.30          # US card rate
STRIPE_BILLING_PCT = 0.007                      # Stripe Billing (pay as you go)
MC_BASIC_MONTHLY, MC_BASIC_CALLS = 299.0, 5000  # MarketCheck Basic
MC_STANDARD_MONTHLY = 749.0                     # MarketCheck Standard (unlimited calls)
MC_FSBO_PER_CALL = 0.01                         # Private Party Search data fee
SMS_PER_MSG = (0.011, 0.012, 0.013)             # all-in per segment (optimistic, base, conservative)
SMS_CAMPAIGN_MONTHLY = (1.50, 10.0, 10.0)       # 10DLC campaign (range reported)
DROPLET_4GB = 24.0
MANAGED_PG = (0.0, 15.0, 30.0)                  # self-hosted / single node / with standby

# ---------------- ASSUMPTIONS ----------------
CASES = ("optimistic", "base", "conservative")
# Data-plan scenarios
DATA = {
    # optimistic: MarketCheck grants a pilot/startup price (UNCONFIRMED)
    "optimistic": {"platform_fee": 149.0, "poll_minutes": 30, "subs_per_territory": 250},
    "base": {"platform_fee": MC_BASIC_MONTHLY, "poll_minutes": 30, "subs_per_territory": 150},
    "conservative": {"platform_fee": MC_BASIC_MONTHLY, "poll_minutes": 15, "subs_per_territory": 100},
}
HOSTING = {  # app VPS + Postgres + backups/monitoring; grows with load
    "optimistic": lambda n: DROPLET_4GB + MANAGED_PG[0] + 3 + 24 * (n // 5000),
    "base": lambda n: DROPLET_4GB + MANAGED_PG[1] + 5 + 24 * (n // 3000),
    "conservative": lambda n: DROPLET_4GB + MANAGED_PG[2] + 15 + 48 * (n // 2000),
}
REFUND_LOSS = {"optimistic": 0.015, "base": 0.03, "conservative": 0.05}
SUPPORT = {  # cash tools + per-subscriber support time valued in cash
    "optimistic": (0.0, 0.02), "base": (10.0, 0.05), "conservative": (25.0, 0.15)}
# Engineering operations: AI coding tools / agent API spend for maintenance
# and features (owner's choice of plan; spend-capped) and monitoring/alerting.
AI_DEV = {"optimistic": 20.0, "base": 100.0, "conservative": 200.0}
MONITORING = {"optimistic": 0.0, "base": 10.0, "conservative": 30.0}
SMS_OPT_IN = {"optimistic": 0.10, "base": 0.20, "conservative": 0.40}   # share of Pro users
SMS_PER_USER = {"optimistic": 8, "base": 15, "conservative": 30}        # Pro cap = 30
PRO_SHARE = 0.6                                                          # mixed-tier case
LITE, PRO = 2.99, 4.99
SUBS = (25, 50, 100, 250, 500, 1000, 5000, 10000)


def data_cost(n: int, case: str, platform_fee: float | None = None,
              per_call: float | None = None) -> dict:
    d = DATA[case]
    fee = d["platform_fee"] if platform_fee is None else platform_fee
    pc = MC_FSBO_PER_CALL if per_call is None else per_call
    territories = max(1, math.ceil(n / d["subs_per_territory"]))
    calls = territories * (30 * 24 * 60 // d["poll_minutes"])
    plan = fee if calls <= MC_BASIC_CALLS else max(fee, MC_STANDARD_MONTHLY * fee / MC_BASIC_MONTHLY)
    return {"territories": territories, "calls": calls,
            "fixed": plan, "fees": calls * pc}


def scenario(n: int, case: str, price_mix: str, **data_kw) -> dict:
    if price_mix == "lite":
        arpu_list = [(LITE, n)]
    elif price_mix == "pro":
        arpu_list = [(PRO, n)]
    else:
        pro = round(n * PRO_SHARE)
        arpu_list = [(PRO, pro), (LITE, n - pro)]
    revenue = sum(p * k for p, k in arpu_list)
    payments = sum((p * (STRIPE_PCT + STRIPE_BILLING_PCT) + STRIPE_FIXED) * k for p, k in arpu_list)
    refunds = revenue * REFUND_LOSS[case]
    i = CASES.index(case)
    pro_users = sum(k for p, k in arpu_list if p == PRO)
    sms = pro_users * SMS_OPT_IN[case] * SMS_PER_USER[case] * SMS_PER_MSG[i]
    sup_fixed, sup_per = SUPPORT[case]
    dc = data_cost(n, case, **data_kw)
    eng = AI_DEV[case] + MONITORING[case]
    fixed = dc["fixed"] + dc["fees"] + HOSTING[case](n) + sup_fixed + eng + (
        SMS_CAMPAIGN_MONTHLY[i] if pro_users else 0)
    variable = sms + sup_per * n
    contribution = revenue - payments - refunds - variable
    return {"n": n, "revenue": revenue, "payments": payments, "refunds": refunds,
            "variable": variable, "fixed": fixed, "contribution": contribution,
            "profit": contribution - fixed, "data": dc, "engineering": eng,
            "total_cost": payments + refunds + variable + fixed}


def breakeven(case: str, mix: str, **data_kw) -> int | None:
    for n in range(1, 50001):
        if scenario(n, case, mix, **data_kw)["profit"] >= 0:
            return n
    return None


def money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def render() -> str:
    out = []
    for mix, label in (("lite", "All Lite @ $2.99"), ("pro", "All Pro @ $4.99"),
                       ("mixed", f"Mixed: {int(PRO_SHARE*100)}% Pro / {100-int(PRO_SHARE*100)}% Lite")):
        out.append(f"### {label}\n")
        for case in CASES:
            be = breakeven(case, mix)
            out.append(f"**{case.title()} case** — cash break-even: "
                       f"**{be:,} subscribers**\n" if be else
                       f"**{case.title()} case** — no break-even below 50,000\n")
            out.append("| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | "
                       "Contribution | Fixed ops (data+host+tools) | Operating result | "
                       "Data: territories / calls |")
            out.append("|---:|---:|---:|---:|---:|---:|---:|---:|---|")
            for n in SUBS:
                s = scenario(n, case, mix)
                d = s["data"]
                out.append(f"| {n:,} | {money(s['revenue'])} | {money(s['payments'])} | "
                           f"{money(s['refunds'])} | {money(s['variable'])} | "
                           f"{money(s['contribution'])} | {money(s['fixed'])} | "
                           f"**{money(s['profit'])}** | {d['territories']} / {d['calls']:,} |")
            out.append("")
    out.append("### Cost per active customer (mixed tiers)\n")
    out.append("All monthly costs (data, hosting, engineering, support, SMS, payment "
               "fees, refunds) divided by subscribers; ARPU is the 60/40 mix "
               f"(${PRO_SHARE * PRO + (1 - PRO_SHARE) * LITE:.2f}).\n")
    out.append("| Subs | " + " | ".join(c.title() for c in CASES) + " |")
    out.append("|---:|" + "---:|" * len(CASES))
    for n in SUBS[:6]:
        out.append(f"| {n:,} | " + " | ".join(
            f"${scenario(n, c, 'mixed')['total_cost'] / n:,.2f}" for c in CASES) + " |")
    out.append("")
    out.append("### Cost per territory (data only)\n")
    out.append("Marginal provider fees per extra territory, before any plan "
               f"change (Basic covers {MC_BASIC_CALLS:,} calls/month).\n")
    out.append("| Polling | Calls / month | Fees @ $0.01 | Fees @ $0.02 | Territories on Basic |")
    out.append("|---|---:|---:|---:|---:|")
    for m in (60, 30, 15):
        calls = 30 * 24 * 60 // m
        out.append(f"| every {m} min | {calls:,} | ${calls * 0.01:,.2f} | "
                   f"${calls * 0.02:,.2f} | {MC_BASIC_CALLS // calls} |")
    out.append("")
    out.append("### Sensitivity to data pricing (base case, mixed tiers)\n")
    out.append("Cash break-even subscribers by platform fee (rows) and per-call "
               "fee (columns). Higher tiers scale proportionally.\n")
    fees, pcs = (99.0, 149.0, 299.0, 499.0, 749.0), (0.005, 0.01, 0.02, 0.03)
    out.append("| Platform fee | " + " | ".join(f"${p:.3f}/call" for p in pcs) + " |")
    out.append("|---:|" + "---:|" * len(pcs))
    for f in fees:
        cells = []
        for pc in pcs:
            be = breakeven("base", "mixed", platform_fee=f, per_call=pc)
            cells.append(f"{be:,}" if be else "none")
        out.append(f"| ${f:,.0f}/mo | " + " | ".join(cells) + " |")
    out.append("")
    per = lambda p: p * (STRIPE_PCT + STRIPE_BILLING_PCT) + STRIPE_FIXED
    out.append("### Payment fee per charge\n")
    out.append("| Price | Fee | Share of price |\n|---:|---:|---:|")
    for p in (LITE, PRO, 29.99, 49.99):
        out.append(f"| ${p:.2f} | ${per(p):.3f} | {per(p)/p:.1%} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(render())
