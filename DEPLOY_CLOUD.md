# Put FlipFirst online — the short path (Render)

One container runs everything (migrations → worker → scheduler → web) next
to a managed PostgreSQL database. No server to set up, no SSH, no Caddy:
Render provides HTTPS. Files: `Dockerfile`, `deploy/paas/start.sh`,
`render.yaml`. The VPS path (`FIRST_DEPLOY.md`, `deploy/`) still works and
is cheaper at scale; this one is faster to get running.

## What it costs

Render bills the web service and the database separately. `render.yaml`
uses the smallest **paid** plans (`0.5c-512mb` web, `0.1c-256mb` Postgres).
Check today's prices at render.com/pricing before you create anything.
Don't switch the web service to the free plan: it sleeps after 15 idle
minutes, which stops the scheduler and the alert worker.

## Steps (~15 minutes)

1. **Merge the PR** that added these files into `main`.
2. Sign in at **render.com** with GitHub and allow it to read
   `Orangi3/car-sniper-saas`.
3. Render dashboard → **New → Blueprint** → pick the repo → **Apply**.
   It creates `flipfirst` (web) and `flipfirst-db` (Postgres) and links them.
   The first build takes a few minutes.
4. Open the `flipfirst` service → its `https://….onrender.com` address →
   `/health` should show `{"status":"ok"}`.
5. **Make your admin account.** Service → **Shell**, then:
   ```bash
   python -m accounts create-admin you@example.com
   ```
   It asks for a password (12+ characters) and never echoes it.
6. **Invite testers** (sign-ups are invite-only), same Shell:
   ```bash
   python -m invites issue --note "pilot" --count 10 --days 14
   ```
   Each link is printed once; send each one to one person yourself.
7. Log in at `https://….onrender.com/m`.

Deploys don't happen on every push (`autoDeploy: false`). To ship a new
version: merge to `main`, then **Manual Deploy → Deploy latest commit**.

## What works on day one, and what doesn't

Works: accounts and invites, the mobile app (`/m`), saved deals, cost
assumptions, territory, seller listing pages, the alert worker and the
scheduler.

Doesn't yet, because the data and services aren't connected:
- **No automatic car listings.** Commercial mode only polls licensed sources
  (`SOURCES.md`). Listings start when eBay or MarketCheck keys and agreements
  are added (`OWNER_ACTIONS.md`).
- **Seller email confirmation** needs SMTP settings (`OWNER_ACTIONS.md` E).
- **Payments** need your Stripe keys and prices (`STRIPE_TESTING.md`).

Add any of these later under the service's **Environment** tab, then
redeploy. Variable names are in `.env.example` and `LAUNCH_CHECKLIST.md` §4.
Never commit a key to Git.

## Custom domain (optional)

Service → **Settings → Custom Domains** → add `app.tryflipfirst.com`, create
the DNS record Render shows, then set `BILLING_ORIGIN=https://app.tryflipfirst.com`
in Environment and redeploy. Without it the `onrender.com` address is used
automatically.

## Backups

Check the `flipfirst-db` page → **Recovery** to see which automatic backups
your Render plan keeps. The database only accepts connections from inside
Render (`ipAllowList: []`), so off-site encrypted copies with
`deploy/backup.sh` (Backblaze B2 + age, see `FIRST_DEPLOY.md`) would have to
run from a Render cron job. That isn't set up yet.

## If something breaks

- **Logs:** service → **Logs**. Every line from the four processes is there;
  `[start]` lines come from the start script.
- **Container keeps restarting:** if the web server or worker exits, the
  container stops on purpose so Render restarts it. The log line
  `[start] a process exited (code N)` names the cause just above it.
- **"cross-site request refused" on login:** `BILLING_ORIGIN` doesn't match
  the address in your browser bar. Unset it to use the Render address, or set
  it to your custom domain exactly (https, no trailing slash).
- **Admin pages:** `/admin/ops` (admin accounts only).
