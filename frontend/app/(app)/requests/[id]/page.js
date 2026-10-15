"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { useUser } from "@/components/AppShell";
import { Addr, Card, ErrorState, Loading, SimulatedTag, TxLink } from "@/components/ui";
import { api } from "@/lib/api";
import { hasRole } from "@/lib/auth";
import { ist, titleCase } from "@/lib/format";

const STEPS = [
  ["DRAFT", "Drafted"],
  ["APPROVED", "Approved"],
  ["DISPATCHED_SIMULATED", "Dispatched (simulated)"],
  ["RESPONSE_RECEIVED", "Response received"],
];

export default function RequestPage() {
  const { id } = useParams();
  const user = useUser();
  const [r, setR] = useState(null);
  const [error, setError] = useState(null);
  const [actionError, setActionError] = useState(null);
  const [busy, setBusy] = useState(null);
  const [pdf, setPdf] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setError(null);
    api.get(`/requests/${id}`).then(setR).catch(setError);
  }, [id, reload]);

  useEffect(() => {
    let url;
    let alive = true;
    if (!r) return undefined;
    api
      .file(`/requests/${id}/pdf`, "request.pdf")
      .then(({ blob, sha256 }) => {
        if (!alive) return;
        url = URL.createObjectURL(blob);
        setPdf({ url, sha256 });
      })
      .catch(() => setPdf(null));
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, r]);

  async function act(path, body) {
    setBusy(path);
    setActionError(null);
    try {
      setR(await api.post(`/requests/${id}/${path}`, body));
    } catch (e) {
      setActionError(e.detail || e.message);
    } finally {
      setBusy(null);
    }
  }

  if (error) return <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!r) return <Loading label="Loading request…" lines={4} />;

  const idx = STEPS.findIndex(([s]) => s === r.status);
  const isSupervisor = hasRole(user, "SUPERVISOR");
  const sameAsDrafter = user && user.username === r.drafted_by;
  return (
    <>
      <div className="page-head">
        <div>
          <div className="row">
            <h1 className="mono">{r.request_no}</h1>
            <span className="tag tag-mono">{r.status}</span>
            <SimulatedTag />
          </div>
          <p className="sub">
            {titleCase(r.request_type)} to {r.entity_name} · case <Link href={`/cases/${r.case_id}`}>{r.case_no}</Link>
          </p>
        </div>
      </div>

      <section className="card">
        <div className="stages" style={{ gridTemplateColumns: "repeat(4, minmax(0, 1fr))" }}>
          {STEPS.map(([s, label], i) => (
            <div key={s} className={`stage ${i <= idx ? "done" : ""}`}>
              <div className="stage-bar">
                <span />
              </div>
              <span>{label}</span>
            </div>
          ))}
        </div>
        <div className="row" style={{ marginTop: 20 }}>
          {r.status === "DRAFT" ? (
            <button
              type="button"
              className="btn"
              disabled={!isSupervisor || sameAsDrafter || busy}
              onClick={() => act("approve", { note: "" })}
              title={!isSupervisor ? "Only a supervisor can approve" : sameAsDrafter ? "The approver must differ from the drafter" : ""}
            >
              {busy === "approve" ? "Approving…" : "Approve"}
            </button>
          ) : null}
          {r.status === "APPROVED" ? (
            <button type="button" className="btn" disabled={busy} onClick={() => act("dispatch")}>
              {busy === "dispatch" ? "Queuing…" : "Dispatch to SAHYOG"}
            </button>
          ) : null}
          {r.status === "DISPATCHED_SIMULATED" ? (
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => act("response", { note: "" })}>
              Record response received
            </button>
          ) : null}
          {r.status === "DRAFT" && !isSupervisor ? <span className="small muted">Waiting for a supervisor (not the drafter) to approve.</span> : null}
          {r.outbox ? (
            <span className="small">
              {r.outbox.status} <SimulatedTag />
            </span>
          ) : null}
        </div>
        {actionError ? <div className="form-error" style={{ marginTop: 12 }}>{actionError}</div> : null}
      </section>

      <div className="split">
        <Card title="PDF preview" hint={pdf?.sha256 ? `SHA-256 ${pdf.sha256.slice(0, 16)}…` : ""}>
          {pdf ? <iframe className="pdf" src={pdf.url} title="Request PDF" /> : <Loading label="Rendering PDF…" />}
        </Card>
        <div className="stack">
          <Card title="Routing">
            <div className="stack-sm small">
              <b>
                {r.routing} <SimulatedTag />
              </b>
              <span>{r.routing_reason}</span>
              {r.routing.startsWith("Manual") ? (
                <span className="muted">An ADMIN can record SAHYOG onboarding for this VASP, with a source URL, in the VASP directory.</span>
              ) : null}
            </div>
          </Card>
          <Card title="Legal basis">
            <div className="stack-sm small">
              <span>{r.legal_basis_label}</span>
              <span className="muted">{r.legal_note}</span>
            </div>
          </Card>
          <Card title="Subject">
            <dl className="dl">
              <dt>Suspect</dt>
              <dd>
                <Addr value={r.payload.suspect_address} />
              </dd>
              <dt>Deposit address</dt>
              <dd>
                <Addr value={r.payload.target_address} />
              </dd>
              <dt>Confidence</dt>
              <dd className="mono">
                {r.confidence?.toFixed(0)} ({r.payload.tier})
              </dd>
              <dt>Evidence</dt>
              <dd className="stack-sm">
                {(r.payload.tx_hashes || []).slice(0, 6).map((h) => (
                  <TxLink key={h} hash={h} />
                ))}
              </dd>
              <dt>Analysis</dt>
              <dd>
                <Link href={`/analyses/${r.analysis_id}`}>Open analysis</Link>
              </dd>
            </dl>
          </Card>
          <Card title="Trail">
            <dl className="dl small">
              <dt>Drafted</dt>
              <dd>
                {r.drafted_by} · {ist(r.drafted_at)}
              </dd>
              <dt>Approved</dt>
              <dd>{r.approved_by ? `${r.approved_by} · ${ist(r.approved_at)}` : "—"}</dd>
              <dt>Dispatched</dt>
              <dd>{r.dispatched_by ? `${r.dispatched_by} · ${ist(r.dispatched_at)}` : "—"}</dd>
            </dl>
          </Card>
        </div>
      </div>
    </>
  );
}
