import { createFileRoute, Link } from "@tanstack/react-router";

import { EmptyState, Pill } from "@/components/grader/pills";
import { AppShell, PageHeader } from "@/components/grader/shell";
import { fmt } from "@/lib/format";
import { useAppData } from "@/lib/store";

export const Route = createFileRoute("/calibration/")({
  head: () => ({ meta: [{ title: "Calibration · Grader Console" }] }),
  component: CalibrationsPage,
});

function CalibrationsPage() {
  const data = useAppData();
  const entries = Object.entries(data.calibrations);
  return (
    <AppShell>
      <PageHeader
        title="Calibration"
        meta="The launch gate: the judge compared with human grades."
      />
      <div className="mt-6">
        {entries.length === 0 ? (
          <EmptyState title="No calibration loaded">
            Run <span className="font-mono">uv run evaluate calibrate</span> on human-graded
            submissions, then load <span className="font-mono">calibration.json</span> on the{" "}
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
                  <th>Result</th>
                  <th className="num">Submissions</th>
                  <th className="num">Runs each</th>
                  <th className="num">Cost</th>
                </tr>
              </thead>
              <tbody>
                {entries.map(([key, c]) => (
                  <tr key={key}>
                    <td>
                      <Link
                        to="/calibration/$id"
                        params={{ id: key }}
                        className="font-medium text-primary underline-offset-4 hover:underline"
                      >
                        {key}
                      </Link>
                    </td>
                    <td>
                      {c.passed ? <Pill tone="ok">pass</Pill> : <Pill tone="bad">needs work</Pill>}
                    </td>
                    <td className="num">{c.submissions}</td>
                    <td className="num">{c.runs_per_submission}</td>
                    <td className="num">${fmt(c.cost.total_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </AppShell>
  );
}
