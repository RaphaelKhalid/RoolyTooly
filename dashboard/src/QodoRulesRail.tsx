import { useEffect } from "react";

type Metric = { value?: unknown; artifact?: string | null };

type MistakeOrigin = {
  task_summary?: string | null;
  agent_claim?: string | null;
  user_correction?: string | null;
};

export type QodoRuleSnapshot = {
  record_id?: string | null;
  rule_id?: number | string | null;
  qodo_state?: string | null;
  scopes?: string[];
  synced_at?: string | null;
  lesson_id?: string | null;
  family?: string | null;
  lesson_status?: string | null;
  intervention_type?: string | null;
  rule_text?: string | null;
  mistake?: MistakeOrigin | null;
  lifecycle_drift?: boolean;
  artifact?: string | null;
};

export type QodoRetrieval = {
  lesson_id?: string | null;
  rule_id?: number | string | null;
  family?: string | null;
  rule_text?: string | null;
};

export type QodoSnapshot = {
  artifact?: string | null;
  linked_count?: number | null;
  rules?: QodoRuleSnapshot[];
  workspace_catalog?: {
    total?: Metric;
    source?: string | null;
    error?: string | null;
  };
  // Populated only if the builder ever records which rules a live session's
  // task retrieved. Not produced by build_qodo_rules today — always absent,
  // which the rail renders as "no data" rather than inventing a number.
  retrieved_for_task?: QodoRetrieval[] | null;
};

const displayCount = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value) ? value.toLocaleString() : "no data";

function QodoRulesRail({
  data,
  open,
  onClose,
  sessionActive,
}: {
  data?: QodoSnapshot;
  open: boolean;
  onClose: () => void;
  sessionActive: boolean;
}) {
  const rules = data?.rules ?? [];
  const catalogTotal = data?.workspace_catalog?.total?.value;
  const retrieved = data?.retrieved_for_task;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <>
      <div className={`qodo-rail-backdrop${open ? " open" : ""}`} onClick={onClose} aria-hidden="true" />
      <aside
        className={`qodo-rail${open ? " open" : ""}`}
        aria-label="Qodo rules compiled from agent mistakes"
        aria-hidden={!open ? "true" : undefined}
      >
        <div className="qodo-rail-topbar">
          <span className="qodo-rail-topbar-label">Rules</span>
          <button type="button" className="qodo-rail-close" onClick={onClose} aria-label="Close rules panel">
            close
          </button>
        </div>

        <header className="qodo-rail-header">
          <p className="qodo-kicker">Qodo rule memory</p>
          <h2>Rules born from mistakes</h2>
          <div className="qodo-totals" aria-label="Qodo rule counts">
            <div>
              <strong>{displayCount(data?.linked_count)}</strong>
              <span>mistake-linked</span>
              <code className="qodo-total-source">{data?.artifact ?? "no data"}</code>
            </div>
            <div>
              <strong>{displayCount(catalogTotal)}</strong>
              <span>workspace catalog</span>
              <code className="qodo-total-source">{data?.workspace_catalog?.source ?? "no data"}</code>
            </div>
          </div>
          <p className="qodo-rail-note">
            Only ledger-proven lesson-to-Qodo mappings appear below. The workspace count is context, not a claim that every rule came from this harness.
          </p>
        </header>

        {sessionActive && (
          <section className="qodo-retrieved" aria-live="polite">
            <h3>Retrieved for this task</h3>
            {retrieved && retrieved.length ? (
              <ul className="qodo-retrieved-list">
                {retrieved.map((r, i) => (
                  <li key={r.lesson_id ?? r.rule_id ?? i}>
                    <code>#{r.rule_id ?? "no id"}</code> {r.family ?? "no family"} — {r.rule_text ?? "no rule text"}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="qodo-retrieved-empty">no data</p>
            )}
          </section>
        )}

        <div className="qodo-rule-list">
          {rules.length ? (
            rules.map((rule) => {
              const claim = rule.mistake?.agent_claim || rule.mistake?.task_summary;
              return (
                <article className={`qodo-rule-card${rule.lifecycle_drift ? " drift" : ""}`} key={rule.record_id ?? rule.rule_id}>
                  <div className="qodo-rule-meta">
                    <span className="qodo-family">{rule.family ?? "no family"}</span>
                    <span>{rule.intervention_type ?? "rule"}</span>
                    <span className={`qodo-state ${rule.qodo_state ?? "unknown"}`}>Qodo {rule.qodo_state ?? "unknown"}</span>
                  </div>
                  {claim ? <p className="qodo-mistake">Mistake: &quot;{claim}&quot;</p> : null}
                  <p className="qodo-rule-text" title={rule.rule_text ?? undefined}>{rule.rule_text ?? "no rule text"}</p>
                  <div className="qodo-rule-foot">
                    <span className={`ledger-state ${rule.lesson_status ?? "unknown"}`}>ledger {rule.lesson_status ?? "unknown"}</span>
                    <code>#{rule.rule_id ?? "no id"}</code>
                  </div>
                  <code className="qodo-source">{rule.artifact ?? data?.artifact ?? "no data"}</code>
                </article>
              );
            })
          ) : (
            <p className="qodo-empty">No lesson-backed Qodo rule records in this snapshot.</p>
          )}
        </div>

        <footer className="qodo-rail-source">
          <span>catalog source</span>
          <code>{data?.workspace_catalog?.source ?? "no data"}</code>
        </footer>
      </aside>
    </>
  );
}

export default QodoRulesRail;
