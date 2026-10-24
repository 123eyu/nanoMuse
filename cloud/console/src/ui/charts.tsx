/** Charts drawn as SVG by hand: stacked bars, lines, sparklines, ranked bars, a donut and a
 *  gauge. Each takes plain arrays and a formatter; a tooltip follows the pointer. */

import { useMemo, useRef, useState, type ReactNode } from "react";
import { fmt, maxOf } from "../lib/format";
import { cx, useTooltip } from "./primitives";

export const COLORS = ["var(--c1)", "var(--c2)", "var(--c3)", "var(--c4)", "var(--c5)", "var(--c6)", "var(--c7)", "var(--c8)"];
export const colorAt = (i: number) => COLORS[i % COLORS.length];

/** A stable colour per model name across the console. */
const MODEL_COLORS = new Map<string, string>();
export const modelColor = (name: string): string => {
  let c = MODEL_COLORS.get(name);
  if (!c) {
    c = colorAt(MODEL_COLORS.size);
    MODEL_COLORS.set(name, c);
  }
  return c;
};

export const PLATFORM_COLORS: Record<string, string> = {
  android: "var(--c2)",
  ios: "var(--c1)",
  desktop: "var(--c4)",
  windows: "var(--c5)",
  macos: "var(--c6)",
  linux: "var(--c7)",
  runtime: "var(--c8)",
  browser: "var(--c3)",
  minis: "var(--ink-3)",
  other: "var(--bad)",
};
export const platformColor = (p: string) => PLATFORM_COLORS[p] || "var(--ink-3)";

export const STATUS_COLORS: Record<string, string> = {
  ok: "var(--ok)",
  upstream_error: "var(--bad)",
  upstream_timeout: "var(--c8)",
  content_rejected: "var(--c3)",
  broken: "var(--c6)",
  refused: "var(--violet)",
  client_gone: "var(--ink-3)",
};
export const statusColor = (s: string) => STATUS_COLORS[s] || "var(--ink-3)";

export interface Series {
  name: string;
  values: number[];
  color?: string;
}

export function Legend({ items, className }: { items: { name: ReactNode; color: string; value?: ReactNode }[]; className?: string }) {
  return (
    <div className={cx("flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-ink-2", className)}>
      {items.map((it, i) => (
        <span key={i} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2 w-2 rounded-sm" style={{ background: it.color }} />
          {it.name}
          {it.value !== undefined && <span className="text-ink-3">{it.value}</span>}
        </span>
      ))}
    </div>
  );
}

/** How often to print an x label so they do not overlap. */
export const labelEvery = (n: number, width = 800): number => Math.max(1, Math.ceil(n / Math.max(4, Math.floor(width / 64))));

export function Bars({
  series,
  labels,
  titles,
  height = 160,
  format = fmt,
  onPick,
  picked,
  stacked = true,
  className,
  yTicks = 3,
  lines,
}: {
  series: Series[];
  labels: string[];
  titles?: string[];
  height?: number;
  format?: (v: number) => string;
  onPick?: (i: number) => void;
  picked?: number;
  stacked?: boolean;
  className?: string;
  yTicks?: number;
  lines?: Series[];
}) {
  const tt = useTooltip();
  const n = labels.length;
  const totals = useMemo(() => labels.map((_, i) => (stacked ? series.reduce((a, s) => a + (s.values[i] || 0), 0) : maxOf(series.map((s) => s.values[i] || 0)))), [series, labels, stacked]);
  const max = Math.max(1, maxOf(totals), ...(lines || []).map((l) => maxOf(l.values)));
  const W = 1000,
    H = height,
    padB = 18,
    padT = 6;
  const plotH = H - padB - padT;
  const gap = n > 60 ? 1 : n > 30 ? 2 : 4;
  const bw = (W - gap * (n - 1)) / Math.max(1, n);
  const every = labelEvery(n, 1000);
  const y = (v: number) => padT + plotH - (plotH * v) / max;
  const tip = (i: number) => {
    const parts = series.filter((s) => s.values[i]).map((s) => `${s.name}: ${format(s.values[i] || 0)}`);
    const extra = (lines || []).filter((l) => l.values[i]).map((l) => `${l.name}: ${format(l.values[i] || 0)}`);
    return [titles ? titles[i] : labels[i], ...(series.length > 1 ? [`${format(totals[i])}`] : []), ...parts, ...extra].join("\n");
  };
  return (
    <div className={cx("relative w-full", className)}>
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="block w-full" style={{ height }} onMouseLeave={tt.hide}>
        {Array.from({ length: yTicks }, (_, k) => {
          const v = (max * (k + 1)) / yTicks;
          return <line key={k} x1={0} x2={W} y1={y(v)} y2={y(v)} stroke="var(--line)" strokeWidth={1} vectorEffect="non-scaling-stroke" />;
        })}
        {labels.map((_, i) => {
          let acc = 0;
          const x = i * (bw + gap);
          return (
            <g key={i} className={onPick ? "cursor-pointer" : undefined} onClick={onPick ? () => onPick(i) : undefined} onMouseMove={(e) => tt.show(e, tip(i))}>
              <rect x={x} y={padT} width={bw} height={plotH} fill={picked === i ? "var(--accent-soft)" : "transparent"} />
              {series.map((s, k) => {
                const v = s.values[i] || 0;
                if (!v) return null;
                const h = (plotH * v) / max;
                const top = stacked ? y(acc + v) : y(v);
                if (stacked) acc += v;
                const sw = stacked ? bw : bw / series.length;
                const sx = stacked ? x : x + k * sw;
                return <rect key={k} x={sx} y={top} width={Math.max(0.5, sw)} height={Math.max(0.5, h)} fill={s.color || colorAt(k)} rx={n <= 31 ? 2 : 0} />;
              })}
            </g>
          );
        })}
        {(lines || []).map((l, k) => (
          <polyline key={k} fill="none" stroke={l.color || colorAt(series.length + k)} strokeWidth={1.5} vectorEffect="non-scaling-stroke" points={l.values.map((v, i) => `${i * (bw + gap) + bw / 2},${y(v)}`).join(" ")} />
        ))}
      </svg>
      <div className="relative mt-1 h-4 w-full">
        {labels.map((l, i) =>
          i % every === 0 ? (
            <span key={i} className="absolute top-0 text-[10.5px] text-ink-3" style={{ left: `${((i * (bw + gap) + bw / 2) / W) * 100}%`, transform: "translateX(-50%)" }}>
              {l}
            </span>
          ) : null,
        )}
      </div>
      <span className="pointer-events-none absolute top-0 right-0 text-[10.5px] text-ink-3">{format(max)}</span>
      {tt.node}
    </div>
  );
}

export function Lines({
  series,
  labels,
  titles,
  height = 160,
  format = fmt,
  className,
  fill = true,
  yTicks = 3,
  onPick,
  max: maxIn,
}: {
  series: Series[];
  labels: string[];
  titles?: string[];
  height?: number;
  format?: (v: number) => string;
  className?: string;
  fill?: boolean;
  yTicks?: number;
  onPick?: (i: number) => void;
  max?: number;
}) {
  const tt = useTooltip();
  const [hover, setHover] = useState<number | null>(null);
  const n = labels.length;
  const max = Math.max(1e-9, maxIn ?? maxOf(series.flatMap((s) => s.values)));
  const W = 1000,
    H = height,
    padB = 18,
    padT = 6;
  const plotH = H - padB - padT;
  const x = (i: number) => (n <= 1 ? W / 2 : (i * W) / (n - 1));
  const y = (v: number) => padT + plotH - (plotH * v) / max;
  const every = labelEvery(n, 1000);
  const ref = useRef<SVGSVGElement>(null);
  const onMove = (e: React.MouseEvent) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r || !n) return;
    const i = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width) * (n - 1))));
    setHover(i);
    tt.show(e, [titles ? titles[i] : labels[i], ...series.map((s) => `${s.name}: ${format(s.values[i] || 0)}`)].join("\n"));
  };
  return (
    <div className={cx("relative w-full", className)}>
      <svg
        ref={ref}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className={cx("block w-full", onPick && "cursor-pointer")}
        style={{ height }}
        onMouseMove={onMove}
        onMouseLeave={() => {
          setHover(null);
          tt.hide();
        }}
        onClick={() => hover !== null && onPick?.(hover)}
      >
        {Array.from({ length: yTicks }, (_, k) => {
          const v = (max * (k + 1)) / yTicks;
          return <line key={k} x1={0} x2={W} y1={y(v)} y2={y(v)} stroke="var(--line)" strokeWidth={1} vectorEffect="non-scaling-stroke" />;
        })}
        {series.map((s, k) => {
          const pts = s.values.map((v, i) => `${x(i)},${y(v || 0)}`).join(" ");
          const color = s.color || colorAt(k);
          return (
            <g key={k}>
              {fill && n > 1 && <polygon points={`${x(0)},${y(0)} ${pts} ${x(n - 1)},${y(0)}`} fill={color} opacity={0.08} />}
              <polyline fill="none" stroke={color} strokeWidth={1.8} vectorEffect="non-scaling-stroke" points={pts} strokeLinejoin="round" />
            </g>
          );
        })}
        {hover !== null && <line x1={x(hover)} x2={x(hover)} y1={padT} y2={padT + plotH} stroke="var(--ink-3)" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />}
        {hover !== null && series.map((s, k) => <circle key={k} cx={x(hover)} cy={y(s.values[hover] || 0)} r={4} fill={s.color || colorAt(k)} vectorEffect="non-scaling-stroke" />)}
      </svg>
      <div className="relative mt-1 h-4 w-full">
        {labels.map((l, i) =>
          i % every === 0 ? (
            <span key={i} className="absolute top-0 text-[10.5px] text-ink-3" style={{ left: `${(x(i) / W) * 100}%`, transform: i === 0 ? "none" : i === n - 1 ? "translateX(-100%)" : "translateX(-50%)" }}>
              {l}
            </span>
          ) : null,
        )}
      </div>
      <span className="pointer-events-none absolute top-0 right-0 text-[10.5px] text-ink-3">{format(max)}</span>
      {tt.node}
    </div>
  );
}

export function Spark({ values, color = "var(--accent)", height = 28, width = 90, className }: { values: number[]; color?: string; height?: number; width?: number; className?: string }) {
  const n = values.length;
  const max = Math.max(1e-9, maxOf(values));
  const pts = values.map((v, i) => `${n <= 1 ? width / 2 : (i * width) / (n - 1)},${height - 2 - ((height - 4) * (v || 0)) / max}`).join(" ");
  return (
    <svg width={width} height={height} className={cx("block", className)}>
      <polyline fill="none" stroke={color} strokeWidth={1.5} points={pts} strokeLinejoin="round" />
    </svg>
  );
}

export function Ranks({
  rows,
  format = fmt,
  max: maxIn,
  className,
  limit,
  showShare,
}: {
  rows: { name: ReactNode; value: number; sub?: ReactNode; color?: string; onClick?: () => void; title?: string }[];
  format?: (v: number) => string;
  max?: number;
  className?: string;
  limit?: number;
  showShare?: boolean;
}) {
  const shown = limit ? rows.slice(0, limit) : rows;
  const max = Math.max(1e-9, maxIn ?? maxOf(rows.map((r) => r.value)));
  const total = rows.reduce((a, r) => a + r.value, 0) || 1;
  return (
    <div className={cx("flex flex-col gap-1.5", className)}>
      {shown.map((r, i) => (
        <div key={i} className={cx("grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-0.5", r.onClick && "cursor-pointer rounded-md hover:bg-panel-2")} onClick={r.onClick} title={r.title}>
          <div className="min-w-0 truncate text-[12.5px]">{r.name}</div>
          <div className="text-right text-[12.5px] font-medium">
            {format(r.value)}
            {showShare && <span className="ml-1.5 text-ink-3">{((100 * r.value) / total).toFixed(0)}%</span>}
          </div>
          <div className="col-span-2">
            <div className="bar-track h-1.5">
              <div className="bar-fill" style={{ width: `${(100 * r.value) / max}%`, background: r.color || colorAt(i) }} />
            </div>
            {r.sub && <div className="hint mt-0.5 truncate">{r.sub}</div>}
          </div>
        </div>
      ))}
      {!shown.length && <div className="hint py-3 text-center">·</div>}
    </div>
  );
}

export function Donut({ parts, size = 120, thickness = 16, center, className }: { parts: { name: string; value: number; color: string }[]; size?: number; thickness?: number; center?: ReactNode; className?: string }) {
  const total = parts.reduce((a, p) => a + p.value, 0);
  const r = (size - thickness) / 2;
  const C = 2 * Math.PI * r;
  let acc = 0;
  return (
    <div className={cx("relative inline-block", className)} style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="var(--fill)" strokeWidth={thickness} />
        {total > 0 &&
          parts.map((p, i) => {
            const len = (C * p.value) / total;
            const el = <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={p.color} strokeWidth={thickness} strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-acc} />;
            acc += len;
            return el;
          })}
      </svg>
      {center && <div className="absolute inset-0 flex items-center justify-center text-center text-[13px] font-semibold">{center}</div>}
    </div>
  );
}

/** Slots in use of a limit: one cell per slot, the waiting ones shown as a count after. */
export function Gauge({ active, limit, waiting = 0, className }: { active: number; limit: number; waiting?: number; className?: string }) {
  const cells = Math.max(1, Math.min(64, limit));
  const share = limit > 0 ? active / limit : 0;
  const color = waiting > 0 || share >= 1 ? "var(--bad)" : share >= 0.75 ? "var(--warn)" : "var(--ok)";
  return (
    <div className={cx("flex items-center gap-2", className)}>
      <div className="flex flex-1 gap-0.5">
        {Array.from({ length: cells }, (_, i) => (
          <span key={i} className="h-3 flex-1 rounded-sm" style={{ background: i < Math.round(share * cells) ? color : "var(--fill)" }} />
        ))}
      </div>
      {waiting > 0 && <span className="pill bad">+{waiting}</span>}
    </div>
  );
}

/** A tiny stacked share bar with a legend, for a row in a table. */
export function Share({ parts, className, height = 8 }: { parts: { value: number; color: string; name?: string }[]; className?: string; height?: number }) {
  const total = parts.reduce((a, p) => a + p.value, 0) || 1;
  return (
    <div className={cx("flex w-full overflow-hidden rounded-full bg-fill", className)} style={{ height }} title={parts.map((p) => `${p.name ?? ""} ${((100 * p.value) / total).toFixed(0)}%`).join(" · ")}>
      {parts.map((p, i) => (
        <span key={i} style={{ width: `${(100 * p.value) / total}%`, background: p.color }} />
      ))}
    </div>
  );
}

/** The style for a heat-map cell: the colour's alpha follows value/max. */
export const heatStyle = (value: number, max: number, color = "37, 99, 235"): React.CSSProperties => {
  const a = max > 0 ? Math.min(1, value / max) : 0;
  return { background: `rgba(${color}, ${(0.08 + 0.72 * a).toFixed(3)})`, color: a > 0.55 ? "#fff" : "inherit" };
};
