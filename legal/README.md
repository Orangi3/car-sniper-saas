# Legal and policy drafts — NOT PUBLISHED

**Status: DRAFTS for the owner and a lawyer to review. Do not publish, link
or send them as final.** They describe what the code does as of this branch;
they do not create licensing rights FlipFirst doesn't have. Every place
marked **[LEGAL REVIEW]** needs a professional decision; `[…]` marks facts
only the owner can fill in (legal entity, address, contact email, hosting
provider).

| File | For | Publish where (after review) |
|---|---|---|
| `PRIVACY_POLICY.md` | everyone who uses the app | a public page linked from sign-in and invitations |
| `TERMS_OF_SERVICE.md` | buyers / subscribers | linked from sign-in |
| `SELLER_TERMS.md` | people who list a car | linked from the Sell flow before the confirmations |
| `BETA_TESTER_TERMS.md` | the 10 pilot testers | sent with the invitation; tester confirms by email |
| `VALUATION_DISCLAIMER.md` | everyone | the deal page already shows the short form; publish the long form |
| `DATA_HANDLING.md` | internal (owner, anyone with server access) | not public |

The app has no `/privacy` or `/terms` page yet. For the pilot, publish the
reviewed text anywhere you control (for example `tryflipfirst.com/privacy`)
and put the links in the invitation message.

Facts these drafts rely on are checked in code: data stored (`migrations/`),
session cookie (`auth.py`: `Secure`, `HttpOnly`, `SameSite=Lax`, 30 days),
log retention (`deploy/logrotate/sniper`: 14 days), backups (30-day B2
lifecycle, `FIRST_DEPLOY.md`), deletion (`python -m accounts delete`,
seller `Remove`), provider rules (`COMPLIANCE.md`).
