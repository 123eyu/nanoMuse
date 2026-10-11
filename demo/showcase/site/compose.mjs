#!/usr/bin/env node
// Compose the page around the phone from our template (page/index.html) and MobileGym's own
// demo chrome, taken from the checkout at build time rather than copied into this repository:
//
//   - the Gesture Guide (Back / Home / Recents, as buttons and as a legend of the gestures),
//   - the State Builder dock and its drawer (session snapshots, phone language, device time /
//     battery / location, WeChat, Alipay, SMS, 12306 and Weather data, patched live),
//   - their stylesheet (styles.css), scripts (state-builder.js, boot-hero.js) and app icons.
//
// Nothing of MobileGym's is cut down: the markup is lifted whole from web/index.html, the
// scripts are used as they are (one default changed: the phone's address is /phone.html, not
// /, since the page lives at /), and the stylesheet is loaded before ours. MobileGym is
// Apache-2.0; see the page's footer and THIRD_PARTY_NOTICES.
//
//   node compose.mjs <mobilegym checkout> <page dir> <dist dir>

import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const [checkout, pageDir, dist] = process.argv.slice(2);
if (!checkout || !pageDir || !dist) {
  console.error("usage: compose.mjs <mobilegym checkout> <page dir> <dist dir>");
  process.exit(2);
}
const web = join(checkout, "web");
for (const needed of ["index.html", "styles.css", "scripts/state-builder.js", "scripts/boot-hero.js"]) {
  if (!existsSync(join(web, needed))) {
    console.error(`${join(web, needed)} is missing — not a MobileGym checkout with its web/ page?`);
    process.exit(1);
  }
}

/** The element that starts with `startTag` (a unique prefix), with its matching close tag. */
function fragment(html, startTag, tag) {
  const start = html.indexOf(startTag);
  if (start < 0) throw new Error(`MobileGym's page no longer has ${startTag}`);
  const open = new RegExp(`<${tag}\\b`, "g");
  const close = `</${tag}>`;
  let depth = 0;
  let i = start;
  for (;;) {
    open.lastIndex = i;
    const nextOpen = open.exec(html);
    const nextClose = html.indexOf(close, i);
    if (nextClose < 0) throw new Error(`unclosed <${tag}> at ${start}`);
    if (nextOpen && nextOpen.index < nextClose) {
      depth += 1;
      i = nextOpen.index + 1;
    } else {
      depth -= 1;
      i = nextClose + close.length;
      if (depth === 0) return html.slice(start, i);
    }
  }
}

const theirs = readFileSync(join(web, "index.html"), "utf8");

// nanoMuse first in the dock: its launcher icon (the mark on a white tile) above MobileGym's
// State Builder tabs, with a divider between. It carries no data-studio-tab, so their script
// leaves it alone; page.js brings the app to the front on a tap and lights it while it is there.
const nanomuseTab = `
          <button type="button" class="state-dock-tab nm-dock-app" data-nanomuse-open aria-pressed="false" aria-label="nanoMuse" title="nanoMuse">
            <span class="state-dock-tab-icon state-dock-tab-icon-img"><img src="/page/mark.svg" alt="" /></span>
          </button>
          <span class="state-dock-divider" aria-hidden="true"></span>`;
function withNanoMuseFirst(dock) {
  const open = dock.indexOf(">") + 1; // the end of the <aside …> tag
  return dock.slice(0, open) + nanomuseTab + dock.slice(open);
}

const pieces = {
  "gesture-guide": fragment(theirs, '<aside class="gesture-guide ui"', "aside"),
  "state-dock": withNanoMuseFirst(fragment(theirs, '<aside class="state-dock ui"', "aside")) + '\n<span class="state-dock-hint" aria-hidden="true">Patch state</span>',
  "state-drawer": fragment(theirs, '<aside id="state-drawer"', "aside"),
};
// their icons move under /page/icons/ (the page is served from /, their page from its own dir)
for (const k of Object.keys(pieces)) pieces[k] = pieces[k].replace(/src="figures\/icons\//g, 'src="/page/icons/');

let page = readFileSync(join(pageDir, "index.html"), "utf8");
for (const [name, html] of Object.entries(pieces)) {
  const marker = `<!--@mobilegym:${name}-->`;
  if (!page.includes(marker)) throw new Error(`the page template has no ${marker}`);
  page = page.replace(marker, html);
}

mkdirSync(join(dist, "page"), { recursive: true });
cpSync(pageDir, join(dist, "page"), { recursive: true });
writeFileSync(join(dist, "index.html"), page);
writeFileSync(join(dist, "page", "index.html"), page);

// their stylesheet and scripts, as they are, except that the phone is at /phone.html here, and
// only there: upstream's page takes the frame's address from ?sim= (for a local preview against a
// separate dev server), which on a public origin would let a crafted link put any page, or a
// javascript: URL running as this origin, inside the phone. The showcase has no such preview,
// so the address is fixed.
cpSync(join(web, "styles.css"), join(dist, "page", "mobilegym.css"));
cpSync(join(web, "scripts", "boot-hero.js"), join(dist, "page", "boot-hero.js"));
let builder = readFileSync(join(web, "scripts", "state-builder.js"), "utf8");
const simDefault = "new URLSearchParams(location.search).get('sim') || '/'";
if (!builder.includes(simDefault)) throw new Error("state-builder.js: the simulator's default address moved; update compose.mjs");
builder = builder.replace(simDefault, "'/phone.html'");
writeFileSync(join(dist, "page", "state-builder.js"), builder);
if (existsSync(join(web, "figures", "icons"))) cpSync(join(web, "figures", "icons"), join(dist, "page", "icons"), { recursive: true });
console.log(`page composed: ${join(dist, "index.html")} (MobileGym's chrome from ${web})`);
