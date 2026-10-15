"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { Addr, Empty, ErrorState, Loading, TxLink } from "@/components/ui";
import { api } from "@/lib/api";
import { amount, chainName, ist, titleCase, usd } from "@/lib/format";

const C = {
  navy: "#0B1F3A",
  navy3: "#1E3A5F",
  blue: "#2563EB",
  blue2: "#3B82F6",
  tint: "#EEF3FA",
  border: "#E3E9F2",
  muted: "#5C6B7F",
  mixer: "#B42318",
  bridge: "#6941C6",
  unknown: "#98A2B3",
  severe: "#8B1E2D",
};

function cssVar(name, fallback) {
  if (typeof window === "undefined") return fallback;
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
}

function stylesheet(monoFont) {
  return [
    {
      selector: "node",
      style: {
        "background-color": C.unknown,
        label: "data(display)",
        "font-family": monoFont,
        "font-size": 9,
        color: C.navy,
        "text-valign": "bottom",
        "text-margin-y": 5,
        "text-background-color": "#FFFFFF",
        "text-background-opacity": 0.85,
        "text-background-padding": 2,
        width: 22,
        height: 22,
        "border-width": 0,
      },
    },
    { selector: "node.vasp", style: { "background-color": C.blue, width: 28, height: 28 } },
    {
      selector: "node.deposit_inferred, node.cluster_inferred, node.inferred",
      style: { "background-color": C.blue2, "border-width": 2, "border-style": "dashed", "border-color": C.navy },
    },
    { selector: "node.mixer", style: { "background-color": C.mixer } },
    { selector: "node.bridge", style: { "background-color": C.bridge } },
    { selector: "node.sanctioned", style: { "background-color": C.severe } },
    { selector: "node.risk", style: { "background-color": C.mixer } },
    { selector: "node.dex", style: { "background-color": C.navy3 } },
    { selector: "node.labelled", style: { "background-color": C.navy3 } },
    {
      selector: "node.high_activity",
      style: { "background-color": C.unknown, "border-width": 2, "border-style": "dashed", "border-color": C.muted },
    },
    {
      selector: "node.suspect",
      style: {
        "background-color": C.navy,
        width: 38,
        height: 38,
        "border-width": 5,
        "border-style": "solid",
        "border-color": "#C7D5EA",
        "font-weight": 700,
      },
    },
    {
      selector: "node.suspect.inferred",
      style: { "border-style": "dashed", "border-color": C.blue },
    },
    {
      selector: "node:selected",
      style: { "overlay-color": C.blue, "overlay-opacity": 0.12, "overlay-padding": 6 },
    },
    {
      selector: "edge",
      style: {
        width: "data(width)",
        "line-color": "#B8C4D6",
        "target-arrow-color": "#B8C4D6",
        "target-arrow-shape": "triangle",
        "arrow-scale": 0.8,
        "curve-style": "bezier",
        label: "data(label)",
        "font-family": monoFont,
        "font-size": 8,
        color: C.muted,
        "text-rotation": "autorotate",
        "text-background-color": "#FFFFFF",
        "text-background-opacity": 0.9,
        "text-background-padding": 1,
      },
    },
    { selector: "edge.sweep", style: { "line-color": C.blue, "target-arrow-color": C.blue } },
    { selector: "edge.funding", style: { "line-style": "dashed", "line-color": "#C9D3E1", "target-arrow-color": "#C9D3E1" } },
    { selector: "edge.gas", style: { "line-style": "dotted", width: 1, label: "" } },
    { selector: "edge:selected", style: { "line-color": C.navy, "target-arrow-color": C.navy } },
  ];
}

const LEGEND = [
  ["Suspect", { background: C.navy, borderColor: "#C7D5EA" }],
  ["VASP (labelled)", { background: C.blue }],
  ["Inferred deposit / cluster", { background: C.blue2, borderColor: C.navy, borderStyle: "dashed" }],
  ["Mixer", { background: C.mixer }],
  ["Bridge", { background: C.bridge }],
  ["Unlabelled", { background: C.unknown }],
];

export default function FlowGraph({ analysisId }) {
  const box = useRef(null);
  const cyRef = useRef(null);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null);
  const [reload, setReload] = useState(0);

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .get(`/analyses/${analysisId}/graph`)
      .then((r) => alive && setData(r))
      .catch((e) => alive && setError(e));
    return () => {
      alive = false;
    };
  }, [analysisId, reload]);

  useEffect(() => {
    if (!data || !box.current || !data.elements.length) return undefined;
    let cy;
    let cancelled = false;
    (async () => {
      const { default: cytoscape } = await import("cytoscape");
      if (cancelled || !box.current) return;
      const mono = cssVar("--font-mono", "ui-monospace, monospace");
      const roots = data.elements.filter((e) => e.group === "nodes" && e.data.role === "suspect").map((e) => `#${CSS.escape(e.data.id)}`);
      cy = cytoscape({
        container: box.current,
        elements: data.elements,
        style: stylesheet(mono),
        layout: {
          name: "breadthfirst",
          directed: true,
          roots: roots.length ? roots.join(",") : undefined,
          spacingFactor: 1.15,
          padding: 24,
          animate: false,
        },
        minZoom: 0.2,
        maxZoom: 1.6,
      });
      cy.fit(undefined, 32);
      cy.center();
      cy.on("tap", "node", (evt) => setSelected({ kind: "node", id: evt.target.id() }));
      cy.on("tap", "edge", (evt) => setSelected({ kind: "edge", id: evt.target.id() }));
      cy.on("tap", (evt) => {
        if (evt.target === cy) setSelected(null);
      });
      cyRef.current = cy;
      const suspect = data.elements.find((e) => e.group === "nodes" && e.data.role === "suspect");
      if (suspect) setSelected({ kind: "node", id: suspect.data.id });
    })();
    return () => {
      cancelled = true;
      if (cy) cy.destroy();
      cyRef.current = null;
    };
  }, [data]);

  const detail = useMemo(() => {
    if (!data || !selected) return null;
    if (selected.kind === "node") {
      const n = data.nodes[selected.id];
      if (!n) return null;
      const edges = Object.values(data.edges).filter((e) => e.source === n.id || e.target === n.id);
      return { kind: "node", n, edges };
    }
    const e = data.edges[selected.id];
    return e ? { kind: "edge", e } : null;
  }, [data, selected]);

  if (error) return <ErrorState error={error} onRetry={() => setReload((x) => x + 1)} />;
  if (!data) return <Loading label="Loading graph…" />;
  if (!data.elements.length) return <Empty title="No fund movements to draw">The suspect had no in-scope transfers in the window.</Empty>;

  return (
    <div className="stack">
      <div className="row-between">
        <div className="graph-legend">
          {LEGEND.map(([label, s]) => (
            <span key={label} className="legend-item">
              <span className="legend-swatch" style={{ background: s.background, borderColor: s.borderColor || "transparent", borderStyle: s.borderStyle || "solid" }} />
              {label}
            </span>
          ))}
        </div>
        <div className="row">
          <button type="button" className="btn btn-secondary btn-sm" onClick={() => {
              cyRef.current?.fit(undefined, 32);
              cyRef.current?.center();
            }}>
            Fit
          </button>
        </div>
      </div>
      <div className="graph-shell">
        <div className="graph-canvas" ref={box} aria-label="Fund-flow graph" />
        <aside className="card" style={{ maxHeight: 560, overflowY: "auto" }}>
          {detail ? <Detail detail={detail} /> : <p className="muted small">Select a node or an edge to see its labels, sources and transfers.</p>}
        </aside>
      </div>
      <p className="footer-note">Edge width grows with log(USD value). USD values are approximate, current price.</p>
    </div>
  );
}

function Detail({ detail }) {
  if (detail.kind === "edge") {
    const e = detail.e;
    return (
      <div className="stack">
        <h3>{titleCase(e.kind)} transfer</h3>
        <dl className="dl">
          <dt>From</dt>
          <dd>
            <Addr value={e.source.split(":").slice(1).join(":")} />
          </dd>
          <dt>To</dt>
          <dd>
            <Addr value={e.target.split(":").slice(1).join(":")} />
          </dd>
          <dt>Amount</dt>
          <dd className="mono">
            {amount(e.amount)} {e.asset}
          </dd>
          <dt>USD</dt>
          <dd className="mono">{usd(e.usd)}</dd>
          <dt>First</dt>
          <dd>{ist(e.first_time)}</dd>
          <dt>Transactions</dt>
          <dd className="stack-sm">
            {(e.tx_links || []).map((t) => (
              <TxLink key={t.tx_hash} hash={t.tx_hash} url={t.explorer_url} />
            ))}
          </dd>
        </dl>
      </div>
    );
  }
  const { n, edges } = detail;
  const out = edges.filter((e) => e.source === n.id);
  const inc = edges.filter((e) => e.target === n.id);
  return (
    <div className="stack">
      <div className="stack-sm">
        <span className="tag tag-mono">{titleCase(n.role)}</span>
        <Addr value={n.address} href={n.explorer_url} head={10} tail={8} />
        <span className="muted small">
          {chainName(n.chain)}
          {n.hop !== null && n.hop !== undefined ? ` · hop ${n.hop}` : ""}
          {n.direction ? ` · ${n.direction}` : ""}
        </span>
        {n.terminal_reason ? <span className="small">Stopped: {n.terminal_reason}</span> : null}
      </div>
      <div className="divider" />
      <h3>Labels</h3>
      {n.labels?.length ? (
        n.labels.map((l, i) => (
          <div key={i} className="card-flat stack-sm">
            <b>
              {l.entity_name} <span className="muted small">({l.entity_type})</span>
            </b>
            <span className="small">
              Source <span className="mono">{l.source}</span> · tier {l.tier} · reliability {Number(l.reliability).toFixed(2)}
            </span>
            <span className="small muted">Retrieved {ist(l.retrieved_at)}</span>
            {l.source_url ? (
              <a className="small" href={l.source_url} target="_blank" rel="noopener noreferrer">
                Source link
              </a>
            ) : null}
            {l.inherited_from ? <span className="small muted">Carried over from {l.inherited_from}</span> : null}
          </div>
        ))
      ) : (
        <p className="muted small">No label. Unlabelled addresses are never attributed without evidence.</p>
      )}
      {n.activity && Object.keys(n.activity).length ? (
        <>
          <h3>Activity in window</h3>
          <dl className="dl">
            <dt>Transactions</dt>
            <dd className="mono">{n.activity.tx_count_window ?? "—"}</dd>
            <dt>Counterparties</dt>
            <dd className="mono">{n.activity.counterparties_window ?? "—"}</dd>
          </dl>
        </>
      ) : null}
      <h3>Transfers</h3>
      {[...out.map((e) => ["Out", e]), ...inc.map((e) => ["In", e])].slice(0, 30).map(([dir, e]) => (
        <div key={`${dir}${e.id}`} className="small stack-sm" style={{ gap: 2 }}>
          <span>
            <b>{dir}</b> · <span className="mono">{amount(e.amount)} {e.asset}</span> · {titleCase(e.kind)} · {e.tx_count} tx
          </span>
          <TxLink hash={e.tx_hashes[0]} url={e.explorer_url} />
        </div>
      ))}
      {!out.length && !inc.length ? <p className="muted small">No drawn transfers.</p> : null}
    </div>
  );
}
