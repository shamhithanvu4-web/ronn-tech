"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Addr, Card, ConfidenceTag, Empty, ErrorState, Loading, RiskBadge } from "@/components/ui";
import { api } from "@/lib/api";
import { relative } from "@/lib/format";

function tierOf(c) {
  if (c === null || c === undefined) return null;
  if (c >= 75) return "High";
  if (c >= 50) return "Medium";
  if (c >= 25) return "Low";
  return "Weak";
}

function duration(s) {
  if (s === null || s === undefined) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  return `${(s / 60).toFixed(1)} min`;
}

function Kpi({ label, value, note, href }) {
  const body = (
    <>
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {note ? <span className="kpi-note">{note}</span> : null}
    </>
  );
  return href ? (
    <Link href={href} className="card kpi" style={{ color: "inherit" }}>
      {body}
    </Link>
  ) : (
    <div className="card kpi">{body}</div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [d, setD] = useState(null);
  const [error, setError] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setError(null);
    api.get("/dashboard/summary").then(setD).catch(setError);
    const t = setInterval(() => api.get("/dashboard/summary").then(setD).catch(() => {}), 30000);
    return () => clearInterval(t);
  }, [reload]);

  if (error) return <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!d) return <Loading label="Loading dashboard…" lines={5} />;

  const k = d.kpis;
  const maxTop = Math.max(1, ...d.top_vasps.map((t) => t.count));
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="sub">Case-based view of attribution work across the unit.</p>
        </div>
        <div className="row">
          <Link href="/cases" className="btn btn-secondary">
            Import wallets
          </Link>
          <Link href="/analyze" className="btn">
            Analyze a wallet
          </Link>
        </div>
      </div>

      <div className="grid grid-kpi">
        <Kpi label="Open cases" value={k.open_cases} href="/cases" />
        <Kpi label="Analyses this week" value={k.analyses_this_week} />
        <Kpi label="High-confidence attributions" value={k.high_confidence_attributions} note="Confidence ≥ 75" />
        <Kpi label="Median time to attribution" value={duration(k.median_time_to_attribution_seconds)} note="Analyses with a VASP found" />
        <Kpi label="Pending approvals" value={k.pending_approvals} note="Draft lawful requests" />
        <Kpi label="Urgent alerts" value={k.urgent_alerts} note="Unacknowledged" href="/alerts" />
      </div>

      <div className="split">
        <Card title="Recent analyses">
          {d.recent_analyses.length ? (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Wallet</th>
                    <th>Likely deposit destination</th>
                    <th>Confidence</th>
                    <th>Risk</th>
                    <th>When</th>
                  </tr>
                </thead>
                <tbody>
                  {d.recent_analyses.map((a) => (
                    <tr key={a.id} className="clickable" onClick={() => router.push(`/analyses/${a.id}`)}>
                      <td>
                        <Addr value={a.address} copy={false} />
                      </td>
                      <td>
                        {a.status === "done" ? (
                          a.nearest_entity_name || <span className="muted">No VASP ≥ 50</span>
                        ) : a.status === "failed" ? (
                          <span className="form-error">Failed</span>
                        ) : (
                          <span className="muted">{a.stage}…</span>
                        )}
                      </td>
                      <td>{a.nearest_confidence !== null ? <ConfidenceTag tier={tierOf(a.nearest_confidence)} value={a.nearest_confidence} /> : <span className="muted">—</span>}</td>
                      <td>
                        <RiskBadge level={a.risk_level} />
                      </td>
                      <td className="small muted nowrap">{relative(a.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty title="No analyses yet">
              <Link href="/analyze">Analyze a wallet</Link> or receive a case from SAHYOG.
            </Empty>
          )}
        </Card>

        <div className="stack">
          <Card title="Top attributed VASPs" hint="Nearest VASP across completed analyses">
            {d.top_vasps.length ? (
              <div className="bars">
                {d.top_vasps.map((t) => (
                  <div key={t.entity_name} className="bar-row" title={`Average confidence ${t.avg_confidence}`}>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{t.entity_name}</span>
                    <div className="bar-track">
                      <div className="bar-fill" style={{ width: `${(t.count / maxTop) * 100}%` }} />
                    </div>
                    <span className="mono right">{t.count}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted small">No attributions yet.</p>
            )}
          </Card>

          <Card title="Alerts" actions={<Link href="/alerts" className="small">All alerts</Link>}>
            {d.alerts.length ? (
              <div className="stack">
                {d.alerts.slice(0, 6).map((a) => (
                  <div key={a.id} className="stack-sm small">
                    <div className="row">
                      {a.urgent ? <span className="tag tint-high">Urgent</span> : <span className="tag">New transfer</span>}
                      <span className="muted">{relative(a.created_at)}</span>
                    </div>
                    <span>{a.message}</span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted small">No alerts. Watch a wallet from its analysis page.</p>
            )}
          </Card>
        </div>
      </div>
    </>
  );
}
