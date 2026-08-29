import { useEffect, useMemo, useState } from "react";
import bundledSnapshot from "../public/data.json";

type Metric = { value: unknown; artifact?: string | null };
type HEMode = { artifact?: string | null; n?: number | null; pass_at_1?: number | null; false_completion_rate?: number | null; honest_fail_rate?: number | null; unknown_rate?: number | null; evidence_rate?: number | null; mean_tokens?: number | null };
type Point = { artifact?: string; label?: string; ran_at?: string; n_active_lessons?: number; n_cases?: number; mean_score?: number; mistake_repetition_rate?: number; false_completion_rate?: number; control_pass_rate?: number; per_family?: Record<string, number | null> };
type Family = { family?: string; trap_runs: Metric; mistakes: Metric; repetition_rate: Metric; controls_passed: Metric; failure_cases?: { case_id?: string; claim?: string }[] };
type Lesson = { id?: string; family?: string; status?: string; intervention_type?: string; rule_text?: string; evidence?: { evidence_kind?: string; verdict?: string; artifact_path?: string }[] };
type Observation = { id?: string; source_url?: string; quote?: string; family?: string };
type Snapshot = {
  generated_at?: string;
  humaneval?: { bare?: HEMode | null; harness?: HEMode | null };
  timeline?: Point[];
  baseline?: { artifact?: string | null; families?: Family[] };
  ledger?: { lessons?: Lesson[]; observations?: Observation[]; observation_counts?: { family?: string; count?: number }[] };
  transfer?: { artifact?: string | null; result?: { final_message?: string; score?: number; case_id?: string; session_id?: string } | null };
  spend?: { data?: { cap_usd?: number; spent_usd?: number } | null };
};

const pct = (v: unknown) => (typeof v === "number" && !Number.isNaN(v) ? `${Math.round(v * 100)}%` : "—");
const num = (v: unknown) => (typeof v === "number" && !Number.isNaN(v) ? (Number.isInteger(v) ? String(v) : v.toFixed(1)) : "—");
const mv = (m?: Metric) => (m ? m.value : undefined);

function App() {
  const [data, setData] = useState<Snapshot | null>(null);
  useEffect(() => {
    fetch("./data.json", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setData)
      .catch(() => setData(bundledSnapshot as unknown as Snapshot));
  }, []);
  const lessons = useMemo(() => data?.ledger?.lessons ?? [], [data]);
  const active = lessons.filter((l) => l.status === "active");
  if (!data) return <main className="loading">loading…</main>;

  const he = data.humaneval ?? {};
  const bare = he.bare ?? null, harness = he.harness ?? null;
  const points = data.timeline ?? [];
  const first = points[0], last = points[points.length - 1];
  const families = (data.baseline?.families ?? []).filter((f) => typeof mv(f.trap_runs) === "number" && (mv(f.trap_runs) as number) > 0);

  return (
    <main>
      <header className="hero">
        <div className="hero-inner">
          <div className="brand">ROOLYTOOLY</div>
          <h1>Correct it once.<br />It proves it won't repeat the mistake.</h1>
          <p>A TrueForge agent that turns one correction into a tested, promoted skill. Every number below is read from a file in <code>results/</code>.</p>
        </div>
      </header>

      <section className="compare">
        <h2>HumanEval+ · gpt-5.6 luna (high) · bare vs. with harness</h2>
        <div className="compare-grid">
          <Column title="bare luna" m={bare} />
          <Column title="luna + harness" m={harness} accent />
        </div>
        <p className="fine">{bare || harness ? `n = ${num(harness?.n ?? bare?.n)} problems · ground truth = plus tests read from out/results.json · ${harness?.artifact ?? bare?.artifact ?? ""}` : "run in progress — no artifact yet"}</p>
      </section>

      <section className="timeline">
        <h2>Self-improvement over the hackathon</h2>
        {points.length ? (
          <>
            <Chart points={points} />
            <div className="timeline-stats">
              <Stat label="repetition rate" from={pct(first?.mistake_repetition_rate)} to={pct(last?.mistake_repetition_rate)} />
              <Stat label="false completions" from={pct(first?.false_completion_rate)} to={pct(last?.false_completion_rate)} />
              <Stat label="controls passing" from={pct(first?.control_pass_rate)} to={pct(last?.control_pass_rate)} />
              <Stat label="promoted lessons" from={num(first?.n_active_lessons)} to={num(last?.n_active_lessons)} />
            </div>
            <p className="fine">{points.length} points · each is a full run of the agent-as-of-then on every non-train case · {points.map((p) => p.artifact).join(", ")}</p>
          </>
        ) : <p className="fine">no timeline artifacts yet</p>}
      </section>

      <section className="three">
        <div>
          <h2>Where luna still slips</h2>
          <table>
            <thead><tr><th>family</th><th>traps</th><th>repeats</th><th>controls</th></tr></thead>
            <tbody>{families.map((f) => <tr key={f.family}><td><b>{f.family}</b></td><td>{num(mv(f.trap_runs))}</td><td className={(mv(f.repetition_rate) as number) > 0 ? "bad" : "good"}>{pct(mv(f.repetition_rate))}</td><td>{typeof mv(f.controls_passed) === "string" ? String(mv(f.controls_passed)) : num(mv(f.controls_passed))}</td></tr>)}</tbody>
          </table>
          <p className="fine">{data.baseline?.artifact ?? ""}</p>
        </div>
        <div>
          <h2>Lessons ({active.length} promoted / {lessons.length} tried)</h2>
          {lessons.slice().reverse().slice(0, 6).map((l) => (
            <div className={`lesson ${l.status}`} key={l.id}>
              <div className="lesson-head"><span className="tag">{l.family}</span><span className="tag dim">{l.intervention_type}</span><span className={`state ${l.status}`}>{l.status}</span></div>
              <p>{l.rule_text}</p>
            </div>
          ))}
        </div>
        <div>
          <h2>In the wild</h2>
          <p className="fine">{data.ledger?.observations?.length ?? 0} real reports mined with Bright Data</p>
          <div className="counts">{(data.ledger?.observation_counts ?? []).slice(0, 8).map((c) => <span key={c.family}><b>{c.family}</b> {c.count}</span>)}</div>
          {(data.ledger?.observations ?? []).slice(-3).reverse().map((o) => <blockquote key={o.id}>“{o.quote}” <a href={o.source_url} target="_blank" rel="noreferrer">source ↗</a></blockquote>)}
        </div>
      </section>

      {data.transfer?.result && (
        <section className="transfer">
          <h2>Fresh agent, hidden task, promoted skill loaded</h2>
          <div className="transfer-row"><strong>{num(data.transfer.result.score)}</strong><p>“{data.transfer.result.final_message}”</p></div>
          <p className="fine">{data.transfer.result.case_id} · {data.transfer.artifact}</p>
        </section>
      )}

      <footer>snapshot {data.generated_at} · spend ${num(data.spend?.data?.spent_usd)} of ${num(data.spend?.data?.cap_usd)} · <a href="https://github.com/RaphaelKhalid/RoolyTooly">github</a></footer>
    </main>
  );
}

function Column({ title, m, accent }: { title: string; m: HEMode | null; accent?: boolean }) {
  return (
    <div className={accent ? "col accent" : "col"}>
      <h3>{title}</h3>
      <div className="big">{pct(m?.pass_at_1)}<span>pass@1</span></div>
      <div className="row"><span>false completions</span><b>{pct(m?.false_completion_rate)}</b></div>
      <div className="row"><span>honest failures</span><b>{pct(m?.honest_fail_rate)}</b></div>
      <div className="row"><span>read the evidence</span><b>{pct(m?.evidence_rate)}</b></div>
    </div>
  );
}

function Stat({ label, from, to }: { label: string; from: string; to: string }) {
  return <div className="stat"><span>{label}</span><b>{from} → {to}</b></div>;
}

function Chart({ points }: { points: Point[] }) {
  const w = 720, h = 200, pad = 28;
  const xs = points.map((_, i) => pad + (i * (w - 2 * pad)) / Math.max(1, points.length - 1));
  const y = (v?: number) => (typeof v === "number" ? h - pad - v * (h - 2 * pad) : h - pad);
  const path = (key: keyof Point) => xs.map((x, i) => `${i ? "L" : "M"}${x},${y(points[i][key] as number)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="chart" role="img" aria-label="timeline">
      {[0, 0.5, 1].map((g) => <line key={g} x1={pad} x2={w - pad} y1={y(g)} y2={y(g)} className="grid" />)}
      <path d={path("mistake_repetition_rate")} className="line rep" />
      <path d={path("control_pass_rate")} className="line ctrl" />
      {points.map((p, i) => <g key={p.artifact}><circle cx={xs[i]} cy={y(p.mistake_repetition_rate)} r={4} className="dot rep" /><text x={xs[i]} y={h - 8} textAnchor="middle" className="label">{(p.ran_at ?? "").slice(11, 16)} · {p.n_active_lessons ?? 0} lessons</text></g>)}
      <text x={pad} y={14} className="legend rep">repetition rate (down is good)</text>
      <text x={w - pad} y={14} textAnchor="end" className="legend ctrl">controls passing</text>
    </svg>
  );
}

export default App;
