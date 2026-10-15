"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { useShell } from "@/components/AppShell";
import { Addr, Card, Empty, ErrorState, Loading, TxLink } from "@/components/ui";
import { api } from "@/lib/api";
import { chainName, ist, relative } from "@/lib/format";

export default function AlertsPage() {
  const { refreshAlerts } = useShell();
  const [alerts, setAlerts] = useState(null);
  const [watch, setWatch] = useState(null);
  const [error, setError] = useState(null);
  const [urgentOnly, setUrgentOnly] = useState(false);
  const [busy, setBusy] = useState(null);
  const [msg, setMsg] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setError(null);
    Promise.all([api.get(`/alerts?limit=100${urgentOnly ? "&urgent=true" : ""}`), api.get("/watchlist")])
      .then(([a, w]) => {
        setAlerts(a.items);
        setWatch(w.items);
      })
      .catch(setError);
  }, [urgentOnly, reload]);

  async function ack(id) {
    await api.post(`/alerts/${id}/ack`);
    refreshAlerts();
    setReload((x) => x + 1);
  }

  async function check(id) {
    setBusy(id);
    setMsg(null);
    try {
      const r = await api.post(`/watchlist/${id}/check`);
      setMsg(r.error ? `Check failed: ${r.error}` : `${r.created_alerts.length} new alert(s).`);
      refreshAlerts();
      setReload((x) => x + 1);
    } catch (e) {
      setMsg(e.detail || e.message);
    } finally {
      setBusy(null);
    }
  }

  async function unwatch(id) {
    await api.del(`/watchlist/${id}`);
    setReload((x) => x + 1);
  }

  if (error) return <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!alerts || !watch) return <Loading label="Loading alerts…" />;

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Alerts</h1>
          <p className="sub">Watched wallets are checked every 5 minutes. An alert is urgent when funds reach a labelled VASP within 2 hops.</p>
        </div>
        <label className="check">
          <input type="checkbox" checked={urgentOnly} onChange={(e) => setUrgentOnly(e.target.checked)} /> Urgent only
        </label>
      </div>

      <div className="split">
        <Card title="Alert feed">
          {alerts.length ? (
            <div className="stack">
              {alerts.map((a) => (
                <div key={a.id} className="path" style={{ opacity: a.acknowledged ? 0.65 : 1 }}>
                  <div className="row-between">
                    <div className="row">
                      {a.urgent ? <span className="tag tint-high">Urgent</span> : <span className="tag">New transfer</span>}
                      <span className="small muted">
                        {chainName(a.chain)} · {relative(a.created_at)} · block time {ist(a.block_time)}
                      </span>
                    </div>
                    {!a.acknowledged ? (
                      <button type="button" className="btn btn-secondary btn-sm" onClick={() => ack(a.id)}>
                        Acknowledge
                      </button>
                    ) : (
                      <span className="small muted">Acknowledged</span>
                    )}
                  </div>
                  <span>{a.message}</span>
                  <div className="row small">
                    <span className="muted">From</span>
                    <Addr value={a.address} />
                    <span className="muted">to</span>
                    <Addr value={a.to_addr} />
                    <TxLink hash={a.tx_hash} url={a.explorer_url} />
                  </div>
                  {a.evidence?.reach ? (
                    <span className="small muted">
                      Reaches {a.evidence.reach.entity_name} in {a.evidence.reach.hops} hop(s) via <span className="mono">{a.evidence.reach.source}</span>
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No alerts">Alerts appear when a watched wallet sends funds.</Empty>
          )}
        </Card>

        <Card title="Watchlist" hint={`${watch.length} wallet(s)`}>
          {msg ? <p className="small" style={{ marginBottom: 12 }}>{msg}</p> : null}
          {watch.length ? (
            <div className="stack">
              {watch.map((w) => (
                <div key={w.id} className="stack-sm small">
                  <div className="row-between">
                    <Addr value={w.address} />
                    <span className="muted">{chainName(w.chain)}</span>
                  </div>
                  <span className="muted">
                    Checked {w.last_checked_at ? relative(w.last_checked_at) : "not yet"} · {w.alert_count} alert(s)
                    {w.case_id ? (
                      <>
                        {" · "}
                        <Link href={`/cases/${w.case_id}`}>case</Link>
                      </>
                    ) : null}
                  </span>
                  {w.last_error ? <span className="form-error">{w.last_error}</span> : null}
                  <div className="row">
                    <button type="button" className="btn btn-secondary btn-sm" disabled={busy === w.id} onClick={() => check(w.id)}>
                      {busy === w.id ? "Checking…" : "Check now"}
                    </button>
                    <button type="button" className="btn btn-ghost btn-sm" onClick={() => unwatch(w.id)}>
                      Stop watching
                    </button>
                  </div>
                  <div className="divider" />
                </div>
              ))}
            </div>
          ) : (
            <p className="muted small">No wallets watched. Use “Watch wallet” on an analysis.</p>
          )}
        </Card>
      </div>
    </>
  );
}
