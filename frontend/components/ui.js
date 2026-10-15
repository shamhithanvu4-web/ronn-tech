"use client";

import { useEffect, useState } from "react";

import { truncate } from "@/lib/format";

export function CopyButton({ value, label = "Copy" }) {
  const [done, setDone] = useState(false);
  useEffect(() => {
    if (!done) return undefined;
    const t = setTimeout(() => setDone(false), 1200);
    return () => clearTimeout(t);
  }, [done]);
  async function copy(e) {
    e.preventDefault();
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      setDone(true);
    } catch {
      setDone(false);
    }
  }
  return (
    <button type="button" className="icon-btn" onClick={copy} title={done ? "Copied" : `${label} ${value}`} aria-label={label}>
      {done ? (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M3 8.5l3 3 7-7" />
        </svg>
      ) : (
        <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4">
          <rect x="5" y="5" width="8.5" height="8.5" rx="1.5" />
          <path d="M10.5 5V3.5A1.5 1.5 0 0 0 9 2H3.5A1.5 1.5 0 0 0 2 3.5V9a1.5 1.5 0 0 0 1.5 1.5H5" />
        </svg>
      )}
    </button>
  );
}

/** Address or hash: truncated 0x1234…abcd, full value on hover, copy button, optional explorer link. */
export function Addr({ value, href, head = 6, tail = 4, full = false, copy = true }) {
  if (!value) return <span className="muted">—</span>;
  const text = full ? value : truncate(value, head, tail);
  return (
    <span className="addr-wrap">
      {href ? (
        <a className="addr" href={href} target="_blank" rel="noopener noreferrer" title={value}>
          {text}
        </a>
      ) : (
        <span className="addr" title={value}>
          {text}
        </span>
      )}
      {copy ? <CopyButton value={value} /> : null}
    </span>
  );
}

export function TxLink({ hash, url }) {
  return <Addr value={hash} href={url || undefined} head={8} tail={6} />;
}

export function ConfidenceTag({ tier, value }) {
  const cls = { High: "conf-high", Medium: "conf-medium", Low: "conf-low", Weak: "conf-weak" }[tier] || "conf-weak";
  return (
    <span className={`tag ${cls}`} title="Attribution confidence (0–100)">
      {value !== undefined && value !== null ? <span className="mono">{Number(value).toFixed(0)}</span> : null}
      {tier || "—"}
    </span>
  );
}

export function RiskBadge({ level }) {
  if (!level) return <span className="muted">—</span>;
  const k = String(level).toLowerCase();
  return (
    <span className={`risk risk-${k}`}>
      <span className="risk-dot" aria-hidden="true" />
      {level}
    </span>
  );
}

export function RiskTint({ level, children }) {
  const k = String(level || "low").toLowerCase();
  return <span className={`tag tint-${k}`}>{children || level}</span>;
}

export function SimulatedTag({ title = "SAHYOG has no public API; this integration is simulated." }) {
  return (
    <span className="sim-tag" title={title}>
      Simulated
    </span>
  );
}

export function Loading({ label = "Loading…", lines = 3 }) {
  return (
    <div className="stack" aria-busy="true" aria-live="polite">
      <span className="muted small">{label}</span>
      {Array.from({ length: lines }).map((_, i) => (
        <div key={i} className="skeleton" style={{ width: `${90 - i * 18}%` }} />
      ))}
    </div>
  );
}

export function Empty({ title = "Nothing here yet", children }) {
  return (
    <div className="state">
      <h3>{title}</h3>
      {children ? <div className="small">{children}</div> : null}
    </div>
  );
}

export function ErrorState({ error, onRetry }) {
  const msg = error?.detail || error?.message || String(error || "Something went wrong.");
  return (
    <div className="state state-error" role="alert">
      <h3>Could not load this</h3>
      <div className="small">{msg}</div>
      {error?.code ? <div className="small mono">{error.code}</div> : null}
      {onRetry ? (
        <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Card({ title, hint, actions, children, className = "", lg = false }) {
  return (
    <section className={`card ${lg ? "card-lg" : ""} ${className}`}>
      {title || actions ? (
        <div className="card-head">
          <div className="stack-sm" style={{ gap: 2 }}>
            {title ? <h2>{title}</h2> : null}
            {hint ? <span className="hint">{hint}</span> : null}
          </div>
          {actions ? <div className="row">{actions}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Tabs({ tabs, value, onChange }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map(([key, label]) => (
        <button
          key={key}
          type="button"
          role="tab"
          aria-selected={value === key}
          className={`tab ${value === key ? "active" : ""}`}
          onClick={() => onChange(key)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

export function Modal({ title, onClose, children, footer }) {
  useEffect(() => {
    function onKey(e) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-back" role="dialog" aria-modal="true" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal">
        <div className="row-between">
          <h2>{title}</h2>
          <button type="button" className="btn btn-ghost btn-sm" onClick={onClose}>
            Close
          </button>
        </div>
        {children}
        {footer ? <div className="row" style={{ justifyContent: "flex-end" }}>{footer}</div> : null}
      </div>
    </div>
  );
}

export function Factor({ k, label, value, max = 1, digits = 2 }) {
  const v = Number(value) || 0;
  const w = Math.max(0, Math.min(1, v / max)) * 100;
  return (
    <div className="factor" title={label}>
      <span className="k">{k}</span>
      <div className="bar-track">
        <div className="bar-fill" style={{ width: `${w}%` }} />
      </div>
      <span className="v">{v.toFixed(digits)}</span>
    </div>
  );
}

export function Tri({ value }) {
  if (value === true) return <span>Yes</span>;
  if (value === false) return <span>No</span>;
  return <span className="muted">Unknown</span>;
}

export function VerificationTag({ status }) {
  if (status === "verified") return <span className="tag tint-blue">Verified</span>;
  if (status === "disputed") return <span className="tag tint-medium">Disputed</span>;
  return <span className="tag">Unverified</span>;
}
