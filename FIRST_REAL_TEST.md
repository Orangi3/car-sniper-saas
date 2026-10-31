# First real test: one buyer, one seller

The first time real people use FlipFirst. One flipper (the buyer) and one
seller who really owns, or is authorized to sell, the car they list. No
paid data is used. Until FlipFirst has at least three comparable listings,
it shows **no market value** and says so; the buyer works from their own
resale estimate, which is labeled as theirs.

Nothing here has happened yet: these are the steps and the pass/fail checks.
The whole flow below, including step 4's plan command verbatim, was
rehearsed on a disposable Ubuntu 24.04 host with SIMULATED people and a
SIMULATED car (`DEPLOY_TEST_REPORT.md`, rehearsal of 2026-10-31).

## Before you start (owner)

- [ ] The release is merged (PRs #11 → #14) and deployed to
      `app.tryflipfirst.com` (`FIRST_DEPLOY.md`).
- [ ] `/etc/sniper/env` on the server (values never in chat or Git):
  - `ALLOW_REGISTRATION=0`
  - `SNIPER_SELLER_SUBMISSIONS=1`
  - `BILLING_ORIGIN=https://app.tryflipfirst.com`
  - Resend's `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and
    `SMTP_FROM=FlipFirst <no-reply@app.tryflipfirst.com>`
  - `HEALTHCHECK_*` (web, pipeline, backup), and the billing reconcile
    timer disabled (`FIRST_DEPLOY.md` step 13)
- [ ] Backups run nightly, and one restore drill has passed
      (`FIRST_DEPLOY.md` step 11).
- [ ] Legal:
  - The privacy policy, beta-tester terms and seller terms are reviewed
    (`legal/`). Running this test with two people you know, before review,
    is your decision.
  - Either way, tell both people in writing that:
    - it's a private test;
    - values are estimates, not appraisals;
    - their data can be deleted on request.
- [ ] The seller agrees to list one car they may sell, with accurate title
      status and known problems. A dealer must show you their business
      (dealer license or registration) before you approve it.

Server commands run as the app user with its environment:

```bash
alias ff='sudo systemd-run --pipe --wait --quiet --collect --uid=sniper -p EnvironmentFile=/etc/sniper/env -p WorkingDirectory=/opt/sniper/current /opt/sniper/.venv/bin/python3'
```

## Steps and pass checks

| # | Who | Do | Passes when |
|---|---|---|---|
| 1 | Owner | `ff -m invites issue --note "seller: NAME" --count 1`, then the same with `"buyer: NAME"`. Send each link to its person yourself | two single-use links; `ff -m invites list` shows both unused |
| 2 | Seller | Open the link, create the account. **Sell** → confirm the contact email → click the emailed link | the email arrives within 2 minutes from `no-reply@app.tryflipfirst.com`; Sell shows the address as confirmed |
| 3 | Seller | **Sell → List a car**: VIN → "Fill in from the VIN" → check the details → title, problems, price, ZIP → the three confirmations → Submit. A dealer, once approved (`ff -m seller_listings approve-dealer EMAIL --name "BUSINESS" --note "what you checked"`), can instead upload the CSV template | the listing shows **Active**. A dealer sees per-row import results |
| 4 | Buyer, then owner | The buyer opens the link, creates the account and sets the buying area (**Account → ZIP and radius**). Then you set the buyer to Lite by hand, at no charge, because seller contact details are shown only on paid plans: `ff -c "import auth; auth.set_plan(auth.get_user_by_email('BUYER_EMAIL').id, 'starter')"` (`'free'` reverses it) | signed in; the area is saved; the plan chip shows **Lite**. Lite also allows saved searches and push alerts, but alerts fire only on cars that have a market value. That needs at least three comparable listings; a one-seller test with no licensed data has none |
| 5 | Buyer | **Discover → FlipFirst sellers** | the seller's car is listed. Its page says the details are seller-reported and shows **"Not enough data to value this car"** — no invented value |
| 6 | Buyer | **Analyze with my numbers** → enter their own resale estimate and costs → **Analyze** | the result shows the identity check, "No market value found … uses your resale estimate", a decision with reasons, break-even resale and maximum offer |
| 7 | Buyer | **Save to my deals**; analyze one more car they're considering (from anywhere) and save it; **Deals → Analyzed** → tick both → **Compare** | both deals are side by side; the notes and stage changes save |
| 8 | Buyer | Email the seller from the deal page about the car (the address appears under the provenance note). Give feedback (questions below) by email or a form you set up | the seller receives the email; you have the answers |
| 9 | Owner | `ff -m pilot_report`; `ff -m seller_listings reports`; **Operations** page; Healthchecks dashboard; `journalctl -u sniper-web --since today \| grep -c ' 5[0-9][0-9] '` | counts match what happened, there are no open reports (unless one was filed), every check is green, and there are 0 server errors |

**Overall pass:**
- every step passes, with no 5xx errors and no emails lost;
- no market value is shown without comparables;
- neither person can see the other's private data. The seller sees no
  analyses; the buyer sees only the listing and the seller's contact email.

**Stop the test if:**
- an email doesn't arrive (check Resend's log);
- the car doesn't appear in step 5. Check: is the seller's email confirmed,
  is the listing Active, does the buyer's radius include the car's ZIP;
- any page errors.

Record what you saw before fixing anything.

## Feedback questions

**Buyer** (after step 8):
1. Was the analysis clear?
2. Did you trust the "no market value" message?
3. Were break-even and maximum offer useful?
4. What did you have to work out yourself?
5. Would you pay $4.99 a month once FlipFirst shows market values?

**Seller:**
1. How long did listing take?
2. Was anything confusing?
3. Did the buyer's email reach you?
4. Would you list again, or list more cars?

## Afterwards

- **Sold or withdrawn:** the seller taps **Remove**. Removal deletes the
  listing and everything derived from it.
- **Deletion requests:** run `ff -m accounts delete EMAIL` (dry run), then
  the same with `--yes`.
- **Next:** if everything passed, invite the next testers (up to 10) the
  same way (`PILOT_TUSCALOOSA.md`).
