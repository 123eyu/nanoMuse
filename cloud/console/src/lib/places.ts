/** Where an address is, as the relay's answers say (`places: {ip: {...}}`), kept for the
 *  session so every page names an address the same way. */

export interface Place {
  country?: string;
  code?: string;
  province?: string;
  city?: string;
  isp?: string;
  local?: boolean;
  text?: string;
}

const PLACES = new Map<string, Place>();
let version = 0;
const listeners = new Set<() => void>();

export function mergePlaces(j: unknown): void {
  if (!j || typeof j !== "object") return;
  const places = (j as { places?: unknown }).places;
  if (!places || typeof places !== "object") return;
  let changed = false;
  for (const [ip, p] of Object.entries(places as Record<string, Place>)) {
    if (!PLACES.has(ip)) changed = true;
    PLACES.set(ip, p);
  }
  if (changed) {
    version++;
    for (const fn of listeners) fn();
  }
}

export const placeOf = (ip: string | undefined | null): Place | null => (ip ? PLACES.get(ip) || null : null);
export const placesVersion = () => version;
export const onPlaces = (fn: () => void) => {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
};

/** The language-neutral region key ("本地网络" is what the relay's own tallies use). */
export const regionKey = (p: Place | null): string => (!p ? "?" : p.local ? "本地网络" : p.country || "?");
