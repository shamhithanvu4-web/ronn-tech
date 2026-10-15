"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";

import AnalysisProgress from "@/components/AnalysisProgress";
import { Card } from "@/components/ui";
import { api } from "@/lib/api";
import { CHAIN_OPTIONS, detectChain } from "@/lib/chain";

function AnalyzeForm() {
  const router = useRouter();
  const params = useSearchParams();
  const [address, setAddress] = useState(params.get("address") || "");
  const [chain, setChain] = useState(params.get("chain") || "");
  const [maxHops, setMaxHops] = useState(3);
  const [includeFunding, setIncludeFunding] = useState(true);
  const [caseId, setCaseId] = useState(params.get("case") || "");
  const [cases, setCases] = useState([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [running, setRunning] = useState(null);

  const det = detectChain(address);

  useEffect(() => {
    api
      .get("/cases?open=true&limit=100")
      .then((r) => setCases(r.items || []))
      .catch(() => setCases([]));
  }, []);

  const onFinished = useCallback(
    (a) => {
      if (a.status === "done") router.push(`/analyses/${a.id}`);
      else setBusy(false);
    },
    [router],
  );

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await api.post("/analyses", {
        address: address.trim(),
        chain: chain || null,
        case_id: caseId ? Number(caseId) : null,
        max_hops: Number(maxHops),
        include_funding: includeFunding,
      });
      setRunning(r.id);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Analyze a wallet</h1>
          <p className="sub">Trace where the funds went and find the nearest VASP that received deposits.</p>
        </div>
      </div>
      <Card lg>
        <form className="stack" onSubmit={submit}>
          <div className="field">
            <label htmlFor="addr">Suspect wallet address</label>
            <input
              id="addr"
              className="input input-lg"
              placeholder="0x…, T…, bc1…, 1…, 3… or a Solana address"
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              spellCheck={false}
              autoComplete="off"
              disabled={busy}
            />
            <div className="row" style={{ minHeight: 26 }}>
              {address.trim() ? (
                <span className={`chip ${det.ok ? "chip-ok" : "chip-bad"}`} aria-live="polite">
                  {det.ok ? "Detected: " : ""}
                  {det.label}
                </span>
              ) : (
                <span className="muted small">The chain is detected as you type. Checksums are verified by the server.</span>
              )}
            </div>
          </div>
          <div className="grid grid-3">
            <div className="field">
              <label htmlFor="chain">Chain</label>
              <select id="chain" className="select" value={chain} onChange={(e) => setChain(e.target.value)} disabled={busy}>
                {CHAIN_OPTIONS.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="hops">Max hops (1–5)</label>
              <select id="hops" className="select" value={maxHops} onChange={(e) => setMaxHops(e.target.value)} disabled={busy}>
                {[1, 2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}
                    {n === 3 ? " (default)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <label htmlFor="case">Case (optional)</label>
              <select id="case" className="select" value={caseId} onChange={(e) => setCaseId(e.target.value)} disabled={busy}>
                <option value="">Not linked to a case</option>
                {cases.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.case_no} · {c.title}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <label className="check">
            <input type="checkbox" checked={includeFunding} onChange={(e) => setIncludeFunding(e.target.checked)} disabled={busy} />
            Include funding sources (exchange withdrawals that funded the suspect, 2 hops back)
          </label>
          {error ? (
            <div className="notice notice-danger" role="alert">
              <div>
                {error.detail || error.message}
                {error.code ? <span className="mono small"> · {error.code}</span> : null}
              </div>
            </div>
          ) : null}
          <div className="row">
            <button className="btn" type="submit" disabled={busy || !address.trim()}>
              {busy ? "Analysing…" : "Start analysis"}
            </button>
            <span className="muted small">Typical runtime is under a few minutes; free-tier API limits apply.</span>
          </div>
        </form>
      </Card>
      {running ? (
        <Card title="Progress" hint="Updated every 1.5 seconds">
          <AnalysisProgress analysisId={running} onFinished={onFinished} />
        </Card>
      ) : null}
    </>
  );
}

export default function AnalyzePage() {
  return (
    <Suspense fallback={<div className="card muted">Loading…</div>}>
      <AnalyzeForm />
    </Suspense>
  );
}
