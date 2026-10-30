# Tuscaloosa pilot plan

Free, invite-only pilot: **10 testers**, home ZIP **35401**, **150-mile**
radius, **$2,000–$15,000**. Goal: learn whether FlipFirst finds cars worth
contacting in real local data — not revenue. Every threshold here is a
proposal for the owner to confirm; no pilot data exists yet.

## Territory

A 150-mile radius around 35401 (straight-line from ZIP centroids; driving
distances are longer, and eBay applies its own distance) takes in:

| Inside | Outside |
|---|---|
| Hoover / Birmingham (~47–49 mi), Columbus MS (56), Selma (62), Starkville (75), Meridian (86), Montgomery (92), Tupelo (99), Decatur (102), Anniston (104), Gadsden (111), Florence (113), Huntsville (120), Auburn (130) | Jackson MS and Hattiesburg (165), Mobile (176), Chattanooga (181), Atlanta (187), Dothan (191) |

Birmingham and Montgomery are inside, so most supply will come from two
metros 50–90 miles away. A tester in Tuscaloosa should expect most
candidates to be an hour or more of driving; the profit estimate already
charges round-trip transport by distance.

## Sources

| Source | Pilot role | Status | Before the pilot |
|---|---|---|---|
| eBay Motors (Browse API) | listings, if eBay grants production access **and** written permission for price estimates | REQUIRES COMMERCIAL AUTHORIZATION — its licence bars modeling prices of eBay items | `EBAY_SETUP.md` steps 1–9 |
| FlipFirst seller listings | real local cars testers and their contacts post | IMPLEMENTED — NOT LIVE-VERIFIED | email setup; `SNIPER_SELLER_SUBMISSIONS=1` (`SELLER_PILOT.md`) |
| MarketCheck private sellers (+ Price API for values) | most likely licensed source of both listings and values | REQUIRES COMMERCIAL AUTHORIZATION + ≥ $299/mo list price (no quote yet) | written agreement and quote; the Free tier caps the radius at 100 mi, so 150 mi could need a paid plan or two queries (e.g. 35401 and Birmingham) |
| Craigslist, Facebook Marketplace, OfferUp | none | not licensed | stay off |

## Likely volume

**Unknown, and not estimated here.** Neither eBay nor MarketCheck publishes
local inventory counts (ask MarketCheck — inquiry question 5), and no request
with real credentials has ever been made. Week 0
measures it: the first real `validate_deals` run reports in-band listings,
how many could be valued, and how many became trusted candidates. Decide the
go/no-go from those counts against the targets in `EVALUATION.md` (≥ 5
trusted candidates a week for 10 testers). If the first source falls short,
the choices are: widen the band or radius, add the other source, or grow
seller listings.

## Missing data (and what it does to the pilot)

| Gap | Effect | Mitigation now |
|---|---|---|
| eBay summaries carry no description | damage and title problems are not screened | `description not checked` label on every such deal; testers read the listing first |
| Mileage only when the title states it | weaker comparisons, lower confidence | `mileage unknown` flag; confidence reduced |
| Trim, cab, drivetrain only from the title | wrong comparables (e.g. work truck vs Lariat) | capped at low confidence when most comparables state another trim; duty class and Wrangler Unlimited split |
| No VIN or title brand from eBay summaries | salvage cars can look clean | branded-title wording is caught when present; VIN history is a later add-on |
| No completed-sale prices (commercial mode) | values are from asking prices, labeled as such, range widened | every value says "asking prices", never "sold" |
| Seller type absent on eBay US | dealers can't be told apart | `seller type unknown` flag; dealer wording still detected |
| eBay photos are not stored | deal cards show no photo for eBay cars | the eBay link shows the photos; mapping images needs the agreement's display terms first |
| Auctions show the current bid | not a buyable price | flagged `Auction bid`, kept out of the trusted feed; ended auctions hidden |

## Tester feedback

- **In the app:** each deal's stage — saved, contacted, negotiating, going
  to see, bought, passed. Stage counts are the engagement signal.
- **Weekly (5 minutes, any form tool):** Which deals did you contact? Was
  any not what it looked like, and why (wrong comparison, damage, already
  sold, scam, too far)? Did anything you'd have wanted never show up? Would
  you have paid for this week?
- **Owner review:** every trusted candidate gets an outcome label in the
  harness review sheet (`EVALUATION.md` § Real-deal validation); tester
  answers go in `notes`. A false-positive pattern seen twice becomes a
  regression test and a fix, as in `tests/test_false_positives.py`.
- **Bought cars:** record purchase price, costs and resale with
  `python -m evaluation label … purchased_profit_verified` — the only
  realized-profit evidence the pilot can produce.

## Must work before inviting testers

- [ ] A licensed source approved in writing (MarketCheck agreement, or eBay
      production access + price-estimate permission); a real
      `validate_deals` run exits 0 (Gate 0, `FIRST_LIVE_TEST.md`)
- [ ] Gate 1 in `EVALUATION.md`: 7 days of healthy polling, trusted
      candidates reviewed with precision lower bound ≥ 40%
- [ ] Deployment per `FIRST_DEPLOY.md`: HTTPS, `ALLOW_REGISTRATION=0`,
      `SNIPER_COMMERCIAL_MODE=1`, `SNIPER_TRUSTED_PROXIES` set, backups with
      one restore drill, Healthchecks alerting, worker and scheduler running
- [ ] `python -m preflight --target free` passes on the server
- [ ] Each tester's home ZIP and radius set at onboarding; deal feed shows
      only their territory
- [ ] Customer alerts stay **off** until Gate 2; testers browse the feed.
      Invited accounts start on Free, which cannot create saved searches or
      alert channels, so this holds by construction. At Gate 2, set each
      tester to Lite by hand (`COMPLIANCE.md` → C11); with no Stripe
      subscription behind it, billing reconciliation leaves it alone
      (`tests/test_pilot_access.py`)
- [ ] A short pilot note for testers: free, estimates not promises, values
      come from asking prices, always inspect the car and title, how to
      give feedback, how to leave
- [ ] Invites: `python -m invites issue --note "pilot" --count 10 --days 14`
      (one link per tester; links are single-use)

## Timeline (calendar time depends on eBay's approval)

| Week | What happens |
|---|---|
| 0 | Sandbox check; eBay application; server up (`FIRST_DEPLOY.md`) |
| after approval, +0 | First real harness run; review; fix what it shows |
| +1 | 7 days of polling, Gate 1 review |
| +2 – +3 | 10 testers, feed only; weekly review and fixes |
| +4 | Gate 2 decision: alerts on, extend, or stop |
