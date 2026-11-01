# FlipFirst brand

Black and gold. A precise, premium automotive identity for a product that finds
underpriced cars first and shows the math behind every deal.

Everything visual comes from one source: `brand/build.py` holds the geometry and
the palette and writes every logo file. Change it there, never by hand-editing
an SVG.

```bash
python3 brand/build.py           # SVGs → static/brand/ and brand/exports/
node brand/render.js             # PNG sizes (Playwright, already used by tests/browser)
python3 brand/build.py --ico     # static/brand/favicon.ico (16, 32, 48 px)
```

`tests/test_brand.py` fails if a committed file drifts from the generator, if a
page points at a missing brand file, or if a colour pair drops below WCAG AA.

---

## 1. The emblem: the Wings

An **F and its mirror image, set back to back**. Their bars sweep out and up
into blade-cut wings. Their stems run down the centre like a lane line and end in a
point.

- **Flip:** one F is the other, flipped.
- **First:** wings, and gold. Gold is first place.
- **Automotive:** the winged badge is the classic language of the car emblem.
  This one is built from letters, not feathers.

Two drawings of the same emblem:

| Version | Use | Why |
|---|---|---|
| **Full** (`WINGS["full"]`) | 48 px and up: app icons, social, sign-in, print | long, fine wing tips |
| **Bold** (`WINGS["bold"]`) | 20–48 px: app header, sidebar, 48 px favicon | shorter, thicker wings |
| **Micro** (`MICRO`) | 16 and 32 px favicon only | the same Wings snapped to a 16 px grid, so the tab icon stays sharp |

Finishes:

| Finish | Where |
|---|---|
| **Polished** (faceted: upper blades lit, undersides in shadow) | app icons, Instagram, link preview, stacked logo on dark |
| **Foil** (one gold gradient) | in-app header and sidebar, sign-in, logo on light |
| **Flat** (`#D9B45A`, or one colour) | favicon, single-colour uses: stamping, embroidery, engraving |

## 2. The logotype

**FLIPFIRST** in extended, squared-round capitals drawn for FlipFirst. There is no
font file, so the logotype is never typed. **FLIP** is ivory on dark and ink on light;
**FIRST** is always gold.

| Weight | Stroke / cap | Tracking | Use |
|---|---|---|---|
| Text | 11 % | 30 | lockups at UI sizes, the app header |
| Display | 7 % | 38 | stacked logo, link preview, large print |

## 3. Lockups

| File (`brand/exports/`) | Use |
|---|---|
| `flipfirst-logo-dark.svg` / `-light.svg` | horizontal: emblem + logotype |
| `flipfirst-logo-stacked-dark.svg` / `-light.svg` | emblem over logotype: hero, social, signage |
| `flipfirst-emblem-gold.svg`, `-flat.svg` | emblem alone |
| `flipfirst-wordmark-dark.svg` / `-light.svg` | logotype alone |
| `flipfirst-badge.svg` (+ `-1024.png`) | emblem on a carbon tile |

**Clear space:** keep half the emblem's height clear on every side.
**Minimum size:** emblem 16 px tall (Bold). Horizontal lockup: emblem 18 px tall
(logotype cap height 9 px). Stacked lockup: 120 px wide.

**Don't:**
- recolour the emblem outside the gold palette;
- add glows, drop shadows or outlines;
- stretch, rotate, italicise or re-space the logotype;
- set FLIPFIRST in a font;
- split the two F's apart;
- put the light-background lockup on a dark background (or the reverse);
- use the earlier blue arrow-F mark. It is retired.

## 4. Colour

### Brand

| Name | Hex | Role |
|---|---|---|
| Carbon | `#0E0F11` | tiles, brand panels, dark surfaces |
| Gold | `#D9B45A` | the signature: emblem, FIRST, gold on dark |
| Gold deep | `#8A6719` | FIRST on light backgrounds (5.2:1 on white) |
| Ivory | `#F2EEE4` | FLIP on dark |
| Ink | `#111215` | FLIP and text on light |
| Gold foil | `#F6E3A6 → #E2BF6C → #B98A3A → #E7C97E → #C6984A` | emblem and FIRST on dark (light-theme foil runs `#C9A04C → #7E5C16`) |

### Interface tokens (`static/ff.css`)

| Token | Light | Dark | Notes |
|---|---|---|---|
| `--bg` | `#F6F6F3` porcelain | `#0B0B0C` | |
| `--surface` | `#FFFFFF` | `#141416` | cards |
| `--ink` / `--muted` | `#111215` / `#64635D` | `#F2F0EA` / `#9D9A92` | muted ≥ 5.2:1 on every surface |
| `--accent` (gold text, links, active) | `#86661A` | `#E2C27A` | 5.3:1 / 10.7:1 on cards |
| `--accent-strong` (gold fills) | `#A98327` | `#D4AF5A` | ink text on it ≥ 5.3:1 |
| `--primary` / `--on-primary` | ink `#111215` / gold `#F2D894` | gold `#D9B45A` / ink `#111215` | primary buttons, 13.4:1 / 9.5:1 |
| `--profit` | `#067647` | `#34D399` | money made |
| `--warn` | `#B54708` orange | `#FB923C` | caution, **never gold** |
| `--danger` | `#B42318` | `#FF7A7A` | loss, errors |

Gold means **brand and action**. Caution is orange and loss is red, so a warning is
never mistaken for the brand. Green means profit only.

## 5. Typography

The logotype is the brand voice; the interface uses the platform's own type (SF Pro,
Segoe UI, Roboto) for speed and legibility. No web font is loaded.

| Style | Size / weight | Notes |
|---|---|---|
| Display (sign-in) | 38 / 650 | tracking −0.02em |
| H1 | 30 / 700 | page titles 24 / 700 |
| H2 | 20 / 650 | |
| H3 | 17 / 650 | |
| Body | 15 / 400, line height 1.5 | |
| Small, caption | 13, 12 | |
| Eyebrow | 12 / 650, uppercase, tracking 0.12em | echoes the logotype's spacing |
| Numbers | tabular figures | money and metrics align in columns |

## 6. Components

- **Primary button:** ink with gold lettering on light, gold with ink lettering on dark.
  Height 40, or 48 for the large size. Radius 8 (12 for large). A faint top highlight.
- **Secondary:** surface with a hairline border. **Ghost:** text only. **Danger:** red text.
- **Cards:** surface, 16 px radius, hairline border, soft shadow.
  Metric strips sit on `--surface-2`.
- **Chips and badges:** gold-soft for brand and plan (Pro, Lite), green profit,
  orange caution, red danger, neutral for facts.
- **Navigation:** on mobile, the active tab bar item is gold with a gold indicator.
  On desktop, the active sidebar item sits on gold-soft.
  The plan chip and alert counts are gold with ink numerals.
- **Focus:** a 3 px gold ring, visible in both themes.
- **Toasts:** carbon with a gold icon and gold action, a hairline gold border.

## 7. Icons, imagery, loading and empty states

- Icons are the original 24 px stroke set in `static/icons.svg`. There are no emoji
  and no clip-art cars.
- **No photo:** a listing without a photo shows the Wings as a faint gold watermark
  (32 % opacity), not a drawn car.
- **Loading:** skeleton blocks in `--surface-3` with a soft shimmer. Busy buttons
  spin in their own text colour.
- **Empty states:** a gold-soft icon tile, one sentence on what will appear, one
  action.

## 8. App icon, favicon, install

| File (`static/brand/`) | Size | Notes |
|---|---|---|
| `favicon.svg`, `favicon.ico`, `favicon-32.png` | 16/32/48 | Micro emblem at 16 and 32 px, Bold at 48. Flat gold on a carbon tile |
| `apple-touch-icon.png` | 180 | full bleed, so iOS rounds the corners itself |
| `icon-192.png`, `icon-512.png` | 192/512 | rounded carbon tile |
| `icon-maskable-512.png` | 512 | emblem inside Android's 80 % safe zone |
| `manifest.webmanifest` | | theme `#0E0F11`, background `#0B0B0C` |

## 9. Social

The sign-in page and the app shell both carry the link-preview tags (`og:*`,
`twitter:card`), so a shared link to the app shows the card too.

- **Link preview** (`static/brand/og-image.png`, 1200×630): the stacked logo on
  carbon with a faint gold light, plus FIND IT FIRST · FLIP IT BETTER. It is
  referenced from `login.html` (`og:image`, `twitter:card`).
- **Instagram profile picture** (`brand/exports/instagram-profile-1080.png`): the
  polished emblem on carbon, safe for the circular crop. Upload it to @tryflipfirst
  so Instagram and the app match.

## 10. Responsive branding

| Place | Emblem height | Notes |
|---|---|---|
| Mobile top bar | 22 px | emblem + logotype |
| Desktop sidebar | 24 px | |
| Operations and classic console | 22 px | |
| Sign-in brand panel | 30 px mobile, 40 px desktop | always carbon and gold, in both themes |

## 11. Notifications and email

- **Push alerts (ntfy):** carry the app icon (`/static/brand/icon-192.png` on the
  public HTTPS origin, `BILLING_ORIGIN`). Android shows it beside the alert. There
  are no emoji tags.
- **Email:** plain text, from "FlipFirst", signed
  `FlipFirst | Find it first. Flip it better.` Plain text is deliberate: it
  delivers reliably and carries no tracking pixels.

## 12. Name and trademark

The emblem was drawn to stay clear of known marks. It is not an interlocking or
rotated double F (fashion monograms), and it is not a feathered wing with a central
badge (luxury carmakers). That is a design precaution, **not legal clearance.** The
FlipFirst name is used by another business. A new logo does not settle a name
conflict: have a trademark attorney search the name and the emblem
(USPTO classes 9, 35 and 42) before filing or scaling up.

## 13. Platform notes

- **Exports** carry a small transparent margin, so wing tips and letters never touch
  the file's edge.
- **Safari / WebKit:** the header lockup is painted through CSS masks. Every mask
  also has its `-webkit-` form (Safari 15.4+), and `tests/test_brand.py` enforces this.
  WebKit itself was not available to the test environment.
- **Push icons:** ntfy shows the app icon on Android. iOS shows the title and text
  only, which is an ntfy limitation.
- **Link previews** resolve once `app.tryflipfirst.com` serves the app. Until then the
  image URL has nothing to fetch.

