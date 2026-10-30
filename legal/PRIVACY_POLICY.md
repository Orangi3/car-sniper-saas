# FlipFirst Privacy Policy — DRAFT, NOT PUBLISHED

*Draft for legal review. Effective date: […]. Operator: […legal entity…],
[…address…]. Contact: […privacy email…].*

## What FlipFirst is

FlipFirst is a web app that shows used-car buyers vehicle listings with an
estimated market value. During the pilot, access is by invitation only.

## Information we collect and why

| Information | Why | Kept |
|---|---|---|
| Email address, password (stored only as a one-way bcrypt hash), plan, sign-up and last sign-in time | your account | until you delete your account |
| Sign-in sessions: a hashed session token, the IP address and browser description at sign-in, expiry | keeping you signed in; spotting misuse | 30 days, then deleted |
| Failed sign-in attempts, counted by network address | stopping password guessing | minutes; deleted as newer attempts arrive |
| Your buying area (ZIP code and radius) | showing cars near you | until you change it or delete your account |
| Saved searches, saved or dismissed cars and their stage (contacted, going to see, bought…), your notes, matches and alert history | the features you use | until you delete them or your account |
| Your cost assumptions for profit estimates | your estimates | until you change them or delete your account |
| Cars you analyze and save in your workspace: the vehicle details, VIN and prices you enter, the analysis, your inspection notes, the deal's stage, and any purchase, repair, fee and sale figures you record | showing you your analyses, comparisons and track record | until you delete the deal or your account; private to you, never shown to other users or used in aggregate **[LEGAL REVIEW: any future aggregate use needs a separate opt-in]** |
| A count of analyses you run each day | enforcing plan limits | with your account |
| If you report a listing: the listing, the reason and your note | reviewing the listing | kept with your account after the report is resolved; deleted with your account **[owner: confirm]** |
| If you're an approved dealer: the business name shown on your listings, your listing limit, and the owner's note of what was checked | labeling and limiting dealer listings | until approval is revoked or your account is deleted |
| Alert settings: push topic, webhook address, and — only if you turn on text alerts — your phone number | sending your alerts | until you remove them or delete your account |
| If you list a car: vehicle details, VIN, price, ZIP, condition notes, description, a verified contact email, and your three confirmations (authorized to sell, details accurate, contact may be shown) with their time | publishing your listing and showing buyers how to reach you | until you remove the listing; removal deletes it and everything derived from it, keeping only a record that a listing existed |
| Server logs: pages requested, IP address, browser, and the signed-in account's email | security and troubleshooting | 14 days for app logs; web-server logs are replaced as they reach a size limit **[owner: confirm]** |
| Encrypted database backups | recovery | 30 days |

We do not use advertising or analytics trackers. Besides the sign-in cookie,
the app keeps your light/dark display choice in your browser's local storage,
and — only until you close the tab — unfinished form drafts and list
preferences in its session storage. None of it is sent to anyone.

## Cookies

One cookie, `sniper_session`, keeps you signed in. It is secure (HTTPS
only), not readable by page scripts, and expires after 30 days.

## Who receives information

We don't sell personal information. Service providers that process it for
us:

| Provider | What | When |
|---|---|---|
| […hosting provider…] | the server and database | always |
| Backblaze B2 | encrypted backups (we hold the decryption key, not the server) | nightly |
| Resend | the email address and message for seller email confirmation | when you list a car |
| ntfy (ntfy.sh, unless we run our own) | alert text (car, price, link) sent to your push topic | when you turn on push alerts |
| NHTSA vPIC (U.S. Department of Transportation's public VIN decoder) | the VIN only — no name, email or account | when you decode or analyze a car with a VIN |
| Twilio | your phone number and alert text | only if text alerts are enabled — **not used in the pilot** |
| Stripe | payment details (FlipFirst never sees card numbers) | only once paid plans exist — **not used in the pilot** |

If you list a car, your verified contact email is shown to signed-in buyers
on paid plans so they can contact you.

**[LEGAL REVIEW]** disclosures required to authorities, business
transfers, and any state-specific rights language.

## Vehicle listing data from others

Listings may come from licensed data providers and from sellers on
FlipFirst. Provider data is used only as each provider's terms allow
(including deleting it when the provider requires).

## Your choices

- Change or delete saved searches, alerts and listings in the app.
- Ask for a copy of your information, or for your account to be deleted,
  by emailing […privacy email…]. We confirm deletion within […30…] days.
  Deleted data stays in encrypted backups until they expire (30 days) and in
  logs until they rotate out (14 days).
- **[LEGAL REVIEW]** whether any state privacy law applies at the pilot's
  size and location, and the rights language it requires.

## Security

HTTPS everywhere; passwords and session tokens stored only as hashes;
sign-in rate limits; server access over a private network; encrypted
backups. No system is perfectly secure. **[LEGAL REVIEW]** breach-notice
language (Alabama has a data-breach notification law).

## Children

FlipFirst is not for anyone under 18.

## Changes

We will post changes here and tell signed-in users about material changes.
