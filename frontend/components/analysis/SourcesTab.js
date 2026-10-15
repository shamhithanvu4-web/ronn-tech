"use client";

import { useState } from "react";

import ReportVerify from "@/components/ReportVerify";
import { Addr } from "@/components/ui";
import { chainName, ist, titleCase } from "@/lib/format";

export default function SourcesTab({ analysis }) {
  const r = analysis.result;
  const raws = r.evidence?.raw_responses || [];
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? raws : raws.slice(0, 15);
  return (
    <div className="stack">
      <div className="grid grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Integrity</h2>
          </div>
          <dl className="dl">
            <dt>Analysis SHA-256</dt>
            <dd>
              <Addr value={analysis.result_sha256} head={16} tail={8} />
            </dd>
            <dt>Hash check</dt>
            <dd>{analysis.result_hash_valid ? "Stored result matches its SHA-256" : <span className="form-error">Stored result does not match its hash</span>}</dd>
            <dt>Algorithm</dt>
            <dd className="small">SHA-256 over canonical JSON (sorted keys, compact separators, UTF-8)</dd>
            <dt>Scoring config</dt>
            <dd>
              <Addr value={r.scoring_config_sha256} head={12} tail={6} />
            </dd>
            <dt>Methodology</dt>
            <dd className="mono">v{r.methodology_version}</dd>
            <dt>Data mode</dt>
            <dd className="mono">{r.data_mode}</dd>
            <dt>Generated</dt>
            <dd>{ist(r.generated_at)}</dd>
          </dl>
        </div>
        <div className="card">
          <div className="card-head">
            <h2>Verify a report</h2>
            <span className="hint">Upload a PDF or JSON export to recompute its hashes</span>
          </div>
          <ReportVerify />
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Label sources</h2>
          <span className="hint">Every label carries a named source and retrieval time</span>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Tier</th>
                <th>Retrieved</th>
                <th>Link</th>
              </tr>
            </thead>
            <tbody>
              {(r.evidence?.label_sources || []).map((s) => (
                <tr key={`${s.source}${s.source_url}`}>
                  <td className="mono">{s.source}</td>
                  <td>{s.tier}</td>
                  <td>{ist(s.retrieved_at)}</td>
                  <td className="small">
                    {s.source_url ? (
                      <a href={s.source_url} target="_blank" rel="noopener noreferrer">
                        {s.source_url.replace(/^https?:\/\//, "").slice(0, 60)}
                      </a>
                    ) : (
                      <span className="muted">Runtime inference (evidence hashes in the path)</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Raw provider responses</h2>
          <span className="hint">{raws.length} responses, each stored with its SHA-256 and retrieval time</span>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Provider</th>
                <th>Chain</th>
                <th>Request</th>
                <th>SHA-256</th>
                <th>Retrieved</th>
                <th>Origin</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((e) => (
                <tr key={e.content_sha256 + e.url}>
                  <td className="mono small">{e.provider}</td>
                  <td>{chainName(e.chain)}</td>
                  <td className="mono small" style={{ maxWidth: 360, overflowWrap: "anywhere" }}>
                    {e.url}
                  </td>
                  <td>
                    <Addr value={e.content_sha256} head={10} tail={6} />
                  </td>
                  <td className="small">{ist(e.retrieved_at)}</td>
                  <td className="small">{titleCase(e.origin)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {raws.length > 15 ? (
          <button type="button" className="btn btn-secondary btn-sm" style={{ marginTop: 12 }} onClick={() => setShowAll((x) => !x)}>
            {showAll ? "Show fewer" : `Show all ${raws.length}`}
          </button>
        ) : null}
        <p className="footer-note" style={{ marginTop: 12 }}>
          API keys are stripped from stored request URLs. USD values are approximate, current price.
        </p>
      </div>

      {r.errors?.length || r.summary?.partial ? (
        <div className="card">
          <div className="card-head">
            <h2>Limits reached</h2>
          </div>
          {r.summary.partial_reasons?.map((p) => (
            <p key={p} className="small">
              {p}
            </p>
          ))}
          {r.errors?.map((e, i) => (
            <p key={i} className="small mono">
              {e.node}: {e.code} {e.detail}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}
