"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import AnalysisProgress from "@/components/AnalysisProgress";
import Actions from "@/components/analysis/Actions";
import Attribution, { explorerAddress } from "@/components/analysis/Attribution";
import RiskTab from "@/components/analysis/RiskTab";
import SourcesTab from "@/components/analysis/SourcesTab";
import FlowGraph from "@/components/FlowGraph";
import { Addr, ConfidenceTag, ErrorState, Loading, RiskBadge, Tabs, Tri, VerificationTag } from "@/components/ui";
import { api } from "@/lib/api";
import { chainName, hops, ist } from "@/lib/format";

const TABS = [
  ["attribution", "Attribution"],
  ["graph", "Graph"],
  ["risk", "Risk & typologies"],
  ["sources", "Sources & integrity"],
];

function Headline({ a, cfg }) {
  const r = a.result;
  const nv = r.summary.nearest_vasp;
  const ent = nv ? a.entities?.[String(nv.entity_id)] : null;
  return (
    <section className="card card-lg headline">
      {nv ? (
        <>
          <div className="headline-title">
            Nearest VASP: {nv.entity_name}
            <span className="sep">·</span>Confidence <span className="mono">{nv.confidence.toFixed(0)}</span>
            <span className="sep">·</span>
            {nv.tier}
            <span className="sep">·</span>
            {hops(nv.hops)}
            <span className="sep">·</span>
            via {nv.via} <Addr value={nv.target_address} href={explorerAddress(cfg, nv.chain, nv.target_address)} />
          </div>
          <div className="row">
            <ConfidenceTag tier={nv.tier} value={nv.confidence} />
            <span className="small muted">Likely deposit destination · attribution is an investigative lead, not proof of ownership</span>
          </div>
        </>
      ) : (
        <>
          <div className="headline-title">No VASP reached attribution confidence 50</div>
          <div className="small muted">
            {r.candidates.length ? `${r.candidates.length} lower-confidence candidate(s) are listed below; none is routable.` : "No candidate was found within the traced paths."}
          </div>
        </>
      )}
      <div className="headline-meta">
        <span>
          Suspect <Addr value={r.suspect.address} />
        </span>
        <span>
          Chains <b>{r.suspect.active_chains.map(chainName).join(", ") || "none active"}</b>
        </span>
        <span>
          Risk <RiskBadge level={r.risk.level} />
        </span>
        <span>
          Completed <b>{ist(a.finished_at)}</b>
        </span>
        {a.runtime_seconds !== null ? (
          <span>
            Runtime <b className="mono">{a.runtime_seconds}s</b>
          </span>
        ) : null}
        {r.summary.partial ? <span className="tag tint-medium">Partial result</span> : null}
        {r.data_mode === "replay" ? <span className="tag">Replay data</span> : null}
      </div>
      {ent ? (
        <div className="headline-meta">
          <span>
            Directory <VerificationTag status={ent.verification_status} />
          </span>
          <span>
            FIU-IND registered <b><Tri value={ent.fiu_ind_registered} /></b>
          </span>
          <span>
            SAHYOG onboarded <b><Tri value={ent.sahyog_onboarded} /></b>
          </span>
          <span>
            Request channel <b>{ent.le_request_channel || <span className="muted">Not recorded</span>}</b>
          </span>
        </div>
      ) : null}
      <div className="notice">
        <span>
          <b>Recommended action.</b> {r.summary.recommended_action}
        </span>
      </div>
    </section>
  );
}

export default function AnalysisPage() {
  const { id } = useParams();
  const [a, setA] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [error, setError] = useState(null);
  const [tab, setTab] = useState("attribution");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .get(`/analyses/${id}`)
      .then((r) => alive && setA(r))
      .catch((e) => alive && setError(e));
    return () => {
      alive = false;
    };
  }, [id, reload]);

  useEffect(() => {
    api
      .get("/config/public")
      .then(setCfg)
      .catch(() => setCfg(null));
  }, []);

  const onFinished = useCallback(() => setReload((x) => x + 1), []);

  if (error) return <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!a) return <Loading label="Loading analysis…" lines={4} />;

  const head = (
    <div className="page-head">
      <div>
        <h1>Wallet analysis</h1>
        <p className="sub">
          <span className="mono">{a.address}</span>
          {a.case_id ? (
            <>
              {" · "}
              <Link href={`/cases/${a.case_id}`}>Open case</Link>
            </>
          ) : null}
        </p>
      </div>
    </div>
  );

  if (a.status !== "done") {
    return (
      <>
        {head}
        <section className="card">
          <AnalysisProgress analysisId={a.id} onFinished={onFinished} />
        </section>
      </>
    );
  }

  return (
    <>
      {head}
      <Headline a={a} cfg={cfg} />
      <Actions analysis={a} onChanged={() => setReload((x) => x + 1)} />
      <Tabs tabs={TABS} value={tab} onChange={setTab} />
      {tab === "attribution" ? <Attribution result={a.result} entities={a.entities} cfg={cfg} /> : null}
      {tab === "graph" ? (
        <section className="card">
          <FlowGraph analysisId={a.id} />
        </section>
      ) : null}
      {tab === "risk" ? <RiskTab result={a.result} /> : null}
      {tab === "sources" ? <SourcesTab analysis={a} /> : null}
    </>
  );
}
