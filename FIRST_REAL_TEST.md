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

- [ ] The release is merged (PRs #11 → #15) and deployed to
      `app.tryflipfirst.com` (`FIRST_DEPLOY.md`).
- [ ] `sudo bash /opt/sniper/current/deploy/golive.sh --test-email YOUR_EMAIL`
      passed every gate, and the test email arrived. It checks the pilot
      settings, HTTPS, the firewall, one backup and the health probes:
  - pilot settings: invitation-only, seller listings on, Resend, backups,
    monitoring, no billing.
- [ ] One restore drill has passed on your computer (`FIRST_DEPLOY.md`).
- [ ] Legal:
  - The privacy policy, beta-tester terms and seller terms are reviewed
    (`legal/`). Running this test with two people you know, before review,
    is your decision.
  - The app doesn't show these documents yet. Email each person the ones
    that apply and keep their written "I agree":
    - seller: seller terms and privacy policy;
    - buyer: tester terms and privacy policy.
  - Tell both people in writing that:
    - it's a private test;
    - values are estimates, not appraisals;
    - their data can be deleted on request;
    - the seller's confirmed email is shown to the invited buyer.
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
| 8 | Seller | **Sell → Change price** on the listing, once | the buyer's deal page shows the new price |
| 9 | Buyer | On the car's deal page set the stage to **Contacted**, then email the seller (the address appears under the provenance note). Give feedback (questions below) by email or a form you set up | the seller receives the email; you have the answers |
| 10 | Owner | `ff -m pilot_report`; `ff -m seller_listings reports`; **Operations** page; Healthchecks dashboard; Resend's log; `journalctl -u sniper-web --since today \| grep -c ' 5[0-9][0-9] '`; `free -m; df -h /` | `pilot_report` shows 1 live seller listing, analyses, 2 saved deals and 1 buyer contact; no open reports (unless one was filed); every check green; Resend shows each email delivered; 0 server errors; memory and disk well below the limits |

**Overall pass:**
- every step passes, with no 5xx errors and no emails lost;
- no market value is shown without comparables;
- neither person can see the other's private data. The seller sees no
  analyses; the buyer sees only the listing and the seller's contact email.

A pass means the flow worked for real people; it is not a sale. Count only
the seller's real car (the server holds nothing from the rehearsals). Record
a sale only if the buyer says they bought it, as their own record, which the
app labels "recorded by you, not verified".

**Stop the test if:**
- an email doesn't arrive (check Resend's log);
- the car doesn't appear in step 5. Check: is the seller's email confirmed,
  is the listing Active, does the buyer's radius include the car's ZIP;
- any page errors.

Record what you saw before fixing anything.

## Invitation messages (you send them, after approving the test)

Send each with its own link from step 1. Nothing is sent for you.

> **Seller:** Thanks for helping test FlipFirst. It's a private test with
> one buyer. Here's your invitation link: LINK. It works once. Please list
> one car you're allowed to sell, with accurate title status and known
> problems. Your email address is shown only to the invited buyer. Reply
> "I agree" to the attached seller terms and privacy policy before you
> start, and tell me anytime if you want your data deleted.

> **Buyer:** Thanks for helping test FlipFirst. It's a private test, and
> values are estimates, not appraisals. Here's your invitation link: LINK.
> It works once. Please reply "I agree" to the attached tester terms and
> privacy policy before you start. After you sign up, I'll turn on the
> features you need, then you can try the seller's car and one of your own.

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
