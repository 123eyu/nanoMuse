/** Retention and conversion: how far the free allowance goes, how fast it runs out, what
 *  people do after, who runs a key of their own, and weekly retention by sign-up week. */

import type { PageProps } from "../App";
import { dateOf, fmt, money, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { nameOf, useT } from "../lib/i18n";
import type { Cohorts as CohortsT } from "../lib/types";
import { Donut, Ranks, heatStyle } from "../ui/charts";
import { DataTable, Grid, Kpi, Kpis, Note, Panel } from "../ui/primitives";
import { Who } from "../ui/who";
import { Loaded } from "./common";

export function Cohorts({ days }: PageProps) {
  const T = useT();
  const q = useData<CohortsT>(`/v1/admin/analytics/cohorts?days=${days}`, { poll: 600_000 });
  return (
    <Loaded q={q}>
      {(d) => {
        const after = d.after_exhaustion;
        const ok = d.own_key;
        const afterParts = [
          { name: T.co.chattedAgain, value: after.chatted_again, color: "var(--ok)" },
          { name: T.co.activeNoChat, value: after.active_no_chat, color: "var(--c5)" },
          { name: T.co.gone, value: after.gone, color: "var(--ink-3)" },
          { name: T.co.disabled, value: after.disabled, color: "var(--bad)" },
        ];
        const ownParts = [
          { name: T.co.ownLikely, value: ok.own_key_likely, color: "var(--c4)" },
          { name: T.co.both, value: ok.both, color: "var(--c1)" },
          { name: T.co.cloudOnly, value: ok.cloud_only, color: "var(--c2)" },
          { name: T.co.appOpen, value: ok.app_open_no_model, color: "var(--ink-3)" },
        ];
        return (
          <>
            <Kpis>
              <Kpi label={T.co.pool} value={fmt(d.pool.limited)} sub={T.co.poolSub(d.pool.limited, money(d.allowance_cny))} small />
              <Kpi label={T.co.neverUsed} value={fmt(d.pool.never_used)} sub={pct(d.pool.limited ? d.pool.never_used / d.pool.limited : 0)} />
              <Kpi label={T.co.spent} value={money(d.pool.spent_cny)} sub={`${T.co.granted} ${money(d.pool.granted_cny)} · ${pct(d.pool.granted_cny ? d.pool.spent_cny / d.pool.granted_cny : 0)}`} />
              <Kpi label={T.co.exhaust} value={T.co.daysUnit(d.exhaustion.median_days)} sub={T.co.exhaustSub(d.exhaustion.accounts)} />
              <Kpi label={T.co.chattedAgain} value={fmt(after.chatted_again)} sub={`/ ${fmt(after.total)} · ${pct(after.total ? after.chatted_again / after.total : 0)}`} tone="ok" />
              <Kpi label={T.co.ownLikely} value={fmt(ok.own_key_likely)} sub={`${T.co.both} ${fmt(ok.both)}`} tone="violet" />
            </Kpis>
            <Grid cols={3}>
              <Panel title={T.co.pool} sub={T.co.poolSub(d.pool.limited, money(d.allowance_cny))}>
                <Ranks rows={d.pool.buckets.map((b, i) => ({ name: nameOf(T.co.poolBuckets, b.used), value: b.accounts, color: b.used === "100%" ? "var(--bad)" : b.used === "no limit" ? "var(--violet)" : `var(--c${(i % 8) + 1})` }))} showShare />
              </Panel>
              <Panel title={T.co.exhaust} sub={T.co.exhaustSub(d.exhaustion.accounts)}>
                <div className="mb-3 grid grid-cols-3 gap-2">
                  <Kpi flat small label={T.co.p25} value={T.co.daysUnit(d.exhaustion.p25_days)} />
                  <Kpi flat small label={T.co.median} value={T.co.daysUnit(d.exhaustion.median_days)} />
                  <Kpi flat small label={T.co.p75} value={T.co.daysUnit(d.exhaustion.p75_days)} />
                </div>
                <Ranks rows={d.exhaustion.buckets.map((b, i) => ({ name: nameOf(T.co.exhaustBuckets, b.days), value: b.accounts, color: `var(--c${(i % 8) + 1})` }))} showShare />
              </Panel>
              <Panel title={T.co.after} sub={T.co.afterSub(after.total)}>
                <div className="flex items-center gap-4">
                  <Donut parts={afterParts} center={fmt(after.total)} />
                  <div className="min-w-0 flex-1">
                    <Ranks rows={afterParts.map((p) => ({ ...p }))} showShare />
                  </div>
                </div>
                <div className="sub mt-3">
                  {T.co.syncedAfter}: <b>{fmt(after.synced_assistant_after)}</b>
                </div>
              </Panel>
            </Grid>
            <Grid cols={2}>
              <Panel title={T.co.ownKey} sub={T.co.ownKeySub(ok.window_days)}>
                <div className="flex items-center gap-4">
                  <Donut parts={ownParts} center={fmt(ok.own_key_likely + ok.both + ok.cloud_only)} />
                  <div className="min-w-0 flex-1">
                    <Ranks rows={ownParts} showShare />
                  </div>
                </div>
                <Note className="mt-3">{T.co.howOwnKey}</Note>
              </Panel>
              <Panel title={T.co.ownList} pad={false}>
                <DataTable
                  rows={ok.accounts}
                  rowKey={(a) => a.account_id}
                  defaultSort="synced_replies"
                  limit={15}
                  columns={[
                    { key: "who", label: T.g.account, sortable: false, render: (a) => <Who id={a.account_id} hint={a.hint} identifier={a.identifier} /> },
                    { key: "synced_replies", label: T.co.syncedReplies, num: true, render: (a) => fmt(a.synced_replies) },
                    { key: "devices", label: T.g.devices, render: (a) => <span className="hint">{a.devices || "·"}</span> },
                  ]}
                />
              </Panel>
            </Grid>
            <Panel title={T.co.retention} sub={T.co.retentionSub} pad={false}>
              <div className="scroll-x">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>{T.co.week}</th>
                      <th className="num">{T.co.signups}</th>
                      {[0, 1, 2, 3].map((i) => (
                        <th key={`c${i}`} className="num">
                          {T.co.chat} {T.co.w(i)}
                        </th>
                      ))}
                      {[0, 1, 2, 3].map((i) => (
                        <th key={`a${i}`} className="num">
                          {T.co.any} {T.co.w(i)}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {d.retention.map((r) => (
                      <tr key={r.week}>
                        <td>{dateOf(r.week)}</td>
                        <td className="num font-medium">{fmt(r.signups)}</td>
                        {r.chat.map((v, i) => (
                          <td key={`c${i}`} className="num">
                            {v === null ? <span className="text-ink-3">·</span> : <span className="heat" style={heatStyle(r.signups ? v / r.signups : 0, 1, "37, 99, 235")}>{pct(r.signups ? v / r.signups : 0)}</span>}
                          </td>
                        ))}
                        {r.any.map((v, i) => (
                          <td key={`a${i}`} className="num">
                            {v === null ? <span className="text-ink-3">·</span> : <span className="heat" style={heatStyle(r.signups ? v / r.signups : 0, 1, "22, 163, 74")}>{pct(r.signups ? v / r.signups : 0)}</span>}
                          </td>
                        ))}
                      </tr>
                    ))}
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
