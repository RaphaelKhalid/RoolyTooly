// Client for the /api/tf proxy in front of the private TrueForge tunnel.

const PASSWORD_KEY = "tf_harness_password";

// The demo is open: no password is required, so the stored value is only kept for compatibility.
const OPEN_ACCESS = "open";

export function getStoredPassword(): string {
  try {
    return sessionStorage.getItem(PASSWORD_KEY) || OPEN_ACCESS;
  } catch {
    return OPEN_ACCESS;
  }
}

export function setStoredPassword(pw: string): void {
  try {
    sessionStorage.setItem(PASSWORD_KEY, pw);
  } catch {
    // sessionStorage unavailable (e.g. private mode) — password just won't persist
  }
}

export class HarnessAuthError extends Error {}

export async function tfFetch(path: string, init: RequestInit = {}): Promise<any> {
  const headers = new Headers(init.headers);
  headers.set("x-harness-password", getStoredPassword());
  if (init.body && !headers.has("content-type")) headers.set("content-type", "application/json");

  const res = await fetch(`/api/tf/${path}`, { ...init, headers });
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }

  if (res.status === 401) throw new HarnessAuthError("401 Unauthorized — wrong harness password.");
  if (!res.ok) {
    const message = data && typeof data === "object" && "error" in data ? data.error : `Request failed (${res.status})`;
    throw new Error(String(message));
  }
  return data;
}

export type TurnStatus = "running" | "done" | "cancelled" | "error";

export type ToolCall = { id: string; function?: { name?: string; arguments?: string } };

export type RequiredAction = {
  type: string;
  thread_id?: string;
  tool_calls?: ToolCall[];
};

export type TurnState = {
  status: TurnStatus;
  required_actions?: RequiredAction[];
};

export type Turn = {
  id: string;
  state: TurnState;
};

export type SessionEventType =
  | "model.message"
  | "tool.response"
  | "thread.created"
  | "tool.approval_required"
  | "turn.done"
  | "sandbox.created"
  | string;

export type SessionEvent = {
  type: SessionEventType;
  id?: string;
  content?: string | null;
  thread_id?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string; // on tool.response: the call this response answers
};

export type EventRow = {
  turn_id: string;
  event: SessionEvent;
};

export async function createSession(): Promise<string> {
  const res = await tfFetch("sessions", {
    method: "POST",
    body: JSON.stringify({ agent: { name: "roolytooly" } }),
  });
  return res.data.id;
}

export async function startTurn(sessionId: string, content: string): Promise<string> {
  const res = await tfFetch(`sessions/${sessionId}/turns`, {
    method: "POST",
    body: JSON.stringify({ input: [{ type: "user.message", content }], stream: false }),
  });
  return res.data.id;
}

export async function getTurn(sessionId: string, turnId: string): Promise<Turn> {
  const res = await tfFetch(`sessions/${sessionId}/turns/${turnId}`, { method: "GET" });
  return res.data;
}

export async function resumeApproval(
  sessionId: string,
  threadId: string,
  toolCallId: string,
  allow: boolean,
  previousTurnId: string
): Promise<string> {
  const res = await tfFetch(`sessions/${sessionId}/turns`, {
    method: "POST",
    body: JSON.stringify({
      input: [
        {
          type: "user.tool_approval",
          thread_id: threadId,
          tool_call_id: toolCallId,
          approval: { status: allow ? "allow" : "deny" },
        },
      ],
      stream: false,
      previous_turn_id: previousTurnId,
    }),
  });
  return res.data.id;
}

export async function sessionEvents(sessionId: string): Promise<EventRow[]> {
  const res = await tfFetch(`sessions/${sessionId}/events`, { method: "GET" });
  return res.data ?? [];
}
