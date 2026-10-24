/** The switches that pause a part of the service, the threshold rules, and the audit log
 *  that says who did what. Every action asks first and needs an operator's name. */

import { useState } from "react";
import { actor as actorStore, del, post, put } from "../lib/api";
import { fmt, when } from "../lib/format";
import { invalidate, useData } from "../lib/hooks";
import { nameOf, useT } from "../lib/i18n";
import type { Controls as ControlsT, Rule } from "../lib/types";
import { Btn, DataTable, Field, Grid, Note, Panel, Pill, Toggle, cx, useDialog, useToast } from "../ui/primitives";
import { IpChip } from "../ui/who";
import { Loaded } from "./common";

const SWITCHES = ["free_allowance", "signups", "cloud_service", "sync", "hub", "apps_only"];
const ACTIONS = ["close_signups", "pause_allowance", "pause_sync", "notify"];

export function Controls() {
  const T = useT();
  const dialog = useDialog();
  const toast = useToast();
  const q = useData<ControlsT>("/v1/admin/controls", { poll: 60_000 });
  const [busy, setBusy] = useState<string | null>(null);
  const [draft, setDraft] = useState({ threshold: "", action: "notify", note: "" });

  const needActor = () => {
    if (actorStore.get()) return true;
    toast(T.ct.actorNeeded, "warn");
    return false;
  };
  const run = async (key: string, fn: () => Promise<unknown>, done?: string) => {
    setBusy(key);
    try {
      await fn();
      invalidate("/v1/admin/controls", "/v1/admin/health", "/v1/admin/overview");
      await q.reload();
      if (done) toast(done, "ok");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "bad");
    } finally {
      setBusy(null);
    }
  };

  const flip = async (key: string, on: boolean) => {
    if (!needActor()) return;
    const name = nameOf(T.ct.names, key);
    const what = on ? T.ct.whatOn[key] || "" : T.ct.what[key] || "";
    const r = await dialog({ title: name, body: <span className="whitespace-pre-line">{T.ct.confirm(name, on, what)}</span>, fields: [{ id: "note", label: T.ct.why, type: "text" }], danger: !on, okLabel: on ? T.g.on : T.g.off });
    if (!r) return;
    await run(key, () => post(`/v1/admin/controls/${key}`, { enabled: on, note: String(r.note || "") }));
  };
  const addRule = async () => {
    if (!needActor()) return;
    const threshold = Number(draft.threshold);
    if (!Number.isFinite(threshold) || threshold <= 0) return toast(T.ct.threshold, "warn");
    await run("add", () => post("/v1/admin/controls/rules", { threshold, action: draft.action, enabled: true, note: draft.note }), T.g.done);
    setDraft({ threshold: "", action: "notify", note: "" });
  };
  const updateRule = async (r: Rule, patch: Record<string, unknown>) => {
    if (!needActor()) return;
    await run(`rule-${r.id}`, () => put(`/v1/admin/controls/rules/${r.id}`, patch));
  };
  const removeRule = async (r: Rule) => {
    if (!needActor()) return;
    if (!(await dialog({ title: T.ct.remove, body: T.ct.deleteConfirm(r.id), danger: true, okLabel: T.g.delete }))) return;
    await run(`rule-${r.id}`, () => del(`/v1/admin/controls/rules/${r.id}`));
  };
  const evaluate = () => run("evaluate", async () => toast(T.ct.evaluated((await post<{ fired: number }>("/v1/admin/controls/evaluate")).fired), "ok"));
  const notifyTest = () =>
    run("notify", async () => {
      const r = await post<{ sent: boolean; configured: boolean }>("/v1/admin/controls/notify-test");
      toast(r.sent ? T.ct.notifySent : T.ct.notifyFailed, r.sent ? "ok" : "bad");
    });

  return (
    <Loaded q={q}>
      {(c) => (
        <>
          <Note>{T.ct.intro}</Note>
          <Panel title={T.ct.switches} pad={false}>
            <div className="divide-y divide-line">
              {SWITCHES.map((key) => {
                const s = c.switches[key] || { enabled: true, updated_at: null, actor: "", note: "" };
                return (
                  <div key={key} className={cx("flex flex-wrap items-start gap-3 px-4 py-3", !s.enabled && "bg-bad-soft/40")}>
                    <Toggle on={s.enabled} onChange={(v) => flip(key, v)} disabled={busy === key} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 font-medium">
                        {nameOf(T.ct.names, key)}
                        <Pill tone={s.enabled ? "ok" : "bad"}>{s.enabled ? T.g.on : T.g.off}</Pill>
                        <code className="hint">{key}</code>
                      </div>
                      <div className="sub mt-0.5 max-w-3xl">{T.ct.what[key]}</div>
                      {s.updated_at ? (
                        <div className="hint mt-1">
                          {T.ct.by(s.actor || "console", when(s.updated_at))}
                          {s.note ? ` · ${s.note}` : ""}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
            </div>
          </Panel>
          <Grid cols={2}>
            <Panel
              title={T.ct.rules}
              sub={`${T.ct.accounts(c.accounts_total)}${c.next_threshold ? ` · ${T.ct.next(c.next_threshold)}` : ""}`}
              right={
                <>
                  <Btn size="sm" onClick={evaluate} disabled={busy === "evaluate"}>
                    {T.ct.evaluate}
                  </Btn>
                  <Btn size="sm" onClick={notifyTest} disabled={busy === "notify" || !c.notify_configured} title={c.notify_configured ? "" : T.ct.notifyOff}>
                    {T.ct.notifyTest}
                  </Btn>
                </>
              }
            >
              <div className="hint mb-3">{T.ct.rulesNote}</div>
              {!c.notify_configured && <Note tone="warn" className="mb-3">{T.ct.notifyOff}</Note>}
              <div className="flex flex-col gap-2">
                {c.rules.map((r) => (
                  <div key={r.id} className={cx("rounded-lg border border-line px-3 py-2", !r.enabled && "opacity-60")}>
                    <div className="flex flex-wrap items-center gap-2">
                      <Toggle on={r.enabled} onChange={(v) => updateRule(r, { enabled: v })} disabled={busy === `rule-${r.id}`} />
                      <span className="font-medium">
                        {T.ct.threshold} <b>{fmt(r.threshold)}</b>
                      </span>
                      <span>→</span>
                      <Pill tone={r.action === "notify" ? "cyan" : "warn"}>{nameOf(T.ct.actions, r.action)}</Pill>
                      <code className="hint">#{r.id}</code>
                      <span className="ml-auto flex gap-1">
                        {r.last_fired_at ? (
                          <Btn size="sm" onClick={() => updateRule(r, { rearm: true })} disabled={busy === `rule-${r.id}`}>
                            {T.ct.rearm}
                          </Btn>
                        ) : null}
                        <Btn size="sm" variant="danger" onClick={() => removeRule(r)} disabled={busy === `rule-${r.id}`}>
                          {T.ct.remove}
                        </Btn>
                      </span>
                    </div>
                    <div className="hint mt-1">
                      {T.ct.lastFired}: {r.last_fired_at ? T.ct.firedAt(r.fired_accounts || 0, when(r.last_fired_at)) : T.ct.never}
                      {r.note ? ` · ${r.note}` : ""} · {when(r.created_at)}
                    </div>
                  </div>
                ))}
                {!c.rules.length && <div className="hint py-3 text-center">{T.g.none}</div>}
              </div>
              <div className="mt-4 flex flex-wrap items-end gap-2 rounded-lg bg-panel-2 p-3">
                <Field label={T.ct.threshold} className="w-32">
                  <input className="inp" type="number" min={1} step={1} value={draft.threshold} onChange={(e) => setDraft({ ...draft, threshold: e.target.value })} />
                </Field>
                <Field label={T.ct.action} className="w-48">
                  <select className="inp" value={draft.action} onChange={(e) => setDraft({ ...draft, action: e.target.value })}>
                    {ACTIONS.map((a) => (
                      <option key={a} value={a}>
                        {nameOf(T.ct.actions, a)}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={T.ct.note} className="min-w-[160px] flex-1">
                  <input className="inp" value={draft.note} onChange={(e) => setDraft({ ...draft, note: e.target.value })} />
                </Field>
                <Btn variant="primary" onClick={addRule} disabled={busy === "add" || !draft.threshold}>
                  {T.ct.add}
                </Btn>
              </div>
            </Panel>
            <Panel title={T.ct.audit} pad={false}>
              <DataTable
                rows={c.audit}
                rowKey={(a) => a.id}
                defaultSort="ts"
                limit={100}
                maxHeight={640}
                columns={[
                  { key: "ts", label: T.ct.auditCols.time, num: true, render: (a) => when(a.ts) },
                  { key: "actor", label: T.ct.auditCols.actor, render: (a) => <span className="font-medium">{a.actor}</span> },
                  { key: "action", label: T.ct.auditCols.action, render: (a) => <code>{a.action}</code> },
                  { key: "target", label: T.ct.auditCols.target, render: (a) => <code className="hint">{a.target}</code> },
                  { key: "detail", label: T.ct.auditCols.detail, wrap: true, sortable: false },
                  { key: "ip", label: T.ct.auditCols.ip, sortable: false, hideSm: true, render: (a) => <IpChip ip={a.ip} /> },
                ]}
              />
            </Panel>
          </Grid>
        </>
      )}
    </Loaded>
  );
}
