/** The website's traffic (from the access-log database) and the GitHub repository's
 *  numbers, each with its per-day chart. */

import type { PageProps } from "../App";
import { post } from "../lib/api";
import { bytes, compact, dateOf, dayUtc, fmt, when } from "../lib/format";
import { invalidate, useData } from "../lib/hooks";
import { nameOf, useT } from "../lib/i18n";
import type { GitHub, Traffic } from "../lib/types";
import { Bars, Legend, Lines, Ranks } from "../ui/charts";
import { Btn, DataTable, Grid, Kpi, Kpis, Note, Panel, useToast } from "../ui/primitives";
import { Loaded } from "./common";

export function Site({ days }: PageProps) {
  const T = useT();
  const toast = useToast();
  const tr = useData<Traffic>(`/v1/admin/traffic?days=${days}`, { poll: 300_000 });
  const gh = useData<GitHub>(`/v1/admin/github?days=${days}`, { poll: 300_000 });
  const refresh = async () => {
    try {
      await post("/v1/admin/github/refresh");
      toast(T.si.running, "ok");
      setTimeout(() => {
        invalidate("/v1/admin/github");
        void gh.reload();
      }, 4000);
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "bad");
    }
  };
  return (
    <>
      <Loaded q={tr} title={T.si.title(days)}>
        {(t) => {
          if (!t.available) return <Note tone="warn">{T.si.off}</Note>;
          const ds = t.days || [];
          const tot = (k: keyof (typeof ds)[number]) => ds.reduce((a, d) => a + Number(d[k] || 0), 0);
          const labels = ds.map((d) => d.day.slice(5));
          return (
            <>
              <Kpis>
                <Kpi label={T.si.requests} value={fmt(tot("requests"))} sub={T.si.title(days)} />
                <Kpi label={T.si.pages} value={fmt(tot("pages"))} />
                <Kpi label={T.si.visitors} value={fmt(tot("visitors"))} />
                <Kpi label={T.si.bots} value={fmt(tot("bots"))} />
                <Kpi label={T.si.downloads} value={fmt(tot("downloads"))} />
                <Kpi label={T.si.bytes} value={bytes(tot("bytes"))} sub={t.updated_at ? T.si.updated(when(t.updated_at)) : undefined} small />
              </Kpis>
              <Panel title={T.si.title(days)} sub={t.since ? `${T.g.first} ${t.since}` : undefined}>
                <Lines
                  series={[
                    { name: T.si.pages, values: ds.map((d) => d.pages), color: "var(--c1)" },
                    { name: T.si.visitors, values: ds.map((d) => d.visitors), color: "var(--c2)" },
                    { name: T.si.downloads, values: ds.map((d) => d.downloads), color: "var(--c4)" },
                    { name: T.si.bots, values: ds.map((d) => d.bots), color: "var(--ink-3)" },
                  ]}
                  labels={labels}
                  titles={ds.map((d) => d.day)}
                  format={compact}
                  height={160}
                  fill={false}
                />
                <Legend className="mt-2" items={[{ name: T.si.pages, color: "var(--c1)" }, { name: T.si.visitors, color: "var(--c2)" }, { name: T.si.downloads, color: "var(--c4)" }, { name: T.si.bots, color: "var(--ink-3)" }]} />
              </Panel>
              <Grid cols={3}>
                <Panel title={T.si.topPages}>
                  <Ranks rows={(t.pages || []).slice(0, 15).map((p) => ({ name: <code className="text-[12px]">{p.name}</code>, value: p.hits, color: "var(--c1)" }))} showShare />
                </Panel>
                <Panel title={T.si.referrers}>
                  <Ranks rows={(t.referrers || []).slice(0, 15).map((p) => ({ name: <span className="truncate">{p.name || T.g.unknown}</span>, value: p.hits, color: "var(--c2)" }))} showShare />
                </Panel>
                <Panel title={T.si.files} pad={false}>
                  <DataTable
                    rows={t.downloads || []}
                    rowKey={(d) => d.name}
                    defaultSort="hits"
                    limit={20}
                    columns={[
                      { key: "name", label: T.si.files, render: (d) => <code className="text-[12px]">{d.name}</code> },
                      { key: "hits", label: T.si.downloads, num: true, render: (d) => fmt(d.hits) },
                      { key: "bytes", label: T.si.bytes, num: true, render: (d) => bytes(d.bytes) },
                    ]}
                  />
                </Panel>
              </Grid>
            </>
          );
        }}
      </Loaded>
      <Loaded q={gh} title={T.si.ghTitle}>
        {(g) => {
          if (!g.status.enabled) return <Note tone="warn">{T.si.ghOff}</Note>;
          const ds = g.days;
          const l = g.latest;
          return (
            <>
              <Panel
                title={`${T.si.ghTitle} · ${g.status.repo}`}
                sub={
                  <>
                    {g.status.last_run ? T.si.lastRun(when(g.status.last_run)) : T.ct.never}
                    {g.status.error ? ` · ${T.si.error(g.status.error)}` : ""}
                    {g.status.running ? ` · ${T.si.running}` : ""}
                  </>
                }
                right={
                  <Btn size="sm" onClick={refresh} disabled={g.status.running}>
                    {T.si.refresh}
                  </Btn>
                }
              >
                <Kpis>
                  <Kpi flat label={T.si.stars} value={fmt(l.stars)} sub={l.day ? dayUtc(l.day) : undefined} />
                  <Kpi flat label={T.si.forks} value={fmt(l.forks)} />
                  <Kpi flat label={T.si.watchers} value={fmt(l.watchers)} />
                  <Kpi flat label={T.si.issues} value={fmt(l.issues)} />
                  <Kpi flat label={T.si.ghDownloads} value={fmt(l.downloads)} />
                </Kpis>
                <div className="mt-3">
                  <div className="hint mb-1">{T.si.perDay}</div>
                  <Bars
                    series={[{ name: T.si.legend.new_stars, values: ds.map((d) => Number(d.new_stars || 0)), color: "var(--c3)" }]}
                    lines={[{ name: T.si.legend.new_downloads, values: ds.map((d) => Number(d.new_downloads || 0)), color: "var(--c1)" }]}
                    labels={ds.map((d) => dateOf(d.day).slice(5))}
                    titles={ds.map((d) => dateOf(d.day))}
                    height={140}
                  />
                  <Legend className="mt-2" items={[{ name: T.si.legend.new_stars, color: "var(--c3)" }, { name: T.si.legend.new_downloads, color: "var(--c1)" }]} />
                </div>
              </Panel>
              <Grid cols={3}>
                <Panel title={T.si.platforms}>
                  <Ranks rows={Object.entries(l.platforms || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ name: nameOf(T.platforms, k), value: v, color: "var(--c5)" }))} showShare />
                </Panel>
                <Panel title={T.si.assets} pad={false}>
                  <DataTable rows={l.assets || []} rowKey={(a) => a.name} defaultSort="downloads" limit={20} columns={[{ key: "name", label: T.si.files, render: (a) => <code className="text-[12px]">{a.name}</code> }, { key: "downloads", label: T.si.downloads, num: true, render: (a) => fmt(a.downloads) }]} />
                </Panel>
                <Panel title={T.si.releases} pad={false}>
                  <DataTable rows={l.releases || []} rowKey={(r) => r.tag} defaultSort="downloads" limit={20} columns={[{ key: "tag", label: T.g.version, render: (r) => <code>{r.tag}</code> }, { key: "downloads", label: T.si.downloads, num: true, render: (r) => fmt(r.downloads) }]} />
                </Panel>
              </Grid>
            </>
          );
        }}
      </Loaded>
    </>
  );
}
