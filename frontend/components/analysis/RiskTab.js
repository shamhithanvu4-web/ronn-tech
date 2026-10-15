"use client";

import { Addr, RiskBadge, RiskTint, TxLink } from "@/components/ui";
import { titleCase } from "@/lib/format";

export default function RiskTab({ result }) {
  const risk = result.risk || { level: "Low", reasons: [] };
  const typ = result.typologies || [];
  const poison = result.address_poisoning_candidates || [];
  return (
    <div className="stack">
      <div className="card">
        <div className="card-head">
          <h2>Wallet risk</h2>
          <RiskBadge level={risk.level} />
        </div>
        <div className="stack">
          {risk.reasons.map((r, i) => (
            <div key={i} className="stack-sm">
              <div className="row">
                <RiskTint level={r.level} />
                <span>{r.reason}</span>
              </div>
              <div className="row small muted">
                {r.address ? <Addr value={r.address} /> : null}
                {r.source ? (
                  <span>
                    Source {r.source_url ? <a href={r.source_url} target="_blank" rel="noopener noreferrer">{r.source}</a> : r.source}
                  </span>
                ) : null}
                {(r.evidence_txs || []).slice(0, 4).map((h) => (
                  <TxLink key={h} hash={h} />
                ))}
              </div>
            </div>
          ))}
        </div>
        <p className="footer-note" style={{ marginTop: 16 }}>
          Severe: direct sanctioned exposure. High: direct mixer, darknet, ransomware or scam exposure. Medium: indirect exposure within 2 hops with at
          least 10% value share, or two or more typologies. Low: everything else.
        </p>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Laundering typologies</h2>
          <span className="hint">Rule-based, each with its evidence transactions</span>
        </div>
        {typ.length ? (
          <div className="stack">
            {typ.map((t) => (
              <div key={t.code} className="path">
                <div className="row-between">
                  <div className="row">
                    <b>{t.title}</b>
                    <span className="tag tag-mono">{t.code}</span>
                  </div>
                  <span className="muted small">{t.nodes.length} address(es)</span>
                </div>
                <span className="small">{t.description}</span>
                <div className="row small">
                  <span className="muted">Evidence:</span>
                  {t.evidence_txs.slice(0, 8).map((h) => (
                    <TxLink key={h} hash={h} />
                  ))}
                  {t.evidence_txs.length > 8 ? <span className="muted">+{t.evidence_txs.length - 8} more</span> : null}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted small">No typology matched within the traced paths.</p>
        )}
      </div>

      {poison.length ? (
        <div className="card">
          <div className="card-head">
            <h2>Possible address-poisoning transfers</h2>
            <span className="hint">Zero-value transfers or tokens with a stablecoin symbol but an unknown contract; never followed</span>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>From</th>
                  <th>To</th>
                  <th>Asset</th>
                  <th>Reason</th>
                  <th>Tx</th>
                </tr>
              </thead>
              <tbody>
                {poison.slice(0, 25).map((p) => (
                  <tr key={p.tx_hash + p.from}>
                    <td>
                      <Addr value={p.from} />
                    </td>
                    <td>
                      <Addr value={p.to} />
                    </td>
                    <td className="mono">{p.asset}</td>
                    <td className="small">{titleCase(p.reason)}</td>
                    <td>
                      <TxLink hash={p.tx_hash} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}
