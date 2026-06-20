const SUPPORTED_HOST_SUBSTRINGS = [
  "facebook.com/marketplace",
  "offerup.com",
  "nextdoor.com",
  "cars.com",
  "autotrader.com",
  "cargurus.com",
  "carsandbids.com",
];

function hostOK(url) {
  if (!url) return false;
  return SUPPORTED_HOST_SUBSTRINGS.some(s => url.includes(s));
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  return tab;
}

function setOut(html) {
  document.getElementById("out").innerHTML = html;
}

document.getElementById("push").addEventListener("click", async () => {
  const btn = document.getElementById("push");
  btn.disabled = true; btn.textContent = "Pushing…"; setOut("");
  try {
    const tab = await activeTab();
    if (!hostOK(tab.url)) {
      setOut('<span class="bad">Open a supported site first (FB Marketplace, OfferUp, Nextdoor, Cars.com, AutoTrader, CarGurus, Cars &amp; Bids).</span>');
      return;
    }
    const r = await chrome.tabs.sendMessage(tab.id, {action: "push", autoScroll: true});
    setOut(`<span class="ok">[${r.platform}] found ${r.found}, pushed ${r.pushed}` +
           (r.failed ? `, ${r.failed} failed` : "") + ".</span>");
  } catch (e) {
    setOut('<span class="bad">Error: ' + e.message + '</span>');
  } finally {
    btn.disabled = false; btn.textContent = "Push visible listings (auto-scroll)";
  }
});

document.getElementById("auto_start").addEventListener("click", async () => {
  setOut("");
  try {
    const tab = await activeTab();
    if (!hostOK(tab.url)) {
      setOut('<span class="bad">Open a supported site first.</span>'); return;
    }
    const every = Math.max(30, parseInt(document.getElementById("every").value || "120", 10));
    const r = await chrome.tabs.sendMessage(tab.id, {action: "auto_push_start", every_sec: every});
    setOut(`<span class="ok">Auto-push running every ${r.every_sec}s on this tab. Leave it open.</span>`);
  } catch (e) {
    setOut('<span class="bad">Error: ' + e.message + '</span>');
  }
});

document.getElementById("auto_stop").addEventListener("click", async () => {
  setOut("");
  try {
    const tab = await activeTab();
    const r = await chrome.tabs.sendMessage(tab.id, {action: "auto_push_stop"});
    setOut(`<span class="ok">Auto-push stopped.</span>`);
  } catch (e) {
    setOut('<span class="bad">Error: ' + e.message + '</span>');
  }
});
