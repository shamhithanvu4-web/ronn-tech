"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { Card, Empty, ErrorState, Loading, Modal, SimulatedTag } from "@/components/ui";
import { api } from "@/lib/api";
import { relative, titleCase } from "@/lib/format";

const CRIME_TYPES = ["investment fraud", "task scam", "sextortion", "digital arrest", "ransomware", "darknet", "terror financing", "other"];

function NewCase({ onClose, onDone }) {
  const [f, setF] = useState({ title: "", crime_type: "other", complaint_ref: "", fir_no: "", agency: "", assigned_officer: "" });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim() || null]));
      body.crime_type = f.crime_type;
      onDone(await api.post("/cases", body));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="New case"
      onClose={onClose}
      footer={
        <button className="btn" type="button" disabled={busy || !f.title.trim()} onClick={submit}>
          {busy ? "Creating…" : "Create case"}
        </button>
      }
    >
      <div className="field">
        <label htmlFor="t">Title</label>
        <input id="t" className="input" value={f.title} onChange={set("title")} />
      </div>
      <div className="grid grid-2">
        <div className="field">
          <label htmlFor="ct">Crime type</label>
          <select id="ct" className="select" value={f.crime_type} onChange={set("crime_type")}>
            {CRIME_TYPES.map((c) => (
              <option key={c} value={c}>
                {titleCase(c)}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label htmlFor="cr">Complaint ref (NCRP ack. no.)</label>
          <input id="cr" className="input" value={f.complaint_ref} onChange={set("complaint_ref")} />
        </div>
        <div className="field">
          <label htmlFor="fir">FIR number</label>
          <input id="fir" className="input" value={f.fir_no} onChange={set("fir_no")} />
        </div>
        <div className="field">
          <label htmlFor="ag">Agency</label>
          <input id="ag" className="input" value={f.agency} onChange={set("agency")} />
        </div>
      </div>
      <div className="field">
        <label htmlFor="of">Assigned officer</label>
        <input id="of" className="input" value={f.assigned_officer} onChange={set("assigned_officer")} />
      </div>
      {error ? <div className="form-error">{error.detail || error.message}</div> : null}
    </Modal>
  );
}

function ImportCsv({ onClose, onDone }) {
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState("");
  const [crime, setCrime] = useState("other");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("title", title);
      form.append("crime_type", crime);
      onDone(await api.form("/cases/import", form));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Batch import wallets"
      onClose={onClose}
      footer={
        <button className="btn" type="button" disabled={busy || !file} onClick={submit}>
          {busy ? "Importing…" : "Import and analyse"}
        </button>
      }
    >
      <p className="small muted">
        A CSV with a header row <span className="mono">address,chain_hint,complaint_ref</span>. One case is created with one analysis per wallet;
        analyses run two at a time.
      </p>
      <input className="input" type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <div className="grid grid-2">
        <div className="field">
          <label htmlFor="it">Case title (optional)</label>
          <input id="it" className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div className="field">
          <label htmlFor="ic">Crime type</label>
          <select id="ic" className="select" value={crime} onChange={(e) => setCrime(e.target.value)}>
            {CRIME_TYPES.map((c) => (
              <option key={c} value={c}>
                {titleCase(c)}
              </option>
            ))}
          </select>
        </div>
      </div>
      {error ? <div className="form-error">{error.detail || error.message}</div> : null}
    </Modal>
  );
}

export default function CasesPage() {
  const router = useRouter();
  const [items, setItems] = useState(null);
  const [error, setError] = useState(null);
  const [q, setQ] = useState("");
  const [showClosed, setShowClosed] = useState(false);
  const [modal, setModal] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setError(null);
    const params = new URLSearchParams({ limit: "200" });
    if (!showClosed) params.set("open", "true");
    if (q.trim()) params.set("q", q.trim());
    const t = setTimeout(() => {
      api
        .get(`/cases?${params}`)
        .then((r) => setItems(r.items))
        .catch(setError);
    }, 200);
    return () => clearTimeout(t);
  }, [q, showClosed, reload]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Cases</h1>
          <p className="sub">Wallets, analyses, lawful requests and the timeline of each investigation.</p>
        </div>
        <div className="row">
          <button type="button" className="btn btn-secondary" onClick={() => setModal("import")}>
            Import CSV
          </button>
          <button type="button" className="btn" onClick={() => setModal("new")}>
            New case
          </button>
        </div>
      </div>
      <Card>
        <div className="row-between" style={{ marginBottom: 16 }}>
          <input className="input" style={{ maxWidth: 360 }} placeholder="Search case no., title, complaint ref, FIR" value={q} onChange={(e) => setQ(e.target.value)} />
          <label className="check">
            <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> Show closed
          </label>
        </div>
        {error ? (
          <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />
        ) : items === null ? (
          <Loading />
        ) : items.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Case</th>
                  <th>Title</th>
                  <th>Crime type</th>
                  <th>Status</th>
                  <th className="num">Wallets</th>
                  <th className="num">Requests</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id} className="clickable" onClick={() => router.push(`/cases/${c.id}`)}>
                    <td className="mono nowrap">{c.case_no}</td>
                    <td>
                      {c.title} {c.source === "sahyog_simulated" ? <SimulatedTag /> : null}
                      {c.complaint_ref ? <div className="small muted">{c.complaint_ref}</div> : null}
                    </td>
                    <td>{titleCase(c.crime_type)}</td>
                    <td>
                      <span className="tag tag-mono">{c.status}</span>
                    </td>
                    <td className="num">{c.wallet_count}</td>
                    <td className="num">{c.request_count}</td>
                    <td className="small muted nowrap">{relative(c.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No cases">Create a case, import a CSV of wallets, or receive one from SAHYOG (simulated).</Empty>
        )}
      </Card>
      {modal === "new" ? <NewCase onClose={() => setModal(null)} onDone={(c) => router.push(`/cases/${c.id}`)} /> : null}
      {modal === "import" ? <ImportCsv onClose={() => setModal(null)} onDone={(r) => router.push(`/cases/${r.case_id}`)} /> : null}
    </>
  );
}
