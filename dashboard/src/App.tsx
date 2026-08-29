import { useEffect, useState } from "react";
import bundledSnapshot from "../public/data.json";
import Harness from "./Harness";
import QodoRulesRail, { type QodoSnapshot } from "./QodoRulesRail";

type HEMode = { artifact?: string | null; n?: number | null; pass_at_1?: number | null; false_completion_rate?: number | null; honest_fail_rate?: number | null; unknown_rate?: number | null; evidence_rate?: number | null; mean_tokens?: number | null };
type Snapshot = {
  generated_at?: string;
  humaneval?: { bare?: HEMode | null; harness?: HEMode | null };
  livecodebench?: { bare?: HEMode | null; harness?: HEMode | null };
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

            <section className="compare">
              <h2>Hard contest problems with hidden tests · gpt-5.6 luna (high) · bare vs. with harness</h2>
              <div className="compare-grid">
                <Column title="bare luna" m={data?.livecodebench?.bare ?? null} />
                <Column title="luna + harness" m={data?.livecodebench?.harness ?? null} accent />
              </div>
              <p className="fine">{data?.livecodebench?.bare || data?.livecodebench?.harness ? `n = ${num(data?.livecodebench?.harness?.n ?? data?.livecodebench?.bare?.n)} problems · ground truth = hidden tests read from out/results.json · ${data?.livecodebench?.harness?.artifact ?? data?.livecodebench?.bare?.artifact ?? ""}` : "run in progress — no artifact yet"}</p>
            </section>

            {!data && <p className="fine">loading…</p>}
          </div>

          <footer>snapshot {data?.generated_at} · spend ${num(data?.spend?.data?.spent_usd)} of ${num(data?.spend?.data?.cap_usd)} · <a href="https://github.com/RaphaelKhalid/RoolyTooly">github</a></footer>
        </main>
      </div>

      <QodoRulesRail data={data?.qodo} open={rulesOpen} onClose={() => setRulesOpen(false)} sessionActive={sessionActive} />
    </div>
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
