# Codebase Submission Evaluator

Grades a submitted codebase ZIP against a course rubric and produces an evidence-backed report out
of 100. See `Codebase Submission Evaluator — Design Spec.md` for the design.

**Status:** all five build steps are implemented and tested (85 offline tests). A full live grade has
run on OpenAI models against a synthetic sample; no live Claude run or real-submission run yet. The
frontend in `frontend/` is written but not yet type-checked or built (its packages are not installed).

## Commands

```sh
uv sync
export ANTHROPIC_API_KEY=...        # or `ant auth login`

# One submission
uv run evaluate intake sub.zip --config configs/repo_evaluator.yaml --out runs/sub      # code only
uv run evaluate judge runs/sub --config configs/repo_evaluator.yaml --criterion architecture_reconstruction   # step-2 check
uv run evaluate grade runs/sub --config configs/repo_evaluator.yaml [--assignment assignment.md]

# Calibration (launch gate)
uv run evaluate calibrate calib_zips/ --human human.csv --config configs/repo_evaluator.yaml --out calib/ \
    --haiku communication [--baseline calib_prev/calibration.json]

# Cohort
uv run evaluate cohort zips/ --config configs/repo_evaluator.yaml --out cohort/ --batch [--template starter.zip]

uv run pytest
```

## Frontend (Grader Console)

`frontend/` is the instructor UI. It reads results through a local API that keeps API keys on
the server:

```sh
uv run evaluate serve                 # API on http://127.0.0.1:8765, serving ./workspace
cd frontend && npm install && npm run dev
```

Point CLI runs at the workspace so the console finds them, e.g.
`uv run evaluate cohort zips/ --config configs/repo_evaluator.yaml --out workspace/cohorts/fall`.
The Grade page uploads one ZIP and grades it in `workspace/submissions/`. The API binds to
localhost and has no login: keep it on your machine.

## Models and providers

Each config fixes which model handles each role (`judge`, `classifier`, `summary`); criteria name a
role. The default is Claude (Sonnet 5.5 / Haiku 5.5). `model_profiles.openai` swaps every role to pinned
OpenAI snapshots; select it with `--models openai` or `EVALUATOR_MODELS=openai` in `.env`. Requests go
to the right provider by model name (`providers.py`); OpenAI calls use the Responses API with
`store=False`. Keys live in a gitignored `.env`:

```
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...        # only needed for Claude models
EVALUATOR_MODELS=openai      # optional default profile
```

Different models need their own calibration: cached results are keyed by model, and calibration must
be rerun for the profile you grade with. Add OpenAI prices under `prices:` in the config for cost reports.

## Pipeline

| Stage | Module | Model |
| --- | --- | --- |
| Safe unzip, inventory, hardened git read, pseudonyms, snapshot, repo map | `intake/` | — |
| Fact sheet (8 extractors + signals) | `facts/`, `pipeline.py` | — |
| Injection-match classification, documentation claims | `judge/integrity.py` | Haiku 5.5 |
| Criterion judges (5 read-only tools, verified citations) | `judge/` | Sonnet 5.5 |
| Re-runs, zero rules, caps, score, confidence | `grading.py`, `scoring/` | — |
| Summary (picks defects by id; cannot add findings) | `judge/summary.py` | Sonnet 5.5 |
| Reports (instructor + student Markdown, JSON) | `report.py` | — |
| Calibration: agreement, stability, Haiku routing, README swap, baseline gate | `calibration.py` | both |
| Cohort: batch rounds, similarity, ranking, order-trend check | `batch.py`, `cohort.py` | — |

Outputs per submission: `runs/<sub>/grades/<judge key>/report.json`, `report_instructor.md`,
`report_student.md`, per-criterion run files, `usage.json` (token log and cost).

## How the judge is kept honest

- **Citations are checked by code**: file exists, range exists, the quoted text is in the range, the
  file is in the criterion's scope, and its evidence type is allowed for the element. "Missing" needs a
  logged zero-hit search. A 4 requires every element found.
- **Failed checks**: the judge gets one in-conversation correction; anything still unverified is
  dropped, and the rating cannot stay above 3 if a 4/4 element lost its evidence.
- **Bias isolation**: code criteria cannot open README/write-up files; they get Haiku-extracted claims
  to verify instead. The README swap test in calibration measures leakage.
- **Severe consequences need two signals**: judgment caps need the fact sheet and the judge to agree;
  the unedited-AI zero needs the phrase search and the judge to agree. Disagreement is reported as a
  defect, not applied. Confirmed injection is flagged (or zeroes the total if the config says so).
- **Re-runs on objective triggers only** (judgment cap or zero rule in play, total within 3 points of a
  cutoff): 2 extra runs, median-low of 3, majority vote for findings.

## Decisions beyond the spec

- **No `temperature`**: the API rejects sampling parameters as of 2026-10-21. Consistency comes from
  pinned models and prompts, byte-stable inputs, cached results and calibration.
- **Structured outputs, not forced tools**: Sonnet 5.5 rejects forced `tool_choice`; the final
  assessment is JSON via `output_config.format`, identical across criteria so the cache is shared.
- **No refusal fallback to another model**: it would break "same model for every candidate". A refusal
  is retried, then the criterion (and so the submission) is "not graded".
- **Median of 3, not 2**, for every re-run trigger.
- **Rounding**: half-up to 2 decimals once; bands are thresholds (89.99 is below 90).
- **Pseudonyms**: people become `person-N`; agents stay labelled (`agent:claude`).
- **Policy changes re-score only**: the cache key covers only what the judge sees, so changing caps,
  bands or weights re-uses every judge run.
- **Ties** are broken by points on the heaviest criteria; remaining ties are shown, not resolved by a
  model call.
- **One rubric:** `configs/repo_evaluator.yaml`, with all level text from the course rubric. The
  A20/B10 configs the suite was built on live in `tests/configs/` as test fixtures only. Synthesis's
  point bands (9–10 … 0–2) map to levels 4…0. Caps other than the 75 AI-disclosure cap come from the
  design spec and can be removed in the config.

## Before launch

1. Run `evaluate judge` on Prakhar's submission and compare with his known ratings
   (`calibration/reference_ratings.csv`).
2. Measure real token use on ~5 submissions (`usage.json`) and replace the cost estimate.
3. Calibrate on ~30 human-graded submissions; tune signal patterns until it passes.
4. Course-owner decisions: the spec's extra caps, injection consequence, similarity consequence,
   data retention, dispute process.
