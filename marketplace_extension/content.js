// content.js — Sniper Bridge content script.
// Runs on FB Marketplace, OfferUp, Nextdoor, Cars.com, AutoTrader, CarGurus, Cars & Bids.
// On user-initiated "Push" (or auto-push timer), scrapes the visible vehicle
// listings in the current logged-in session and POSTs them to the local sniper.
//
// v0.3.0 adds:
//   - Cars.com, AutoTrader, CarGurus, Cars & Bids scrapers
//   - autoScroll() — scrolls the results list N times to load more listings
//                    before scraping (lazy-loaded grids no longer cut off)
//   - is_dealer flag set per-platform (dealer sites = true; private-party = false)
//   - is_cash_only inferred from is_dealer
//   - per-listing platform tagging stays consistent with marketplace_import.py

const SNIPER_URL = "http://127.0.0.1:8765/api/import";
const AUTOSCROLL_STEPS = 12;     // ~12 page-downs is enough for most grids
const AUTOSCROLL_PAUSE_MS = 600; // let lazy-load fire

function detectPlatform() {
  const h = location.hostname;
  if (h.includes("facebook.com"))    return "facebook";
  if (h.includes("offerup.com"))     return "offerup";
  if (h.includes("nextdoor.com"))    return "nextdoor";
  if (h.includes("cars.com"))        return "cars_com";
  if (h.includes("autotrader.com"))  return "autotrader";
  if (h.includes("cargurus.com"))    return "cargurus";
  if (h.includes("carsandbids.com")) return "carsandbids";
  return "unknown";
}

function parsePrice(text) {
  if (!text) return null;
  const m = String(text).match(/\$\s?([\d,]+)/);
  return m ? parseInt(m[1].replace(/,/g, ""), 10) : null;
}
function parseMiles(text) {
  if (!text) return null;
  const m = String(text).match(/([\d,]+)\s*(?:mi|miles|mileage)\b/i);
  return m ? parseInt(m[1].replace(/,/g, ""), 10) : null;
}
function parseYear(text) {
  if (!text) return null;
  const m = String(text).match(/\b(19\d\d|20[0-3]\d)\b/);
  return m ? parseInt(m[1], 10) : null;
}

// ---------- auto-scroll ----------------------------------------------------
async function autoScroll(steps = AUTOSCROLL_STEPS, pauseMs = AUTOSCROLL_PAUSE_MS) {
  for (let i = 0; i < steps; i++) {
    window.scrollBy(0, window.innerHeight * 0.9);
    await new Promise(r => setTimeout(r, pauseMs));
  }
  window.scrollTo(0, 0);
  // Settle so DOM queries see the freshly-loaded nodes
  await new Promise(r => setTimeout(r, 400));
}

// ---------- scrapers ------------------------------------------------------

function scrapeFacebook() {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href*="/marketplace/item/"]').forEach((a) => {
    const href = a.href.split("?")[0];
    if (seen.has(href)) return; seen.add(href);
    const card = a.closest("div[role='article']") || a.closest("div") || a;
    const text = (card.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    const price = parsePrice(text); if (!price) return;
    const idMatch = href.match(/\/item\/(\d+)/);
    const id = idMatch ? idMatch[1] : href;
    const title = text.slice(0, 200).replace(/\s*\$\s?[\d,]+.*$/, "").trim();
    out.push({
      id, url: href, title, price,
      year: parseYear(title), miles: parseMiles(text),
      description: text, platform: "facebook",
      is_dealer: false,
      posted_at: new Date().toISOString(),
    });
  });
  return out;
}

function scrapeOfferUp() {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href*="/item/detail/"]').forEach((a) => {
    const href = (a.href || "").split("?")[0];
    if (!href || seen.has(href)) return; seen.add(href);
    const card = a.closest("li") || a.closest("article") || a.closest("div") || a;
    const text = (card.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    const price = parsePrice(text); if (!price) return;
    const idMatch = href.match(/\/item\/detail\/([^\/?#]+)/);
    const id = idMatch ? idMatch[1] : href;
    const title = text.slice(0, 200).replace(/\$\s?[\d,]+/, "").trim();
    out.push({
      id, url: href, title, price,
      year: parseYear(title), miles: parseMiles(text),
      description: text, platform: "offerup",
      is_dealer: false,
      posted_at: new Date().toISOString(),
    });
  });
  return out;
}

function scrapeNextdoor() {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href*="/for_sale_and_free/"], a[href*="/p/"], a[href*="/post/"]').forEach((a) => {
    const href = (a.href || "").split("?")[0];
    if (!href || seen.has(href)) return; seen.add(href);
    const card = a.closest("article") || a.closest("div[role='article']") || a.closest("div") || a;
    const text = (card.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    const price = parsePrice(text); if (!price) return;
    const id = href.split("/").filter(Boolean).slice(-1)[0] || href;
    const title = text.slice(0, 200).replace(/\$\s?[\d,]+/, "").trim();
    out.push({
      id, url: href, title, price,
      year: parseYear(title), miles: parseMiles(text),
      description: text, platform: "nextdoor",
      is_dealer: false,
      posted_at: new Date().toISOString(),
    });
  });
  return out;
}

// Cars.com — listing anchors live at /vehicledetail/{id}/
function scrapeCarsCom() {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href*="/vehicledetail/"]').forEach((a) => {
    const href = new URL(a.href, location.origin).href.split("?")[0];
    if (seen.has(href)) return; seen.add(href);
    const card = a.closest("[class*='vehicle-card']") || a.closest("article") ||
                 a.closest("li") || a.closest("div") || a;
    const text = (card.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    const price = parsePrice(text); if (!price) return;
    const idMatch = href.match(/\/vehicledetail\/([^\/?#]+)/);
    const id = idMatch ? idMatch[1] : href;
    // Cars.com cards typically lead with "{year} {make} {model} ..."
    const titleMatch = text.match(/\b(19\d\d|20[0-3]\d)\b[^$\n]{0,120}/);
    const title = (titleMatch ? titleMatch[0] : text.slice(0, 140)).trim();
    out.push({
      id, url: href, title, price,
      year: parseYear(title), miles: parseMiles(text),
      description: text, platform: "cars_com",
      is_dealer: true,
      accepts_financing: true,
      posted_at: new Date().toISOString(),
    });
  });
  return out;
}

// AutoTrader — listing anchors include /cars-for-sale/vehicle/{id}
function scrapeAutoTrader() {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href*="/cars-for-sale/vehicle/"], a[data-cmp="inventoryListing"]').forEach((a) => {
    const href = new URL(a.href, location.origin).href.split("?")[0];
    if (seen.has(href)) return; seen.add(href);
    const card = a.closest("[data-cmp='inventoryListing']") || a.closest("article") ||
                 a.closest("div[class*='inventory']") || a.closest("li") || a.closest("div") || a;
    const text = (card.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    const price = parsePrice(text); if (!price) return;
    const idMatch = href.match(/\/vehicle\/([^\/?#]+)/);
    const id = idMatch ? idMatch[1] : href;
    const titleMatch = text.match(/\b(19\d\d|20[0-3]\d)\b[^$\n]{0,120}/);
    const title = (titleMatch ? titleMatch[0] : text.slice(0, 140)).trim();
    out.push({
      id, url: href, title, price,
      year: parseYear(title), miles: parseMiles(text),
      description: text, platform: "autotrader",
      is_dealer: true,
      accepts_financing: true,
      posted_at: new Date().toISOString(),
    });
  });
  return out;
}

// CarGurus — anchors include /Cars/inventorylisting/viewDetailsFilterViewInventoryListing.action or /VDP/
function scrapeCarGurus() {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href*="/Cars/inventorylisting/"], a[href*="/Cars/link/"], a[href*="/VDP/"]').forEach((a) => {
    const href = new URL(a.href, location.origin).href.split("?")[0];
    if (seen.has(href)) return; seen.add(href);
    const card = a.closest("[data-testid*='listing']") || a.closest("article") ||
                 a.closest("div[class*='listing']") || a.closest("li") || a.closest("div") || a;
    const text = (card.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    const price = parsePrice(text); if (!price) return;
    // CarGurus listing IDs are in the parent card data attrs
    const id = (card.getAttribute && (card.getAttribute("data-listing-id") ||
                card.getAttribute("data-cg-ft-pin"))) || href;
    const titleMatch = text.match(/\b(19\d\d|20[0-3]\d)\b[^$\n]{0,120}/);
    const title = (titleMatch ? titleMatch[0] : text.slice(0, 140)).trim();
    // CarGurus tags each listing as "Great Deal" / "Good Deal" / "Fair Deal" / "Overpriced"
    const cgDeal = (text.match(/(Great|Good|Fair)\s+Deal|Overpriced/i) || [null])[0];
    out.push({
      id, url: href, title, price,
      year: parseYear(title), miles: parseMiles(text),
      description: text, platform: "cargurus",
      is_dealer: true,
      accepts_financing: true,
      cargurus_deal: cgDeal,
      posted_at: new Date().toISOString(),
    });
  });
  return out;
}

// Cars & Bids — live auctions. Anchors at /auctions/{slug}
function scrapeCarsAndBids() {
  const out = []; const seen = new Set();
  document.querySelectorAll('a[href^="/auctions/"], a[href*="carsandbids.com/auctions/"]').forEach((a) => {
    const href = new URL(a.href, location.origin).href.split("?")[0];
    if (!href.includes("/auctions/") || seen.has(href)) return; seen.add(href);
    const card = a.closest("[class*='auction-item']") || a.closest("li") ||
                 a.closest("article") || a.closest("div") || a;
    const text = (card.innerText || "").replace(/\s+/g, " ").trim();
    if (!text) return;
    const price = parsePrice(text);   // current bid
    const slug = href.split("/auctions/")[1]?.split("/")[0] || href;
    const titleMatch = text.match(/\b(19\d\d|20[0-3]\d)\b[^$\n]{0,120}/);
    const title = (titleMatch ? titleMatch[0] : text.slice(0, 140)).trim();
    // Pull "X days" / "X hours" remaining → ISO end time (best effort)
    let endIso = null;
    const daysMatch = text.match(/(\d+)\s*d(?:ays?)?\b/i);
    const hoursMatch = text.match(/(\d+)\s*h(?:ours?)?\b/i);
    const minsMatch = text.match(/(\d+)\s*m(?:in(?:utes?)?)?\b/i);
    if (daysMatch || hoursMatch || minsMatch) {
      const ms = (parseInt(daysMatch?.[1] || 0) * 86400 +
                  parseInt(hoursMatch?.[1] || 0) * 3600 +
                  parseInt(minsMatch?.[1] || 0) * 60) * 1000;
      if (ms > 0) endIso = new Date(Date.now() + ms).toISOString();
    }
    out.push({
      id: slug, url: href, title, price,
      year: parseYear(title), miles: parseMiles(text),
      description: text, platform: "carsandbids",
      is_dealer: false,
      is_auction: true,
      auction_end_at: endIso,
      posted_at: new Date().toISOString(),
    });
  });
  return out;
}

// ---------- dispatch ------------------------------------------------------

const SCRAPERS = {
  facebook:    scrapeFacebook,
  offerup:     scrapeOfferUp,
  nextdoor:    scrapeNextdoor,
  cars_com:    scrapeCarsCom,
  autotrader:  scrapeAutoTrader,
  cargurus:    scrapeCarGurus,
  carsandbids: scrapeCarsAndBids,
};

async function pushAll({withAutoScroll = true} = {}) {
  const platform = detectPlatform();
  const scraper = SCRAPERS[platform];
  if (!scraper) return {found: 0, pushed: 0, failed: 0, error: "unsupported page"};

  if (withAutoScroll) {
    try { await autoScroll(); } catch (e) { /* keep going even if scroll throws */ }
  }
  const listings = scraper();

  let ok = 0, fail = 0;
  for (const l of listings) {
    try {
      const r = await fetch(SNIPER_URL, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(l),
      });
      if (r.ok) ok++; else fail++;
    } catch (e) { fail++; }
  }
  return {found: listings.length, pushed: ok, failed: fail, platform};
}

// ---------- auto-push timer (page-scoped, controlled by popup) ------------
let AUTO_PUSH_TIMER = null;
function startAutoPush(intervalSec) {
  stopAutoPush();
  const ms = Math.max(30, intervalSec | 0) * 1000;
  AUTO_PUSH_TIMER = setInterval(() => { pushAll({withAutoScroll: true}); }, ms);
  return {running: true, every_sec: ms / 1000};
}
function stopAutoPush() {
  if (AUTO_PUSH_TIMER) { clearInterval(AUTO_PUSH_TIMER); AUTO_PUSH_TIMER = null; }
  return {running: false};
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "push") {
    pushAll({withAutoScroll: msg.autoScroll !== false}).then((r) => sendResponse(r));
    return true;  // async
  }
  if (msg.action === "auto_push_start") {
    sendResponse(startAutoPush(msg.every_sec || 120));
    return false;
  }
  if (msg.action === "auto_push_stop") {
    sendResponse(stopAutoPush());
    return false;
  }
});
