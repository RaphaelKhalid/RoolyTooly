import { useEffect, useState } from "react";
import bundledSnapshot from "../public/data.json";
import Harness from "./Harness";
import QodoRulesRail, { type QodoSnapshot } from "./QodoRulesRail";

type HEMode = { artifact?: string | null; n?: number | null; pass_at_1?: number | null; false_completion_rate?: number | null; honest_fail_rate?: number | null; unknown_rate?: number | null; evidence_rate?: number | null; mean_tokens?: number | null };
type Retrieval = {
  artifact?: string | null;
  local_ms_median?: number | null;
  qodo_ms_median?: number | null;
  speedup?: number | null;
  agreement_rate?: number | null;
  overlap_rate?: number | null;
  compared?: number | null;
  n?: number | null;
  lessons_indexed?: number | null;
  embedding_model?: string | null;
  cold_ms_median?: number | null;
};
type Snapshot = {
  generated_at?: string;
  humaneval?: { bare?: HEMode | null; harness?: HEMode | null };
  livecodebench?: { bare?: HEMode | null; harness?: HEMode | null };
  retrieval?: Retrieval | null;
  qodo?: QodoSnapshot;
  spend?: { data?: { cap_usd?: number; spent_usd?: number } | null };
};

const pct = (v: unknown) => (typeof v === "number" && !Number.isNaN(v) ? `${Math.round(v * 100)}%` : "—");
const num = (v: unknown) => (typeof v === "number" && !Number.isNaN(v) ? (Number.isInteger(v) ? String(v) : v.toFixed(1)) : "—");

function App() {
  const [data, setData] = useState<Snapshot | null>(null);
  const [rulesOpen, setRulesOpen] = useState(false);
  const [sessionActive, setSessionActive] = useState(false);
  useEffect(() => {
    fetch("./data.json", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then(setData)
      .catch(() => setData(bundledSnapshot as unknown as Snapshot));
  }, []);

  return (
    <div className="app-layout">
      <div className="app-main">
        <Harness onOpenRules={() => setRulesOpen(true)} onSessionActiveChange={setSessionActive} />

        <main className="evidence-wrap">
          <div className="evidence">
            <p className="evidence-label">Evidence</p>

            <div className="evidence-row">
              <section className="compare">
                <h2>Hard contest problems with hidden tests · gpt-5.6 luna (high) · bare vs. with harness</h2>
                <div className="compare-grid">
                  <Column title="bare luna" m={data?.livecodebench?.bare ?? null} />
                  <Column title="luna + harness" m={data?.livecodebench?.harness ?? null} accent />
                </div>
                <p className="fine">{data?.livecodebench?.bare || data?.livecodebench?.harness ? `n = ${num(data?.livecodebench?.harness?.n ?? data?.livecodebench?.bare?.n)} problems · ground truth = hidden tests read from out/results.json · ${data?.livecodebench?.harness?.artifact ?? data?.livecodebench?.bare?.artifact ?? ""}` : "run in progress — no artifact yet"}</p>
              </section>

              <RetrievalCard r={data?.retrieval} />
            </div>

            {!data && <p className="fine">loading…</p>}
          </div>

          <footer>snapshot {data?.generated_at} · spend ${num(data?.spend?.data?.spent_usd)} of ${num(data?.spend?.data?.cap_usd)} · <a href="https://github.com/RaphaelKhalid/RoolyTooly">github</a></footer>
        </main>
      </div>

      <QodoRulesRail data={data?.qodo} open={rulesOpen} onClose={() => setRulesOpen(false)} sessionActive={sessionActive} />
    </div>
  );
}

function ms(v: unknown): string {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return v >= 100 ? `${Math.round(v)}` : v.toFixed(1);
}

function RetrievalCard({ r }: { r?: Retrieval | null }) {
  const hasData = typeof r?.local_ms_median === "number" && typeof r?.qodo_ms_median === "number";
  const speedup = typeof r?.speedup === "number" ? `×${r.speedup >= 10 ? Math.round(r.speedup) : r.speedup.toFixed(1)}` : "—";
  const overlapPct = typeof r?.overlap_rate === "number" ? Math.round(r.overlap_rate * 100) : null;
  const agreementPct = typeof r?.agreement_rate === "number" ? Math.round(r.agreement_rate * 100) : null;

  return (
    <section className="compare retrieval-card">
      <h2>Rule retrieval</h2>
      <div className="retrieval-grid">
        <div className="retrieval-stat">
          <div className="big">{ms(r?.local_ms_median)}<span>local hybrid ms (BM25 + 3-gram + embeddings)</span></div>
        </div>
        <div className="retrieval-stat">
          <div className="big">{ms(r?.qodo_ms_median)}<span>Qodo rule search ms</span></div>
        </div>
        <div className="retrieval-stat accent">
          <div className="big">{speedup}<span>faster</span></div>
        </div>
      </div>
      <p className="fine">
        {hasData && overlapPct !== null && typeof r?.compared === "number"
          ? `shares ≥1 rule with Qodo on ${overlapPct}% of ${r.compared} tasks (exact top-3 match ${agreementPct ?? "—"}%) · n is small, read this as a direction, not a lab result · ${r?.artifact ?? ""}`
          : "run in progress — no artifact yet"}
      </p>
    </section>
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

export default App;
