// tests/browser/ui_flows.js — FlipFirst critical flows in a real browser.
// Needs a server with ALLOW_REGISTRATION=0 and SNIPER_SELLER_SUBMISSIONS=1 over
// a database seeded by seed_demo.py, plus two shell commands (see CI):
//   INVITE_CMD  prints one invitation link        (python -m invites issue …)
//   ADMIN_CMD   makes {email} an admin            (python -c "import auth; …")
//   BASE=http://127.0.0.1:8766 SHOTS=/tmp node tests/browser/ui_flows.js
const { chromium } = require("playwright");
const { execSync } = require("child_process");
const BASE = process.env.BASE || "http://127.0.0.1:8766";
const SHOTS = process.env.SHOTS || ".";
// in GitHub Actions a failure is also an ::error annotation, so the reason shows
// on the pull request without opening the job log
const fail = msg => {
  console.error(process.env.GITHUB_ACTIONS ? "::error title=ui_flows::" + msg.replace(/%/g, "%25").replace(/\r/g, "%0D").replace(/\n/g, "%0A")
                                         : "FAIL: " + msg);
  process.exitCode = 1;
};
const ok = msg => console.log("ok   " + msg);
// waits for text through a locator: older Playwright (CI pins 1.47) runs
// page.waitForFunction predicates through eval, which the app's CSP refuses
const textIn = (page, sel, text = /\S/, timeout) => page.locator(sel, { hasText: text }).waitFor({ timeout });

(async () => {
  const b = await chromium.launch();
  const errs = [];
  const watch = (page, tag) => {
    page.on("pageerror", e => errs.push(`${tag} pageerror: ${e.message}`));
    // 402/403/503 responses are provoked on purpose below (plan gate, reused
    // invitation, no SMTP); anything else logged as an error fails the run
    page.on("console", m => { if (m.type() === "error" && !/Failed to load resource: the server responded with a status of (402|403|503)/.test(m.text())) errs.push(`${tag}: ${m.text()}`); });
    page.on("dialog", d => d.accept());
  };
  const newPage = async (w = 390, h = 844) => {
    const ctx = await b.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
    const p = await ctx.newPage(); watch(p, `${w}px`); return p;
  };

  // ---- 1. invite-only registration ------------------------------------
  const link = execSync(process.env.INVITE_CMD).toString().match(/\/login#invite=[A-Za-z0-9_-]+/);
  if (!link) { fail("INVITE_CMD printed no invitation link"); process.exit(1); }
  const email = `pilot${Date.now()}@example.com`;
  let p = await newPage();
  await p.goto(BASE + "/login");
  await textIn(p, "#switch");
  if (!(await p.textContent("#switch")).includes("invite-only")) fail("login page doesn't explain invite-only access");
  if (await p.$("#switch button")) fail("create-account offered without an invitation");
  await p.goto(BASE + link[0]);
  await p.waitForSelector("#invite-note:not([hidden])");
  if (p.url().includes("invite=")) fail("invitation token left in the address bar");
  await p.fill("#email", email); await p.fill("#password", "pilot-pass-123");
  await p.click("#submit");
  await p.waitForURL(u => u.pathname === "/m", { timeout: 8000 }).catch(() => fail("invite registration didn't reach the app"));
  await p.waitForSelector(".page__head h1");
  ok("invitation creates an account with the user's own password");
  const reuse = await newPage();
  await reuse.goto(BASE + link[0]);
  await reuse.waitForSelector("#invite-note:not([hidden])");
  await reuse.fill("#email", "second@example.com"); await reuse.fill("#password", "pilot-pass-123");
  await reuse.click("#submit");
  await textIn(reuse, "#err");
  if (!/already used/i.test(await reuse.textContent("#err"))) fail("reused invitation not refused: " + await reuse.textContent("#err"));
  else ok("reused invitation refused");
  await reuse.context().close();

  // ---- 2. free plan: browsing works, saving prompts an upgrade ---------
  await p.goto(BASE + "/m#/discover/trusted");
  await p.waitForSelector("article[data-cid]");
  await p.click('article [data-act="save"]');
  await p.waitForSelector(".toast");
  if (!/Lite plan/.test(await p.textContent(".toast"))) fail("saving on Free didn't explain the plan requirement");
  else ok("plan gate explained on save");

  // ---- 3. seller wizard ------------------------------------------------
  await p.goto(BASE + "/m#/sell");
  await p.waitForSelector('a[href="#/sell/new"]');
  await p.click('a[href="#/sell/new"]');
  await p.waitForSelector("#wiz");
  await p.click("#next");                                    // empty step → inline errors, no advance
  if (!(await p.textContent('[data-err="year"]')).length) fail("seller wizard advanced without required fields");
  await p.fill("#s-year", "2015"); await p.fill("#s-mileage", "128000");
  await p.fill("#s-make", "Toyota"); await p.fill("#s-model", "Camry"); await p.fill("#s-trim", "SE");
  await p.click("#next");
  await p.click('input[name="title_status"][value="clean"]');
  await p.click('[data-has="mechanical"][data-val="no"]');
  await p.click('[data-has="cosmetic"][data-val="yes"]');
  await p.fill("#s-cosmetic_issues", "door ding, faded hood");
  await p.click("#next");
  await p.fill("#s-price", "6800"); await p.fill("#s-zip", "35473");
  await p.click("#next");
  await p.waitForSelector("#c-send");
  await p.click("#c-send");                                   // no SMTP in CI → honest message, nothing published
  await textIn(p, "#c-msg");
  if (!/email delivery isn't set up/.test(await p.textContent("#c-msg"))) fail("missing-SMTP state not explained: " + await p.textContent("#c-msg"));
  await p.click("#next");
  const review = await p.textContent("#wiz");
  for (const want of ["2015 Toyota Camry SE", "128,000 mi", "Clean", "None known", "door ding", "$6,800", "35473"])
    if (!review.includes(want)) fail("review step missing: " + want);
  await p.click("#next");                                     // attestations missing → blocked
  if (!(await p.textContent("#werr")).includes("confirm all three")) fail("submitted without seller attestations");
  await p.check("#a1"); await p.check("#a2"); await p.check("#a3");
  await p.screenshot({ path: SHOTS + "/flow-sell-review.png", fullPage: true });
  await p.click("#next");
  await p.waitForSelector("[data-listing]", { timeout: 8000 }).catch(() => fail("listing not shown after submit"));
  if (!/Waiting for email confirmation/.test(await p.textContent("#main"))) fail("unverified listing not shown as pending");
  else ok("seller wizard submits a pending listing");
  await p.click("[data-unlist]");
  await p.waitForSelector("[data-listing]", { state: "detached", timeout: 8000 }).catch(() => fail("listing not removed"));
  ok("seller can withdraw the listing");

  // ---- 4. account settings ---------------------------------------------
  await p.goto(BASE + "/m#/account/territory");
  await p.waitForSelector("#t-zip");
  await p.fill("#t-zip", "35401"); await p.fill("#t-radius", "150");
  await p.click("#terr-form button[type=submit]");
  await textIn(p, "#terr-msg", /^\s*Saved\.\s*$/).catch(() => fail("territory not saved"));
  await p.click('[data-theme-opt="dark"]');
  if (await p.getAttribute("html", "data-theme") !== "dark") fail("dark theme not applied");
  await p.click('[data-theme-opt="system"]');
  await p.click("#assume-form button[type=submit]");
  await textIn(p, "#assume-msg");
  if (!/Lite and Pro/.test(await p.textContent("#assume-msg"))) fail("assumptions plan gate not explained");
  ok("account: territory, theme, plan-gated assumptions");

  // ---- 5. operations console is owner-only -----------------------------
  const r = await p.request.get(BASE + "/admin/ops");
  if (r.status() !== 403) fail("non-admin reached /admin/ops: " + r.status());
  execSync(process.env.ADMIN_CMD.replace("{email}", email));
  await p.goto(BASE + "/admin/ops");
  await p.waitForSelector(".kpis .kpi");
  const ops = await p.textContent("#main");
  for (const want of ["Sources working", "Deal pipeline", "Data sources", "System checks", "Never tested"])
    if (!ops.includes(want)) fail("ops console missing: " + want);
  await p.screenshot({ path: SHOTS + "/flow-ops.png", fullPage: true });
  ok("operations console for admins only");

  // ---- 6. layout sweep: no horizontal overflow at phone/tablet/desktop --
  const cookies = await p.context().cookies();
  for (const w of [375, 768, 1024, 1440]) {
    const q = await newPage(w, 900);
    await q.context().addCookies(cookies);
    await q.goto(BASE + "/m#/discover/trusted"); await q.waitForSelector("article[data-cid]");
    const deal = await q.getAttribute(".deal__title a", "href");
    for (const h of ["#/discover/trusted", "#/discover/risk", deal, "#/saved", "#/alerts", "#/alerts/new",
                     "#/sell", "#/sell/new", "#/account", "OPS"]) {
      await q.goto(h === "OPS" ? BASE + "/admin/ops" : BASE + "/m" + h); await q.waitForTimeout(700);
      const [sw, iw, clipped] = await q.evaluate(() => [document.documentElement.scrollWidth, window.innerWidth,
        // amounts and their labels must never be cut off or run into each other
        // (e.g. "$1,323–$…" or "mkt $13…" in a narrow column, "Evidence" under its value)
        [...document.querySelectorAll(".metric__value, .metric__sub, .kpi__value, .scenario__value, .dl > dt, .dl > dd")]
          .filter(el => el.offsetParent && el.scrollWidth > el.clientWidth + 1).map(el => el.textContent.trim())]);
      if (sw > iw) fail(`${w}px ${h}: horizontal overflow ${sw}>${iw}`);
      if (clipped.length) fail(`${w}px ${h}: clipped text ${JSON.stringify(clipped)}`);
    }
    await q.goto(BASE + "/login"); await q.waitForTimeout(300);
    await q.context().close();
  }
  ok("no horizontal overflow or clipped amounts/labels at 375/768/1024/1440");

  // ---- 7. keyboard: skip link first, visible focus ---------------------
  const k = await newPage(1440, 900);
  await k.context().addCookies(cookies);
  await k.goto(BASE + "/m#/discover/trusted"); await k.waitForSelector("article[data-cid]");
  await k.keyboard.press("Tab");
  if (!(await k.evaluate(() => document.activeElement && document.activeElement.classList.contains("skip-link")))) fail("first Tab doesn't reach the skip link");
  else ok("skip link is the first tab stop");

  if (errs.length) fail("console errors: " + JSON.stringify([...new Set(errs)]));
  console.log(process.exitCode ? "UI FLOWS FAILED" : "UI FLOWS OK");
  await b.close();
})().catch(e => { fail("stopped: " + String(e && e.message || e).split("\n").slice(0, 4).join("\n")); process.exit(1); });
