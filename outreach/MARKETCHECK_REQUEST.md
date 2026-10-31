# MarketCheck partnership request — ready for the owner to send

**Not sent.** You send it, from your own address, after reading it. Nothing
here commits FlipFirst to a purchase.

**Where to send** (from MarketCheck's own pages, via search 2026-10-30):
the "Contact us" / "Schedule a demo" form on marketcheck.com, or
`info@marketcheck.com` (listed on the homepage). `support@marketcheck.com`
is their technical support address — use it only for API questions after
an account exists. No verified sales phone number was found.

---

**Subject:** Data licensing for FlipFirst — private-party listings, comparables and Price API for a small pilot

Hello MarketCheck team,

I'm the founder of FlipFirst (planned at app.tryflipfirst.com), a web app
for independent used-car flippers: people who buy private-party cars below
market, recondition them and resell. FlipFirst shows them local listings
with an estimated market value and the evidence behind it, so they can
decide quickly which cars are worth a call.

**Pilot.** We start with a free, invite-only pilot for 10 flippers around
Tuscaloosa, Alabama: ZIP 35401, 150-mile radius, vehicles $2,000–$15,000.
After the pilot we plan a subscription of roughly $5–10 a month per user,
territory by territory.

**What we would need from you**
1. **Private-party listings** — Private Party Search
   (`/v2/search/car/fsbo/active`): one server-side query per territory
   every 15–60 minutes, newest first. Users never call your API.
2. **Comparables** — active dealer and private-party listings near the
   target vehicle, to estimate a value range and show which comparables were
   used.
3. **MarketCheck Price API** — a predicted price per vehicle as an
   additional, labeled value estimate.
4. **Storage** — listing records (price, year/make/model/trim, mileage,
   location, listing URL, first/last seen) and price history, kept up to
   [30] days to detect new listings and price drops, then deleted.
5. **Display to users** — limited listing details with a link to the
   original listing, our value range and the comparables behind it; later,
   alerts to subscribers. No bulk export, resale, or AI training.
6. **Internal evaluation first** — a few polls stored privately, reviewed
   by me, then deleted, before anything is shown to users.

**Questions**
1. Which plan or agreement permits uses 1–6, including storage beyond a
   live query and display to subscribers? Your terms say data may not form
   persistent datasets outside the Service and use must be tied to a live
   query or user action; we want to be clearly within your license.
2. Can the internal evaluation run on the Free or Basic plan?
3. Which plan includes Private Party Search and the Price API, what does
   each call cost, and is a 150-mile radius available (the Free tier lists a
   100-mile cap)?
4. Attribution, display, caching and retention requirements for listings,
   URLs and photos.
5. Which marketplaces feed the private-party data, and roughly how many
   active listings are within 150 miles of ZIP 35401 in our price range?
6. How quickly do new private listings and price changes appear?
7. Is there pilot or startup pricing for the first 3 months, before we
   charge users?

Thank you — I'm happy to walk through the product on a call.

[Your name] · FlipFirst · [email] · [phone]

---

**After a reply:** save the written answers. Record an agreement reference
in `MARKETCHECK_COMMERCIAL_AGREEMENT` only once the use above is permitted
in writing; then follow `FIRST_LIVE_TEST.md` § 4. Buy a plan only after you
approve the quoted price.
