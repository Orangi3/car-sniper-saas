"""
tests/test_brand.py — the FlipFirst identity is one coherent, generated set.

- every committed logo file is exactly what brand/build.py generates;
- every brand file a page or the stylesheet points at exists and is served;
- the previous mark and the old text wordmark are gone;
- PNG and ICO sizes are right, and the favicon holds 16, 32 and 48 px;
- the colour tokens keep WCAG AA contrast in both themes;
- push alerts carry the app icon, not emoji, and only from an HTTPS origin.
"""
from __future__ import annotations

import importlib.util
import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["mobile.html", "login.html", "ops.html", "dashboard.html"]


def _build():
    spec = importlib.util.spec_from_file_location("brand_build", ROOT / "brand" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_logo_files_match_the_generator():
    stale = [rel for rel, text in _build().assets() if (ROOT / rel).read_text(encoding="utf-8") != text]
    assert not stale, f"run python3 brand/build.py (and brand/render.js for PNGs): {stale}"


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", data[16:24])


@pytest.mark.parametrize("name,size", [
    ("favicon-32.png", (32, 32)), ("apple-touch-icon.png", (180, 180)), ("icon-192.png", (192, 192)),
    ("icon-512.png", (512, 512)), ("icon-maskable-512.png", (512, 512)), ("og-image.png", (1200, 630)),
])
def test_png_sizes(name, size):
    assert _png_size(ROOT / "static" / "brand" / name) == size


def test_favicon_ico_holds_16_32_48():
    data = (ROOT / "static" / "brand" / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind, count) == (0, 1, 3)
    sizes = sorted(data[6 + 16 * i] for i in range(count))
    assert sizes == [16, 32, 48]


def _referenced_brand_files():
    refs = set()
    for page in PAGES + ["static/app.js"]:
        refs |= set(re.findall(r"/static/brand/([\w.-]+)", (ROOT / page).read_text()))
    refs |= set(re.findall(r'url\("brand/([\w.-]+)"\)', (ROOT / "static" / "ff.css").read_text()))
    return refs


def test_every_referenced_brand_file_exists_and_is_served(client):
    refs = _referenced_brand_files()
    assert {"emblem-mask.svg", "wordmark-flip.svg", "wordmark-first.svg", "favicon.svg"} <= refs
    for name in sorted(refs):
        r = client.get(f"/static/brand/{name}")
        assert r.status_code == 200, name
        assert r.mimetype in ("image/svg+xml", "image/png", "image/x-icon", "image/vnd.microsoft.icon"), (name, r.mimetype)


def test_the_previous_mark_and_text_wordmark_are_gone():
    old_path = "M16.5 18.5H37V12.5L50.5 23.25"            # the arrow-F mark this identity replaces
    served = [p for p in (ROOT / "static").rglob("*") if p.is_file() and p.suffix in (".svg", ".css", ".js", ".webmanifest")]
    for p in served + [ROOT / page for page in PAGES]:
        text = p.read_text(errors="ignore")
        assert old_path not in text, p
        assert "Flip<b>First</b>" not in text, p             # the old typeset wordmark
        assert "#2454F5" not in text.upper(), p              # the old electric-blue accent
    assert not (ROOT / "static" / "brand" / "app-icon.svg").exists()


def test_social_preview_points_at_a_real_image():
    login = (ROOT / "login.html").read_text()
    m = re.search(r'<meta property="og:image" content="https://app\.tryflipfirst\.com(/static/brand/[\w.-]+)">', login)
    assert m and (ROOT / m.group(1).lstrip("/")).exists()
    assert 'name="twitter:card" content="summary_large_image"' in login


def test_manifest_uses_the_carbon_palette():
    import json
    man = json.loads((ROOT / "static" / "manifest.webmanifest").read_text())
    assert man["theme_color"] == "#0E0F11" and man["background_color"] == "#0B0B0C"
    assert any(i.get("purpose") == "maskable" for i in man["icons"])


# ---------- colour contrast (WCAG 2.1 AA) --------------------------------------
def _lum(h):
    h = h.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= .03928 else ((c + .055) / 1.055) ** 2.4   # noqa: E731
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b)


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + .05) / (lb + .05)


def _themes():
    css = (ROOT / "static" / "ff.css").read_text()
    light_block = css[css.index(":root {"):css.index("}", css.index(":root {"))]
    dark_block = css[css.index(':root[data-theme="dark"] {'):]
    dark_block = dark_block[:dark_block.index("}")]
    tok = lambda block: dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})\b", block))   # noqa: E731
    light = tok(light_block)
    dark = {**light, **tok(dark_block)}
    return {"light": light, "dark": dark}


PAIRS = [   # (text or UI colour, background, minimum)
    ("ink", "bg", 7), ("ink-2", "surface", 7), ("muted", "surface", 4.5), ("muted", "bg", 4.5),
    ("muted", "surface-3", 4.5), ("accent", "surface", 4.5), ("accent", "bg", 4.5),
    ("accent", "accent-soft", 4.5), ("accent", "surface-2", 4.5), ("on-accent", "accent-strong", 4.5),
    ("accent-strong", "surface", 3), ("on-primary", "primary", 7), ("profit", "surface", 4.5),
    ("profit", "profit-soft", 4.5), ("warn", "warn-soft", 4.5), ("danger", "danger-soft", 4.5),
    ("danger", "surface", 4.5),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_colour_tokens_meet_wcag_aa(theme):
    t = _themes()[theme]
    low = [(fg, bg, round(_ratio(t[fg], t[bg]), 2)) for fg, bg, mn in PAIRS if _ratio(t[fg], t[bg]) < mn]
    assert not low, low


def test_the_light_and_dark_dark_blocks_agree():
    css = (ROOT / "static" / "ff.css").read_text()
    media = css[css.index("@media (prefers-color-scheme: dark)"):css.index(':root[data-theme="dark"] {')]
    explicit = css[css.index(':root[data-theme="dark"] {'):]
    explicit = explicit[:explicit.index("}")]
    pick = lambda b: dict(re.findall(r"--([\w-]+):\s*([^;]+);", b))   # noqa: E731
    assert pick(media) == pick(explicit)                        # system dark == chosen dark


# ---------- push alerts ---------------------------------------------------------
def _capture_ntfy(monkeypatch):
    import notifications
    sent = {}

    class R:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    def fake_post(url, **kw):
        sent["payload"] = kw.get("json")
        return R()
    monkeypatch.setattr(notifications, "_post", fake_post)
    monkeypatch.setattr(notifications, "_check_response", lambda r, name: name)
    return notifications, sent


def test_push_alerts_carry_the_app_icon_not_emoji(monkeypatch):
    notifications, sent = _capture_ntfy(monkeypatch)
    monkeypatch.setenv("BILLING_ORIGIN", "https://app.tryflipfirst.com/")
    notifications._send_ntfy("flipfirst-test", "2015 Toyota Camry · $7,600", "body", "https://example.com/x")
    p = sent["payload"]
    assert "tags" not in p
    assert p["icon"] == "https://app.tryflipfirst.com/static/brand/icon-192.png"
    assert p["click"] == "https://example.com/x" and p["priority"] == 4


@pytest.mark.parametrize("origin", ["", "http://app.tryflipfirst.com", "app.tryflipfirst.com"])
def test_no_icon_without_an_https_origin(monkeypatch, origin):
    notifications, sent = _capture_ntfy(monkeypatch)
    monkeypatch.setenv("BILLING_ORIGIN", origin)
    notifications._send_ntfy("flipfirst-test", "t", "b", "")
    assert "icon" not in sent["payload"] and "tags" not in sent["payload"]


# ---------- vector files are valid, self-contained and safe ---------------------
def _svgs():
    return sorted([*(ROOT / "static" / "brand").glob("*.svg"), *(ROOT / "brand" / "exports").glob("*.svg")])


def test_every_svg_is_well_formed_self_contained_and_script_free():
    import xml.etree.ElementTree as ET
    assert len(_svgs()) >= 20
    for p in _svgs():
        text = p.read_text(encoding="utf-8")
        root = ET.fromstring(text)                                   # raises on malformed XML
        assert root.tag == "{http://www.w3.org/2000/svg}svg", p
        vb = [float(v) for v in root.get("viewBox").split()]
        assert len(vb) == 4 and vb[2] > 0 and vb[3] > 0, p
        assert not re.search(r"\b(nan|inf)\b", text, re.I), p
        assert "<script" not in text and not re.search(r"\son\w+=", text), p     # no script or event handlers
        assert not re.search(r'href="(https?:)?//', text), p                     # nothing loaded from elsewhere


def test_favicon_is_the_pixel_snapped_micro_emblem():
    fav = (ROOT / "static" / "brand" / "favicon.svg").read_text()
    assert 'viewBox="0 0 16 16"' in fav                              # drawn on the 16 px grid


# ---------- Safari: every mask also has its -webkit- form -------------------------
@pytest.mark.parametrize("path", ["static/ff.css", "dashboard.html"])
def test_masks_carry_webkit_prefixes(path):
    css = (ROOT / path).read_text()
    for block in re.findall(r"\{([^{}]*)\}", css):
        decls = [d.strip() for d in block.split(";")]
        for prop in ("mask", "mask-image"):
            if any(d.startswith(prop + ":") for d in decls):
                assert any(d.startswith("-webkit-" + prop + ":") for d in decls), (path, block[:120])


# ---------- keyboard: scrollable tables can be reached and scrolled ---------------
@pytest.mark.parametrize("path", ["static/ops.js", "static/app.js"])
def test_scrollable_tables_are_focusable_named_regions(path):
    js = (ROOT / path).read_text()
    wraps = re.findall(r'<div class="table-wrap"[^>]*>', js)
    assert wraps, path
    for w in wraps:
        assert 'tabindex="0"' in w and 'role="region"' in w and "aria-labelledby=" in w, w


def test_app_shell_has_a_link_preview_too():
    shell = (ROOT / "mobile.html").read_text()
    assert '<meta property="og:image" content="https://app.tryflipfirst.com/static/brand/og-image.png">' in shell
    login = (ROOT / "login.html").read_text()
    assert '<meta property="og:url" content="https://app.tryflipfirst.com/login">' in login
