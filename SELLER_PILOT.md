# First ten real vehicles — seller pilot playbook

Goal: 10 genuine, seller-authorized cars in Tuscaloosa + 150 mi
($2,000–$15,000), reviewed by a person, then grow to 100. Everything here is
done by you (the owner) by hand. Nothing is sent automatically, and Car
Sniper has no seller partnerships yet — don't imply otherwise.

## Before the first invitation (gates)

- [ ] Release merged and running on an HTTPS host (`OWNER_ACTIONS.md`)
- [ ] Email configured (`FIRST_DEPLOY.md` → Email setup) and a test
      confirmation email received in Gmail and Outlook, not in spam.
      Without it nothing publishes (by design).
- [ ] Short seller terms + privacy notice published (yours to write/approve):
      who sees the contact email (signed-in paid buyers), that listings
      hide after 30 days without confirmation, how to remove a listing,
      that FlipFirst doesn't verify the car
- [ ] `SNIPER_SELLER_SUBMISSIONS=1`, registration invite-only
      (`ALLOW_REGISTRATION=0`, accounts created for invited people)

## Who to invite (in this order)

1. People you know personally who are selling a car now
2. Local flippers who already have 1–5 cars for sale
3. Independent dealers / small lots (each car still needs their consent
   to list; treat them as sellers, marked as such in the description)
4. Small auto businesses (mechanics, detailers) who hear of private sales

Only contact people through channels where they invited contact (a
listing's own contact method, your network). Don't collect or store
contact details beyond what they enter themselves.

## Seller value (one line)

"List your car once, free; local buyers who are searching for exactly that
car get an instant alert with your asking price and a market estimate."

## Invitation (send yourself; edit freely)

> Hi {name} — I'm piloting FlipFirst, a tool that alerts local buyers
> when a car they're looking for comes up for sale. I saw you're selling
> your {car}. Would you like to list it there for free? It takes about two
> minutes: create an account at {link}, confirm your email, and fill in the
> car's details. Buyers on paid plans see your email so they can reach you;
> nobody else does. You can change the price or remove it any time, and it
> hides automatically after 30 days unless you confirm it's still for sale.
> No obligation, and I won't share your details anywhere else.

## Invitation links (you, on the server)

Registration stays closed; each person needs their own single-use link
(expires in 14 days, works once, can be revoked). Issue them as root:

```
systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile=/etc/sniper/env \
    -p WorkingDirectory=/opt/sniper/current \
    /opt/sniper/.venv/bin/python3 -m invites issue --count 1 --note "Pat (seller)"
```

The link is printed once and never stored (only its hash is). Send it to
that one person. `... -m invites list` shows who used which; `... -m invites
revoke <id>` cancels an unused one. Each person picks their own password.

## Seller onboarding (what they do)

1. Create account with the invitation link you send them → sign in at `/m`
2. Sell → "List a car" → five short steps (vehicle, condition & title, price & location,
   contact, review) and the three confirmations
3. "Email me a confirmation link" → click the link → listing goes live
4. Every few weeks: "Still for sale" if it hasn't sold; "Remove" when sold

## Listing quality checklist

| Field | Required | Notes |
|---|---|---|
| Year, make, model | yes | validated |
| Trim | optional | improves comparables |
| Mileage | yes | odometer reading |
| Asking price | yes | $500–$250,000 accepted; pilot band $2k–$15k |
| ZIP where the car is | yes | must be a real US ZIP |
| Title status | yes | clean / rebuilt / salvage / not sure |
| Known mechanical problems | asked | "none known" is allowed |
| Known cosmetic problems | asked | |
| VIN | optional | enables duplicate detection and history checks |
| Contact | yes | verified email; shown only to paid buyers |
| Listing time | automatic | |

Everything the seller enters is shown as **seller-reported**. FlipFirst
verifies the seller controls the email address — not the car, its title, or
its condition.

## Permission, updates, removal

- Permission is the seller's own submission plus three recorded
  confirmations (authorized to sell, details accurate, contact may be shown)
  with a timestamp.
- Price change: seller edits it (price history is kept; buyers with
  price-drop alerts are notified).
- Removal: seller taps Remove (or asks you; admins can remove any listing).
  The listing and everything derived from it is deleted.
- Stale: hidden after 30 days without "Still for sale".
- Abuse or a bad listing: buyers tap **Report this listing** on any FlipFirst
  listing; nothing is hidden automatically.
  - Review open reports: `python -m seller_listings reports`.
  - Close one: `python -m seller_listings resolve ID --note "…"`.
  - Close it and take the listing down: add `--remove --admin YOUR_EMAIL`.
    That deletes the listing and everything derived from it.
  - Repeat offenders: delete the account
    (`python -m accounts delete EMAIL --yes`).
- Dealers and wholesalers: check the business first (dealer license or
  business registration, seen in person), then run
  `python -m seller_listings approve-dealer EMAIL --name "Business" --max-active 50 --note "what you checked"`.
  - Their cars are labeled as dealer listings under that name.
  - They can import a CSV of their own inventory from **Sell**.
  - `revoke-dealer EMAIL` returns them to private-seller limits.

## Reviewing the first 10 (no technical skills needed)

Weekly, ask whoever runs the server to produce the sheet:

```
python -m evaluation cohort --source direct --n 100 --snapshot --sheet review.csv
```

Open `review.csv` in a spreadsheet. For each car:

1. Look at the listing on FlipFirst and the estimated value/confidence.
2. In `outcome_label`, write one of: `genuine_opportunity`,
   `weak_opportunity`, `bad_comparison`, `mechanical_condition_problem`,
   `suspicious_listing`, `duplicate`, `listing_unavailable`,
   `insufficient_evidence`. If a car actually sells and you learn the price,
   note it in `notes` (only `purchased_profit_verified` needs proof).
3. Put your name in `labeled_by`. Save. Then run
   `python -m evaluation import review.csv`.

Columns, by where the information comes from:

| Seller-reported | System-estimated | Manually reviewed | Verified outcome |
|---|---|---|---|
| year, make, model, trim, mileage, asking price, ZIP, title, known issues | comparables, est. value, confidence, est. profit (conservative) | outcome_label, labeled_by, notes | sale amount / realized profit — only with evidence |

Estimated profit is never realized profit.

## Buyer pilot (up to 10 buyers, after the first listings are live)

Don't charge yet. Invite buyers who actually buy cars in the territory.

| Metric | How measured |
|---|---|
| Completed onboarding | account + territory + saved search |
| Saved search created | `saved_searches` |
| Relevant alerts delivered | ops view → notifications delivered |
| Alerts opened / sellers contacted | ask buyers (not measurable in-app yet) |
| Deals reviewed | evaluation cohort |
| Usefulness, willingness to pay | a short conversation with each buyer at 2 and 4 weeks |

Stop and reassess if: a seller's details are used outside FlipFirst, a
listing turns out fraudulent, or buyers report the estimates as
misleading.

Record buyer feedback in a simple sheet you keep yourself (one row per
conversation): date, buyer, which alert or listing, "was this relevant?"
(yes/no/why), "did you contact the seller?", "would you pay, and how
much?". Copy anything about a specific car into that car's `notes` in
`review.csv`.

## First 14 days after go-live (targets, not results)

| Days | Target | How you know |
|---|---|---|
| 1–2 | Site on HTTPS, email arrives, one backup + one restore drill done, Healthchecks green | `FIRST_DEPLOY.md` steps 8–13 |
| 3–5 | 3 real seller listings verified and live; 1 alert reaches a real buyer's phone | ops view; ask the buyer |
| 6–10 | 10 real listings; each reviewed by you in `review.csv` | `python -m evaluation cohort --source direct --sheet review.csv` |
| 11–14 | Feedback from every pilot buyer; first cohort review imported | your feedback sheet; `evaluation import` |

What to measure, and what each threshold is based on:

- **Listing completeness**: every required field present (the form
  enforces this), plus VIN or trim on at least half. Without trim or VIN,
  comparables are coarser, so many missing would explain weak estimates.
- **Comparable coverage**: share of listings whose estimate is not
  `insufficient` (fewer than 3 usable comparables). There is no target
  yet: it depends on how much licensed market data is connected, and
  without a data source most seller cars will be `insufficient`. That is
  the honest result, not a bug.
- **Alert relevance**: buyer says "relevant" for an alert. Ask after
  every alert during the pilot.
- **False positives**: reviewed listings labeled `bad_comparison`,
  `mechanical_condition_problem` or `suspicious_listing` among those that
  alerted.
- **Seller contact success**: buyers who tried to reach a seller and got
  an answer.
- **Buyer engagement**: buyers who open the app at least weekly and keep
  their saved search.
- **Uptime / alerts**: Healthchecks shows no missed ping lasting more
  than an hour.
- **Support burden**: minutes per week you spend helping sellers or
  buyers. Above about 2 hours a week, the onboarding needs fixing before
  you invite more people.

Too early to judge with 10 cars: false-positive *rates*, valuation
accuracy, and willingness to pay. With fewer than 20 reviewed listings
(`MIN_N` in `evaluation.py`) any percentage swings by 5+ points per car.
Record the raw counts; draw conclusions at 30+ listings.
