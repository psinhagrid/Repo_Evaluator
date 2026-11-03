"""Command line: intake, score, judge, grade, calibrate, cohort."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import run_intake
from .env import MissingCredentials, check_credentials, resolve_config
from .scoring.config import ConfigError, load_config
from .scoring.score import ScoringError, compute_score, decide_caps


def _config(args):
    text = Path(args.assignment).read_text() if getattr(args, "assignment", None) else None
    return resolve_config(Path(args.config), getattr(args, "models", None), text)


def _check_credentials(config) -> None:
    check_credentials(config)


def _llm(config):
    from .providers import RouterLLM

    _check_credentials(config)
    return RouterLLM()


def _grade(args) -> int:
    import asyncio

    from .grading import GradeOptions, grade_submission

    config = _config(args)
    opts = GradeOptions(concurrency=args.concurrency, force=args.force, rerun_triggers=not args.no_reruns)
    report = asyncio.run(grade_submission(Path(args.run_dir), config, _llm(config), opts))
    if report["status"] != "graded":
        print(f"not graded: {report.get('reason')}", file=sys.stderr)
        return 2
    s = report["score"]
    print(f"{report['assignment']}  final={s['final']:g}  band={s['band']}  total={s['total']:g}  "
          f"confidence={report['confidence']['level']}  cost=${report['cost']['total_usd']:.4f}")
    for sec in report["sections"]:
        print(f"  {sec['criterion']:<32} {sec['rating']:>4g}/4  runs={sec['runs']}")
    print(f"  report: {Path(args.run_dir) / 'grades' / report['judge_key']}")
    return 0


def _judge(args) -> int:
    """Step-2 tool: run one criterion judge and print its verified assessment."""
    import asyncio
    import json as _json

    from .grading import GradeOptions, SubmissionGrader
    from .judge.runner import run_session

    config = _config(args)
    g = SubmissionGrader(Path(args.run_dir), config, GradeOptions(force=True))
    if not g.ok:
        print("intake did not complete", file=sys.stderr)
        return 2
    pre = {"claims": [], "contexts": []}
    session = g.criterion_session(args.criterion, 0, pre)
    result = asyncio.run(run_session(_llm(config), session))
    print(_json.dumps(result.to_dict() if args.full else
                      {"status": result.status, "reason": result.reason, "rating": result.rating,
                       "assessment": result.assessment, "verification_notes": result.verification_notes,
                       "usage": session.usage.summary()}, indent=2))
    return 0 if result.status == "ok" else 2


def _intake(args) -> int:
    config = load_config(args.config)
    roster = json.loads(Path(args.roster).read_text()) if args.roster else None
    result = run_intake(Path(args.zip), config, Path(args.out), roster)
    if result.status != "ok":
        print(f"not graded: {result.reason}", file=sys.stderr)
        return 2
    facts = json.loads((result.out_dir / "fact_sheet.json").read_text())
    print(f"ok  sha256={result.archive_sha256[:16]}…  out={result.out_dir}")
    print(f"    files={facts['inventory']['file_count']}  commits={facts['git']['commit_count']}  "
          f"injection_candidates={len(facts['injection_candidates'])}  "
          f"forbidden={len(facts['hygiene']['forbidden_files'])}  secrets={len(facts['hygiene']['secret_matches'])}")
    for sid, s in facts["signals"].items():
        print(f"    signal {sid}: {'present' if s['present'] else 'absent'}")
    return 0


def _score(args) -> int:
    config = load_config(args.config)
    data = json.loads(Path(args.ratings).read_text())
    ratings = data.get("ratings", data)
    judge_findings = data.get("judge_findings", {})
    signals = {}
    if args.facts:
        facts = json.loads(Path(args.facts).read_text())
        signals = {k: v["present"] for k, v in facts["signals"].items()}
    signals.update(data.get("signals", {}))
    caps = decide_caps(config, signals, judge_findings) if config.caps else []
    result = compute_score(config, ratings, caps)
    print(json.dumps(result.to_dict(), indent=2))
    return 0


def _batch_api(config):
    from .batch import RouterBatchAPI

    _check_credentials(config)
    return RouterBatchAPI()


def _cohort(args) -> int:
    import asyncio
    from .cohort import run_cohort
    from .grading import GradeOptions

    config = _config(args)
    zips = sorted(Path(args.zips).glob("*.zip"))
    if not zips:
        print(f"no .zip files in {args.zips}", file=sys.stderr)
        return 2
    opts = GradeOptions(cache_ttl="1h" if args.batch else "5m", force=args.force)
    view = asyncio.run(run_cohort(
        zips, config, Path(args.out),
        llm=None if args.batch else _llm(config), batch_api=_batch_api(config) if args.batch else None,
        submissions_at_once=args.at_once, template=Path(args.template) if args.template else None, options=opts))
    print(f"graded {view['graded']}/{view['submissions']}  cost=${view['cost_usd']:.2f}  "
          f"order-trend p={view['order_trend'].get('p')}  similar pairs={len(view['similarity'])}")
    print(f"  {Path(args.out) / 'cohort.md'}")
    return 0


def _calibrate(args) -> int:
    import asyncio
    import json as _json

    from .calibration import Calibrator, load_human, render_calibration

    config = _config(args)
    human = load_human(Path(args.human))
    zips = {p.stem: p for p in Path(args.zips).glob("*.zip")}
    missing = sorted(set(human) - set(zips))
    if missing:
        print(f"warning: no ZIP for {missing}", file=sys.stderr)
    out = Path(args.out)
    cal = Calibrator(config, _llm(config), out, runs=args.runs, concurrency=args.concurrency)
    baseline = _json.loads(Path(args.baseline).read_text()) if args.baseline else None
    haiku = [c for c in (args.haiku or "").split(",") if c]
    report = asyncio.run(cal.run(zips, human, haiku_criteria=haiku, swap=not args.no_swap, baseline=baseline))
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration.json").write_text(_json.dumps(report, indent=2, default=str))
    (out / "calibration.md").write_text(render_calibration(report))
    print(f"{'PASS' if report['passed'] else 'NEEDS WORK'}  {out / 'calibration.md'}")
    return 0 if report["passed"] else 1


def _serve(args) -> int:
    from .env import load_env
    from .server import serve

    load_env()
    if args.host not in ("127.0.0.1", "localhost"):
        print("warning: the API has no authentication; only bind to a trusted network", file=sys.stderr)
    serve(Path(args.workspace), Path(args.configs), args.host, args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evaluate", description="Codebase submission evaluator (stage 1: deterministic core)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("intake", help="unzip, snapshot and build the fact sheet")
    pi.add_argument("zip")
    pi.add_argument("--config", required=True, help="rubric YAML")
    pi.add_argument("--out", required=True, help="output directory")
    pi.add_argument("--roster", help="JSON list of {name, email} to anonymize")
    pi.set_defaults(fn=_intake)

    ps = sub.add_parser("score", help="ratings -> points, caps and band")
    ps.add_argument("ratings", help='JSON: {"ratings": {...}, "judge_findings": {...}, "signals": {...}}')
    ps.add_argument("--config", required=True)
    ps.add_argument("--facts", help="fact_sheet.json to read cap signals from")
    ps.set_defaults(fn=_score)

    pg = sub.add_parser("grade", help="grade one intake directory with the judge (calls the Claude API)")
    pg.add_argument("run_dir")
    pg.add_argument("--config", required=True)
    pg.add_argument("--models", help="model profile from the config's model_profiles (e.g. openai)")
    pg.add_argument("--assignment", help="assignment text file (otherwise assignment_text from the config)")
    pg.add_argument("--concurrency", type=int, default=10)
    pg.add_argument("--force", action="store_true", help="ignore cached results")
    pg.add_argument("--no-reruns", action="store_true", help="skip objective-trigger re-runs")
    pg.set_defaults(fn=_grade)

    pj = sub.add_parser("judge", help="run a single criterion judge (step-2 check against a known report)")
    pj.add_argument("run_dir")
    pj.add_argument("--config", required=True)
    pj.add_argument("--criterion", required=True)
    pj.add_argument("--models", help="model profile from the config's model_profiles (e.g. openai)")
    pj.add_argument("--assignment")
    pj.add_argument("--full", action="store_true", help="include tool log and raw output")
    pj.set_defaults(fn=_judge)

    pc = sub.add_parser("cohort", help="intake and grade every ZIP in a folder, then build the cohort view")
    pc.add_argument("zips", help="folder of submission ZIPs")
    pc.add_argument("--config", required=True)
    pc.add_argument("--out", required=True)
    pc.add_argument("--models", help="model profile from the config's model_profiles (e.g. openai)")
    pc.add_argument("--assignment")
    pc.add_argument("--batch", action="store_true", help="use the Message Batches API (half price, slower)")
    pc.add_argument("--at-once", type=int, default=10, help="live mode: submissions graded concurrently")
    pc.add_argument("--template", help="starter-code ZIP excluded from similarity")
    pc.add_argument("--force", action="store_true")
    pc.set_defaults(fn=_cohort)

    pk = sub.add_parser("calibrate", help="launch gate: compare the judge with human grades")
    pk.add_argument("zips", help="folder of calibration ZIPs (file stem = submission id)")
    pk.add_argument("--human", required=True, help="CSV with columns submission, criterion, rating")
    pk.add_argument("--config", required=True)
    pk.add_argument("--out", required=True)
    pk.add_argument("--models", help="model profile from the config's model_profiles (e.g. openai)")
    pk.add_argument("--assignment")
    pk.add_argument("--runs", type=int, default=3)
    pk.add_argument("--concurrency", type=int, default=10)
    pk.add_argument("--haiku", help="comma-separated criteria to test on Haiku")
    pk.add_argument("--baseline", help="previous calibration.json; fail on regressions")
    pk.add_argument("--no-swap", action="store_true", help="skip the README swap test")
    pk.set_defaults(fn=_calibrate)

    pv = sub.add_parser("serve", help="local API for the Grader Console frontend")
    pv.add_argument("--workspace", default="workspace", help="folder with cohorts, calibrations and graded runs")
    pv.add_argument("--configs", default="configs")
    pv.add_argument("--host", default="127.0.0.1")
    pv.add_argument("--port", type=int, default=8765)
    pv.set_defaults(fn=_serve)

    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, ScoringError, KeyError, MissingCredentials) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
