# Grader Console (frontend)

Instructor UI for the codebase submission evaluator: cohort dashboard, submission reports
(instructor and student views) with an evidence drawer, calibration results, and a page to grade
a ZIP. Originally scaffolded in Lovable; this copy is no longer connected to a Lovable project.

## Run

```sh
# 1. API (from the repository root) — holds the API keys, serves results from ./workspace
uv run evaluate serve

# 2. Frontend (from this folder)
npm install
npm run dev
```

The frontend talks to `VITE_API_URL` (default `http://127.0.0.1:8765`; see `.env.example`).
Without the API it still works on JSON files you load by hand, or on the built-in demo data.

## Checks

```sh
npx tsc --noEmit   # type-check
npm run lint
npm test
npm run build
```

## Layout

- `src/routes/` — pages (file-based routing): `index` (load), `cohorts.*`, `reports.*`,
  `calibration.*`, `grade`
- `src/components/grader/` — shell, status pills, evidence drawer
- `src/lib/` — `api.ts` (grader API client), `load.ts` (file loading, API sync), `store.ts`
  (in-browser store), `types.ts` (report/cohort/calibration shapes), `demo.ts` (demo data)
