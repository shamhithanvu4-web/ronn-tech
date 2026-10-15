"use client";

import { useEffect, useRef, useState } from "react";

import { api } from "@/lib/api";
import { STAGES } from "@/lib/format";

/** Staged progress, polling GET /analyses/{id} every 1.5 s until done or failed. */
export default function AnalysisProgress({ analysisId, onFinished }) {
  const [a, setA] = useState(null);
  const [error, setError] = useState(null);
  const finished = useRef(false);

  useEffect(() => {
    let timer;
    let alive = true;
    async function tick() {
      try {
        const r = await api.get(`/analyses/${analysisId}`);
        if (!alive) return;
        setA(r);
        setError(null);
        if ((r.status === "done" || r.status === "failed") && !finished.current) {
          finished.current = true;
          onFinished?.(r);
          return;
        }
      } catch (e) {
        if (!alive) return;
        setError(e);
      }
      timer = setTimeout(tick, 1500);
    }
    tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [analysisId, onFinished]);

  const stageIdx = a ? STAGES.findIndex(([k]) => k === a.stage) : -1;
  const failed = a?.status === "failed";

  return (
    <div className="stack">
      <div className="stages" aria-live="polite">
        {STAGES.map(([key, label], i) => {
          let cls = "";
          if (a?.status === "done" || i < stageIdx) cls = "done";
          else if (i === stageIdx && !failed) cls = "current";
          return (
            <div key={key} className={`stage ${cls}`}>
              <div className="stage-bar">
                <span />
              </div>
              <span>{label}</span>
            </div>
          );
        })}
      </div>
      <div className="row-between small muted">
        <span>
          {failed
            ? "Analysis failed"
            : a?.status === "done"
              ? "Done"
              : a
                ? `${STAGES[Math.max(0, stageIdx)]?.[1] || "Queued"} · ${a.progress ?? 0}%`
                : "Queued"}
        </span>
        <span className="mono">{analysisId}</span>
      </div>
      {failed ? (
        <div className="notice notice-danger" role="alert">
          <div>
            <b>{a.error_code}</b>: {a.error}
          </div>
        </div>
      ) : null}
      {error ? <div className="notice notice-warn">Polling problem: {error.detail || error.message}. Retrying…</div> : null}
    </div>
  );
}
