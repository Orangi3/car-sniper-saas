// brand/render.js — render the PNG sizes from the SVGs that brand/build.py writes.
// Uses Playwright's Chromium (already used by tests/browser). Run from the repo root:
//   python3 brand/build.py && node brand/render.js && python3 brand/build.py --ico
const path = require("path");
const fs = require("fs");
const { chromium } = require("playwright");

const ROOT = path.resolve(__dirname, "..");
const S = p => path.join(ROOT, "static", "brand", p);
const E = p => path.join(ROOT, "brand", "exports", p);

// [source svg, output png, width, height]
const JOBS = [
  [S("favicon.svg"), S("favicon-16.png"), 16, 16],
  [S("favicon.svg"), S("favicon-32.png"), 32, 32],
  [S("favicon.svg"), S("favicon-48.png"), 48, 48],
  [E("app-icon.svg"), S("apple-touch-icon.png"), 180, 180],          // iOS rounds the corners itself
  [E("app-icon-rounded.svg"), S("icon-192.png"), 192, 192],
  [E("app-icon-rounded.svg"), S("icon-512.png"), 512, 512],
  [E("app-icon-maskable.svg"), S("icon-maskable-512.png"), 512, 512], // Android crops to its own shape
  [E("og-image.svg"), S("og-image.png"), 1200, 630],
  [E("instagram-profile.svg"), E("instagram-profile-1080.png"), 1080, 1080],
  [E("flipfirst-badge.svg"), E("flipfirst-badge-1024.png"), 1024, 1024],
  [E("flipfirst-emblem-gold.svg"), E("flipfirst-emblem-gold.png"), 0, 600],
  [E("flipfirst-logo-dark.svg"), E("flipfirst-logo-dark.png"), 0, 400],
  [E("flipfirst-logo-light.svg"), E("flipfirst-logo-light.png"), 0, 400],
  [E("flipfirst-logo-stacked-dark.svg"), E("flipfirst-logo-stacked-dark.png"), 0, 900],
  [E("flipfirst-logo-stacked-light.svg"), E("flipfirst-logo-stacked-light.png"), 0, 900],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const [src, out, w0, h] of JOBS) {
    const svg = fs.readFileSync(src, "utf8");
    const vb = svg.match(/viewBox="0 0 ([\d.]+) ([\d.]+)"/);
    const w = w0 || Math.round(h * Number(vb[1]) / Number(vb[2]));
    await page.setViewportSize({ width: w, height: h });
    const sized = svg.replace(/ width="[\d.]+" height="[\d.]+"/, ` width="${w}" height="${h}"`);
    await page.setContent(`<!doctype html><style>html,body{margin:0;background:transparent}svg{display:block}</style>${sized}`);
    await page.screenshot({ path: out, omitBackground: true, clip: { x: 0, y: 0, width: w, height: h } });
    console.log(path.relative(ROOT, out), `${w}x${h}`);
  }
  await browser.close();
})();
