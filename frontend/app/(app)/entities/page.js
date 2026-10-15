"use client";

import { useEffect, useState } from "react";

import { useUser } from "@/components/AppShell";
import { Card, Empty, ErrorState, Loading, Modal, SimulatedTag, Tri, VerificationTag } from "@/components/ui";
import { api } from "@/lib/api";
import { hasRole } from "@/lib/auth";
import { entityType, ist } from "@/lib/format";

function triValue(v) {
  return v === true ? "true" : v === false ? "false" : "";
}

function EditEntity({ entity, onClose, onDone }) {
  const [f, setF] = useState({
    jurisdiction: entity.jurisdiction || "",
    fiu: triValue(entity.fiu_ind_registered),
    sahyog: triValue(entity.sahyog_onboarded),
    channel: entity.le_request_channel || "",
    status: entity.verification_status,
    url: entity.verified_source_url || "",
  });
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });

  async function submit() {
    setBusy(true);
    setError(null);
    const body = { verification_status: f.status, verified_source_url: f.url.trim() || null, clear: [] };
    const tri = (v) => (v === "true" ? true : v === "false" ? false : null);
    for (const [key, val] of [
      ["fiu_ind_registered", tri(f.fiu)],
      ["sahyog_onboarded", tri(f.sahyog)],
    ]) {
      if (val === null) body.clear.push(key);
      else body[key] = val;
    }
    if (f.channel.trim()) body.le_request_channel = f.channel.trim();
    else body.clear.push("le_request_channel");
    if (f.jurisdiction.trim()) body.jurisdiction = f.jurisdiction.trim();
    else body.clear.push("jurisdiction");
    try {
      onDone(await api.patch(`/entities/${entity.id}`, body));
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  return (
    <Modal
      title={`Verify ${entity.name}`}
      onClose={onClose}
      footer={
        <button className="btn" type="button" disabled={busy} onClick={submit}>
          {busy ? "Saving…" : "Save"}
        </button>
      }
    >
      <div className="notice notice-warn small">
        Record only facts you have checked in an official source, and give its URL. Never enter contact details or registrations from memory.
      </div>
      <div className="grid grid-2">
        <div className="field">
          <label htmlFor="fiu">FIU-IND registered</label>
          <select id="fiu" className="select" value={f.fiu} onChange={set("fiu")}>
            <option value="">Unknown</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="sah">SAHYOG onboarded</label>
          <select id="sah" className="select" value={f.sahyog} onChange={set("sahyog")}>
            <option value="">Unknown</option>
            <option value="true">Yes</option>
            <option value="false">No</option>
          </select>
        </div>
        <div className="field">
          <label htmlFor="jur">Jurisdiction</label>
          <input id="jur" className="input" value={f.jurisdiction} onChange={set("jurisdiction")} />
        </div>
        <div className="field">
          <label htmlFor="vs">Verification status</label>
          <select id="vs" className="select" value={f.status} onChange={set("status")}>
            <option value="unverified">Unverified</option>
            <option value="verified">Verified</option>
            <option value="disputed">Disputed</option>
          </select>
        </div>
      </div>
      <div className="field">
        <label htmlFor="ch">Law-enforcement request channel</label>
        <input id="ch" className="input" value={f.channel} onChange={set("channel")} placeholder="e.g. the VASP's published LE portal URL" />
      </div>
      <div className="field">
        <label htmlFor="url">Source URL (required for any recorded fact)</label>
        <input id="url" className="input" value={f.url} onChange={set("url")} placeholder="https://" />
      </div>
      {error ? <div className="form-error">{error.detail || error.message}</div> : null}
    </Modal>
  );
}

export default function EntitiesPage() {
  const user = useUser();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const [editing, setEditing] = useState(null);
  const [reload, setReload] = useState(0);
  const isAdmin = hasRole(user, "ADMIN");

  useEffect(() => {
    const params = new URLSearchParams({ limit: "200" });
    if (q.trim()) params.set("q", q.trim());
    if (status) params.set("verification_status", status);
    const t = setTimeout(() => {
      api
        .get(`/entities?${params}`)
        .then((r) => {
          setData(r);
          setError(null);
        })
        .catch(setError);
    }, 200);
    return () => clearTimeout(t);
  }, [q, status, reload]);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>VASP directory</h1>
          <p className="sub">
            Exchanges and services found in the label sources. FIU-IND registration, SAHYOG onboarding <SimulatedTag /> and request channels stay
            unknown until an ADMIN verifies them with a source.
          </p>
        </div>
      </div>
      <Card>
        <div className="row" style={{ marginBottom: 16 }}>
          <input className="input" style={{ maxWidth: 320 }} placeholder="Search by name" value={q} onChange={(e) => setQ(e.target.value)} />
          <select className="select" style={{ maxWidth: 200 }} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            <option value="unverified">Unverified</option>
            <option value="verified">Verified</option>
            <option value="disputed">Disputed</option>
          </select>
          {data ? <span className="muted small">{data.total.toLocaleString("en-IN")} entities</span> : null}
        </div>
        {error ? (
          <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />
        ) : !data ? (
          <Loading />
        ) : data.items.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Type</th>
                  <th className="num">Labels</th>
                  <th>FIU-IND</th>
                  <th>SAHYOG</th>
                  <th>Request channel</th>
                  <th>Status</th>
                  {isAdmin ? <th /> : null}
                </tr>
              </thead>
              <tbody>
                {data.items.map((e) => (
                  <tr key={e.id}>
                    <td>
                      {e.name}
                      {e.verified_source_url ? (
                        <div className="small">
                          <a href={e.verified_source_url} target="_blank" rel="noopener noreferrer">
                            source
                          </a>{" "}
                          <span className="muted">· {ist(e.last_verified_at)}</span>
                        </div>
                      ) : null}
                    </td>
                    <td>{entityType(e.entity_type)}</td>
                    <td className="num">{e.label_count?.toLocaleString("en-IN")}</td>
                    <td>
                      <Tri value={e.fiu_ind_registered} />
                    </td>
                    <td>
                      <Tri value={e.sahyog_onboarded} />
                    </td>
                    <td className="small" style={{ maxWidth: 220, overflowWrap: "anywhere" }}>
                      {e.le_request_channel || <span className="muted">Not recorded</span>}
                    </td>
                    <td>
                      <VerificationTag status={e.verification_status} />
                    </td>
                    {isAdmin ? (
                      <td className="right">
                        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setEditing(e)}>
                          Edit
                        </button>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty title="No entities match">Load the label seed with scripts/seed_users.py or run scripts/ingest_labels.py.</Empty>
        )}
      </Card>
      {editing ? (
        <EditEntity
          entity={editing}
          onClose={() => setEditing(null)}
          onDone={() => {
            setEditing(null);
            setReload((x) => x + 1);
          }}
        />
      ) : null}
    </>
  );
}
