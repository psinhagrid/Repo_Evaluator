import type { Calibration, Cohort, Report } from "./types";

/*
 * Client for the evaluator's local API (`uv run evaluate serve`). API keys live
 * on that server; this module only ever talks to it.
 */

export const API_URL: string =
  (import.meta.env["VITE_API_URL"] as string | undefined) ?? "http://127.0.0.1:8765";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, timeoutMs = 15000): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_URL}${path}`, { ...init, signal: ctrl.signal });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = (await res.json()) as { detail?: unknown };
        if (typeof body.detail === "string") detail = body.detail;
      } catch {
        // non-JSON error body
      }
      throw new ApiError(detail || `HTTP ${res.status}`, res.status);
    }
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    if (e instanceof DOMException && e.name === "AbortError")
      throw new ApiError("The grader API did not respond", 0);
    throw new ApiError("Cannot reach the grader API", 0);
  } finally {
    clearTimeout(timer);
  }
}

export type Health = {
  ok: boolean;
  version: string;
  workspace: string;
  providers: { anthropic: boolean; openai: boolean };
};
export type ConfigInfo = {
  name: string;
  assignment?: string;
  title?: string;
  policy_version?: string;
  criteria?: { id: string; name: string; weight: number }[];
  profiles?: string[];
  profile_status?: Record<
    string,
    { ready: boolean; models: Record<string, string>; missing: string[]; error?: string }
  >;
  suggested_profile?: string;
  error?: string;
};
export type IndexEntry = { id: string; assignment: string | null; path: string };
export type ApiIndex = {
  cohorts: (IndexEntry & {
    policy_version: string | null;
    graded: number | null;
    submissions: number | null;
  })[];
  calibrations: (IndexEntry & { passed: boolean | null; submissions: number | null })[];
  reports: {
    id: string;
    assignment: string | null;
    title: string | null;
    status: string | null;
    submission_sha256: string | null;
    submission_file: string | null;
    generated_at: string | null;
    final: number | null;
    band: string | null;
    run: string;
  }[];
};
export type FileLines = {
  path: string;
  total_lines: number;
  start: number;
  end: number;
  lines: { n: number; text: string }[];
};
export type Job = {
  id: string;
  filename: string;
  config: string;
  models: string | null;
  status: "queued" | "intake" | "grading" | "done" | "not_graded" | "failed";
  created_at: string;
  message: string | null;
  report_id: string | null;
  final: number | null;
  band: string | null;
  progress: { done: number; total: number };
};

export const api = {
  health: () => request<Health>("/api/health", undefined, 3000),
  index: () => request<ApiIndex>("/api/index"),
  configs: () => request<ConfigInfo[]>("/api/configs"),
  cohort: (id: string) => request<Cohort>(`/api/cohorts/${encodeURIComponent(id)}`),
  cohortReports: (id: string) =>
    request<Report[]>(`/api/cohorts/${encodeURIComponent(id)}/reports`, undefined, 60000),
  calibration: (id: string) => request<Calibration>(`/api/calibrations/${encodeURIComponent(id)}`),
  report: (id: string) => request<Report>(`/api/reports/${encodeURIComponent(id)}`),
  file: (reportId: string, path: string, start: number, end: number) =>
    request<FileLines>(
      `/api/reports/${encodeURIComponent(reportId)}/file?` +
        new URLSearchParams({ path, start: String(start), end: String(end) }),
    ),
  jobs: () => request<Job[]>("/api/jobs"),
  job: (id: string) => request<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  grade: (file: File, config: string, models: string, assignmentText: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("config", config);
    if (models && models !== "default") form.append("models", models);
    if (assignmentText.trim()) form.append("assignment_text", assignmentText);
    return request<Job>("/api/grade", { method: "POST", body: form }, 300000);
  },
};
