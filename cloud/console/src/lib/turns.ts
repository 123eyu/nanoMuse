/** What the record's messages hold, for the question-by-question view: the text of a
 *  message, the files in it (the `blob:sha256:` references the relay left in place of
 *  pictures, clips and voice notes, and plain URLs), and a tool call's arguments laid out. */

export const BLOB_PREFIX = "blob:sha256:";

export const isBlobRef = (v: unknown): v is string => typeof v === "string" && v.startsWith(BLOB_PREFIX) && v.length === BLOB_PREFIX.length + 64;
export const shaOf = (ref: string) => ref.slice(BLOB_PREFIX.length);

type Part = { type?: string; text?: string; image_url?: unknown; input_audio?: unknown; video_url?: unknown; video?: unknown };

/** The text of a message's content and every file in it, in order. */
export function partsOf(content: unknown): { text: string; files: string[] } {
  if (typeof content === "string") return { text: content, files: [] };
  if (!Array.isArray(content)) return { text: content ? JSON.stringify(content) : "", files: [] };
  const texts: string[] = [];
  const files: string[] = [];
  const url = (v: unknown): void => {
    if (typeof v === "string") {
      if (isBlobRef(v) || /^https?:\/\//.test(v)) files.push(v);
    } else if (Array.isArray(v)) v.forEach(url);
    else if (v && typeof v === "object") {
      const o = v as Record<string, unknown>;
      url(o.url ?? o.data);
    }
  };
  for (const p of content as Part[]) {
    if (!p || typeof p !== "object") continue;
    if (p.type === "text" && p.text) texts.push(p.text);
    else url(p.image_url ?? p.input_audio ?? p.video_url ?? p.video);
  }
  return { text: texts.join("\n"), files };
}

/** A tool call's arguments as the model wrote them, pretty when they parse as JSON. */
export const toolArgs = (raw: unknown): string => {
  if (typeof raw !== "string") return raw === undefined ? "" : JSON.stringify(raw, null, 1);
  try {
    return JSON.stringify(JSON.parse(raw), null, 1);
  } catch {
    return raw;
  }
};
