import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { PrinterIcon } from "lucide-react";

import { EvidenceList, EvidenceProvider } from "@/components/grader/evidence";
import {
  Band,
  CapStatus,
  Confidence,
  ElementStatus,
  EmptyState,
  InjectionLabel,
  Pill,
  RatingBar,
  RunDots,
  SectionTitle,
  Severity,
} from "@/components/grader/pills";
import { AppShell, PageHeader } from "@/components/grader/shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { fmt, humanize, shortSha } from "@/lib/format";
import { lookupReport, useAppData } from "@/lib/store";
import type { Item, Report } from "@/lib/types";

type View = "instructor" | "student";

export const Route = createFileRoute("/reports/$id")({
  // Instructor view is the default and has no search param; ?view=student switches.
  validateSearch: (search: Record<string, unknown>): { view?: "student" } =>
    search["view"] === "student" ? { view: "student" } : {},
  head: () => ({ meta: [{ title: "Report · Grader Console" }] }),
  component: ReportPage,
});

function ReportPage() {
  const { id } = Route.useParams();
  const view: View = Route.useSearch().view ?? "instructor";
  const navigate = useNavigate({ from: Route.fullPath });
  const data = useAppData();
  const report = lookupReport(data, id);

  if (!report) {
    return (
      <AppShell>
        <EmptyState title="This report is not loaded">
          Load it on the{" "}
          <Link to="/" className="text-primary underline underline-offset-4">
            Load page
          </Link>{" "}
          or open it from a cohort.
        </EmptyState>
      </AppShell>
    );
  }

  const reportId = data.apiReportIds[report.submission_sha256] ?? null;
  const setView = (v: string) => {
    if (v === "student") void navigate({ search: { view: "student" }, replace: true });
    else if (v === "instructor") void navigate({ search: {}, replace: true });
  };

  return (
    <AppShell>
      <EvidenceProvider reportId={reportId}>
        <PageHeader
          title={`${report.assignment} — ${report.title}`}
          meta={
            <span className="flex flex-wrap gap-x-2">
              <span>{report.submission_file ?? "Submission"}</span>
              <span className="font-mono text-xs leading-5">
                {shortSha(report.submission_sha256)}
              </span>
              <span>policy {report.policy_version}</span>
              {view === "instructor" ? <span>prompt {report.prompt_version}</span> : null}
              <span>graded {report.generated_at?.slice(0, 16).replace("T", " ")}</span>
            </span>
          }
          actions={
            <>
              <ToggleGroup
                type="single"
                value={view}
                onValueChange={setView}
                variant="outline"
                aria-label="Report view"
              >
                <ToggleGroupItem value="instructor">Instructor</ToggleGroupItem>
                <ToggleGroupItem value="student">Student</ToggleGroupItem>
              </ToggleGroup>
              {view === "student" ? (
                <Button variant="outline" onClick={() => window.print()}>
                  <PrinterIcon data-icon="inline-start" />
                  Print student view
                </Button>
              ) : null}
            </>
          }
        />
        {report.status !== "graded" ? (
          <div className="mt-6">
            <EmptyState title="Not graded">
              {report.reason ?? "The grader could not complete this submission."}
            </EmptyState>
          </div>
        ) : (
          <ReportBody report={report} view={view} />
        )}
      </EvidenceProvider>
    </AppShell>
  );
}

function ReportBody({ report, view }: { report: Report; view: View }) {
  const instructor = view === "instructor";
  const s = report.score;
  return (
    <>
      <div className="mt-6 flex flex-wrap items-end gap-x-8 gap-y-3 border-b pb-6">
        <div>
          <div className="text-5xl font-semibold tabular-nums tracking-tight">
            {fmt(s.final)}
            <span className="text-2xl font-normal text-muted-foreground"> / 100</span>
          </div>
          {s.binding_cap ? (
            <div className="mt-1 text-sm text-warn">
              Capped from {fmt(s.total)} by {humanize(s.binding_cap)}
            </div>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2 pb-1">
          <Band band={s.band} />
          {instructor ? <Confidence level={report.confidence.level} /> : null}
        </div>
        {instructor && report.confidence.reasons.length > 0 ? (
          <ul className="basis-full text-sm text-muted-foreground">
            {report.confidence.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        ) : null}
      </div>

      <SectionTitle>Criteria</SectionTitle>
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="dense-table">
          <thead>
            <tr>
              <th>Criterion</th>
              <th className="num">Weight</th>
              <th>Rating</th>
              <th className="num">Points</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {report.sections.map((sec) => (
              <tr key={sec.criterion}>
                <td className="font-medium">
                  <a href={`#c-${sec.criterion}`} className="underline-offset-4 hover:underline">
                    {sec.name}
                  </a>
                </td>
                <td className="num">{sec.weight}</td>
                <td>
                  <span className="flex items-center gap-2">
                    <RatingBar rating={sec.rating} />
                    {sec.zero_rule ? <Pill tone="bad">rule</Pill> : null}
                  </span>
                </td>
                <td className="num">{fmt(sec.points)}</td>
                <td className="min-w-72 text-muted-foreground">{sec.assessment.rationale}</td>
              </tr>
            ))}
            <tr className="font-semibold">
              <td>Total</td>
              <td className="num">100</td>
              <td />
              <td className="num">{fmt(s.total)}</td>
              <td />
            </tr>
          </tbody>
        </table>
      </div>

      <SectionTitle>Feedback by criterion</SectionTitle>
      <div className="flex flex-col gap-4">
        {report.sections.map((sec) => (
          <Card key={sec.criterion} id={`c-${sec.criterion}`} className="scroll-mt-20">
            <CardHeader>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <CardTitle className="text-base">{sec.name}</CardTitle>
                <RatingBar rating={sec.rating} />
              </div>
              <CardDescription>{sec.assessment.rationale}</CardDescription>
            </CardHeader>
            <CardContent className="flex flex-col gap-5">
              {sec.zero_rule ? (
                <Alert variant="destructive">
                  <AlertTitle>Rated 0 under the rule: {sec.zero_rule.description}</AlertTitle>
                  <AlertDescription>
                    {sec.zero_rule.quotes.map((q) => (
                      <div key={`${q.file}:${q.line}`} className="font-mono text-xs">
                        {q.file}:{q.line} — {q.excerpt}
                      </div>
                    ))}
                  </AlertDescription>
                </Alert>
              ) : null}

              <table className="dense-table">
                <thead>
                  <tr>
                    <th>Element</th>
                    <th>Status</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {sec.assessment.elements.map((e) => (
                    <tr key={e.id}>
                      <td>
                        <div>{humanize(e.id)}</div>
                        {e.note ? (
                          <div className="text-xs text-muted-foreground">{e.note}</div>
                        ) : null}
                      </td>
                      <td>
                        <ElementStatus status={e.status} />
                      </td>
                      <td>
                        <EvidenceList evidence={e.evidence} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="grid gap-5 md:grid-cols-2">
                <FeedbackList title="What worked" items={sec.assessment.what_worked} />
                <FeedbackList title="What to improve" items={sec.assessment.what_to_improve} />
              </div>
              <FeedbackList title="Defects" items={sec.assessment.defects} withSeverity />

              {sec.assessment.unverified.length > 0 ? (
                <div className="text-sm">
                  <div className="font-medium">Could not be verified statically</div>
                  <ul className="mt-1 list-disc pl-5 text-muted-foreground">
                    {sec.assessment.unverified.map((u) => (
                      <li key={u}>{u}</li>
                    ))}
                  </ul>
                </div>
              ) : null}

              {instructor ? (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t pt-3 text-xs text-muted-foreground">
                  <span className="font-mono">{sec.model}</span>
                  <span className="inline-flex items-center gap-1">
                    runs <RunDots runs={sec.runs} />
                  </span>
                  <span>{sec.tool_calls} tool calls</span>
                  <span>{sec.corrections} correction(s)</span>
                  <span>
                    citations failed: {sec.citations_failed_first_pass} first pass,{" "}
                    {sec.citations_failed_final} final
                  </span>
                  {sec.level_source ? <span>level text: {sec.level_source}</span> : null}
                  {sec.verification_notes.map((n) => (
                    <span key={n} className="basis-full">
                      verification: {n}
                    </span>
                  ))}
                </div>
              ) : null}
            </CardContent>
          </Card>
        ))}
      </div>

      <SectionTitle>Cap review</SectionTitle>
      {s.caps.length === 0 ? (
        <p className="text-sm text-muted-foreground">This assignment has no caps.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="dense-table">
            <thead>
              <tr>
                <th>Cap</th>
                <th className="num">Max</th>
                <th>Status</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {s.caps.map((c) => (
                <tr key={c.cap_id}>
                  <td>{c.description}</td>
                  <td className="num">{c.max}</td>
                  <td>
                    <CapStatus status={c.status} />
                  </td>
                  <td className="text-muted-foreground">{c.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {report.policy_defects.length > 0 ? (
        <div className="mt-4 text-sm">
          <div className="font-medium">
            Reported as defects because the two required signals disagree
          </div>
          <ul className="mt-2 flex flex-col gap-2">
            {report.policy_defects.map((d) => (
              <li key={d.source} className="flex flex-wrap items-start gap-2">
                <Severity severity={d.severity} />
                <span className="flex-1">{d.text}</span>
                <EvidenceList evidence={d.evidence} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {report.top_defects.length > 0 ? (
        <>
          <SectionTitle>Most consequential defects</SectionTitle>
          <ol className="flex flex-col gap-2 text-sm">
            {report.top_defects.map((d, i) => (
              <li key={d.id} className="grid grid-cols-[1.5rem_auto_1fr] items-start gap-2">
                <span className="tabular-nums text-muted-foreground">{i + 1}.</span>
                <Severity severity={d.severity} />
                <span>
                  {d.text} <span className="text-muted-foreground">({humanize(d.criterion)})</span>{" "}
                  <EvidenceList evidence={d.evidence} />
                </span>
              </li>
            ))}
          </ol>
        </>
      ) : null}

      {report.verdict ? (
        <>
          <SectionTitle>Verdict and next steps</SectionTitle>
          <p className="max-w-prose text-sm leading-6">{report.verdict}</p>
          {report.next_steps.length > 0 ? (
            <ul className="mt-3 max-w-prose list-disc pl-5 text-sm leading-6">
              {report.next_steps.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          ) : null}
        </>
      ) : null}

      <SectionTitle>Prompt-injection pre-check</SectionTitle>
      <InjectionSection report={report} instructor={instructor} />

      <SectionTitle>Integrity and submission hygiene</SectionTitle>
      <IntegritySection report={report} instructor={instructor} />

      <SectionTitle>Static-review limitations</SectionTitle>
      <p className="max-w-prose text-sm leading-6 text-muted-foreground">
        {report.limitations.text}
      </p>
      {report.limitations.unverified.length > 0 ? (
        <ul className="mt-2 list-disc pl-5 text-sm">
          {report.limitations.unverified.map((u) => (
            <li key={`${u.criterion}:${u.item}`}>
              <span className="text-muted-foreground">{humanize(u.criterion)}:</span> {u.item}
            </li>
          ))}
        </ul>
      ) : null}

      {instructor ? (
        <p className="mt-10 border-t pt-3 text-xs text-muted-foreground">
          Judge key <span className="font-mono">{report.judge_key}</span> · prompt{" "}
          {report.prompt_version} · cost of this run ${fmt(report.cost.total_usd, 4)}
        </p>
      ) : null}
    </>
  );
}

function FeedbackList({
  title,
  items,
  withSeverity = false,
}: {
  title: string;
  items: Item[];
  withSeverity?: boolean;
}) {
  if (items.length === 0) return null;
  return (
    <div className="text-sm">
      <div className="font-medium">{title}</div>
      <ul className="mt-2 flex flex-col gap-2">
        {items.map((it, i) => (
          <li key={`${i}-${it.text.slice(0, 24)}`} className="flex flex-col gap-1">
            <span className="flex items-start gap-2">
              {withSeverity ? <Severity severity={it.severity} /> : null}
              <span>{it.text}</span>
            </span>
            <EvidenceList evidence={it.evidence} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function InjectionSection({ report, instructor }: { report: Report; instructor: boolean }) {
  const inj = report.injection_precheck;
  if (!instructor) {
    return inj.confirmed_manipulation.length > 0 ? (
      <p className="text-sm">
        Text addressed to the grader was found and ignored:{" "}
        {inj.confirmed_manipulation.map((m) => `${m.file}:${m.line}`).join(", ")}.
      </p>
    ) : (
      <p className="text-sm text-muted-foreground">
        No text attempting to influence the grader was found.
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-3 text-sm">
      <div className="flex flex-wrap gap-2">
        <Pill tone="dim">{inj.regex_matches} regex matches</Pill>
        <Pill tone={inj.classifier_status === "failed" ? "warn" : "dim"}>
          classifier {inj.classifier_status}
        </Pill>
        <Pill tone={inj.confirmed_manipulation.length ? "bad" : "ok"}>
          {inj.confirmed_manipulation.length} confirmed
        </Pill>
        <Pill tone={inj.judge_flags.length ? "warn" : "dim"}>
          {inj.judge_flags.length} judge flags
        </Pill>
      </div>
      {inj.classified.length > 0 ? (
        <div className="overflow-x-auto rounded-lg border bg-card">
          <table className="dense-table">
            <thead>
              <tr>
                <th>Location</th>
                <th>Pattern</th>
                <th>Label</th>
                <th>Excerpt</th>
              </tr>
            </thead>
            <tbody>
              {inj.classified.map((m) => (
                <tr key={`${m.file}:${m.line}:${m.pattern}`}>
                  <td className="font-mono text-xs">
                    {m.file}:{m.line}
                  </td>
                  <td>{humanize(m.pattern)}</td>
                  <td>
                    <InjectionLabel label={m.label} />
                  </td>
                  <td className="max-w-md text-muted-foreground">{m.excerpt}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {inj.hidden_unicode.map((h) => (
        <div key={`${h.file}:${h.line}`}>
          Hidden Unicode at{" "}
          <span className="font-mono text-xs">
            {h.file}:{h.line}
          </span>
        </div>
      ))}
      {inj.judge_flags.map((f) => (
        <div key={`${f.criterion}:${f.file}:${f.line}`}>
          Judge ({humanize(f.criterion)}) flagged grader-directed text at{" "}
          <span className="font-mono text-xs">
            {f.file}:{f.line}
          </span>
        </div>
      ))}
    </div>
  );
}

function IntegritySection({ report, instructor }: { report: Report; instructor: boolean }) {
  const integ = report.integrity;
  const list = (items: { file?: string; path?: string; line?: number }[]) =>
    items
      .slice(0, 8)
      .map((m) => m.path ?? `${m.file}:${m.line}`)
      .join(", ");
  return (
    <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[12rem_1fr]">
      <dt className="text-muted-foreground">Git history</dt>
      <dd>
        {integ.git.commit_count} commit(s); chronology {integ.git.chronology}
        {integ.git.agent_only_commits
          ? `; ${integ.git.agent_only_commits} agent-only commit(s)`
          : ""}
      </dd>
      <dt className="text-muted-foreground">Forbidden files</dt>
      <dd>
        {integ.forbidden_files.length ? (
          <span className="font-mono text-xs">{list(integ.forbidden_files)}</span>
        ) : (
          "None"
        )}
      </dd>
      <dt className="text-muted-foreground">Possible secrets</dt>
      <dd>
        {integ.secret_matches.length ? (
          <span className="font-mono text-xs">{list(integ.secret_matches)}</span>
        ) : (
          "None"
        )}
      </dd>
      <dt className="text-muted-foreground">Unfilled placeholders</dt>
      <dd>
        {integ.placeholders.length ? (
          <span className="font-mono text-xs">{list(integ.placeholders)}</span>
        ) : (
          "None"
        )}
      </dd>
      <dt className="text-muted-foreground">Leftover agent text</dt>
      <dd>
        {integ.leftover_agent_text.length ? (
          <span className="font-mono text-xs">{list(integ.leftover_agent_text)}</span>
        ) : (
          "None"
        )}
      </dd>
      {instructor && integ.similarity && integ.similarity.length > 0 ? (
        <>
          <dt className="text-muted-foreground">Similar submissions</dt>
          <dd>
            {integ.similarity
              .map((m) => `${m.other_name} (${m.kind}, ${Math.round(m.score * 100)}%)`)
              .join("; ")}
          </dd>
        </>
      ) : null}
    </dl>
  );
}
