/** Platforms side by side: requests, accounts, money and telemetry per client platform,
 *  the accounts' last clients, multi-platform use, the hub's devices, the models people
 *  configured themselves. */

import type { PageProps } from "../App";
import { fmt, money, ms, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { nameOf, useT } from "../lib/i18n";
import type { Platforms as PlatformsT } from "../lib/types";
import { Donut, Ranks, Share, platformColor } from "../ui/charts";
import { DataTable, Grid, Kpi, Kpis, Note, Panel, Pill } from "../ui/primitives";
import { PlatformPill } from "../ui/who";
import { Loaded } from "./common";

export function Platforms({ days }: PageProps) {
  const T = useT();
  const q = useData<PlatformsT>(`/v1/admin/analytics/platforms?days=${days}`, { poll: 300_000 });
  return (
    <Loaded q={q}>
      {(d) => {
        const total = d.platforms.reduce((a, p) => a + p.requests, 0) || 1;
        const totalCost = d.platforms.reduce((a, p) => a + p.cost_cny, 0) || 1;
        const appReq = d.platforms.filter((p) => p.app).reduce((a, p) => a + p.requests, 0);
        const devices = d.devices.reduce((a, g) => a + g.devices, 0);
        const online = Object.values(d.online).reduce((a, b) => a + b, 0);
        return (
          <>
            <Note>{T.pl.intro}</Note>
            <Kpis>
              <Kpi label={T.g.requests} value={fmt(total)} sub={`${T.pl.isApp} ${pct(appReq / total)}`} />
              <Kpi label={T.g.accounts} value={fmt(d.accounts_with_requests)} sub={T.pl.multi(d.multi_platform_accounts, d.accounts_with_requests)} />
              <Kpi label={T.g.cost} value={money(totalCost)} />
              <Kpi label={T.pl.devices} value={fmt(devices)} sub={`${T.pl.online} ${fmt(online)}`} />
              <Kpi label={T.pl.ownModels} value={fmt(d.own_models.reduce((a, o) => a + o.devices, 0))} sub={`${fmt(d.own_models.reduce((a, o) => a + o.accounts, 0))} ${T.g.accounts}`} />
              <Kpi label={T.pl.combos} value={d.combinations.length} small />
            </Kpis>
            <Panel title={T.pl.title(days)} pad={false}>
              <DataTable
                rows={d.platforms}
                rowKey={(p) => p.platform}
                defaultSort="requests"
                columns={[
                  { key: "platform", label: T.g.platform, render: (p) => <PlatformPill platform={p.platform} /> },
                  { key: "app", label: T.pl.app, render: (p) => (p.app ? <Pill tone="ok">{T.pl.isApp}</Pill> : <Pill tone="bad">{T.pl.notApp}</Pill>) },
                  { key: "requests", label: T.g.requests, num: true, render: (p) => fmt(p.requests) },
                  { key: "share", label: T.g.share, num: true, value: (p) => p.requests, render: (p) => pct(p.requests / total) },
                  { key: "accounts", label: T.g.accounts, num: true, render: (p) => fmt(p.accounts) },
                  { key: "requests_per_account", label: T.pl.reqPerAcct, num: true, render: (p) => fmt(p.requests_per_account) },
                  { key: "cost_cny", label: T.g.cost, num: true, render: (p) => money(p.cost_cny) },
                  { key: "cost_per_account_cny", label: T.pl.costPerAcct, num: true, hideSm: true, render: (p) => money(p.cost_per_account_cny) },
                  { key: "avg_prompt_tokens", label: T.pl.avgIn, num: true, hideSm: true, render: (p) => fmt(p.avg_prompt_tokens) },
                  { key: "avg_completion_tokens", label: T.pl.avgOut, num: true, hideSm: true, render: (p) => fmt(p.avg_completion_tokens) },
                  { key: "versions", label: T.pl.versions, sortable: false, hideSm: true, render: (p) => <span className="hint">{p.versions.map((v) => `${v.version} (${fmt(v.requests)})`).join(" · ")}</span> },
                ]}
              />
            </Panel>
            <Panel title={T.pl.telemetry} pad={false}>
              <DataTable
                rows={d.platforms.filter((p) => p.calls)}
                rowKey={(p) => p.platform}
                defaultSort="n"
                empty={T.pl.noTele}
                columns={[
                  { key: "platform", label: T.g.platform, render: (p) => <PlatformPill platform={p.platform} /> },
                  { key: "n", label: T.us.calls, num: true, value: (p) => p.calls!.n, render: (p) => fmt(p.calls!.n) },
                  { key: "ok", label: T.pl.okShare, num: true, value: (p) => p.calls!.ok_share, render: (p) => pct(p.calls!.ok_share, 1) },
                  { key: "err", label: T.pl.errShare, num: true, value: (p) => p.calls!.error_share, render: (p) => <span className={p.calls!.error_share > 0.05 ? "text-bad" : ""}>{pct(p.calls!.error_share, 1)}</span> },
                  { key: "refused", label: T.g.refused, num: true, value: (p) => p.calls!.refused, render: (p) => fmt(p.calls!.refused) },
                  { key: "hands", label: T.pl.hands, num: true, value: (p) => p.calls!.hands_share, render: (p) => pct(p.calls!.hands_share, 1) },
                  { key: "images", label: T.pl.images, num: true, value: (p) => p.calls!.images_share, render: (p) => pct(p.calls!.images_share, 1) },
                  { key: "tools", label: T.pl.tools, num: true, value: (p) => p.calls!.tools_share, render: (p) => pct(p.calls!.tools_share, 1) },
                  { key: "stream", label: T.pl.stream, num: true, hideSm: true, value: (p) => p.calls!.stream_share, render: (p) => pct(p.calls!.stream_share) },
                  { key: "msgs", label: T.pl.msgs, num: true, hideSm: true, value: (p) => p.calls!.avg_messages, render: (p) => p.calls!.avg_messages },
                  { key: "ttft", label: T.pl.ttft, num: true, value: (p) => p.calls!.p50_ttft_ms, render: (p) => ms(p.calls!.p50_ttft_ms) },
                  { key: "wait", label: T.pl.wait, num: true, value: (p) => p.calls!.p95_wait_ms, render: (p) => ms(p.calls!.p95_wait_ms) },
                ]}
              />
            </Panel>
            <Grid cols={3}>
              <Panel title={T.pl.lastClient}>
                <div className="flex items-center gap-4">
                  <Donut parts={d.accounts_by_last_client.map((r) => ({ name: nameOf(T.platforms, r.platform), value: r.accounts, color: platformColor(r.platform) }))} center={fmt(d.accounts_by_last_client.reduce((a, r) => a + r.accounts, 0))} />
                  <div className="min-w-0 flex-1">
                    <Ranks rows={d.accounts_by_last_client.map((r) => ({ name: nameOf(T.platforms, r.platform), value: r.accounts, color: platformColor(r.platform), sub: r.disabled ? T.ab.disabledN(r.disabled) : undefined }))} showShare />
                  </div>
                </div>
              </Panel>
              <Panel title={T.pl.combos} sub={T.pl.multi(d.multi_platform_accounts, d.accounts_with_requests)}>
                <Ranks rows={d.combinations.map((c) => ({ name: c.platforms.split("+").map((p) => nameOf(T.platforms, p)).join(" + "), value: c.accounts, color: c.platforms.includes("+") ? "var(--c4)" : platformColor(c.platforms) }))} showShare />
              </Panel>
              <Panel title={T.pl.online} sub={fmt(online)}>
                <Ranks rows={Object.entries(d.online).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ name: k, value: v, color: platformColor(k.split("/")[1] || k.split("/")[0]) }))} />
              </Panel>
            </Grid>
            <Grid cols={2}>
              <Panel title={T.pl.devices} pad={false}>
                <DataTable
                  rows={d.devices}
                  rowKey={(g) => `${g.kind}/${g.os}`}
                  defaultSort="devices"
                  columns={[
                    { key: "kind", label: T.pl.deviceKind },
                    { key: "os", label: T.pl.deviceOs, render: (g) => g.os || "·" },
                    { key: "devices", label: T.g.devices, num: true, render: (g) => fmt(g.devices) },
                    { key: "seen_30d", label: T.pl.seen30, num: true, render: (g) => fmt(g.seen_30d) },
                    { key: "screen", label: T.pl.screen, num: true, render: (g) => fmt(g.screen) },
                    { key: "versions", label: T.pl.versions, sortable: false, render: (g) => <span className="hint">{g.versions.map((v) => `${v.version} (${v.devices})`).join(" · ")}</span> },
                  ]}
                />
              </Panel>
              <Panel title={T.pl.ownModels} sub={T.pl.ownModelsNote}>
                {d.own_models.length ? (
                  <div className="flex flex-col gap-3">
                    <Share parts={d.own_models.map((o, i) => ({ name: o.provider, value: o.devices, color: `var(--c${(i % 8) + 1})` }))} />
                    <DataTable
                      rows={d.own_models}
                      rowKey={(o) => o.provider}
                      defaultSort="devices"
                      columns={[
                        { key: "provider", label: T.pl.provider, render: (o) => <code>{o.provider}</code> },
                        { key: "devices", label: T.g.devices, num: true, render: (o) => fmt(o.devices) },
                        { key: "accounts", label: T.g.accounts, num: true, render: (o) => fmt(o.accounts) },
                        { key: "models", label: T.g.model, sortable: false, wrap: true, render: (o) => <span className="hint">{o.models.map((m) => `${m.model} (${m.devices})`).join(" · ")}</span> },
                      ]}
                    />
                  </div>
                ) : (
                  <div className="hint py-6 text-center">{T.pl.noOwn}</div>
                )}
              </Panel>
            </Grid>
          </>
        );
      }}
    </Loaded>
  );
}
