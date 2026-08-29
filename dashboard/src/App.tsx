import { useEffect, useMemo, useState } from "react";
import bundledSnapshot from "../public/data.json";

type Metric = { value: unknown; artifact?: string | null };
type Snapshot = {
  generated_at?: string;
  baseline?: { artifact?: string | null; summary?: Record<string, Metric>; families?: Family[]; worst_cases?: FailureCase[] };
  board?: BoardRow[];
  interventions?: Intervention[];
  ledger?: { artifact?: string | null; lessons?: Lesson[]; observations?: Observation[]; observation_counts?: CountRow[] };
  transfer?: { artifact?: string | null; summary?: Record<string, unknown>; result?: TransferResult | null; trueforge_base_url?: string };
  spend?: { artifact?: string; data?: SpendData | null; error?: string | null };
  warnings?: string[];
};
type Family = { family?: string; trap_runs: Metric; mistakes: Metric; repetition_rate: Metric; controls_passed: Metric; mean_score: Metric; failure_cases?: FailureCase[]; fabricated_completion?: boolean };
type FailureCase = { case_id?: string; score?: number; caps?: string[]; claim?: string; artifact?: string | null };
type BoardRow = { artifact?: string; before_artifact?: string | null; after_artifact?: string | null; family?: string | null; intervention_type?: string | null; decision?: string; reasons?: string[]; rule_text?: string; metrics: Record<string, { before: Metric; after: Metric; delta: Metric }> };
type Intervention = { intervention_type?: string | null; repetition: { before: Metric; after: Metric; artifact?: string }[]; control_pass: { before: Metric; after: Metric; artifact?: string }[]; decisions: { value?: unknown; artifact?: string }[]; regressions: Regression[] };
type Regression = { artifact?: string; case_id?: string; base_fails: Metric; candidate_passes: Metric; valid_regression_test: Metric; rule_text?: string };
type Lesson = { id?: string; ts?: string; family?: string; status?: string; status_note?: string | null; intervention_type?: string; rule_text?: string; invariant?: string; correction?: Record<string, unknown> | null; evidence?: Evidence[]; provenance?: { correction_id?: string; lesson_id?: string; evidence_ids?: string[]; status_id?: string | null } };
type Evidence = { id?: string; evidence_kind?: string; artifact_path?: string; verdict?: string; summary?: Record<string, unknown>; artifact?: string };
type Observation = { id?: string; source_url?: string; quote?: string; family?: string; surface?: string; causal_trap?: string };
type CountRow = { family?: string; count?: number; artifact?: string | null };
type TransferResult = { final_message?: string; score?: number; session_id?: string; case_id?: string };
type SpendData = { cap_usd?: number; spent_usd?: number; remaining_usd?: number; by_model?: Record<string, { usd?: number; turns?: number }> };

const metricNames: Record<string, string> = {
  mean_score: "mean score",
  mistake_repetition_rate: "repetition",
  false_completion_rate: "false completion",
  control_pass_rate: "control pass",
  evidence_rate: "evidence",
};

function noData(value: unknown): value is null | undefined {
  return value === null || value === undefined || value === "";
}

function numberText(value: unknown, percent = false): string {
  if (noData(value) || typeof value !== "number" || Number.isNaN(value)) return "no data";
  if (percent) return `${(value * 100).toFixed(value * 100 % 1 === 0 ? 0 : 1)}%`;
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function valueText(metric: Metric | undefined, key?: string): string {
  const value = metric?.value;
  if (typeof value === "string") return value || "no data";
  return numberText(value, Boolean(key?.includes("rate")));
}

function Artifact({ name }: { name?: string | null }) {
  return name ? <span className="artifact">{name}</span> : <span className="artifact muted">no data</span>;
}

function MetricCell({ item, keyName, delta }: { item?: Metric; keyName?: string; delta?: boolean }) {
  return <span className={delta && typeof item?.value === "number" && item.value > 0 ? "metric positive" : "metric"}>{valueText(item, keyName)}{item?.artifact && <Artifact name={item.artifact} />}</span>;
}

function Decision({ value }: { value?: unknown }) {
  const text = typeof value === "string" ? value : "no data";
  return <span className={`decision ${text}`}>{text}</span>;
}

function SectionTitle({ eyebrow, title, count, id }: { eyebrow: string; title: string; count?: string; id?: string }) {
  return <div className="section-title" id={id}><div><span className="eyebrow">{eyebrow}</span><h2>{title}</h2></div>{count && <span className="count">{count}</span>}</div>;
}

function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetch("./data.json", { cache: "no-store" })
      .then((response) => { if (!response.ok) throw new Error(`data.json ${response.status}`); return response.json(); })
      .then(setSnapshot)
      .catch(() => setSnapshot(bundledSnapshot as Snapshot));
  }, []);

  const data = snapshot;
  const baseline = data?.baseline;
  const spend = data?.spend?.data;
  const spentPercent = spend && typeof spend.cap_usd === "number" && typeof spend.spent_usd === "number" && spend.cap_usd > 0 ? Math.min(100, (spend.spent_usd / spend.cap_usd) * 100) : null;
  const activeLessons = useMemo(() => data?.ledger?.lessons?.filter((lesson) => lesson.status === "active") ?? [], [data]);

  if (!data) return <main className="loading">Loading evidence snapshot…</main>;
  if (error) return <main className="loading">{error}</main>;

  return <main>
    <header className="topbar">
      <div className="brand"><span className="signal" /> <span>ROOLYTOOLY</span><span className="slash">/</span><span className="subbrand">EVIDENCE BOARD</span></div>
      <nav><a href="#board">board</a><a href="#families">families</a><a href="#ledger">ledger</a><a href="#transfer">transfer</a></nav>
      <div className="status"><span className="dot" /> snapshot <Artifact name={data.generated_at} /></div>
    </header>

    <section className="overview">
      <div><span className="eyebrow">01 / instrument panel</span><h1>Corrections that survive the next run.</h1><p>Code-derived evidence from benchmark artifacts, regression runs, the append-only ledger, and transfer.</p></div>
      <Spend spend={spend} percent={spentPercent} artifact={data.spend?.artifact} />
      <div className="overview-stats">
        <Stat label="baseline mean" item={baseline?.summary?.mean_score} keyName="mean_score" />
        <Stat label="baseline repetition" item={baseline?.summary?.mistake_repetition_rate} keyName="mistake_repetition_rate" />
        <Stat label="active lessons" item={{ value: activeLessons.length, artifact: data.ledger?.artifact }} />
      </div>
    </section>

    <section id="board" className="section board-section">
      <SectionTitle eyebrow="02 / autoresearch" title="Before / after" count={`${data.board?.length ?? 0} comparison${data.board?.length === 1 ? "" : "s"}`} />
      <div className="board-grid">{data.board?.length ? data.board.map((row) => <BoardCard key={row.artifact} row={row} />) : <Empty />}</div>
    </section>

    <section id="families" className="section split-section">
      <div className="panel family-panel">
        <SectionTitle eyebrow="03 / naive worker" title="Family failures" count={baseline?.artifact ?? "no data"} />
        <div className="table-wrap"><table><thead><tr><th>family</th><th>trap runs</th><th>mistakes</th><th>repeat</th><th>controls</th><th>mean</th></tr></thead><tbody>{baseline?.families?.length ? baseline.families.map((family) => <FamilyRow key={family.family} family={family} />) : <tr><td colSpan={6}><Empty /></td></tr>}</tbody></table></div>
        <div className="failure-strip"><span className="eyebrow">worst claims / quoted from worker artifact</span>{baseline?.worst_cases?.length ? baseline.worst_cases.slice(0, 3).map((item) => <div className="quote-row" key={`${item.case_id}-${item.score}`}><span className="score-bad">{valueText({ value: item.score })}</span><span><b>{item.case_id}</b> “{item.claim || "no data"}”</span><Artifact name={item.artifact} /></div>) : <Empty />}</div>
      </div>
      <div className="panel intervention-panel">
        <SectionTitle eyebrow="04 / intervention matrix" title="What changed" count={`${data.interventions?.length ?? 0} type${data.interventions?.length === 1 ? "" : "s"}`} />
        {data.interventions?.length ? data.interventions.map((row, index) => <InterventionRow key={`${row.intervention_type}-${index}`} row={row} />) : <Empty />}
      </div>
    </section>

    <section id="ledger" className="section split-section ledger-section">
      <div className="panel ledger-panel">
        <SectionTitle eyebrow="05 / append-only" title="Lesson ledger" count={data.ledger?.artifact ?? "no data"} />
        {data.ledger?.lessons?.length ? data.ledger.lessons.slice().reverse().map((lesson) => <LessonCard key={lesson.id} lesson={lesson} />) : <Empty />}
      </div>
      <div className="panel wild-panel">
        <SectionTitle eyebrow="06 / observation stream" title="In the wild" count={`${data.ledger?.observations?.length ?? 0} observations`} />
        <div className="count-list">{data.ledger?.observation_counts?.map((item) => <div className="count-item" key={item.family}><span>{item.family}</span><strong>{item.count}</strong><Artifact name={item.artifact} /></div>)}</div>
        {data.ledger?.observations?.slice(-4).reverse().map((item) => <div className="observation" key={item.id}><div className="observation-head"><span className="tag">{item.family || "no data"}</span><a href={item.source_url} target="_blank" rel="noreferrer">source ↗</a></div><p>“{item.quote || "no data"}”</p><span className="muted small">{item.surface || "no data"}</span></div>)}
      </div>
    </section>

    <section id="transfer" className="section transfer-section">
      <SectionTitle eyebrow="07 / zero-history proof" title="Transfer" count={data.transfer?.artifact ?? "no data"} />
      <div className="transfer-card">{data.transfer?.result ? <><div className="transfer-score"><span className="eyebrow">fresh agent score</span><strong>{numberText(data.transfer.result.score)}</strong><Artifact name={data.transfer.artifact} /></div><div className="transfer-message"><span className="eyebrow">final message / artifact-backed</span><p>“{data.transfer.result.final_message || "no data"}”</p><div className="transfer-meta"><span>{data.transfer.result.case_id || "no data"}</span><span>session <code>{data.transfer.result.session_id || "no data"}</code></span>{data.transfer.result.session_id ? <a href={`${data.transfer.trueforge_base_url || "http://localhost:8790/sessions/"}${data.transfer.result.session_id}`} target="_blank" rel="noreferrer">open TrueForge ↗</a> : <span className="muted">no data</span>}</div></div></> : <Empty />}</div>
    </section>

    {data.warnings?.length ? <footer><span className="eyebrow">snapshot warnings</span>{data.warnings.map((warning) => <span key={warning} className="warning">{warning}</span>)}</footer> : null}
  </main>;
}

function Stat({ label, item, keyName }: { label: string; item?: Metric; keyName?: string }) { return <div className="stat"><span className="eyebrow">{label}</span><strong>{valueText(item, keyName)}</strong><Artifact name={item?.artifact} /></div>; }

function Spend({ spend, percent, artifact }: { spend?: SpendData | null; percent: number | null; artifact?: string }) { return <div className="spend"><div className="spend-head"><span className="eyebrow">spend</span><span>{spend ? `$${numberText(spend.spent_usd)} / $${numberText(spend.cap_usd)}` : "no data"}</span></div><div className="meter"><span style={{ width: `${percent ?? 0}%` }} /></div><div className="spend-foot"><span>{spend ? `$${numberText(spend.remaining_usd)} remaining` : "TrueForge unavailable"}</span><Artifact name={artifact} /></div></div>; }

function BoardCard({ row }: { row: BoardRow }) { return <article className="board-card"><div className="card-head"><div><span className="tag">{row.family || "no data"}</span><span className="intervention">{row.intervention_type || "no data"}</span></div><Decision value={row.decision} /></div><h3>{row.rule_text || "no data"}</h3><div className="artifact-pair"><Artifact name={row.before_artifact} /><span>→</span><Artifact name={row.after_artifact} /></div><div className="metric-grid">{Object.entries(row.metrics).map(([key, values]) => <div className="metric-row" key={key}><span>{metricNames[key] || key}</span><MetricCell item={values.before} keyName={key} /><span className="arrow">→</span><MetricCell item={values.after} keyName={key} /><MetricCell item={values.delta} keyName={key} delta /></div>)}</div>{row.reasons?.length ? <div className="reasons"><span className="eyebrow">decision reasons</span>{row.reasons.map((reason) => <span key={reason}>{reason}</span>)}</div> : null}</article>; }

function FamilyRow({ family }: { family: Family }) { return <tr className={family.fabricated_completion ? "highlight-row" : ""}><td><b>{family.family || "no data"}</b>{family.fabricated_completion && <span className="cap">fabricated_completion</span>}</td><td><MetricCell item={family.trap_runs} /></td><td><MetricCell item={family.mistakes} /></td><td><MetricCell item={family.repetition_rate} keyName="repetition_rate" /></td><td><MetricCell item={family.controls_passed} /></td><td><MetricCell item={family.mean_score} /></td></tr>; }

function InterventionRow({ row }: { row: Intervention }) {
  const label = row.intervention_type || "no data";
  const runCount = Math.max(row.repetition.length, row.control_pass.length, row.decisions.length);
  return <div className="intervention-row">
    <div className="intervention-label"><span className="tag">{label}</span><span className="muted">{row.regressions.length} regression artifact{row.regressions.length === 1 ? "" : "s"}</span></div>
    {runCount ? Array.from({ length: runCount }, (_, index) => {
      const bench = row.repetition[index];
      const control = row.control_pass[index];
      const decision = row.decisions[index];
      return <div className="intervention-run" key={`${label}-${index}-${bench?.artifact || control?.artifact || decision?.artifact || "no-data"}`}>
        <span className="run-label">comparison {index + 1}</span>
        <div className="intervention-values">
          <div><span className="eyebrow">repetition</span><MetricCell item={bench?.before} keyName="mistake_repetition_rate" /><span className="arrow">→</span><MetricCell item={bench?.after} keyName="mistake_repetition_rate" /></div>
          <div><span className="eyebrow">control pass</span><MetricCell item={control?.before} keyName="control_pass_rate" /><span className="arrow">→</span><MetricCell item={control?.after} keyName="control_pass_rate" /></div>
          <div><span className="eyebrow">decision</span><Decision value={decision?.value} /><Artifact name={decision?.artifact} /></div>
        </div>
      </div>;
    }) : <Empty />}
    {row.regressions.map((regression) => <div className="regression" key={regression.artifact}><span>{regression.case_id || "no data"}</span><span>base fails <MetricCell item={regression.base_fails} /></span><span>candidate passes <MetricCell item={regression.candidate_passes} /></span><Decision value={regression.valid_regression_test.value === true ? "valid" : regression.valid_regression_test.value === false ? "invalid" : undefined} /><Artifact name={regression.artifact} /></div>)}
  </div>;
}

function LessonCard({ lesson }: { lesson: Lesson }) { return <article className="lesson-card"><div className="card-head"><div><span className="tag">{lesson.family || "no data"}</span><span className="intervention">{lesson.intervention_type || "no data"}</span></div><span className={`status ${lesson.status}`}>{lesson.status || "candidate"}</span></div><p className="rule">{lesson.rule_text || "no data"}</p><div className="provenance"><span>correction <code>{lesson.provenance?.correction_id || "no data"}</code></span><span>lesson <code>{lesson.provenance?.lesson_id || "no data"}</code></span><span>evidence <code>{lesson.provenance?.evidence_ids?.length ? lesson.provenance.evidence_ids.join(", ") : "no data"}</code></span><span>status <code>{lesson.provenance?.status_id || "candidate / no status record"}</code></span></div>{lesson.evidence?.map((item) => <div className="evidence-row" key={item.id}><span className="tag">{item.evidence_kind || "no data"}</span><Decision value={item.verdict} /><span>{item.summary ? JSON.stringify(item.summary) : "no data"}</span><Artifact name={item.artifact_path} /></div>)}{lesson.correction ? <div className="correction"><span className="eyebrow">correction</span><p>“{String(lesson.correction.user_correction || "no data")}”</p><Artifact name={typeof lesson.correction.evidence === "string" ? "ledger/ledger.jsonl" : undefined} /></div> : null}</article>; }

function Empty() { return <span className="empty">no data</span>; }

export default App;
