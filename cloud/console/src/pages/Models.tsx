/** The models the operator's key reaches, what the probes said about each, and which are
 *  on the menu people see. */

import { useState } from "react";
import { when } from "../lib/format";
import { useData } from "../lib/hooks";
import { nameOf, useT } from "../lib/i18n";
import type { Catalog, Overview } from "../lib/types";
import { Chips, DataTable, Kpi, Kpis, Note, Panel, Pill, Search } from "../ui/primitives";
import { Loaded } from "./common";

export function Models() {
  const T = useT();
  const q = useData<Catalog>("/v1/admin/catalog", { poll: 120_000 });
  const ov = useData<Overview>("/v1/admin/overview?days=7", { maxAge: 300_000 });
  const [text, setText] = useState("");
  const [only, setOnly] = useState<"" | "vision" | "offered" | "verified">("");
  const menu = new Set(ov.data?.settings.models || []);
  return (
    <Loaded q={q}>
      {(c) => {
        if (!c.enabled) return <Note tone="warn">{T.mo.off}</Note>;
        const t = text.trim().toLowerCase();
        const chat = c.models.filter((m) => m.kind === "chat");
        const others = c.models.filter((m) => m.kind !== "chat");
        const rows = chat.filter((m) => (!t || m.id.toLowerCase().includes(t)) && (only === "" || (only === "vision" ? m.vision : only === "offered" ? menu.has(m.id) : m.verified)));
        return (
          <>
            <Kpis>
              <Kpi label={T.mo.count(chat.length)} value={chat.length} sub={c.fetched_at ? T.mo.fetched(when(c.fetched_at)) : undefined} />
              <Kpi label={T.mo.vision} value={chat.filter((m) => m.vision).length} />
              <Kpi label={T.mo.verified} value={chat.filter((m) => m.verified).length} sub={c.probe ? (c.probing ? T.mo.probing : c.pending ? T.mo.pending(c.pending) : undefined) : T.mo.names} />
              <Kpi label={T.mo.offered} value={menu.size} />
              <Kpi label={T.mo.unusable} value={c.unusable.length} tone={c.unusable.length ? "warn" : ""} />
            </Kpis>
            {c.error && <Note tone="bad">{c.error}</Note>}
            <Panel
              title={T.mo.title}
              right={
                <>
                  <Chips options={[{ id: "", label: T.g.all }, { id: "offered", label: T.mo.offered }, { id: "vision", label: T.mo.visionCol }, { id: "verified", label: T.mo.verified }]} value={only} onChange={setOnly} />
                  <Search value={text} onChange={setText} placeholder={T.mo.search} className="w-56" />
                </>
              }
              pad={false}
            >
              <DataTable
                rows={rows}
                rowKey={(m) => m.id}
                defaultSort="id"
                defaultDesc={false}
                limit={200}
                columns={[
                  { key: "id", label: T.g.model, render: (m) => <span className="inline-flex items-center gap-2"><code>{m.id}</code>{menu.has(m.id) && <Pill tone="accent">{T.mo.offered}</Pill>}</span> },
                  { key: "vision", label: T.mo.visionCol, value: (m) => (m.vision ? 1 : 0), render: (m) => (m.vision ? <Pill tone="cyan">{T.g.yesShort}</Pill> : <span className="hint">{T.g.noShort}</span>) },
                  { key: "verified", label: T.mo.verified, value: (m) => (m.verified ? 1 : 0), render: (m) => (m.verified ? <Pill tone="ok">{T.g.yesShort}</Pill> : <span className="hint">{T.g.noShort}</span>) },
                ]}
              />
            </Panel>
            {(c.unusable.length > 0 || others.length > 0) && (
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {c.unusable.length > 0 && (
                  <Panel title={T.mo.unusable} pad={false}>
                    <DataTable rows={c.unusable} rowKey={(u) => u.id} defaultSort="checked_at" columns={[{ key: "id", label: T.g.model, render: (u) => <code>{u.id}</code> }, { key: "note", label: T.g.note, wrap: true }, { key: "checked_at", label: T.g.time, num: true, render: (u) => when(u.checked_at) }]} />
                  </Panel>
                )}
                {others.length > 0 && (
                  <Panel title={T.mo.others} pad={false}>
                    <DataTable rows={others} rowKey={(m) => m.id} defaultSort="kind" defaultDesc={false} limit={100} columns={[{ key: "id", label: T.g.model, render: (m) => <code>{m.id}</code> }, { key: "kind", label: T.g.kind, render: (m) => nameOf(T.kinds, m.kind) }]} />
                  </Panel>
                )}
              </div>
            )}
          </>
        );
      }}
    </Loaded>
  );
}
