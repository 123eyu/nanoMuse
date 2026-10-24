/** Every metric of stats.py: a chart for the series, a table for the rest, the CSV, and
 *  how each is computed. */

import { useState } from "react";
import type { PageProps } from "../App";
import { download } from "../lib/api";
import { dateOf, dayUtc, fmt, money, pct, when } from "../lib/format";
import { useData } from "../lib/hooks";
import { useT, useZh } from "../lib/i18n";
import type { Metric, Stats } from "../lib/types";
import { Legend, Lines, colorAt } from "../ui/charts";
import { Btn, DataTable, Panel, Seg, useToast, type Col } from "../ui/primitives";
import { Loaded } from "./common";

const cell = (v: unknown, kind: string): string => {
  if (v === null || v === undefined || v === "") return "·";
  switch (kind) {
    case "cny":
      return money(Number(v));
    case "pct":
      return pct(Number(v), 1);
    case "ts":
      return when(Number(v));
    case "day":
      return typeof v === "number" ? dayUtc(v) : String(v);
    case "bool":
      return v ? "✓" : "·";
    case "int":
      return fmt(Number(v));
    default:
      return String(v);
  }
};

function MetricPanel({ m }: { m: Metric }) {
  const T = useT();
  const zh = useZh();
  const toast = useToast();
  const [mode, setMode] = useState<"chart" | "table">(m.shape === "series" ? "chart" : "table");
  const chartKeys = (m.chart as string[]).length ? (m.chart as string[]) : m.columns.filter((c) => c.kind === "int" && c.key !== "day").map((c) => c.key);
  const cols: Col<Record<string, unknown>>[] = m.columns.map((c) => ({
    key: c.key,
    label: zh ? c.label_zh : c.label,
    num: c.kind !== "str" && c.kind !== "day",
    render: (r) => cell(r[c.key], c.kind),
    value: (r) => r[c.key] as number | string,
  }));
  const rows = m.rows;
  const labels = rows.map((r) => (typeof r.day === "number" ? dayUtc(r.day as number).slice(5) : String(r.day ?? "")));
  const titles = rows.map((r) => (typeof r.day === "number" ? dayUtc(r.day as number) : String(r.day ?? "")));
  const last = rows[rows.length - 1] || {};
  return (
    <Panel
      id={`m-${m.id}`}
      title={zh ? m.title_zh : m.title}
      sub={zh ? m.how_zh : m.how}
      right={
        <>
          {m.shape === "series" && <Seg options={[{ id: "chart", label: T.st.chart }, { id: "table", label: T.st.table }]} value={mode} onChange={setMode} />}
          <Btn size="sm" onClick={() => download(`/v1/admin/stats/${m.id}.csv`, `${m.id}.csv`, true).catch((e) => toast(String(e.message || e), "bad"))}>
            {T.st.csv}
          </Btn>
        </>
      }
      pad={mode === "chart"}
    >
      {mode === "chart" ? (
        <>
          <Lines series={chartKeys.map((k, i) => ({ name: (zh ? m.columns.find((c) => c.key === k)?.label_zh : m.columns.find((c) => c.key === k)?.label) || k, values: rows.map((r) => Number(r[k] || 0)), color: colorAt(i) }))} labels={labels} titles={titles} height={150} fill={chartKeys.length === 1} />
          <Legend className="mt-2" items={chartKeys.map((k, i) => ({ name: (zh ? m.columns.find((c) => c.key === k)?.label_zh : m.columns.find((c) => c.key === k)?.label) || k, color: colorAt(i), value: cell(last[k], m.columns.find((c) => c.key === k)?.kind || "int") }))} />
        </>
      ) : (
        <DataTable rows={rows} rowKey={(_, i) => i} columns={cols} limit={60} defaultSort={m.shape === "series" ? "day" : undefined} />
      )}
    </Panel>
  );
}

export function StatsPage({ days }: PageProps) {
  const T = useT();
  const zh = useZh();
  const q = useData<Stats>(`/v1/admin/stats?days=${days}`, { poll: 300_000 });
  return (
    <Loaded q={q}>
      {(d) => (
        <>
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="hint mr-1">{T.st.jump}</span>
            {d.metrics.map((m) => (
              <a key={m.id} href={`#stats?days=${days}`} className="chip" onClick={(e) => { e.preventDefault(); document.getElementById(`m-${m.id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }); }}>
                {zh ? m.title_zh : m.title}
              </a>
            ))}
            <span className="hint ml-auto">{T.st.title(days)} · {dateOf(d.since)} →</span>
          </div>
          {d.metrics.map((m) => (
            <MetricPanel key={m.id} m={m} />
          ))}
        </>
      )}
    </Loaded>
  );
}
