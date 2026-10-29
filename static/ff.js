/* FlipFirst shared helpers (no framework). Everything that renders data
   goes through html``: interpolated values are escaped unless wrapped in
   raw() — the same discipline as the old esc() calls, but by default. */
"use strict";
(function (w) {
  const ASSET_V = (document.documentElement.dataset.v || "");

  class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
  const raw = s => new Raw(String(s == null ? "" : s));
  const esc = v => String(v == null ? "" : v).replace(/[&<>"']/g,
    c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
  const render = v => v instanceof Raw ? v.s : Array.isArray(v) ? v.map(render).join("")
    : (v == null || v === false) ? "" : esc(v);
  const html = (strings, ...vals) => raw(strings.reduce((out, s, i) => out + s + (i < vals.length ? render(vals[i]) : ""), ""));
  const setHTML = (el, h) => { el.innerHTML = String(h); return el; };

  const safeUrl = u => /^https?:\/\//i.test(String(u || "")) ? String(u) : null;
  const icon = (name, cls) => raw(`<svg class="ico${cls ? " " + esc(cls) : ""}" aria-hidden="true" focusable="false"><use href="/static/icons.svg?v=${esc(ASSET_V)}#i-${esc(name)}"></use></svg>`);

  const n = v => (v == null || v === "" || isNaN(Number(v))) ? null : Number(v);
  const money = v => n(v) == null ? "—" : (n(v) < 0 ? "−$" : "$") + Math.round(Math.abs(n(v))).toLocaleString("en-US");
  const moneyK = v => {
    if (n(v) == null) return "—";
    const a = Math.abs(n(v)), s = n(v) < 0 ? "−" : "";
    return a >= 10000 ? `${s}$${(a / 1000).toFixed(a >= 100000 ? 0 : 1).replace(/\.0$/, "")}k` : s + "$" + Math.round(a).toLocaleString("en-US");
  };
  const num = v => n(v) == null ? "—" : Math.round(n(v)).toLocaleString("en-US");
  const pct = v => n(v) == null ? "—" : Math.round(n(v)) + "%";
  const ago = iso => {
    if (!iso) return "";
    const t = new Date(iso).getTime(); if (isNaN(t)) return "";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    if (s < 86400 * 30) return Math.round(s / 86400) + "d ago";
    return new Date(t).toLocaleDateString("en-US", {month: "short", day: "numeric"});
  };
  const hoursSince = iso => { const t = new Date(iso || "").getTime(); return isNaN(t) ? null : (Date.now() - t) / 3.6e6; };

  class ApiError extends Error { constructor(msg, status, body) { super(msg); this.status = status; this.body = body || {}; } }
  async function api(url, opts) {
    const o = Object.assign({credentials: "same-origin", headers: {"Content-Type": "application/json", "Accept": "application/json"}}, opts || {});
    let r;
    try { r = await fetch(url, o); }
    catch (e) { throw new ApiError("You're offline or the server can't be reached. Try again in a moment.", 0); }
    if (r.status === 401 && !(opts && opts.allow401)) { location.href = "/login"; throw new ApiError("Signed out", 401); }
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new ApiError(body.error || (body.errors && body.errors.join("; ")) || friendlyStatus(r.status), r.status, body);
    return body;
  }
  const friendlyStatus = s => s === 402 ? "This needs a paid plan." : s === 403 ? "You don't have access to that."
    : s === 404 ? "Not found." : s === 429 ? "Too many attempts — please wait a moment." : s >= 500 ? "Something went wrong on our side. Try again." : "Request failed.";
  const post = (url, body) => api(url, {method: "POST", body: JSON.stringify(body || {})});

  // ---- toasts ----------------------------------------------------------
  function toast(msg, opts) {
    opts = opts || {};
    let box = document.querySelector(".toasts");
    if (!box) { box = document.createElement("div"); box.className = "toasts"; box.setAttribute("role", "status"); box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
    const t = document.createElement("div");
    t.className = "toast" + (opts.kind ? " toast--" + opts.kind : "");
    setHTML(t, html`${icon(opts.kind === "bad" ? "alert" : opts.kind === "ok" ? "check" : "info")}<span class="toast__msg">${msg}</span>${opts.action ? html`<button type="button">${opts.action.label}</button>` : ""}`);
    if (opts.action) t.querySelector("button").onclick = () => { t.remove(); opts.action.run(); };
    box.appendChild(t);
    setTimeout(() => t.remove(), opts.ms || (opts.action ? 6000 : 3500));
  }

  // ---- theme -----------------------------------------------------------
  const THEME_KEY = "ff-theme";
  const getTheme = () => { try { return localStorage.getItem(THEME_KEY) || "system"; } catch (e) { return "system"; } };
  function setTheme(t) {
    try { t === "system" ? localStorage.removeItem(THEME_KEY) : localStorage.setItem(THEME_KEY, t); } catch (e) {}
    if (t === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
  }

  // ---- misc ------------------------------------------------------------
  const $ = (s, root) => (root || document).querySelector(s);
  const $$ = (s, root) => Array.from((root || document).querySelectorAll(s));
  function busy(btn, on) { if (!btn) return; btn.setAttribute("aria-busy", on ? "true" : "false"); btn.disabled = !!on; }
  function focusMain() { const m = $("#main"); if (m) { m.setAttribute("tabindex", "-1"); m.focus({preventScroll: true}); } }
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) { return false; }
  }

  w.FF = {raw, esc, html, setHTML, safeUrl, icon, money, moneyK, num, pct, ago, hoursSince, api, post,
          ApiError, toast, getTheme, setTheme, $, $$, busy, focusMain, copyText, n, ASSET_V};
})(window);
