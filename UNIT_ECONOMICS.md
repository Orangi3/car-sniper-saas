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
| AI development (coding tools / agent API spend, capped) | $200 | $100 | $20 |
| Monitoring / error tracking / uptime | $30 | $10 | $0 |

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

**Optimistic case** — cash break-even: **84 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $75 | $10 | $1 | $0 | $63 | $210 | **-$147** | 1 / 1,440 |
| 50 | $150 | $20 | $2 | $1 | $126 | $210 | **-$85** | 1 / 1,440 |
| 100 | $299 | $41 | $4 | $2 | $252 | $210 | **$41** | 1 / 1,440 |
| 250 | $748 | $102 | $11 | $5 | $629 | $210 | **$419** | 1 / 1,440 |
| 500 | $1,495 | $204 | $22 | $10 | $1,259 | $225 | **$1,034** | 2 / 2,880 |
| 1,000 | $2,990 | $408 | $45 | $20 | $2,518 | $478 | **$2,040** | 4 / 5,760 |
| 5,000 | $14,950 | $2,038 | $224 | $100 | $12,588 | $732 | **$11,855** | 20 / 28,800 |
| 10,000 | $29,900 | $4,076 | $449 | $200 | $25,175 | $1,044 | **$24,131** | 40 / 57,600 |

**Base case** — cash break-even: **202 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $75 | $10 | $2 | $1 | $61 | $477 | **-$416** | 1 / 1,440 |
| 50 | $150 | $20 | $4 | $2 | $122 | $477 | **-$355** | 1 / 1,440 |
| 100 | $299 | $41 | $9 | $5 | $244 | $477 | **-$233** | 1 / 1,440 |
| 250 | $748 | $102 | $22 | $12 | $611 | $492 | **$119** | 2 / 2,880 |
| 500 | $1,495 | $204 | $45 | $25 | $1,221 | $971 | **$251** | 4 / 5,760 |
| 1,000 | $2,990 | $408 | $90 | $50 | $2,443 | $1,014 | **$1,429** | 7 / 10,080 |
| 5,000 | $14,950 | $2,038 | $449 | $250 | $12,213 | $1,427 | **$10,787** | 34 / 48,960 |
| 10,000 | $29,900 | $4,076 | $897 | $500 | $24,427 | $1,950 | **$22,477** | 67 / 96,480 |

**Conservative case** — cash break-even: **546 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $75 | $10 | $4 | $4 | $57 | $652 | **-$595** | 1 / 2,880 |
| 50 | $150 | $20 | $7 | $8 | $114 | $652 | **-$538** | 1 / 2,880 |
| 100 | $299 | $41 | $15 | $15 | $228 | $652 | **-$424** | 1 / 2,880 |
| 250 | $748 | $102 | $37 | $38 | $571 | $1,159 | **-$589** | 3 / 8,640 |
| 500 | $1,495 | $204 | $75 | $75 | $1,141 | $1,217 | **-$76** | 5 / 14,400 |
| 1,000 | $2,990 | $408 | $150 | $150 | $2,283 | $1,361 | **$922** | 10 / 28,800 |
| 5,000 | $14,950 | $2,038 | $748 | $750 | $11,414 | $2,609 | **$8,805** | 50 / 144,000 |
| 10,000 | $29,900 | $4,076 | $1,495 | $1,500 | $22,829 | $4,193 | **$18,636** | 100 / 288,000 |

### All Pro @ $4.99

**Optimistic case** — cash break-even: **49 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $125 | $12 | $2 | $1 | $110 | $212 | **-$102** | 1 / 1,440 |
| 50 | $250 | $24 | $4 | $1 | $220 | $212 | **$8** | 1 / 1,440 |
| 100 | $499 | $48 | $7 | $3 | $441 | $212 | **$229** | 1 / 1,440 |
| 250 | $1,248 | $120 | $19 | $7 | $1,102 | $212 | **$890** | 1 / 1,440 |
| 500 | $2,495 | $240 | $37 | $14 | $2,203 | $226 | **$1,977** | 2 / 2,880 |
| 1,000 | $4,990 | $480 | $75 | $29 | $4,407 | $479 | **$3,927** | 4 / 5,760 |
| 5,000 | $24,950 | $2,398 | $374 | $144 | $22,034 | $734 | **$21,300** | 20 / 28,800 |
| 10,000 | $49,900 | $4,796 | $748 | $288 | $44,067 | $1,046 | **$43,021** | 40 / 57,600 |

**Base case** — cash break-even: **115 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $125 | $12 | $4 | $2 | $107 | $487 | **-$381** | 1 / 1,440 |
| 50 | $250 | $24 | $7 | $4 | $214 | $487 | **-$274** | 1 / 1,440 |
| 100 | $499 | $48 | $15 | $9 | $427 | $487 | **-$60** | 1 / 1,440 |
| 250 | $1,248 | $120 | $37 | $22 | $1,069 | $502 | **$567** | 2 / 2,880 |
| 500 | $2,495 | $240 | $75 | $43 | $2,137 | $981 | **$1,157** | 4 / 5,760 |
| 1,000 | $4,990 | $480 | $150 | $86 | $4,275 | $1,024 | **$3,251** | 7 / 10,080 |
| 5,000 | $24,950 | $2,398 | $748 | $430 | $21,373 | $1,437 | **$19,937** | 34 / 48,960 |
| 10,000 | $49,900 | $4,796 | $1,497 | $860 | $42,747 | $1,960 | **$40,787** | 67 / 96,480 |

**Conservative case** — cash break-even: **296 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $125 | $12 | $6 | $8 | $99 | $662 | **-$563** | 1 / 2,880 |
| 50 | $250 | $24 | $12 | $15 | $198 | $662 | **-$464** | 1 / 2,880 |
| 100 | $499 | $48 | $25 | $31 | $395 | $662 | **-$266** | 1 / 2,880 |
| 250 | $1,248 | $120 | $62 | $76 | $989 | $1,169 | **-$181** | 3 / 8,640 |
| 500 | $2,495 | $240 | $125 | $153 | $1,977 | $1,227 | **$750** | 5 / 14,400 |
| 1,000 | $4,990 | $480 | $250 | $306 | $3,955 | $1,371 | **$2,584** | 10 / 28,800 |
| 5,000 | $24,950 | $2,398 | $1,248 | $1,530 | $19,774 | $2,619 | **$17,155** | 50 / 144,000 |
| 10,000 | $49,900 | $4,796 | $2,495 | $3,060 | $39,549 | $4,203 | **$35,346** | 100 / 288,000 |

### Mixed: 60% Pro / 40% Lite

**Optimistic case** — cash break-even: **58 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $105 | $11 | $2 | $1 | $91 | $212 | **-$121** | 1 / 1,440 |
| 50 | $210 | $23 | $3 | $1 | $183 | $212 | **-$29** | 1 / 1,440 |
| 100 | $419 | $45 | $6 | $3 | $365 | $212 | **$153** | 1 / 1,440 |
| 250 | $1,048 | $113 | $16 | $6 | $913 | $212 | **$701** | 1 / 1,440 |
| 500 | $2,095 | $225 | $31 | $13 | $1,826 | $226 | **$1,599** | 2 / 2,880 |
| 1,000 | $4,190 | $451 | $63 | $25 | $3,651 | $479 | **$3,172** | 4 / 5,760 |
| 5,000 | $20,950 | $2,254 | $314 | $126 | $18,255 | $734 | **$17,521** | 20 / 28,800 |
| 10,000 | $41,900 | $4,508 | $628 | $253 | $36,510 | $1,046 | **$35,465** | 40 / 57,600 |

**Base case** — cash break-even: **138 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $105 | $11 | $3 | $2 | $89 | $487 | **-$399** | 1 / 1,440 |
| 50 | $210 | $23 | $6 | $4 | $177 | $487 | **-$310** | 1 / 1,440 |
| 100 | $419 | $45 | $13 | $7 | $354 | $487 | **-$133** | 1 / 1,440 |
| 250 | $1,048 | $113 | $31 | $18 | $885 | $502 | **$384** | 2 / 2,880 |
| 500 | $2,095 | $225 | $63 | $36 | $1,771 | $981 | **$790** | 4 / 5,760 |
| 1,000 | $4,190 | $451 | $126 | $72 | $3,542 | $1,024 | **$2,518** | 7 / 10,080 |
| 5,000 | $20,950 | $2,254 | $628 | $358 | $17,709 | $1,437 | **$16,273** | 34 / 48,960 |
| 10,000 | $41,900 | $4,508 | $1,257 | $716 | $35,419 | $1,960 | **$33,459** | 67 / 96,480 |

**Conservative case** — cash break-even: **365 subscribers**

| Subs | Revenue | Payment fees | Refund/fail loss | Variable ops | Contribution | Fixed ops (data+host+tools) | Operating result | Data: territories / calls |
|---:|---:|---:|---:|---:|---:|---:|---:|---|
| 25 | $105 | $11 | $5 | $6 | $82 | $662 | **-$580** | 1 / 2,880 |
| 50 | $210 | $23 | $10 | $12 | $164 | $662 | **-$497** | 1 / 2,880 |
| 100 | $419 | $45 | $21 | $24 | $329 | $662 | **-$333** | 1 / 2,880 |
| 250 | $1,048 | $113 | $52 | $61 | $822 | $1,169 | **-$348** | 3 / 8,640 |
| 500 | $2,095 | $225 | $105 | $122 | $1,643 | $1,227 | **$416** | 5 / 14,400 |
| 1,000 | $4,190 | $451 | $210 | $244 | $3,286 | $1,371 | **$1,915** | 10 / 28,800 |
| 5,000 | $20,950 | $2,254 | $1,048 | $1,218 | $16,430 | $2,619 | **$13,811** | 50 / 144,000 |
| 10,000 | $41,900 | $4,508 | $2,095 | $2,436 | $32,861 | $4,203 | **$28,658** | 100 / 288,000 |

### Cost per active customer (mixed tiers)

All monthly costs (data, hosting, engineering, support, SMS, payment fees, refunds) divided by subscribers; ARPU is the 60/40 mix ($4.19).

| Subs | Optimistic | Base | Conservative |
|---:|---:|---:|---:|
| 25 | $9.01 | $20.14 | $27.38 |
| 50 | $4.78 | $10.40 | $14.14 |
| 100 | $2.66 | $5.52 | $7.52 |
| 250 | $1.39 | $2.66 | $5.58 |
| 500 | $0.99 | $2.61 | $3.36 |
| 1,000 | $1.02 | $1.67 | $2.27 |

### Cost per territory (data only)

Marginal provider fees per extra territory, before any plan change (Basic covers 5,000 calls/month).

| Polling | Calls / month | Fees @ $0.01 | Fees @ $0.02 | Territories on Basic |
|---|---:|---:|---:|---:|
| every 60 min | 720 | $7.20 | $14.40 | 6 |
| every 30 min | 1,440 | $14.40 | $28.80 | 3 |
| every 15 min | 2,880 | $28.80 | $57.60 | 1 |

### Sensitivity to data pricing (base case, mixed tiers)

Cash break-even subscribers by platform fee (rows) and per-call fee (columns). Higher tiers scale proportionally.

| Platform fee | $0.005/call | $0.010/call | $0.020/call | $0.030/call |
|---:|---:|---:|---:|---:|
| $99/mo | 80 | 81 | 86 | 90 |
| $149/mo | 94 | 96 | 100 | 104 |
| $299/mo | 136 | 138 | 142 | 146 |
| $499/mo | 195 | 199 | 207 | 215 |
| $749/mo | 265 | 269 | 277 | 285 |

### Payment fee per charge

| Price | Fee | Share of price |
|---:|---:|---:|
| $2.99 | $0.408 | 13.6% |
| $4.99 | $0.480 | 9.6% |
| $29.99 | $1.380 | 4.6% |
| $49.99 | $2.100 | 4.2% |


## What the numbers say

- **Fixed cost dominates until ~100–150 subscribers.** Base-case fixed cost
  is ~$487/mo: data ~$313 (MarketCheck Basic + 1,440 FSBO calls), hosting
  ~$44, AI development $100, support tools $10, monitoring $10, SMS campaign
  $10. At 25 subscribers the base case costs ~$20 per subscriber against a
  $4.19 mixed ARPU; at 250 it falls to ~$2.66.
- **Break-even (base case):** ~138 subscribers on the 60/40 mix; ~115 if
  everyone takes Pro; ~202 if everyone takes Lite. Conservative: 296–546.
  Optimistic (pilot data pricing, low tooling): 49–84.
- **Data price is the biggest lever.** At base assumptions, break-even moves
  from ~96 subscribers at a $149 platform fee to ~269 at $749. The per-call
  fee barely matters (±10 subscribers between $0.005 and $0.03) because one
  territory polls only 1,440 times a month.
- **AI development is a real, owner-controlled cost.** $100/mo moves base
  break-even by ~25 subscribers. It is spend-capped and pausable
  (`AGENTS.md`); it is not "continuous" work and stops when the budget does.
- **$2.99 is weak.** Stripe's fixed $0.30 plus 3.6% takes 13.6% of a $2.99
  charge (9.6% of $4.99). Annual billing ($29.99 / $49.99) cuts the fee
  drag to ~4–5%.
- **SMS is a minor cost** at the Pro cap (30/month) *because it is capped*.
- **Territories, not subscribers, drive data cost.** Each extra territory
  adds $7–29/month in call fees at 60–15 min polling, until the Basic quota
  (5,000 calls) forces the Standard plan.

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
   a $149 platform fee cuts base break-even from ~138 to ~96 subscribers.
