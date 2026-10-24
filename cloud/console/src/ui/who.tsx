/** The chips that name an account, an address, a platform or a status, and open the
 *  drawers: a click on an account goes to `acct=` on the current page, on an address to `ip=`. */

import { useSyncExternalStore, type ReactNode } from "react";
import { clientLine } from "../lib/client";
import { ago } from "../lib/format";
import { nameOf, useT, useZh } from "../lib/i18n";
import { onPlaces, placeOf, placesVersion } from "../lib/places";
import { openAccountHash, openAddressHash } from "../lib/router";
import { platformColor, statusColor } from "./charts";
import { Pill, cx, type Tone } from "./primitives";

export const usePlaces = () => useSyncExternalStore(onPlaces, placesVersion);

export const shortId = (id: string | null | undefined) => String(id || "").slice(0, 8);

/** The name shown for an account: the identifier when known, else the hint, else the id. */
export const identOf = (id: string | null | undefined, hint?: string | null, identifier?: string | null): string => identifier || hint || shortId(id);

export function Who({
  id,
  hint,
  identifier,
  channel,
  tags,
  sub,
  className,
  disabled,
  member,
  stop,
}: {
  id: string;
  hint?: string | null;
  identifier?: string | null;
  channel?: string;
  tags?: ReactNode;
  sub?: ReactNode;
  className?: string;
  disabled?: boolean;
  member?: boolean;
  stop?: boolean;
}) {
  const T = useT();
  const name = identOf(id, hint, identifier);
  const initial = (name || "?").replace(/^\+/, "").slice(0, 1).toUpperCase();
  return (
    <a
      href={openAccountHash(id)}
      className={cx("inline-flex min-w-0 items-center gap-2 rounded-md hover:bg-panel-2", className)}
      onClick={(e) => {
        if (stop) e.stopPropagation();
      }}
    >
      <span className={cx("grid h-6 w-6 flex-none place-items-center rounded-full text-[11px] font-semibold", disabled ? "bg-bad-soft text-bad" : member ? "bg-violet-soft text-violet" : "bg-accent-soft text-accent")}>{initial}</span>
      <span className="min-w-0">
        <span className="flex items-center gap-1.5">
          <span className={cx("truncate font-medium", disabled && "line-through text-ink-3")}>{name}</span>
          {channel && <span className="hint">{nameOf(T.channels, channel)}</span>}
          {tags}
        </span>
        <span className="flex items-center gap-1.5 text-[11.5px] text-ink-3">
          <code>{shortId(id)}</code>
          {sub}
        </span>
      </span>
    </a>
  );
}

export function PlaceText({ ip, className }: { ip: string | null | undefined; className?: string }) {
  usePlaces();
  const T = useT();
  const p = placeOf(ip);
  if (!p) return null;
  const text = p.local ? T.g.local : [p.country, p.province, p.city].filter((x, i, a) => x && a.indexOf(x) === i).join(" ");
  return <span className={cx("text-ink-3", className)}>{text}</span>;
}

export function IpChip({ ip, className, stop = true }: { ip: string | null | undefined; className?: string; stop?: boolean }) {
  if (!ip) return null;
  return (
    <a href={openAddressHash(ip)} className={cx("inline-flex max-w-full items-baseline gap-1.5 rounded px-1 hover:bg-panel-2", className)} onClick={(e) => stop && e.stopPropagation()}>
      <code className="truncate">{ip}</code>
      <PlaceText ip={ip} className="truncate text-[11.5px]" />
    </a>
  );
}

export function PlatformPill({ platform, className, version }: { platform: string | null | undefined; className?: string; version?: string }) {
  const T = useT();
  if (!platform) return null;
  return (
    <span className={cx("pill", className)}>
      <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: platformColor(platform) }} />
      {nameOf(T.platforms, platform)}
      {version && <span className="text-ink-3">{version}</span>}
    </span>
  );
}

export function StatusPill({ status, detail }: { status: string; detail?: string }) {
  const T = useT();
  return (
    <span className="pill" title={detail} style={{ background: "transparent", border: `1px solid ${statusColor(status)}`, color: statusColor(status) }}>
      {nameOf(T.statuses, status)}
    </span>
  );
}

export function TierPill({ tier }: { tier: string }) {
  const T = useT();
  const tone: Tone = tier === "high" ? "ok" : tier === "low" ? "bad" : "warn";
  return <Pill tone={tone}>{nameOf(T.tiers, tier)}</Pill>;
}

export const Client = ({ ua }: { ua: string | null | undefined }) => <span title={ua || ""}>{clientLine(ua)}</span>;

export const Ago = ({ ts }: { ts: number | null | undefined }) => {
  const zh = useZh();
  return <span title={ts ? new Date(ts * 1000).toLocaleString() : ""}>{ago(ts, zh)}</span>;
};

export const Flags = ({ hands, images, tools, stream, retries }: { hands?: boolean | number; images?: number; tools?: number; stream?: boolean; retries?: number }) => {
  const T = useT();
  return (
    <span className="inline-flex gap-1">
      {hands ? <Pill tone="violet">{T.g.hands}</Pill> : null}
      {images ? <Pill tone="cyan">{T.g.images} {images}</Pill> : null}
      {tools ? <Pill>{T.g.tools} {tools}</Pill> : null}
      {stream ? <Pill>{T.g.stream}</Pill> : null}
      {retries ? <Pill tone="warn">{T.g.retries} {retries}</Pill> : null}
    </span>
  );
};
