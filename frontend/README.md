# EuroGrid frontend

React 19 + TypeScript + Vite + ECharts. The app has seven pages behind a sidebar with hash routing (`src/App.tsx`), and every page reads only from the FastAPI service. See the [project README](../README.md) for the full picture.

| Folder | Contents |
|---|---|
| `src/pages/` | One file per page: Market Overview, Analytics, Investigations, Planner, Event Replay, Data Operations, Scenario Lab |
| `src/components/` | Shared loading/empty/error/stale states, error boundary, chart wrapper, chaos-mode panel |
| `src/lib/` | API client and formatters, ECharts setup, household planner types, hash navigation |

```powershell
npm ci
```

```powershell
npm run dev
```

```powershell
npm test
```

```powershell
npm run lint
```

```powershell
npm run build
```

- `npm run dev` starts the app on http://localhost:5173 and expects the API on http://127.0.0.1:8000. Set `VITE_API_BASE_URL` to point it elsewhere.
- `npm test` runs the Vitest unit tests.
- `npm run lint` runs oxlint.
- `npm run build` type-checks and builds; the pages are lazy-loaded chunks.
