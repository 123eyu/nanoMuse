/** 简体中文 first, English second; both tables in locales.ts carry the same keys
 *  (locales.test.ts checks). The language follows the browser's, is kept once chosen,
 *  and `useT()` hands the page its table. */

import { createContext, useContext } from "react";
import { en, zh, type Lang, type Strings } from "./locales";

export const LANG_KEY = "nm.admin.lang";

export const initialLang = (): Lang => {
  const stored = localStorage.getItem(LANG_KEY);
  if (stored === "zh" || stored === "en") return stored;
  return (navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
};

export const tableOf = (lang: Lang): Strings => (lang === "zh" ? zh : en);

export const LangContext = createContext<{ lang: Lang; setLang: (l: Lang) => void }>({ lang: "zh", setLang: () => undefined });

export const useLang = () => useContext(LangContext);
export const useT = (): Strings => tableOf(useContext(LangContext).lang);
export const useZh = (): boolean => useContext(LangContext).lang === "zh";

/** A label from a {key: label} map, falling back to the key. */
export const nameOf = (map: Record<string, string>, key: string | null | undefined): string => (key ? map[key] || key : "");
