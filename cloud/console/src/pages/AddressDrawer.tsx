/** One address: where it is and the accounts seen from it. */

import { errorText } from "../App";
import { fmt, when } from "../lib/format";
import { useData } from "../lib/hooks";
import { useT } from "../lib/i18n";
import type { Address } from "../lib/types";
import { DataTable, Drawer, Failed, KV, Loading, Panel } from "../ui/primitives";
import { Ago, Who } from "../ui/who";

export function AddressDrawer({ ip, onClose }: { ip: string; onClose: () => void }) {
  const T = useT();
  const q = useData<Address>(`/v1/admin/address?ip=${encodeURIComponent(ip)}`);
  const p = q.data?.place;
  return (
    <Drawer title={T.ad.title(ip)} sub={p ? (p.local ? T.g.local : p.text || [p.country, p.province, p.city].filter(Boolean).join(" ")) : undefined} onClose={onClose} width={640}>
      {q.error && !q.data ? (
        <Failed error={errorText(q.error)} onRetry={() => void q.reload()} />
      ) : !q.data ? (
        <Loading />
      ) : (
        <div className="flex flex-col gap-3">
          {p && (
            <Panel title={T.ad.place}>
              <KV
                rows={[
                  [T.plc.country, `${p.country || T.g.unknown}${p.code ? ` (${p.code})` : ""}`],
                  [T.plc.province, [p.province, p.city].filter(Boolean).join(" · ") || "·"],
                  [T.ad.isp, p.isp || "·"],
                ]}
              />
            </Panel>
          )}
          <Panel title={T.ad.accounts(q.data.accounts.length)} pad={false}>
            <DataTable
              rows={q.data.accounts}
              rowKey={(a) => a.id}
              defaultSort="last_seen"
              empty={T.ad.none}
              columns={[
                { key: "who", label: T.ad.cols.who, sortable: false, render: (a) => <Who id={a.id} hint={a.hint} channel={a.channel} /> },
                { key: "created_at", label: T.ad.cols.created, num: true, render: (a) => when(a.created_at) },
                { key: "n", label: T.ad.cols.n, num: true, render: (a) => fmt(a.n) },
                { key: "last_seen", label: T.ad.cols.last, num: true, render: (a) => <Ago ts={a.last_seen} /> },
              ]}
            />
          </Panel>
        </div>
      )}
    </Drawer>
  );
}
