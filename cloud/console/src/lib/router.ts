/** Hash routing: `#page?k=v`. The page's own filters live in the params so a view can be
 *  bookmarked and the back button works; `acct` and `ip` open the account and address
 *  drawers over any page. `days` (the period every period page reads) is kept in
 *  localStorage, not the hash, so it follows you from page to page. */

import { useCallback, useEffect, useMemo, useState } from "react";

export const PAGES = [
  "overview",
  "live",
  "usage",
  "platforms",
  "cohorts",
  "hands",
  "quality",
  "abuse",
  "people",
  "invites",
  "places",
  "activity",
  "demo",
  "data",
  "health",
  "site",
  "models",
  "stats",
  "controls",
  "settings",
] as const;
export type Page = (typeof PAGES)[number];

export const GROUPS: { id: string; pages: Page[] }[] = [
  { id: "insight", pages: ["overview", "live", "usage", "platforms", "cohorts", "hands", "quality", "abuse"] },
  { id: "ops", pages: ["people", "invites", "places", "activity", "demo", "data"] },
  { id: "system", pages: ["health", "site", "models", "stats", "controls", "settings"] },
];

export interface Route {
  page: Page;
  params: URLSearchParams;
}

export function parseHash(hash: string): Route {
  const h = hash.replace(/^#\/?/, "");
  const [p, q = ""] = h.split("?");
  const page = (PAGES as readonly string[]).includes(p) ? (p as Page) : "overview";
  return { page, params: new URLSearchParams(q) };
}

export function hashOf(page: Page, params?: URLSearchParams | Record<string, string | number | null | undefined>): string {
  let sp: URLSearchParams;
  if (params instanceof URLSearchParams) sp = params;
  else {
    sp = new URLSearchParams();
    for (const [k, v] of Object.entries(params || {})) if (v !== null && v !== undefined && v !== "") sp.set(k, String(v));
  }
  const q = sp.toString();
  return `#${page}${q ? "?" + q : ""}`;
}

export function useRoute() {
  const [hash, setHash] = useState(() => location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const route = useMemo(() => parseHash(hash), [hash]);
  /** Change some params of the current page (null removes); `replace` keeps history clean while typing. */
  const set = useCallback(
    (patch: Record<string, string | number | null | undefined>, replace = false) => {
      const r = parseHash(location.hash);
      const sp = new URLSearchParams(r.params);
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === undefined || v === "") sp.delete(k);
        else sp.set(k, String(v));
      }
      const next = hashOf(r.page, sp);
      if (next === location.hash) return;
      if (replace) history.replaceState(null, "", next);
      else history.pushState(null, "", next);
      setHash(next);
    },
    [],
  );
  const go = useCallback((page: Page, params?: Record<string, string | number | null | undefined>) => {
    const next = hashOf(page, params);
    if (next === location.hash) return;
    location.hash = next;
  }, []);
  return { ...route, set, go };
}

/** The account drawer from anywhere: `acct` on the current page. */
export const openAccountHash = (id: string): string => {
  const r = parseHash(location.hash);
  const sp = new URLSearchParams(r.params);
  sp.set("acct", id);
  sp.delete("ip");
  return hashOf(r.page, sp);
};
export const openAddressHash = (ip: string): string => {
  const r = parseHash(location.hash);
  const sp = new URLSearchParams(r.params);
  sp.set("ip", ip);
  return hashOf(r.page, sp);
};
