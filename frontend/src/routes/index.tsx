import { createFileRoute, Link } from "@tanstack/react-router";
import { useEffect, useRef, useState, type DragEvent } from "react";

import { AppShell, PageHeader } from "@/components/grader/shell";
import { Pill, SectionTitle } from "@/components/grader/pills";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api, API_URL, type Health } from "@/lib/api";
import { getDemoData } from "@/lib/demo";
import { loadFiles, syncFromApi, type LoadResult } from "@/lib/load";
import { clearAll, loadDemoData, useAppData } from "@/lib/store";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/")({
  head: () => ({ meta: [{ title: "Load results · Grader Console" }] }),
  component: LoadPage,
});

type ApiState = { kind: "checking" } | { kind: "offline" } | { kind: "online"; health: Health };

function LoadPage() {
  const data = useAppData();
  const [apiState, setApiState] = useState<ApiState>({ kind: "checking" });
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState<string | null>(null);
  const [results, setResults] = useState<LoadResult[]>([]);
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((health) => {
        if (!cancelled) setApiState({ kind: "online", health });
      })
      .catch(() => {
        if (!cancelled) setApiState({ kind: "offline" });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const sync = async () => {
    setSyncing(true);
    setSyncMessage(null);
    try {
      const n = await syncFromApi();
      setSyncMessage(
        `Loaded ${n.cohorts} cohort(s), ${n.reports} report(s) and ${n.calibrations} calibration(s).`,
      );
    } catch (e) {
      setSyncMessage(e instanceof Error ? e.message : "Sync failed");
    } finally {
      setSyncing(false);
    }
  };

  const onFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setResults(await loadFiles(Array.from(files)));
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void onFiles(e.dataTransfer.files);
  };

  const counts = {
    cohorts: Object.keys(data.cohorts).length,
    reports: Object.keys(data.reports).length,
    calibrations: Object.keys(data.calibrations).length,
  };
  const hasData = counts.cohorts + counts.reports + counts.calibrations > 0;

  return (
    <AppShell>
      <PageHeader
        title="Load results"
        meta="Connect to the grader on this machine, or open the JSON files it writes."
        actions={
          hasData ? (
            <Button variant="outline" onClick={clearAll}>
              Clear loaded data
            </Button>
          ) : undefined
        }
      />

      <div className="mt-6 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Grader API</CardTitle>
            <CardDescription className="font-mono text-xs">{API_URL}</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 text-sm">
            {apiState.kind === "checking" ? (
              <span className="text-muted-foreground">Checking the connection…</span>
            ) : apiState.kind === "offline" ? (
              <div className="flex flex-col gap-2">
                <span>
                  <Pill tone="dim">not running</Pill>
                </span>
                <p className="text-muted-foreground">
                  Start it from the project folder, then reload this page:
                </p>
                <code className="rounded-md bg-muted px-3 py-2 font-mono text-xs">
                  uv run evaluate serve
                </code>
              </div>
            ) : (
              <div className="flex flex-col gap-2">
                <span className="flex flex-wrap items-center gap-2">
                  <Pill tone="ok">connected</Pill>
                  <span className="text-muted-foreground">v{apiState.health.version}</span>
                </span>
                <span className="text-muted-foreground">
                  Workspace{" "}
                  <span className="font-mono text-xs text-foreground">
                    {apiState.health.workspace}
                  </span>
                </span>
                <span className="flex flex-wrap gap-2">
                  <Pill tone={apiState.health.providers.openai ? "ok" : "dim"}>
                    OpenAI key {apiState.health.providers.openai ? "set" : "not set"}
                  </Pill>
                  <Pill tone={apiState.health.providers.anthropic ? "ok" : "dim"}>
                    Anthropic key {apiState.health.providers.anthropic ? "set" : "not set"}
                  </Pill>
                </span>
              </div>
            )}
            {syncMessage !== null && <p className="text-muted-foreground">{syncMessage}</p>}
          </CardContent>
          <CardFooter className="gap-2">
            <Button onClick={() => void sync()} disabled={apiState.kind !== "online" || syncing}>
              {syncing ? "Loading results…" : "Load results from the API"}
            </Button>
            <Button variant="outline" asChild>
              <Link to="/grade">Grade a submission</Link>
            </Button>
          </CardFooter>
        </Card>

        <Card
          onDragOver={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={cn("transition-colors", dragging && "border-primary bg-accent")}
        >
          <CardHeader>
            <CardTitle>Open result files</CardTitle>
            <CardDescription>
              Drop <span className="font-mono">cohort.json</span>,{" "}
              <span className="font-mono">report.json</span> or{" "}
              <span className="font-mono">calibration.json</span> here. Each file is recognised by
              its contents.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <input
              ref={inputRef}
              type="file"
              accept=".json,application/json"
              multiple
              className="sr-only"
              onChange={(e) => void onFiles(e.target.files)}
            />
            {results.length > 0 ? (
              <ul className="flex flex-col gap-1 text-sm">
                {results.map((r, i) => (
                  <li key={`${r.file}-${i}`} className="flex items-center gap-2">
                    <Pill tone={r.kind === "error" ? "bad" : "ok"}>{r.kind}</Pill>
                    <span className="truncate font-mono text-xs">{r.file}</span>
                    <span className="truncate text-muted-foreground">{r.message}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                Files stay in this browser; nothing is uploaded.
              </p>
            )}
          </CardContent>
          <CardFooter className="gap-2">
            <Button variant="outline" onClick={() => inputRef.current?.click()}>
              Choose files
            </Button>
            <Button variant="ghost" onClick={() => loadDemoData(getDemoData())}>
              Load demo data
            </Button>
          </CardFooter>
        </Card>
      </div>

      {data.demo ? (
        <Alert className="mt-4">
          <AlertTitle>Showing demo data</AlertTitle>
          <AlertDescription>
            These submissions are generated examples. Load real results before reviewing grades.
          </AlertDescription>
        </Alert>
      ) : null}

      <SectionTitle>Loaded</SectionTitle>
      {hasData ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <SummaryLink to="/cohorts" label="Cohorts" count={counts.cohorts} />
          <SummaryLink to="/reports" label="Submission reports" count={counts.reports} />
          <SummaryLink to="/calibration" label="Calibrations" count={counts.calibrations} />
        </div>
      ) : (
        <p className="text-sm text-muted-foreground">
          Nothing loaded yet. Use one of the options above.
        </p>
      )}
    </AppShell>
  );
}

function SummaryLink({
  to,
  label,
  count,
}: {
  to: "/cohorts" | "/reports" | "/calibration";
  label: string;
  count: number;
}) {
  return (
    <Link
      to={to}
      className="rounded-lg border bg-card px-4 py-3 transition-colors hover:border-primary/40 hover:bg-accent"
    >
      <div className="text-2xl font-semibold tabular-nums">{count}</div>
      <div className="text-sm text-muted-foreground">{label}</div>
    </Link>
  );
}
