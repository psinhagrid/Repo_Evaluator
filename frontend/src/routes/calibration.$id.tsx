import { createFileRoute, Link } from "@tanstack/react-router";
import { CopyIcon } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, Pill, SectionTitle, Stat } from "@/components/grader/pills";
import { AppShell, PageHeader } from "@/components/grader/shell";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { fmt, humanize, pct, signed } from "@/lib/format";
import { useAppData } from "@/lib/store";
import type { Calibration } from "@/lib/types";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/calibration/$id")({
  head: () => ({ meta: [{ title: "Calibration · Grader Console" }] }),
  component: CalibrationPage,
});

const WITHIN_HALF_TARGET = 0.8;

function CalibrationPage() {
  const { id } = Route.useParams();
  const cal = useAppData().calibrations[id];
  return (
    <AppShell>
      {cal ? (
        <CalibrationView name={id} cal={cal} />
      ) : (
        <EmptyState title="This calibration is not loaded">
          Load its <span className="font-mono">calibration.json</span> on the{" "}
          <Link to="/" className="text-primary underline underline-offset-4">
            Load page
          </Link>
          .
        </EmptyState>
      )}
    </AppShell>
  );
}

function CalibrationView({ name, cal }: { name: string; cal: Calibration }) {
  const criteria = Object.entries(cal.criteria);
  const yaml = JSON.stringify(cal.reference_excerpt_suggestions, null, 2);
  const copy = () => {
    navigator.clipboard
      .writeText(yaml)
      .then(() => toast.success("Copied reference excerpts"))
      .catch(() => toast.error("Could not copy; select the text instead"));
  };

  return (
    <>
      <PageHeader
        title={`Calibration — ${name}`}
        meta={`${cal.submissions} submissions · ${cal.runs_per_submission} runs each`}
      />

      <div className="mt-6 grid gap-3 sm:grid-cols-3">
        <div
          className={cn(
            "rounded-lg border px-4 py-3",
            cal.passed ? "border-ok/30 bg-ok-soft" : "border-bad/30 bg-bad-soft",
          )}
        >
          <div className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
            Result
          </div>
          <div className={cn("mt-1 text-xl font-semibold", cal.passed ? "text-ok" : "text-bad")}>
            {cal.passed ? "Pass" : "Needs work"}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {cal.passed
              ? "Agreement, stability and bias checks all hold."
              : "Fix the issues below before launch."}
          </div>
        </div>
        <Stat
          label="Submissions"
          value={cal.submissions}
          hint={`${cal.runs_per_submission} runs each`}
        />
        <Stat label="Cost" value={`$${fmt(cal.cost.total_usd)}`} />
      </div>

      {cal.regressions.length > 0 ? (
        <Alert variant="destructive" className="mt-4">
          <AlertTitle>Worse than the baseline</AlertTitle>
          <AlertDescription>
            <ul className="list-disc pl-5">
              {cal.regressions.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          </AlertDescription>
        </Alert>
      ) : null}

      <SectionTitle>Agreement with human graders</SectionTitle>
      <div className="overflow-x-auto rounded-lg border bg-card">
        <table className="dense-table">
          <thead>
            <tr>
              <th>Criterion</th>
              <th className="num">n</th>
              <th>Within half a level</th>
              <th className="num">Exact</th>
              <th className="num">MAE</th>
              <th className="num">Bias</th>
              <th className="num">Run range</th>
              <th>Issues</th>
            </tr>
          </thead>
          <tbody>
            {criteria.map(([cid, c]) => (
              <tr key={cid}>
                <td className="font-medium">{humanize(cid)}</td>
                <td className="num">{c.agreement.n}</td>
                <td>
                  <WithinHalf value={c.agreement.within_half} />
                </td>
                <td className="num">{pct(c.agreement.exact)}</td>
                <td className="num">{fmt(c.agreement.mae)}</td>
                <td
                  className={cn(
                    "num",
                    Math.abs(c.agreement.bias) >= 0.25 && "font-medium text-warn",
                  )}
                >
                  {signed(c.agreement.bias)}
                  <span className="ml-1 text-xs text-muted-foreground">
                    {c.agreement.bias < 0 ? "harsh" : c.agreement.bias > 0 ? "lenient" : ""}
                  </span>
                </td>
                <td className="num">{fmt(c.stability.mean_range)}</td>
                <td className="max-w-sm">
                  {c.issues.length === 0 ? (
                    <Pill tone="ok">ok</Pill>
                  ) : (
                    <ul className="text-xs text-bad">
                      {c.issues.map((i) => (
                        <li key={i}>{i}</li>
                      ))}
                    </ul>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {cal.haiku_routing && Object.keys(cal.haiku_routing).length > 0 ? (
        <>
          <SectionTitle>Cheaper-model routing</SectionTitle>
          <div className="grid gap-3 md:grid-cols-2">
            {Object.entries(cal.haiku_routing).map(([cid, r]) => (
              <div key={cid} className="rounded-lg border bg-card px-4 py-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{humanize(cid)}</span>
                  {r.route_to_haiku ? (
                    <Pill tone="ok">use the cheaper model</Pill>
                  ) : (
                    <Pill tone="dim">keep the judge model</Pill>
                  )}
                </div>
                <div className="mt-1 text-muted-foreground">
                  Within half a level {pct(r.haiku.agreement.within_half)}, MAE{" "}
                  {fmt(r.haiku.agreement.mae)}. {r.why}
                </div>
              </div>
            ))}
          </div>
        </>
      ) : null}

      {cal.readme_swap ? (
        <>
          <SectionTitle>README swap test</SectionTitle>
          <p className="mb-3 max-w-prose text-sm text-muted-foreground">
            Code criteria were re-graded with a polished and a weak README. Their ratings should not
            move.
          </p>
          {cal.readme_swap.criteria.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No criterion hides the README, so there was nothing to test.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border bg-card">
              <table className="dense-table">
                <thead>
                  <tr>
                    <th>Criterion</th>
                    <th>README</th>
                    <th className="num">n</th>
                    <th className="num">Mean change</th>
                    <th className="num">Mean |change|</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(cal.readme_swap.results ?? {}).flatMap(([cid, byVariant]) =>
                    Object.entries(byVariant).map(([variant, d]) => (
                      <tr key={`${cid}-${variant}`}>
                        <td className="font-medium">{humanize(cid)}</td>
                        <td>{variant}</td>
                        <td className="num">{d.n}</td>
                        <td className="num">{signed(d.mean_delta)}</td>
                        <td
                          className={cn("num", d.mean_abs_delta > 0.25 && "font-medium text-bad")}
                        >
                          {fmt(d.mean_abs_delta)}
                        </td>
                      </tr>
                    )),
                  )}
                </tbody>
              </table>
            </div>
          )}
          {cal.readme_swap.leaks.map((l) => (
            <Alert key={l} variant="destructive" className="mt-3">
              <AlertTitle>Leak</AlertTitle>
              <AlertDescription>{l}</AlertDescription>
            </Alert>
          ))}
        </>
      ) : null}

      {Object.keys(cal.reference_excerpt_suggestions).length > 0 ? (
        <>
          <SectionTitle
            aside={
              <Button variant="outline" size="sm" onClick={copy}>
                <CopyIcon data-icon="inline-start" />
                Copy
              </Button>
            }
          >
            Suggested reference excerpts
          </SectionTitle>
          <p className="mb-2 max-w-prose text-sm text-muted-foreground">
            Quotes from submissions humans placed at each level. Review them, then add the good ones
            to the criterion's <span className="font-mono">examples</span> in the config.
          </p>
          <pre className="max-h-96 overflow-auto rounded-lg border bg-card p-3 font-mono text-xs">
            {yaml}
          </pre>
        </>
      ) : null}
    </>
  );
}

function WithinHalf({ value }: { value: number }) {
  const ok = value >= WITHIN_HALF_TARGET;
  return (
    <span className="flex items-center gap-2">
      <span className="relative h-2 w-28 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span
          className={cn("absolute inset-y-0 left-0", ok ? "bg-ok" : "bg-warn")}
          style={{ width: `${value * 100}%` }}
        />
        <span
          className="absolute inset-y-0 w-px bg-foreground/60"
          style={{ left: `${WITHIN_HALF_TARGET * 100}%` }}
          title="80% target"
        />
      </span>
      <span className={cn("tabular-nums text-xs", !ok && "font-medium text-warn")}>
        {pct(value)}
      </span>
    </span>
  );
}
