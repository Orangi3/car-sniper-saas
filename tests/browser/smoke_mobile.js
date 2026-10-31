// tests/browser/smoke_mobile.js — headless browser smoke test of the FlipFirst app (/m).
// Requires a running server seeded by seed_demo.py (see DEPLOY.md / CI).
//   BASE=http://127.0.0.1:8765 SHOTS=/tmp node tests/browser/smoke_mobile.js
const { chromium } = require("playwright");
const BASE = process.env.BASE || "http://127.0.0.1:8765";
const SHOTS = process.env.SHOTS || ".";
// in GitHub Actions a failure is also an ::error annotation, so the reason shows
// on the pull request without opening the job log
const fail = msg => {
  console.error(process.env.GITHUB_ACTIONS ? "::error title=smoke_mobile::" + msg.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A")
                                         : "FAIL: " + msg);
  process.exitCode = 1;
};

(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();
  const errs = [];
  page.on("pageerror", e => errs.push("pageerror: " + e.message));
  page.on("console", m => { if (m.type() === "error") errs.push(m.text()); });
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

  // Analyze: a Discover deal, with my numbers, then the private workspace
  await page.click(".only-mobile[data-analyze]");
  await page.waitForSelector("#az");
  if ((await page.inputValue("#a-asking_price")) !== "7600") fail("Analyze not prefilled from the deal");
  await page.selectOption("#a-title_status", "clean");
  await page.selectOption("#a-condition", "runs_drives");
  await page.click("#az details summary");             // costs start folded away
  await page.fill("#a-c_mechanical", "600");
  await page.click("#a-run");
  await page.waitForSelector("#a-result .decision", { timeout: 8000 }).catch(() => fail("no analysis result"));
  const an = await text("#a-result");
  for (const want of ["Break-even resale", "Maximum offer", "Vehicle identity", "Price vs. market", "Low case",
                      "Comparable vehicles", "Risk factors", "Data used"])
    if (!an.includes(want)) fail("analysis missing: " + want);
  if (!/Strong candidate|Worth investigating/.test(an)) fail("unexpected decision: " + an.slice(0, 200));
  await page.screenshot({ path: SHOTS + "/m-analyze.png", fullPage: true });
  await page.click("#a-save");
  await page.waitForSelector("#o-notes");
  await page.fill("#o-notes", "Tires at 4/32; no codes; title in hand");
  await page.click("#o-notes-save");
  await page.waitForSelector("#o-notes-msg:text-is('Saved.')");     // no waitForFunction: the CSP forbids eval
  await page.fill("#o-purchase_price", "7300"); await page.fill("#o-purchased_on", "2026-10-01");
  await page.fill("#o-sale_price", "9400"); await page.fill("#o-sold_on", "2026-10-21");
  await page.selectOption("#o-stage", "sold");
  await page.click("#o-out-save");
  await page.waitForSelector("#main :text('Realized profit')");
  await page.screenshot({ path: SHOTS + "/m-workspace-deal.png", fullPage: true });

  // a car FlipFirst has no comparables for: no value is invented
  await page.goto(BASE + "/m#/analyze");
  await page.waitForSelector("#az");
  await page.click("#a-clear"); await page.waitForSelector("#az");
  await page.fill("#a-year", "2014"); await page.fill("#a-make", "Ford"); await page.fill("#a-model", "Fusion");
  await page.fill("#a-mileage", "140000"); await page.fill("#a-asking_price", "4200");
  await page.click("#a-run");
  await page.waitForSelector("#a-result .decision");
  const none = await text("#a-result");
  if (!none.includes("Insufficient evidence") || !none.includes("Not enough data to value this car")) fail("no-data car not labeled");
  if (!none.includes("Money in")) fail("no-data car missing the money-in breakdown");
  await page.click("#a-save"); await page.waitForSelector("#o-notes");

  // the workspace list and a side-by-side comparison
  await page.goto(BASE + "/m#/saved"); await page.waitForSelector(".w-pick");
  if (!(await text("#main")).includes("Your track record")) fail("track record not shown after a recorded sale");
  const boxes = await page.$$(".w-pick");
  if (boxes.length !== 2) fail("expected 2 saved deals, got " + boxes.length);
  for (const bx of boxes) await bx.check();
  await page.click("#w-compare"); await page.waitForSelector("table.compare");
  if (!(await text("table.compare")).includes("Break-even resale")) fail("comparison table incomplete");
  await page.screenshot({ path: SHOTS + "/m-compare.png", fullPage: true });

  // six tabs fit a 320 px phone without sideways scrolling
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto(BASE + "/m#/analyze"); await page.waitForSelector("#az");
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (over > 0) fail("page scrolls sideways at 320 px by " + over + " px");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + "/m#/discover/trusted"); await page.waitForSelector("article[data-cid]");

  // FlipFirst sellers tab: seller listings are findable even without a market value
  await page.goto(BASE + "/m#/discover/sellers");
  await page.waitForSelector("#feed .empty, #feed article[data-cid]");
  if (!(await text("#main")).includes("seller-reported")) fail("sellers feed not labeled seller-reported");

  // flagged tab is labeled
  await page.goto(BASE + "/m#/discover/risk");
  await page.waitForSelector("article[data-cid]");
  if (!/branded title/i.test(await text("#main"))) fail("risk flag not shown");

  if (errs.length) fail("console errors: " + JSON.stringify(errs));
  console.log(process.exitCode ? "SMOKE FAILED" : "SMOKE OK");
  await b.close();
})().catch(e => { fail("stopped: " + String(e && e.message || e).split("\n").slice(0, 4).join("\n")); process.exit(1); });
