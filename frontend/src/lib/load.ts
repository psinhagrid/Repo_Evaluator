import { api, type ApiIndex } from "./api";
import { addCalibration, addCohort, addFiles, addReports, loadApiData } from "./store";
import type { Calibration, Cohort, Report } from "./types";

export type LoadResult = {
  file: string;
  kind: "cohort" | "report" | "calibration" | "error";
  message: string;
};

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

export function classify(json: unknown): "cohort" | "report" | "calibration" | null {
  if (!isObject(json)) return null;
  if (Array.isArray(json["ranking"])) return "cohort";
  if (
    (Array.isArray(json["sections"]) && isObject(json["score"])) ||
    json["status"] === "not_graded"
  )
    return "report";
  const criteria = json["criteria"];
  if (isObject(criteria)) {
    const first = Object.values(criteria)[0];
    if (isObject(first) && isObject(first["agreement"])) return "calibration";
  }
  return null;
}

/** Load dropped/picked JSON files into the store, recognising each by shape. */
export async function loadFiles(files: File[]): Promise<LoadResult[]> {
  const results: LoadResult[] = [];
  const reports: Report[] = [];
  for (const f of files) {
    if (!f.name.toLowerCase().endsWith(".json")) {
      results.push({ file: f.name, kind: "error", message: "not a JSON file" });
      continue;
    }
    let json: unknown;
    try {
      json = JSON.parse(await f.text());
    } catch {
      results.push({ file: f.name, kind: "error", message: "invalid JSON" });
      continue;
    }
    const kind = classify(json);
    if (kind === "cohort") {
      const c = json as Cohort;
      addCohort(c);
      results.push({ file: f.name, kind, message: `${c.assignment}: ${c.graded} graded` });
    } else if (kind === "report") {
      const r = json as Report;
      reports.push(r);
      results.push({
        file: f.name,
        kind,
        message: `${r.assignment} · ${r.status === "graded" ? r.score.final : "not graded"}`,
      });
    } else if (kind === "calibration") {
      const c = json as Calibration & { assignment?: string };
      addCalibration(c, c.assignment ?? f.name.replace(/\.json$/i, ""));
      results.push({ file: f.name, kind, message: c.passed ? "PASS" : "needs work" });
    } else {
      results.push({
        file: f.name,
        kind: "error",
        message: "not a cohort, report or calibration file",
      });
    }
  }
  if (reports.length) addReports(reports);
  addFiles(results.filter((r) => r.kind !== "error").map((r) => r.file));
  return results;
}

/** Pull every cohort, calibration and report from the grader API into the store. */
export async function syncFromApi(): Promise<{
  cohorts: number;
  calibrations: number;
  reports: number;
}> {
  const idx: ApiIndex = await api.index();
  const cohorts: Record<string, Cohort> = {};
  const calibrations: Record<string, Calibration> = {};
  const reports: Record<string, Report> = {};
  const apiReportIds: Record<string, string> = {};

  const uniqueKey = (base: string, taken: Record<string, unknown>, path: string) =>
    base in taken ? `${base} (${path})` : base;

  await Promise.all([
    ...idx.cohorts.map(async (c) => {
      const cohort = await api.cohort(c.id);
      cohorts[uniqueKey(cohort.assignment, cohorts, c.path)] = cohort;
    }),
    ...idx.calibrations.map(async (c) => {
      const cal = await api.calibration(c.id);
      calibrations[uniqueKey(c.assignment ?? c.path, calibrations, c.path)] = cal;
    }),
    ...idx.reports.map(async (r) => {
      const rep = await api.report(r.id);
      const key = rep.submission_sha256 || r.id;
      // Several reports can exist for one submission (policy or model changes); keep the newest.
      const prev = reports[key];
      if (!prev || (rep.generated_at ?? "") > (prev.generated_at ?? "")) {
        reports[key] = rep;
        apiReportIds[key] = r.id;
      }
    }),
  ]);
  loadApiData({ cohorts, calibrations, reports, apiReportIds });
  return {
    cohorts: idx.cohorts.length,
    calibrations: idx.calibrations.length,
    reports: Object.keys(reports).length,
  };
}
