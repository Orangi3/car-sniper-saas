# Sniper Marketplace Bridge — Chrome extension

A tiny Chrome extension that, when you click its icon while on Facebook
Marketplace, pushes the visible vehicle listings to your local Car Sniper.

## Why this and not auto-scraping?

Facebook's TOS prohibits automated scraping. Even with a burner account, a
fully automated scraper risks IP-level bans (which affect your whole house)
and is the same activity Meta has sued companies for. This extension stays
inside the rules: you, a real human, are browsing Marketplace; the
extension just snapshots what's already on your screen and forwards the
data to a process running on your own laptop.

## Install

1. Open Chrome → go to `chrome://extensions`.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked**.
4. Choose this folder (`marketplace_extension`).
5. The Sniper icon appears in your toolbar.

## Use

1. Make sure your sniper is running (double-click `START HERE.command`).
2. Go to Facebook Marketplace, search for cars in your area.
3. Scroll until you've loaded as many listings as you want.
4. Click the Sniper icon → **Push visible listings**.
5. Switch to your Sniper dashboard → filter by source `marketplace`.

The extension reads only what's already on screen — same data you can see
yourself. It never opens new tabs, never logs in, never persists anything
beyond a single button click.

## Caveats

- Facebook rotates CSS class names. If pushed counts drop to zero, edit
  the selectors in `content.js`.
- Some listings won't have a parsed price/year/mileage from the card text
  alone. Those will still upload but with partial data.
- The extension never auto-runs. Every push is a deliberate click.
