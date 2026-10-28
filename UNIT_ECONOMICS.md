# Unit economics

Generated tables come from `economics/model.py` (run
`python economics/model.py > economics/tables.md`). Change an input there,
not here. Figures are monthly, USD, cash basis, before income tax and before
paying the founder.

## Inputs

**Sourced** — retrieved 2026-10-28 via web search; several come from
third-party pricing summaries, so verify on the vendor's site before relying
on them.

| Input | Value | Source |
|---|---|---|
| Stripe card processing (US) | 2.9% + $0.30 per charge | [Stripe fee summaries](https://www.budgetforge.dev/tools/stripe-fees-2026) (verify at stripe.com/pricing) |
| Stripe Billing | 0.7% of billing volume (some sources say 0.5%) | [Stripe Billing fees](https://ecomcalctools.com/blog/fees-stripe/stripe-billing-fees/) |
| MarketCheck Basic | $299/mo, 5,000 calls/mo, 5 calls/s | [MarketCheck pricing](https://www.marketcheck.com/pricing) |
| MarketCheck Standard | $749/mo, no fixed call quota | [MarketCheck pricing](https://www.marketcheck.com/pricing) |
| MarketCheck Private Party Search | $0.01 per call | [MarketCheck API pricing](https://www.marketcheck.com/apis/pricing/) |
| Twilio US SMS, all-in | ~$0.011–0.013 per segment (base ~$0.0079–0.0083 + ~$0.003 carrier) | [Twilio SMS pricing](https://www.twilio.com/en-us/sms/pricing/us), [breakdown](https://apidog.com/blog/twilio-sms-api-cost) |
| 10DLC registration | brand $4.50–$46 one-time; campaign ~$1.50–10/mo | [Twilio support](https://support.twilio.com/hc/en-us/articles/1260803965530) |
| VPS 2 vCPU / 4 GB | ~$24/mo | [DigitalOcean pricing summary](https://www.fluence.network/blog/digitalocean-droplets-vs-fluence/) |
| Managed Postgres | ~$15/mo single node; ~$30 with standby | [InfraTally](https://infratally.com/articles/digitalocean-managed-postgresql-pricing-2026-billing-model/) |

**Assumptions** (modeling choices; conservative / base / optimistic)

| Assumption | Conservative | Base | Optimistic |
|---|---|---|---|
| Data platform fee | $299 (Basic) | $299 (Basic) | $149 — **only if MarketCheck grants pilot pricing (unconfirmed)** |
| FSBO polling per territory | every 15 min (2,880 calls) | every 30 min (1,440) | every 30 min |
| Subscribers served per territory (100 mi radius) | 100 | 150 | 250 |
| Hosting | VPS + PG w/ standby + $15 tools, +$48 per 2k subs | VPS + PG + $5, +$24 per 3k | VPS w/ local PG + $3, +$24 per 5k |
| Refund + failed-payment loss | 5% | 3% | 1.5% |
| Support (cash tools + per-sub time) | $25 + $0.15/sub | $10 + $0.05/sub | $0 + $0.02/sub |
| Pro users opting into SMS / msgs each (cap 30) | 40% / 30 | 20% / 15 | 10% / 8 |
| Mixed-tier share on Pro | 60% | 60% | 60% |

Above 5,000 calls/month the model moves to MarketCheck Standard ($749).
Push alerts (ntfy) and webhooks cost ~$0 per message.

## Polling cost is independent of subscribers

Car Sniper polls each **territory** once per interval and matches every
subscriber against that shared data. 1 subscriber or 150 in the same
territory cost the same provider calls. What grows cost is **geographic
spread**: a beta scattered across the country needs one territory per
cluster. **Launch in one metro** (e.g. Tuscaloosa/Birmingham, 100 mi) so the
data bill stays at ~$313/mo (base: $299 + 1,440 calls × $0.01) no matter how
many local users join.

| Polling | Calls / territory / month | FSBO fees | Territories on Basic (5,000 calls) |
|---|---:|---:|---:|
| 60 min | 720 | $7.20 | 6 |
| 30 min | 1,440 | $14.40 | 3 |
| 15 min | 2,880 | $28.80 | 1 |

## Results

### All Lite @ $2.99

**Optimistic case** — cash break-even: **76 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $75 | $10 | $1 | $0 | $63 | $190 | **-$127** | 1 / 1,440 |
| 50 | $150 | $20 | $2 | $1 | $126 | $190 | **-$65** | 1 / 1,440 |
| 100 | $299 | $41 | $4 | $2 | $252 | $190 | **$61** | 1 / 1,440 |
| 250 | $748 | $102 | $11 | $5 | $629 | $190 | **$439** | 1 / 1,440 |
| 500 | $1,495 | $204 | $22 | $10 | $1,259 | $205 | **$1,054** | 2 / 2,880 |
| 1,000 | $2,990 | $408 | $45 | $20 | $2,518 | $834 | **$1,684** | 4 / 5,760 |
| 5,000 | $14,950 | $2,038 | $224 | $100 | $12,588 | $1,088 | **$11,500** | 20 / 28,800 |
| 10,000 | $29,900 | $4,076 | $449 | $200 | $25,175 | $1,400 | **$23,775** | 40 / 57,600 |

**Base case** — cash break-even: **157 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $75 | $10 | $2 | $1 | $61 | $367 | **-$306** | 1 / 1,440 |
| 50 | $150 | $20 | $4 | $2 | $122 | $367 | **-$245** | 1 / 1,440 |
| 100 | $299 | $41 | $9 | $5 | $244 | $367 | **-$123** | 1 / 1,440 |
| 250 | $748 | $102 | $22 | $12 | $611 | $382 | **$229** | 2 / 2,880 |
| 500 | $1,495 | $204 | $45 | $25 | $1,221 | $861 | **$361** | 4 / 5,760 |
| 1,000 | $2,990 | $408 | $90 | $50 | $2,443 | $904 | **$1,539** | 7 / 10,080 |
| 5,000 | $14,950 | $2,038 | $449 | $250 | $12,213 | $1,317 | **$10,897** | 34 / 48,960 |
| 10,000 | $29,900 | $4,076 | $897 | $500 | $24,427 | $1,840 | **$22,587** | 67 / 96,480 |

**Conservative case** — cash break-even: **433 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $75 | $10 | $4 | $4 | $57 | $422 | **-$365** | 1 / 2,880 |
| 50 | $150 | $20 | $7 | $8 | $114 | $422 | **-$308** | 1 / 2,880 |
| 100 | $299 | $41 | $15 | $15 | $228 | $422 | **-$194** | 1 / 2,880 |
| 250 | $748 | $102 | $37 | $38 | $571 | $929 | **-$359** | 3 / 8,640 |
| 500 | $1,495 | $204 | $75 | $75 | $1,141 | $987 | **$154** | 5 / 14,400 |
| 1,000 | $2,990 | $408 | $150 | $150 | $2,283 | $1,131 | **$1,152** | 10 / 28,800 |
| 5,000 | $14,950 | $2,038 | $748 | $750 | $11,414 | $2,379 | **$9,035** | 50 / 144,000 |
| 10,000 | $29,900 | $4,076 | $1,495 | $1,500 | $22,829 | $3,963 | **$18,866** | 100 / 288,000 |

### All Pro @ $4.99

**Optimistic case** — cash break-even: **44 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $125 | $12 | $2 | $1 | $110 | $192 | **-$82** | 1 / 1,440 |
| 50 | $250 | $24 | $4 | $1 | $220 | $192 | **$28** | 1 / 1,440 |
| 100 | $499 | $48 | $7 | $3 | $441 | $192 | **$249** | 1 / 1,440 |
| 250 | $1,248 | $120 | $19 | $7 | $1,102 | $192 | **$910** | 1 / 1,440 |
| 500 | $2,495 | $240 | $37 | $14 | $2,203 | $206 | **$1,997** | 2 / 2,880 |
| 1,000 | $4,990 | $480 | $75 | $29 | $4,407 | $835 | **$3,572** | 4 / 5,760 |
| 5,000 | $24,950 | $2,398 | $374 | $144 | $22,034 | $1,090 | **$20,944** | 20 / 28,800 |
| 10,000 | $49,900 | $4,796 | $748 | $288 | $44,067 | $1,402 | **$42,666** | 40 / 57,600 |

**Base case** — cash break-even: **89 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $125 | $12 | $4 | $2 | $107 | $377 | **-$271** | 1 / 1,440 |
| 50 | $250 | $24 | $7 | $4 | $214 | $377 | **-$164** | 1 / 1,440 |
| 100 | $499 | $48 | $15 | $9 | $427 | $377 | **$50** | 1 / 1,440 |
| 250 | $1,248 | $120 | $37 | $22 | $1,069 | $392 | **$677** | 2 / 2,880 |
| 500 | $2,495 | $240 | $75 | $43 | $2,137 | $871 | **$1,267** | 4 / 5,760 |
| 1,000 | $4,990 | $480 | $150 | $86 | $4,275 | $914 | **$3,361** | 7 / 10,080 |
| 5,000 | $24,950 | $2,398 | $748 | $430 | $21,373 | $1,327 | **$20,047** | 34 / 48,960 |
| 10,000 | $49,900 | $4,796 | $1,497 | $860 | $42,747 | $1,850 | **$40,897** | 67 / 96,480 |

**Conservative case** — cash break-even: **238 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $125 | $12 | $6 | $8 | $99 | $432 | **-$333** | 1 / 2,880 |
| 50 | $250 | $24 | $12 | $15 | $198 | $432 | **-$234** | 1 / 2,880 |
| 100 | $499 | $48 | $25 | $31 | $395 | $432 | **-$36** | 1 / 2,880 |
| 250 | $1,248 | $120 | $62 | $76 | $989 | $939 | **$49** | 3 / 8,640 |
| 500 | $2,495 | $240 | $125 | $153 | $1,977 | $997 | **$980** | 5 / 14,400 |
| 1,000 | $4,990 | $480 | $250 | $306 | $3,955 | $1,141 | **$2,814** | 10 / 28,800 |
| 5,000 | $24,950 | $2,398 | $1,248 | $1,530 | $19,774 | $2,389 | **$17,385** | 50 / 144,000 |
| 10,000 | $49,900 | $4,796 | $2,495 | $3,060 | $39,549 | $3,973 | **$35,576** | 100 / 288,000 |

### Mixed: 60% Pro / 40% Lite

**Optimistic case** — cash break-even: **53 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $105 | $11 | $2 | $1 | $91 | $192 | **-$101** | 1 / 1,440 |
| 50 | $210 | $23 | $3 | $1 | $183 | $192 | **-$9** | 1 / 1,440 |
| 100 | $419 | $45 | $6 | $3 | $365 | $192 | **$173** | 1 / 1,440 |
| 250 | $1,048 | $113 | $16 | $6 | $913 | $192 | **$721** | 1 / 1,440 |
| 500 | $2,095 | $225 | $31 | $13 | $1,826 | $206 | **$1,619** | 2 / 2,880 |
| 1,000 | $4,190 | $451 | $63 | $25 | $3,651 | $835 | **$2,816** | 4 / 5,760 |
| 5,000 | $20,950 | $2,254 | $314 | $126 | $18,255 | $1,090 | **$17,166** | 20 / 28,800 |
| 10,000 | $41,900 | $4,508 | $628 | $253 | $36,510 | $1,402 | **$35,109** | 40 / 57,600 |

**Base case** — cash break-even: **107 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $105 | $11 | $3 | $2 | $89 | $377 | **-$289** | 1 / 1,440 |
| 50 | $210 | $23 | $6 | $4 | $177 | $377 | **-$200** | 1 / 1,440 |
| 100 | $419 | $45 | $13 | $7 | $354 | $377 | **-$23** | 1 / 1,440 |
| 250 | $1,048 | $113 | $31 | $18 | $885 | $392 | **$494** | 2 / 2,880 |
| 500 | $2,095 | $225 | $63 | $36 | $1,771 | $871 | **$900** | 4 / 5,760 |
| 1,000 | $4,190 | $451 | $126 | $72 | $3,542 | $914 | **$2,628** | 7 / 10,080 |
| 5,000 | $20,950 | $2,254 | $628 | $358 | $17,709 | $1,327 | **$16,383** | 34 / 48,960 |
| 10,000 | $41,900 | $4,508 | $1,257 | $716 | $35,419 | $1,850 | **$33,569** | 67 / 96,480 |

**Conservative case** — cash break-even: **286 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $105 | $11 | $5 | $6 | $82 | $432 | **-$350** | 1 / 2,880 |
| 50 | $210 | $23 | $10 | $12 | $164 | $432 | **-$267** | 1 / 2,880 |
| 100 | $419 | $45 | $21 | $24 | $329 | $432 | **-$103** | 1 / 2,880 |
| 250 | $1,048 | $113 | $52 | $61 | $822 | $939 | **-$118** | 3 / 8,640 |
| 500 | $2,095 | $225 | $105 | $122 | $1,643 | $997 | **$646** | 5 / 14,400 |
| 1,000 | $4,190 | $451 | $210 | $244 | $3,286 | $1,141 | **$2,145** | 10 / 28,800 |
| 5,000 | $20,950 | $2,254 | $1,048 | $1,218 | $16,430 | $2,389 | **$14,041** | 50 / 144,000 |
| 10,000 | $41,900 | $4,508 | $2,095 | $2,436 | $32,861 | $3,973 | **$28,888** | 100 / 288,000 |

### Payment fee per charge

| Price | Fee | Share of price |
|---:|---:|---:|
| $2.99 | $0.408 | 13.6% |
| $4.99 | $0.480 | 9.6% |
| $29.99 | $1.380 | 4.6% |
| $49.99 | $2.100 | 4.2% |


## What the numbers say

- **Fixed cost dominates until ~100–150 subscribers.** Base-case fixed cost
  is ~$377/mo (data ~$313 + hosting ~$44 + tools $10 + SMS campaign $10).
  At 25 subscribers the base case loses ~$290/month, at 50 ~$200, at 100
  ~$23. $299/mo of vendor spend is not sustainable at tiny scale — it is a
  deliberate investment of roughly $375/mo until break-even.
- **Break-even (base case):** ~107 subscribers on the 60/40 mix; ~89 if
  everyone takes Pro; ~157 if everyone takes Lite. Conservative: 238–433.
- **$2.99 is weak.** Stripe's fixed $0.30 plus 3.6% takes 13.6% of a $2.99
  charge (9.6% of $4.99). Lite needs ~75% more subscribers than Pro for the
  same result.
- **Annual billing fixes most of the fee drag:** a $29.99/yr Lite or
  $49.99/yr Pro charge loses ~4–5% to fees instead of 10–14%.
- **SMS is a minor cost** at the Pro cap (30/month) — under $0.40 per SMS
  user even in the conservative case — *because it is capped*. Removing the
  cap is the main variable-cost risk.
- **Scaling limits:** each new metro adds a polling territory (the 1,440
  calls/month at 30-min polling fill Basic's 5,000 calls at 3 territories,
  then Standard at $749 is needed). Coverage expansion, not subscriber count,
  drives data cost.

## Primary cost risks

1. MarketCheck declining the stored-alert use case or requiring a higher
   commercial tier than Basic → the whole model changes.
2. FSBO listing density in the launch territory too low to deliver
   worthwhile opportunities (unverified; vendor reports ~80,000 active FSBO
   listings nationwide).
3. Users spread across many metros before revenue supports more territories.
4. Refunds/chargebacks from users who expected more deals than the
   territory produces.

## Recommendation

1. **Run a free controlled beta first** (owner-funded data, 1 metro, 25–50
   invited users) to measure listing density, opportunity rate and alert
   speed with the evaluation harness before charging.
2. **Paid launch with Pro at $4.99/mo and an annual option** ($49.99/yr).
   Keep **Lite at $2.99 only as annual ($29.99/yr)** or drop it at launch;
   monthly Lite loses too much to fixed fees.
3. Gate signups to served territories (waitlist elsewhere) so data cost
   stays flat until each territory has ~150 subscribers.
4. Pursue MarketCheck pilot/startup pricing before committing to $299/mo;
   the optimistic case cuts break-even to ~53 subscribers.
