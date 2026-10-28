// tests/browser/smoke_mobile.js — headless browser smoke test of /m.
// Requires a running server seeded by seed_demo.py (see DEPLOY.md / CI).
//   BASE=http://127.0.0.1:8765 SHOTS=/tmp node tests/browser/smoke_mobile.js
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://127.0.0.1:8765";
const SHOTS = process.env.SHOTS || ".";

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error" && !/fonts\.googleapis/.test(m.text())) errs.push(m.text()); });
  const fail = msg => { console.error("FAIL: " + msg); process.exitCode = 1; };

  const email = `smoke${Date.now()}@example.com`;
  let r = await page.request.post(BASE + "/api/auth/register", { data: { email, password: "testpass123" } });
  if (!r.ok()) fail("register " + r.status());
  if (process.env.PROMOTE_CMD) require("child_process").execSync(process.env.PROMOTE_CMD.replace("{email}", email));
  await page.request.post(BASE + "/api/auth/login", { data: { email, password: "testpass123" } });

  await page.goto(BASE + "/m", { waitUntil: "networkidle" });
  // new user → onboarding
  await page.waitForSelector("#wizard");
  await page.fill("#f-make", "Toyota"); await page.fill("#f-model", "Camry");
  await page.click("#next");
  await page.fill("#f-zip", "35401"); await page.fill("#f-radius_mi", "75");
  await page.click("#next");
  await page.fill("#f-min_discount_pct", "12"); await page.click("#next");
  await page.click("#gen-topic"); await page.waitForTimeout(400);
  if (!/ntfy/.test(await page.textContent("#topic-out"))) fail("topic not generated");
  await page.fill("#f-name", "Camrys 12%+"); await page.click("#next");
  await page.waitForSelector("article[data-cid]", { timeout: 5000 }).catch(() => fail("no preview matches"));
  await page.screenshot({ path: SHOTS + "/m-onboarding-matches.png", fullPage: true });

  // trusted feed + detail sheet
  await page.click('#tabs button[data-feed="trusted"]');
  await page.waitForSelector("article[data-cid]");
  const titles = await page.$$eval("article .title", els => els.map(e => e.textContent));
  if (!titles.some(t => /8,100|7,600/.test(t))) fail("trusted feed missing demo deal: " + titles);
  if (titles.some(t => /rebuilt|5,200/.test(t))) fail("risky car in trusted feed");
  if (titles.some(t => /6,500/.test(t))) fail("car outside the 75 mi territory shown");
  if (!(await page.textContent("#main")).includes("mi away")) fail("distance not shown");
  await page.screenshot({ path: SHOTS + "/m-trusted.png", fullPage: true });
  await page.click("article [data-open]");
  await page.waitForSelector("#sheet.open h3");
  const sheet = await page.textContent("#sheet-body");
  for (const want of ["Market value", "Why it ranks here", "Comparables used", "Profit estimate", "Price history",
                      "price change", "not completed sales"])
    if (!sheet.includes(want)) fail("detail sheet missing: " + want);
  await page.screenshot({ path: SHOTS + "/m-detail.png", fullPage: true });
  await page.click("#sheet-back");

  // risk tab is labeled
  await page.click('#tabs button[data-feed="risk"]');
  await page.waitForSelector("article[data-cid]");
  if (!(await page.textContent("#main")).includes("branded title")) fail("risk flag not shown");

  if (errs.length) fail("console errors: " + JSON.stringify(errs));
  console.log(process.exitCode ? "SMOKE FAILED" : "SMOKE OK");
  await b.close();
})();
