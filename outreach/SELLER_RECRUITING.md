# Recruiting the first 10–25 sellers (playbook and drafts, NOT SENT)

Status: **draft for the owner. Nothing here has been sent**, and no seller
has been contacted. Each message needs the owner's approval before use. The
seller terms in `legal/SELLER_TERMS.md` need legal review before any seller
is onboarded.

## What FlipFirst can honestly offer a seller today

- Their cars shown to FlipFirst's invited buyers. In the pilot these are
  independent car flippers around Tuscaloosa who buy $2,000–$15,000 cars,
  usually for cash. Say how many buyers there actually are when you call;
  never round up.
- Free during the pilot: no listing fee and no commission. FlipFirst never
  handles the money; buyers email the seller directly.
- The seller stays in control:
  - they can change a price or remove a car at any time;
  - listings hide after 30 days unless the seller confirms the car is still
    for sale;
  - dealers can upload their whole inventory as a CSV and re-upload it to
    keep it current.
- What FlipFirst does **not** promise: a number of views, inquiries or sales.
  It also doesn't promise exclusivity or "early access", except where a
  seller has agreed in writing to show a car on FlipFirst before listing it
  elsewhere. Never suggest scarcity that doesn't exist.

## Who to approach first (in order)

| Segment | Why they might list | Where to find them |
|---|---|---|
| Small independent used-car lots (Tuscaloosa, Northport, Cottondale) | older trade-ins and high-mileage cars they would rather wholesale than retail | drive the lots and use public business listings; visit in person |
| Wholesalers who buy at dealer auctions | they need fast, cash, as-is buyers | ask the small lots who they wholesale to |
| Independent repair shops | project cars, cars customers abandoned or sold cheap after a big repair quote | ask your own mechanic; local shop listings |
| Buy-here-pay-here lots | repossessions and trade-ins they don't want to finance | public business listings |
| Private sellers who want a quick, as-is sale | flippers are their natural buyers | testers' networks and word of mouth (no ads during the pilot) |

Build the list by hand from public business information. Do not scrape
marketplace listings to find sellers. Do not contact people who posted cars
on other platforms through those platforms' messaging for this purpose.

## The process, with what to measure at each step

| Step | What happens | How it's measured |
|---|---|---|
| 1. Contact | visit or call (best for small lots), then send the email below | your tracker: contacted, date, outcome |
| 2. Authorization | they agree to the seller terms. For a dealer, check their dealer license or business registration in person, then approve the account | `python -m seller_listings approve-dealer EMAIL --name "…" --note "license seen"`; `pilot_report` → `sellers.approved_dealers` |
| 3. Submission | private sellers use the Sell form (VIN decode fills the details); dealers upload the CSV template | `pilot_report` → `sellers.new_listings`, `accounts_with_live_listings` |
| 4. Shown to buyers | listings go live once the seller confirms their email; they're valued and matched like any other car | live listings by source in `pilot_report` → `supply` |
| 5. Buyer inquiries | buyers email the seller directly and mark the deal "Contacted" | buyers' "Contacted" actions on FlipFirst listings (weekly review); ask sellers how many emails they got |
| 6. Feedback | 2-week check-in (questions below) | your notes |
| 7. Repeat | dealers re-upload weekly; private sellers confirm or remove | renewals and re-imports; removed-as-sold count |

Problems: buyer reports arrive in `python -m seller_listings reports`. Resolve
each within one business day (`resolve ID`, adding `--remove --admin EMAIL`
to take a listing down).

Pilot target (proposed — confirm it): 25 sellers contacted → 10 onboarded →
5 with at least three live cars → 30 live original listings, all within six
weeks. Report the actual numbers, not the target.

## Tracker columns (keep it in your own spreadsheet)

`business, contact person, segment, how found, date contacted, channel,
outcome, terms accepted (date), dealer check (what you saw), approved (date),
live listings, last check-in, notes`

## Drafts

### 1. Email after a visit or call (dealers and wholesalers)

> Subject: Listing your trade-ins on FlipFirst (free pilot)
>
> Hi [name],
>
> Thanks for talking with me [today / on Tuesday]. As promised, here's how
> FlipFirst works. It's a new tool for independent car flippers around
> Tuscaloosa. Right now [N] local buyers use it in a free private pilot,
> mostly buying $2,000–$15,000 cars for cash.
>
> You can list the cars you'd rather not retail, one by one or as a CSV
> upload of your inventory. Buyers email you directly. There's no fee, and
> you can change prices or remove cars at any time.
>
> If you'd like to try it, reply and I'll send your sign-up link and a
> one-page CSV template. I'll check in after two weeks to hear whether the
> inquiries were worth your time.
>
> [owner name] · FlipFirst · [phone] · [email]

### 2. Phone or visit opener (30 seconds)

> "I run FlipFirst, a tool for local car flippers — people who buy
> $2–15k cars with cash and fix them up. We're in a small free pilot with
> [N] buyers around Tuscaloosa. Do you get trade-ins you'd rather not put on
> your lot? You could list them with us for free, and buyers contact you
> directly. Can I send you the details?"

### 3. Onboarding email (after they say yes)

> Subject: Your FlipFirst seller link
>
> Here's your sign-up link (it works once): [invite link]
>
> 1. Create your account and confirm your email address. Your listings go
>    live after that.
> 2. Private seller: tap **Sell → List a car**. Enter the VIN and tap "Fill
>    in from the VIN" to save typing.
> 3. Dealer: once I've approved your business, **Sell → Import your
>    inventory** accepts the attached CSV template. Re-upload it whenever
>    your inventory changes.
>
> Everything you enter is shown as reported by you. Please describe title
> status and known problems accurately; buyers rely on it.

### 4. Two-week check-in questions

1. How many buyers contacted you about FlipFirst listings?
2. Were they serious? Did any car sell to a FlipFirst buyer?
3. Was listing or uploading easy? What was annoying?
4. What would make you list more cars here?
5. Would you pay to list, and how much, if it reliably sold cars?

## Owner approvals needed before any of this is used

- Approve each message above, or edit it.
- Get the seller terms reviewed by a lawyer (`legal/SELLER_TERMS.md`), and
  for dealers, decide what you'll accept as proof of a business.
- Turn on seller submissions on the server (`SNIPER_SELLER_SUBMISSIONS=1`)
  once email delivery works (`FIRST_DEPLOY.md`).
