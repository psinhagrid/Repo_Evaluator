"""Render a graded report as Markdown, in an instructor view and a student view.

Students see scores, feedback and evidence. Instructors also see integrity
internals: injection-scan matches, verification notes, runs and cost.
"""

from __future__ import annotations


def _cite(ev: dict) -> str:
    f = ev["file"]
    if f == "fact_sheet":
        return f"fact sheet `{ev['quote']}`"
    a, b = ev["start_line"], ev["end_line"]
    return f"`{f}:{a}`" if a == b or not b else f"`{f}:{a}-{b}`"


def _cites(evs: list[dict]) -> str:
    return ", ".join(_cite(e) for e in evs) if evs else "searches found nothing"


def _esc(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict, view: str = "instructor") -> str:
    instructor = view == "instructor"
    out: list[str] = []
    w = out.append
    w(f"# {report['assignment']} — {report['title']}: grading report")
    w("")
    w(f"Submission `{(report.get('submission_sha256') or '')[:12]}` · policy {report['policy_version']} · "
      f"prompt {report['prompt_version']} · generated {report['generated_at']}")
    w("")
    if report["status"] != "graded":
        w(f"**Not graded.** {report.get('reason', '')}")
        return "\n".join(out) + "\n"

    s = report["score"]
    band = f" — **{s['band']}**" if s.get("band") else ""
    capped = f" (capped from {s['total']:g} by `{s['binding_cap']}`)" if s.get("binding_cap") else ""
    w(f"## Result: {s['final']:g} / 100{band}{capped}")
    w("")
    if instructor:
        w(f"Confidence: {report['confidence']['text']}")
        w("")

    # 1. Prompt-injection pre-check
    inj = report["injection_precheck"]
    w("## Prompt-injection pre-check")
    w("")
    if instructor:
        w(f"Regex matches: {inj['regex_matches']} · classifier: {inj['classifier_status']} · "
          f"confirmed manipulation: {len(inj['confirmed_manipulation'])} · judge flags: {len(inj['judge_flags'])}")
        w("")
        if inj["classified"]:
            w("| Location | Pattern | Label | Excerpt |")
            w("| --- | --- | --- | --- |")
            for m in inj["classified"]:
                w(f"| `{m['file']}:{m['line']}` | {m['pattern']} | {m['label']} | {_esc(m['excerpt'])[:120]} |")
            w("")
        for h in inj["hidden_unicode"]:
            w(f"- Hidden Unicode at `{h['file']}:{h['line']}`")
        for jf in inj["judge_flags"]:
            w(f"- Judge ({jf['criterion']}) flagged grader-directed text at `{jf['file']}:{jf['line']}`")
    else:
        if inj["confirmed_manipulation"]:
            w("Text addressed to the grader was found and ignored:")
            for m in inj["confirmed_manipulation"]:
                w(f"- `{m['file']}:{m['line']}`")
        else:
            w("No text attempting to influence the grader was found.")
    w("")

    # 2. Criterion table
    w("## Criteria")
    w("")
    w("| Criterion | Weight | Rating | Points | Rationale |")
    w("| --- | ---: | ---: | ---: | --- |")
    for sec in report["sections"]:
        a = sec["assessment"]
        rating = f"{sec['rating']:g}/4"
        if sec.get("zero_rule"):
            rating += " (rule)"
        w(f"| {sec['name']} | {sec['weight']:g} | {rating} | {sec['points']:.2f} | {_esc(a['rationale'])} |")
    w(f"| **Total** | 100 | | **{s['total']:.2f}** | |")
    w("")

    # 3. What worked / what to improve, per criterion
    w("## Feedback by criterion")
    for sec in report["sections"]:
        a = sec["assessment"]
        w("")
        w(f"### {sec['name']} — {sec['rating']:g}/4")
        if sec.get("zero_rule"):
            z = sec["zero_rule"]
            w("")
            w(f"Rated 0 under the rule: {z['description']}.")
            for q in z["quotes"]:
                w(f"> `{q['file']}:{q['line']}` {q['excerpt']}")
        w("")
        w("| Element | Status | Evidence |")
        w("| --- | --- | --- |")
        for e in a["elements"]:
            w(f"| {e['id']} | {e['status']} | {_cites(e['evidence'])} |")
        if a["what_worked"]:
            w("")
            w("**What worked**")
            for it in a["what_worked"]:
                w(f"- {it['text']} ({_cites(it['evidence'])})")
        if a["what_to_improve"]:
            w("")
            w("**What to improve**")
            for it in a["what_to_improve"]:
                w(f"- {it['text']} ({_cites(it['evidence'])})")
        if a["defects"]:
            w("")
            w("**Defects**")
            for it in a["defects"]:
                w(f"- [{it['severity']}] {it['text']} ({_cites(it['evidence'])})")
        if instructor:
            w("")
            runs = ", ".join(f"{r:g}" for r in sec["runs"])
            w(f"<sub>model {sec['model']} · runs [{runs}] · {sec['tool_calls']} tool calls · corrections "
              f"{sec['corrections']} · citations failed first pass {sec['citations_failed_first_pass']}, final "
              f"{sec['citations_failed_final']}"
              + (f" · level text: {sec['level_source']}" if sec.get("level_source") else "") + "</sub>")
            for n in sec["verification_notes"]:
                w(f"<sub>verification: {_esc(n)}</sub>")

    # 4. Totals and caps
    w("")
    w("## Total and cap review")
    w("")
    w(f"Weighted total: {s['total']:.2f}. Final score: {s['final']:g}.")
    if s["caps"]:
        w("")
        w("| Cap | Max | Status | Reason |")
        w("| --- | ---: | --- | --- |")
        for c in s["caps"]:
            w(f"| {c['description']} | {c['max']:g} | {c['status']} | {_esc(c['reason'])} |")
    if report.get("policy_defects"):
        w("")
        w("Reported as defects because the two required signals disagree:")
        for d in report["policy_defects"]:
            w(f"- [{d['severity']}] {d['text']} ({_cites(d['evidence'])})")
    for z in report["zero_rules"]:
        shown = ("applied", "defect_only", "rejected_by_judge") if instructor else ("applied", "defect_only")
        if z["status"] in shown:
            w("")
            w(f"Rule **{z['description']}**: {z['status']} ({z['reason']}).")
    if instructor and s["flags"]:
        w("")
        w("Flags: " + "; ".join(s["flags"]))

    # 5-6. Top defects, verdict
    if report["top_defects"]:
        w("")
        w("## Most consequential defects")
        w("")
        for i, d in enumerate(report["top_defects"], start=1):
            w(f"{i}. [{d['severity']}] {d['text']} ({_cites(d['evidence'])})")
    if report.get("verdict"):
        w("")
        w("## Verdict and next steps")
        w("")
        w(report["verdict"])
        if report["next_steps"]:
            w("")
            for step in report["next_steps"]:
                w(f"- {step}")

    # 7. Integrity and hygiene
    integ = report["integrity"]
    w("")
    w("## Integrity and submission hygiene")
    w("")
    g = integ["git"]
    w(f"- Git: {g.get('commit_count', 0)} commits; chronology {g.get('chronology')}"
      + (f"; agent-only commits {g['agent_only_commits']}" if g.get("agent_only_commits") else ""))
    w(f"- Forbidden files: {len(integ['forbidden_files'])}"
      + (" (" + ", ".join(f"`{f['path']}`" for f in integ["forbidden_files"][:8]) + ")" if integ["forbidden_files"] else ""))
    w(f"- Possible secrets: {len(integ['secret_matches'])}"
      + (" (" + ", ".join(f"`{m['file']}:{m['line']}`" for m in integ["secret_matches"][:8]) + ")" if integ["secret_matches"] else ""))
    w(f"- Unfilled placeholders: {len(integ['placeholders'])}"
      + (" (" + ", ".join(f"`{m['file']}:{m['line']}`" for m in integ["placeholders"][:8]) + ")" if integ["placeholders"] else ""))
    if integ["leftover_agent_text"]:
        w("- Leftover agent text: " + ", ".join(f"`{m['file']}:{m['line']}`" for m in integ["leftover_agent_text"][:8]))
    if instructor and integ.get("similarity"):
        for m in integ["similarity"]:
            w(f"- Similar to submission `{m['other'][:12]}` ({m['kind']}: {m['score']:.2f})")

    # 8. Limitations
    w("")
    w("## Static-review limitations")
    w("")
    w(report["limitations"]["text"])
    for u in report["limitations"]["unverified"]:
        w(f"- ({u['criterion']}) {u['item']}")

    if instructor:
        w("")
        w("## Run details")
        w("")
        w(f"Judge key `{report['judge_key']}` · documentation claims: {report.get('doc_claims_status')} · "
          f"cost this run ${report['cost']['total_usd']:.4f}"
          + (f" (no prices configured for {', '.join(report['cost']['unpriced_models'])})"
             if report["cost"].get("unpriced_models") else ""))
    return "\n".join(out) + "\n"
