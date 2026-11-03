import { createFileRoute, Link } from "@tanstack/react-router";
import { useDeferredValue, useMemo, useState } from "react";

import { Band, Confidence, EmptyState, Pill } from "@/components/grader/pills";
import { AppShell, PageHeader } from "@/components/grader/shell";
import { Input } from "@/components/ui/input";
import { fmt, shortSha } from "@/lib/format";
import { useAppData } from "@/lib/store";

export const Route = createFileRoute("/reports/")({
  head: () => ({ meta: [{ title: "Reports · Grader Console" }] }),
  component: ReportsPage,
});

function ReportsPage() {
  const data = useAppData();
  const [query, setQuery] = useState("");
  const q = useDeferredValue(query).trim().toLowerCase();

  const rows = useMemo(
    () =>
      Object.entries(data.reports)
        .filter(
          ([sha, r]) =>
            q === "" ||
            sha.startsWith(q) ||
            r.assignment.toLowerCase().includes(q) ||
            (r.submission_file ?? "").toLowerCase().includes(q),
        )
        .sort(([, a], [, b]) => (b.generated_at ?? "").localeCompare(a.generated_at ?? "")),
    [data.reports, q],
  );

  return (
    <AppShell>
      <PageHeader title="Submission reports" meta={`${Object.keys(data.reports).length} loaded`} />
      <div className="mb-3 mt-6">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by assignment, file or hash"
          className="w-72"
          aria-label="Search reports"
        />
      </div>
      {Object.keys(data.reports).length === 0 ? (
        <EmptyState title="No reports loaded">
          Grade a submission on the{" "}
          <Link to="/grade" className="text-primary underline underline-offset-4">
            Grade page
          </Link>{" "}
          or load results on the{" "}
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
                <th>Submission</th>
                <th>Assignment</th>
                <th className="num">Final</th>
                <th>Band</th>
                <th>Confidence</th>
                <th>Graded</th>
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center text-muted-foreground">
                    No reports match “{query}”.
                  </td>
                </tr>
              ) : (
                rows.map(([sha, r]) => (
                  <tr key={sha}>
                    <td>
                      <Link
                        to="/reports/$id"
                        params={{ id: sha }}
                        className="font-medium text-primary underline-offset-4 hover:underline"
                      >
                        {r.submission_file ?? shortSha(sha)}
                      </Link>
                      <div className="font-mono text-[11px] text-muted-foreground">
                        {shortSha(r.submission_sha256)}
                      </div>
                    </td>
                    <td>{r.assignment}</td>
                    <td className="num font-semibold">
                      {r.status === "graded" ? fmt(r.score.final) : "—"}
                    </td>
                    <td>
                      {r.status === "graded" ? (
                        <Band band={r.score.band} />
                      ) : (
                        <Pill tone="bad">not graded</Pill>
                      )}
                    </td>
                    <td>
                      {r.status === "graded" ? <Confidence level={r.confidence.level} /> : null}
                    </td>
                    <td className="text-muted-foreground">
                      {r.generated_at?.slice(0, 16).replace("T", " ")}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </AppShell>
  );
}
