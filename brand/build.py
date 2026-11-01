#!/usr/bin/env python3
"""brand/build.py — the FlipFirst identity, generated from one set of numbers.

Every logo file in static/brand/ and brand/exports/ comes from the geometry
below, so a change here moves the emblem, favicon, app icons, logotype and
social images together. Pure Python, no dependencies.

    python3 brand/build.py           # write the SVGs
    node brand/render.js             # render the PNG sizes (Playwright, as in tests/browser)
    python3 brand/build.py --ico     # pack static/brand/favicon.ico from the rendered PNGs

The emblem — "the Wings": an F and its mirror image set back to back. Their
bars sweep out and up into blade-cut wings, their stems run down the centre
like a lane line and end in a point. Flip (the mirrored F), First (wings,
gold). The logotype — FLIPFIRST in extended capitals drawn for FlipFirst (no
font file), FLIP in ivory or ink and FIRST in gold. brand/BRAND.md has the rules.
"""
from __future__ import annotations

import math
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(ROOT, "static", "brand")
EXPORTS = os.path.join(ROOT, "brand", "exports")

# ---------------------------------------------------------------- palette
CARBON = "#0E0F11"          # tiles, dark surfaces
IVORY = "#F2EEE4"           # FLIP on dark
INK = "#111215"             # FLIP on light
GOLD = "#D9B45A"            # signature gold: FIRST on dark, flat emblem
GOLD_DEEP = "#8A6719"       # FIRST on light (5.2:1 on white)

FOIL_DARK = [(0, "#F6E3A6"), (.28, "#E2BF6C"), (.52, "#B98A3A"), (.72, "#E7C97E"), (1, "#C6984A")]
FOIL_LIGHT = [(0, "#C9A04C"), (.3, "#A87F2C"), (.55, "#7E5C16"), (.75, "#A98232"), (1, "#8E6A1E")]
FACETS = {                  # polished-metal finish: upper blades catch the light
    "hi": [(0, "#FFF0C2"), (.5, "#F1D58C"), (1, "#FCE9B6")],
    "lo": [(0, "#C9993F"), (.5, "#A97C2C"), (1, "#C79A45")],
    "in": [(0, "#B3842F"), (1, "#8E6522")],
    "out": [(0, "#F3D997"), (1, "#D7B060")],
}

# ---------------------------------------------------------------- the emblem
# One half (the right-hand F); the other half is its mirror. Units: the stems
# are 100 tall, x = 0 is the centre line, y grows downward.
WINGS = {   # the full emblem, 48 px and up
    "full": dict(g=4, s=12, T=66, M=43, m=38, taper=.40, sweep=13, tipcut=14, point=.9),
    # shorter, thicker wings that stay solid at 16-32 px
    "bold": dict(g=5, s=15, T=56, M=36, m=40, taper=.50, sweep=10, tipcut=11, point=.9),
}


def _d(pts):
    return "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z"


def _mirror(pts):
    return [(-x, y) for x, y in pts][::-1]


def half(g, s, T, M, m, taper, sweep, tipcut, point):
    """Facets of the right-hand F: (tone, polygon)."""
    x0, x1, xm = g, g + s, g + s / 2
    out = [("in", [(x0, 0), (xm, 0), (xm, 100 - s * point / 2), (x0, 100)]),
           ("out", [(xm, 0), (x1, 0), (x1, 100 - s * point), (xm, 100 - s * point / 2)])]
    for y, L in ((0, T), (m, M)):
        tip = x1 + L
        P = (tip + tipcut, y - sweep)                     # the blade's point
        out.append(("hi", [(x1, y), P, (x1, y + s / 2)]))
        out.append(("lo", [(x1, y + s / 2), P, (tip, y - sweep + s * taper), (x1, y + s)]))
    return out


def emblem_polys(size="full"):
    h = half(**WINGS[size])
    return h + [(k, _mirror(p)) for k, p in h]


def emblem_box(size="full"):
    xs = [x for _, p in emblem_polys(size) for x, _ in p]
    ys = [y for _, p in emblem_polys(size) for _, y in p]
    return min(xs), min(ys), max(xs), max(ys)


def _grad(gid, stops, x1, y1, x2, y2, user=True):
    units = ' gradientUnits="userSpaceOnUse"' if user else ""
    s = "".join(f'<stop offset="{o:g}" stop-color="{c}"/>' for o, c in stops)
    return f'<linearGradient id="{gid}"{units} x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}">{s}</linearGradient>'


def emblem_defs(prefix="ff", foil=FOIL_DARK):
    return (_grad(f"{prefix}-foil", foil, -80, -20, 80, 110)
            + _grad(f"{prefix}-hi", FACETS["hi"], -90, -20, 90, 60)
            + _grad(f"{prefix}-lo", FACETS["lo"], -90, -10, 90, 70)
            + _grad(f"{prefix}-in", FACETS["in"], 0, 0, 0, 1, user=False)
            + _grad(f"{prefix}-out", FACETS["out"], 0, 0, 0, 1, user=False))


def emblem(cx, cy, width, size="full", finish="faceted", prefix="ff", color=GOLD):
    """The Wings, `width` wide, centred on (cx, cy). finish: faceted | foil | flat."""
    x0, y0, x1, y1 = emblem_box(size)
    sc = width / (x1 - x0)
    polys = emblem_polys(size)
    silhouette = "".join(_d(p) for _, p in polys)
    fill = {"foil": f"url(#{prefix}-foil)", "flat": color}.get(finish, f"url(#{prefix}-foil)")
    body = f'<path d="{silhouette}" fill="{fill}"/>'      # one path: no seams between facets
    if finish == "faceted":
        body += "".join(f'<path d="{_d(p)}" fill="url(#{prefix}-{k})"/>' for k, p in polys)
    ty = -(y0 + y1) / 2
    return f'<g transform="translate({cx:.3f} {cy:.3f}) scale({sc:.5f}) translate(0 {ty:.3f})">{body}</g>'


def emblem_aspect(size="full"):
    x0, y0, x1, y1 = emblem_box(size)
    return (x1 - x0) / (y1 - y0)


def tile(size, finish="faceted", wing=0.74, emb="full", radius=0.225, full_bleed=False, circle=False):
    if circle:
        bg = f'<circle cx="{size / 2}" cy="{size / 2}" r="{size / 2}" fill="{CARBON}"/>'
    else:
        bg = f'<rect width="{size}" height="{size}" rx="{0 if full_bleed else size * radius:.2f}" fill="{CARBON}"/>'
    return bg + emblem(size / 2, size * 0.5, size * wing, size=emb, finish=finish)


# ---------------------------------------------------------------- the logotype
WEIGHTS = {"text": dict(w=11, track=30), "display": dict(w=7, track=38)}


def _glyphs(w, rc=12):
    """Centre-line strokes per capital (cap height 100): (advance, [path d])."""
    h = w / 2

    def bowl(W, Hb, r=rc):
        return (f"M{h},100 V{h} H{W - h - r} A{r},{r} 0 0 1 {W - h},{h + r} V{Hb - h - r} "
                f"A{r},{r} 0 0 1 {W - h - r},{Hb - h} H{h}")
    r = 13
    s_path = (f"M88,{h} H{h + r} A{r},{r} 0 0 0 {h},{h + r} V{50 - r} A{r},{r} 0 0 0 {h + r},50 "
              f"H{88 - h - r} A{r},{r} 0 0 1 {88 - h},{50 + r} V{100 - h - r} A{r},{r} 0 0 1 {88 - h - r},{100 - h} H0")
    # R's leg ends flat on the baseline: a parallelogram, not a stroke
    lx = 48 - w * 0.55
    leg = f"M{lx:.2f},{54 - w:.2f} L{lx + w * 1.25:.2f},{54 - w:.2f} L{88:.2f},100 L{88 - w * 1.25:.2f},100 Z"
    return {
        "F": (78, [("s", f"M{h},100 V{h} H78"), ("s", f"M{h},{47 + h} H64")]),
        "L": (72, [("s", f"M{h},0 V{100 - h} H72")]),
        "I": (w, [("s", f"M{h},0 V100")]),
        "P": (82, [("s", bowl(82, 54))]),
        "R": (88, [("s", bowl(82, 54)), ("f", leg)]),
        "S": (88, [("s", s_path)]),
        "T": (88, [("s", f"M0,{h} H88"), ("s", f"M44,{h} V100")]),
    }


def wordmark(weight="text", colors=("currentColor", "currentColor"), only=None):
    """(width, svg group) of FLIPFIRST, cap height 100, FLIP and FIRST coloured separately."""
    wt = WEIGHTS[weight]
    G = _glyphs(wt["w"])
    x, els = 0.0, []
    text = "FLIPFIRST"
    for i, ch in enumerate(text):
        part = "flip" if i < 4 else "first"
        adv, items = G[ch]
        if not only or only == part:
            c = colors[0] if part == "flip" else colors[1]
            for kind, d in items:
                if kind == "s":
                    els.append(f'<path d="{d}" transform="translate({x:.2f} 0)" fill="none" stroke="{c}" '
                               f'stroke-width="{wt["w"]}" stroke-linejoin="miter" stroke-miterlimit="10"/>')
                else:
                    els.append(f'<path d="{d}" transform="translate({x:.2f} 0)" fill="{c}"/>')
        x += adv + (wt["track"] if i < len(text) - 1 else 0)
    return x, "".join(els)


# ---------------------------------------------------------------- compositions
def svg(w, h, body, defs="", label=None):
    role = f' role="img" aria-label="{label}"' if label else ' aria-hidden="true"'
    d = f"<defs>{defs}</defs>" if defs else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:g} {h:g}" width="{w:g}" height="{h:g}"{role}>'
            f"{d}{body}</svg>\n")


def lockup(theme, stacked=False, weight=None):
    flip, first = (IVORY, GOLD) if theme == "dark" else (INK, GOLD_DEEP)
    foil = FOIL_DARK if theme == "dark" else FOIL_LIGHT
    weight = weight or ("display" if stacked else "text")
    ww, word = wordmark(weight, (flip, first))
    if stacked:
        ew = 300.0
        eh = ew / emblem_aspect()
        cap = 34.0
        W = max(ew, ww * cap / 100) + 80
        H = 40 + eh + 46 + cap + 40
        body = (emblem(W / 2, 40 + eh / 2, ew, finish="faceted" if theme == "dark" else "foil")
                + f'<g transform="translate({(W - ww * cap / 100) / 2:.2f} {40 + eh + 46:.2f}) scale({cap / 100})">{word}</g>')
    else:
        eh = 100.0
        ew = eh * emblem_aspect()
        cap = 40.0
        gap = 30.0
        W, H = ew + gap + ww * cap / 100, eh
        body = (emblem(ew / 2, eh / 2, ew, finish="faceted" if theme == "dark" else "foil")
                + f'<g transform="translate({ew + gap:.2f} {(eh - cap) / 2:.2f}) scale({cap / 100})">{word}</g>')
    return svg(round(W, 2), round(H, 2), body, defs=emblem_defs(foil=foil), label="FlipFirst")


def og_image():
    """1200x630 link preview."""
    W, H = 1200, 630
    ww, word = wordmark("display", (IVORY, GOLD))
    ew = 330.0
    eh = ew / emblem_aspect()
    cap = 40.0
    top = 132.0
    glow = ('<radialGradient id="ff-glow" cx="50%" cy="36%" r="60%"><stop offset="0" stop-color="#D9B45A" stop-opacity=".18"/>'
            '<stop offset="1" stop-color="#D9B45A" stop-opacity="0"/></radialGradient>')
    body = (f'<rect width="{W}" height="{H}" fill="{CARBON}"/><rect width="{W}" height="{H}" fill="url(#ff-glow)"/>'
            + emblem(W / 2, top + eh / 2, ew)
            + f'<g transform="translate({(W - ww * cap / 100) / 2:.2f} {top + eh + 56:.2f}) scale({cap / 100})">{word}</g>'
            + f'<text x="{W / 2}" y="{top + eh + 56 + cap + 74:.0f}" text-anchor="middle" fill="#B9B3A6" '
              f'font-family="Inter, system-ui, sans-serif" font-size="26" font-weight="400" letter-spacing="3">'
              f'FIND IT FIRST · FLIP IT BETTER</text>')
    return svg(W, H, body, defs=emblem_defs() + glow, label="FlipFirst — Find it first. Flip it better.")


def assets():
    """Every generated file: [(path relative to the repo, svg text)]."""
    ea, ba = emblem_aspect(), emblem_aspect("bold")
    out = [
        # --- served by the app
        ("static/brand/flipfirst-mark.svg", svg(64, 64, tile(64, finish="foil", emb="bold", wing=.78), defs=emblem_defs(), label="FlipFirst")),
        ("static/brand/flipfirst-glyph.svg", svg(round(100 * ea, 2), 100, emblem(50 * ea, 50, 100 * ea, finish="foil"), defs=emblem_defs(), label="FlipFirst")),
        ("static/brand/favicon.svg", svg(32, 32, tile(32, finish="flat", emb="bold", wing=.84), label="FlipFirst")),
        ("static/brand/emblem-mask.svg", svg(round(100 * ba, 2), 100, emblem(50 * ba, 50, 100 * ba, size="bold", finish="flat", color="#000"))),
    ]
    for part in ("flip", "first"):
        ww, g = wordmark("text", ("#000", "#000"), only=part)
        out.append((f"static/brand/wordmark-{part}.svg", svg(round(ww, 2), 100, g)))
    # --- sources for the PNG sizes and the brand kit (not served)
    out += [
        ("brand/exports/app-icon.svg", svg(512, 512, tile(512, full_bleed=True), defs=emblem_defs())),
        ("brand/exports/app-icon-rounded.svg", svg(512, 512, tile(512), defs=emblem_defs())),
        ("brand/exports/app-icon-maskable.svg", svg(512, 512, tile(512, full_bleed=True, wing=.6), defs=emblem_defs())),
        ("brand/exports/og-image.svg", og_image()),
        ("brand/exports/instagram-profile.svg", svg(1080, 1080, tile(1080, full_bleed=True, wing=.74), defs=emblem_defs())),
    ]
    for theme in ("dark", "light"):
        cols = (IVORY, GOLD) if theme == "dark" else (INK, GOLD_DEEP)
        ww, g = wordmark("text", cols)
        out += [(f"brand/exports/flipfirst-logo-{theme}.svg", lockup(theme)),
                (f"brand/exports/flipfirst-logo-stacked-{theme}.svg", lockup(theme, stacked=True)),
                (f"brand/exports/flipfirst-wordmark-{theme}.svg", svg(round(ww, 2), 100, g, label="FlipFirst"))]
    out += [
        ("brand/exports/flipfirst-emblem-gold.svg", svg(round(100 * ea, 2), 100, emblem(50 * ea, 50, 100 * ea), defs=emblem_defs(), label="FlipFirst")),
        ("brand/exports/flipfirst-emblem-flat.svg", svg(round(100 * ea, 2), 100, emblem(50 * ea, 50, 100 * ea, finish="flat"), label="FlipFirst")),
        ("brand/exports/flipfirst-badge.svg", svg(512, 512, tile(512), defs=emblem_defs(), label="FlipFirst")),
    ]
    return out


def build():
    for rel, text in assets():
        path = os.path.join(ROOT, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return [rel for rel, _ in assets()]


def pack_ico(pngs, dest):
    """favicon.ico holding PNG images (Vista+ format), no imaging library needed."""
    blobs = []
    for p in pngs:
        with open(p, "rb") as fh:
            data = fh.read()
        w, h = struct.unpack(">II", data[16:24])
        blobs.append((w, h, data))
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, payload = b"", b""
    for w, h, data in blobs:
        entries += struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, 1, 32, len(data), offset + len(payload))
        payload += data
    with open(dest, "wb") as fh:
        fh.write(header + entries + payload)
    return os.path.relpath(dest, ROOT)


if __name__ == "__main__":
    if "--ico" in sys.argv:
        src = [os.path.join(STATIC, f"favicon-{n}.png") for n in (16, 32, 48)]
        if any(not os.path.exists(p) for p in src):
            sys.exit("render the PNGs first: node brand/render.js")
        print(pack_ico(src, os.path.join(STATIC, "favicon.ico")))
        for p in src:
            if not p.endswith("favicon-32.png"):
                os.remove(p)        # 16 and 48 live only inside the .ico
    else:
        print("\n".join(build()))
        print(f"emblem aspect {emblem_aspect():.4f} (full), {emblem_aspect('bold'):.4f} (bold); "
              f"wordmark {wordmark('text')[0] / 100:.4f} (text), {wordmark('display')[0] / 100:.4f} (display)")
