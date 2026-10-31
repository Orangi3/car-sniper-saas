# VinAudit Market Values inquiry (draft — NOT SENT)

Send only with the owner's approval, from the owner's address, through
VinAudit's contact form or sales address (check data.vinaudit.com for the
current one). It's an alternative to MarketCheck's Price API as the licensed
value source in `DATA_PROVIDER_DECISION.md` (tier 2). Prices in this file
are from search excerpts of VinAudit's pages, not a quote.

---

**Subject:** Market Values API for a small used-car analysis tool — licensing questions before a trial

Hello,

I run FlipFirst, a small subscription tool that helps independent used-car
buyers ("flippers") in Alabama judge whether a car is worth buying. A user
enters a car they found (VIN or year/make/model/trim, mileage, ZIP) and
FlipFirst shows its costs, risks and, when we have a licensed source, an
estimated market value.

We'd like to call your Market Values API once per user-initiated analysis
(not in bulk). We expect a few hundred calls a month during a private pilot,
growing with subscribers. Before we start the 100-query trial, could you
confirm in writing:

1. **Display:** may we show the returned value (mean, range, count) to our
   paying subscribers inside our app?
2. **Storage:** may a user's saved analysis keep the value we showed them,
   and for how long? May we cache one result per VIN for repeat lookups?
3. **Basis:**
   - Is the value private-party, retail, wholesale or trade-in?
   - Is it based on asking prices or completed sales, over what dates, and
     with what minimum count?
4. **Pricing:**
   - Does the self-service plan ($100 a month + $0.10 per query, as we read
     your site) include any queries?
   - Are no-match queries billed?
   - Is there pilot pricing for the first months?
5. **Attribution:** what wording or logo must we show next to the value?
6. **Accuracy:** do you publish error statistics for $2,000–$15,000 cars
   more than 10 years old?
7. **Terms:** which agreement governs this use, and may we see it before
   subscribing?

Thank you — happy to walk through the product on a call.

[Owner name] · FlipFirst · [email] · [phone]

---

**After a reply:** save the written answers. Record an agreement reference
only once they cover display to subscribers and storage in saved deals. The
adapter (about a day of work) is built only then.
