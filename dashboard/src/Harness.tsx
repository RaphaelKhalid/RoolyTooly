import { useEffect, useRef, useState } from "react";
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

const PRESETS = [
  "Run the worker on M05_hollow_report_01.",
  "Correction: the printed summary is a cached value; report.json says null. Never report a value you haven't read from the artifact. Compile this into a lesson and take it through transfer and the PR.",
];

const shortId = (id: string) => (id.length > 8 ? id.slice(0, 8) : id);
const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n)}…` : s);

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
              <button key={p} onClick={() => void send(p)} disabled={busy || running} title={p}>
                {truncate(p, 60)}
              </button>
            ))}
            <button onClick={() => void send(message)} disabled={busy || running || !message.trim()}>Send</button>
          </div>
        </div>
      )}

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
