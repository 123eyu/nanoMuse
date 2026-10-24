/** "Help improve" in aggregate and the latest kept turns, the export, and conversation
 *  sync's counts. */

import type { PageProps } from "../App";
import { download } from "../lib/api";
import { bytes, compact, dateOf, fmt, nowS } from "../lib/format";
import { useData } from "../lib/hooks";
import { nameOf, useT } from "../lib/i18n";
import type { DataView, Sync } from "../lib/types";
import { Bars, Legend, Ranks, modelColor, platformColor } from "../ui/charts";
import { Btn, DataTable, Grid, Kpi, Kpis, Note, Panel, Pill, useToast } from "../ui/primitives";
import { Ago, Who } from "../ui/who";
import { Loaded, dayLabels, dayTitles } from "./common";
import { SampleCard } from "./SampleCard";

export function Data({ days }: PageProps) {
  const T = useT();
  const toast = useToast();
  const q = useData<DataView>(`/v1/admin/data?days=${days}`, { poll: 120_000 });
  const sy = useData<Sync>("/v1/admin/sync", { poll: 300_000 });
  const exp = (since: number) => download(`/v1/admin/samples/export?since=${since}`, `nanomuse-samples-${since}.jsonl`).catch((e) => toast(String(e.message || e), "bad"));
  return (
    <>
      <Loaded q={q}>
        {(d) => (
          <>
            <Kpis>
              <Kpi label={T.da.on} value={fmt(d.accounts.on)} sub={T.da.onSub(d.accounts.on, d.accounts.total)} tone="ok" />
              <Kpi label={T.da.off} value={fmt(d.accounts.turned_off_ever)} sub={T.da.offSub} />
              <Kpi label={T.da.turns} value={fmt(d.period.samples)} sub={T.da.turnsSub(d.totals.samples, d.accounts.with_samples)} />
              <Kpi label={T.da.tokens} value={compact(d.period.prompt_tokens + d.period.completion_tokens)} sub={T.da.tokensSub(compact(d.totals.prompt_tokens), compact(d.totals.completion_tokens))} />
              <Kpi label={T.g.accounts} value={fmt(d.period.accounts)} sub={T.g.period(days)} />
              <Kpi label={T.g.share} value={`${(100 * d.accounts.share).toFixed(0)}%`} sub={T.da.defaultOn(d.default_on)} small />
            </Kpis>
            <Note>
              <b>{T.da.kept}:</b> {d.keeps.kept.join(", ")}. <b>{T.da.notKept}:</b> {d.keeps.not_kept.join(", ")}.
            </Note>
            <Panel
              title={T.da.series}
              right={
                <>
                  <Btn size="sm" onClick={() => exp(nowS() - days * 86400)}>{T.da.exportSince(days)}</Btn>
                  <Btn size="sm" onClick={() => exp(0)}>{T.da.export}</Btn>
                  <span className="hint">{T.da.exportNote}</span>
                </>
              }
            >
              <Bars
                series={[{ name: T.da.legend.samples, values: d.days.map((x) => x.samples), color: "var(--c1)" }]}
                lines={[
                  { name: T.da.legend.turned_on, values: d.days.map((x) => x.turned_on), color: "var(--ok)" },
                  { name: T.da.legend.turned_off, values: d.days.map((x) => x.turned_off), color: "var(--bad)" },
                ]}
                labels={dayLabels(d.days.map((x) => x.day))}
                titles={dayTitles(d.days.map((x) => x.day))}
                height={150}
              />
              <Legend className="mt-2" items={[{ name: T.da.legend.samples, color: "var(--c1)" }, { name: T.da.legend.turned_on, color: "var(--ok)" }, { name: T.da.legend.turned_off, color: "var(--bad)" }]} />
            </Panel>
            <Grid cols={3}>
              <Panel title={T.da.byModel}>
                <Ranks rows={d.by_model.map((m) => ({ name: <code>{m.model}</code>, value: m.samples, sub: `${compact(m.tokens)} ${T.g.tokens}`, color: modelColor(m.model) }))} showShare />
              </Panel>
              <Panel title={T.da.byPlatform}>
                <Ranks rows={d.by_platform.map((p) => ({ name: nameOf(T.platforms, p.platform), value: p.samples, color: platformColor(p.platform) }))} showShare />
              </Panel>
              <Panel title={T.da.byAccount} pad={false}>
                <DataTable
                  rows={d.by_account}
                  rowKey={(a) => a.account_id}
                  defaultSort="samples"
                  limit={12}
                  columns={[
                    { key: "who", label: T.da.accCols.who, sortable: false, render: (a) => <Who id={a.account_id} hint={a.hint} channel={a.channel} tags={!a.contribute ? <Pill tone="warn">{T.g.off}</Pill> : null} /> },
                    { key: "samples", label: T.da.accCols.samples, num: true, render: (a) => fmt(a.samples) },
                    { key: "tokens", label: T.da.accCols.tokens, num: true, value: (a) => a.prompt_tokens + a.completion_tokens, render: (a) => compact(a.prompt_tokens + a.completion_tokens) },
                    { key: "last_ts", label: T.da.accCols.last, num: true, render: (a) => <Ago ts={a.last_ts} /> },
                  ]}
                />
              </Panel>
            </Grid>
            <Panel title={T.da.recent}>
              <div className="flex flex-col gap-2">
                {d.recent.map((s) => (
                  <SampleCard key={s.id} s={s} withWho />
                ))}
                {!d.recent.length && <div className="hint py-6 text-center">{T.g.none}</div>}
              </div>
            </Panel>
          </>
        )}
      </Loaded>
      <Loaded q={sy} title={T.da.syTitle}>
        {(s) => (
          <Panel title={T.da.syTitle} sub={T.da.syNote}>
            <Kpis>
              <Kpi flat label={T.da.syOn} value={fmt(s.accounts_enabled)} sub={T.da.syOnSub(s.accounts_disabled)} />
              <Kpi flat label={T.da.syWith} value={fmt(s.accounts_with_data)} />
              <Kpi flat label={T.da.syConvs} value={fmt(s.conversations)} sub={`${T.g.limit} ${fmt(s.limits.conversations)}`} />
              <Kpi flat label={T.da.syMsgs} value={fmt(s.messages)} sub={T.da.syMsgsSub(s.limits.messages)} />
              <Kpi flat label={T.da.syBytes} value={bytes(s.bytes)} />
              <Kpi flat label={T.g.day} value={dateOf(nowS())} small />
            </Kpis>
          </Panel>
        )}
      </Loaded>
    </>
  );
}
