/** One kept turn, folded: the last user message and the reply, the rest on demand. The
 *  content is what the person agreed to share by turning "help improve" on. */

import { useState } from "react";
import { clientLine } from "../lib/client";
import { compact, when } from "../lib/format";
import { useT } from "../lib/i18n";
import type { Sample } from "../lib/types";
import { Pill, cx } from "../ui/primitives";
import { PlatformPill, Who } from "../ui/who";

type Part = { type?: string; text?: string; image_url?: unknown };
const textOf = (content: unknown): { text: string; pictures: number } => {
  if (typeof content === "string") return { text: content, pictures: 0 };
  if (Array.isArray(content)) {
    let pictures = 0;
    const texts: string[] = [];
    for (const p of content as Part[]) {
      if (p && p.type === "text" && p.text) texts.push(p.text);
      else if (p && (p.type === "image_url" || p.image_url)) pictures++;
    }
    return { text: texts.join("\n"), pictures };
  }
  return { text: content ? JSON.stringify(content) : "", pictures: 0 };
};

export function SampleCard({ s, withWho }: { s: Sample; withWho?: boolean }) {
  const T = useT();
  const [open, setOpen] = useState(false);
  const msgs = Array.isArray(s.request) ? s.request : [];
  const user = [...msgs].reverse().find((m) => m.role === "user");
  const u = textOf(user?.content);
  const pictures = msgs.reduce((a, m) => a + textOf(m.content).pictures, 0);
  const toolCalls = msgs.filter((m) => (m as { tool_calls?: unknown[] }).tool_calls?.length).length;
  const roleName = (r: string) => (r === "user" ? T.ac.user : r === "assistant" ? T.ac.assistant : r === "system" ? T.ac.system : r);
  return (
    <div className="rounded-lg border border-line bg-panel-2 p-3">
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
        <span>{when(s.ts)}</span>
        {withWho && <Who id={s.account_id} hint={s.hint} />}
        <PlatformPill platform={s.platform} />
        <code>{s.model}</code>
        <span>
          {compact(s.prompt_tokens)} in · {compact(s.completion_tokens)} out · {msgs.length} {T.g.messages}
        </span>
        {pictures > 0 && <Pill tone="cyan">{T.g.images} {pictures}</Pill>}
        {toolCalls > 0 && <Pill>{T.ac.toolCalls(toolCalls)}</Pill>}
        {s.meta?.ua && <span className="hide-sm">{clientLine(s.meta.ua)}</span>}
        <button type="button" className="ml-auto text-accent" onClick={() => setOpen(!open)}>
          {open ? T.g.hide : T.g.show}
        </button>
      </div>
      {!open ? (
        <div className="mt-2 grid gap-1.5 text-[13px]">
          <div className="line-clamp-3 whitespace-pre-wrap break-words">
            <span className="hint mr-1.5">{T.ac.user}</span>
            {u.text || <span className="hint">({T.g.empty})</span>}
          </div>
          <div className="line-clamp-3 whitespace-pre-wrap break-words text-ink-2">
            <span className="hint mr-1.5">{T.ac.assistant}</span>
            {s.response || <span className="hint">({T.g.empty})</span>}
          </div>
        </div>
      ) : (
        <div className="mt-2 flex max-h-[520px] flex-col gap-2 overflow-auto text-[13px]">
          {msgs.map((m, i) => {
            const { text, pictures: pics } = textOf(m.content);
            const tc = (m as { tool_calls?: { function?: { name?: string } }[] }).tool_calls || [];
            return (
              <div key={i} className={cx("rounded-md px-2.5 py-1.5", m.role === "user" ? "bg-accent-soft" : m.role === "system" ? "bg-fill" : "bg-panel")}>
                <div className="hint mb-0.5">
                  {roleName(m.role)}
                  {pics > 0 && <span className="ml-2">{T.g.images} {pics}</span>}
                  {tc.length > 0 && <span className="ml-2">{tc.map((t) => t.function?.name || "?").join(", ")}</span>}
                </div>
                <div className="whitespace-pre-wrap break-words">{text}</div>
              </div>
            );
          })}
          <div className="rounded-md bg-ok-soft px-2.5 py-1.5">
            <div className="hint mb-0.5">{T.ac.assistant}</div>
            <div className="whitespace-pre-wrap break-words">{s.response}</div>
          </div>
        </div>
      )}
    </div>
  );
}
