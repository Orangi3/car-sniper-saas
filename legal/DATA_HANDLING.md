# FlipFirst Data Handling Policy (internal) — DRAFT

Applies to the owner and anyone given server or database access.

## Access

- Server access only over Tailscale; public SSH is blocked (ufw). Admin
  accounts in the app only for people who run it.
- Secrets live only in `/etc/sniper/env` (`root:sniper`, mode 0600) and in
  your password manager — never in Git, chat, issues, email or AI tools.
- Backups are encrypted on the server with a public key; the decryption key
  stays off the server (`FIRST_DEPLOY.md` → Backups).

## Retention schedule

| Data | Retention | Mechanism |
|---|---|---|
| Accounts and their data | until deleted | `python -m accounts delete EMAIL --yes` (dry run without `--yes`) |
| Listing reports | open until resolved; then kept with the reporter's account | `python -m seller_listings reports / resolve`; account deletion |
| Dealer approvals | until revoked | `approve-dealer` / `revoke-dealer`; account deletion |
| Analyzed deals (workspace) | until the user deletes the deal or the account | the user's Delete button; account deletion. Private: no admin screen shows them; read them only to answer that user's own support request |
| Sessions | 30 days | expiry + cleanup |
| Failed sign-in counters | minutes | deleted as newer attempts arrive |
| Seller listings | until removed; hidden after 30 days unconfirmed | seller `Remove` / admin removal deletes derived rows; **[owner decision]** delete hidden listings after […] days |
| App and gunicorn logs | 14 days | `deploy/logrotate/sniper` |
| Caddy access log | 7 files of 10 MiB | Caddy log roll |
| Database backups | 30 days (B2) + last 3 nights on server | B2 lifecycle; `backup.sh` |
| Validation-run data (`validate_deals`) | until reviewed, then purged | `python -m validate_deals purge DIR` |
| eBay listing data on the server | delete when no longer publicly available | **not built — required before eBay data reaches the server** (`COMPLIANCE.md` C2) |
| MarketCheck data | per the signed agreement | **[agreement]** |

## Provider data rules

Follow `COMPLIANCE.md`. In short: no scraping; licensed sources only, gated
by `*_COMMERCIAL_AGREEMENT`; eBay listing data displayed at most 6 hours
stale; never put real provider data or personal data into AI tools (eBay's
2025 terms restrict generative-AI use of eBay data).

## Requests

- Deletion: verify the request comes from the account's email, run the
  dry run, then `--yes`; reply when done (within the period the privacy
  policy states).
- Copy of data: export the person's rows by hand **[owner: decide format]**.

## Incidents

If personal data may have been exposed: rotate affected secrets, revoke
sessions (`auth.revoke_sessions`), preserve logs, and get legal advice the
same day. **[LEGAL REVIEW]** Alabama breach-notification duties and
deadlines.
