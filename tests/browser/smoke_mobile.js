// tests/browser/smoke_mobile.js — headless browser smoke test of the FlipFirst app (/m).
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
  page.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
  const fail = msg => { console.error("FAIL: " + msg); process.exitCode = 1; };
  const text = async sel => (await page.textContent(sel)) || "";

  const email = `smoke${Date.now()}@example.com`;
  let r = await page.request.post(BASE + "/api/auth/register", { data: { email, password: "testpass123" } });
  if (!r.ok()) fail("register " + r.status());
  if (process.env.PROMOTE_CMD) require("child_process").execSync(process.env.PROMOTE_CMD.replace("{email}", email));
  await page.request.post(BASE + "/api/auth/login", { data: { email, password: "testpass123" } });

  // new user with no saved search → top deals + an invitation to create a search
  await page.goto(BASE + "/m", { waitUntil: "networkidle" });
  await page.waitForSelector(".page__head h1");
  if (!/#\/discover\/trusted$/.test(page.url())) fail("new user not routed to top deals: " + page.url());
  if (!(await text("#main")).includes("Create a search")) fail("no create-a-search prompt for a new user");

  // guided search setup (4 steps)
  await page.click('a[href="#/alerts/new"]');
  await page.waitForSelector("#wiz");
  await page.fill("#f-make", "Toyota"); await page.fill("#f-model", "Camry");
  await page.click("#next");
  await page.fill("#f-min_discount_pct", "12"); await page.click("#next");
  await page.fill("#f-zip", "35401"); await page.fill("#f-radius_mi", "75"); await page.click("#next");
  await page.fill("#f-name", "Camrys 12%+");
  if (!(await text("#wiz")).includes("12%+ below market")) fail("summary chips missing the discount rule");
  await page.click("#next");
  await page.waitForSelector("article[data-cid]", { timeout: 6000 }).catch(() => fail("no preview matches"));
  if (!(await text(".page__head h1")).includes("Camrys 12%+")) fail("search matches page missing the search name");
  await page.screenshot({ path: SHOTS + "/m-onboarding-matches.png", fullPage: true });

  // delivery channel: create a push topic on the Alerts page
  await page.goto(BASE + "/m#/alerts"); await page.waitForSelector("#gen-topic");
  await page.click("#gen-topic"); await page.waitForSelector("code", { timeout: 5000 }).catch(() => fail("push topic not shown"));
  if (!/ntfy/.test(await text("#main"))) fail("ntfy topic not generated");

  // top deals feed + deal analysis
  await page.goto(BASE + "/m#/discover/trusted");
  await page.waitForSelector("article[data-cid]");
  const titles = await page.$$eval("article .deal__title", els => els.map(e => e.textContent));
  if (!titles.some(t => /8,100|7,600/.test(t))) fail("trusted feed missing demo deal: " + titles);
  if (titles.some(t => /rebuilt|5,200/.test(t))) fail("risky car in trusted feed");
  if (titles.some(t => /6,500/.test(t))) fail("car outside the 75 mi territory shown");
  if (!/\d+ mi\b/.test(await text("#feed"))) fail("distance not shown");
  await page.screenshot({ path: SHOTS + "/m-trusted.png", fullPage: true });
  await page.click("article .deal__title a");
  await page.waitForSelector("#deal-title");
  const detail = await text("#main");
  for (const want of ["Price vs. market", "Estimated profit", "Total invested", "Comparable vehicles", "Risk factors",
                      "Price history", "price change", "not completed sales"])
    if (!detail.includes(want)) fail("deal analysis missing: " + want);
  await page.screenshot({ path: SHOTS + "/m-detail.png", fullPage: true });
  await page.click("[data-back]");

  // flagged tab is labeled
  await page.goto(BASE + "/m#/discover/risk");
  await page.waitForSelector("article[data-cid]");
  if (!/branded title/i.test(await text("#main"))) fail("risk flag not shown");

  if (errs.length) fail("console errors: " + JSON.stringify(errs));
  console.log(process.exitCode ? "SMOKE FAILED" : "SMOKE OK");
  await b.close();
})();
