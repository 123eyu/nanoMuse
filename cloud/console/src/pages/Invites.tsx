/** Who invited whom: the funnel, the inviters ranked by what their code brought, each
 *  expandable to its invitees; the reclaim lives in the account drawer. */

import { useState } from "react";
import { fmt, money, when } from "../lib/format";
import { useData } from "../lib/hooks";
import { useT } from "../lib/i18n";
import { useRoute } from "../lib/router";
import type { Invites as InvitesT, Leader } from "../lib/types";
import { Btn, DataTable, Kpi, Kpis, Panel, Pill, Search, cx } from "../ui/primitives";
import { Ago, IpChip, Who } from "../ui/who";
import { Loaded } from "./common";

export function Invites() {
  const T = useT();
  const route = useRoute();
  const q = useData<InvitesT>("/v1/admin/invites?limit=500&per=20", { poll: 300_000 });
  const [open, setOpen] = useState<string | null>(null);
  const [text, setText] = useState("");
  return (
    <Loaded q={q}>
      {(d) => {
        const f = d.funnel;
        const t = text.trim().toLowerCase();
        const rows = t ? d.leaders.filter((l) => [l.identifier, l.hint, l.id, l.first_ip, l.last_ip].filter(Boolean).join(" ").toLowerCase().includes(t)) : d.leaders;
        return (
          <>
            <Kpis>
              <Kpi label={T.in.withCode} value={fmt(f.with_code)} />
              <Kpi label={T.in.inviters} value={fmt(f.inviters)} />
              <Kpi label={T.in.invited} value={fmt(f.invited)} />
              <Kpi label={T.in.bonuses} value={fmt(f.contribute_bonuses)} />
            </Kpis>
            <Panel title={T.in.title} right={<Search value={text} onChange={setText} placeholder={T.pe.search} className="w-64" />} pad={false}>
              <div className="scroll-x">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>{T.in.cols.who}</th>
                      <th className="num">{T.in.cols.invited}</th>
                      <th className="num">{T.in.cols.active}</th>
                      <th className="num">{T.in.cols.settled}</th>
                      <th className="num">{T.in.cols.pending}</th>
                      <th className="num">{T.in.cols.sameIp}</th>
                      <th className="num">{T.in.cols.spent}</th>
                      <th className="num">{T.in.cols.earned}</th>
                      <th className="num hide-sm">{T.in.cols.first}</th>
                      <th className="num">{T.in.cols.last}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((l) => (
                      <LeaderRow key={l.id} l={l} open={open === l.id} onToggle={() => setOpen(open === l.id ? null : l.id)} onOpen={() => route.set({ acct: l.id, tab: "invited" })} />
                    ))}
                    {!rows.length && (
                      <tr>
                        <td colSpan={11} className="hint py-8 text-center">
                          {T.in.none}
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>
          </>
        );
      }}
    </Loaded>
  );
}

function LeaderRow({ l, open, onToggle, onOpen }: { l: Leader; open: boolean; onToggle: () => void; onOpen: () => void }) {
  const T = useT();
  const sameIpHeavy = l.invited >= 3 && l.invited_same_ip / Math.max(1, l.invited) >= 0.5;
  return (
    <>
      <tr className={cx("tap", open && "on")} onClick={onToggle}>
        <td>
          <Who id={l.id} hint={l.hint} identifier={l.identifier} channel={l.channel} disabled={l.disabled} tags={sameIpHeavy ? <Pill tone="warn" title={T.in.sameIpWarn}>{T.in.cols.sameIp}</Pill> : null} />
        </td>
        <td className="num font-medium">{fmt(l.invited)}</td>
        <td className="num">{fmt(l.invited_active)}</td>
        <td className="num">{fmt(l.invited_settled)}</td>
        <td className="num">{fmt(l.invited_pending)}</td>
        <td className={cx("num", sameIpHeavy && "text-warn")}>{fmt(l.invited_same_ip)}</td>
        <td className="num">{money(l.invited_spent_cny)}</td>
        <td className="num">{money(l.earned_cny)}</td>
        <td className="num hide-sm">{when(l.first_invite_at)}</td>
        <td className="num">
          <Ago ts={l.last_invite_at} />
        </td>
        <td>
          <Btn size="sm" onClick={(e) => { e.stopPropagation(); onOpen(); }}>
            {T.ac.abuse}
          </Btn>
        </td>
      </tr>
      {open && (
        <tr>
          <td colSpan={11} className="bg-panel-2 p-0">
            <div className="px-3 py-2">
              <div className="hint mb-1">{T.in.invitees} ({l.invitees.length} / {l.invited})</div>
              <DataTable
                rows={l.invitees}
                rowKey={(i) => i.id}
                defaultSort="created_at"
                columns={[
                  { key: "who", label: T.ac.invCols.who, sortable: false, render: (i) => <Who id={i.id} hint={i.hint} channel={i.channel} disabled={i.disabled} /> },
                  { key: "created_at", label: T.ac.invCols.created, num: true, render: (i) => when(i.created_at) },
                  { key: "last_seen_at", label: T.ac.invCols.last, num: true, render: (i) => <Ago ts={i.last_seen_at} /> },
                  { key: "requests", label: T.ac.invCols.requests, num: true, render: (i) => fmt(i.requests) },
                  { key: "spent_cny", label: T.ac.invCols.spent, num: true, render: (i) => money(i.spent_cny) },
                  { key: "settled", label: T.ac.invCols.settled, render: (i) => (i.settled ? <Pill tone="ok">{T.ac.settled}</Pill> : <Pill>{T.ac.pending}</Pill>) },
                  { key: "ip", label: T.ac.invCols.ip, sortable: false, render: (i) => <span className="inline-flex items-center gap-1"><IpChip ip={i.first_ip} />{i.first_ip && i.first_ip === l.first_ip && <Pill tone="warn">= {T.in.cols.who}</Pill>}</span> },
                ]}
              />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
