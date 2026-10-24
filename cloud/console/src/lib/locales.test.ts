/** The two locales carry the same keys with the same shapes, and no string carries an
 *  em dash or an exclamation mark (the voice rule in AGENTS.md). */

import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { en, zh } from "./locales";

type Tree = Record<string, unknown>;

const walk = (t: Tree, prefix = ""): Map<string, unknown> => {
  const out = new Map<string, unknown>();
  for (const [k, v] of Object.entries(t)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object") for (const [k2, v2] of walk(v as Tree, key)) out.set(k2, v2);
    else out.set(key, v);
  }
  return out;
};

describe("locales", () => {
  const z = walk(zh as unknown as Tree);
  const e = walk(en as unknown as Tree);

  it("has the same keys in zh and en", () => {
    const onlyZh = [...z.keys()].filter((k) => !e.has(k));
    const onlyEn = [...e.keys()].filter((k) => !z.has(k));
    expect(onlyZh, "keys only in zh").toEqual([]);
    expect(onlyEn, "keys only in en").toEqual([]);
  });

  it("has the same kind of value for every key", () => {
    const wrong: string[] = [];
    for (const [k, v] of z) {
      const w = e.get(k);
      if (typeof v !== typeof w) wrong.push(`${k}: zh ${typeof v}, en ${typeof w}`);
      else if (typeof v === "function" && (v as (...a: unknown[]) => unknown).length !== (w as (...a: unknown[]) => unknown).length) wrong.push(`${k}: arity differs`);
    }
    expect(wrong).toEqual([]);
  });

  it("leaves no string empty", () => {
    const empty = [...z.entries(), ...e.entries()].filter(([, v]) => typeof v === "string" && !v.trim()).map(([k]) => k);
    expect(empty).toEqual([]);
  });

  it("carries no em dash and no exclamation mark", () => {
    const src = readFileSync(new URL("./locales.ts", import.meta.url), "utf8");
    const literals: string[] = src.match(/"(?:[^"\\]|\\.)*"|`(?:[^`\\]|\\.)*`/g) ?? [];
    const bad = literals.filter((s) => /[—!！]/.test(s));
    expect(bad).toEqual([]);
  });
});
