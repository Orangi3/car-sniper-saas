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

## Pilot operations

**Onboarding (per tester, about 5 minutes of your time)**
1. Send the reviewed tester terms and privacy policy (`legal/`); wait for
   "I agree".
2. Issue one invitation link per tester on the server (`COMPLIANCE.md`
   shows the `systemd-run` prefix):
   `python -m invites issue --note "pilot: <first name>" --count 1 --days 14`.
   Links are single-use and shown once — send each to its person yourself.
3. The tester opens the link, sets a password, then **Account → Buying
   area**: ZIP (e.g. 35401) and radius (150). The deal feeds then show only
   their territory.
4. Testers start on Free: trusted and risk feeds, deal pages, save /
   contacted / passed stages. Saved searches and alerts wait for Gate 2.
   Before any licensed data exists, the first real test
   (`FIRST_REAL_TEST.md`) uses only FlipFirst seller listings. Its one
   buyer is set to Lite by hand, because only paid plans show a seller's
   contact email; with no market values, no alert can fire.

**Feedback and false-positive reports**
- In the app: deal stages (above) — counted weekly by `pilot_report`.
- Weekly form (any free form tool), five questions: deals you contacted;
  any that weren't what they looked like and why (wrong comparison, damage,
  title, already sold, scam, too far); anything missing; time saved; "would
  you pay $4.99/month for this week's results?" (yes / no / at what price).
- Anything urgent — a scam, a wrong value, a listing that should come down —
  by email to you; remove a seller listing as admin if needed.

**Your weekly review (about an hour)**
1. `python -m pilot_report --days 7` — sign-ins, deal actions, alert
   delivery, supply, seller listings.
2. Label every trusted candidate from the week (harness review sheet or
   `python -m evaluation cohort --sheet review.csv`, then `import`); copy
   tester reasons into `notes`.
3. `python -m validate_deals report <run dir>` or `python -m evaluation
   report` for precision and false-positive reasons.
4. Any reason seen twice becomes a fix with a regression test.

**Support:** one email address for testers, answered within one business
day; account deletion on request with `python -m accounts delete EMAIL`
(dry run first, then `--yes`).

## Pilot success criteria (proposed — confirm before the pilot starts)

| Measure | Target over the pilot | Source |
|---|---|---|
| Verified real listings | ≥ 1 licensed source exits 0 in `validate_deals`; in-band listings counted weekly | harness, `pilot_report` |
| Useful opportunities | ≥ 5 trusted candidates a week | `pilot_report` |
| Valuation accuracy | precision lower bound ≥ 40% at Gate 1 and ≥ 50% at Gate 2 | `EVALUATION.md` gates |
| False-positive rate | no single reason ≥ 25% of judged candidates without a fix | review labels |
| Alert reliability (after Gate 2) | delivery rate ≥ 95% | `pilot_report` |
| Engagement | ≥ 6 of 10 testers sign in each week; ≥ 1 contacted car per tester | `pilot_report` |
| Willingness to pay | ≥ 4 of 10 say they'd pay ≥ $4.99/month | weekly form |

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
