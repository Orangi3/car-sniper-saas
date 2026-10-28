"""
economics/provider_matrix.py — reproducible ranking of listing-data providers.

    python economics/provider_matrix.py

Scores are 0–5 per criterion. Each score is tagged:
  V  verified from the provider's published docs/terms (via search excerpts,
     2026-10-28) or from this codebase
  E  estimated / judgment — not confirmed with the provider
A score is evidence-weighted judgment, not a measurement. Change scores
here (with a reason), not in the markdown.
"""
from __future__ import annotations

CRITERIA = {  # name: weight (sums to 100)
    "private_party_coverage": 20,     # the product's niche is private sellers
    "commercial_rights": 20,          # may we store + alert paying users?
    "freshness": 10,
    "geo_filtering": 8,
    "field_richness": 8,
    "cost": 10,
    "quota_scalability": 6,
    "contract_effort": 6,             # 5 = click-through, 0 = bespoke negotiation
    "integration_status": 6,          # in this codebase
    "verification": 6,                # how much is confirmed live
}

# (score, tag, note)
P = {
    "MarketCheck FSBO": {
        "private_party_coverage": (4, "V", "FSBO endpoint, ~80k active listings nationwide (vendor)"),
        "commercial_rights": (2, "E", "public ToS restricts persistent storage; needs written agreement"),
        "freshness": (3, "E", "docs say near real-time; older article says 24h"),
        "geo_filtering": (5, "V", "zip + radius"),
        "field_richness": (4, "V", "price, YMM, miles, location, source URL; no seller contact"),
        "cost": (2, "V", "$299/mo + $0.01/call"),
        "quota_scalability": (3, "V", "5k calls/mo on Basic; Standard $749"),
        "contract_effort": (2, "E", "order form + written permission"),
        "integration_status": (4, "V", "adapter + schema check + probe built; never run live"),
        "verification": (1, "V", "no live response seen"),
    },
    "eBay Browse API": {
        "private_party_coverage": (2, "E", "some private sellers; auctions/BIN, not classifieds"),
        "commercial_rights": (4, "V", "API License Agreement: display w/ freshness ≤6h, delete ≤30d"),
        "freshness": (4, "V", "live API"),
        "geo_filtering": (4, "V", "pickupPostalCode/pickupRadius filters (documented)"),
        "field_richness": (3, "V", "title, price, location, seller account type; YMM parsed"),
        "cost": (5, "V", "free"),
        "quota_scalability": (4, "E", "default call limits; sufficient for central polling"),
        "contract_effort": (5, "V", "click-through developer agreement"),
        "integration_status": (5, "V", "adapter in production code; geo fixed; probe built"),
        "verification": (1, "V", "not yet called with real keys"),
    },
    "Vehicle Acquisition Network": {
        "private_party_coverage": (3, "E", "claims FSBO from 25+ marketplaces"),
        "commercial_rights": (1, "E", "unknown; provenance of FSBO data must be checked"),
        "freshness": (2, "E", "unknown"),
        "geo_filtering": (2, "E", "unknown"),
        "field_richness": (2, "E", "unknown"),
        "cost": (2, "E", "not public"),
        "quota_scalability": (2, "E", "unknown"),
        "contract_effort": (1, "E", "sales contract"),
        "integration_status": (0, "V", "none"),
        "verification": (0, "V", "directory listing only"),
    },
    "MarketCheck dealer inventory": {
        "private_party_coverage": (0, "V", "dealers only — comparables, not deals"),
        "commercial_rights": (2, "E", "same ToS as FSBO"),
        "freshness": (3, "E", "as FSBO"),
        "geo_filtering": (5, "V", "zip + radius"),
        "field_richness": (5, "V", "full dealer listing"),
        "cost": (3, "V", "$0.002/call + plan"),
        "quota_scalability": (3, "V", "as FSBO"),
        "contract_effort": (2, "E", "as FSBO"),
        "integration_status": (3, "V", "existing source module"),
        "verification": (1, "V", "not live-verified"),
    },
    "Seller-submitted listings": {
        "private_party_coverage": (1, "E", "cold start: only what we attract"),
        "commercial_rights": (5, "E", "our own terms with seller consent"),
        "freshness": (5, "E", "instant"),
        "geo_filtering": (5, "E", "we collect location"),
        "field_richness": (3, "E", "whatever the form asks"),
        "cost": (4, "E", "our build cost only"),
        "quota_scalability": (5, "E", "no quota"),
        "contract_effort": (4, "E", "seller terms + privacy policy"),
        "integration_status": (1, "E", "not built"),
        "verification": (0, "V", "n/a"),
    },
    "Craigslist (unlicensed)": {
        "private_party_coverage": (5, "E", "largest local private supply"),
        "commercial_rights": (0, "V", "terms prohibit automated collection; enforced"),
        "freshness": (5, "E", "real time"),
        "geo_filtering": (4, "E", "by site/region"),
        "field_richness": (3, "E", "varies"),
        "cost": (5, "E", "free"),
        "quota_scalability": (1, "E", "blocked/throttled"),
        "contract_effort": (0, "E", "no license program known"),
        "integration_status": (4, "V", "source exists; disabled in commercial mode"),
        "verification": (2, "V", "worked historically; blocked from this sandbox"),
    },
}

# Without commercial rights a provider cannot be used for a paid product:
# >= 3 usable; 2 only after a written agreement; < 2 excluded.
GATE = "commercial_rights"


def status(name: str) -> str:
    r = P[name][GATE][0]
    return "yes" if r >= 3 else "only after written agreement" if r == 2 else "no — rights gate"


def score(name: str) -> float:
    return sum(CRITERIA[c] * P[name][c][0] / 5 for c in CRITERIA)


def ranked() -> list[tuple[str, float, bool]]:
    """(name, score, usable_at_all) — excluded providers rank last."""
    rows = [(n, round(score(n), 1), P[n][GATE][0] >= 2) for n in P]
    return sorted(rows, key=lambda r: (not r[2], -r[1]))


def render() -> str:
    out = ["| Rank | Provider | Weighted score /100 | Usable for paid launch? | "
           + " | ".join(f"{c} ({w})" for c, w in CRITERIA.items()) + " |",
           "|---:|---|---:|---|" + "---|" * len(CRITERIA)]
    for i, (n, s, ok) in enumerate(ranked(), 1):
        cells = " | ".join(f"{P[n][c][0]}{P[n][c][1]}" for c in CRITERIA)
        out.append(f"| {i} | {n} | {s} | {status(n)} | {cells} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    print(render())
