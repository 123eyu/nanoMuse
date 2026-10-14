# nanoMuse brand

Decided 2026-09-24. The app icon and the in-app colour system are one decision: the palette
is derived from the icon so the whole app matches the tile on the home screen.

## The mark

One continuous pen stroke that reads as an **N**: a hooked entry, one rounded peak, a long
diagonal, a tight valley, and an upward exit: an N with one arch, drawn without lifting the
pen. The mark is abstract on purpose: it is a gesture first and a letter second.

- Source of truth: `assets/brand/nanomuse-icon-source-1024.png` (final render, white squircle on
  brand blue). Everything else is derived from it.
- `assets/brand/nanomuse-mark.svg`: the stroke alone, traced as a single filled path, viewBox
  `0 0 100 100` in tile coordinates, gradient fill.
- `assets/brand/nanomuse-icon.svg`: white squircle tile (`rx=27`) + mark. This is the app icon.
- `assets/brand/nanomuse-icon-on-blue.svg`: the icon on the brand-blue backdrop, for
  store listings and the splash only. Wherever the icon stands for the app (README, site,
  social card, favicon) it is the plain tile: white, the mark, a hairline edge, no backdrop.
- `assets/brand/nanomuse-mark-rgba-688.png`: the stroke with alpha, 688 px, raster fallback.
- `docs/app-icon.png`: the tile rendered with rounded corners (27 % radius, transparent
  outside, a hairline `#D9D9DE` edge so it keeps its shape on white), 512 px: the icon as it
  sits on a home screen. This is the README hero and the picture to use wherever
  the app is shown as an app.

The icon is **not** the mascot. The default face is the bundled **dragon** (`web/public/avatars/`,
the Android app's `avatar/`); the other choices are an emoji on a colour and a face made in
the avatar studio. The red panda of the early releases retired in 0.1.23. Never put the dragon
(or the old red panda) on the launcher icon, and never put the N mark in the chat as a face.

## Colour

Measured from the icon.

| Token | Value | Use |
|---|---|---|
| `brand.stroke.start` | `#015CFB` | left end of the mark's gradient |
| `brand.stroke.end` | `#0186FB` | right end of the mark's gradient |
| `brand.blue` | `#0078FD` | backdrop behind the tile; splash; hero backgrounds |
| `tile` | `#FFFFFF` | icon tile |

### Light theme

| Token | Value | Contrast | Use |
|---|---|---|---|
| `primary` | `#015CFB` | 5.35 on white | links, primary buttons (white text), status line |
| `primary.container` | `#E5F0FF` | — | selected rows, chips |
| `ink` | `#000000` | 21 on white | your message pill (white text), the send button and the scroll-to-latest disc (white arrow), the selected tab's disc |
| `pill` | `#F2F2F7` |  | the agent's message pill |
| `primary.tint` | `#EEF5FF` | — | subtle highlighted surfaces |
| `bg` | `#FFFFFF` |  | the chat, the pill bar, the menus and the drawer |
| `bg.grouped` | `#F2F2F7` | — | the settings pages (unchanged from OpenMinis) |
| `surface` | `#FFFFFF` | — | cards, input |

### Dark theme

| Token | Value | Contrast | Use |
|---|---|---|---|
| `primary` | `#58A6FF` | 8.3 on `#000`, 6.7 on `#1C1C1E` | links, buttons (black text), status line |
| `primary.container` | `#1A2B4A` | — | selected rows, chips |
| `ink` | `#FFFFFF` | 21 on `#000` | your message pill (black text), the send button and the scroll-to-latest disc (black arrow), the selected tab's disc |
| `pill` | `#1C1C1E` |  | the agent's message pill |
| `bg` / `surface` / `surface.2` | `#000000` / `#1C1C1E` / `#2C2C2E` | — | unchanged from OpenMinis |

Rules: one accent hue only. Code blocks, thinking text and links that OpenMinis coloured with
iOS blue, orange and green become `primary` plus neutrals. The conversation itself is
monochrome: your pill, the send button and the selected tab are the ink colour on the
canvas, and the blue is left to links and the status line (`ChatColors.kt`, written by
`scripts/rebrand.py`). The dragon's own colours stay inside the avatar; they are not UI
colours.

## Android mapping (done in S1)

- Adaptive icon: background layer = solid `#FFFFFF`; foreground layer = the mark as a
  `VectorDrawable` whose `fillColor` is the two-stop linear gradient above; monochrome layer =
  the same path with a flat fill (Android tints it).
- Icon skins (activity aliases kept from OpenMinis): *Auto* = white tile; *Light* = white tile;
  *Dark* = `#0F1B33` tile with a white mark.
- Notification small icon: the mark, flat white, 24 dp.
- Never ship the old OpenMinis icon, name or the letter-n drafts.

## Don'ts

- Don't recolour the mark outside the blue gradient / flat white / flat black.
- Don't rotate, outline, or add a shadow to the stroke.
- Don't place the mark on the brand blue without the white tile (contrast).
- Don't use another product's mark, name or blue as an asset; the only mark is ours.
