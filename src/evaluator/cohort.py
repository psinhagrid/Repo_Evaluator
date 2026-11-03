"""Stage 5: grade a cohort and build the cohort view.

    zips -> intake (code) -> grade (live: 10-20 at a time, or batch API rounds)
         -> similarity (code) -> cohort view (code)

Every candidate is graded with the same config, prompts and models; the
cohort view checks that scores do not trend with grading order.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import random
import re
import statistics
from collections import Counter
from pathlib import Path

from .batch import BatchAPI, drive_rounds
from .grading import GradeOptions, SubmissionGrader, grade_submission
from .intake.snapshot import Snapshot
from .llm import LLM
from .pipeline import run_intake
from .report import render_markdown
from .scoring.config import RubricConfig

SHINGLE = 7
CODE_EXT = (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".rb", ".sh", ".ipynb")
DOC_EXT = (".md", ".markdown", ".rst", ".txt")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\sA-Za-z0-9_]")
MIN_SHINGLES = 30  # below this, similarity is not meaningful
TREND_PERMUTATIONS = 2000


# -- intake -------------------------------------------------------------------------

def submission_name(zip_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(zip_path).stem)[:60]
    return f"{stem}-{hashlib.sha256(str(zip_path).encode()).hexdigest()[:6]}"


def intake_all(zips: list[Path], config: RubricConfig, root: Path) -> dict[str, Path]:
    out = {}
    for z in sorted(zips):
        name = submission_name(z)
        run_dir = Path(root) / name
        if not (run_dir / "intake.json").exists():
            run_intake(z, config, run_dir)
        out[name] = run_dir
    return out


# -- grading ----------------------------------------------------------------------------

async def grade_live(run_dirs: dict[str, Path], config: RubricConfig, llm: LLM, submissions_at_once: int = 10,
                     options: GradeOptions | None = None) -> dict[str, dict]:
    sem = asyncio.Semaphore(submissions_at_once)

    async def one(name, d):
        async with sem:
            return name, await grade_submission(d, config, llm, options)

    return dict(await asyncio.gather(*(one(n, d) for n, d in run_dirs.items())))


async def grade_batch(run_dirs: dict[str, Path], config: RubricConfig, api: BatchAPI, log_dir: Path,
                      options: GradeOptions | None = None) -> dict[str, dict]:
    """Same decisions as grade_submission, phase by phase across the cohort, one batch per round."""
    options = options or GradeOptions(cache_ttl="1h")
    log = Path(log_dir) / "batches.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    graders = {n: SubmissionGrader(d, config, options) for n, d in run_dirs.items()}
    reports: dict[str, dict] = {}
    active = {}
    for n, g in graders.items():
        if not g.ok:
            reports[n] = g.not_graded_report(g.intake.get("reason", "intake did not complete"))
        elif (cached := g.cached_report()) is not None:
            reports[n] = cached
        else:
            active[n] = g
    index = {n: i for i, n in enumerate(sorted(active))}

    # Phase A: integrity pre-checks (Haiku).
    pre_sessions, pre_bundles, pres = {}, {}, {}
    for n, g in active.items():
        pre = g.cached_pre()
        if pre is not None:
            pres[n] = pre
            continue
        bundle = g.pre_sessions()
        pre_bundles[n] = bundle
        for kind in ("injection", "claims"):
            if bundle[kind]:
                pre_sessions[f"s{index[n]}-{kind[:3]}"] = bundle[kind]
    await drive_rounds(api, pre_sessions, log, "pre")
    for n, bundle in pre_bundles.items():
        pres[n] = active[n].finish_pre(bundle)

    # Phase B: first run of every criterion.
    runs: dict[str, dict[str, list]] = {n: {} for n in active}

    async def run_phase(wanted: dict[str, list[tuple[str, int]]], label: str):
        sessions, owners = {}, {}
        for n, pairs in wanted.items():
            g = active[n]
            for cid, run in pairs:
                cached = g.cached_run(cid, run)
                if cached:
                    runs[n].setdefault(cid, []).append(cached)
                    continue
                key = f"s{index[n]}-c{[c.id for c in config.criteria].index(cid)}-r{run}"
                sessions[key] = g.criterion_session(cid, run, pres[n])
                owners[key] = n
        await drive_rounds(api, sessions, log, label)
        for key, s in sessions.items():
            runs[owners[key]].setdefault(s.c.id, []).append(active[owners[key]].save_run(s))

    await run_phase({n: [(c.id, 0) for c in config.criteria] for n in active}, "criteria")

    # Phase C: targeted re-runs where an objective trigger fired.
    plans = {}
    for n, g in active.items():
        if g.failed_reason(runs[n]):
            continue
        plan = g.rerun_plan(runs[n])
        if plan:
            plans[n] = [(cid, i) for cid in sorted(plan) for i in range(1, 1 + g.options.extra_runs)]
    if plans:
        await run_phase(plans, "reruns")
    for n in active:
        runs[n] = {cid: sorted(rs, key=lambda r: r.run_index) for cid, rs in runs[n].items()}

    # Phase D: summary calls.
    summary_sessions, summary_out = {}, {}
    for n, g in active.items():
        if g.failed_reason(runs[n]):
            continue
        call, cached = g.summary_session(runs[n])
        if call is None:
            summary_out[n] = cached
        else:
            summary_sessions[f"s{index[n]}-sum"] = (n, call)
    await drive_rounds(api, {k: c for k, (_, c) in summary_sessions.items()}, log, "summary")
    for _, (n, call) in summary_sessions.items():
        summary_out[n] = active[n].finish_summary(call, runs[n])

    for n, g in active.items():
        reports[n] = g.finalize(runs[n], pres[n], summary_out.get(n))
    return reports


# -- similarity -------------------------------------------------------------------------

def _shingles(text: str, k: int = SHINGLE) -> set[int]:
    # Stable hash (not Python's per-process hash) so results are reproducible across runs.
    toks = TOKEN_RE.findall(text.lower())
    return {int.from_bytes(hashlib.blake2b(" ".join(toks[i:i + k]).encode(), digest_size=8).digest(), "big")
            for i in range(max(0, len(toks) - k + 1))}


def fingerprints(run_dir: Path) -> dict[str, set[int]]:
    snap = Snapshot.load(Path(run_dir) / "snapshot")
    code, docs = [], []
    for p in sorted(snap.files):
        low = p.lower()
        if low.endswith(CODE_EXT):
            code.append("\n".join(snap.lines(p)))
        elif low.endswith(DOC_EXT):
            docs.append("\n".join(snap.lines(p)))
    return {"code": _shingles("\n".join(code)), "writeup": _shingles("\n".join(docs))}


def similarity(run_dirs: dict[str, Path], threshold: float, template: Path | None = None) -> list[dict]:
    """Pairwise Jaccard similarity of code and write-up shingles, minus any starter template."""
    prints = {}
    for n, d in run_dirs.items():
        if (Path(d) / "snapshot" / "index.json").exists():
            prints[n] = fingerprints(d)
    base = fingerprints(template) if template else {"code": set(), "writeup": set()}
    for fp in prints.values():
        for kind in fp:
            fp[kind] -= base[kind]
    names = sorted(prints)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for kind in ("code", "writeup"):
                x, y = prints[a][kind], prints[b][kind]
                if len(x) < MIN_SHINGLES or len(y) < MIN_SHINGLES:
                    continue
                j = len(x & y) / len(x | y)
                if j >= threshold:
                    out.append({"a": a, "b": b, "kind": kind, "score": round(j, 3)})
    return sorted(out, key=lambda m: -m["score"])


def attach_similarity(reports: dict[str, dict], run_dirs: dict[str, Path], matches: list[dict]) -> None:
    """Record matches in each report's integrity section (instructor view) and re-render."""
    by: dict[str, list[dict]] = {}
    for m in matches:
        by.setdefault(m["a"], []).append({"other": reports.get(m["b"], {}).get("submission_sha256") or m["b"],
                                          "other_name": m["b"], "kind": m["kind"], "score": m["score"]})
        by.setdefault(m["b"], []).append({"other": reports.get(m["a"], {}).get("submission_sha256") or m["a"],
                                          "other_name": m["a"], "kind": m["kind"], "score": m["score"]})
    for n, r in reports.items():
        if r.get("status") != "graded":
            continue
        r["integrity"]["similarity"] = by.get(n, [])
        d = Path(run_dirs[n]) / "grades" / r["judge_key"]
        (d / "report.json").write_text(json.dumps(r, indent=2, ensure_ascii=False, default=str))
        (d / "report_instructor.md").write_text(render_markdown(r, "instructor"))


# -- cohort view ---------------------------------------------------------------------------

def _spearman(xs: list[float], ys: list[float]) -> float:
    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def order_trend(scores_in_order: list[float], seed: int = 0) -> dict:
    """Does score trend with grading order? Spearman rho with a permutation p-value."""
    n = len(scores_in_order)
    if n < 8:
        return {"n": n, "rho": None, "p": None, "flag": False, "note": "too few submissions"}
    order = list(range(n))
    rho = _spearman(order, scores_in_order)
    rng = random.Random(seed)
    ys = list(scores_in_order)
    hits = 0
    for _ in range(TREND_PERMUTATIONS):
        rng.shuffle(ys)
        if abs(_spearman(order, ys)) >= abs(rho) - 1e-12:
            hits += 1
    p = (hits + 1) / (TREND_PERMUTATIONS + 1)
    return {"n": n, "rho": round(rho, 3), "p": round(p, 4), "flag": p < 0.05}


def cohort_view(reports: dict[str, dict], config: RubricConfig, order: list[str], matches: list[dict]) -> dict:
    graded = {n: r for n, r in reports.items() if r.get("status") == "graded"}
    tie_order = [c.id for c in sorted(config.criteria, key=lambda c: (-c.weight, c.id))]

    def key(n):
        r = graded[n]
        pts = {s["criterion"]: s["points"] for s in r["sections"]}
        return (-r["score"]["final"], *(-pts[c] for c in tie_order))

    ranked = sorted(graded, key=lambda n: (key(n), n))
    finals = [graded[n]["score"]["final"] for n in ranked]
    rows = []
    for pos, n in enumerate(ranked):
        r = graded[n]
        f = r["score"]["final"]
        below, equal = sum(x < f for x in finals), sum(x == f for x in finals)
        tied_full = [m for m in ranked if m != n and key(m) == key(n)]
        rows.append({
            "rank": pos + 1, "submission": n, "sha": r["submission_sha256"][:12], "final": f,
            "total": r["score"]["total"], "band": r["score"]["band"],
            "percentile": round(100 * (below + 0.5 * equal) / len(finals), 1),
            "caps": [c["cap_id"] for c in r["score"]["caps"] if c["status"] == "applied"],
            "binding_cap": r["score"].get("binding_cap"),
            "confidence": r["confidence"]["level"],
            "tied_with": tied_full,
            "ratings": {s["criterion"]: s["rating"] for s in r["sections"]},
        })

    crit_stats = {}
    for c in config.criteria:
        vals = [row["ratings"][c.id] for row in rows]
        if vals:
            crit_stats[c.id] = {"mean": round(statistics.fmean(vals), 2), "median": statistics.median(vals),
                                "distribution": dict(sorted(Counter(vals).items()))}

    gaps = Counter()
    for r in graded.values():
        for s in r["sections"]:
            for e in s["assessment"]["elements"]:
                if e["status"] != "found":
                    gaps[f"{s['criterion']}.{e['id']} ({e['status']})"] += 1
    caps = Counter(c for row in rows for c in row["caps"])
    hygiene = Counter()
    for r in graded.values():
        integ = r["integrity"]
        for k in ("forbidden_files", "secret_matches", "placeholders", "leftover_agent_text"):
            if integ[k]:
                hygiene[k] += 1

    in_order = [graded[n]["score"]["final"] for n in order if n in graded]
    return {
        "assignment": config.assignment, "policy_version": config.policy_version,
        "submissions": len(reports), "graded": len(graded),
        "not_graded": {n: r.get("reason") for n, r in reports.items() if r.get("status") != "graded"},
        "ranking": rows,
        "bands": dict(Counter(row["band"] for row in rows)),
        "criteria": crit_stats,
        "common_gaps": gaps.most_common(15),
        "caps_applied": dict(caps),
        "hygiene": dict(hygiene),
        "order_trend": order_trend(in_order),
        "similarity": matches,
        "cost_usd": round(sum(r.get("cost", {}).get("total_usd", 0) for r in reports.values()), 2),
    }


def render_cohort(view: dict) -> str:
    out = [f"# Cohort — {view['assignment']} (policy {view['policy_version']})", "",
           f"{view['graded']} graded of {view['submissions']} · cost ${view['cost_usd']:.2f}", ""]
    t = view["order_trend"]
    if t.get("rho") is not None:
        out.append(f"Grading-order check: Spearman rho {t['rho']:+.3f}, p={t['p']:.3f} — "
                   + ("**scores trend with grading order; investigate**" if t["flag"] else "no trend"))
        out.append("")
    out += ["| Rank | Submission | Final | Band | Percentile | Caps | Confidence | Tied with |",
            "| ---: | --- | ---: | --- | ---: | --- | --- | --- |"]
    for r in view["ranking"]:
        caps = ", ".join(r["caps"]) + (f" (binding: {r['binding_cap']})" if r["binding_cap"] else "")
        out.append(f"| {r['rank']} | {r['submission']} | {r['final']:g} | {r['band'] or ''} | {r['percentile']:g} | "
                   f"{caps} | {r['confidence']} | {', '.join(r['tied_with'])} |")
    out += ["", "## Criterion averages", "", "| Criterion | Mean | Median | Distribution |", "| --- | ---: | ---: | --- |"]
    for cid, s in view["criteria"].items():
        dist = ", ".join(f"{k:g}: {v}" for k, v in s["distribution"].items())
        out.append(f"| {cid} | {s['mean']:.2f} | {s['median']:g} | {dist} |")
    if view["common_gaps"]:
        out += ["", "## Most common gaps", ""] + [f"- {g}: {n}" for g, n in view["common_gaps"]]
    if view["caps_applied"]:
        out += ["", "## Caps applied", ""] + [f"- {c}: {n}" for c, n in view["caps_applied"].items()]
    if view["hygiene"]:
        out += ["", "## Hygiene issues (submissions affected)", ""] + [f"- {k}: {n}" for k, n in view["hygiene"].items()]
    if view["similarity"]:
        out += ["", "## Near-identical submissions (consequence set by course policy)", ""]
        out += [f"- {m['a']} ~ {m['b']}: {m['kind']} {m['score']:.2f}" for m in view["similarity"]]
    if view["not_graded"]:
        out += ["", "## Not graded", ""] + [f"- {n}: {why}" for n, why in view["not_graded"].items()]
    return "\n".join(out) + "\n"


def cohort_csv(view: dict) -> str:
    buf = io.StringIO()
    crits = list(view["criteria"])
    w = csv.writer(buf)
    w.writerow(["rank", "submission", "sha", "final", "total", "band", "percentile", "caps", "confidence", *crits])
    for r in view["ranking"]:
        w.writerow([r["rank"], r["submission"], r["sha"], r["final"], r["total"], r["band"], r["percentile"],
                    ";".join(r["caps"]), r["confidence"], *(r["ratings"].get(c) for c in crits)])
    return buf.getvalue()


async def run_cohort(zips: list[Path], config: RubricConfig, out_dir: Path, *, llm: LLM | None = None,
                     batch_api: BatchAPI | None = None, submissions_at_once: int = 10,
                     template: Path | None = None, options: GradeOptions | None = None) -> dict:
    out_dir = Path(out_dir)
    run_dirs = intake_all(zips, config, out_dir / "submissions")
    order = list(run_dirs)  # grading order, recorded for the trend check
    (out_dir / "grading_order.json").write_text(json.dumps(order, indent=1))
    if batch_api is not None:
        reports = await grade_batch(run_dirs, config, batch_api, out_dir, options)
    else:
        reports = await grade_live(run_dirs, config, llm, submissions_at_once, options)
    template_dir = None
    if template:
        template_dir = out_dir / "template"
        if not (template_dir / "intake.json").exists():
            run_intake(template, config, template_dir)
    matches = similarity(run_dirs, config.similarity_threshold, template_dir)
    attach_similarity(reports, run_dirs, matches)
    view = cohort_view(reports, config, order, matches)
    (out_dir / "cohort.json").write_text(json.dumps(view, indent=2, default=str))
    (out_dir / "cohort.md").write_text(render_cohort(view))
    (out_dir / "cohort.csv").write_text(cohort_csv(view))
    return view
