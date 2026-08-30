// Serverless proxy: browser -> this function -> private TrueForge tunnel.
function safeParse(text: string): any {
  try {
    return JSON.parse(text);
  } catch {
    return {};
  }
}
// Public demo proxy: forwards sessions/* calls to TRUEFORGE_URL for the contained demo agent.
// No password: the agent is scoped (demo manifest, no repo/file access) and DELETE is blocked below.
export default async function handler(req: any, res: any) {
  res.setHeader("Cache-Control", "no-store");

  const { TRUEFORGE_URL } = process.env;
  if (!TRUEFORGE_URL) {
    res.status(500).json({ error: "Server misconfigured: set TRUEFORGE_URL in Vercel." });
    return;
  }

  // Derive the TrueForge path from the request URL itself: /api/tf/<path>?<qs>
  // vercel.json rewrites /api/tf/<path> -> /api/tf?path=<path>; fall back to the URL path itself
  const reqUrl = new URL(req.url ?? "/", "http://local");
  const rawPath = req.query?.path ?? reqUrl.searchParams.get("path");
  let tail = Array.isArray(rawPath) ? rawPath.join("/") : rawPath ? String(rawPath) : "";
  if (!tail) tail = reqUrl.pathname.replace(/^\/api\/tf\/?/, "");
  tail = tail.replace(/^\/+/, "");
  reqUrl.searchParams.delete("path");
  const qs = reqUrl.searchParams.toString();
  const base = TRUEFORGE_URL.replace(/\/+$/, "");
  const url = `${base}/api/v1/${tail}${qs ? `?${qs}` : ""}`;

  // Public-demo containment: only session routes are reachable (no settings, agents, connectors,
  // sandbox file downloads), and every new session is forced onto the GitHub-less demo agent.
  if (!/^sessions(\/[A-Za-z0-9_.-]+(\/(turns|events)(\/[A-Za-z0-9_.-]+(\/events)?)?)?)?$/.test(tail)) {
    res.status(403).json({ error: "Route not exposed by the public demo proxy." });
    return;
  }
  const method: string = req.method ?? "GET";
  const hasBody = method !== "GET" && method !== "HEAD";
  let payload: any = hasBody ? (typeof req.body === "string" ? safeParse(req.body) : req.body ?? {}) : undefined;
  if (method === "POST" && tail === "sessions") payload = { agent: { name: process.env.DEMO_AGENT || "roolytooly-demo" } };
  if (method === "DELETE") {
    res.status(403).json({ error: "Deletes are not exposed." });
    return;
  }
  const body = hasBody ? JSON.stringify(payload ?? {}) : undefined;

  try {
    const upstream = await fetch(url, {
      method,
      headers: hasBody ? { "content-type": "application/json" } : undefined,
      body,
    });
    const text = await upstream.text();
    const contentType = upstream.headers.get("content-type") ?? "";
    res.status(upstream.status);
    res.setHeader("content-type", contentType || "text/plain");
    res.send(text);
  } catch {
    res.status(502).json({ error: "Upstream request to TrueForge failed." });
  }
}
