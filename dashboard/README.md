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
