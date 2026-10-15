"use client";

import { useEffect, useState } from "react";

import { Card, ErrorState, Loading } from "@/components/ui";
import { api } from "@/lib/api";
import { ist } from "@/lib/format";

function Metric({ label, value, note }) {
  return (
    <div className="card kpi">
      <span className="kpi-label">{label}</span>
      <span className="kpi-value">{value}</span>
      {note ? <span className="kpi-note">{note}</span> : null}
    </div>
  );
}

function pctOrDash(v) {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`;
}

export default function MethodologyPage() {
  const [m, setM] = useState(null);
  const [error, setError] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    setError(null);
    api
      .get("/methodology")
      .then(setM)
      .catch(setError);
  }, [reload]);

  if (error) return <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!m) return <Loading label="Loading methodology…" lines={5} />;

  const s = m.scoring;
  const ev = m.evaluation;
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Methodology</h1>
          <p className="sub">How attribution confidence is computed, where labels come from, and what the system cannot see.</p>
        </div>
        <span className="tag tag-mono">v{m.methodology_version}</span>
      </div>

      <Card title="Scoring" hint={`config/scoring.yaml · SHA-256 ${m.scoring_config_sha256.slice(0, 12)}…`}>
        <div className="stack">
          <div className="card-flat mono" style={{ fontSize: 13 }}>
            path_score(p) = R × H(hops) × √V × T &nbsp;&nbsp;·&nbsp;&nbsp; C(E) = 1 − Π (1 − path_score(p))
          </div>
          <div className="grid grid-2">
            <dl className="dl">
              <dt>R</dt>
              <dd>
                Reliability of the label reached: tier A {s.reliability_tiers.A}, B {s.reliability_tiers.B}, C {s.reliability_tiers.C_min}–
                {s.reliability_tiers.C_max}, D {s.reliability_tiers.D}
              </dd>
              <dt>H</dt>
              <dd className="mono">
                {Object.entries(s.hop_decay)
                  .map(([k, v]) => `${k}:${Number(v).toFixed(2)}`)
                  .join("  ")}
              </dd>
              <dt>V</dt>
              <dd>Proportional (haircut) share of the suspect&apos;s outflow reaching the entity along the path</dd>
              <dt>T</dt>
              <dd>
                {s.timing.fast_factor} if every hop is within {s.timing.fast_hours} h of the previous; {s.timing.medium_factor} within{" "}
                {s.timing.medium_days} days; otherwise {s.timing.slow_factor}
              </dd>
            </dl>
            <dl className="dl">
              <dt>Tiers</dt>
              <dd>
                High ≥ {s.confidence_tiers.high} · Medium {s.confidence_tiers.medium}–{s.confidence_tiers.high - 1} · Low {s.confidence_tiers.low}–
                {s.confidence_tiers.medium - 1} · Weak &lt; {s.confidence_tiers.low}
              </dd>
              <dt>Nearest VASP</dt>
              <dd>The VASP candidate with the fewest hops among those with confidence ≥ {s.routable_min_confidence}; ties go to higher confidence. Weak candidates are never routable.</dd>
              <dt>Traversal</dt>
              <dd>
                {s.traversal.default_max_hops} hops forward (max {s.traversal.max_hops_limit}), {s.traversal.backward_hops} back; {s.traversal.window_days}-day window; top{" "}
                {s.traversal.top_counterparties} counterparties plus any ≥ {s.traversal.min_outflow_share * 100}% of outflow; dust &lt; ${s.traversal.dust_usd}; budget{" "}
                {s.traversal.node_budget} nodes / {s.traversal.api_call_budget} API calls
              </dd>
            </dl>
          </div>
        </div>
      </Card>

      <Card title="Heuristics and their sources">
        <div className="stack">
          {m.heuristics.map((h) => (
            <div key={h.name} className="path">
              <div className="row-between">
                <b>{h.name}</b>
                <span className="muted small">{h.applies_to}</span>
              </div>
              <span className="small">{h.rule}</span>
              <span className="small muted">{h.corroboration}</span>
              <span className="small">
                Reliability: <span className="mono">{h.reliability}</span>
              </span>
              <span className="small muted">Source: {h.source}</span>
            </div>
          ))}
        </div>
      </Card>

      <div className="grid grid-2">
        <Card title="Label coverage" hint="Counts in this deployment">
          <table className="table">
            <thead>
              <tr>
                <th>Source</th>
                <th>Tier</th>
                <th className="num">Labels</th>
              </tr>
            </thead>
            <tbody>
              {m.labels_by_source.map((x) => (
                <tr key={x.source + x.tier}>
                  <td className="mono">{x.source}</td>
                  <td>{x.tier}</td>
                  <td className="num">{x.labels.toLocaleString("en-IN")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Limitations">
          <ul className="stack-sm small" style={{ margin: 0, paddingLeft: 18 }}>
            {m.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Card>
      </div>

      <Card title="Measured evaluation" hint={ev ? `Run ${ist(ev.generated_at)} · ${ev.data_mode} data` : "eval/run_eval.py has not been run on this deployment"}>
        {ev ? (
          <div className="stack">
            <div className="grid grid-kpi">
              <Metric label="Top-1 accuracy" value={pctOrDash(ev.metrics.top1_accuracy)} note={`${ev.metrics.positives} positive cases`} />
              <Metric label="Top-3 accuracy" value={pctOrDash(ev.metrics.top3_accuracy)} />
              <Metric label="Precision at High" value={pctOrDash(ev.metrics.precision_at_high)} note={`${ev.metrics.high_count} High results`} />
              <Metric label="Coverage (≥ 50)" value={pctOrDash(ev.metrics.coverage)} />
              <Metric label="False positives" value={pctOrDash(ev.metrics.false_positive_rate)} note={`${ev.metrics.negatives} negative controls`} />
              <Metric label="Runtime p50 / p95" value={`${ev.metrics.runtime_p50 ?? "—"}s`} note={`p95 ${ev.metrics.runtime_p95 ?? "—"}s`} />
            </div>
            <p className="footer-note">Numbers are reported exactly as measured by eval/run_eval.py. {ev.note || ""}</p>
          </div>
        ) : (
          <p className="small muted">
            No evaluation results yet. With API keys set, run <span className="mono">python scripts/build_ground_truth.py</span> then{" "}
            <span className="mono">python eval/run_eval.py</span> in backend/. The results appear here and in docs/EVALUATION.md.
          </p>
        )}
      </Card>
    </>
  );
}
