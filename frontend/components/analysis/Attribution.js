"use client";

import { useState } from "react";

import { Addr, ConfidenceTag, Empty, Factor, TxLink } from "@/components/ui";
import { amount, chainName, chainStatus, entityType, hops, ist, usd, utc } from "@/lib/format";

export function explorerAddress(cfg, chain, address) {
  const pattern = cfg?.chains?.[chain]?.explorer_address;
  return pattern ? pattern.replace("{address}", address) : undefined;
}

function PathView({ path, index, cfg }) {
  const f = path.factors;
  return (
    <div className="path">
      <div className="row-between">
        <div className="row">
          <b>Path {index + 1}</b>
          <span className="muted small">
            {hops(path.hops)} · via {path.via} · score <span className="mono">{path.score.toFixed(4)}</span>
          </span>
        </div>
        <span className="small muted mono">
          R {f.R.toFixed(2)} · H {f.H.toFixed(2)} · V {f.V.toFixed(4)} · T {f.T.toFixed(1)}
        </span>
      </div>
      <div className="small">
        Label reached: <span className="mono">{path.label.source}</span> (tier {path.label.tier}, reliability {Number(path.label.reliability).toFixed(2)}, retrieved{" "}
        {ist(path.label.retrieved_at)})
        {path.label.source_url ? (
          <>
            {" · "}
            <a href={path.label.source_url} target="_blank" rel="noopener noreferrer">
              source
            </a>
          </>
        ) : null}
      </div>
      {path.label.evidence ? <InferredEvidence ev={path.label.evidence} /> : null}
      {path.hops === 0 ? (
        <div className="small muted">
          Hop 0: the suspect address itself is the attributed address (
          <Addr value={path.nodes[0]} href={explorerAddress(cfg, path.chain, path.nodes[0])} />
          ).
        </div>
      ) : null}
      {path.hops_detail.map((h, i) => (
        <div key={i} className="hop">
          <span className="hop-n">{i + 1}</span>
          <div className="stack-sm">
            <div className="row small">
              <Addr value={h.from} href={explorerAddress(cfg, h.chain, h.from)} />
              <span className="muted">→</span>
              <Addr value={h.to} href={explorerAddress(cfg, h.chain, h.to)} />
              <span className="muted">
                {chainName(h.chain)} · share {(h.share * 100).toFixed(1)}%
              </span>
            </div>
            {h.transfers.map((t) => (
              <div key={t.tx_hash + t.amount} className="hop-tx small">
                <span className="mono" style={{ color: "var(--navy)" }}>
                  {amount(t.amount)} {t.asset}
                </span>
                <span className="mono">{usd(t.usd)}</span>
                <span title={utc(t.time)}>{t.time_ist}</span>
                <span className="muted">{t.time_utc}</span>
                <TxLink hash={t.tx_hash} url={t.explorer_url} />
              </div>
            ))}
            {h.transfer_count > h.transfers.length ? (
              <span className="muted small">+{h.transfer_count - h.transfers.length} more transfers on this hop</span>
            ) : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function InferredEvidence({ ev }) {
  const groups = [
    ["Deposit transactions", ev.deposit_txs],
    ["Sweep transactions", ev.sweep_txs],
    ["Gas top-ups from the exchange", ev.gas_topup_txs],
    ["Common-input transactions", ev.common_input_txs],
  ].filter(([, v]) => v && v.length);
  return (
    <div className="card-flat stack-sm small">
      <b>Inference evidence</b>
      {ev.corroborations ? <span>Corroborations: {ev.corroborations.length ? ev.corroborations.join(", ") : "none (sweep only)"}</span> : null}
      {ev.co_input_addresses ? <span>Co-spent with: {ev.co_input_addresses.length} exchange-labelled address(es)</span> : null}
      {groups.map(([label, txs]) => (
        <div key={label} className="row">
          <span className="muted">{label}:</span>
          {txs.slice(0, 6).map((h) => (
            <TxLink key={h} hash={h} />
          ))}
        </div>
      ))}
    </div>
  );
}

function CandidateCard({ c, cfg, rank, entity }) {
  const [open, setOpen] = useState(rank === 0);
  const f = c.best_factors;
  return (
    <div className="card stack">
      <div className="row-between">
        <div className="stack-sm" style={{ gap: 4 }}>
          <div className="row">
            <span className="muted mono small">#{rank + 1}</span>
            <h2>{c.entity_name}</h2>
            <span className="tag">{entityType(c.entity_type)}</span>
            {c.routable ? <span className="tag tint-blue">Routable</span> : null}
          </div>
          <span className="muted small">
            {hops(c.min_hops)} · {c.path_count} evidence path{c.path_count === 1 ? "" : "s"} · via {c.via} · label sources {c.label_sources.join(", ")}
          </span>
          {entity ? (
            <span className="muted small">
              Directory: {entity.verification_status} · SAHYOG onboarded {entity.sahyog_onboarded === null ? "unknown" : entity.sahyog_onboarded ? "yes" : "no"}
            </span>
          ) : null}
        </div>
        <ConfidenceTag tier={c.tier} value={c.confidence} />
      </div>
      <div className="grid grid-2">
        <div className="stack-sm">
          <span className="label">Factor breakdown (strongest path)</span>
          <Factor k="R" label="Reliability of the label reached" value={f.R} />
          <Factor k="H" label="Hop decay" value={f.H} />
          <Factor k="V" label="Value share reaching the entity" value={f.V} digits={4} />
          <Factor k="T" label="Timing factor" value={f.T} digits={1} />
          <span className="small muted">
            path score = R × H × √V × T = <span className="mono">{c.best_score.toFixed(4)}</span>; confidence = 1 − Π(1 − path score) ={" "}
            <span className="mono">{c.confidence.toFixed(1)}</span>
          </span>
        </div>
        <div className="card-flat stack-sm">
          <span className="label">What would raise this?</span>
          <span className="small">{c.raise_note}</span>
        </div>
      </div>
      <div className="row">
        <button type="button" className="btn btn-secondary btn-sm" onClick={() => setOpen((x) => !x)}>
          {open ? "Hide" : "Show"} evidence paths ({c.paths.length})
        </button>
      </div>
      {open ? (
        <div className="stack">
          {c.paths.map((p, i) => (
            <PathView key={i} path={p} index={i} cfg={cfg} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

export default function Attribution({ result, entities, cfg }) {
  const cands = result.candidates || [];
  const top = cands.slice(0, 3);
  const rest = cands.slice(3);
  return (
    <div className="stack">
      {top.length ? (
        top.map((c, i) => <CandidateCard key={c.entity_id} c={c} rank={i} cfg={cfg} entity={entities?.[String(c.entity_id)]} />)
      ) : (
        <div className="card">
          <Empty title="No attribution candidates">
            No labelled VASP, mixer or bridge was reached within {result.params?.max_hops} hops, and no deposit address could be inferred. This is an
            honest no-match, not an error.
          </Empty>
        </div>
      )}

      {rest.length ? (
        <div className="card">
          <div className="card-head">
            <h2>Other candidates</h2>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Entity</th>
                  <th>Type</th>
                  <th className="num">Confidence</th>
                  <th>Tier</th>
                  <th className="num">Hops</th>
                  <th className="num">Paths</th>
                </tr>
              </thead>
              <tbody>
                {rest.map((c) => (
                  <tr key={c.entity_id}>
                    <td>{c.entity_name}</td>
                    <td>{entityType(c.entity_type)}</td>
                    <td className="num">{c.confidence.toFixed(1)}</td>
                    <td>
                      <ConfidenceTag tier={c.tier} />
                    </td>
                    <td className="num">{c.min_hops}</td>
                    <td className="num">{c.path_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Funding-source VASPs</h2>
            <span className="hint">Exchange withdrawals that funded the suspect (2 hops back)</span>
          </div>
          {result.funding_sources?.length ? (
            <table className="table">
              <tbody>
                {result.funding_sources.slice(0, 6).map((c) => (
                  <tr key={c.entity_id}>
                    <td>
                      {c.entity_name} <span className="muted small">{entityType(c.entity_type)}</span>
                    </td>
                    <td className="num">{hops(c.min_hops)}</td>
                    <td className="right">
                      <ConfidenceTag tier={c.tier} value={c.confidence} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted small">{result.params?.include_funding ? "None found." : "Not requested for this analysis."}</p>
          )}
        </div>
        <div className="card">
          <div className="card-head">
            <h2>Inferred deposit addresses</h2>
            <span className="hint">Tier C, with evidence hashes</span>
          </div>
          {result.deposit_inferences?.length || result.cluster_inferences?.length ? (
            <div className="stack">
              {(result.deposit_inferences || []).map((d) => (
                <div key={d.deposit_address} className="stack-sm small">
                  <div className="row">
                    <Addr value={d.deposit_address} href={explorerAddress(cfg, d.chain, d.deposit_address)} />
                    <span>→ {d.entity_name}</span>
                    <span className="tag tag-mono">{Number(d.reliability).toFixed(2)}</span>
                  </div>
                  <span className="muted">
                    {d.deposited_amount} {d.asset} deposited, {d.swept_amount} swept · corroborations: {d.corroborations.join(", ") || "none"}
                  </span>
                </div>
              ))}
              {(result.cluster_inferences || []).map((d) => (
                <div key={d.address} className="stack-sm small">
                  <div className="row">
                    <Addr value={d.address} href={explorerAddress(cfg, d.chain, d.address)} />
                    <span>→ {d.entity_name} (common-input cluster)</span>
                    <span className="tag tag-mono">{Number(d.reliability).toFixed(2)}</span>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted small">No deposit address was inferred.</p>
          )}
          {result.linked_wallets?.length ? (
            <div className="stack-sm" style={{ marginTop: 16 }}>
              <span className="label">Linked wallets (shared exchange deposit address)</span>
              <div className="pill-list">
                {result.linked_wallets.slice(0, 20).map((w) => (
                  <Addr key={w} value={w} />
                ))}
              </div>
            </div>
          ) : null}
        </div>
      </div>

      <div className="grid grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Cross-chain mapping</h2>
          </div>
          <table className="table">
            <tbody>
              {(result.suspect?.chains_checked || []).map((c) => (
                <tr key={c.chain}>
                  <td>{chainName(c.chain)}</td>
                  <td>
                    <span className={`tag ${c.status === "active" ? "tint-blue" : ""}`}>{chainStatus(c.status)}</span>
                  </td>
                  <td className="num">{c.transfers || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {result.cross_chain?.same_key_holder_note ? <p className="small" style={{ marginTop: 12 }}>{result.cross_chain.same_key_holder_note}</p> : null}
        </div>
        <div className="card">
          <div className="card-head">
            <h2>Breakpoints and unlabelled services</h2>
          </div>
          {result.cross_chain?.breakpoints?.length ? (
            result.cross_chain.breakpoints.map((b) => (
              <div key={b.address} className="stack-sm small" style={{ marginBottom: 12 }}>
                <b>{b.bridge}</b>
                <span>
                  <Addr value={b.address} /> · hop {b.hop} · {Object.entries(b.amounts).map(([a, v]) => `${amount(v)} ${a}`).join(", ")} · {ist(b.first_time)}
                </span>
                <span className="muted">{b.recommendation}</span>
              </div>
            ))
          ) : (
            <p className="muted small">No decentralised bridge on the traced paths.</p>
          )}
          {result.possible_unlabelled_services?.length ? (
            <div className="stack-sm small" style={{ marginTop: 12 }}>
              <span className="label">Unlabelled high-activity services (possible VASP)</span>
              {result.possible_unlabelled_services.map((s) => (
                <span key={s.address}>
                  <Addr value={s.address} href={explorerAddress(cfg, s.chain, s.address)} /> · hop {s.hop} · {(s.value_share * 100).toFixed(1)}% of value ·{" "}
                  {s.activity?.tx_count_estimate} txs
                </span>
              ))}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}
