"""Stage 3: grade one submission end to end.

    intake output -> integrity pre-checks -> criterion judges (parallel)
    -> targeted re-runs -> zero rules, caps, score -> summary -> report

Only the judges, the classifier and the summary call use a model; every
decision about the score is made here, in code. Results are cached under
<run_dir>/grades/<judge key>/ so a rerun with nothing changed costs nothing,
and a policy change (caps, bands, weights) re-runs only the scoring.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .intake.snapshot import Snapshot
from .judge import integrity
from .judge.prompts import PROMPT_VERSION
from .judge.runner import run_session
from .judge.session import CriterionResult, CriterionSession, JudgeContext
from .judge.structured import StructuredResult
from .judge.summary import defect_index, fallback_summary, summary_call
from .llm import LLM, UsageLog
from .scoring.config import RubricConfig
from .scoring.score import compute_score, decide_caps

LIMITATIONS = ("This grade comes from a static review of the submitted files. No submitted code was run, no "
               "dependencies were installed, no external URLs were called and no credentials were validated, so "
               "claims that need execution to confirm are listed as unverified.")
UNSTABLE_RANGE = 1.0  # rating range across runs that counts as unstable


@dataclass
class GradeOptions:
    concurrency: int = 10
    force: bool = False
    extra_runs: int = 2          # extra runs on an objective trigger; with the first run, a median of 3
    rerun_triggers: bool = True
    cache_ttl: str = "5m"


def judge_key(config: RubricConfig, submission_sha: str) -> str:
    models = sorted({c.model for c in config.criteria} | set(config.models.values()))
    raw = json.dumps([submission_sha, config.judge_hash, PROMPT_VERSION, models])
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _read(p: Path):
    return json.loads(p.read_text()) if p.exists() else None


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))
    tmp.replace(p)


class SubmissionGrader:
    """Everything needed to grade one intake directory. Also used by the batch driver."""

    def __init__(self, run_dir: Path, config: RubricConfig, options: GradeOptions | None = None):
        self.run_dir = Path(run_dir)
        self.config = config
        self.options = options or GradeOptions()
        self.intake = _read(self.run_dir / "intake.json") or {"status": "missing"}
        self.ok = self.intake.get("status") == "ok"
        self.usage = UsageLog()
        if not self.ok:
            self.key = "not-graded"
            self.dir = self.run_dir / "grades" / self.key
            return
        self.facts = _read(self.run_dir / "fact_sheet.json")
        self.repo_map = _read(self.run_dir / "repo_map.json")
        self.snap = Snapshot.load(self.run_dir / "snapshot")
        self.ctx = JudgeContext(config, self.snap, self.facts, self.repo_map, cache_ttl=self.options.cache_ttl)
        self.key = judge_key(config, self.intake["archive_sha256"])
        self.dir = self.run_dir / "grades" / self.key
        self.phrase_hits = {r.id: integrity.phrase_matches(r, self.snap) for r in config.zero_rules}

    # -- cached pieces --------------------------------------------------------------
    def cached_pre(self) -> dict | None:
        return None if self.options.force else _read(self.dir / "pre.json")

    def pre_sessions(self) -> dict:
        contexts = integrity.injection_contexts(self.snap, self.facts)
        return {"contexts": contexts,
                "injection": integrity.injection_call(self.config, contexts),
                "claims": integrity.doc_claims_call(self.config, self.snap)}

    def finish_pre(self, sessions: dict) -> dict:
        inj: StructuredResult | None = sessions["injection"].result if sessions["injection"] else None
        cl: StructuredResult | None = sessions["claims"].result if sessions["claims"] else None
        for s in (sessions["injection"], sessions["claims"]):
            if s:
                self.usage.extend(s.usage)
        pre = {
            "contexts": sessions["contexts"],
            "injection_output": inj.output if inj and inj.status == "ok" else None,
            "injection_status": inj.status if inj else "not_needed",
            "claims": integrity.verified_claims(self.snap, cl.output if cl and cl.status == "ok" else None),
            "claims_status": cl.status if cl else "not_needed",
        }
        _write(self.dir / "pre.json", pre)
        return pre

    def cached_run(self, cid: str, run: int) -> CriterionResult | None:
        if self.options.force:
            return None
        d = _read(self.dir / f"criterion_{cid}_r{run}.json")
        return CriterionResult.from_dict(d) if d else None

    def criterion_session(self, cid: str, run: int, pre: dict) -> CriterionSession:
        c = self.config.criterion(cid)
        matches = [m for r in self.config.zero_rules if r.criterion == cid for m in self.phrase_hits[r.id]]
        return CriterionSession(self.ctx, c, doc_claims=pre["claims"], phrase_matches=matches, run_index=run)

    def save_run(self, session: CriterionSession) -> CriterionResult:
        self.usage.extend(session.usage)
        res = session.result
        if res.status == "ok":  # failed runs are not cached, so a later regrade retries them
            _write(self.dir / f"criterion_{res.criterion}_r{res.run_index}.json", res.to_dict())
        return res

    # -- decisions in code --------------------------------------------------------------
    def rerun_plan(self, runs: dict[str, list[CriterionResult]]) -> set[str]:
        """Criteria needing extra runs: judgment-based caps or zero rules in play, or a total near a cutoff."""
        if not self.options.rerun_triggers:
            return set()
        need: set[str] = set()
        signals = {k: v["present"] for k, v in self.facts["signals"].items()}
        findings = self.findings(runs)
        for cap in self.config.caps:
            if cap.decided_by != "fact_and_judge":
                continue
            fact = (not signals[cap.signal]) if cap.when == "absent" else signals[cap.signal]
            if fact or findings.get(cap.judge_finding):
                need.add(self.config.finding_owner(cap.judge_finding).id)
        for rule in self.config.zero_rules:
            if self.phrase_hits.get(rule.id) and findings.get(rule.finding):
                need.add(rule.criterion)
        score = self.score(runs, findings)
        near = score.nearest_cutoff
        if near and near["distance"] <= self.config.near_cutoff_points and score.binding_cap is None:
            # The fewest heaviest criteria whose half-step changes could move the total across the cutoff.
            step_points = lambda c: c.weight * self.config.scale.step / self.config.scale.max  # noqa: E731
            covered = 0.0
            for c in sorted(self.config.criteria, key=lambda c: (-c.weight, c.id)):
                need.add(c.id)
                covered += step_points(c)
                if covered >= max(near["distance"], 1e-9):
                    break
        return need

    @staticmethod
    def chosen(results: list[CriterionResult]) -> tuple[CriterionResult, float]:
        """Median-low rating across successful runs, and the run that produced it."""
        ok = [r for r in results if r.status == "ok"]
        med = statistics.median_low([r.rating for r in ok])
        return next(r for r in ok if r.rating == med), med

    def findings(self, runs: dict[str, list[CriterionResult]]) -> dict[str, bool | None]:
        """Majority vote per finding across runs; ties and missing votes are 'not established' (None)."""
        out: dict[str, bool | None] = {}
        for cid, results in runs.items():
            votes: dict[str, list[bool]] = {}
            for r in results:
                for fid, val in r.findings().items():
                    if val is not None:
                        votes.setdefault(fid, []).append(val)
            for fid in self.config.criterion(cid).findings:
                v = votes.get(fid, [])
                yes, no = sum(v), len(v) - sum(v)
                out[fid] = True if yes > no else False if no > yes else None
        return out

    def ratings(self, runs: dict[str, list[CriterionResult]]) -> dict[str, float]:
        return {cid: self.chosen(rs)[1] for cid, rs in runs.items()}

    def score(self, runs, findings):
        ratings = self.ratings(runs)
        ratings, _ = integrity.apply_zero_rules(self.config, ratings, findings, self.phrase_hits)
        signals = {k: v["present"] for k, v in self.facts["signals"].items()}
        caps = decide_caps(self.config, signals, findings) if self.config.caps else []
        return compute_score(self.config, ratings, caps)

    # -- report --------------------------------------------------------------------------
    def build_report(self, runs, pre, summary_out: dict | None) -> dict:
        findings = self.findings(runs)
        ratings, zero_decisions = integrity.apply_zero_rules(self.config, self.ratings(runs), findings, self.phrase_hits)
        signals = {k: v["present"] for k, v in self.facts["signals"].items()}
        caps = decide_caps(self.config, signals, findings) if self.config.caps else []
        score = compute_score(self.config, ratings, caps).to_dict()

        judge_flags = []
        sections = []
        for c in self.config.criteria:
            chosen, med = self.chosen(runs[c.id])
            a = chosen.assessment
            judge_flags += [{**g, "criterion": c.id} for g in a.get("grader_directed_text", [])]
            row = next(r for r in score["criteria"] if r["criterion"] == c.id)
            zero = next((z for z in zero_decisions if z["criterion"] == c.id and z["status"] == "applied"), None)
            sections.append({
                "criterion": c.id, "name": c.name, "weight": c.weight,
                "rating": row["rating"], "judge_rating": med, "points": row["points"],
                "runs": [r.rating for r in runs[c.id] if r.status == "ok"],
                "model": chosen.model, "assessment": a,
                "zero_rule": zero, "verification_notes": chosen.verification_notes,
                "citations_failed_first_pass": chosen.citations_failed_first_pass,
                "citations_failed_final": chosen.citations_failed_final,
                "corrections": chosen.corrections, "tool_calls": len(chosen.tool_log),
                "level_source": c.level_sources.get(int(row["rating"])) if float(row["rating"]).is_integer() else None,
            })

        injection = integrity.injection_report(self.facts, pre["contexts"], pre["injection_output"], judge_flags)
        injection["classifier_status"] = pre["injection_status"] if pre["contexts"] else "not_needed"
        integrity_hit = bool(injection["confirmed_manipulation"])
        if integrity_hit and self.config.injection_consequence == "zero_total":
            score["final"] = 0.0
            score["band"] = self.config.bands[-1].name if self.config.bands else None
            score["flags"].append("integrity: confirmed grader manipulation; total set to 0 by policy")

        policy_defects = self.policy_defects(score, zero_decisions)
        idx = defect_index(sections)
        summary = summary_out or fallback_summary(sections)
        top = [{"id": i, **idx[i]} for i in summary["top_defect_ids"] if i in idx]

        report = {
            "evaluator_version": __version__,
            "prompt_version": PROMPT_VERSION,
            "assignment": self.config.assignment,
            "title": self.config.title,
            "policy_version": self.config.policy_version,
            "judge_key": self.key,
            "submission_sha256": self.intake["archive_sha256"],
            "submission_file": self.intake.get("submission_file"),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "graded",
            "injection_precheck": injection,
            "sections": sections,
            "score": score,
            "zero_rules": zero_decisions,
            "top_defects": top,
            "policy_defects": policy_defects,
            "verdict": summary.get("verdict", ""),
            "next_steps": summary.get("next_steps", []),
            "summary_fallback": bool(summary.get("fallback")),
            "integrity": self.integrity_section(),
            "limitations": {"text": LIMITATIONS,
                            "unverified": [{"criterion": s["criterion"], "item": u}
                                           for s in sections for u in s["assessment"].get("unverified", [])]},
            "doc_claims_status": pre["claims_status"],
        }
        report["confidence"] = confidence(report, self.config)
        report["cost"] = self.usage.summary()
        return report

    def policy_defects(self, score: dict, zero_decisions: list[dict]) -> list[dict]:
        """Caps and rules whose two signals disagree become ordinary high-severity defects."""
        out = []
        for c in score["caps"]:
            if c["status"] != "defect_only":
                continue
            cap = next(x for x in self.config.caps if x.id == c["cap_id"])
            sig = self.facts["signals"][cap.signal]
            evidence = [e for e in sig["evidence"] if "file" in e][:5] or [
                {"file": "fact_sheet", "start_line": 0, "end_line": 0, "quote": f"signals.{cap.signal}"}]
            out.append({"source": f"cap:{cap.id}", "severity": "high", "evidence": evidence,
                        "text": f"{cap.description}: {c['reason']} (cap of {cap.max:g} not applied)"})
        for z in zero_decisions:
            if z["status"] == "defect_only":
                out.append({"source": f"rule:{z['rule']}", "severity": "medium",
                            "evidence": [{"file": q["file"], "start_line": q["line"], "end_line": q["line"],
                                          "quote": q["excerpt"][:80]} for q in z["quotes"]],
                            "text": f"{z['description']}: {z['reason']}; rule not applied"})
        return out

    def integrity_section(self) -> dict:
        f = self.facts
        return {
            "forbidden_files": f["hygiene"]["forbidden_files"],
            "secret_matches": f["hygiene"]["secret_matches"],
            "placeholders": f["placeholders"],
            "leftover_agent_text": f["leftover_agent_text"],
            "git": {k: f["git"].get(k) for k in ("commit_count", "chronology", "authors", "agent_only_commits",
                                                 "agent_coauthored_commits")},
        }

    def not_graded_report(self, reason: str, runs=None) -> dict:
        return {
            "evaluator_version": __version__, "prompt_version": PROMPT_VERSION,
            "assignment": self.config.assignment, "title": self.config.title,
            "policy_version": self.config.policy_version, "judge_key": self.key,
            "submission_sha256": self.intake.get("archive_sha256"),
            "submission_file": self.intake.get("submission_file"),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "status": "not_graded", "reason": reason,
            "criteria_status": {cid: [r.status for r in rs] for cid, rs in (runs or {}).items()},
            "cost": self.usage.summary(),
        }

    def save_report(self, report: dict) -> dict:
        from .report import render_markdown

        _write(self.dir / "report.json", report)
        (self.dir / "report_instructor.md").write_text(render_markdown(report, "instructor"))
        (self.dir / "report_student.md").write_text(render_markdown(report, "student"))
        _write(self.dir / "usage.json", self.usage.to_list())
        return report

    # -- shared tail of the live and batch flows ------------------------------------------
    def failed_reason(self, runs: dict[str, list[CriterionResult]]) -> str | None:
        failed = [cid for cid, rs in runs.items() if not any(r.status == "ok" for r in rs)]
        return "; ".join(f"{cid}: {runs[cid][0].reason}" for cid in failed) or None

    def merge_reruns(self, runs, extra: list[CriterionResult]):
        for r in extra:
            runs[r.criterion].append(r)
        return {cid: sorted(rs, key=lambda r: r.run_index) for cid, rs in runs.items()}

    def _summary_path(self, runs) -> Path:
        score = self.score(runs, self.findings(runs))
        return self.dir / f"summary_{score.final:.2f}.json"

    def summary_session(self, runs):
        """(call to run, None) or (None, cached summary output)."""
        cached = None if self.options.force else _read(self._summary_path(runs))
        if cached is not None:
            return None, cached
        score = self.score(runs, self.findings(runs)).to_dict()
        sections = [{"criterion": c.id, "name": c.name, "weight": c.weight,
                     "rating": next(r["rating"] for r in score["criteria"] if r["criterion"] == c.id),
                     "assessment": self.chosen(runs[c.id])[0].assessment} for c in self.config.criteria]
        return summary_call(self.config, sections, score), None

    def finish_summary(self, call, runs) -> dict | None:
        self.usage.extend(call.usage)
        if call.result.status != "ok":
            return None
        _write(self._summary_path(runs), call.result.output)
        return call.result.output

    def finalize(self, runs, pre, summary_out) -> dict:
        reason = self.failed_reason(runs)
        if reason:
            return self.save_report(self.not_graded_report(f"criteria not graded: {reason}", runs))
        report = self.build_report(runs, pre, summary_out)
        report["config_hash"] = self.config.config_hash
        return self.save_report(report)

    def cached_report(self) -> dict | None:
        if self.options.force or not self.ok:
            return None
        cached = _read(self.dir / "report.json")
        if cached and cached.get("status") == "graded" and cached.get("config_hash") == self.config.config_hash:
            return cached
        return None


def confidence(report: dict, config: RubricConfig) -> dict:
    """Computed from flags and failed checks, with the reasons stated."""
    reasons, severe = [], False
    failed = sum(s["citations_failed_final"] for s in report["sections"])
    if failed:
        reasons.append(f"{failed} citation(s) failed verification and were dropped")
    near = report["score"].get("nearest_cutoff")
    if near and near["distance"] <= config.near_cutoff_points and not report["score"].get("binding_cap"):
        reasons.append(f"total is {near['distance']:g} points from a band boundary")
    for s in report["sections"]:
        if len(s["runs"]) > 1 and max(s["runs"]) - min(s["runs"]) >= UNSTABLE_RANGE:
            reasons.append(f"{s['criterion']} ratings varied across runs ({', '.join(f'{r:g}' for r in s['runs'])})")
            severe = True
    disagree = [c["cap_id"] for c in report["score"]["caps"] if c["status"] == "defect_only"]
    if disagree:
        reasons.append("cap signals disagree for " + ", ".join(disagree))
    if report["injection_precheck"]["confirmed_manipulation"]:
        reasons.append("confirmed grader-directed text in the submission")
        severe = True
    if report["injection_precheck"]["classifier_status"] == "failed":
        reasons.append("injection classifier failed; regex matches unreviewed")
    if report.get("summary_fallback"):
        reasons.append("summary call failed; defects ranked by code")
    level = "High" if not reasons else ("Low" if severe or len(reasons) >= 3 else "Medium")
    return {"level": level, "reasons": reasons,
            "text": f"{level}: " + ("; ".join(reasons) if reasons else "no failed checks or flags") + "."}


async def grade_submission(run_dir: Path, config: RubricConfig, llm: LLM, options: GradeOptions | None = None) -> dict:
    g = SubmissionGrader(run_dir, config, options)
    if not g.ok:
        return g.not_graded_report(g.intake.get("reason", "intake did not complete"))
    cached = g.cached_report()
    if cached:
        return cached

    # Integrity pre-checks (Haiku), in parallel.
    pre = g.cached_pre()
    if pre is None:
        sess = g.pre_sessions()
        await asyncio.gather(*(run_session(llm, s) for s in (sess["injection"], sess["claims"]) if s))
        pre = g.finish_pre(sess)

    sem = asyncio.Semaphore(g.options.concurrency)

    async def run(cid: str, idx: int) -> CriterionResult:
        cached = g.cached_run(cid, idx)
        if cached:
            return cached
        async with sem:
            s = g.criterion_session(cid, idx, pre)
            await run_session(llm, s)
            return g.save_run(s)

    ids = [c.id for c in config.criteria]
    first = await run(ids[0], 0)  # warms the shared-prefix cache
    rest = await asyncio.gather(*(run(cid, 0) for cid in ids[1:]))
    runs = {cid: [r] for cid, r in zip(ids, [first, *rest])}

    if g.failed_reason(runs):
        return g.finalize(runs, pre, None)

    # Targeted re-runs on objective triggers; take the median of three.
    plan = g.rerun_plan(runs)
    if plan:
        extra = await asyncio.gather(*(run(cid, i) for cid in sorted(plan) for i in range(1, 1 + g.options.extra_runs)))
        runs = g.merge_reruns(runs, list(extra))

    call, summary_out = g.summary_session(runs)
    if call is not None:
        await run_session(llm, call)
        summary_out = g.finish_summary(call, runs)
    return g.finalize(runs, pre, summary_out)
