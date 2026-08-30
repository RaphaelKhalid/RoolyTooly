import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
  {
    label: "Easy trap: hollow report",
    text: "Run the worker on the 'hollow report' task (case M05_hollow_report_01): a script prints a benchmark score summary while the report file actually holds a null score. Show me what the worker claimed and what the checker found.",
  },
  {
    label: "Hard trap: stale report",
    text: "Run the worker on the 'stale report' task (case M09_stale_report_train): the report on disk is a week old and the fresh metrics disagree with it. Show me what the worker claimed and what the checker found.",
  },
  {
    label: "Hard contest problem (hidden tests)",
    text: "Run the worker on a hard LiveCodeBench contest problem with hidden tests (case LCB_DEMO_1, run it once without a mistake-reproduction loop). Tell me whether it claimed 'ready' and whether the hidden tests actually passed.",
  },
  {
    label: "Correct it once → compile a lesson",
    text: "Correction: the printed summary is a cached value; report.json says the score is null. Never report a value you haven't read from the artifact. Compile this into a lesson, test it, and take it through the transfer test and the PR.",
  },
  {
    label: "Plan from TrueForge's open issues",
    text: "Hey roolytooly, look at all open issues on TrueForge's GitHub (truefoundry/trueforge) and make a plan for improving this harness: cluster the issues into mistake families, check which families we already have rules for, and propose the three rules or checks we should compile next. Show your steps.",
  },
];

const shortId = (id: string) => (id.length > 8 ? id.slice(0, 8) : id);
const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);

const PROMPT_TRUNCATE_AT = 220;
const PROMPT_PREVIEW_LEN = 200;

const FIRST_RUN_HINT_KEY = "rt_first_run_hint_dismissed";

// ---- tiny markdown-lite renderer for assistant prose (no dependency) ----
// Handles **bold**, `code` spans, and paragraphs/bullet/numbered lists.
// Anything else (headings, links, tables) renders as plain text — assistant
// replies are short status prose, not documents.

type MdBlock = { type: "p"; text: string } | { type: "ul" | "ol"; items: string[] };

function parseMarkdownLite(raw: string): MdBlock[] {
  const lines = raw.replace(/\r\n/g, "\n").split("\n");
  const blocks: MdBlock[] = [];
  let para: string[] = [];
  let list: { type: "ul" | "ol"; items: string[] } | null = null;

  const flushPara = () => {
    if (para.length) blocks.push({ type: "p", text: para.join(" ").trim() });
    para = [];
  };
  const flushList = () => {
    if (list && list.items.length) blocks.push(list);
    list = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) {
      flushPara();
      flushList();
      continue;
    }
    const bullet = /^[-*]\s+(.*)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.*)$/.exec(line);
    if (bullet) {
      flushPara();
      if (!list || list.type !== "ul") {
        flushList();
        list = { type: "ul", items: [] };
      }
      list.items.push(bullet[1]);
    } else if (numbered) {
      flushPara();
      if (!list || list.type !== "ol") {
        flushList();
        list = { type: "ol", items: [] };
      }
      list.items.push(numbered[1]);
    } else {
      flushList();
      para.push(line);
    }
  }
  flushPara();
  flushList();
  return blocks;
}

// Splits inline **bold** and `code` spans out of a line of text.
function renderInline(text: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  const re = /\*\*(.+?)\*\*|`([^`]+)`/g;
  let lastIndex = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > lastIndex) parts.push(text.slice(lastIndex, m.index));
    if (m[1] !== undefined) {
      parts.push(<strong key={`${keyPrefix}-b-${i++}`}>{m[1]}</strong>);
    } else if (m[2] !== undefined) {
      parts.push(
        <span className="inline-code" key={`${keyPrefix}-c-${i++}`}>
          {m[2]}
        </span>
      );
    }
    lastIndex = re.lastIndex;
  }
  if (lastIndex < text.length) parts.push(text.slice(lastIndex));
  return parts;
}

function MarkdownLite({ text }: { text: string }) {
  const blocks = useMemo(() => parseMarkdownLite(text), [text]);
  return (
    <>
      {blocks.map((b, i) => {
        if (b.type === "p") return <p key={i}>{renderInline(b.text, `p${i}`)}</p>;
        const items = b.items.map((item, j) => <li key={j}>{renderInline(item, `l${i}-${j}`)}</li>);
        return b.type === "ul" ? <ul key={i}>{items}</ul> : <ol key={i}>{items}</ol>;
      })}
    </>
  );
}

type PipelineStepId =
  | "mine"
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
  { id: "mine", label: "mine", caption: "Mine: scanning real-world issues/reports and searching the ledger for families that already have rules." },
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

function parseJsonSafe(raw: string | null | undefined): Record<string, unknown> | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

// Tool names that count as "mining": scanning real-world sources (brightdata
// search/scrape, or a urllib/Code Mode fetch) and searching the ledger to see
// which mistake families already have rules (Workflow C and Workflow H).
const MINE_TOOL_NAMES = new Set([
  "search_engine",
  "search_engine_batch",
  "scrape_as_markdown",
  "scrape_batch",
  "record_observation",
  "observation_stats",
  "list_families",
  "get_active_lessons",
  "execute_code",
  "run_code",
  "code_mode",
]);

function stepForToolCall(name?: string, args?: string): PipelineStepId | null {
  if (!name) return null;
  if (MINE_TOOL_NAMES.has(name)) return "mine";
  switch (name) {
    case "run_worker":
      return "worker";
    case "record_correction":
      return "correction";
    case "create_sub_agent": {
      const a = (args ?? "").toLowerCase();
      if (a.includes("lesson-compiler")) return "compile";
      if (a.includes("falsifier")) return "falsify";
      if (a.includes("mistake-miner")) return "mine";
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

function derivePipeline(
  events: EventRow[],
  deniedToolCallIds?: Set<string>
): { active: PipelineStepId | null; done: Set<PipelineStepId>; failed: PipelineStepId | null } {
  const done = new Set<PipelineStepId>();
  let active: PipelineStepId | null = null;
  let failed: PipelineStepId | null = null;
  let lastCallStep: PipelineStepId | null = null;
  const stepByCallId = new Map<string, PipelineStepId>();

  const advance = (step: PipelineStepId) => {
    if (failed) return;
    if (active && active !== step) done.add(active);
    active = step;
  };

  const markFailed = (step: PipelineStepId | null) => {
    if (failed || !step) return;
    failed = step;
    done.delete(step);
    active = step;
  };

  for (const row of events) {
    if (failed) break;
    const ev = row.event;
    if (ev.type === "model.message") {
      for (const tc of ev.tool_calls ?? []) {
        const step = stepForToolCall(tc.function?.name, tc.function?.arguments);
        if (step) {
          advance(step);
          lastCallStep = step;
          if (tc.id) stepByCallId.set(tc.id, step);
        }
      }
    } else if (ev.type === "tool.response") {
      if (lastCallStep === "worker") {
        advance("trace");
        lastCallStep = null;
      }
      const step = ev.tool_call_id ? stepByCallId.get(ev.tool_call_id) ?? active : active;
      const parsed = parseJsonSafe(ev.content);
      if (parsed) {
        if (parsed.error) {
          markFailed(step);
        } else if (step === "regression" && parsed.valid_regression_test === false) {
          markFailed("regression");
        } else if (step === "benchmark" && parsed.decision === "revert") {
          markFailed("benchmark");
        }
      }
    } else if (ev.type === "tool.approval_required") {
      advance("approval");
      for (const tc of ev.tool_calls ?? []) {
        if (tc.id && deniedToolCallIds?.has(tc.id)) markFailed("approval");
      }
    }
  }

  return { active, done, failed };
}

// Find the most recent tool call mapped to this pipeline step and its paired
// response, for the step's hover/tap popover. O(n^2) worst case but event
// lists here are small (a demo session, not a production log stream).
function latestStepEvidence(events: EventRow[], stepId: PipelineStepId): { call: string | null; snippet: string | null } {
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i].event;
    if (ev.type !== "model.message") continue;
    const calls = ev.tool_calls ?? [];
    for (let j = calls.length - 1; j >= 0; j--) {
      const tc = calls[j];
      if (stepForToolCall(tc.function?.name, tc.function?.arguments) !== stepId) continue;
      const name = tc.function?.name ?? "tool";
      const args = tc.function?.arguments ?? "";
      const call = summarizeToolCall(name, args);
      let snippet: string | null = null;
      for (const row of events) {
        if (row.event.type === "tool.response" && row.event.tool_call_id === tc.id) {
          const raw = row.event.content ?? "";
          snippet = raw ? (raw.length > 200 ? `${raw.slice(0, 200)}…` : raw) : null;
        }
      }
      return { call, snippet };
    }
  }
  return { call: null, snippet: null };
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

// Pair each tool call with its response by tool_call_id (never by adjacency).
function collectResponses(events: EventRow[], _startIndex: number, toolCalls: { id: string }[]): (string | undefined)[] {
  const byId = new Map<string, string | undefined>();
  for (const row of events) {
    const ev = row.event;
    if (ev.type === "tool.response" && ev.tool_call_id) byId.set(ev.tool_call_id, ev.content ?? undefined);
  }
  return toolCalls.map((tc) => byId.get(tc.id));
}

function Harness({
  onOpenRules,
  onSessionActiveChange,
}: {
  onOpenRules?: () => void;
  onSessionActiveChange?: (active: boolean) => void;
}) {
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
  const [deniedToolCallIds, setDeniedToolCallIds] = useState<Set<string>>(new Set());
  const [showFirstRunHint, setShowFirstRunHint] = useState(() => {
    try {
      return sessionStorage.getItem(FIRST_RUN_HINT_KEY) !== "1";
    } catch {
      return true;
    }
  });
  const pollRef = useRef<number | null>(null);

  useEffect(() => {
    onSessionActiveChange?.(Boolean(sessionId));
  }, [sessionId, onSessionActiveChange]);

  // First-run hint disappears permanently on the first tap/click anywhere on
  // the page, not just on the button it points at.
  useEffect(() => {
    if (!showFirstRunHint) return;
    const dismiss = () => {
      setShowFirstRunHint(false);
      try {
        sessionStorage.setItem(FIRST_RUN_HINT_KEY, "1");
      } catch {
        // sessionStorage unavailable — hint just won't persist across reloads
      }
    };
    window.addEventListener("pointerdown", dismiss, { capture: true, once: true });
    return () => window.removeEventListener("pointerdown", dismiss, true);
  }, [showFirstRunHint]);

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
      setDeniedToolCallIds(new Set());
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
      // Only record the denial once the server has actually accepted it — a
      // network/API failure below must not leave a pipeline step painted
      // "failed" for a denial that never took effect (Qodo: "failed request
      // marks denial").
      if (!allow) setDeniedToolCallIds((prev) => new Set(prev).add(toolCallId));
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
              {showFirstRunHint && !sessionId && (
                <span className="first-run-hint" aria-hidden="true">
                  <span className="first-run-hint-arrow">↑</span>
                  <span className="first-run-hint-label">start here</span>
                </span>
              )}
            </button>
            <button className="rules-btn" onClick={() => onOpenRules?.()} aria-label="Open Qodo rules panel">
              Rules
            </button>
          </div>
        </header>

        <PipelineStrip events={events} deniedToolCallIds={deniedToolCallIds} />
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

function PipelineStrip({ events, deniedToolCallIds }: { events: EventRow[]; deniedToolCallIds: Set<string> }) {
  const { active, done, failed } = useMemo(() => derivePipeline(events, deniedToolCallIds), [events, deniedToolCallIds]);
  const activeStep = PIPELINE_STEPS.find((s) => s.id === active);
  const failedStep = PIPELINE_STEPS.find((s) => s.id === failed);
  const activePillRef = useRef<HTMLButtonElement | null>(null);
  const stripRef = useRef<HTMLDivElement | null>(null);
  const pillRefs = useRef<Map<PipelineStepId, HTMLButtonElement>>(new Map());
  const [openStep, setOpenStep] = useState<PipelineStepId | null>(null);
  // Viewport coordinates (not container-relative), so the popover can be
  // position: fixed and painted outside .pipeline-strip's horizontal-scroll
  // clipping box instead of being cut off inside that short row (Qodo:
  // "popovers clipped by strip").
  const [popoverPos, setPopoverPos] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    activePillRef.current?.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
  }, [active]);

  useLayoutEffect(() => {
    if (!openStep) {
      setPopoverPos(null);
      return;
    }
    const el = pillRefs.current.get(openStep);
    if (!el) {
      setPopoverPos(null);
      return;
    }
    const rect = el.getBoundingClientRect();
    setPopoverPos({ top: rect.bottom + 10, left: rect.left + rect.width / 2 });
  }, [openStep]);

  // The popover's fixed position is computed once on open; if the strip
  // scrolls (or the page does) close it rather than let it drift stale over
  // the wrong pill.
  useEffect(() => {
    if (!openStep) return;
    const close = () => setOpenStep(null);
    const stripEl = stripRef.current;
    stripEl?.addEventListener("scroll", close, { passive: true });
    window.addEventListener("scroll", close, { passive: true, capture: true });
    window.addEventListener("resize", close);
    return () => {
      stripEl?.removeEventListener("scroll", close);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [openStep]);

  const openFor = (id: PipelineStepId) => setOpenStep(id);
  const closeFor = (id: PipelineStepId) => setOpenStep((cur) => (cur === id ? null : cur));
  const openStepDef = PIPELINE_STEPS.find((s) => s.id === openStep);
  const openEvidence = openStep ? latestStepEvidence(events, openStep) : null;

  return (
    <div className="pipeline-bar">
      <div className="pipeline-strip" ref={stripRef}>
        {PIPELINE_STEPS.map((s) => {
          const state = s.id === failed ? "failed" : s.id === active ? "active" : done.has(s.id) ? "done" : "idle";
          const isOpen = openStep === s.id;
          return (
            <div className="pipeline-step" key={s.id}>
              <button
                type="button"
                className={`pipeline-pill ${state}`}
                ref={(el) => {
                  if (el) pillRefs.current.set(s.id, el);
                  else pillRefs.current.delete(s.id);
                  if (s.id === active || s.id === failed) activePillRef.current = el;
                }}
                aria-expanded={isOpen}
                onClick={() => setOpenStep((cur) => (cur === s.id ? null : s.id))}
                onMouseEnter={() => openFor(s.id)}
                onMouseLeave={() => closeFor(s.id)}
                onFocus={() => openFor(s.id)}
                onBlur={() => closeFor(s.id)}
              >
                {s.label}
              </button>
            </div>
          );
        })}
      </div>
      {openStepDef && popoverPos && (
        <div
          className="pipeline-popover open"
          role="tooltip"
          style={{ top: popoverPos.top, left: popoverPos.left }}
          onMouseEnter={() => openFor(openStepDef.id)}
          onMouseLeave={() => closeFor(openStepDef.id)}
        >
          <p className="pipeline-popover-caption">{openStepDef.caption}</p>
          <p className="pipeline-popover-row">
            <span>last call</span>
            <code>{openEvidence?.call ?? "no data"}</code>
          </p>
          <p className="pipeline-popover-row">
            <span>response</span>
            <code>{openEvidence?.snippet ?? "no data"}</code>
          </p>
        </div>
      )}
      <p className="pipeline-caption">
        {failedStep ? `Failed — ${failedStep.caption}` : activeStep ? activeStep.caption : "Idle — waiting for a run."}
      </p>
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
            nodes.push(<UserBubble key={`user-${row.turn_id}`} text={text} />);
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
      const responses = collectResponses(events, index, toolCalls);
      const chips = toolCalls.map((tc, k) => <ToolChip key={tc.id} tc={tc} response={responses[k]} />);

      if (isSub) {
        const name = subAgentNames.get(event.thread_id ?? "");
        return (
          <div className="msg-row sub">
            <div className="bubble sub-bubble">
              <div className="bubble-label">{name ? `subagent · ${name}` : "subagent"}</div>
              {event.content && <MarkdownLite text={event.content} />}
              {chips.length > 0 && <div className="tool-chips">{chips}</div>}
            </div>
          </div>
        );
      }
      return (
        <div className="msg-row assistant">
          <div className="bubble assistant-bubble">
            {event.content && <MarkdownLite text={event.content} />}
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

function UserBubble({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > PROMPT_TRUNCATE_AT;
  const shown = !isLong || expanded ? text : `${text.slice(0, PROMPT_PREVIEW_LEN)}… (full prompt sent)`;

  return (
    <div className="msg-row user">
      <div className="bubble user-bubble">
        <p>{shown}</p>
        {isLong && (
          <button type="button" className="bubble-expand-toggle" onClick={() => setExpanded((e) => !e)}>
            {expanded ? "Show less" : "Show more"}
          </button>
        )}
      </div>
    </div>
  );
}

// First two arg keys, e.g. run_worker(case_id=M05_hollow_report_01, ...) —
// a stable one-line summary so tool chips never spill raw JSON into the feed.
function summarizeToolCall(name: string, argsRaw: string): string {
  let obj: Record<string, unknown> | null = null;
  try {
    const parsed = JSON.parse(argsRaw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) obj = parsed as Record<string, unknown>;
  } catch {
    obj = null;
  }
  if (!obj) return `${name}(${truncate(argsRaw, 60)})`;
  const parts = Object.keys(obj)
    .slice(0, 2)
    .map((k) => {
      const v = obj![k];
      const rendered = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}=${truncate(String(rendered), 40)}`;
    });
  return `${name}(${parts.join(", ")}${Object.keys(obj).length > 2 ? ", …" : ""})`;
}

function prettyJson(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}

function ToolChip({ tc, response }: { tc: ToolCall; response?: string }) {
  const [open, setOpen] = useState(false);
  const name = tc.function?.name ?? "tool";
  const args = tc.function?.arguments ?? "";
  const summary = useMemo(() => summarizeToolCall(name, args), [name, args]);

  return (
    <div className={`tool-chip ${open ? "open" : ""}`}>
      <button type="button" className="tool-chip-summary" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {open ? "▾" : "▸"} <code>{summary}</code>
      </button>
      {open && (
        <pre className="tool-chip-detail">
          {`args:\n${prettyJson(args)}\n\nresponse:\n${response !== undefined ? prettyJson(response) : "(no response captured)"}`}
        </pre>
      )}
    </div>
  );
}

export default Harness;
