/** Where people are: one metric at a time, by country and province. */

import { useMemo } from "react";
import type { PageProps } from "../App";
import { fmt, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { useT } from "../lib/i18n";
import { useRoute } from "../lib/router";
import type { PlaceCell, Places as PlacesT } from "../lib/types";
import { Ranks } from "../ui/charts";
import { Chips, DataTable, Grid, Note, Panel } from "../ui/primitives";
import { Loaded } from "./common";

type Metric = "accounts" | "requests" | "signins" | "new_accounts" | "demo_visitors";
const METRICS: Metric[] = ["accounts", "requests", "signins", "new_accounts", "demo_visitors"];

export function Places({ days }: PageProps) {
  const T = useT();
  const route = useRoute();
  const metric = (METRICS.includes(route.params.get("m") as Metric) ? route.params.get("m") : "accounts") as Metric;
  const country = route.params.get("c") || "";
  const q = useData<PlacesT>(`/v1/admin/places?days=${days}`, { poll: 300_000 });
  const cells: PlaceCell[] = useMemo(() => (q.data ? (q.data[metric] as PlaceCell[] | null) || [] : []), [q.data, metric]);
  const label = (c: string) => (c === "本地网络" ? T.g.local : c || T.plc.unknown);
  return (
    <Loaded q={q}>
      {(d) => {
        if (!d.geo.enabled) return <Note tone="warn">{T.plc.noGeo}</Note>;
        const byCountry = new Map<string, { n: number; ips: number; code: string }>();
        for (const c of cells) {
          const e = byCountry.get(c.country) || { n: 0, ips: 0, code: c.code };
          e.n += c.n;
          e.ips += c.ips;
          byCountry.set(c.country, e);
        }
        const countries = [...byCountry.entries()].sort((a, b) => b[1].n - a[1].n);
        const total = countries.reduce((a, [, v]) => a + v.n, 0) || 1;
        const provinces = country ? cells.filter((c) => c.country === country && c.province) : cells.filter((c) => c.province).slice(0, 40);
        return (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <Chips options={METRICS.map((m) => ({ id: m, label: T.plc.metric[m] }))} value={metric} onChange={(m) => route.set({ m, c: null }, true)} />
              {!d.geo.ready && <span className="hint">{d.geo.fetching ? T.plc.fetching : d.geo.error}</span>}
              {metric === "demo_visitors" && d.demo_visitors === null && <span className="hint">{T.plc.noDemo}</span>}
            </div>
            <Grid cols={2}>
              <Panel title={T.plc.country} sub={`${fmt(total)} · ${T.plc.title(days)}`}>
                <Ranks rows={countries.slice(0, 30).map(([c, v]) => ({ name: label(c), value: v.n, sub: `${fmt(v.ips)} ${T.plc.ips}`, color: c === country ? "var(--accent)" : "var(--c5)", onClick: () => route.set({ c: country === c ? null : c }, true) }))} showShare />
              </Panel>
              <Panel title={country ? `${label(country)} · ${T.plc.province}` : T.plc.province} pad={false}>
                <DataTable
                  rows={provinces}
                  rowKey={(c) => `${c.country}/${c.province}`}
                  defaultSort="n"
                  limit={40}
                  columns={[
                    { key: "country", label: T.plc.country, render: (c) => label(c.country) },
                    { key: "province", label: T.plc.province },
                    { key: "n", label: T.plc.n, num: true, render: (c) => fmt(c.n) },
                    { key: "share", label: T.g.share, num: true, value: (c) => c.n, render: (c) => pct(c.n / total, 1) },
                    { key: "ips", label: T.plc.ips, num: true, render: (c) => fmt(c.ips) },
                  ]}
                />
              </Panel>
            </Grid>
          </>
        );
      }}
    </Loaded>
  );
}
