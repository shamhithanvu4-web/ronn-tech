"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { useUser } from "@/components/AppShell";
import { Modal, SimulatedTag } from "@/components/ui";
import { api, saveBlob } from "@/lib/api";
import { hasRole } from "@/lib/auth";

function AddToCase({ analysis, onClose, onDone }) {
  const [cases, setCases] = useState(null);
  const [caseId, setCaseId] = useState("");
  const [title, setTitle] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get("/cases?open=true&limit=100")
      .then((r) => setCases(r.items || []))
      .catch((e) => setError(e));
  }, []);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      let id = caseId;
      if (caseId === "new") {
        const c = await api.post("/cases", { title: title.trim() || `Wallet ${analysis.address.slice(0, 10)}…`, crime_type: "other" });
        id = c.id;
      }
      await api.post(`/cases/${id}/wallets`, { address: analysis.address, chain_hint: analysis.chain, analysis_id: analysis.id, analyse: false });
      onDone(Number(id));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Add to case"
      onClose={onClose}
      footer={
        <button className="btn" type="button" disabled={busy || !caseId || (caseId === "new" && !title.trim())} onClick={submit}>
          {busy ? "Adding…" : "Add wallet and analysis"}
        </button>
      }
    >
      <div className="field">
        <label htmlFor="case-sel">Case</label>
        <select id="case-sel" className="select" value={caseId} onChange={(e) => setCaseId(e.target.value)}>
          <option value="">{cases === null ? "Loading cases…" : "Choose a case"}</option>
          <option value="new">New case…</option>
          {(cases || []).map((c) => (
            <option key={c.id} value={c.id}>
              {c.case_no} · {c.title}
            </option>
          ))}
        </select>
      </div>
      {caseId === "new" ? (
        <div className="field">
          <label htmlFor="case-title">New case title</label>
          <input id="case-title" className="input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
      ) : null}
      {error ? <div className="form-error">{error.detail || error.message}</div> : null}
    </Modal>
  );
}

export function DraftRequest({ analysis, caseId, onClose }) {
  const router = useRouter();
  const [legal, setLegal] = useState(null);
  const [type, setType] = useState("INFORMATION_DISCLOSURE");
  const [basis, setBasis] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const nv = analysis.result?.summary?.nearest_vasp;

  useEffect(() => {
    api
      .get("/config/public")
      .then((r) => setLegal(r.legal))
      .catch((e) => setError(e));
  }, []);

  useEffect(() => {
    if (!legal) return;
    setBasis(legal[type]?.default || "");
  }, [legal, type]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.post(`/cases/${caseId}/requests`, { analysis_id: analysis.id, request_type: type, legal_basis_id: basis, notes });
      router.push(`/requests/${r.id}`);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  const options = legal?.[type]?.options || [];
  return (
    <Modal
      title="Draft lawful request"
      onClose={onClose}
      footer={
        <button className="btn" type="button" disabled={busy || !basis || !nv} onClick={submit}>
          {busy ? "Drafting…" : "Create draft"}
        </button>
      }
    >
      {nv ? (
        <p className="small">
          Routed to <b>{nv.entity_name}</b> (attribution confidence {nv.confidence.toFixed(0)}, {nv.tier}). A different supervisor must approve the
          draft before it is queued to SAHYOG <SimulatedTag />.
        </p>
      ) : (
        <div className="notice notice-warn">No VASP reached confidence 50, so there is no routable recipient for a request.</div>
      )}
      <div className="field">
        <label htmlFor="rtype">Request type</label>
        <select id="rtype" className="select" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="INFORMATION_DISCLOSURE">Information disclosure</option>
          <option value="FREEZE_HOLD">Freeze / hold</option>
        </select>
      </div>
      <div className="field">
        <label htmlFor="basis">Legal basis</label>
        <select id="basis" className="select" value={basis} onChange={(e) => setBasis(e.target.value)}>
          <option value="">{type === "FREEZE_HOLD" ? "Choose a legal basis (no default)" : "Choose a legal basis"}</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.label}
            </option>
          ))}
        </select>
        <span className="small muted">{legal?.disclaimer || "Legal provisions to be confirmed by the investigating officer."}</span>
      </div>
      <div className="field">
        <label htmlFor="notes">Notes for the VASP and the approver</label>
        <textarea id="notes" className="textarea" value={notes} onChange={(e) => setNotes(e.target.value)} />
      </div>
      {error ? <div className="form-error">{error.detail || error.message}</div> : null}
    </Modal>
  );
}

export default function Actions({ analysis, onChanged }) {
  const user = useUser();
  const [modal, setModal] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(null);
  const [linkedCase, setLinkedCase] = useState(analysis.case_id || null);

  async function openPdf() {
    setBusy("pdf");
    setMsg(null);
    try {
      const { blob, sha256 } = await api.file(`/analyses/${analysis.id}/report.pdf`, "report.pdf");
      const url = URL.createObjectURL(blob);
      window.open(url, "_blank", "noopener");
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      setMsg(`Report generated. SHA-256 ${sha256}`);
    } catch (e) {
      setMsg(e.detail || e.message);
    } finally {
      setBusy(null);
    }
  }

  async function downloadJson() {
    setBusy("json");
    setMsg(null);
    try {
      const { blob, sha256, filename } = await api.file(`/analyses/${analysis.id}/report.json`, "report.json");
      saveBlob(blob, filename);
      setMsg(`JSON export downloaded. SHA-256 ${sha256}`);
    } catch (e) {
      setMsg(e.detail || e.message);
    } finally {
      setBusy(null);
    }
  }

  async function watch(fromWindowStart) {
    setBusy("watch");
    setMsg(null);
    setModal(null);
    try {
      const chains = analysis.result?.suspect?.active_chains || [];
      const since = fromWindowStart ? analysis.result?.params?.window_start ?? null : null;
      await api.post("/watchlist", {
        address: analysis.address,
        chain: analysis.chain || chains[0] || null,
        analysis_id: analysis.id,
        case_id: linkedCase,
        note: "Added from analysis",
        since,
      });
      setMsg(
        fromWindowStart
          ? "Wallet added to the watchlist. Transfers since the start of the analysis window will raise alerts on the next check (Alerts page, or within 5 minutes)."
          : "Wallet added to the watchlist. New outgoing transfers create alerts (checked every 5 minutes).",
      );
    } catch (e) {
      setMsg(e.detail || e.message);
    } finally {
      setBusy(null);
    }
  }

  const canDraft = hasRole(user, "ANALYST", "ADMIN");
  return (
    <>
      <div className="row">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setModal("case")}>
          {linkedCase ? "Add to another case" : "Add to case"}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy === "pdf"} onClick={openPdf}>
          {busy === "pdf" ? "Generating…" : "Generate report"}
        </button>
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy === "json"} onClick={downloadJson}>
          JSON export
        </button>
        {canDraft ? (
          <button
            type="button"
            className="btn btn-sm"
            title={linkedCase ? "" : "Add the analysis to a case first"}
            onClick={() => setModal(linkedCase ? "request" : "case-then-request")}
          >
            Draft request
          </button>
        ) : null}
        <button type="button" className="btn btn-secondary btn-sm" disabled={busy === "watch"} onClick={() => setModal("watch")}>
          Watch wallet
        </button>
      </div>
      {msg ? <p className="small muted" style={{ overflowWrap: "anywhere" }}>{msg}</p> : null}
      {modal === "case" || modal === "case-then-request" ? (
        <AddToCase
          analysis={analysis}
          onClose={() => setModal(null)}
          onDone={(cid) => {
            const next = modal === "case-then-request" ? "request" : null;
            if (!linkedCase || next) setLinkedCase(cid);
            onChanged?.(cid);
            setModal(next);
            if (!next) setMsg("Added to the case.");
          }}
        />
      ) : null}
      {modal === "watch" ? (
        <Modal title="Watch wallet" onClose={() => setModal(null)}>
          <p className="small">Alert on outgoing transfers from this wallet. Alerts are urgent when the funds reach a labelled VASP within 2 hops.</p>
          <div className="stack-sm">
            <button type="button" className="btn" onClick={() => watch(false)}>
              New transfers from now on
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => watch(true)}>
              All transfers since the analysis window start
            </button>
          </div>
        </Modal>
      ) : null}
      {modal === "request" ? <DraftRequest analysis={analysis} caseId={linkedCase} onClose={() => setModal(null)} /> : null}
    </>
  );
}
