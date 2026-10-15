"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Addr, Card, ConfidenceTag, Empty, ErrorState, Loading, Modal, RiskBadge, SimulatedTag } from "@/components/ui";
import { api } from "@/lib/api";
import { CHAIN_OPTIONS, detectChain } from "@/lib/chain";
import { ist, relative, titleCase } from "@/lib/format";

function tierOf(c) {
  if (c === null || c === undefined) return null;
  return c >= 75 ? "High" : c >= 50 ? "Medium" : c >= 25 ? "Low" : "Weak";
}

function AddWallet({ caseId, onClose, onDone }) {
  const [address, setAddress] = useState("");
  const [chain, setChain] = useState("");
  const [ref, setRef] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const det = detectChain(address);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/cases/${caseId}/wallets`, { address: address.trim(), chain_hint: chain || null, complaint_ref: ref.trim() || null, analyse: true });
      onDone();
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Add wallet"
      onClose={onClose}
      footer={
        <button className="btn" type="button" disabled={busy || !address.trim()} onClick={submit}>
          {busy ? "Adding…" : "Add and analyse"}
        </button>
      }
    >
      <div className="field">
        <label htmlFor="wa">Address</label>
        <input id="wa" className="input mono" value={address} onChange={(e) => setAddress(e.target.value)} spellCheck={false} />
        {address.trim() ? <span className={`chip ${det.ok ? "chip-ok" : "chip-bad"}`}>{det.label}</span> : null}
      </div>
      <div className="grid grid-2">
        <div className="field">
          <label htmlFor="wc">Chain</label>
          <select id="wc" className="select" value={chain} onChange={(e) => setChain(e.target.value)}>
            {CHAIN_OPTIONS.map(([v, l]) => (
              <option key={v} value={v}>
                {l}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="wr">Complaint ref</label>
          <input id="wr" className="input" value={ref} onChange={(e) => setRef(e.target.value)} />
        </div>
      </div>
      {error ? <div className="form-error">{error.detail || error.message}</div> : null}
    </Modal>
  );
}

export default function CasePage() {
  const { id } = useParams();
  const router = useRouter();
  const [c, setC] = useState(null);
  const [error, setError] = useState(null);
  const [modal, setModal] = useState(false);
  const [statusMsg, setStatusMsg] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    let timer;
    async function load() {
      try {
        const r = await api.get(`/cases/${id}`);
        if (!alive) return;
        setC(r);
        setError(null);
        if (r.analyses.some((a) => a.status === "queued" || a.status === "running")) timer = setTimeout(load, 2000);
      } catch (e) {
        if (alive) setError(e);
      }
    }
    load();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [id, reload]);

  async function changeStatus(status) {
    setStatusMsg(null);
    try {
      await api.patch(`/cases/${id}`, { status });
      setReload((x) => x + 1);
    } catch (e) {
      setStatusMsg(e.detail || e.message);
    }
  }

  if (error) return <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!c) return <Loading label="Loading case…" lines={4} />;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="row">
            <h1 className="mono">{c.case_no}</h1>
            <span className="tag tag-mono">{c.status}</span>
            {c.source === "sahyog_simulated" ? <SimulatedTag /> : null}
          </div>
          <p className="sub">{c.title}</p>
        </div>
        <div className="row">
          <select className="select" style={{ width: 220 }} value="" onChange={(e) => e.target.value && changeStatus(e.target.value)} aria-label="Change status">
            <option value="">Change status…</option>
            {c.allowed_status.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn-secondary" onClick={() => setModal(true)} disabled={c.status === "CLOSED"}>
            Add wallet
          </button>
          <Link href={`/analyze?case=${c.id}`} className="btn">
            Analyze in case
          </Link>
        </div>
      </div>
      {statusMsg ? <div className="notice notice-warn">{statusMsg}</div> : null}

      <div className="split">
        <div className="stack">
          <Card title="Wallets and analyses" hint={`${c.wallets.length} wallet(s)`}>
            {c.wallets.length ? (
              <div className="table-wrap">
                <table className="table">
                  <thead>
                    <tr>
                      <th>Wallet</th>
                      <th>Likely deposit destination</th>
                      <th>Confidence</th>
                      <th>Risk</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {c.wallets.map((w) => {
                      const a = w.analysis;
                      return (
                        <tr key={w.id} className={a ? "clickable" : ""} onClick={() => a && router.push(`/analyses/${a.id}`)}>
                          <td>
                            <Addr value={w.address} />
                            <div className="small muted">{w.chain_hint || "auto"}{w.complaint_ref ? ` · ${w.complaint_ref}` : ""}</div>
                          </td>
                          <td>{a?.status === "done" ? a.nearest_entity_name || <span className="muted">No VASP ≥ 50</span> : <span className="muted">—</span>}</td>
                          <td>{a?.nearest_confidence != null ? <ConfidenceTag tier={tierOf(a.nearest_confidence)} value={a.nearest_confidence} /> : <span className="muted">—</span>}</td>
                          <td>
                            <RiskBadge level={a?.risk_level} />
                          </td>
                          <td className="small">
                            {a ? (a.status === "failed" ? <span className="form-error" title={a.error}>Failed</span> : a.status === "done" ? "Done" : `${titleCase(a.stage)} ${a.progress}%`) : "Not analysed"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <Empty title="No wallets yet">Add a wallet to start an analysis.</Empty>
            )}
          </Card>

          <Card title="Lawful requests" hint="Draft from an analysis page; a different supervisor approves">
            {c.requests.length ? (
              <table className="table">
                <thead>
                  <tr>
                    <th>Request</th>
                    <th>Type</th>
                    <th>VASP</th>
                    <th>Routing</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {c.requests.map((r) => (
                    <tr key={r.id} className="clickable" onClick={() => router.push(`/requests/${r.id}`)}>
                      <td className="mono nowrap">{r.request_no}</td>
                      <td>{titleCase(r.request_type)}</td>
                      <td>{r.entity_name}</td>
                      <td className="small">
                        {r.routing} <SimulatedTag />
                      </td>
                      <td>
                        <span className="tag tag-mono">{r.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="muted small">No requests yet.</p>
            )}
          </Card>
        </div>

        <div className="stack">
          <Card title="Details">
            <dl className="dl">
              <dt>Crime type</dt>
              <dd>{titleCase(c.crime_type)}</dd>
              <dt>Complaint ref</dt>
              <dd>{c.complaint_ref || "—"}</dd>
              <dt>FIR</dt>
              <dd>{c.fir_no || "—"}</dd>
              <dt>Agency</dt>
              <dd>{c.agency || "—"}</dd>
              <dt>Officer</dt>
              <dd>{c.assigned_officer || "—"}</dd>
              <dt>Source</dt>
              <dd>
                {c.source === "sahyog_simulated" ? (
                  <>
                    SAHYOG <SimulatedTag /> <span className="mono small">{c.sahyog_ref}</span>
                  </>
                ) : (
                  titleCase(c.source)
                )}
              </dd>
              <dt>Created</dt>
              <dd>
                {ist(c.created_at)} by {c.created_by}
              </dd>
            </dl>
          </Card>
          <Card title="Timeline">
            <ul className="timeline">
              {c.timeline.map((e) => (
                <li key={e.id}>
                  <span className="tl-dot" />
                  <div className="stack-sm" style={{ gap: 2 }}>
                    <span className="small">{e.message}</span>
                    <span className="small muted">
                      {e.actor} · {relative(e.ts)}
                    </span>
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
      {modal ? (
        <AddWallet
          caseId={c.id}
          onClose={() => setModal(false)}
          onDone={() => {
            setModal(false);
            setReload((x) => x + 1);
          }}
        />
      ) : null}
    </>
  );
}
