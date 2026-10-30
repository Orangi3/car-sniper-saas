/* FlipFirst app (/m) — Discover, deal analysis, Saved, Alerts, Sell, Account.
   Hash routes; all data from the existing JSON API; every value rendered
   through FF.html (escaped by default). No framework, no build step. */
"use strict";
(function () {
const {html, raw, setHTML, icon, money, moneyK, num, pct, ago, hoursSince, api, post, toast, $, $$, busy, safeUrl, n} = FF;
const V = FF.ASSET_V;

/* ---------------- vocabulary ---------------- */
const PLAN_NAMES = {free: "Free", starter: "Lite", pro: "Pro"};
const PLAN_RANK = {free: 0, starter: 1, pro: 2};
const FEEDS = [["mine", "For you"], ["trusted", "Top deals"], ["risk", "Flagged"]];
const SORTS = [["rank", "Best opportunity"], ["discount", "Biggest discount"], ["profit", "Highest est. profit"],
               ["price", "Lowest price"], ["newest", "Newest"]];
const EVIDENCE = {confirmed_sales: "completed sales", asking_comparables: "asking prices",
                  historical_incomplete: "older or partial sales", model_estimate: "a model estimate",
                  insufficient: "not enough data"};
const COMP_KIND = {sold: ["Completed sale", "chip--profit"], asking: ["Asking price", ""], historical: ["Older sale", "chip--warn"]};
const FLAGS = {
  scam_signals: ["Scam signals", "danger", "Text or price patterns common in scams. Confirm the seller and never pay before seeing the car."],
  too_good_to_be_true: ["Price looks too good", "danger", "Far below comparable cars — often a scam, a typo or a serious problem."],
  branded_title: ["Branded title", "danger", "A salvage, rebuilt or other branded title is mentioned. Resale value and financing are affected."],
  needs_work: ["Needs work", "danger", "The listing mentions damage or mechanical problems; repairs may exceed the repair allowance."],
  auction_bid: ["Auction bid", "danger", "The price is the current bid in a running auction, not a price you can buy at. The final price is usually higher."],
  description_unchecked: ["Description not checked", "warn", "We only saw the listing's title. Read the full listing for damage or title problems before you go."],
  dealer: ["Dealer listing", "warn", "Sold by a dealer; prices and fees differ from private sales."],
  seller_type_unknown: ["Seller type unknown", "warn", "We couldn't tell whether this is a private seller or a dealer."],
  mileage_unknown: ["Mileage unknown", "warn", "No odometer reading, so comparables can't be matched on mileage."],
  relisted: ["Relisted", "warn", "This car was listed before — ask why it didn't sell."],
  low_confidence: ["Low confidence", "warn", "Few or loosely matched comparables. Treat the value as rough."],
  insufficient_data: ["Not enough data", "warn", "Fewer than 3 usable comparables — no reliable value."],
};
const SOURCE_NAMES = {ebay: "eBay", craigslist: "Craigslist", direct: "FlipFirst seller", marketcheck: "MarketCheck",
  marketcheck_fsbo: "MarketCheck private sellers", demo: "Demo data", bat: "Bring a Trailer", govdeals: "GovDeals",
  hemmings: "Hemmings", marketplace: "Marketplace import", email_imap: "Email digest", cars_and_bids: "Cars & Bids",
  gsa_auctions: "GSA Auctions", publicsurplus: "Public Surplus", copart_live: "Copart", iaa_live: "IAA"};
const STAGES = [["saved", "Saved"], ["contacted", "Contacted"], ["negotiating", "Negotiating"],
                ["going_to_see", "Going to see"], ["bought", "Bought"]];
const STAGE_NAME = Object.fromEntries(STAGES);
const CONF_LEVEL = {high: 3, medium: 2, low: 1, insufficient: 0};
const CONF_LABEL = {high: "High confidence", medium: "Medium confidence", low: "Low confidence", insufficient: "Not enough data"};
const TITLE_STATUS = [["clean", "Clean", "No salvage, rebuilt or other brand on the title."],
                      ["rebuilt", "Rebuilt", "Previously salvage, repaired and re-inspected."],
                      ["salvage", "Salvage", "Declared a total loss; not road-legal until rebuilt."],
                      ["unknown", "Not sure", "Buyers will see that the title status is unconfirmed."]];
const ASSUME_ORDER = ["negotiation_pct", "mechanical", "recon", "transport", "transport_per_mile", "selling_fees",
  "sales_tax_pct", "title_registration", "holding_per_day", "days_to_sell", "misc", "asking_to_sale_pct",
  "dealer_to_private_pct", "negotiation_spread_pct"];

const srcName = s => SOURCE_NAMES[s] || (s ? String(s).replace(/_/g, " ") : "Unknown source");
const planName = p => PLAN_NAMES[p] || (p ? String(p) : "Free");

/* ---------------- state ---------------- */
const S = {me: null, plan: "free", isAdmin: false, territory: null, searches: [], section: "discover",
           route: 0, first: true, scroll: {}, lastList: "#/discover"};
const hasPlan = need => (PLAN_RANK[S.plan] || 0) >= (PLAN_RANK[need] || 0);
const alive = tok => tok === S.route;
const store = {
  get(k) { try { return JSON.parse(sessionStorage.getItem(k) || "null"); } catch (e) { return null; } },
  set(k, v) { try { sessionStorage.setItem(k, JSON.stringify(v)); } catch (e) {} },
  del(k) { try { sessionStorage.removeItem(k); } catch (e) {} },
};

/* ---------------- shell ---------------- */
function setNav(section) {
  S.section = section;
  $$("[data-nav]").forEach(a => a.dataset.nav === section ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current"));
}
function topbar(o) {
  o = o || {};
  $("#topbar").classList.toggle("topbar--root", !o.back);
  const pl = $("[data-plan-link]"); if (pl) setHTML(pl, html`<span>${planName(S.plan)} plan</span>${icon("chevron-right", "sm")}`);
  setHTML($("#topbar"), html`
    ${o.back ? html`<button class="btn btn--ghost btn--icon" type="button" data-back aria-label="Back">${icon("chevron-left")}</button>`
      : html`<a class="brand" href="#/discover" aria-label="FlipFirst — Discover"><img class="brand__mark" src="/static/brand/flipfirst-mark.svg?v=${V}" alt="" width="28" height="28"><span class="brand__word">Flip<b>First</b></span></a>`}
    ${o.title ? html`<div class="topbar__title">${o.title}</div>` : ""}
    <div class="spacer"></div>
    ${o.right || ""}
    <a class="chip chip--accent" href="#/account/plan" aria-label="Your plan: ${planName(S.plan)}">${planName(S.plan)}</a>`);
}
const render = h => setHTML($("#main"), h);
const skeletons = k => raw(Array.from({length: k}, () => '<div class="skel deal-skel" aria-hidden="true"></div>').join(""));
function emptyState(ic, title, text, actions) {
  return html`<div class="empty"><div class="empty__icon">${icon(ic)}</div><h3>${title}</h3>${text ? html`<p>${text}</p>` : ""}${actions || ""}</div>`;
}
function errorBlock(e) {
  return html`<div class="card">${emptyState("alert", "Couldn't load this", e && e.message ? e.message : "Something went wrong.",
    html`<button class="btn btn--primary" type="button" data-retry>${icon("refresh")}Try again</button>`)}</div>`;
}
function planPrompt(e, what) {
  if (!e || e.status !== 402) return false;
  const need = (e.body && e.body.required_plan) || "starter";
  toast(`${what} needs the ${planName(need)} plan.`, {action: {label: "See plans", run: () => go("#/account/plan")}});
  return true;
}
function go(hash) { if (location.hash === hash) route(); else location.hash = hash; }
function goBack() {
  if (history.length > 1 && S.cameFrom) history.back();
  else go(S.lastList || "#/discover");
}

/* ---------------- router ---------------- */
const ROUTES = [
  [/^#\/discover(?:\/(mine|trusted|risk))?$/, viewDiscover],
  [/^#\/deal\/(.+)$/, viewDeal],
  [/^#\/saved$/, viewSaved],
  [/^#\/alerts$/, viewAlerts],
  [/^#\/alerts\/new$/, viewSearchWizard],
  [/^#\/alerts\/search\/(\d+)$/, viewSearchMatches],
  [/^#\/sell$/, viewSell],
  [/^#\/sell\/new$/, viewSellWizard],
  [/^#\/account(?:\/([a-z-]+))?$/, viewAccount],
];
async function route() {
  const h = location.hash || "";
  if (/^#verify=/.test(h)) return verifyLink(h);
  const tok = ++S.route;
  for (const [re, fn] of ROUTES) {
    const m = h.match(re);
    if (!m) continue;
    try { await fn(m, tok); }
    catch (e) { if (alive(tok)) render(errorBlock(e)); }
    if (alive(tok) && !S.first) FF.focusMain();
    S.first = false;
    return;
  }
  location.replace(S.searches.length ? "#/discover/mine" : "#/discover/trusted");
}
window.addEventListener("hashchange", e => {
  try { const old = new URL(e.oldURL).hash; S.scroll[old] = window.scrollY; S.cameFrom = old; } catch (x) {}
  route();
});
if ("scrollRestoration" in history) history.scrollRestoration = "manual";
function restoreScroll() {
  const y = S.scroll[location.hash];
  window.scrollTo(0, y || 0);
}

/* ---------------- deal building blocks ---------------- */
function meter(conf, extra, compact) {
  const lvl = CONF_LEVEL[conf] != null ? CONF_LEVEL[conf] : 0;
  const label = CONF_LABEL[conf] || "Not valued";
  if (compact) {
    const word = {high: "High", medium: "Medium", low: "Low", insufficient: "No data"}[conf] || "Not valued";
    return html`<span class="meter" data-level="${lvl}" title="${label}${extra ? " · " + extra : ""}"><span class="meter__bars" aria-hidden="true"><i></i><i></i><i></i></span><span>${word}${conf && conf !== "insufficient" ? html`<span class="hide-narrow"> confidence</span>` : ""}${extra ? html` <span class="muted">· ${extra}</span>` : ""}</span></span>`;
  }
  return html`<span class="meter" data-level="${lvl}"><span class="meter__bars" aria-hidden="true"><i></i><i></i><i></i></span>${label}${extra ? html` <span class="muted">· ${extra}</span>` : ""}</span>`;
}
function compsLabel(d, short) {
  const k = n(d.comp_n);
  if (!k) return "";
  if (short) return `${k} ${d.evidence_class === "confirmed_sales" ? "sales" : "comps"}`;
  const what = {confirmed_sales: "completed sales", asking_comparables: "asking-price comps",
                historical_incomplete: "older sales"}[d.evidence_class] || "comps";
  return `${k} ${what}`;
}
function profitRange(p) {
  if (!p) return null;
  const lo = p.conservative != null ? p.conservative : p.base, hi = p.optimistic != null ? p.optimistic : p.base;
  if (lo == null) return null;
  const sep = lo < 0 ? " to " : "–";
  const k = v => Math.abs(v) >= 1000 ? (v < 0 ? "−$" : "$") + (Math.abs(v) / 1000).toFixed(1).replace(/\.0$/, "") + "k" : money(v);
  const single = lo === hi || hi == null;
  return {lo, hi, base: p.base, cls: lo > 0 ? "pos" : (hi > 0 ? "" : "neg"),
          text: single ? money(lo) : `${money(lo)}${sep}${money(hi)}`,
          short: single ? k(lo) : `${k(lo)}${sep}${k(hi)}`};
}
function flagChips(flags, max) {
  const fs = (flags || []).filter(f => FLAGS[f]);
  const shown = max ? fs.slice(0, max) : fs;
  return html`${shown.map(f => html`<span class="chip chip--${FLAGS[f][1] === "danger" ? "danger" : "warn"}">${FLAGS[f][0]}</span>`)}${max && fs.length > max ? html`<span class="chip">+${fs.length - max}</span>` : ""}`;
}
function placeLabel(d) {
  const where = d.location || d.state || "";
  const dist = n(d.distance_mi) != null ? `${Math.round(d.distance_mi)} mi` : "";
  return where && dist ? `${where} · ${dist}` : (where || dist);
}
function freshness(d) {
  const h = hoursSince(d.first_seen_at);
  return h == null ? "" : h < 24 ? "ok" : h < 24 * 7 ? "warn" : "";
}
const isSaved = d => d.user_status && d.user_status !== "passed";

function dealCard(d, opts) {
  opts = opts || {};
  const cid = d.composite_id;
  const img = safeUrl(d.image), link = safeUrl(d.url);
  const drop = n(d.previous_price) != null && n(d.previous_price) > n(d.price);
  const pr = profitRange(d.profit);
  const disc = n(d.discount_pct);
  const saved = isSaved(d);
  const fr = freshness(d);
  return html`<article class="deal" data-cid="${cid}">
    <div class="deal__top">
      <div class="thumb">${img ? html`<img src="${img}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" width="92" height="69">` : html`${icon("car")}<span class="sr-only">No photo</span>`}</div>
      <div class="deal__id">
        <h2 class="deal__title"><a href="#/deal/${encodeURIComponent(cid)}">${d.title || [d.year, d.make, d.model].filter(Boolean).join(" ")}</a></h2>
        <div class="deal__meta">
          <span>${icon("gauge")}${d.odometer ? num(d.odometer) + " mi" : "Mileage unknown"}</span>
          ${placeLabel(d) ? html`<span>${icon("pin")}${placeLabel(d)}</span>` : ""}
          <span title="First seen ${d.first_seen_at || ""}">${fr ? html`<i class="dot dot--${fr}" aria-hidden="true"></i>` : icon("clock")}${ago(d.first_seen_at) || "—"}</span>
          <span>${srcName(d.source)}</span>
        </div>
      </div>
    </div>
    <div class="deal__metrics">
      <div class="metric"><div class="metric__label">Asking</div><div class="metric__value">${money(d.price)}</div>
        <div class="metric__sub">${drop ? html`was <s>${money(d.previous_price)}</s>` : (d.event === "price_drop" ? "price drop" : " ")}</div></div>
      <div class="metric"><div class="metric__label">Est. profit</div><div class="metric__value ${pr ? pr.cls : ""}">${pr ? html`<span class="full">${pr.text}</span><span class="compact">${pr.short}</span>` : "—"}</div>
        <div class="metric__sub">${pr ? "after est. costs" : "no valuation"}</div></div>
      <div class="metric"><div class="metric__label">Below mkt</div><div class="metric__value ${disc > 0 ? "pos" : ""}">${disc != null ? pct(disc) : "—"}</div>
        <div class="metric__sub">${n(d.value_mid) ? "mkt " + moneyK(d.value_mid) : " "}</div></div>
    </div>
    <div class="deal__foot">
      <div class="deal__signals">${meter(d.confidence, compsLabel(d, true), true)}${flagChips(d.risk_flags, 1)}
        ${d.event === "price_drop" ? html`<span class="chip chip--profit">${icon("trend-down")}Price drop</span>` : ""}
        ${opts.search && d.search_name ? html`<span class="chip chip--accent">${d.search_name}</span>` : ""}</div>
      <div class="deal__actions">
        ${opts.stage ? stageSelect(d) : html`<button class="btn btn--ghost btn--icon btn--sm${saved ? " is-on" : ""}" type="button" data-act="${saved ? "unsave" : "save"}" aria-pressed="${saved ? "true" : "false"}" aria-label="${saved ? "Saved — remove from saved" : "Save deal"}" title="${saved ? "Saved" : "Save"}">${icon(saved ? "bookmark-fill" : "bookmark")}</button>`}
        <button class="btn btn--ghost btn--icon btn--sm" type="button" data-act="dismiss" aria-label="Dismiss deal" title="Dismiss">${icon("x")}</button>
        ${link ? html`<a class="btn btn--ghost btn--icon btn--sm" href="${link}" target="_blank" rel="noopener noreferrer" aria-label="Open original listing (new tab)" title="Original listing">${icon("external")}</a>` : ""}
      </div>
    </div>
  </article>`;
}
function stageSelect(d) {
  return html`<label class="sr-only" for="stage-${d.composite_id}">Stage</label>
    <select class="select" style="min-height:34px;height:34px;padding:0 30px 0 10px;font-size:13px;width:auto" id="stage-${d.composite_id}" data-stage>
      ${STAGES.map(([k, l]) => html`<option value="${k}"${d.user_status === k ? raw(" selected") : ""}>${l}</option>`)}
    </select>`;
}
function sortDeals(rows, key) {
  const r = rows.slice();
  const desc = f => (a, b) => { const x = f(a), y = f(b); if (x == null && y == null) return 0; if (x == null) return 1; if (y == null) return -1; return y - x; };
  if (key === "discount") r.sort(desc(d => n(d.discount_pct)));
  else if (key === "profit") r.sort(desc(d => d.profit ? n(d.profit.base) : null));
  else if (key === "price") r.sort(desc(d => n(d.price) == null ? null : -n(d.price)));
  else if (key === "newest") r.sort(desc(d => { const t = new Date(d.first_seen_at || "").getTime(); return isNaN(t) ? null : t; }));
  else r.sort(desc(d => d.opportunity ? n(d.opportunity.risk_adjusted_profit) : null));
  return r;
}

/* ---------------- deal actions ---------------- */
const ACT_ERR = "Saving and organizing deals";
async function dealAction(cid, act, btn) {
  const card = btn && btn.closest("[data-cid]");
  try {
    if (act === "save") {
      busy(btn, true); await post(`/api/deals/${encodeURIComponent(cid)}/action`, {action: "save"}); busy(btn, false);
      if (btn) { btn.classList.add("is-on"); btn.dataset.act = "unsave"; btn.setAttribute("aria-pressed", "true"); btn.setAttribute("aria-label", "Saved — remove from saved"); setHTML(btn, icon("bookmark-fill")); }
      toast("Saved to your deals.", {kind: "ok", action: {label: "View", run: () => go("#/saved")}});
    } else if (act === "unsave") {
      busy(btn, true); await post(`/api/deals/${encodeURIComponent(cid)}/action`, {action: "clear"}); busy(btn, false);
      if (S.section === "saved" && card) { card.classList.add("is-leaving"); setTimeout(() => { card.remove(); }, 200); }
      else if (btn) { btn.classList.remove("is-on"); btn.dataset.act = "save"; btn.setAttribute("aria-pressed", "false"); btn.setAttribute("aria-label", "Save deal"); setHTML(btn, icon("bookmark")); }
      toast("Removed from saved.");
    } else if (act === "dismiss") {
      busy(btn, true); await post(`/api/deals/${encodeURIComponent(cid)}/action`, {action: "dismiss"}); busy(btn, false);
      if (card && card.classList.contains("deal")) { card.classList.add("is-leaving"); setTimeout(() => card.remove(), 200); }
      else go(S.lastList || "#/discover");
      toast("Dismissed — it won't show again.", {action: {label: "Undo", run: async () => {
        try { await post(`/api/deals/${encodeURIComponent(cid)}/action`, {action: "clear"}); route(); } catch (e) { toast(e.message, {kind: "bad"}); }
      }}});
    } else if (act === "contacted") {
      busy(btn, true); await post(`/api/deals/${encodeURIComponent(cid)}/action`, {action: "contacted"}); busy(btn, false);
      toast("Marked as contacted. Track it under Saved.", {kind: "ok"});
    }
  } catch (e) {
    busy(btn, false);
    if (!planPrompt(e, ACT_ERR)) toast(e.message, {kind: "bad"});
  }
}

/* ---------------- Discover ---------------- */
async function viewDiscover(m, tok) {
  setNav("discover"); topbar();
  const feed = m[1] || (S.searches.length ? "mine" : "trusted");
  S.lastList = "#/discover/" + feed;
  const sortKey = store.get("ff-sort-" + feed) || (feed === "mine" ? "newest" : "rank");
  const terr = S.territory || {};
  render(html`
    <div class="page__head">
      <div><h1>Discover</h1>
        <p>${terr.home_zip ? html`Deals within ${terr.radius_mi || "—"} mi of ${terr.home_zip} · <a href="#/account/territory">Change</a>`
          : html`Showing all areas · <a href="#/account/territory">Set your buying area</a>`}</p></div>
    </div>
    <div class="toolbar">
      <div class="seg" role="tablist" aria-label="Deal feeds">
        ${FEEDS.map(([k, l]) => html`<a class="seg__btn" role="tab" href="#/discover/${k}" aria-selected="${k === feed ? "true" : "false"}">${l}<span class="seg__count" data-count="${k}"></span></a>`)}
      </div>
      <div class="toolbar__sort"><label class="small muted" for="sort">Sort</label>
        <select class="select" id="sort">${SORTS.map(([k, l]) => html`<option value="${k}"${k === sortKey ? raw(" selected") : ""}>${l}</option>`)}</select></div>
    </div>
    ${!S.searches.length ? html`<div class="card card--pad" style="margin-bottom:var(--s4);display:flex;gap:var(--s4);align-items:center;flex-wrap:wrap">
        <div class="empty__icon" style="margin:0;width:44px;height:44px">${icon("bell")}</div>
        <div style="flex:1 1 220px;min-width:0"><h3 style="font-size:var(--fs-md)">Get alerted the moment a deal appears</h3>
          <p class="small muted">Tell FlipFirst what you buy, where, and what counts as a deal. Matches arrive as push alerts.</p></div>
        <a class="btn btn--primary" href="#/alerts/new">${icon("plus")}Create a search</a></div>` : ""}
    ${feed === "risk" ? html`<div class="notice notice--warn" style="margin-bottom:var(--s4)">${icon("alert")}<div>These listings have <strong>risk flags</strong> — branded titles, mechanical problems, scam signals or weak comparables. Shown for transparency; verify everything before acting.</div></div>` : ""}
    <div id="feed" class="feed" aria-busy="true">${skeletons(4)}</div>
    <p class="xsmall muted" style="margin-top:var(--s6);text-align:center">Values and profits are estimates from comparable listings, not guarantees. Inspect every car and check its title before you buy.</p>`);
  $("#sort").onchange = e => { store.set("ff-sort-" + feed, e.target.value); paint(); };
  let rows;
  try { rows = await api(`/api/deals?feed=${feed}&limit=50`); }
  catch (e) { if (alive(tok)) setHTML($("#feed"), errorBlock(e)); return; }
  if (!alive(tok)) return;
  function paint() {
    if (!rows) return;
    const box = $("#feed"); box.setAttribute("aria-busy", "false");
    const sorted = sortDeals(rows, $("#sort").value);
    const c = $(`[data-count="${feed}"]`); if (c) c.textContent = rows.length ? String(rows.length) : "";
    if (!rows.length) return setHTML(box, html`<div class="card" style="grid-column:1/-1">${feedEmpty(feed)}</div>`);
    setHTML(box, html`${sorted.map(d => dealCard(d, {search: feed === "mine"}))}`);
  }
  paint();
  restoreScroll();
  if (!rows.length && feed !== "mine") sourceNote();
}
function feedEmpty(feed) {
  if (feed === "mine") return emptyState("bell", "No matches yet",
    S.searches.length ? "Matches appear here when a new listing meets one of your saved searches. You'll get an alert too."
      : "Create a saved search and matching listings will appear here — and on your phone.",
    html`<div class="row" style="justify-content:center;flex-wrap:wrap">${S.searches.length ? "" : html`<a class="btn btn--primary" href="#/alerts/new">${icon("plus")}Create a search</a>`}<a class="btn" href="#/discover/trusted">Browse top deals</a></div>`);
  if (feed === "trusted") return emptyState("discover", "No top deals right now",
    "Top deals are at least 10% below market with medium or high valuation confidence and no risk flags. New listings are checked continuously.", raw('<div id="src-note"></div>'));
  return emptyState("shield", "Nothing flagged right now", "Risk-flagged listings that are still priced well below market show up here.");
}
async function sourceNote() {
  try {
    const list = await api("/api/diagnostics");
    const box = $("#src-note"); if (!box) return;
    const ok = list.filter(s => s.status === "healthy").length;
    setHTML(box, html`<p class="small muted" style="margin-top:var(--s4)">${ok ? `${ok} data source${ok > 1 ? "s" : ""} connected and healthy.` : "No live data sources are connected yet, so there's little to show."}</p>`);
  } catch (e) {}
}

/* ---------------- Deal analysis ---------------- */
async function viewDeal(m, tok) {
  const cid = decodeURIComponent(m[1]);
  setNav(S.section === "saved" || S.section === "alerts" ? S.section : "discover");
  topbar({back: true, title: "Deal analysis"});
  render(html`<div class="split"><div class="stack-lg"><div class="skel" style="height:280px;border-radius:16px"></div><div class="skel" style="height:240px;border-radius:16px"></div></div><div class="skel only-lg" style="height:320px;border-radius:16px"></div></div>`);
  let d;
  try { d = await api("/api/deals/" + encodeURIComponent(cid)); }
  catch (e) {
    if (!alive(tok)) return;
    if (e.status === 410 || e.status === 404) return render(html`<div class="card">${emptyState("clock", e.status === 410 ? "This listing may be gone" : "Listing not found", e.message, html`<a class="btn btn--primary" href="${S.lastList}">Back to deals</a>`)}</div>`);
    throw e;
  }
  if (!alive(tok)) return;
  render(dealDetail(d));
  window.scrollTo(0, 0);
}

function dealDetail(d) {
  const cid = d.composite_id;
  const img = safeUrl(d.image), link = safeUrl(d.url);
  const pr = profitRange(d.profit);
  const disc = n(d.discount_pct);
  const saved = isSaved(d);
  const drop = n(d.previous_price) != null && n(d.previous_price) > n(d.price);
  const p = d.profit_detail;
  const title = d.title || [d.year, d.make, d.model].filter(Boolean).join(" ");
  const keyMetrics = html`<div class="metrics-box"><div class="deal__metrics" style="border-radius:var(--r-md);border:1px solid var(--line)">
      <div class="metric"><div class="metric__label">Asking</div><div class="metric__value">${money(d.price)}</div><div class="metric__sub">${drop ? html`was <s>${money(d.previous_price)}</s>` : " "}</div></div>
      <div class="metric"><div class="metric__label">Est. profit</div><div class="metric__value ${pr ? pr.cls : ""}">${pr ? html`<span class="full">${pr.text}</span><span class="compact">${pr.short}</span>` : "—"}</div><div class="metric__sub">${pr ? "after est. costs" : "no valuation"}</div></div>
      <div class="metric"><div class="metric__label">Below mkt</div><div class="metric__value ${disc > 0 ? "pos" : ""}">${disc != null ? pct(disc) : "—"}</div><div class="metric__sub">${n(d.value_mid) ? "mkt " + moneyK(d.value_mid) : " "}</div></div>
    </div></div>`;
  const actions = html`
    ${link ? html`<a class="btn btn--primary btn--lg btn--block" href="${link}" target="_blank" rel="noopener noreferrer">${icon("external")}Open original listing</a>` : ""}
    <div class="row" style="margin-top:var(--s2)">
      ${saved ? stageSelect(d) : html`<button class="btn btn--block" type="button" data-act="save" data-cid="${cid}">${icon("bookmark")}Save</button>`}
      <button class="btn btn--block" type="button" data-act="contacted" data-cid="${cid}">${icon("send")}Contacted</button>
      <button class="btn btn--icon" type="button" data-act="dismiss" data-cid="${cid}" aria-label="Dismiss deal" title="Dismiss">${icon("x")}</button>
    </div>`;
  return html`
  <div class="split" data-cid="${cid}">
    <div class="stack-lg">
      <section class="card" aria-labelledby="deal-title">
        ${img ? html`<img class="hero-img" src="${img}" alt="Listing photo of ${title}" referrerpolicy="no-referrer">`
          : html`<div class="hero-ph"><div style="text-align:center">${icon("car", "lg")}<div class="xsmall" style="margin-top:4px">No photo in the listing</div></div></div>`}
        <div class="card__body stack">
          <div class="chips"><span class="chip">${srcName(d.source)}</span>${drop ? html`<span class="chip chip--profit">${icon("trend-down")}Price dropped ${money(n(d.previous_price) - n(d.price))}</span>` : ""}${d.relist_of ? html`<span class="chip chip--warn">Relisted</span>` : ""}${saved ? html`<span class="chip chip--accent">${STAGE_NAME[d.user_status] || "Saved"}</span>` : ""}</div>
          <h1 id="deal-title" style="font-size:var(--fs-2xl)">${title}</h1>
          <div class="deal__meta" style="font-size:var(--fs-sm)">
            <span>${icon("gauge")}${d.odometer ? num(d.odometer) + " mi" : "Mileage unknown"}</span>
            ${placeLabel(d) ? html`<span>${icon("pin")}${placeLabel(d)}${n(d.distance_mi) != null ? " away" : ""}</span>` : ""}
            <span>${icon("clock")}First seen ${ago(d.first_seen_at) || "—"}</span>
            ${d.last_seen_at ? html`<span>${icon("refresh")}Confirmed ${ago(d.last_seen_at)}</span>` : ""}
          </div>
          ${keyMetrics}
          ${d.provenance ? html`<div class="notice notice--info">${icon("info")}<div>${d.provenance}${d.seller_contact ? (d.seller_contact.available
              ? html` <br><strong>Seller:</strong> <a href="mailto:${d.seller_contact.email}">${d.seller_contact.email}</a>`
              : html` <br>${d.seller_contact.why}.`) : ""}</div></div>` : ""}
        </div>
      </section>

      ${marketSection(d)}
      ${profitSection(d, p)}
      ${compsSection(d)}
      ${riskSection(d)}
      ${(d.price_history || []).length > 1 ? html`<section class="card"><div class="card__head"><h2>${icon("history")}Price history</h2></div>
        <div class="card__body"><table class="table">${d.price_history.map(h => html`<tr><td>${String(h.event || "").replace(/_/g, " ")}<span class="sub">${new Date(h.observed_at).toLocaleString()}</span></td><td class="r"><b>${money(h.price)}</b></td></tr>`)}</table></div></section>` : ""}
      <section class="card"><div class="card__head"><h2>${icon("file")}Listing description</h2><span class="xsmall muted">${d.source === "direct" ? "seller-reported" : "from the listing"}</span></div>
        <div class="card__body"><p class="small" style="white-space:pre-line;color:var(--ink-2)">${d.description || "No description provided."}</p></div></section>
      ${p ? html`<section class="card"><div class="card__head"><h2>${icon("info")}Assumptions &amp; limitations</h2></div>
        <div class="card__body small stack"><ul style="margin:0;padding-left:18px;color:var(--ink-2)">${p.explanation.map(x => html`<li>${x}</li>`)}</ul>
        <p class="muted">${p.disclaimer} FlipFirst doesn't inspect cars or verify titles, ownership or vehicle history.</p>
        <a href="#/account/assumptions">${icon("sliders", "sm")} Change my cost assumptions</a></div></section>` : ""}
      <div class="only-mobile-pad" aria-hidden="true"></div>
    </div>
    <aside class="split__aside only-lg" aria-label="Deal summary">
      <div class="card card--pad stack">
        <div class="eyebrow">Deal summary</div>
        <div class="row row--between"><span class="muted small">Asking price</span><b class="num" style="font-size:var(--fs-2xl)">${money(d.price)}</b></div>
        <div class="row row--between"><span class="muted small">Market value</span><b class="num">${n(d.value_mid) ? `${moneyK(d.value_low || d.value_mid)}–${moneyK(d.value_high || d.value_mid)}` : "—"}</b></div>
        <div class="row row--between"><span class="muted small">Est. profit</span><b class="num ${pr ? pr.cls : ""}">${pr ? pr.text : "—"}</b></div>
        <div class="row row--between"><span class="muted small">Below market</span><b class="num ${disc > 0 ? "pos" : ""}">${disc != null ? pct(disc) : "—"}</b></div>
        <div>${meter(d.confidence, compsLabel(d))}</div>
        <hr class="sep" style="margin:var(--s2) 0">
        ${actions}
        <p class="xsmall muted">Estimates, not guarantees. Inspect the car and check the title before you buy.</p>
      </div>
    </aside>
  </div>
  <div class="actionbar" data-cid="${cid}">
    ${link ? html`<a class="btn btn--primary" href="${link}" target="_blank" rel="noopener noreferrer">${icon("external")}Open listing</a>` : ""}
    ${saved ? stageSelect(d) : html`<button class="btn" type="button" data-act="save" data-cid="${cid}">${icon("bookmark")}Save</button>`}
    <button class="btn btn--icon" type="button" data-act="contacted" data-cid="${cid}" aria-label="Mark as contacted" title="Mark contacted">${icon("send")}</button>
    <button class="btn btn--icon" type="button" data-act="dismiss" data-cid="${cid}" aria-label="Dismiss deal" title="Dismiss">${icon("x")}</button>
  </div>`;
}

function marketSection(d) {
  const lo = n(d.value_low), hi = n(d.value_high), mid = n(d.value_mid), ask = n(d.price);
  let bar = "";
  if (mid && ask) {
    const a = Math.min(ask, lo || mid), b = Math.max(ask, hi || mid);
    const pad = (b - a) * 0.12 || b * 0.05;
    const min = a - pad, max = b + pad, at = v => ((v - min) / (max - min) * 100).toFixed(2) + "%";
    bar = html`<div class="pvm" role="img" aria-label="Asking ${money(ask)} against an estimated market range of ${money(lo || mid)} to ${money(hi || mid)}">
        <div class="pvm__track"></div>
        <div class="pvm__band" style="left:${at(lo || mid)};width:calc(${at(hi || mid)} - ${at(lo || mid)})"></div>
        <div class="pvm__mid" style="left:${at(mid)}"></div>
        <div class="pvm__ask${ask > mid ? " is-over" : ""}" style="left:${at(ask)}"></div>
      </div>
      <div class="pvm__legend"><span class="key"><i style="background:var(--profit)"></i>Asking <b>${money(ask)}</b></span>
        <span class="key"><i style="background:var(--accent-soft);border:1px solid var(--accent)"></i>Market <b>${money(lo || mid)}–${money(hi || mid)}</b></span></div>`;
  }
  const ev = d.evidence_class;
  return html`<section class="card" aria-labelledby="sec-market">
    <div class="card__head"><h2 id="sec-market">${icon("chart")}Price vs. market</h2>${meter(d.confidence)}</div>
    <div class="card__body">
      ${mid ? html`
        <div class="row row--wrap" style="gap:var(--s6)">
          <div class="metric"><div class="metric__label">Estimated market value</div><div class="metric__value" style="font-size:var(--fs-xl)">${money(mid)}</div><div class="metric__sub">range ${money(lo || mid)}–${money(hi || mid)}</div></div>
          <div class="metric"><div class="metric__label">Discount to market</div><div class="metric__value ${n(d.discount_pct) > 0 ? "pos" : ""}" style="font-size:var(--fs-xl)">${pct(d.discount_pct)}</div><div class="metric__sub">${money(mid - ask)} under the midpoint</div></div>
        </div>
        ${bar}
        <p class="small muted" style="margin-top:var(--s3)">Based on ${n(d.comp_n) || 0} comparable ${n(d.comp_n) === 1 ? "listing" : "listings"} (${EVIDENCE[ev] || "unknown evidence"}).
          ${ev === "asking_comparables" ? "Asking prices are what sellers ask, not what cars sold for — resale estimates are discounted for that." : ""}
          ${ev === "confirmed_sales" ? "Values come from completed sales." : ""}</p>
        ${(d.confidence_reasons || []).length ? html`<details style="margin-top:var(--s3)"><summary class="small" style="cursor:pointer;font-weight:600">Why ${(CONF_LABEL[d.confidence] || "this confidence").toLowerCase()}?</summary>
          <ul class="small" style="margin:var(--s2) 0 0;padding-left:18px;color:var(--ink-2)">${d.confidence_reasons.map(r => html`<li>${r}</li>`)}</ul></details>` : ""}`
      : html`<div class="notice notice--warn">${icon("alert")}<div><strong>Not enough data to value this car.</strong> Fewer than 3 usable comparables were found, so there's no market estimate or profit estimate. Judge it on your own research.</div></div>`}
    </div></section>`;
}

function profitSection(d, p) {
  if (!p) return html`<section class="card"><div class="card__head"><h2>${icon("dollar")}Estimated profit</h2></div>
    <div class="card__body"><p class="small muted">No valuation, so no profit estimate.</p></div></section>`;
  const scen = p.scenarios || {};
  const keys = ["conservative", "base", "optimistic"].filter(k => scen[k]);
  const base = scen.base || scen[keys[0]];
  const c = p.costs || {};
  const sum = ks => ks.reduce((t, k) => t + (n(c[k]) || 0), 0);
  const repairs = sum(["mechanical", "recon"]);
  const feesKeys = ["selling_fees", "transport", "transport_per_mile", "title_registration", "sales_tax_pct", "misc"];
  const fees = sum(feesKeys);
  const assum = Object.fromEntries((p.assumptions || []).map(a => [a.key, a.value]));
  const feeParts = [["selling_fees", "selling fees"], ["transport", "transport"], ["transport_per_mile", "transport (distance)"],
    ["title_registration", "title & registration"], ["sales_tax_pct", "sales tax"], ["misc", "other"]]
    .filter(([k]) => n(c[k])).map(([k, l]) => `${l} ${money(c[k])}`).join(" · ");
  const mid = n(d.value_mid);
  const haircut = mid && base ? Math.round((1 - base.resale / mid) * 1000) / 10 : null;
  const opp = d.opportunity || {};
  return html`<section class="card" aria-labelledby="sec-profit">
    <div class="card__head"><h2 id="sec-profit">${icon("dollar")}Estimated profit</h2><a class="small" href="#/account/assumptions">Edit assumptions</a></div>
    <div class="card__body stack-lg">
      ${keys.length > 1 ? html`<div class="scenarios">${keys.map(k => html`<div class="scenario${k === "base" ? " is-base" : ""}">
          <div class="scenario__name">${k[0].toUpperCase() + k.slice(1)}</div>
          <div class="scenario__value ${scen[k].profit > 0 ? "pos" : "neg"}">${money(scen[k].profit)}</div>
          <div class="scenario__sub">${scen[k].roi_pct != null ? scen[k].roi_pct + "% ROI" : ""}${scen[k].days_to_sell ? ` · ~${Math.round(scen[k].days_to_sell)} days` : ""}</div></div>`)}</div>`
        : html`<div class="notice">${icon("info")}<div>Showing the base case. <a href="#/account/plan">Pro</a> adds conservative and optimistic scenarios.</div></div>`}
      <div>
        <div class="eyebrow" style="margin-bottom:var(--s2)">Base case breakdown</div>
        <dl class="dl">
          <dt>Purchase price<small>${n(assum.negotiation_pct) ? `${assum.negotiation_pct}% under asking` : "at asking price"}</small></dt><dd>${money(base.purchase)}</dd>
          <dt>Repairs &amp; reconditioning<small>${[n(c.mechanical) ? `mechanical allowance ${money(c.mechanical)}` : "", n(c.recon) ? `cosmetic ${money(c.recon)}` : ""].filter(Boolean).join(" · ") || "none entered"}</small></dt><dd>${money(repairs)}</dd>
          <dt>Fees, transport &amp; taxes<small>${feeParts || "none entered"}</small></dt><dd>${money(fees)}</dd>
          <dt>Holding cost<small>${base.days_to_sell ? `~${Math.round(base.days_to_sell)} days` : ""}${n(assum.holding_per_day) ? ` at ${money(assum.holding_per_day)}/day` : ""}</small></dt><dd>${money(base.holding)}</dd>
          <dt class="total">Total invested</dt><dd class="total">${money(base.invested)}</dd>
          <dt>Estimated resale<small>${haircut ? `market value ${money(mid)} less ${haircut}% — asking prices aren't sale prices` : "market value from completed sales"}</small></dt><dd>${money(base.resale)}</dd>
          <dt class="total">Estimated profit</dt><dd class="total ${base.profit > 0 ? "pos" : "neg"}">${money(base.profit)}</dd>
        </dl>
      </div>
      ${p.not_included && p.not_included.length ? html`<div class="notice notice--warn">${icon("alert")}<div><strong>Not included:</strong> ${p.not_included.join("; ")}. <a href="#/account/assumptions">Add them</a></div></div>` : ""}
      ${opp.risk_adjusted_profit != null ? html`<div>
        <div class="row row--between"><div><div class="eyebrow">Risk-adjusted opportunity</div>
          <p class="xsmall muted">Conservative profit ${money(opp.conservative_profit)} × ${opp.multiplier} for evidence, confidence, condition, freshness, distance and liquidity. Used to rank deals.</p></div>
          <b class="num" style="font-size:var(--fs-xl)">${money(opp.risk_adjusted_profit)}</b></div>
        <details style="margin-top:var(--s2)"><summary class="small" style="cursor:pointer;font-weight:600">How it's ranked</summary>
          <table class="table" style="margin-top:var(--s2)">${(opp.factors || []).map(f => html`<tr><td>${String(f.name)}<span class="sub">${f.why}</span></td><td class="r">×${f.value}</td></tr>`)}</table></details></div>` : ""}
    </div></section>`;
}

function compsSection(d) {
  const comps = ((d.evidence || {}).comps || []).slice(0, 12);
  if (!comps.length) return "";
  return html`<section class="card" aria-labelledby="sec-comps">
    <div class="card__head"><h2 id="sec-comps">${icon("layers")}Comparable vehicles</h2><span class="xsmall muted">${comps.length} shown</span></div>
    <div class="card__body">
      <ul class="list" style="list-style:none;margin:0;padding:0">${comps.map(c => {
        const u = safeUrl(c.url); const kind = COMP_KIND[c.kind] || [c.kind || "Comparable", ""];
        const adj = n(c.adjusted_value) && n(c.adjusted_value) !== n(c.price);
        return html`<li class="list__row">
          <div class="list__main"><div class="list__title">${u ? html`<a href="${u}" target="_blank" rel="noopener noreferrer">${c.title || srcName(c.source)}</a>` : (c.title || srcName(c.source))}</div>
            <div class="list__sub" style="display:flex;flex-wrap:wrap;gap:4px 10px;margin-top:4px;align-items:center">
              <span class="chip ${kind[1]}">${kind[0]}</span>
              ${c.odometer ? html`<span>${num(c.odometer)} mi</span>` : ""}
              ${n(c.distance_mi) != null ? html`<span>${Math.round(c.distance_mi)} mi away</span>` : ""}
              ${c.sale_date ? html`<span>sold ${c.sale_date}</span>` : ""}
              ${n(c.match_quality) != null ? html`<span>match ${c.match_quality}/100</span>` : ""}
              ${(c.match_notes || []).slice(0, 2).map(x => html`<span>${x}</span>`)}</div></div>
          <div style="text-align:right;flex:0 0 auto"><b class="num">${money(c.price)}</b>${adj ? html`<div class="list__sub num">adj. ${money(c.adjusted_value)}</div>` : ""}</div></li>`;
      })}</ul>
      ${d.evidence_class === "asking_comparables" ? html`<p class="xsmall muted" style="margin-top:var(--s3)">“Asking price” comparables are current asks from other sellers, not completed sales.</p>` : ""}
    </div></section>`;
}

function riskSection(d) {
  const flags = (d.risk_flags || []).filter(f => FLAGS[f]);
  return html`<section class="card" aria-labelledby="sec-risk">
    <div class="card__head"><h2 id="sec-risk">${icon("shield")}Risk factors</h2>${flags.length ? html`<span class="status status--warn">${flags.length} flag${flags.length > 1 ? "s" : ""}</span>` : html`<span class="status status--ok">No flags</span>`}</div>
    <div class="card__body stack">
      ${flags.length ? html`<ul class="list" style="list-style:none;margin:0;padding:0">${flags.map(f => html`<li class="list__row" style="align-items:flex-start">
          <span style="color:var(--${FLAGS[f][1] === "danger" ? "danger" : "warn"})">${icon("alert")}</span>
          <div class="list__main"><div class="list__title">${FLAGS[f][0]}</div><div class="list__sub">${FLAGS[f][2]}</div></div></li>`)}</ul>`
        : html`<p class="small muted">No risk flags were detected in the listing text, price or history. That's not an inspection.</p>`}
      <div class="notice">${icon("info")}<div><strong>Before you buy:</strong> see the car in person, get a pre-purchase inspection, run a VIN history check and confirm the title matches the seller's ID.</div></div>
    </div></section>`;
}

/* ---------------- Saved ---------------- */
async function viewSaved(m, tok) {
  setNav("saved"); topbar(); S.lastList = "#/saved";
  const stage = store.get("ff-stage") || "all";
  render(html`<div class="page__head"><div><h1>Saved deals</h1><p>Track the cars you're working, from first contact to purchase.</p></div></div>
    <div class="seg" role="tablist" aria-label="Deal stage" id="stages" style="margin-bottom:var(--s4)"></div>
    <div id="feed" class="feed" aria-busy="true">${skeletons(2)}</div>`);
  let rows;
  try { rows = await api("/api/deals?feed=saved&limit=100"); }
  catch (e) { if (alive(tok)) setHTML($("#feed"), errorBlock(e)); return; }
  if (!alive(tok)) return;
  const counts = {}; rows.forEach(r => counts[r.user_status] = (counts[r.user_status] || 0) + 1);
  const tabs = [["all", "All", rows.length]].concat(STAGES.filter(([k]) => counts[k]).map(([k, l]) => [k, l, counts[k]]));
  const cur = tabs.some(t => t[0] === stage) ? stage : "all";
  setHTML($("#stages"), html`${tabs.map(([k, l, c]) => html`<button class="seg__btn" role="tab" type="button" data-stage-tab="${k}" aria-selected="${k === cur ? "true" : "false"}">${l}<span class="seg__count">${c}</span></button>`)}`);
  const paint = st => {
    const list = st === "all" ? rows : rows.filter(r => r.user_status === st);
    $("#feed").setAttribute("aria-busy", "false");
    setHTML($("#feed"), list.length ? html`${list.map(d => dealCard(d, {stage: true}))}`
      : html`<div class="card" style="grid-column:1/-1">${emptyState("bookmark", "No saved deals yet", hasPlan("starter")
          ? "Tap the bookmark on any deal to save it here and track it through contact, negotiation and purchase."
          : "Saving deals is part of the Lite and Pro plans.",
          html`<a class="btn btn--primary" href="${hasPlan("starter") ? "#/discover" : "#/account/plan"}">${hasPlan("starter") ? "Browse deals" : "See plans"}</a>`)}</div>`);
  };
  paint(cur);
  $("#stages").onclick = e => {
    const b = e.target.closest("[data-stage-tab]"); if (!b) return;
    $$("[data-stage-tab]").forEach(x => x.setAttribute("aria-selected", x === b ? "true" : "false"));
    store.set("ff-stage", b.dataset.stageTab); paint(b.dataset.stageTab);
  };
  restoreScroll();
}

/* ---------------- Alerts ---------------- */
function describeFilter(f) {
  f = f || {};
  const out = [];
  const veh = [f.make, f.model].filter(Boolean).map(x => String(x).replace(/\b\w/g, c => c.toUpperCase())).join(" ");
  if (veh) out.push(veh);
  if (f.search) out.push(`“${f.search}”`);
  const lo = n(f.min_price), hi = n(f.max_price);
  if (lo != null && hi != null) out.push(`${moneyK(lo)}–${moneyK(hi)}`); else if (hi != null) out.push(`Under ${moneyK(hi)}`); else if (lo != null) out.push(`Over ${moneyK(lo)}`);
  const y0 = n(f.min_year), y1 = n(f.max_year);
  if (y0 != null && y1 != null) out.push(`${y0}–${y1}`); else if (y0 != null) out.push(`${y0} or newer`); else if (y1 != null) out.push(`${y1} or older`);
  if (n(f.max_miles) != null) out.push(`Under ${num(f.max_miles)} mi`);
  out.push(`${n(f.min_discount_pct) != null ? Math.round(f.min_discount_pct) : 15}%+ below market`);
  if (n(f.min_profit) != null) out.push(`${money(f.min_profit)}+ est. profit`);
  if (f.min_confidence === "high") out.push("High confidence only");
  if (f.risk_mode === "aggressive") out.push("Includes flagged");
  if (f.zip && f.radius_mi) out.push(`Within ${f.radius_mi} mi of ${f.zip}`);
  if (String(f.allow_salvage) === "true") out.push("Branded titles OK");
  if (String(f.allow_needs_work) === "true") out.push("Needs-work OK");
  if (String(f.hide_dealer) === "true") out.push("No dealers");
  if (String(f.hide_auction) === "true") out.push("No auctions");
  if (String(f.include_unknown_location) === "true") out.push("Unknown locations OK");
  if (String(f.price_drops) === "false") out.push("No price-drop alerts");
  return out;
}
async function viewAlerts(m, tok) {
  setNav("alerts"); topbar(); S.lastList = "#/alerts";
  render(html`<div class="page__head"><div><h1>Alerts</h1><p>Saved searches watch every new listing and push matches to your phone.</p></div>
      <a class="btn btn--primary" href="#/alerts/new">${icon("plus")}New search</a></div>
    <div class="stack-lg"><div class="skel" style="height:140px;border-radius:16px"></div><div class="skel" style="height:200px;border-radius:16px"></div></div>`);
  const [searches, ch, recent, usage] = await Promise.all([
    api("/api/saved_searches").catch(() => []), api("/api/me/alert_channels").catch(() => null),
    api("/api/me/alerts?limit=20").catch(() => []), api("/api/me/usage").catch(() => null)]);
  if (!alive(tok)) return;
  S.searches = searches;
  const lim = usage && usage.limits ? usage.limits : {};
  const anyChannel = ch && (ch.ntfy_topic || ch.alert_webhook || ch.notify_phone);
  render(html`
    <div class="page__head"><div><h1>Alerts</h1><p>Saved searches watch every new listing and push matches to your phone.</p></div>
      <a class="btn btn--primary" href="#/alerts/new">${icon("plus")}New search</a></div>
    ${!hasPlan("starter") ? html`<div class="notice notice--info" style="margin-bottom:var(--s4)">${icon("lock")}<div><strong>Saved-search alerts are part of Lite and Pro.</strong> You can still browse top deals on the Free plan. <a href="#/account/plan">Compare plans</a></div></div>` : ""}
    ${hasPlan("starter") && !anyChannel ? html`<div class="notice notice--warn" style="margin-bottom:var(--s4)">${icon("alert")}<div><strong>No delivery channel yet.</strong> Matches still appear under Discover → For you, but nothing reaches your phone until you set one up below.</div></div>` : ""}
    <section aria-labelledby="sec-searches">
      <div class="section__head"><h2 id="sec-searches">Your searches</h2>${lim.saved_searches != null ? html`<span class="xsmall muted">${searches.length} of ${lim.saved_searches} on ${planName(S.plan)}</span>` : ""}</div>
      ${searches.length ? html`<div class="stack">${searches.map(s => html`<div class="card card--pad" data-search="${s.id}">
          <div class="row row--between" style="align-items:flex-start;gap:var(--s3)">
            <div style="min-width:0"><h3 style="font-size:var(--fs-md)">${s.name}</h3>
              <div class="xsmall muted" style="margin-top:2px">${s.notify_enabled ? "Alerts on" : "Alerts off"}${s.created_at ? ` · created ${ago(s.created_at.replace(" ", "T") + "Z")}` : ""}</div></div>
            <div class="row" style="flex:0 0 auto"><a class="btn btn--sm" href="#/alerts/search/${s.id}">Matches</a>
              <button class="btn btn--sm btn--ghost btn--icon" type="button" data-del-search="${s.id}" aria-label="Delete search ${s.name}" title="Delete">${icon("trash")}</button></div>
          </div>
          <div class="chips" style="margin-top:var(--s3)">${describeFilter(s.filter).map(t => html`<span class="chip chip--outline">${t}</span>`)}</div></div>`)}</div>`
        : html`<div class="card">${emptyState("search", "No saved searches yet", "Describe the cars you buy — make, budget, distance, minimum discount — and FlipFirst watches for them around the clock.",
            html`<a class="btn btn--primary" href="#/alerts/new">${icon("plus")}Create your first search</a>`)}</div>`}
    </section>
    <section class="section" aria-labelledby="sec-delivery">
      <div class="section__head"><h2 id="sec-delivery">Delivery</h2>${anyChannel ? html`<button class="btn btn--sm" type="button" id="test-alert">${icon("send")}Send test alert</button>` : ""}</div>
      ${channelsCard(ch, lim)}
    </section>
    <section class="section" aria-labelledby="sec-recent">
      <div class="section__head"><h2 id="sec-recent">Recent alerts</h2></div>
      ${recent.length ? html`<div class="card"><div class="card__body"><ul class="list" style="list-style:none;margin:0;padding:0">${recent.map(a => html`<li class="list__row">
          <span class="${a.event === "price_drop" ? "pos" : "muted"}">${icon(a.event === "price_drop" ? "trend-down" : "bell")}</span>
          <div class="list__main"><div class="list__title"><a href="#/deal/${encodeURIComponent(a.composite_id)}">${a.title}</a></div>
            <div class="list__sub">${a.event === "price_drop" ? `Price drop ${money(a.previous_price)} → ${money(a.price)}` : `New listing at ${money(a.price)}`}${n(a.discount_pct) != null ? ` · ${pct(a.discount_pct)} below market` : ""}${a.saved_search_name ? ` · ${a.saved_search_name}` : ""} · ${ago(a.created_at)}</div></div>
          ${a.seen ? "" : html`<span class="dot dot--accent" title="New" aria-label="New"></span>`}</li>`)}</ul></div></div>`
        : html`<div class="card">${emptyState("bell", "No alerts yet", "When a listing matches one of your searches, it appears here and on your phone.")}</div>`}
    </section>`);
  wireChannels(ch, lim);
  const t = $("#test-alert");
  if (t) t.onclick = async () => {
    busy(t, true);
    try { const r = await post("/api/me/alert_channels/test", {});
      const okCh = Object.entries(r.channels || {}).filter(([, v]) => v.ok).map(([k]) => k);
      toast(r.ok ? `Test sent via ${okCh.join(", ")}.` : "Couldn't deliver the test — check your channel settings.", {kind: r.ok ? "ok" : "bad"});
    } catch (e) { toast(e.message, {kind: "bad"}); }
    busy(t, false);
  };
  $$("[data-del-search]").forEach(b => b.onclick = async () => {
    if (!confirm("Delete this saved search? You'll stop getting its alerts.")) return;
    busy(b, true);
    try { await api(`/api/saved_searches/${b.dataset.delSearch}`, {method: "DELETE"}); toast("Search deleted."); S.searches = S.searches.filter(s => String(s.id) !== b.dataset.delSearch); route(); }
    catch (e) { busy(b, false); if (!planPrompt(e, "Managing searches")) toast(e.message, {kind: "bad"}); }
  });
}
function channelsCard(ch, lim) {
  if (!ch) return html`<div class="card card--pad small muted">Couldn't load your delivery settings.</div>`;
  const topic = ch.ntfy_topic ? `${String(ch.ntfy_server || "https://ntfy.sh").replace(/\/$/, "")}/${ch.ntfy_topic}` : "";
  return html`<div class="card"><div class="card__body">
    <div class="list">
      <div class="list__row" style="align-items:flex-start">
        <span class="${ch.ntfy_topic ? "pos" : "muted"}">${icon("phone")}</span>
        <div class="list__main"><div class="list__title">Push notifications <span class="status ${ch.ntfy_topic ? "status--ok" : "status--off"}" style="margin-left:6px">${ch.ntfy_topic ? "On" : "Not set up"}</span></div>
          <div class="list__sub">Free and instant via the ntfy app (iOS and Android). Your topic is private — anyone who knows it can read it, so don't share it.</div>
          ${topic ? html`<div class="row" style="margin-top:var(--s2);flex-wrap:wrap"><code class="small" style="background:var(--surface-3);padding:6px 8px;border-radius:6px;word-break:break-all">${topic}</code>
            <button class="btn btn--sm" type="button" data-copy="${topic}">${icon("copy")}Copy</button></div>
            <p class="xsmall muted" style="margin-top:6px">In ntfy: tap <b>+</b>, choose “Use another server” if needed, and subscribe to the topic above.</p>` : ""}</div>
        <button class="btn btn--sm" type="button" id="gen-topic">${ch.ntfy_topic ? "New topic" : "Set up"}</button>
      </div>
      <div class="list__row" style="align-items:flex-start">
        <span class="${ch.alert_webhook ? "pos" : "muted"}">${icon("zap")}</span>
        <div class="list__main"><div class="list__title">Webhook <span class="status ${ch.alert_webhook ? "status--ok" : "status--off"}" style="margin-left:6px">${ch.alert_webhook ? "On" : "Off"}</span></div>
          <div class="list__sub">Post alerts to Discord, Slack or your own HTTPS endpoint.</div>
          <form class="row" style="margin-top:var(--s2)" id="hook-form"><label class="sr-only" for="hook">Webhook URL</label>
            <input class="input" id="hook" type="url" inputmode="url" placeholder="https://discord.com/api/webhooks/…" value="${ch.alert_webhook || ""}" style="min-height:38px;font-size:15px">
            <button class="btn btn--sm" type="submit">Save</button></form></div>
      </div>
      ${ch.sms_available ? html`<div class="list__row" style="align-items:flex-start">
        <span class="${ch.notify_phone ? "pos" : "muted"}">${icon("send")}</span>
        <div class="list__main"><div class="list__title">Text messages ${!lim.sms_per_month ? html`<span class="chip chip--accent" style="margin-left:6px">Pro</span>` : ""}</div>
          <div class="list__sub">${lim.sms_per_month ? `Up to ${lim.sms_per_month} texts a month.` : "Available on Pro."}</div>
          <form class="row" style="margin-top:var(--s2)" id="phone-form"><label class="sr-only" for="phone">Mobile number</label>
            <input class="input" id="phone" type="tel" inputmode="tel" autocomplete="tel" placeholder="(205) 555-0123" value="${ch.notify_phone || ""}" style="min-height:38px;font-size:15px">
            <button class="btn btn--sm" type="submit">Save</button></form></div></div>` : ""}
    </div></div></div>`;
}
function wireChannels() {
  const g = $("#gen-topic");
  if (g) g.onclick = async () => {
    busy(g, true);
    try { await post("/api/me/alert_channels", {ntfy_topic: "generate"}); toast("Push topic created — subscribe to it in the ntfy app.", {kind: "ok"}); route(); }
    catch (e) { busy(g, false); if (!planPrompt(e, "Alerts")) toast(e.message, {kind: "bad"}); }
  };
  const hf = $("#hook-form");
  if (hf) hf.onsubmit = async ev => {
    ev.preventDefault(); const b = hf.querySelector("button"); busy(b, true);
    try { await post("/api/me/alert_channels", {alert_webhook: $("#hook").value.trim()}); toast($("#hook").value.trim() ? "Webhook saved." : "Webhook removed.", {kind: "ok"}); route(); }
    catch (e) { busy(b, false); if (!planPrompt(e, "Webhook alerts")) toast(e.message, {kind: "bad"}); }
  };
  const pf = $("#phone-form");
  if (pf) pf.onsubmit = async ev => {
    ev.preventDefault(); const b = pf.querySelector("button"); busy(b, true);
    try { await post("/api/me/alert_channels", {notify_phone: $("#phone").value.trim()}); toast("Phone number saved.", {kind: "ok"}); route(); }
    catch (e) { busy(b, false); if (!planPrompt(e, "Text alerts")) toast(e.message, {kind: "bad"}); }
  };
}

/* search wizard ------------------------------------------------------- */
const SW_STEPS = ["Vehicle", "Price & profit", "Area & condition", "Alerts"];
async function viewSearchWizard(m, tok) {
  setNav("alerts"); topbar({back: true, title: "New search"});
  const D = store.get("ff-search-draft") || {step: 0, f: {min_discount_pct: 15, risk_mode: "conservative", price_drops: true},
    name: "", notify: true};
  if (!D.f.zip && S.territory && S.territory.home_zip) { D.f.zip = S.territory.home_zip; D.f.radius_mi = S.territory.radius_mi || 75; }
  if (!hasPlan("starter")) {
    return render(html`<div class="narrow card">${emptyState("lock", "Saved searches need Lite or Pro",
      "Searches run around the clock and alert you the moment a matching deal appears. Upgrade to create one — or keep browsing top deals for free.",
      html`<div class="row" style="justify-content:center;flex-wrap:wrap"><a class="btn btn--primary" href="#/account/plan">Compare plans</a><a class="btn" href="#/discover/trusted">Browse top deals</a></div>`)}</div>`);
  }
  const save = () => store.set("ff-search-draft", D);
  const v = k => D.f[k] == null ? "" : D.f[k];
  const field = (id, label, input, hint) => html`<div class="field"><label class="label" for="${id}">${label}</label>${input}${hint ? html`<span class="hint">${hint}</span>` : ""}</div>`;
  const numIn = (k, ph, pre, attrs) => html`<div class="input-group">${pre ? html`<span class="prefix">${pre}</span>` : ""}<input class="input" id="f-${k}" data-k="${k}" type="number" inputmode="numeric" min="0" placeholder="${ph || "Any"}" value="${v(k)}" ${raw(attrs || "")}></div>`;
  const toggle = (k, title, sub, def) => html`<label class="switch"><span class="switch__text"><strong>${title}</strong><span>${sub}</span></span><input type="checkbox" role="switch" data-k="${k}" ${raw((D.f[k] != null ? String(D.f[k]) === "true" : def) ? "checked" : "")}></label>`;
  function paint() {
    const step = D.step;
    const body = [
      () => html`<h2>What are you looking for?</h2><p class="small muted" style="margin:4px 0 var(--s5)">Leave anything blank to match all vehicles.</p>
        <div class="grid-2">${field("f-make", "Make", html`<input class="input" id="f-make" data-k="make" autocomplete="off" placeholder="Toyota" value="${v("make")}">`)}
          ${field("f-model", "Model", html`<input class="input" id="f-model" data-k="model" autocomplete="off" placeholder="Camry" value="${v("model")}">`)}</div>
        <div class="field" style="margin-top:var(--s4)">${raw(String(field("f-search", raw('Keyword <span class="opt">(optional)</span>'), html`<input class="input" id="f-search" data-k="search" placeholder="e.g. 4x4, hybrid, manual" value="${v("search")}">`, "Matched against the listing title.")))}</div>
        <div class="grid-2" style="margin-top:var(--s4)">${field("f-min_year", "Oldest year", numIn("min_year", "2008"))}${field("f-max_year", "Newest year", numIn("max_year", "Any"))}</div>
        <div style="margin-top:var(--s4)">${field("f-max_miles", "Maximum mileage", numIn("max_miles", "Any", "", 'step="5000"'))}</div>`,
      () => html`<h2>What counts as a deal?</h2><p class="small muted" style="margin:4px 0 var(--s5)">FlipFirst compares each listing with similar cars nearby.</p>
        <div class="grid-2">${field("f-min_price", "Min price", numIn("min_price", "Any", "$"))}${field("f-max_price", "Max price", numIn("max_price", "Any", "$"))}</div>
        <div class="grid-2" style="margin-top:var(--s4)">${field("f-min_discount_pct", "Min % below market", html`<div class="input-group"><input class="input" id="f-min_discount_pct" data-k="min_discount_pct" type="number" inputmode="numeric" min="0" max="80" value="${v("min_discount_pct")}"><span class="suffix">%</span></div>`, "15% is a good start.")}
          ${field("f-min_profit", raw('Min est. profit <span class="opt">(optional)</span>'), numIn("min_profit", "Any", "$"), "After estimated costs.")}</div>
        <div class="field" style="margin-top:var(--s4)"><span class="label" id="lbl-conf">Valuation confidence</span>
          <div class="seg" role="radiogroup" aria-labelledby="lbl-conf">
            ${[["medium", "Medium or better"], ["high", "High only"]].map(([k, l]) => html`<button class="seg__btn" type="button" role="radio" data-conf="${k}" aria-checked="${(v("min_confidence") || "medium") === k ? "true" : "false"}">${l}</button>`)}</div></div>
        <div class="field" style="margin-top:var(--s4)"><span class="label">Risk</span>
          <label class="check"><input type="radio" name="risk" value="conservative" ${raw(v("risk_mode") !== "aggressive" ? "checked" : "")}><div><strong>Conservative</strong><span>Only trusted listings — no risk flags. Recommended.</span></div></label>
          <label class="check"><input type="radio" name="risk" value="aggressive" ${raw(v("risk_mode") === "aggressive" ? "checked" : "")} ${raw(hasPlan("pro") ? "" : "disabled")}><div><strong>Aggressive ${hasPlan("pro") ? "" : html`<span class="chip chip--accent">Pro</span>`}</strong><span>Also include flagged listings, clearly labeled. For experienced flippers.</span></div></label></div>`,
      () => html`<h2>Where, and what condition?</h2><p class="small muted" style="margin:4px 0 var(--s5)">Distance is measured from the ZIP code's center.</p>
        <div class="grid-2">${field("f-zip", "ZIP code", html`<input class="input" id="f-zip" data-k="zip" inputmode="numeric" autocomplete="postal-code" maxlength="5" placeholder="35401" value="${v("zip")}">`)}
          ${field("f-radius_mi", "Max distance", html`<div class="input-group"><input class="input" id="f-radius_mi" data-k="radius_mi" type="number" inputmode="numeric" min="10" max="500" value="${v("radius_mi") || 75}"><span class="suffix">mi</span></div>`)}</div>
        <div class="card card--flat" style="margin-top:var(--s5);padding:0 var(--s4)">
          ${toggle("allow_salvage", "Include branded titles", "Salvage or rebuilt titles are excluded unless you turn this on.", false)}
          ${toggle("allow_needs_work", "Include cars that need work", "Listings mentioning mechanical problems.", false)}
          ${toggle("hide_dealer", "Hide dealer listings", "Private sellers only.", false)}
          ${toggle("hide_auction", "Hide auctions", "Fixed-price listings only.", false)}
          ${toggle("include_unknown_location", "Include unknown locations", "Listings without a reliable location are skipped by default.", false)}
        </div>`,
      () => html`<h2>Name it and choose alerts</h2><p class="small muted" style="margin:4px 0 var(--s5)">You can delete the search any time.</p>
        ${field("f-name", "Search name", html`<input class="input" id="f-name" placeholder="e.g. Camrys under $10k" value="${D.name || ""}" maxlength="120">`)}
        <div class="card card--flat" style="margin-top:var(--s5);padding:0 var(--s4)">
          <label class="switch"><span class="switch__text"><strong>Send alerts</strong><span>Push new matches to your delivery channels.</span></span><input type="checkbox" role="switch" id="f-notify" ${raw(D.notify ? "checked" : "")}></label>
          ${toggle("price_drops", "Price-drop alerts" + (hasPlan("pro") ? "" : " (Pro)"), "Alert again when a matching car's price drops.", true)}
        </div>
        <div style="margin-top:var(--s5)"><div class="eyebrow" style="margin-bottom:var(--s2)">Summary</div>
          <div class="chips">${describeFilter(collectFilter()).map(t => html`<span class="chip chip--outline">${t}</span>`)}</div></div>`,
    ][step]();
    render(html`<div class="narrow">
      <ol class="stepper" aria-label="Progress" style="list-style:none;padding:0">${SW_STEPS.map((l, i) => html`<li class="stepper__item ${i < step ? "is-done" : i === step ? "is-current" : ""}"${i === step ? raw(' aria-current="step"') : ""}><div class="stepper__bar"></div><div class="stepper__label">${i + 1}. ${l}</div></li>`)}</ol>
      <form class="card card--pad" id="wiz" novalidate>${body}
        <div class="error-text" id="werr" role="alert" style="margin-top:var(--s3)"></div>
        <div class="row" style="margin-top:var(--s6)">
          ${step ? html`<button class="btn btn--lg" type="button" id="prev">${icon("chevron-left")}Back</button>` : html`<a class="btn btn--lg" href="#/alerts">Cancel</a>`}
          <div class="spacer"></div>
          <button class="btn btn--primary btn--lg" type="submit" id="next">${step === SW_STEPS.length - 1 ? html`${icon("check")}Save search` : html`Continue${icon("chevron-right")}`}</button>
        </div></form></div>`);
    wire();
  }
  function collectFilter() {
    const f = {};
    for (const [k, val] of Object.entries(D.f)) {
      if (val === "" || val == null) continue;
      if (["allow_salvage", "allow_needs_work", "hide_dealer", "hide_auction", "include_unknown_location"].includes(k)) { if (val === true || val === "true") f[k] = true; continue; }
      if (k === "price_drops") { if (val === false || val === "false") f[k] = false; continue; }
      f[k] = val;
    }
    return f;
  }
  function read() {
    $$("#wiz [data-k]").forEach(i => {
      const k = i.dataset.k;
      if (i.type === "checkbox") D.f[k] = i.checked;
      else if (i.type === "number") D.f[k] = i.value === "" ? null : Number(i.value);
      else D.f[k] = i.value.trim();
    });
    const r = $("#wiz input[name=risk]:checked"); if (r) D.f.risk_mode = r.value;
    if ($("#f-name")) D.name = $("#f-name").value.trim();
    if ($("#f-notify")) D.notify = $("#f-notify").checked;
    save();
  }
  function validate() {
    const e = [];
    if (D.step === 0) {
      if (D.f.min_year != null && D.f.max_year != null && D.f.min_year > D.f.max_year) e.push("Oldest year must be before newest year.");
    } else if (D.step === 1) {
      if (D.f.min_price != null && D.f.max_price != null && D.f.min_price > D.f.max_price) e.push("Min price must be below max price.");
      if (D.f.min_discount_pct != null && (D.f.min_discount_pct < 0 || D.f.min_discount_pct > 80)) e.push("Discount must be between 0 and 80%.");
    } else if (D.step === 2) {
      if (D.f.zip && !/^\d{5}$/.test(D.f.zip)) e.push("Enter a 5-digit ZIP code.");
      if (D.f.zip && (!(D.f.radius_mi >= 10) || D.f.radius_mi > 500)) e.push("Distance must be 10–500 miles.");
    }
    return e;
  }
  function wire() {
    const f = $("#wiz");
    $$("[data-conf]").forEach(b => b.onclick = () => { D.f.min_confidence = b.dataset.conf === "medium" ? "" : b.dataset.conf;
      $$("[data-conf]").forEach(x => { const on = x === b; x.setAttribute("aria-checked", on); }); save(); });
    const prev = $("#prev"); if (prev) prev.onclick = () => { read(); D.step--; save(); paint(); };
    f.onsubmit = async ev => {
      ev.preventDefault(); read();
      const errs = validate();
      if (errs.length) { $("#werr").textContent = errs.join(" "); return; }
      if (D.step < SW_STEPS.length - 1) { D.step++; save(); paint(); return; }
      const btn = $("#next"); busy(btn, true);
      const filter = collectFilter();
      try {
        if (filter.zip && !(S.territory && S.territory.home_zip)) {
          S.territory = await post("/api/me/territory", {home_zip: filter.zip, radius_mi: Number(filter.radius_mi || 75)}).catch(() => S.territory);
        }
        const r = await post("/api/saved_searches", {name: D.name || describeFilter(filter).slice(0, 2).join(", ") || "My search",
                                                     notify_enabled: !!D.notify, filter});
        store.del("ff-search-draft");
        S.searches = await api("/api/saved_searches").catch(() => S.searches);
        toast("Search saved. We'll alert you when something matches.", {kind: "ok"});
        go(`#/alerts/search/${r.id}`);
      } catch (e) {
        busy(btn, false);
        $("#werr").textContent = e.status === 402 ? ((e.body && e.body.error) || "This needs a paid plan.") : e.message;
      }
    };
    const first = $("#wiz input, #wiz select"); if (first && !S.first) first.focus({preventScroll: true});
  }
  paint();
}
async function viewSearchMatches(m, tok) {
  setNav("alerts"); topbar({back: true, title: "Search matches"}); S.lastList = location.hash;
  const s = (S.searches || []).find(x => String(x.id) === m[1]);
  render(html`<div class="page__head"><div><h1>${s ? s.name : "Search matches"}</h1>
      ${s ? html`<div class="chips" style="margin-top:var(--s2)">${describeFilter(s.filter).map(t => html`<span class="chip chip--outline">${t}</span>`)}</div>` : ""}</div></div>
    <p class="small muted" style="margin-bottom:var(--s4)">Current listings that match. New matches are pushed to you as they're found.</p>
    <div id="feed" class="feed" aria-busy="true">${skeletons(2)}</div>`);
  let rows;
  try { rows = await api(`/api/deals?feed=search&search_id=${encodeURIComponent(m[1])}&limit=30`); }
  catch (e) { if (alive(tok)) setHTML($("#feed"), errorBlock(e)); return; }
  if (!alive(tok)) return;
  $("#feed").setAttribute("aria-busy", "false");
  setHTML($("#feed"), rows.length ? html`${rows.map(d => dealCard(d))}`
    : html`<div class="card" style="grid-column:1/-1">${emptyState("search", "Nothing matches right now", "That's normal — you'll get an alert as soon as a listing does.", html`<a class="btn" href="#/discover/trusted">Browse top deals</a>`)}</div>`);
}

/* ---------------- Sell ---------------- */
const SELL_STATUS = {pending: ["Waiting for email confirmation", "warn"], active: ["Live", "ok"],
                     expired: ["Hidden — confirm it's still for sale", "warn"]};
async function viewSell(m, tok) {
  setNav("sell"); topbar(); S.lastList = "#/sell";
  render(html`<div class="page__head"><div><h1>Sell a car</h1><p>List it once, free. Buyers near you who are looking for it get an alert.</p></div></div>
    <div class="stack-lg"><div class="skel" style="height:120px;border-radius:16px"></div><div class="skel" style="height:160px;border-radius:16px"></div></div>`);
  let s, c;
  try { s = await api("/api/seller/listings"); } catch (e) { if (alive(tok)) render(errorBlock(e)); return; }
  try { c = await api("/api/seller/contact"); } catch (e) { c = {email: (S.me || {}).email || "", verified: false}; }
  if (!alive(tok)) return;
  if (!s.enabled) return render(html`<div class="page__head"><div><h1>Sell a car</h1></div></div>
    <div class="card">${emptyState("tag", "Selling on FlipFirst isn't open yet", "Seller listings are opening to invited sellers first. You'll be able to list a car here when it's your turn.")}</div>`);
  const active = s.listings.filter(l => l.status === "pending" || l.status === "active").length;
  const atLimit = active >= s.limits.active;
  render(html`
    <div class="page__head"><div><h1>Sell a car</h1><p>List it once, free. Buyers near you who are looking for it get an alert.</p></div>
      ${atLimit ? "" : html`<a class="btn btn--primary" href="#/sell/new">${icon("plus")}List a car</a>`}</div>
    ${contactCard(c)}
    <section class="section" aria-labelledby="sec-mine">
      <div class="section__head"><h2 id="sec-mine">Your listings</h2><span class="xsmall muted">${active} of ${s.limits.active} active</span></div>
      ${s.listings.length ? html`<div class="stack">${s.listings.map(l => html`<div class="card card--pad" data-listing="${l.id}">
          <div class="row row--between" style="align-items:flex-start;flex-wrap:wrap">
            <div style="min-width:0"><h3 style="font-size:var(--fs-md)">${l.title}</h3>
              <div class="xsmall muted" style="margin-top:2px">${num(l.odometer || 0)} mi · ZIP ${l.zip}${l.value_mid ? ` · est. value ${money(l.value_mid)} (${l.confidence || "—"} confidence)` : ""}</div></div>
            <span class="status status--${(SELL_STATUS[l.status] || ["", "off"])[1]}">${(SELL_STATUS[l.status] || [l.status])[0]}</span></div>
          <div class="row" style="margin-top:var(--s3);flex-wrap:wrap">
            <b class="num" style="font-size:var(--fs-xl);margin-right:auto">${money(l.price)}</b>
            <button class="btn btn--sm" type="button" data-price="${l.id}" data-current="${l.price}">${icon("dollar")}Change price</button>
            ${l.status === "expired" ? html`<button class="btn btn--sm btn--primary" type="button" data-renew="${l.id}">${icon("check")}Still for sale</button>` : ""}
            <button class="btn btn--sm btn--danger" type="button" data-unlist="${l.id}">${icon("trash")}Remove</button></div>
          ${l.status === "pending" ? html`<p class="xsmall muted" style="margin-top:var(--s2)">Not visible to buyers until you confirm your contact email.</p>` : ""}
        </div>`)}</div>`
        : html`<div class="card">${emptyState("car", "No listings yet", "Listing takes about two minutes: vehicle details, condition, price and location.",
            atLimit ? "" : html`<a class="btn btn--primary" href="#/sell/new">${icon("plus")}List a car</a>`)}</div>`}
      ${atLimit ? html`<p class="small muted" style="margin-top:var(--s3)">You've reached the limit of ${s.limits.active} active listings. Remove one to add another.</p>` : ""}
    </section>
    <section class="section"><div class="notice">${icon("info")}<div>Everything you enter is shown as <strong>seller-reported</strong>. FlipFirst verifies that you control your contact email — not the car, its title or its condition. Listings hide after ${s.limits.confirm_days} days unless you confirm they're still for sale, and you can remove one any time.</div></div></section>`);
  wireContact(c);
  $$("[data-unlist]").forEach(b => b.onclick = async () => {
    if (!confirm("Remove this listing? It disappears for all buyers and can't be undone.")) return;
    busy(b, true);
    try { await api(`/api/seller/listings/${b.dataset.unlist}`, {method: "DELETE"}); toast("Listing removed."); route(); }
    catch (e) { busy(b, false); toast(e.message, {kind: "bad"}); }
  });
  $$("[data-renew]").forEach(b => b.onclick = async () => {
    busy(b, true);
    try { await post(`/api/seller/listings/${b.dataset.renew}/renew`, {}); toast("Confirmed — your listing is visible again.", {kind: "ok"}); route(); }
    catch (e) { busy(b, false); toast(e.message, {kind: "bad"}); }
  });
  $$("[data-price]").forEach(b => b.onclick = async () => {
    const val = prompt("New asking price (USD)", b.dataset.current);
    if (val == null) return;
    const p = Number(String(val).replace(/[^0-9.]/g, ""));
    if (!(p >= 500 && p <= 250000)) { toast("Price must be $500–$250,000.", {kind: "bad"}); return; }
    busy(b, true);
    try { await api(`/api/seller/listings/${b.dataset.price}`, {method: "PATCH", body: JSON.stringify({price: Math.round(p)})}); toast("Price updated. Buyers watching for price drops are notified.", {kind: "ok"}); route(); }
    catch (e) { busy(b, false); toast(e.message, {kind: "bad"}); }
  });
}
function contactCard(c) {
  if (c.verified) return html`<div class="notice notice--ok">${icon("check")}<div>Contact email <strong>${c.email}</strong> is confirmed. Paid buyers can see it on your listings so they can reach you.</div></div>`;
  return html`<div class="card card--pad" id="contact-card">
    <div class="row" style="align-items:flex-start"><span class="empty__icon" style="margin:0;width:40px;height:40px;flex:0 0 auto">${icon("mail")}</span>
      <div style="min-width:0;flex:1"><h3 style="font-size:var(--fs-md)">Confirm your contact email to publish</h3>
        <p class="small muted" style="margin-top:2px">Listings stay hidden until you confirm the email buyers will use to reach you. Confirming proves you control the address — it doesn't verify the car.</p></div></div>
    <form class="row" id="contact-form" style="margin-top:var(--s4);flex-wrap:wrap" novalidate>
      <label class="sr-only" for="c-email">Contact email</label>
      <input class="input" id="c-email" type="email" autocomplete="email" inputmode="email" value="${c.email || ""}" style="flex:1 1 220px">
      <button class="btn btn--primary" type="submit" id="c-send">${icon("send")}Email me a confirmation link</button></form>
    <div class="small" id="c-msg" role="status" style="margin-top:var(--s2)"></div></div>`;
}
function wireContact() {
  // a <form> on the Sell page; a plain group inside the wizard (forms can't nest)
  const btn = $("#c-send"); if (!btn) return;
  const f = $("#contact-form");
  const send = async ev => {
    if (ev) ev.preventDefault();
    const btn = $("#c-send"), msg = $("#c-msg"); busy(btn, true); msg.className = "small"; msg.textContent = "";
    try {
      const r = await post("/api/seller/contact", {email: $("#c-email").value.trim()});
      if (r.verified) { toast("That email is already confirmed.", {kind: "ok"}); route(); return; }
      msg.className = "small pos"; msg.textContent = "Check your inbox for the link. It works once, for 24 hours.";
      busy(btn, false); btn.disabled = true; setTimeout(() => { btn.disabled = false; }, 60000);
    } catch (e) { busy(btn, false); msg.className = "small error-text"; msg.textContent = e.message; }
  };
  if (f) f.onsubmit = send;
  else {
    btn.onclick = send;
    $("#c-email").addEventListener("keydown", ev => { if (ev.key === "Enter") send(ev); });
  }
}

/* seller wizard -------------------------------------------------------- */
const SELL_STEPS = ["Vehicle", "Condition & title", "Price & location", "Contact", "Review"];
async function viewSellWizard(m, tok) {
  setNav("sell"); topbar({back: true, title: "List a car"});
  let s, c;
  try { s = await api("/api/seller/listings"); } catch (e) { if (alive(tok)) render(errorBlock(e)); return; }
  if (!alive(tok)) return;
  if (!s.enabled) { location.replace("#/sell"); return; }
  try { c = await api("/api/seller/contact"); } catch (e) { c = {email: (S.me || {}).email || "", verified: false}; }
  const D = store.get("ff-sell-draft") || {step: 0, v: {}};
  const save = () => store.set("ff-sell-draft", D);
  const v = k => D.v[k] == null ? "" : D.v[k];
  const thisYear = new Date().getFullYear();
  function paint(serverErrs) {
    const st = D.step;
    const fld = (k, label, input, hint) => html`<div class="field"><label class="label" for="s-${k}">${label}</label>${input}${hint ? html`<span class="hint">${hint}</span>` : ""}<span class="error-text" data-err="${k}"></span></div>`;
    const inp = (k, attrs, ph) => html`<input class="input${k === "vin" ? " input--upper" : ""}" id="s-${k}" data-k="${k}" ${raw(attrs || "")} placeholder="${ph || ""}" value="${v(k)}">`;
    const body = [
      () => html`<h2>Tell us about the car</h2><p class="small muted" style="margin:4px 0 var(--s5)">Exact details help buyers find it and help FlipFirst value it.</p>
        <div class="grid-2">${fld("year", "Year", inp("year", `type="number" inputmode="numeric" min="1981" max="${thisYear + 1}"`, "2015"))}
          ${fld("mileage", "Mileage", html`<div class="input-group">${inp("mileage", 'type="number" inputmode="numeric" min="0" max="500000"', "128000")}<span class="suffix">mi</span></div>`)}</div>
        <div class="grid-2" style="margin-top:var(--s4)">${fld("make", "Make", inp("make", 'autocomplete="off" maxlength="30"', "Toyota"))}${fld("model", "Model", inp("model", 'autocomplete="off" maxlength="40"', "Camry"))}</div>
        <div class="grid-2" style="margin-top:var(--s4)">${fld("trim", raw('Trim <span class="opt">(optional)</span>'), inp("trim", 'maxlength="30"', "SE"))}
          ${fld("vin", raw('VIN <span class="opt">(optional)</span>'), inp("vin", 'maxlength="17" autocapitalize="characters" spellcheck="false"', "17 characters"), "Lets buyers run a history check.")}</div>`,
      () => html`<h2>Condition and title</h2><p class="small muted" style="margin:4px 0 var(--s5)">Be accurate — buyers see this as seller-reported, and honest listings sell faster.</p>
        <div class="field"><span class="label" id="lbl-title">Title status</span>
          <div role="radiogroup" aria-labelledby="lbl-title">${TITLE_STATUS.map(([k, l, h]) => html`<label class="check"><input type="radio" name="title_status" value="${k}" ${raw(v("title_status") === k ? "checked" : "")}><div><strong>${l}</strong><span>${h}</span></div></label>`)}</div>
          <span class="error-text" data-err="title_status"></span></div>
        ${["mechanical", "cosmetic"].map(kind => html`<div class="field" style="margin-top:var(--s5)"><span class="label" id="lbl-${kind}">Any known ${kind} problems?</span>
          <div class="seg" role="radiogroup" aria-labelledby="lbl-${kind}" style="margin-bottom:var(--s2)">
            ${[["no", "None known"], ["yes", "Yes"]].map(([k, l]) => html`<button class="seg__btn" type="button" role="radio" data-has="${kind}" data-val="${k}" aria-checked="${(v(kind + "_has") || "") === k ? "true" : "false"}">${l}</button>`)}</div>
          <textarea class="textarea" id="s-${kind}_issues" data-k="${kind}_issues" maxlength="1000" rows="3" placeholder="${kind === "mechanical" ? "e.g. check-engine light, needs brakes, AC not cold" : "e.g. door ding, faded paint on hood"}" ${raw(v(kind + "_has") === "yes" ? "" : "hidden")}>${v(kind + "_issues")}</textarea>
          <span class="error-text" data-err="${kind}"></span></div>`)}
        <div class="field" style="margin-top:var(--s5)"><label class="label" for="s-description">Anything else buyers should know? <span class="opt">(optional)</span></label>
          <textarea class="textarea" id="s-description" data-k="description" maxlength="2000" rows="3" placeholder="Service history, new tires, one owner…">${v("description")}</textarea></div>`,
      () => html`<h2>Price and location</h2><p class="small muted" style="margin:4px 0 var(--s5)">Buyers are alerted by distance, so the ZIP should be where the car is.</p>
        ${fld("price", "Asking price", html`<div class="input-group"><span class="prefix">$</span>${inp("price", 'type="number" inputmode="numeric" min="500" max="250000" step="50"', "6800")}</div>`, "Between $500 and $250,000. You can change it later.")}
        <div style="margin-top:var(--s4)">${fld("zip", "ZIP code where the car is", inp("zip", 'inputmode="numeric" autocomplete="postal-code" maxlength="5"', "35401"))}</div>`,
      () => html`<h2>How buyers reach you</h2><p class="small muted" style="margin:4px 0 var(--s5)">Only signed-in buyers on paid plans see your contact email. Nobody else does.</p>
        ${c.verified ? html`<div class="notice notice--ok">${icon("check")}<div><strong>${c.email}</strong> is confirmed. Your listing goes live as soon as you submit it.</div></div>`
          : html`<div class="notice notice--warn">${icon("mail")}<div>Your listing will wait as <strong>pending</strong> until you confirm your contact email. Send the link now or after submitting — it works once, for 24 hours.</div></div>
            <div class="row" id="contact-inline" style="margin-top:var(--s4);flex-wrap:wrap">
              <label class="sr-only" for="c-email">Contact email</label>
              <input class="input" id="c-email" type="email" autocomplete="email" value="${c.email || ""}" style="flex:1 1 220px">
              <button class="btn" type="button" id="c-send">${icon("send")}Send confirmation link</button></div>
            <div class="small" id="c-msg" role="status" style="margin-top:var(--s2)"></div>`}`,
      () => html`<h2>Review and submit</h2><p class="small muted" style="margin:4px 0 var(--s4)">Check the details, then confirm the three statements below.</p>
        <dl class="dl">
          <dt>Vehicle</dt><dd>${[v("year"), v("make"), v("model"), v("trim")].filter(Boolean).join(" ") || "—"}</dd>
          <dt>Mileage</dt><dd>${v("mileage") !== "" ? num(v("mileage")) + " mi" : "—"}</dd>
          <dt>VIN</dt><dd>${v("vin") || "Not provided"}</dd>
          <dt>Title</dt><dd>${(TITLE_STATUS.find(t => t[0] === v("title_status")) || [, "—"])[1]}</dd>
          <dt>Mechanical</dt><dd style="white-space:normal;max-width:60%">${v("mechanical_has") === "yes" ? v("mechanical_issues") : "None known"}</dd>
          <dt>Cosmetic</dt><dd style="white-space:normal;max-width:60%">${v("cosmetic_has") === "yes" ? v("cosmetic_issues") : "None known"}</dd>
          <dt>Asking price</dt><dd>${money(v("price"))}</dd>
          <dt>Location</dt><dd>ZIP ${v("zip") || "—"}</dd>
          <dt>Contact</dt><dd>${c.email || "—"} ${c.verified ? "" : html`<small>pending confirmation</small>`}</dd>
        </dl>
        <fieldset style="border:0;padding:0;margin:var(--s5) 0 0"><legend class="label" style="margin-bottom:var(--s2)">Please confirm</legend>
          <label class="check"><input type="checkbox" id="a1" ${raw(D.a1 ? "checked" : "")}><div><strong>I own this car or am authorized to sell it</strong></div></label>
          <label class="check"><input type="checkbox" id="a2" ${raw(D.a2 ? "checked" : "")}><div><strong>The details above are accurate</strong><span>Including title status and known problems.</span></div></label>
          <label class="check"><input type="checkbox" id="a3" ${raw(D.a3 ? "checked" : "")}><div><strong>FlipFirst buyers on paid plans may see my contact email</strong><span>So they can reach me about this car.</span></div></label></fieldset>
        <p class="xsmall muted" style="margin-top:var(--s3)">You can remove the listing any time. It hides after ${s.limits.confirm_days} days unless you confirm it's still for sale. Up to ${s.limits.active} active listings.</p>`,
    ][st]();
    render(html`<div class="narrow">
      <ol class="stepper" aria-label="Progress" style="list-style:none;padding:0">${SELL_STEPS.map((l, i) => html`<li class="stepper__item ${i < st ? "is-done" : i === st ? "is-current" : ""}"${i === st ? raw(' aria-current="step"') : ""}><div class="stepper__bar"></div><div class="stepper__label">${i + 1}. ${l}</div></li>`)}</ol>
      ${serverErrs && serverErrs.length ? html`<div class="notice notice--bad" role="alert" style="margin-bottom:var(--s4)">${icon("alert")}<div><strong>Please fix:</strong><ul style="margin:4px 0 0;padding-left:18px">${serverErrs.map(x => html`<li>${x}</li>`)}</ul></div></div>` : ""}
      <form class="card card--pad" id="wiz" novalidate>${body}
        <div class="error-text" id="werr" role="alert" style="margin-top:var(--s3)"></div>
        <div class="row" style="margin-top:var(--s6)">
          ${st ? html`<button class="btn btn--lg" type="button" id="prev">${icon("chevron-left")}Back</button>` : html`<a class="btn btn--lg" href="#/sell">Cancel</a>`}
          <div class="spacer"></div>
          <button class="btn btn--primary btn--lg" type="submit" id="next">${st === SELL_STEPS.length - 1 ? html`${icon("check")}Submit listing` : html`Continue${icon("chevron-right")}`}</button>
        </div></form></div>`);
    wire();
  }
  function read() {
    $$("#wiz [data-k]").forEach(i => { D.v[i.dataset.k] = i.value.trim(); });
    const t = $("#wiz input[name=title_status]:checked"); if (t) D.v.title_status = t.value;
    ["a1", "a2", "a3"].forEach(k => { const el = $("#" + k); if (el) D[k] = el.checked; });
    save();
  }
  const setErr = (k, msg) => { const el = $(`[data-err="${k}"]`); if (el) el.textContent = msg || ""; const inp = $("#s-" + k); if (inp) inp.setAttribute("aria-invalid", msg ? "true" : "false"); };
  function validate() {
    let ok = true;
    const need = (k, cond, msg) => { if (!cond) { setErr(k, msg); ok = false; } else setErr(k, ""); };
    if (D.step === 0) {
      const y = Number(D.v.year), mi = Number(D.v.mileage);
      need("year", D.v.year !== "" && y >= 1981 && y <= thisYear + 1, `Enter a year between 1981 and ${thisYear + 1}.`);
      need("mileage", D.v.mileage !== "" && mi >= 0 && mi <= 500000, "Enter the odometer reading (0–500,000).");
      need("make", /^[A-Za-z0-9 -]+$/.test(D.v.make || ""), "Enter the make (letters and numbers).");
      need("model", /^[A-Za-z0-9 \-./]+$/.test(D.v.model || ""), "Enter the model.");
      need("trim", !D.v.trim || /^[A-Za-z0-9 \-./]+$/.test(D.v.trim), "Letters, numbers, - . / only.");
      need("vin", !D.v.vin || /^[A-HJ-NPR-Za-hj-npr-z0-9]{17}$/.test(D.v.vin), "A VIN has 17 letters and numbers (no I, O or Q).");
    } else if (D.step === 1) {
      need("title_status", !!D.v.title_status, "Choose the title status.");
      for (const kind of ["mechanical", "cosmetic"]) {
        const has = D.v[kind + "_has"];
        need(kind, has === "no" || (has === "yes" && (D.v[kind + "_issues"] || "").length >= 3),
             has === "yes" ? "Describe the problems briefly." : `Tell buyers whether there are known ${kind} problems.`);
      }
    } else if (D.step === 2) {
      const p = Number(D.v.price);
      need("price", D.v.price !== "" && p >= 500 && p <= 250000, "Enter a price between $500 and $250,000.");
      need("zip", /^\d{5}$/.test(D.v.zip || ""), "Enter the 5-digit ZIP code where the car is.");
    } else if (D.step === 4) {
      if (!(D.a1 && D.a2 && D.a3)) { $("#werr").textContent = "Please confirm all three statements to submit."; ok = false; }
    }
    return ok;
  }
  function wire() {
    $$("[data-has]").forEach(b => b.onclick = () => {
      const kind = b.dataset.has; D.v[kind + "_has"] = b.dataset.val; save();
      $$(`[data-has="${kind}"]`).forEach(x => { const on = x === b; x.setAttribute("aria-checked", on); });
      const ta = $(`#s-${kind}_issues`); ta.hidden = b.dataset.val !== "yes"; if (!ta.hidden) ta.focus();
      setErr(kind, "");
    });
    const prev = $("#prev"); if (prev) prev.onclick = () => { read(); D.step--; save(); paint(); };
    wireContact();
    $("#wiz").onsubmit = async ev => {
      ev.preventDefault(); read();
      if (!validate()) { const bad = $("#wiz [aria-invalid=true]") || $("#wiz .error-text:not(:empty)"); if (bad && bad.focus) bad.focus(); return; }
      if (D.step < SELL_STEPS.length - 1) { D.step++; save(); paint(); window.scrollTo(0, 0); return; }
      const btn = $("#next"); busy(btn, true);
      const issues = kind => D.v[kind + "_has"] === "yes" ? D.v[kind + "_issues"] : "None known";
      const body = {year: D.v.year, mileage: D.v.mileage, make: D.v.make, model: D.v.model, trim: D.v.trim,
        vin: (D.v.vin || "").toUpperCase(), title_status: D.v.title_status, mechanical_issues: issues("mechanical"),
        cosmetic_issues: issues("cosmetic"), description: D.v.description, price: D.v.price, zip: D.v.zip,
        attest: {authorized_to_sell: !!D.a1, details_accurate: !!D.a2, share_contact_with_buyers: !!D.a3}};
      try {
        const r = await post("/api/seller/listings", body);
        store.del("ff-sell-draft");
        toast(r.status === "active" ? "Your listing is live." : "Listing saved — confirm your email to publish it.", {kind: "ok", ms: 6000});
        go("#/sell");
      } catch (e) {
        busy(btn, false);
        paint((e.body && e.body.errors) || [e.message]);
      }
    };
  }
  paint();
}

/* ---------------- Account ---------------- */
async function viewAccount(m, tok) {
  setNav("account"); topbar();
  render(html`<div class="page__head"><div><h1>Account</h1></div></div><div class="stack-lg"><div class="skel" style="height:160px;border-radius:16px"></div><div class="skel" style="height:220px;border-radius:16px"></div></div>`);
  const [bill, plans, terr, ps] = await Promise.all([api("/api/billing/me").catch(() => null), api("/api/plans").catch(() => null),
    api("/api/me/territory").catch(() => null), api("/api/me/profit_settings").catch(() => null)]);
  if (!alive(tok)) return;
  if (terr) S.territory = terr;
  const me = S.me || {};
  const theme = FF.getTheme();
  const planRows = plans && plans.plans ? plans.plans : [];
  const feat = [["saved_searches", "Saved searches", x => x || "—"], ["alerts_per_day", "Alerts per day", x => x || "—"],
    ["profit_scenarios", "Profit scenarios", x => x ? "✓" : "—"], ["price_drop_alerts", "Price-drop alerts", x => x ? "✓" : "—"],
    ["custom_profit_assumptions", "Custom cost assumptions", x => x ? "✓" : "—"], ["webhook", "Webhook alerts", x => x ? "✓" : "—"],
    ["sms_per_month", "Text alerts / month", x => x || "—"], ["aggressive_mode", "Flagged deals in alerts", x => x ? "✓" : "—"]];
  render(html`
    <div class="page__head"><div><h1>Account</h1><p>${me.email || ""}</p></div></div>
    <div class="stack-lg">
    <section class="card" id="acct-plan" aria-labelledby="h-plan">
      <div class="card__head"><h2 id="h-plan">${icon("zap")}Plan</h2><span class="chip chip--accent">${planName(S.plan)}</span></div>
      <div class="card__body stack">
        ${planRows.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th scope="col">Feature</th>${planRows.map(p => html`<th scope="col" class="r"${p.plan === S.plan ? raw(' style="color:var(--accent)"') : ""}>${p.name}${p.plan === S.plan ? " (you)" : ""}</th>`)}</tr></thead>
          <tbody>${feat.map(([k, l, fmt]) => html`<tr><td>${l}</td>${planRows.map(p => html`<td class="r">${fmt(p[k])}</td>`)}</tr>`)}</tbody></table></div>` : ""}
        <div class="row row--wrap" id="bill-actions">${billActions(bill)}</div>
        <div class="error-text" id="bill-err" role="alert"></div>
      </div></section>

    <section class="card" id="acct-territory" aria-labelledby="h-terr">
      <div class="card__head"><h2 id="h-terr">${icon("pin")}Buying area</h2></div>
      <form class="card__body" id="terr-form" novalidate>
        <p class="small muted" style="margin-bottom:var(--s4)">Top deals and distances are based on this area. Each saved search can also have its own.</p>
        <div class="grid-2"><div class="field"><label class="label" for="t-zip">Home ZIP</label><input class="input" id="t-zip" inputmode="numeric" autocomplete="postal-code" maxlength="5" placeholder="35401" value="${(terr && terr.home_zip) || ""}"></div>
          <div class="field"><label class="label" for="t-radius">Radius</label><div class="input-group"><input class="input" id="t-radius" type="number" inputmode="numeric" min="10" max="500" value="${(terr && terr.radius_mi) || 75}"><span class="suffix">mi</span></div></div></div>
        <div class="row" style="margin-top:var(--s4)"><button class="btn btn--primary" type="submit">Save area</button><span class="small" id="terr-msg" role="status"></span></div>
      </form></section>

    <section class="card" id="acct-assumptions" aria-labelledby="h-assume">
      <div class="card__head"><h2 id="h-assume">${icon("sliders")}Cost assumptions</h2>${ps ? html`<button class="btn btn--sm btn--ghost" type="button" id="assume-reset">Reset to defaults</button>` : ""}</div>
      ${ps ? html`<form class="card__body" id="assume-form" novalidate>
        <p class="small muted" style="margin-bottom:var(--s4)">Used for every profit estimate. Blank tax and title fees are left out of estimates (and flagged as not included).</p>
        <div class="grid-2 grid-2--end">${ASSUME_ORDER.filter(k => k in ps.labels).map(k => {
          const unit = k.endsWith("_pct") ? "%" : k === "days_to_sell" ? "days" : (k === "transport_per_mile" ? "$/mi" : "$");
          const val = ps.effective[k];
          return html`<div class="field"><label class="label" for="as-${k}">${ps.labels[k]}</label><div class="input-group">${unit === "$" || unit === "$/mi" ? html`<span class="prefix">$</span>` : ""}
            <input class="input" id="as-${k}" data-k="${k}" type="number" step="any" min="0" inputmode="decimal" value="${val == null ? "" : val}" placeholder="not set">${unit !== "$" ? html`<span class="suffix">${unit === "$/mi" ? "/mi" : unit}</span>` : ""}</div></div>`;
        })}</div>
        <div class="row" style="margin-top:var(--s5)"><button class="btn btn--primary" type="submit">Save assumptions</button><span class="small" id="assume-msg" role="status"></span></div>
      </form>` : html`<div class="card__body small muted">Couldn't load your assumptions.</div>`}</section>

    <section class="card" id="acct-appearance" aria-labelledby="h-look">
      <div class="card__head"><h2 id="h-look">${icon("monitor")}Appearance</h2></div>
      <div class="card__body"><div class="seg" role="radiogroup" aria-labelledby="h-look">
        ${[["system", "System", "monitor"], ["light", "Light", "sun"], ["dark", "Dark", "moon"]].map(([k, l, ic]) => html`<button class="seg__btn" type="button" role="radio" data-theme-opt="${k}" aria-checked="${theme === k ? "true" : "false"}">${icon(ic, "sm")}${l}</button>`)}</div></div></section>

    ${S.isAdmin ? html`<section class="card" aria-labelledby="h-admin"><div class="card__head"><h2 id="h-admin">${icon("activity")}Owner tools</h2></div>
      <div class="card__body"><div class="list">
        <a class="list__row" href="/admin/ops" style="color:inherit;text-decoration:none"><span class="muted">${icon("activity")}</span><div class="list__main"><div class="list__title">Operations console</div><div class="list__sub">Source health, pipeline, queue, backups and costs</div></div>${icon("chevron-right")}</a>
        <a class="list__row" href="/" style="color:inherit;text-decoration:none"><span class="muted">${icon("server")}</span><div class="list__main"><div class="list__title">Classic console</div><div class="list__sub">Source polling, VIN providers and legacy settings</div></div>${icon("chevron-right")}</a>
      </div></div></section>` : ""}

    <section class="card card--pad stack">
      <p class="small muted">FlipFirst estimates are based on comparable listings and your cost assumptions. They're not guarantees, appraisals or inspections. Always inspect the car, run a VIN history check and confirm the title before you buy.</p>
      <p class="small muted">Questions or problems? Reply to your invitation email and we'll help.</p>
      <div><button class="btn btn--danger" type="button" data-signout>${icon("logout")}Sign out</button></div>
    </section>
    </div>`);
  wireAccount(bill);
  const sec = m[1] && $("#acct-" + m[1]);
  if (sec) { sec.scrollIntoView({block: "start"}); const f = sec.querySelector("input, button"); if (f) f.focus({preventScroll: true}); }
}
function billActions(b) {
  if (!b) return "";
  if (b.subscription) return html`<button class="btn" type="button" id="bill-manage">${icon("external")}Manage subscription</button>`;
  if (b.billing_available) return html`${S.plan === "free" ? html`<button class="btn btn--primary" type="button" data-up="starter">Upgrade to Lite</button>` : ""}${S.plan !== "pro" ? html`<button class="btn btn--primary" type="button" data-up="pro">Upgrade to Pro</button>` : ""}`;
  return html`<p class="small muted">Paid plans aren't open yet — during the pilot, plans are set by invitation.</p>`;
}
function wireAccount() {
  $$("[data-up]").forEach(b => b.onclick = async () => {
    busy(b, true);
    try { const r = await post("/api/billing/checkout", {plan: b.dataset.up}); if (r.url) location.assign(r.url); }
    catch (e) { busy(b, false); $("#bill-err").textContent = "Couldn't start checkout — try again."; }
  });
  const mg = $("#bill-manage");
  if (mg) mg.onclick = async () => { busy(mg, true); try { const r = await post("/api/billing/portal", {}); if (r.url) location.assign(r.url); } catch (e) { busy(mg, false); $("#bill-err").textContent = "Billing portal unavailable — try again later."; } };
  const tf = $("#terr-form");
  if (tf) tf.onsubmit = async ev => {
    ev.preventDefault(); const b = tf.querySelector("button"); const msg = $("#terr-msg");
    const z = $("#t-zip").value.trim(), r = Number($("#t-radius").value);
    if (!/^\d{5}$/.test(z)) { msg.className = "small error-text"; msg.textContent = "Enter a 5-digit ZIP."; return; }
    if (!(r >= 10 && r <= 500)) { msg.className = "small error-text"; msg.textContent = "Radius must be 10–500 miles."; return; }
    busy(b, true);
    try { S.territory = await post("/api/me/territory", {home_zip: z, radius_mi: r}); msg.className = "small pos"; msg.textContent = "Saved."; }
    catch (e) { msg.className = "small error-text"; msg.textContent = e.message; }
    busy(b, false);
  };
  const af = $("#assume-form");
  if (af) af.onsubmit = async ev => {
    ev.preventDefault(); const b = af.querySelector("button[type=submit]"); const msg = $("#assume-msg");
    const body = {}; $$("#assume-form [data-k]").forEach(i => body[i.dataset.k] = i.value === "" ? null : Number(i.value));
    busy(b, true);
    try { await post("/api/me/profit_settings", body); msg.className = "small pos"; msg.textContent = "Saved — estimates now use your numbers."; }
    catch (e) { msg.className = "small error-text"; msg.textContent = e.status === 402 ? "Custom assumptions are part of Lite and Pro." : e.message; }
    busy(b, false);
  };
  const rs = $("#assume-reset");
  if (rs) rs.onclick = async () => {
    if (!confirm("Reset all cost assumptions to FlipFirst's defaults?")) return;
    const body = {}; ASSUME_ORDER.forEach(k => body[k] = null);
    busy(rs, true);
    try { await post("/api/me/profit_settings", body); toast("Assumptions reset to defaults.", {kind: "ok"}); route(); }
    catch (e) { busy(rs, false); if (!planPrompt(e, "Custom assumptions")) toast(e.message, {kind: "bad"}); }
  };
  $$("[data-theme-opt]").forEach(b => b.onclick = () => {
    FF.setTheme(b.dataset.themeOpt);
    $$("[data-theme-opt]").forEach(x => { const on = x === b; x.setAttribute("aria-checked", on); });
  });
}

/* ---------------- global wiring ---------------- */
document.addEventListener("click", e => {
  const back = e.target.closest("[data-back]");
  if (back) { e.preventDefault(); goBack(); return; }
  const so = e.target.closest("[data-signout]");
  if (so) { e.preventDefault(); signOut(); return; }
  const retry = e.target.closest("[data-retry]");
  if (retry) { e.preventDefault(); route(); return; }
  const cp = e.target.closest("[data-copy]");
  if (cp) { e.preventDefault(); FF.copyText(cp.dataset.copy).then(ok => toast(ok ? "Copied." : "Couldn't copy — select and copy it manually.", {kind: ok ? "ok" : "bad"})); return; }
  const act = e.target.closest("[data-act]");
  if (act) {
    e.preventDefault();
    const holder = act.closest("[data-cid]");
    const cid = act.dataset.cid || (holder && holder.dataset.cid);
    if (cid) dealAction(cid, act.dataset.act, act);
  }
});
document.addEventListener("change", async e => {
  const sel = e.target.closest("[data-stage]");
  if (!sel) return;
  const holder = sel.closest("[data-cid]");
  const cid = holder && holder.dataset.cid;
  if (!cid) return;
  try { await post(`/api/deals/${encodeURIComponent(cid)}/action`, {action: sel.value === "saved" ? "save" : sel.value});
        toast(`Moved to ${STAGE_NAME[sel.value] || sel.value}.`, {kind: "ok"}); }
  catch (err) { if (!planPrompt(err, ACT_ERR)) toast(err.message, {kind: "bad"}); }
});
async function signOut() {
  try { await post("/api/auth/logout", {}); } catch (e) {}
  location.replace("/login");
}

async function verifyLink(h) {
  const m = h.match(/^#verify=([A-Za-z0-9_-]+)$/);
  history.replaceState(null, "", "/m#/sell");
  if (m) {
    try {
      const r = await post("/api/seller/contact/verify", {token: m[1]});
      toast(`Email confirmed.${r.published ? ` ${r.published} listing${r.published > 1 ? "s" : ""} published.` : ""}`, {kind: "ok", ms: 7000});
    } catch (e) { toast(e.message, {kind: "bad", ms: 8000}); }
  }
  route();
}
async function refreshUnseen() {
  try {
    const rows = await api("/api/me/alerts?limit=50");
    const k = rows.filter(r => !r.seen).length;
    $$("[data-unseen]").forEach(b => { b.hidden = !k; b.textContent = k > 9 ? "9+" : String(k); b.setAttribute("aria-label", `${k} new`); });
  } catch (e) {}
}
function billingReturn() {
  const ret = new URLSearchParams(location.search).get("billing_return");
  if (!ret) return;
  history.replaceState(null, "", "/m" + location.hash);
  if (ret === "success") {
    toast("Payment received — your plan updates as soon as Stripe confirms it.", {kind: "ok", ms: 8000});
    for (const wait of [3000, 8000]) setTimeout(async () => {
      try { const me = await api("/api/me"); S.plan = me.user.plan || "free"; topbar(); } catch (x) {}
    }, wait);
  } else if (ret === "cancel") toast("Checkout canceled — nothing was charged.");
}

/* ---------------- boot ---------------- */
(async function boot() {
  let me;
  try { me = await api("/api/me"); } catch (e) { return; }
  S.me = me.user || {}; S.plan = S.me.plan || "free"; S.isAdmin = S.me.role === "admin";
  $$("[data-admin]").forEach(a => { a.hidden = !S.isAdmin; });
  billingReturn();
  const [searches, terr] = await Promise.all([api("/api/saved_searches").catch(() => []), api("/api/me/territory").catch(() => null)]);
  S.searches = searches || []; S.territory = terr;
  refreshUnseen();
  if (!location.hash || location.hash === "#" || location.hash === "#/") location.replace(S.searches.length ? "#/discover/mine" : "#/discover/trusted");
  else route();
})();
})();
