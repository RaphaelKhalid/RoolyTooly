# Evidence dashboard

Static Vite + React + TypeScript dashboard for the benchmark, ledger, transfer, and spend artifacts in the repository. It has no backend and no runtime network dependency. `data.json` is one generated snapshot; the app fetches it at runtime and also bundles it as a `file://` fallback for offline demo use.

## Build

From the repository root:

```bash
python scripts/build_dashboard_data.py
cd dashboard
npm i
npm run build
```

The static output is `dashboard/dist/` and can be deployed to Vercel or previewed locally:

```bash
npm run dev
npm run preview
```

To refresh evidence, rerun the Python command before building. Missing or unavailable artifacts render as `no data`; the dashboard does not substitute placeholder metrics.

## Harness panel

The "Use the agent" section on the site is a password-gated interface to a live `roolytooly` TrueForge session, proxied through `api/tf/[...path].ts` so the browser never talks to TrueForge directly.

Set these two environment variables in the Vercel project (Project Settings → Environment Variables):

- `HARNESS_PASSWORD` — the password visitors must send in the `x-harness-password` header (entered once in the panel and cached in `sessionStorage`).
- `TRUEFORGE_URL` — the base URL of the private tunnel in front of the TrueForge server, e.g. `https://<random>.trycloudflare.com`. Requests are forwarded to `${TRUEFORGE_URL}/api/v1/<path>`.

Neither value should ever be committed to the repo.

On the host running TrueForge, expose it with:

```bash
cloudflared tunnel --url http://localhost:8790
```

and copy the printed `https://*.trycloudflare.com` URL into `TRUEFORGE_URL`. Quick tunnels rotate their hostname on restart, so update the env var (and redeploy) whenever the tunnel is restarted.
