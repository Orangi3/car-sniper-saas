# First ten real vehicles — seller pilot playbook

Goal: 10 genuine, seller-authorized cars in Tuscaloosa + 150 mi
($2,000–$15,000), reviewed by a person, then grow to 100. Everything here is
done by you (the owner) by hand. Nothing is sent automatically, and Car
Sniper has no seller partnerships yet — don't imply otherwise.

## Before the first invitation (gates)

- [ ] Release merged and running on an HTTPS host (`OWNER_ACTIONS.md`)
- [ ] SMTP configured (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`,
      `SMTP_PASSWORD`, `SMTP_FROM`) and a test confirmation email received.
      Without it nothing publishes (by design).
- [ ] Short seller terms + privacy notice published (yours to write/approve):
      who sees the contact email (signed-in paid buyers), that listings
      hide after 30 days without confirmation, how to remove a listing,
      that Car Sniper doesn't verify the car
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

> Hi {name} — I'm piloting Car Sniper, a tool that alerts local buyers
> when a car they're looking for comes up for sale. I saw you're selling
> your {car}. Would you like to list it there for free? It takes about two
> minutes: create an account at {link}, confirm your email, and fill in the
> car's details. Buyers on paid plans see your email so they can reach you;
> nobody else does. You can change the price or remove it any time, and it
> hides automatically after 30 days unless you confirm it's still for sale.
> No obligation, and I won't share your details anywhere else.

## Seller onboarding (what they do)

1. Create account (you send the invite link) → sign in at `/m`
2. Setup → "Selling a car?" → fill the form, tick the three confirmations
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

Everything the seller enters is shown as **seller-reported**. Car Sniper
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

## Reviewing the first 10 (no technical skills needed)

Weekly, ask whoever runs the server to produce the sheet:

```
python -m evaluation cohort --source direct --n 100 --snapshot --sheet review.csv
```

Open `review.csv` in a spreadsheet. For each car:

1. Look at the listing on Car Sniper and the estimated value/confidence.
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

Stop and reassess if: a seller's details are used outside Car Sniper, a
listing turns out fraudulent, or buyers report the estimates as
misleading.
