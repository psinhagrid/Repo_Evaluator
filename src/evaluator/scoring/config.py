"""Rubric config: one YAML per assignment, validated before anything runs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

LEVEL_KEYS = (4, 3, 2, 1, 0)
DECIDED_BY = {"fact", "fact_and_judge"}
SIGNAL_KEYS = {"description", "any_file", "any_heading", "any_text", "in_files", "fact"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
# Evidence types a citation can have; see judge/verify.py for how files map to them.
EVIDENCE_TYPES = {"code", "shell", "md", "csv", "json", "config", "dependencies", "log", "git", "fact", "notebook"}
DEFAULT_MODELS = {
    "judge": "claude-sonnet-5-5",
    "classifier": "claude-haiku-5-5",
    "summary": "claude-sonnet-5-5",
}
MODEL_ALIASES = {"sonnet": "claude-sonnet-5-5", "haiku": "claude-haiku-5-5", "opus": "claude-opus-5-5"}
ROLES = ("judge", "classifier", "summary")  # a criterion's model may name a role instead of a model


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Scale:
    min: float = 0
    max: float = 4
    step: float = 0.5

    def valid(self, rating: float) -> bool:
        if not (self.min <= rating <= self.max):
            return False
        steps = (rating - self.min) / self.step
        return abs(steps - round(steps)) < 1e-9

    def values(self) -> list[float]:
        n = round((self.max - self.min) / self.step)
        return [self.min + i * self.step for i in range(n + 1)]


@dataclass(frozen=True)
class Element:
    """One part of the 4/4 description, with the evidence types that may support it."""
    id: str
    text: str
    evidence: tuple[str, ...] = ()  # empty = any type


@dataclass(frozen=True)
class Criterion:
    id: str
    name: str
    weight: float
    model: str
    levels: dict[int, str]
    elements: tuple[Element, ...] = ()
    reading_list: tuple[str, ...] = ()      # files to start from (globs), shown to the judge
    visible: tuple[str, ...] = ("*",)       # globs the tools may open
    hidden: tuple[str, ...] = ()            # globs the tools may never open (bias isolation)
    doc_claims: bool = False                # receive documentation claims to verify (code criteria)
    findings: tuple[str, ...] = ()          # yes/no judge findings this criterion must report (caps, rules)
    level_sources: dict[int, str] = field(default_factory=dict)  # "rubric" | "draft"
    effort: str = "medium"
    tool_budget: int = 25
    max_context_tokens: int | None = None   # stop tool use near this many input tokens (Haiku pricing tier)
    extra_guidance: str | None = None
    examples: dict[int, tuple[str, ...]] = field(default_factory=dict)  # calibration excerpts per level


@dataclass(frozen=True)
class Signal:
    """A yes/no fact the fact sheet computes from file names, headings or text."""
    id: str
    description: str
    any_file: list[str] = field(default_factory=list)     # glob patterns
    any_heading: list[str] = field(default_factory=list)  # regex over Markdown headings
    any_text: list[str] = field(default_factory=list)     # regex over file text
    in_files: list[str] = field(default_factory=list)     # globs limiting any_text / any_heading
    fact: str | None = None  # dotted path into the fact sheet; present when the value is truthy


@dataclass(frozen=True)
class Cap:
    id: str
    max: float
    description: str
    decided_by: str
    signal: str
    when: str = "absent"  # cap triggers when the signal is absent (or "present")
    judge_finding: str | None = None


@dataclass(frozen=True)
class ZeroRule:
    """A rule that forces one criterion to 0 when code and judge both confirm it.

    Example: the A20 unedited-AI-output rule. Code searches `phrases`; the
    judge of `criterion` must also report `finding` as true.
    """
    id: str
    criterion: str
    finding: str
    phrases: tuple[str, ...]
    description: str
    in_files: tuple[str, ...] = ("*.md",)


@dataclass(frozen=True)
class Band:
    name: str
    min: float


@dataclass(frozen=True)
class RubricConfig:
    assignment: str
    title: str
    policy_version: str
    scale: Scale
    criteria: list[Criterion]
    signals: dict[str, Signal]
    caps: list[Cap]
    bands: list[Band]  # highest first
    near_cutoff_points: float = 3.0
    zero_rules: list[ZeroRule] = field(default_factory=list)
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    injection_consequence: str = "flag"  # "flag" | "zero_total"; open decision for the course owner
    similarity_threshold: float = 0.8
    assignment_text: str = ""
    config_hash: str = ""
    judge_hash: str = ""  # only what the judge sees; policy changes (caps, bands, weights) leave it unchanged

    def criterion(self, cid: str) -> Criterion:
        for c in self.criteria:
            if c.id == cid:
                return c
        raise KeyError(cid)

    def finding_owner(self, finding: str) -> Criterion | None:
        return next((c for c in self.criteria if finding in c.findings), None)


def _require(d: dict, key: str, where: str):
    if key not in d:
        raise ConfigError(f"{where}: missing '{key}'")
    return d[key]


def _model(name: str) -> str:
    return MODEL_ALIASES.get(name, name)


CRITERION_KEYS = {"id", "name", "weight", "model", "levels", "elements", "reading_list", "visible", "hidden",
                  "doc_claims", "findings", "level_sources", "effort", "tool_budget", "max_context_tokens",
                  "extra_guidance", "examples"}


def _parse_criterion(c: dict, where: str, models: dict[str, str]) -> Criterion:
    cid = _require(c, "id", where)
    unknown = set(c) - CRITERION_KEYS
    if unknown:
        raise ConfigError(f"{where} ({cid}): unknown keys {sorted(unknown)}")
    levels = {int(k): str(v) for k, v in (_require(c, "levels", where) or {}).items()}
    missing = [k for k in LEVEL_KEYS if k not in levels]
    if missing:
        raise ConfigError(f"{where} ({cid}): missing level descriptions for {missing}")
    weight = float(_require(c, "weight", where))
    if weight <= 0:
        raise ConfigError(f"{where} ({cid}): weight must be positive")

    elements = []
    for j, e in enumerate(c.get("elements") or []):
        ev = tuple(e.get("evidence") or ())
        bad = set(ev) - EVIDENCE_TYPES
        if bad:
            raise ConfigError(f"{where} ({cid}) elements[{j}]: unknown evidence types {sorted(bad)}")
        elements.append(Element(id=_require(e, "id", f"{where}.elements[{j}]"), text=_require(e, "text", f"{where}.elements[{j}]"), evidence=ev))
    if not elements:
        # Default: the whole 4/4 description is one element.
        elements = [Element(id="level_4", text=levels[4])]
    if len({e.id for e in elements}) != len(elements):
        raise ConfigError(f"{where} ({cid}): duplicate element ids")

    effort = c.get("effort", "medium")
    if effort not in EFFORTS:
        raise ConfigError(f"{where} ({cid}): effort must be one of {sorted(EFFORTS)}")
    budget = int(c.get("tool_budget", 25))
    if not 1 <= budget <= 60:
        raise ConfigError(f"{where} ({cid}): tool_budget must be 1-60")

    return Criterion(
        id=cid,
        name=c.get("name", cid),
        weight=weight,
        model=models.get(c["model"]) if _require(c, "model", where) in ROLES else _model(c["model"]),
        levels=levels,
        elements=tuple(elements),
        reading_list=tuple(c.get("reading_list") or ()),
        visible=tuple(c.get("visible") or ("*",)),
        hidden=tuple(c.get("hidden") or ()),
        doc_claims=bool(c.get("doc_claims", False)),
        findings=tuple(c.get("findings") or ()),
        level_sources={int(k): v for k, v in (c.get("level_sources") or {}).items()},
        effort=effort,
        tool_budget=budget,
        max_context_tokens=c.get("max_context_tokens"),
        extra_guidance=c.get("extra_guidance"),
        examples={int(k): tuple(v) for k, v in (c.get("examples") or {}).items()},
    )


def parse_config(data: dict, profile: str | None = None) -> RubricConfig:
    """`profile` selects a named entry of `model_profiles` (e.g. "openai") that replaces `models`."""
    if not isinstance(data, dict):
        raise ConfigError("config must be a mapping")
    from ..llm import register_prices

    register_prices(data.get("prices") or {})
    models = dict(DEFAULT_MODELS)
    models.update({k: _model(v) for k, v in (data.get("models") or {}).items()})
    if profile:
        profiles = data.get("model_profiles") or {}
        if profile not in profiles:
            raise ConfigError(f"unknown model profile {profile!r}; available: {sorted(profiles)}")
        models.update({k: _model(v) for k, v in profiles[profile].items()})
    unknown_roles = set(models) - set(ROLES)
    if unknown_roles:
        raise ConfigError(f"models: unknown roles {sorted(unknown_roles)}")
    scale = Scale(**(data.get("scale") or {}))
    if scale.step <= 0 or scale.max <= scale.min:
        raise ConfigError("scale: need max > min and step > 0")

    criteria = []
    seen = set()
    for i, c in enumerate(_require(data, "criteria", "config")):
        crit = _parse_criterion(c, f"criteria[{i}]", models)
        if crit.id in seen:
            raise ConfigError(f"criteria[{i}]: duplicate criterion id '{crit.id}'")
        seen.add(crit.id)
        criteria.append(crit)

    total = sum(c.weight for c in criteria)
    if abs(total - 100) > 1e-9:
        raise ConfigError(f"criterion weights total {total:g}, must total 100")

    signals = {}
    for sid, s in (data.get("signals") or {}).items():
        unknown = set(s) - SIGNAL_KEYS
        if unknown:
            raise ConfigError(f"signals.{sid}: unknown keys {sorted(unknown)}")
        signals[sid] = Signal(id=sid, description=s.get("description", sid),
                              any_file=list(s.get("any_file") or []), any_heading=list(s.get("any_heading") or []),
                              any_text=list(s.get("any_text") or []), in_files=list(s.get("in_files") or []),
                              fact=s.get("fact"))

    all_findings = {f for c in criteria for f in c.findings}
    dup = [f for f in all_findings if sum(f in c.findings for c in criteria) > 1]
    if dup:
        raise ConfigError(f"findings {sorted(dup)} are owned by more than one criterion")

    caps = []
    for i, c in enumerate(data.get("caps") or []):
        where = f"caps[{i}]"
        cap = Cap(id=_require(c, "id", where), max=float(_require(c, "max", where)),
                  description=c.get("description", c["id"]), decided_by=_require(c, "decided_by", where),
                  signal=_require(c, "signal", where), when=c.get("when", "absent"),
                  judge_finding=c.get("judge_finding"))
        if cap.decided_by not in DECIDED_BY:
            raise ConfigError(f"{where}: decided_by must be one of {sorted(DECIDED_BY)}")
        if cap.signal not in signals:
            raise ConfigError(f"{where}: unknown signal '{cap.signal}'")
        if cap.when not in ("absent", "present"):
            raise ConfigError(f"{where}: when must be 'absent' or 'present'")
        if cap.decided_by == "fact_and_judge":
            if not cap.judge_finding:
                raise ConfigError(f"{where}: fact_and_judge caps need a judge_finding id")
            if cap.judge_finding not in all_findings:
                raise ConfigError(f"{where}: no criterion reports finding '{cap.judge_finding}'")
        caps.append(cap)

    zero_rules = []
    for i, r in enumerate(data.get("zero_rules") or []):
        where = f"zero_rules[{i}]"
        rule = ZeroRule(id=_require(r, "id", where), criterion=_require(r, "criterion", where),
                        finding=_require(r, "finding", where), phrases=tuple(_require(r, "phrases", where)),
                        description=r.get("description", r["id"]), in_files=tuple(r.get("in_files") or ("*.md",)))
        if rule.criterion not in seen:
            raise ConfigError(f"{where}: unknown criterion '{rule.criterion}'")
        if rule.finding not in next(c for c in criteria if c.id == rule.criterion).findings:
            raise ConfigError(f"{where}: criterion '{rule.criterion}' must list finding '{rule.finding}'")
        zero_rules.append(rule)

    bands = sorted((Band(name=b["name"], min=float(b["min"])) for b in data.get("bands") or []),
                   key=lambda b: b.min, reverse=True)
    if bands and bands[-1].min != 0:
        raise ConfigError("bands: the lowest band must start at 0 so every score has a band")

    consequence = (data.get("integrity") or {}).get("injection_consequence", "flag")
    if consequence not in ("flag", "zero_total"):
        raise ConfigError("integrity.injection_consequence must be 'flag' or 'zero_total'")

    return RubricConfig(
        assignment=_require(data, "assignment", "config"),
        title=data.get("title", data["assignment"]),
        policy_version=str(_require(data, "policy_version", "config")),
        scale=scale,
        criteria=criteria,
        signals=signals,
        caps=caps,
        bands=bands,
        near_cutoff_points=float(data.get("near_cutoff_points", 3.0)),
        zero_rules=zero_rules,
        models=models,
        injection_consequence=consequence,
        similarity_threshold=float((data.get("cohort") or {}).get("similarity_threshold", 0.8)),
        assignment_text=str(data.get("assignment_text", "")).strip(),
        config_hash=_hash(data),
        judge_hash=_hash({
            "criteria": [{k: v for k, v in c.items() if k != "weight"} for c in data["criteria"]],
            "scale": data.get("scale"), "assignment_text": data.get("assignment_text"),
            "zero_rules": data.get("zero_rules"), "models": models,
            "criterion_models": [c.model for c in criteria],
        }),
    )


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


def load_config(path: Path, profile: str | None = None) -> RubricConfig:
    with Path(path).open() as f:
        return parse_config(yaml.safe_load(f), profile)


def with_assignment_text(config: RubricConfig, text: str) -> RubricConfig:
    """Attach the assignment text (one of the evaluator's three inputs); it is part of the judge prompt."""
    from dataclasses import replace

    text = text.strip()
    return replace(config, assignment_text=text, judge_hash=_hash([config.judge_hash, text]))
