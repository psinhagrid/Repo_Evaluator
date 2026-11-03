"""Step 2: tools, citation verification and the criterion session (scripted model)."""

import asyncio
import json

import pytest

from evaluator.intake.snapshot import Snapshot
from evaluator.judge.runner import run_session
from evaluator.judge.session import CriterionSession, JudgeContext
from evaluator.judge.tools import ToolBox
from evaluator.judge.verify import Verifier, evidence_type
from evaluator.pipeline import run_intake
from evaluator.scoring.config import load_config

from .conftest import CONFIGS
from .fakes import APIError400, ScriptedLLM, assessment, element, ev, final, refusal, tool_use, turn_index


@pytest.fixture
def a20():
    return load_config(CONFIGS / "a20.yaml")


@pytest.fixture
def ctx(sample_zip, tmp_path, a20):
    out = tmp_path / "run"
    assert run_intake(sample_zip, a20, out).status == "ok"
    snap = Snapshot.load(out / "snapshot")
    facts = json.loads((out / "fact_sheet.json").read_text())
    repo_map = json.loads((out / "repo_map.json").read_text())
    return JudgeContext(config=a20, snap=snap, facts=facts, repo_map=repo_map)


def _box(ctx, cid):
    return ToolBox(ctx.snap, ctx.repo_map, ctx.config.criterion(cid))


# -- tools ---------------------------------------------------------------------

def test_view_file_and_scope(ctx):
    box = _box(ctx, "agreement_analysis")
    text, err = box.run("x", "view_file", {"path": "agreement_analysis.py", "start_line": 4, "end_line": 5})
    assert not err and "4\tdef kappa(a, b):" in text and "[t1;" in text
    text, err = box.run("x", "view_file", {"path": "README.md", "start_line": None, "end_line": None})
    assert err and "outside this criterion's evidence scope" in text
    text, err = box.run("x", "view_file", {"path": "nope.py", "start_line": None, "end_line": None})
    assert err and "no file" in text
    assert [c.ok for c in box.log] == [True, False, False]


def test_search_logs_zero_hits_and_respects_scope(ctx):
    box = _box(ctx, "agreement_analysis")
    text, _ = box.run("x", "search", {"pattern": "train_test_split", "regex": False, "path_glob": None})
    assert "No matches" in text and box.log[-1].hits == 0
    text, _ = box.run("x", "search", {"pattern": "perfect score", "regex": False, "path_glob": None})
    assert "No matches" in text  # WRITEUP.md is hidden from this criterion
    text, _ = box.run("x", "search", {"pattern": r"kappa\(", "regex": True, "path_glob": "*.py"})
    assert "agreement_analysis.py:4" in text and box.log[-1].hits >= 2
    _, err = box.run("x", "search", {"pattern": "(", "regex": True, "path_glob": None})
    assert err


def test_tool_budget(ctx, a20):
    c = a20.criterion("ground_truth")
    box = ToolBox(ctx.snap, ctx.repo_map, c)
    box.budget = 2
    box.run("a", "search", {"pattern": "x", "regex": False, "path_glob": None})
    box.run("b", "search", {"pattern": "y", "regex": False, "path_glob": None})
    text, err = box.run("c", "search", {"pattern": "z", "regex": False, "path_glob": None})
    assert err and "budget exhausted" in text


def test_get_symbol_find_references_read_table(ctx):
    box = _box(ctx, "professional_baseline")
    text, err = box.run("x", "get_symbol", {"name": "call_judge", "path": None})
    assert not err and "def call_judge" in text and 'path="common.py"' in text
    text, err = box.run("x", "find_references", {"name": "agreement_analysis.py"})
    assert not err and "run_all.sh:2" in text
    box = _box(ctx, "ground_truth")
    text, err = box.run("x", "read_table", {"path": "hand_labels.csv", "row_start": 29, "row_end": 30, "columns": ["label"]})
    assert not err
    assert '"CORRECT": 28' in text and '"INCORRECT": 2' in text and "row 29 (line 30): INCORRECT" in text


# -- verification ------------------------------------------------------------------

def test_evidence_types():
    assert evidence_type("a/b.py") == "code"
    assert evidence_type("run_all.sh") == "shell"
    assert evidence_type("requirements.txt") == "dependencies"
    assert evidence_type("WRITEUP.md") == "md"
    assert evidence_type("x.ipynb", "ipynb") == "notebook"
    assert evidence_type("fact_sheet") == "fact"


def test_verifier_rules(ctx, a20):
    c = a20.criterion("professional_baseline")
    box = ToolBox(ctx.snap, ctx.repo_map, c)
    v = Verifier(ctx.snap, ctx.facts, box, c, a20)
    good = ev("run_all.sh", 2, 2, "python agreement_analysis.py")
    assert v.check_evidence(good, ("code", "shell")) is None
    assert "quoted text not found" in v.check_evidence(ev("run_all.sh", 1, 1, "python agreement"), ())
    assert "beyond end of file" in v.check_evidence(ev("run_all.sh", 2, 9, "python"), ())
    # A README claim is not evidence that one command reproduces the numbers.
    readme = ev("README.md", 3, 3, "Run everything with")
    assert "md evidence" in v.check_evidence(readme, ("code", "shell"))
    assert v.check_evidence(ev("fact_sheet", 0, 0, "dependencies.all_pinned"), ("dependencies", "fact")) is None
    assert "no path" in v.check_evidence(ev("fact_sheet", 0, 0, "dependencies.nope"), ("fact",))

    box.run("s", "search", {"pattern": "cost_usd", "regex": False, "path_glob": None})  # t1, zero hits
    box.run("s", "search", {"pattern": "kappa", "regex": False, "path_glob": None})     # t2, hits
    assert v.check_search_ids(["t1"]) is None
    assert "zero matches" in v.check_search_ids(["t2"])
    assert "unknown" in v.check_search_ids(["t9"])

    a = assessment([
        element("readme", "found", [ev("README.md", 1, 1, "# Judge calibration")]),
        element("one_command", "partial", [good]),
        element("versions_pinned", "partial", [ev("requirements.txt", 1, 3, "pandas>=2.0")]),
        element("script_metrics", "missing", search_ids=["t2"]),
        element("spend_reported", "missing", search_ids=["t1"]),
    ], 4)
    problems = {p.where: p.message for p in v.verify(a).problems}
    assert "a 4 requires every element to be found" in problems["rating"]
    assert "zero-hit search" in problems["elements.script_metrics"]
    assert "elements.spend_reported" not in problems


# -- sessions ------------------------------------------------------------------------

GT_GOOD = assessment([
    element("written_rubric", "missing", search_ids=["t2"]),
    element("labels_before_outputs", "found", [ev("fact_sheet", 0, 0, "git.file_first_added")]),
    element("borderline_documented", "missing", search_ids=["t2"]),
    element("both_classes", "partial", [ev("hand_labels.csv", 30, 31, "INCORRECT")]),
], 1.5, what_to_improve=[{"text": "Add a written rubric.", "evidence": [], "search_ids": ["t2"]}],
    defects=[{"text": "Only 2 of 30 labels are negative.", "severity": "high",
              "evidence": [ev("hand_labels.csv", 30, 31, "INCORRECT")], "search_ids": []}])


def _session(ctx, cid, **kw):
    return CriterionSession(ctx, ctx.config.criterion(cid), **kw)


def test_session_happy_path(ctx):
    def responder(params):
        t = turn_index(params)
        if t == 0:
            return tool_use(("read_table", {"path": "hand_labels.csv", "row_start": None, "row_end": None, "columns": None}),
                            ("search", {"pattern": "borderline", "regex": False, "path_glob": None}))
        return final(GT_GOOD)

    llm = ScriptedLLM(responder)
    res = asyncio.run(run_session(llm, _session(ctx, "ground_truth")))
    assert res.status == "ok" and res.rating == 1.5 and res.corrections == 0
    assert res.citations_failed_first_pass == 0
    assert len(res.tool_log) == 2 and len(res.usage) == 2
    # Request shape: no sampling params, structured output, strict tools, cached prefix.
    p = llm.requests[0]
    assert "temperature" not in p and "top_p" not in p and "thinking" not in p and "tool_choice" not in p
    assert p["output_config"]["format"]["type"] == "json_schema" and p["output_config"]["effort"] == "medium"
    assert all(t["strict"] for t in p["tools"]) and [t["name"] for t in p["tools"]][0] == "view_file"
    assert all("cache_control" in b for b in p["system"]) and p["cache_control"] == {"type": "ephemeral"}
    # Second request carries the first assistant turn unchanged (append-only history).
    second = llm.requests[1]["messages"]
    assert second[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert second[2]["content"][0]["type"] == "tool_result"


def test_session_shared_prefix_identical_across_criteria(ctx):
    seen = []

    def responder(params):
        seen.append((params["system"], params["tools"], params["output_config"]["format"]))
        return refusal()

    for cid in ("ground_truth", "agreement_analysis"):
        asyncio.run(run_session(ScriptedLLM(responder), _session(ctx, cid)))
    assert all(s == seen[0] for s in seen)


def test_failed_citation_gets_one_correction(ctx):
    bad = json.loads(json.dumps(GT_GOOD))
    bad["elements"][3]["evidence"] = [ev("hand_labels.csv", 30, 31, "NEGATIVE")]

    def responder(params):
        t = turn_index(params)
        if t == 0:
            return tool_use(("search", {"pattern": "rubric", "regex": False, "path_glob": None}),
                            ("search", {"pattern": "borderline", "regex": False, "path_glob": None}))
        if t == 1:
            return final(bad)
        last = params["messages"][-1]["content"]
        assert "could not verify" in last and "quoted text not found" in last
        return final(GT_GOOD)

    res = asyncio.run(run_session(ScriptedLLM(responder), _session(ctx, "ground_truth")))
    assert res.status == "ok" and res.corrections == 1
    assert res.citations_failed_first_pass >= 1 and res.citations_failed_final == 0


def test_still_failing_after_correction_is_pruned(ctx):
    overclaim = assessment([
        element("written_rubric", "found", [ev("README.md", 1, 1, "a rubric that is not there")]),
        element("labels_before_outputs", "found", [ev("fact_sheet", 0, 0, "git.file_first_added")]),
        element("borderline_documented", "found", [ev("hand_labels.csv", 1, 1, "id,label")]),
        element("both_classes", "found", [ev("hand_labels.csv", 30, 31, "INCORRECT")]),
    ], 4, what_worked=[{"text": "Great rubric", "evidence": [ev("rubric.md", 1, 2, "x")], "search_ids": []}])

    def responder(params):
        return final(overclaim)

    res = asyncio.run(run_session(ScriptedLLM(responder), _session(ctx, "ground_truth")))
    assert res.status == "ok" and res.corrections == 1
    a = res.assessment
    status = {e["id"]: e["status"] for e in a["elements"]}
    assert status["written_rubric"] == "missing" and status["both_classes"] == "found"
    assert a["rating"] == 3  # 4 not allowed once an element is unverified
    assert a["what_worked"] == []
    assert any("rating lowered" in n for n in res.verification_notes)


def test_invalid_output_refusal_and_400(ctx):
    calls = {"n": 0}

    def garbage(params):
        calls["n"] += 1
        return final("not json at all")

    res = asyncio.run(run_session(ScriptedLLM(garbage), _session(ctx, "ground_truth")))
    assert res.status == "not_graded" and res.attempts == 3 and "invalid output" in res.reason
    assert calls["n"] == 6  # each attempt: reply + one correction

    res = asyncio.run(run_session(ScriptedLLM(lambda p: refusal()), _session(ctx, "ground_truth")))
    assert res.status == "not_graded" and res.attempts == 3 and "refused" in res.reason

    res = asyncio.run(run_session(ScriptedLLM(lambda p: APIError400("bad request")), _session(ctx, "ground_truth")))
    assert res.status == "not_graded" and res.attempts == 1 and "API error" in res.reason


def test_findings_required(ctx):
    c = ctx.config.criterion("ai_collaboration")
    no_finding = assessment([
        element("human_catches", "missing", search_ids=["t1"]),
        element("human_edited", "missing", search_ids=["t1"]),
        element("length_limit", "found", [ev("fact_sheet", 0, 0, "documents.markdown")]),
    ], 1)
    with_finding = dict(no_finding, findings=[{"id": "unedited_ai_output", "value": False, "evidence": [], "search_ids": []}])

    def responder(params):
        t = turn_index(params)
        if t == 0:
            return tool_use(("search", {"pattern": "caught", "regex": False, "path_glob": None}))
        return final(no_finding if t == 1 else with_finding)

    res = asyncio.run(run_session(ScriptedLLM(responder), CriterionSession(ctx, c)))
    assert res.status == "ok" and res.corrections == 1
    assert res.findings() == {"unedited_ai_output": False}
