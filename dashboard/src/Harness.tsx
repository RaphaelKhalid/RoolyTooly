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
  type ToolCall,
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

// Known subagent roles the create_sub_agent tool spawns. Used only to label
// subagent cards in the feed — falls back to a generic "subagent" label.
const SUBAGENT_NAMES = ["lesson-compiler", "falsifier", "autoresearcher", "mistake-miner"];

function deriveSubAgentNames(events: EventRow[]): Map<string, string> {
  const names = new Map<string, string>();
  let pending: string | null = null;
  for (const row of events) {
    const ev = row.event;
    if (ev.type === "model.message") {
      for (const tc of ev.tool_calls ?? []) {
        if (tc.function?.name === "create_sub_agent") {
          const args = (tc.function.arguments ?? "").toLowerCase();
          pending = SUBAGENT_NAMES.find((n) => args.includes(n)) ?? "subagent";
        }
      }
    } else if (ev.type === "thread.created" && ev.thread_id) {
      if (pending) {
        names.set(ev.thread_id, pending);
        pending = null;
      }
    }
  }
  return names;
}

// Best-effort pairing of a model.message's tool_calls with the tool.response
// events that immediately follow it in the stream, so a chip can show its
// own response when expanded.
function collectResponses(events: EventRow[], startIndex: number, count: number): (string | undefined)[] {
  const out: (string | undefined)[] = [];
  let i = startIndex + 1;
  while (out.length < count && i < events.length) {
    const ev = events[i].event;
    if (ev.type === "tool.response") {
      out.push(ev.content ?? undefined);
    } else if (ev.type === "model.message") {
      break;
    }
    i++;
  }
  while (out.length < count) out.push(undefined);
  return out;
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
  const [sentByTurn, setSentByTurn] = useState<Record<string, string>>({});
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
      setSentByTurn({});
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
      setSentByTurn((prev) => ({ ...prev, [tid]: content }));
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
  const locked = !password;
  const approvals = requiredActions.filter((a) => a.type === "tool.approval_required");
  const hasStarted = events.length > 0 || approvals.length > 0;

  return (
    <div className="chat-shell">
      <div className="chat-header">
        <header className="topbar">
          <div className="wordmark">ROOLYTOOLY</div>
          <div className="topbar-right">
            {turnStatus && (
              <span className="fine turn-status">
                turn {shortId(turnId ?? "")} · {turnStatus}
              </span>
            )}
            {!locked && (
              <span className="lock-badge" title="unlocked for this tab">
                🔓
              </span>
            )}
            {sessionId ? (
              <span className="session-pill">session {shortId(sessionId)}</span>
            ) : (
              <span className="session-pill dim">no session</span>
            )}
            <button className="new-session-btn" onClick={() => void newSession()} disabled={busy || locked}>
              New session
            </button>
          </div>
        </header>

        <PipelineStrip events={events} />
      </div>

      <div className="chat-body">
        <div className="chat-column">
          {locked && (
            <div className="msg-row system">
              <div className="password-card">
                <p className="password-card-title">Enter the harness password to unlock the agent.</p>
                <div className="password-card-row">
                  <input
                    type="password"
                    placeholder="harness password"
                    value={passwordDraft}
                    onChange={(e) => setPasswordDraft(e.target.value)}
                  />
                  <button onClick={savePassword} disabled={!passwordDraft}>
                    Unlock
                  </button>
                </div>
              </div>
            </div>
          )}

          {error && <p className="chat-error">{error}</p>}

          {!locked && !sessionId && (
            <p className="chat-empty">Start a new session, then send a message or pick a preset below.</p>
          )}

          {!locked && sessionId && !hasStarted && !running && (
            <p className="chat-empty">Send a message or pick a preset below to begin.</p>
          )}

          <ChatFeed events={events} sentByTurn={sentByTurn} />

          {approvals.flatMap((a) =>
            (a.tool_calls ?? []).map((tc) => (
              <div className="msg-row system" key={tc.id}>
                <div className="approval-card">
                  <div>
                    Approval required — <code>{tc.function?.name ?? tc.id}</code> on thread {shortId(a.thread_id ?? "")}
                  </div>
                  <div className="approval-actions">
                    <button onClick={() => void approve(a.thread_id ?? "", tc.id, true)} disabled={busy}>
                      Approve
                    </button>
                    <button className="deny" onClick={() => void approve(a.thread_id ?? "", tc.id, false)} disabled={busy}>
                      Deny
                    </button>
                  </div>
                </div>
              </div>
            ))
          )}
        </div>
      </div>

      <div className="composer-bar">
        <div className="composer-inner">
          <div className="composer-presets">
            {PRESETS.map((p) => (
              <button
                key={p.label}
                className="preset-chip"
                onClick={() => void send(p.text)}
                disabled={busy || running || locked || !sessionId}
                title={p.text}
              >
                {p.label}
              </button>
            ))}
          </div>
          <div className="composer-row">
            <textarea
              className="composer-textarea"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="message the agent…"
              rows={1}
              disabled={locked || !sessionId}
            />
            <button
              className="send-btn"
              onClick={() => void send(message)}
              disabled={busy || running || locked || !sessionId || !message.trim()}
            >
              Send
            </button>
          </div>
        </div>
      </div>
    </div>
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
    <div className="pipeline-bar">
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

function ChatFeed({ events, sentByTurn }: { events: EventRow[]; sentByTurn: Record<string, string> }) {
  const subAgentNames = useMemo(() => deriveSubAgentNames(events), [events]);
  const seenTurns = new Set<string>();

  return (
    <>
      {events.map((row, i) => {
        const nodes = [];
        if (!seenTurns.has(row.turn_id)) {
          seenTurns.add(row.turn_id);
          const text = sentByTurn[row.turn_id];
          if (text) {
            nodes.push(
              <div className="msg-row user" key={`user-${row.turn_id}`}>
                <div className="bubble user-bubble">
                  <p>{text}</p>
                </div>
              </div>
            );
          }
        }
        nodes.push(<EventRowView key={`${row.turn_id}-${i}`} row={row} index={i} events={events} subAgentNames={subAgentNames} />);
        return nodes;
      })}
    </>
  );
}

function EventRowView({
  row,
  index,
  events,
  subAgentNames,
}: {
  row: EventRow;
  index: number;
  events: EventRow[];
  subAgentNames: Map<string, string>;
}) {
  const { event } = row;
  const isSub = Boolean(event.thread_id && event.thread_id !== "main");

  switch (event.type) {
    case "model.message": {
      const toolCalls = event.tool_calls ?? [];
      if (!event.content && toolCalls.length === 0) return null;
      const responses = collectResponses(events, index, toolCalls.length);
      const chips = toolCalls.map((tc, k) => <ToolChip key={tc.id} tc={tc} response={responses[k]} />);

      if (isSub) {
        const name = subAgentNames.get(event.thread_id ?? "");
        return (
          <div className="msg-row sub">
            <div className="bubble sub-bubble">
              <div className="bubble-label">{name ? `subagent · ${name}` : "subagent"}</div>
              {event.content && <p>{event.content}</p>}
              {chips.length > 0 && <div className="tool-chips">{chips}</div>}
            </div>
          </div>
        );
      }
      return (
        <div className="msg-row assistant">
          <div className="bubble assistant-bubble">
            {event.content && <p>{event.content}</p>}
            {chips.length > 0 && <div className="tool-chips">{chips}</div>}
          </div>
        </div>
      );
    }
    case "tool.response":
      // Surfaced inside the originating tool chip's expanded view instead.
      return null;
    case "thread.created":
      return <div className="meta-row">thread created · {shortId(event.thread_id ?? "")}</div>;
    case "sandbox.created":
      return <div className="meta-row">sandbox created</div>;
    case "turn.done":
      return <div className="meta-row">turn done</div>;
    case "tool.approval_required":
      return <div className="meta-row">approval requested</div>;
    default:
      return null;
  }
}

function ToolChip({ tc, response }: { tc: ToolCall; response?: string }) {
  const [open, setOpen] = useState(false);
  const name = tc.function?.name ?? "tool";
  const args = tc.function?.arguments ?? "";

  return (
    <div className={`tool-chip ${open ? "open" : ""}`} onClick={() => setOpen((o) => !o)}>
      <code>
        {name}({open ? args : truncate(args, 80)})
      </code>
      {open && (
        <div className="tool-chip-response">
          {response !== undefined ? `→ ${response}` : "→ (no response captured)"}
        </div>
      )}
    </div>
  );
}

export default Harness;
