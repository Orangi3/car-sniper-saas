/* FlipFirst operations console (/admin/ops). Renders /api/admin/ops only —
   values the app doesn't measure stay labeled "unavailable", never invented. */
"use strict";
(function () {
const {html, raw, setHTML, icon, money, num, ago, api, toast, $, $$} = FF;
let HOURS = 24;

// one status vocabulary for everything on the page
const ST = {
  ok: ["Healthy", "ok"], warn: ["Warning", "warn"], fail: ["Failed", "bad"],
  off: ["Not configured", "off"], never: ["Never tested", "off"], disabled: ["Disabled", "off"],
};
const pill = (key, text) => html`<span class="status status--${ST[key][1]}">${text || ST[key][0]}</span>`;
function sourceStatus(s) {
  const v = String(s.status || "");
  if (v === "healthy") return "ok";
  if (v === "never polled") return "never";
  if (v === "auth_config") return "off";
  if (v === "disabled") return "disabled";
  if (["blocked", "rate_limited", "timeout"].includes(v)) return "fail";
  return "warn";                       // unverified, degraded, empty, …
}
function checkStatus(c) {
  const d = String(c.detail || "").toLowerCase();
  if (c.status === "ok") return "ok";
  if (c.status === "fail") return /never/.test(d) ? "never" : "fail";
  if (/not set|not configured|missing/.test(d)) return "off";
  if (/never/.test(d)) return "never";
  return "warn";
}
const READY = {
  not_ready: ["Not ready", "bad", "A blocking check is failing — see System checks."],
  private_internal_testing: ["Ready for internal testing", "warn", "Core infrastructure works; customer-facing requirements are still open."],
  controlled_free_beta: ["Ready for a free beta", "ok", "Infrastructure and licensed data are in place; paid-plan requirements are still open."],
  controlled_paid_beta: ["Ready for a paid beta", "ok", "Every preflight check passes."],
};
const isNA = v => v && typeof v === "object" && "unavailable" in v;
const naText = v => html`<span class="na">unavailable — ${v.unavailable}</span>`;

function kpi(label, ic, value, sub, cls) {
  return html`<div class="kpi"><div class="kpi__top"><span>${label}</span>${icon(ic)}</div>
    <div class="kpi__value ${cls || ""}">${value}</div>${sub ? html`<div class="kpi__sub">${sub}</div>` : ""}</div>`;
}

async function load() {
  const main = $("#main");
  let d;
  try { d = await api(`/api/admin/ops?hours=${HOURS}`); }
  catch (e) {
    setHTML(main, html`<div class="card"><div class="empty"><div class="empty__icon">${icon("alert")}</div>
      <h3>Couldn't load operations data</h3><p>${e.status === 403 ? "This page is for the owner account." : e.message}</p>
      <button class="btn btn--primary" type="button" onclick="location.reload()">Try again</button></div></div>`);
    return;
  }
  const dh = d.data_health, di = d.deal_intelligence, sh = d.system_health, c = d.costs;
  const f = di.funnel, q = sh.queue.totals, nt = di.notifications;
  const checks = sh.checks || [];
  const byName = name => checks.find(x => String(x.check).toLowerCase().includes(name));
  const dbc = byName("database"), mig = byName("migration"), bak = byName("backup"), sched = byName("scheduler");
  const ready = READY[sh.ready_for] || [String(sh.ready_for || "unknown").replace(/_/g, " "), "warn", ""];
  const total = dh.sources.length;
  const windowLabel = HOURS === 24 ? "last 24 hours" : HOURS === 72 ? "last 3 days" : "last 7 days";

  // funnel chart — proportional bars from real counts only
  const steps = [["Discovered", f.discovered], ["Scored", f.scored], ["Valued", f.valued],
                 ["Potential opportunities", f.potential_opportunities, true],
                 ["High-confidence (completed sales)", f.high_confidence_opportunities, true],
                 ["Manually verified", f.manually_verified, true]];
  const top = Math.max(1, ...steps.map(s => Number(s[1]) || 0));
  const confOrder = [["high", "var(--profit)"], ["medium", "var(--accent-strong)"], ["low", "var(--warn)"], ["insufficient", "var(--line-strong)"], ["none", "var(--surface-3)"]];
  const confTotal = Object.values(di.by_confidence || {}).reduce((a, b) => a + b, 0);

  setHTML(main, html`
    <div class="page__head" style="margin-bottom:var(--s4)"><div><h1>Operations</h1>
      <p>${windowLabel} · generated ${new Date(d.generated_at).toLocaleString()} · values marked unavailable aren't measured by the app</p></div>
      <a class="btn btn--sm" href="/api/admin/ops?hours=${HOURS}" target="_blank" rel="noopener">${icon("file", "sm")}Raw snapshot</a></div>

    <div class="card readiness" style="margin-bottom:var(--s4)">
      <span class="status status--${ready[1]}" style="font-size:var(--fs-sm)">${ready[0]}</span>
      <span class="small muted" style="flex:1 1 260px">${ready[2]}</span>
      <div class="legend" aria-label="Status legend">${["ok", "warn", "fail", "off", "never"].map(k => pill(k))}</div>
    </div>

    <div class="kpis kpis--8">
      ${kpi("Sources working", "activity", `${dh.working.length} / ${total}`, dh.failing.length ? `${dh.failing.length} failing` : "none failing", dh.working.length ? "ok" : "warn")}
      ${kpi("Listings discovered", "search", num(f.discovered), `scored ${num(f.scored)} · valued ${num(f.valued)}`)}
      ${kpi("Potential opportunities", "trend", num(f.potential_opportunities), "estimated — not verified deals", f.potential_opportunities ? "ok" : "")}
      ${kpi("Alerts delivered", "bell", num(nt.delivered_to_provider), `${num(nt.failed_permanently)} failed · ${num(nt.queued)} queued`, nt.failed_permanently ? "bad" : "")}
      ${kpi("Job queue", "layers", num((q.pending || 0) + (q.running || 0)), `${num(q.dead || 0)} dead-lettered${sh.queue.retried ? ` · ${num(sh.queue.retried)} retried` : ""}`, q.dead ? "bad" : "")}
      ${kpi("Database", "database", dbc ? String(dbc.detail).split(/[ ,;(]/)[0] || "—" : "—", mig ? mig.detail : "", dbc && dbc.status !== "ok" ? "bad" : "")}
      ${kpi("Scheduler", "clock", sched ? (sched.status === "ok" ? "Ticking" : ST[checkStatus(sched)][0]) : "—", sched ? sched.detail : "", sched && sched.status !== "ok" ? "warn" : "ok")}
      ${kpi("Backups", "archive", bak ? ST[checkStatus(bak)][0] : "—", bak ? bak.detail : "", bak && bak.status !== "ok" ? "warn" : "ok")}
    </div>

    <div class="ops-grid">
      <section class="card" aria-labelledby="h-funnel"><div class="card__head"><h2 id="h-funnel">${icon("chart")}Deal pipeline</h2><span class="xsmall muted">${windowLabel}</span></div>
        <div class="card__body">
          ${steps.map(([l, v, good]) => html`<div class="hbar"><span>${l}</span><div class="hbar__track"><div class="hbar__fill${good ? " hbar__fill--profit" : ""}" style="width:${((Number(v) || 0) / top * 100).toFixed(1)}%"></div></div><span class="hbar__val">${num(v)}</span></div>`)}
          <p class="xsmall muted" style="margin-top:var(--s3)">Potential = trusted valuation and profitable in the conservative case, on default cost assumptions. High-confidence needs completed-sale evidence. Only “manually verified” has been checked by a person.</p>
        </div></section>

      <section class="card" aria-labelledby="h-conf"><div class="card__head"><h2 id="h-conf">${icon("shield")}Valuation quality</h2></div>
        <div class="card__body stack">
          ${confTotal ? html`<div class="stackbar" role="img" aria-label="Valuation confidence distribution">${confOrder.filter(([k]) => di.by_confidence[k]).map(([k, col]) => html`<span style="width:${(di.by_confidence[k] / confTotal * 100).toFixed(2)}%;background:${col}" title="${k}: ${di.by_confidence[k]}"></span>`)}</div>
            <div class="legend small">${confOrder.filter(([k]) => di.by_confidence[k]).map(([k, col]) => html`<span class="key"><i style="background:${col}"></i>${k} <b class="num">${num(di.by_confidence[k])}</b></span>`)}</div>`
            : html`<p class="small na">No valuations in this window.</p>`}
          <dl class="dl">
            <dt>Valued listings</dt><dd>${num(di.valued_listings)}</dd>
            <dt>Trusted valuations</dt><dd>${num(di.trusted_valuations)}</dd>
            <dt>Mean comparable quality</dt><dd>${isNA(di.mean_comparable_quality) ? naText(di.mean_comparable_quality) : di.mean_comparable_quality + " / 100"}</dd>
            <dt>Evidence</dt><dd style="white-space:normal">${Object.entries(di.by_evidence_class || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ") || "—"}</dd>
            <dt>Subscriber matches</dt><dd>${num(di.subscriber_matches)}</dd>
          </dl>
        </div></section>

      <section class="card span-all" aria-labelledby="h-src"><div class="card__head"><h2 id="h-src">${icon("server")}Data sources</h2>
          <span class="xsmall muted">${dh.vehicles_seen_on_multiple_listings} vehicles cross-posted</span></div>
        <div class="table-wrap"><table class="table">
          <thead><tr><th scope="col">Source</th><th scope="col">Status</th><th scope="col">Last real success</th><th scope="col" class="r">New (${HOURS}h)</th><th scope="col">Live probe</th></tr></thead>
          <tbody>${dh.sources.map(s => html`<tr>
            <td><span class="src-name">${s.source}</span>${s.commercially_blocked ? html`<span class="sub">not licensed for paid use</span>` : ""}</td>
            <td>${pill(sourceStatus(s), sourceStatus(s) === "warn" || sourceStatus(s) === "fail" ? String(s.status).replace(/_/g, " ") : null)}${s.reason ? html`<span class="sub">${s.reason}</span>` : ""}</td>
            <td>${s.last_genuine_success_at ? html`${ago(s.last_genuine_success_at)}<span class="sub">${new Date(s.last_genuine_success_at).toLocaleString()}</span>` : html`<span class="na">never</span>`}</td>
            <td class="r">${num(s.listings_first_seen_in_window)}</td>
            <td>${isNA(s.last_live_probe) ? html`<span class="na">none</span>` : html`${s.last_live_probe.status} · ${num(s.last_live_probe.listings)} listings<span class="sub">${ago(s.last_live_probe.checked_at)}</span>`}</td></tr>`)}</tbody>
        </table></div>
        <div class="card__foot xsmall muted">Duplicates rejected per poll: ${isNA(dh.duplicates_rejected_per_poll) ? naText(dh.duplicates_rejected_per_poll) : dh.duplicates_rejected_per_poll}</div></section>

      <section class="card" aria-labelledby="h-sys"><div class="card__head"><h2 id="h-sys">${icon("activity")}System checks</h2></div>
        <div class="card__body"><ul class="list" style="list-style:none;margin:0;padding:0">${checks.map(ch => html`<li class="list__row" style="align-items:flex-start">
            <div class="list__main"><div class="list__title">${String(ch.check).replace(/^\w/, x => x.toUpperCase())}</div><div class="list__sub">${ch.detail}</div></div>
            ${pill(checkStatus(ch))}</li>`)}</ul>
          ${(sh.recent_job_errors || []).length ? html`<details style="margin-top:var(--s3)"><summary class="small" style="cursor:pointer;font-weight:600">Recent job errors (${sh.recent_job_errors.length})</summary>
            <table class="table" style="margin-top:var(--s2)">${sh.recent_job_errors.map(e => html`<tr><td>${e.kind} ×${e.attempts}<span class="sub">${ago(e.updated_at)}</span></td><td style="word-break:break-word">${e.last_error}</td></tr>`)}</table></details>`
            : html`<p class="small muted" style="margin-top:var(--s3)">No job errors recorded.</p>`}
        </div></section>

      <section class="card" aria-labelledby="h-cost"><div class="card__head"><h2 id="h-cost">${icon("dollar")}Costs this month</h2></div>
        <div class="card__body stack">
          ${c.providers.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th scope="col">Provider</th><th scope="col" class="r">Calls</th><th scope="col" class="r">Budget left</th><th scope="col" class="r">Fees</th></tr></thead>
            <tbody>${c.providers.map(p => html`<tr><td>${p.source}</td><td class="r">${num(p.calls_this_month)}</td><td class="r">${num(p.remaining)} / ${num(p.budget)}</td><td class="r">${money(p.estimated_call_fees_usd)}</td></tr>`)}</tbody></table></div>` : ""}
          <dl class="dl dl--stack">
            <dt>Hosting</dt><dd style="white-space:normal">${isNA(c.hosting) ? naText(c.hosting) : money(c.hosting.value) + " (configured)"}</dd>
            <dt>Data platform</dt><dd style="white-space:normal">${isNA(c.data_platform_fee) ? naText(c.data_platform_fee) : money(c.data_platform_fee.value) + " (configured)"}</dd>
            <dt>Notifications</dt><dd style="white-space:normal">${isNA(c.notifications_this_month) ? naText(c.notifications_this_month) : Object.entries(c.notifications_this_month).map(([k, v]) => `${k} ${v.sent} (${money(v.estimated_usd)})`).join(" · ")}</dd>
            <dt>AI agent spend</dt><dd style="white-space:normal">${naText(c.ai_agent_spend)}</dd>
          </dl></div></section>

      <section class="card span-all" aria-labelledby="h-top"><div class="card__head"><h2 id="h-top">${icon("trend")}Top potential opportunities</h2><span class="xsmall muted">conservative estimate, default assumptions</span></div>
        ${di.top_by_conservative_profit.length ? html`<div class="table-wrap"><table class="table"><thead><tr><th scope="col">Vehicle</th><th scope="col">Source</th><th scope="col" class="r">Asking</th><th scope="col" class="r">Conservative</th><th scope="col" class="r">Base</th><th scope="col">Confidence</th></tr></thead>
          <tbody>${di.top_by_conservative_profit.map(t => html`<tr><td><a href="/m#/deal/${encodeURIComponent(t.composite_id)}">${t.vehicle}</a><span class="sub">${num(t.comps)} comparables</span></td><td>${t.source}</td>
            <td class="r">${money(t.price)}</td><td class="r pos">${money(t.conservative_profit)}</td><td class="r">${money(t.base_profit)}</td><td>${t.confidence}</td></tr>`)}</tbody></table></div>`
          : html`<div class="card__body"><p class="small na">No potential opportunities in this window.</p></div>`}
        <div class="card__foot xsmall muted">${di.profit_basis}. ${nt.note}.</div></section>
    </div>`);
}

$("#window").addEventListener("click", e => {
  const b = e.target.closest("[data-h]"); if (!b) return;
  HOURS = Number(b.dataset.h);
  $$("#window [data-h]").forEach(x => x.setAttribute("aria-checked", x === b ? "true" : "false"));
  load();
});
$("#refresh").addEventListener("click", () => { load().then(() => toast("Refreshed.", {kind: "ok", ms: 1500})); });
load();
})();
