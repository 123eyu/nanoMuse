/** Traffic that is not the apps: client families, the heaviest accounts on scripts, the
 *  addresses many accounts were made from. Disabling goes through the account drawer. */

import type { PageProps } from "../App";
import { fmt, money, pct } from "../lib/format";
import { useData } from "../lib/hooks";
import { nameOf, useT } from "../lib/i18n";
import type { Abuse as AbuseT } from "../lib/types";
import { Ranks } from "../ui/charts";
import { DataTable, Grid, Kpi, Kpis, Note, Panel, Pill } from "../ui/primitives";
import { Ago, IpChip, PlatformPill, Who } from "../ui/who";
import { Loaded } from "./common";

export function Abuse({ days }: PageProps) {
  const T = useT();
  const q = useData<AbuseT>(`/v1/admin/analytics/abuse?days=${days}`, { poll: 300_000 });
  return (
    <Loaded q={q}>
      {(d) => {
        const s = d.summary;
        const tag = (app: boolean) => (app ? <Pill tone="ok">{T.ab.appTag}</Pill> : <Pill tone="bad">{T.ab.scriptTag}</Pill>);
        return (
          <>
            <Note>{T.ab.intro}</Note>
            <Kpis>
              <Kpi label={T.ab.nonAppReq} value={fmt(s.non_app_requests)} sub={`${pct(s.non_app_request_share, 1)} / ${fmt(s.requests)}`} tone={s.non_app_request_share > 0.2 ? "bad" : s.non_app_request_share > 0.05 ? "warn" : ""} />
              <Kpi label={T.ab.nonAppCost} value={money(s.non_app_cost_cny)} sub={pct(s.non_app_cost_share, 1)} tone={s.non_app_cost_share > 0.2 ? "bad" : s.non_app_cost_share > 0.05 ? "warn" : ""} />
              <Kpi label={T.ab.nonAppAccounts} value={fmt(s.non_app_accounts)} />
              <Kpi label={T.ab.families} value={fmt(s.families)} />
              <Kpi label={T.ab.shared} value={fmt(d.shared_addresses.length)} sub={T.ab.sharedSub} small />
              <Kpi label={T.ab.suspects} value={fmt(d.suspects.length)} small />
            </Kpis>
            <Panel title={T.ab.refused} sub={d.apps_only ? T.ab.refusedOn : T.ab.refusedOff} right={<a href="#controls" className="btn sm">{T.nav.controls} →</a>} pad={false}>
              {d.refused.length === 0 ? (
                <div className="hint px-4 py-3">{T.ab.refusedNone}</div>
              ) : (
                <DataTable
                  rows={d.refused}
                  rowKey={(r) => r.family}
                  defaultSort="requests"
                  limit={30}
                  columns={[
                    { key: "family", label: T.ab.family, render: (r) => <code>{r.family}</code> },
                    { key: "requests", label: T.g.requests, num: true, render: (r) => fmt(r.requests) },
                    { key: "accounts", label: T.g.accounts, num: true, render: (r) => fmt(r.accounts) },
                    { key: "last_ts", label: T.g.last, num: true, render: (r) => <Ago ts={r.last_ts} /> },
                  ]}
                />
              )}
            </Panel>
            <Panel title={T.ab.families} pad={false}>
              <DataTable
                rows={d.families}
                rowKey={(f) => f.family}
                defaultSort="requests"
                limit={30}
                columns={[
                  { key: "family", label: T.ab.family, render: (f) => <code>{f.family}</code> },
                  { key: "app", label: T.pl.app, render: (f) => tag(f.app) },
                  { key: "platform", label: T.g.platform, render: (f) => <PlatformPill platform={f.platform} /> },
                  { key: "requests", label: T.g.requests, num: true, render: (f) => fmt(f.requests) },
                  { key: "request_share", label: T.ab.reqShare, num: true, render: (f) => pct(f.request_share, 1) },
                  { key: "accounts", label: T.g.accounts, num: true, render: (f) => fmt(f.accounts) },
                  { key: "cost_cny", label: T.g.cost, num: true, render: (f) => money(f.cost_cny) },
                  { key: "cost_share", label: T.ab.costShare, num: true, render: (f) => pct(f.cost_share, 1) },
                  { key: "avg_prompt_tokens", label: T.ab.avgIn, num: true, hideSm: true, render: (f) => fmt(f.avg_prompt_tokens) },
                  { key: "avg_completion_tokens", label: T.ab.avgOut, num: true, hideSm: true, render: (f) => fmt(f.avg_completion_tokens) },
                  { key: "distinct_uas", label: T.ab.uas, num: true, hideSm: true, render: (f) => fmt(f.distinct_uas) },
                ]}
              />
            </Panel>
            <Grid cols={2}>
              <Panel title={T.ab.signIns}>
                <Ranks rows={d.sign_ins.map((r) => ({ name: <span className="inline-flex items-center gap-2"><code>{r.family}</code>{tag(r.app)}</span>, value: r.sign_ins, sub: `${fmt(r.accounts)} ${T.g.accounts}`, color: r.app ? "var(--ok)" : "var(--bad)" }))} limit={15} showShare />
              </Panel>
              <Panel title={T.ab.lastClient}>
                <Ranks rows={d.accounts_by_last_client.map((r) => ({ name: <span className="inline-flex items-center gap-2"><code>{r.family}</code>{tag(r.app)}</span>, value: r.accounts, sub: r.disabled ? T.ab.disabledN(r.disabled) : undefined, color: r.app ? "var(--ok)" : "var(--bad)" }))} limit={15} showShare />
              </Panel>
            </Grid>
            <Panel title={T.ab.suspects} pad={false}>
              <DataTable
                rows={d.suspects}
                rowKey={(a) => a.account_id}
                defaultSort="requests"
                limit={30}
                columns={[
                  { key: "who", label: T.g.account, sortable: false, render: (a) => <Who id={a.account_id} hint={a.hint} identifier={a.identifier} channel={a.channel} disabled={a.disabled} member={a.member} /> },
                  { key: "family", label: T.ab.family, render: (a) => <code>{a.family}</code> },
                  { key: "ua", label: T.ab.ua, hideSm: true, render: (a) => <span className="hint inline-block max-w-[260px] truncate align-middle" title={a.ua}>{a.ua}</span> },
                  { key: "requests", label: T.g.requests, num: true, render: (a) => fmt(a.requests) },
                  { key: "avg_prompt_tokens", label: T.ab.avgIn, num: true, render: (a) => fmt(a.avg_prompt_tokens) },
                  { key: "cost_cny", label: T.g.cost, num: true, render: (a) => money(a.cost_cny) },
                  { key: "created_at", label: T.g.created, num: true, render: (a) => <Ago ts={a.created_at} /> },
                  { key: "last_ts", label: T.g.last, num: true, render: (a) => <Ago ts={a.last_ts} /> },
                ]}
              />
            </Panel>
            <Panel title={T.ab.shared} sub={T.ab.sharedSub} pad={false}>
              <DataTable
                rows={d.shared_addresses}
                rowKey={(a) => a.ip}
                defaultSort="accounts"
                limit={30}
                columns={[
                  { key: "ip", label: T.ad.title(""), render: (a) => <IpChip ip={a.ip} /> },
                  { key: "accounts", label: T.g.accounts, num: true, render: (a) => fmt(a.accounts) },
                  { key: "disabled", label: T.g.disabled, num: true, render: (a) => fmt(a.disabled) },
                  { key: "first_at", label: T.g.first, num: true, render: (a) => <Ago ts={a.first_at} /> },
                  { key: "last_at", label: T.g.last, num: true, render: (a) => <Ago ts={a.last_at} /> },
                ]}
              />
            </Panel>
            <div className="hint">{nameOf(T.platforms, "other")}: {T.pl.notApp}</div>
          </>
        );
      }}
    </Loaded>
  );
}
