"""Ratings -> points -> caps -> band. Pure code, no model involved.

Rounding rule (resolves gaps such as 89.5 between "80-89" and "90-100"):
points are kept unrounded; the final score is rounded to 2 decimals once,
and a band applies when final >= band.min. So 89.99 is below a 90 cutoff.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from .config import Cap, RubricConfig


class ScoringError(ValueError):
    pass


def round_half_up(x: float, places: int = 2) -> float:
    """Round like a person would (68.125 -> 68.13); Python's round() would give 68.12."""
    return float(Decimal(repr(x)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


@dataclass
class CapDecision:
    cap_id: str
    max: float
    description: str
    status: str  # "applied" | "defect_only" | "not_triggered"
    reason: str


@dataclass
class ScoreResult:
    assignment: str
    policy_version: str
    criteria: list[dict]
    total: float
    caps: list[CapDecision]
    final: float
    band: str | None
    binding_cap: str | None
    nearest_cutoff: dict | None
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def decide_caps(config: RubricConfig, signals: dict[str, bool], judge_findings: dict[str, bool] | None = None) -> list[CapDecision]:
    """Decide each cap from fact-sheet signals and (where required) judge findings.

    `judge_findings[finding_id]` is True when the judge agrees the cap condition
    holds. A missing finding means the judge has not ruled, which never applies
    a judgment-based cap.
    """
    judge_findings = judge_findings or {}
    decisions = []
    for cap in config.caps:
        if cap.signal not in signals:
            raise ScoringError(f"fact sheet has no value for signal '{cap.signal}'")
        present = signals[cap.signal]
        fact_triggers = (not present) if cap.when == "absent" else present
        decisions.append(_decide(cap, fact_triggers, judge_findings.get(cap.judge_finding) if cap.judge_finding else None))
    return decisions


def _decide(cap: Cap, fact_triggers: bool, judge_agrees: bool | None) -> CapDecision:
    what = f"signal '{cap.signal}' {cap.when}"
    if cap.decided_by == "fact":
        if fact_triggers:
            return CapDecision(cap.id, cap.max, cap.description, "applied", f"fact sheet: {what}")
        return CapDecision(cap.id, cap.max, cap.description, "not_triggered", f"fact sheet: {cap.signal} ok")
    # fact_and_judge: both independent signals must agree.
    if fact_triggers and judge_agrees:
        return CapDecision(cap.id, cap.max, cap.description, "applied", f"fact sheet ({what}) and judge agree")
    if fact_triggers or judge_agrees:
        source = "fact sheet only" if fact_triggers else "judge only"
        return CapDecision(cap.id, cap.max, cap.description, "defect_only",
                           f"{source}; signals disagree, reported as a high-severity defect")
    return CapDecision(cap.id, cap.max, cap.description, "not_triggered", "neither signal triggers")


def band_for(config: RubricConfig, score: float) -> str | None:
    for b in config.bands:
        if score >= b.min:
            return b.name
    return None


def nearest_cutoff(config: RubricConfig, score: float) -> dict | None:
    cutoffs = [b.min for b in config.bands if b.min > 0]
    if not cutoffs:
        return None
    c = min(cutoffs, key=lambda x: abs(x - score))
    return {"cutoff": c, "distance": round_half_up(abs(score - c))}


def compute_score(config: RubricConfig, ratings: dict[str, float], cap_decisions: list[CapDecision] | None = None) -> ScoreResult:
    ids = [c.id for c in config.criteria]
    missing = [cid for cid in ids if cid not in ratings]
    if missing:
        # Never publish a total with a missing criterion.
        raise ScoringError(f"missing ratings for {missing}")
    extra = sorted(set(ratings) - set(ids))
    if extra:
        raise ScoringError(f"ratings for unknown criteria {extra}")

    rows = []
    total = 0.0
    for c in config.criteria:
        r = float(ratings[c.id])
        if not config.scale.valid(r):
            raise ScoringError(f"{c.id}: rating {r} not on scale {config.scale}")
        pts = c.weight * r / config.scale.max
        total += pts
        rows.append({"criterion": c.id, "name": c.name, "weight": c.weight, "rating": r, "points": round_half_up(pts, 4)})

    caps = cap_decisions or []
    applied = [d for d in caps if d.status == "applied"]
    final = total
    binding = None
    for d in applied:
        if d.max < final:
            final, binding = d.max, d.cap_id
    final = round_half_up(final)

    flags = []
    near = nearest_cutoff(config, final)
    if near and near["distance"] <= config.near_cutoff_points and binding is None:
        flags.append(f"total within {config.near_cutoff_points:g} points of band cutoff {near['cutoff']:g}")
    if applied:
        flags.append("cap applied: " + ", ".join(d.cap_id for d in applied))

    return ScoreResult(
        assignment=config.assignment,
        policy_version=config.policy_version,
        criteria=rows,
        total=round_half_up(total),
        caps=caps,
        final=final,
        band=band_for(config, final),
        binding_cap=binding,
        nearest_cutoff=near,
        flags=flags,
    )
