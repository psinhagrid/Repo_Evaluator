import { createFileRoute, Link } from "@tanstack/react-router";
import { UploadIcon } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { Band, EmptyState, Pill, SectionTitle } from "@/components/grader/pills";
import { AppShell, PageHeader } from "@/components/grader/shell";
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
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, type ConfigInfo, type Job } from "@/lib/api";
import { fmt } from "@/lib/format";
import { addApiReport } from "@/lib/store";

export const Route = createFileRoute("/grade")({
  head: () => ({ meta: [{ title: "Grade a submission · Grader Console" }] }),
  component: GradePage,
});

const POLL_MS = 2000;
const ACTIVE: Job["status"][] = ["queued", "intake", "grading"];

const STATUS_TEXT: Record<Job["status"], string> = {
  queued: "Queued",
  intake: "Unpacking and checking the ZIP",
  grading: "Grading criteria",
  done: "Graded",
  not_graded: "Not graded",
  failed: "Failed",
};

function GradePage() {
  const [configs, setConfigs] = useState<ConfigInfo[] | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [config, setConfig] = useState("");
  const [models, setModels] = useState("default");
  const [assignment, setAssignment] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [reportReady, setReportReady] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .configs()
      .then((cs) => {
        if (cancelled) return;
        const usable = cs.filter((c) => !c.error);
        setConfigs(usable);
        const first = usable[0];
        if (first) {
          setConfig(first.name);
          setModels(first.suggested_profile ?? "default");
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setApiError(e instanceof Error ? e.message : "Cannot reach the grader API");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll the running job; when it finishes, pull its report into the console.
  const jobId = job?.id ?? null;
  const jobActive = job !== null && ACTIVE.includes(job.status);
  useEffect(() => {
    if (jobId === null || !jobActive) return undefined;
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .job(jobId)
        .then(async (j) => {
          if (cancelled) return;
          setJob(j);
          if (j.report_id && (j.status === "done" || j.status === "not_graded")) {
            const report = await api.report(j.report_id);
            if (!cancelled) {
              addApiReport(report, j.report_id);
              setReportReady(report.submission_sha256);
            }
          }
        })
        .catch(() => undefined); // transient poll failure: the next tick retries
    }, POLL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobId, jobActive, job]);

  const selected = configs?.find((c) => c.name === config);
  const profiles = selected?.profiles ?? ["default"];
  const status = selected?.profile_status ?? {};
  const modelsReady = status[models]?.ready ?? true;
  const profileLabel = (p: string) => {
    const judge = status[p]?.models["judge"];
    const name = p === "default" ? "Default" : p;
    return judge ? `${name} — ${judge}` : name;
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!file || !config) return;
    setSubmitting(true);
    setSubmitError(null);
    setReportReady(null);
    try {
      setJob(await api.grade(file, config, models, assignment));
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Could not start grading");
    } finally {
      setSubmitting(false);
    }
  };

  if (apiError !== null) {
    return (
      <AppShell>
        <PageHeader title="Grade a submission" />
        <div className="mt-6">
          <EmptyState title="The grader API is not running">
            Start it from the project folder with{" "}
            <span className="font-mono">uv run evaluate serve</span>, then reload this page. API
            keys stay on that server; this page never sees them.
          </EmptyState>
        </div>
      </AppShell>
    );
  }

  const busy = submitting || jobActive;
  return (
    <AppShell>
      <PageHeader
        title="Grade a submission"
        meta="Upload one ZIP. It is unpacked, checked and graded on this machine."
      />
      <form onSubmit={(e) => void submit(e)} className="mt-6 grid gap-4 lg:grid-cols-[1fr_22rem]">
        <Card>
          <CardHeader>
            <CardTitle>Submission</CardTitle>
            <CardDescription>
              A ZIP of the student's repository, up to 200 MB. Submitted code is never run.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <input
              ref={fileRef}
              id="zip"
              type="file"
              accept=".zip,application/zip"
              className="sr-only"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="flex flex-col items-center gap-2 rounded-lg border border-dashed px-6 py-10 text-sm transition-colors hover:border-primary/40 hover:bg-accent"
            >
              <UploadIcon aria-hidden className="size-5 text-muted-foreground" />
              {file ? (
                <span className="font-medium">
                  {file.name}{" "}
                  <span className="text-muted-foreground">
                    ({(file.size / 1024 / 1024).toFixed(1)} MB)
                  </span>
                </span>
              ) : (
                <span className="text-muted-foreground">Choose a ZIP file</span>
              )}
            </button>
            <div className="flex flex-col gap-2">
              <Label htmlFor="assignment">Assignment text (optional)</Label>
              <Textarea
                id="assignment"
                value={assignment}
                onChange={(e) => setAssignment(e.target.value)}
                placeholder="Paste the assignment brief if the config does not include it."
                rows={4}
              />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Rubric and models</CardTitle>
            <CardDescription>
              Every candidate in a cohort should use the same choices.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <Label htmlFor="config">Rubric</Label>
              <Select
                value={config}
                onValueChange={(v) => {
                  setConfig(v);
                  setModels(configs?.find((c) => c.name === v)?.suggested_profile ?? "default");
                }}
              >
                <SelectTrigger id="config" disabled={configs === null}>
                  <SelectValue
                    placeholder={configs === null ? "Loading rubrics…" : "Choose a rubric"}
                  />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {(configs ?? []).map((c) => (
                      <SelectItem key={c.name} value={c.name}>
                        {c.assignment} — {c.title}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="models">Models</Label>
              <Select value={models} onValueChange={setModels}>
                <SelectTrigger id="models">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectGroup>
                    {profiles.map((p) => (
                      <SelectItem key={p} value={p} disabled={status[p]?.ready === false}>
                        {profileLabel(p)}
                        {status[p]?.ready === false
                          ? ` (needs ${status[p]?.missing.join(" and ")} key)`
                          : ""}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                </SelectContent>
              </Select>
            </div>
            {selected?.criteria ? (
              <div className="text-xs text-muted-foreground">
                {selected.criteria.length} criteria:{" "}
                {selected.criteria.map((c) => `${c.name} (${c.weight})`).join(", ")}
              </div>
            ) : null}
          </CardContent>
          <CardFooter>
            <Button
              type="submit"
              className="w-full"
              disabled={!file || !config || busy || !modelsReady}
            >
              {submitting ? "Uploading…" : jobActive ? "Grading…" : "Grade submission"}
            </Button>
          </CardFooter>
        </Card>
      </form>

      {submitError !== null ? (
        <Alert variant="destructive" className="mt-4">
          <AlertTitle>Grading did not start</AlertTitle>
          <AlertDescription>{submitError}</AlertDescription>
        </Alert>
      ) : null}

      {job ? (
        <>
          <SectionTitle>Progress</SectionTitle>
          <JobStatus job={job} reportSha={reportReady} />
        </>
      ) : null}
    </AppShell>
  );
}

function JobStatus({ job, reportSha }: { job: Job; reportSha: string | null }) {
  const total = job.progress.total || 1;
  const value =
    job.status === "done"
      ? 100
      : job.status === "grading"
        ? (job.progress.done / total) * 100
        : job.status === "intake"
          ? 5
          : 0;
  const tone =
    job.status === "done"
      ? "ok"
      : job.status === "failed" || job.status === "not_graded"
        ? "bad"
        : "accent";
  return (
    <div className="flex flex-col gap-3 rounded-lg border bg-card px-4 py-4 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">{job.filename}</span>
        <Pill tone={tone}>{STATUS_TEXT[job.status]}</Pill>
        {job.status === "grading" ? (
          <span className="text-muted-foreground tabular-nums">
            {job.progress.done} of {job.progress.total} criteria
          </span>
        ) : null}
      </div>
      {ACTIVE.includes(job.status) ? (
        <Progress value={value} aria-label="Grading progress" />
      ) : null}
      {job.status === "done" ? (
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-2xl font-semibold tabular-nums">{fmt(job.final)} / 100</span>
          <Band band={job.band} />
          {reportSha ? (
            <Button asChild>
              <Link to="/reports/$id" params={{ id: reportSha }}>
                Open report
              </Link>
            </Button>
          ) : (
            <span className="text-muted-foreground">Loading the report…</span>
          )}
        </div>
      ) : null}
      {job.message ? (
        <p className={job.status === "failed" ? "text-bad" : "text-muted-foreground"}>
          {job.message}
        </p>
      ) : null}
      {job.status === "not_graded" && reportSha ? (
        <Link
          to="/reports/$id"
          params={{ id: reportSha }}
          className="text-primary underline underline-offset-4"
        >
          See why
        </Link>
      ) : null}
    </div>
  );
}
