import { createFileRoute, Link } from "@tanstack/react-router";

import { EmptyState, Pill } from "@/components/grader/pills";
import { AppShell, PageHeader } from "@/components/grader/shell";
import { fmt } from "@/lib/format";
import { useAppData } from "@/lib/store";

export const Route = createFileRoute("/cohorts/")({
  head: () => ({ meta: [{ title: "Cohorts · Grader Console" }] }),
  component: CohortsPage,
});

function CohortsPage() {
  const data = useAppData();
  const entries = Object.entries(data.cohorts);
  return (
    <AppShell>
      <PageHeader title="Cohorts" meta="One row per graded cohort." />
      <div className="mt-6">
        {entries.length === 0 ? (
          <EmptyState title="No cohorts loaded">
            Run <span className="font-mono">uv run evaluate cohort</span>, then load its results on
            the{" "}
            <Link to="/" className="text-primary underline underline-offset-4">
              Load page
            </Link>
            .
          </EmptyState>
        ) : (
          <div className="overflow-x-auto rounded-lg border bg-card">
            <table className="dense-table">
              <thead>
                <tr>
                  <th>Assignment</th>
                  <th>Policy</th>
                  <th className="num">Graded</th>
                  <th className="num">Median final</th>
                  <th>Order check</th>
                  <th className="num">Similar pairs</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([key, c]) => {
                  const finals = c.ranking.map((r) => r.final).sort((a, b) => a - b); // map() already copied
                  const median = finals.length
                    ? finals[Math.floor((finals.length - 1) / 2)]
                    : undefined;
                  return (
                    <tr key={key}>
                      <td>
                        <Link
                          to="/cohorts/$id"
                          params={{ id: key }}
                          className="font-medium text-primary underline-offset-4 hover:underline"
                        >
                          {key}
                        </Link>
                      </td>
                      <td className="text-muted-foreground">{c.policy_version}</td>
                      <td className="num">
                        {c.graded} / {c.submissions}
                      </td>
                      <td className="num">{fmt(median)}</td>
                      <td>
                        {c.order_trend.flag ? (
                          <Pill tone="bad">trend found</Pill>
                        ) : (
                          <Pill tone="dim">no trend</Pill>
                        )}
                      </td>
                      <td className="num">{c.similarity.length}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
