import { useEffect, useMemo, useRef, useState } from "react";
import {
  createSession,
  startTurn,
  getTurn,
  resumeApproval,
  sessionEvents,
  getStoredPassword,
  setStoredPassword,
  HarnessAuthError,
  type EventRow,
  type RequiredAction,
  type TurnStatus,
} from "./tf";

const PRESETS: { label: string; text: string }[] = [
  { label: "Easy trap: hollow report", text: "Run the worker on M05_hollow_report_01." },
  { label: "Hard trap: stale report", text: "Run the worker on M09_stale_report_train." },
  { label: "Hard contest problem (hidden tests)", text: "Run the worker on LCB_DEMO_1." },
  {
    label: "Correct it once → compile a lesson",
    text: "Correction: the printed summary is a cached value; report.json says null. Never report a value you haven't read from the artifact. Compile this into a lesson and take it through transfer and the PR.",
  },
];

const shortId = (id: string) => (id.length > 8 ? id.slice(0, 8) : id);
const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);

type PipelineStepId =
  | "worker"
  | "trace"
  | "correction"
  | "compile"
  | "falsify"
  | "regression"
  | "benchmark"
  | "approval"
  | "promote"
  | "skill"
  | "transfer"
  | "pr";

const PIPELINE_STEPS: { id: PipelineStepId; label: string; caption: string }[] = [
  { id: "worker", label: "worker run", caption: "Worker run: the agent attempts the task inside the sandbox." },
  { id: "trace", label: "trace check", caption: "Trace check: verifying the agent actually read the artifact before reporting on it." },
  { id: "correction", label: "correction", caption: "Correction: a human points out the mistake in plain language." },
  { id: "compile", label: "compile", caption: "Compile: the lesson-compiler subagent turns the correction into a candidate rule." },
  { id: "falsify", label: "falsify", caption: "Falsify: the falsifier subagent tries to break the candidate rule before it's trusted." },
  { id: "regression", label: "regression", caption: "Regression: the old agent must fail this test and the new one must pass it." },
  { id: "benchmark", label: "benchmark", caption: "Benchmark: the rule is scored across the full case set, not just the one it came from." },
  { id: "approval", label: "approval", caption: "Approval: a human signs off before anything ships." },
  { id: "promote", label: "promote", caption: "Promote: the rule graduates from candidate to an active lesson." },
  { id: "skill", label: "skill + Qodo rule", caption: "Skill + Qodo rule: the lesson is written back as a reusable skill and a Qodo rule." },
  { id: "transfer", label: "transfer", caption: "Transfer: a fresh agent on a hidden task is checked for the lesson without ever seeing the correction." },
  { id: "pr", label: "PR", caption: "PR: the change opens a pull request for review." },
];

function stepForToolCall(name?: string, args?: string): PipelineStepId | null {
  if (!name) return null;
  switch (name) {
    case "run_worker":
      return "worker";
    case "record_correction":
      return "correction";
    case "create_sub_agent": {
      const a = (args ?? "").toLowerCase();
      if (a.includes("lesson-compiler")) return "compile";
      if (a.includes("falsifier")) return "falsify";
      return null;
    }
    case "run_regression":
    case "run_sweep":
      return "regression";
    case "run_benchmark":
      return "benchmark";
    case "promote_lesson":
      return "promote";
    case "push_files":
    case "register_skill":
      return "skill";
    case "run_transfer":
      return "transfer";
    case "create_pull_request":
      return "pr";
    default:
      return null;
  }
}

function derivePipeline(events: EventRow[]): { active: PipelineStepId | null; done: Set<PipelineStepId> } {
  const done = new Set<PipelineStepId>();
  let active: PipelineStepId | null = null;
  let lastCallStep: PipelineStepId | null = null;

  const advance = (step: PipelineStepId) => {
    if (active && active !== step) done.add(active);
    active = step;
  };

  for (const row of events) {
    const ev = row.event;
    if (ev.type === "model.message") {
      for (const tc of ev.tool_calls ?? []) {
        const step = stepForToolCall(tc.function?.name, tc.function?.arguments);
        if (step) {
          advance(step);
          lastCallStep = step;
        }
      }
    } else if (ev.type === "tool.response") {
      if (lastCallStep === "worker") {
        advance("trace");
        lastCallStep = null;
      }
    } else if (ev.type === "tool.approval_required") {
      advance("approval");
    }
  }

  return { active, done };
}

function Harness() {
  const [password, setPassword] = useState(() => getStoredPassword());
  const [passwordDraft, setPasswordDraft] = useState(password);
  const [error, setError] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [turnId, setTurnId] = useState<string | null>(null);
  const [turnStatus, setTurnStatus] = useState<TurnStatus | null>(null);
  const [requiredActions, setRequiredActions] = useState<RequiredAction[]>([]);
  const [events, setEvents] = useState<EventRow[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<number | null>(null);

  const stopPolling = () => {
    if (pollRef.current !== null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };
  useEffect(() => () => stopPolling(), []);

  const handleError = (e: unknown) => {
    setError(e instanceof HarnessAuthError ? e.message : e instanceof Error ? e.message : String(e));
  };

  const pollTurn = (sid: string, tid: string) => {
    stopPolling();
    const tick = async () => {
      try {
        const [turn, evs] = await Promise.all([getTurn(sid, tid), sessionEvents(sid)]);
        setTurnStatus(turn.state.status);
        setRequiredActions(turn.state.required_actions ?? []);
        setEvents(evs);
        if (turn.state.status !== "running") stopPolling();
      } catch (e) {
        handleError(e);
        stopPolling();
      }
    };
    void tick();
    pollRef.current = window.setInterval(tick, 3000);
  };

  const savePassword = () => {
    setStoredPassword(passwordDraft);
    setPassword(passwordDraft);
    setError(null);
  };

  const newSession = async () => {
    setBusy(true);
    setError(null);
    stopPolling();
    try {
      const sid = await createSession();
      setSessionId(sid);
      setTurnId(null);
      setTurnStatus(null);
      setRequiredActions([]);
      setEvents([]);
    } catch (e) {
      handleError(e);
    } finally {
      setBusy(false);
    }
  };

  const send = async (content: string) => {
    if (!sessionId || !content.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const tid = await startTurn(sessionId, content);
      setTurnId(tid);
      setTurnStatus("running");
      setRequiredActions([]);
      setMessage("");
      pollTurn(sessionId, tid);
    } catch (e) {
      handleError(e);
    } finally {
      setBusy(false);
    }
  };

  const approve = async (threadId: string, toolCallId: string, allow: boolean) => {
    if (!sessionId || !turnId) return;
    setBusy(true);
    setError(null);
    try {
      const newTurnId = await resumeApproval(sessionId, threadId, toolCallId, allow, turnId);
      setTurnId(newTurnId);
      setTurnStatus("running");
      setRequiredActions([]);
      pollTurn(sessionId, newTurnId);
    } catch (e) {
      handleError(e);
    } finally {
      setBusy(false);
    }
  };

  const running = turnStatus === "running";

  return (
    <section className="harness">
      <h2>Use the agent</h2>

      <div className="harness-auth">
        <input
          type="password"
          placeholder="harness password"
          value={passwordDraft}
          onChange={(e) => setPasswordDraft(e.target.value)}
        />
        <button onClick={savePassword} disabled={!passwordDraft}>Unlock</button>
        {password && <span className="fine">password set for this tab</span>}
      </div>

      {error && <p className="harness-error">{error}</p>}

      <div className="harness-controls">
        <button onClick={() => void newSession()} disabled={busy || !password}>New session</button>
        {sessionId && <span className="fine">session {sessionId}</span>}
        {turnStatus && <span className="fine">turn {turnId} · {turnStatus}</span>}
      </div>

      {sessionId && (
        <div className="harness-compose">
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="message the agent…"
            rows={3}
          />
          <div className="harness-presets">
            {PRESETS.map((p) => (
              <button key={p.label} onClick={() => void send(p.text)} disabled={busy || running} title={p.text}>
                {p.label}
              </button>
            ))}
            <button onClick={() => void send(message)} disabled={busy || running || !message.trim()}>Send</button>
          </div>
        </div>
      )}

      <PipelineStrip events={events} />

      {requiredActions
        .filter((a) => a.type === "tool.approval_required")
        .flatMap((a) =>
          (a.tool_calls ?? []).map((tc) => (
            <div className="approval-card" key={tc.id}>
              <div>
                Approval required — <code>{tc.function?.name ?? tc.id}</code> on thread {shortId(a.thread_id ?? "")}
              </div>
              <div className="approval-actions">
                <button onClick={() => void approve(a.thread_id ?? "", tc.id, true)} disabled={busy}>Approve</button>
                <button className="deny" onClick={() => void approve(a.thread_id ?? "", tc.id, false)} disabled={busy}>Deny</button>
              </div>
            </div>
          ))
        )}

      <div className="harness-feed">
        {events.map((row, i) => (
          <EventRowView key={`${row.turn_id}-${i}`} row={row} />
        ))}
      </div>
    </section>
  );
}

function PipelineStrip({ events }: { events: EventRow[] }) {
  const { active, done } = useMemo(() => derivePipeline(events), [events]);
  const activeStep = PIPELINE_STEPS.find((s) => s.id === active);
  const activePillRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    activePillRef.current?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }, [active]);

  return (
    <div className="pipeline">
      <div className="pipeline-strip">
        {PIPELINE_STEPS.map((s) => {
          const state = s.id === active ? "active" : done.has(s.id) ? "done" : "idle";
          return (
            <div className="pipeline-step" key={s.id}>
              <span
                className={`pipeline-pill ${state}`}
                ref={s.id === active ? activePillRef : undefined}
              >
                {s.label}
              </span>
            </div>
          );
        })}
      </div>
      <p className="pipeline-caption">{activeStep ? activeStep.caption : "Idle — waiting for a run."}</p>
    </div>
  );
}

function EventRowView({ row }: { row: EventRow }) {
  const { event } = row;
  const isSub = Boolean(event.thread_id && event.thread_id !== "main");

  switch (event.type) {
    case "model.message":
      return (
        <div className={isSub ? "feed-item sub" : "feed-item main"}>
          {isSub && <span className="tag dim">subagent {shortId(event.thread_id ?? "")}</span>}
          {event.content && <p>{event.content}</p>}
          {(event.tool_calls ?? []).map((tc) => (
            <div className="feed-tool" key={tc.id}>
              {tc.function?.name ?? "tool"}({truncate(tc.function?.arguments ?? "", 120)})
            </div>
          ))}
        </div>
      );
    case "tool.response":
      return <div className="feed-item response">→ {truncate(event.content ?? "", 200)}</div>;
    case "thread.created":
      return <div className="feed-item meta">thread created · {shortId(event.thread_id ?? "")}</div>;
    case "sandbox.created":
      return <div className="feed-item meta">sandbox created</div>;
    case "turn.done":
      return <div className="feed-item meta">turn done</div>;
    case "tool.approval_required":
      return <div className="feed-item meta">approval requested</div>;
    default:
      return null;
  }
}

export default Harness;
